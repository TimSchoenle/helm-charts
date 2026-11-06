"""Run a rendered Helm release on a single Docker host, as faithfully as Docker allows.

Why this exists
---------------
Some of what a chart ships is only testable by running it: whether two images the chart pins can
still talk to each other, whether the security context it renders still lets an image start,
whether the endpoints its ConfigMap writes resolve to the Services it creates. The install job
answers part of that on kind, but it is one job per changed chart per Kubernetes version, it takes
the better part of an hour for the largest chart, and it can only observe that pods became Ready.

This module takes the manifests `helm template` produced and starts every Deployment and
StatefulSet in them as Docker containers on one network, so an end-to-end driver can then drive
the application the way a user would. Nothing is restated: images, commands, environment,
ConfigMaps, Secrets, users, capabilities, memory limits, volumes and probes are all read from the
render, so the run follows the chart rather than a copy of it.

What is translated, and how
---------------------------
  pod                       containers on one Docker network; the first main container owns the
                            network namespace and the others join it, as a pod's containers share
                            one. Init containers run to completion, in order, before it starts
  Service                   network aliases (`name`, `name.ns`, `name.ns.svc`, and the
                            cluster-local FQDN) on every pod its selector matches. Docker cannot
                            remap a port, so a Service whose port differs from its target port is
                            refused rather than silently unreachable
  env / envFrom             resolved from the ConfigMaps and Secrets in the same render, with the
                            kubelet's `$(VAR)` expansion; passed through the environment rather
                            than argv, so a secret never appears in a process listing
  securityContext           runAsUser/runAsGroup, fsGroup and supplementalGroups as groups,
                            readOnlyRootFilesystem, capabilities, allowPrivilegeEscalation, and
                            the kubelet's refusal to start a runAsNonRoot container as root
  resources.limits          memory and cpu, so a limit too small for the workload fails here as it
                            would be OOM-killed there; requests have no Docker equivalent
  volumes                   persistentVolumeClaim and volumeClaimTemplates as named volumes shared
                            by claim name; emptyDir as a named volume per pod; configMap and secret
                            as read-only bind mounts of their keys. All start empty
                            (`volume-nocopy`), as a fresh volume does, and writable by the pod's
                            fsGroup, as the kubelet leaves them
  probes                    readiness, falling back to startup: httpGet and tcpSocket from the
                            host through a loopback-published port, exec through `docker exec`

What is not
-----------
NetworkPolicy, scheduling, RBAC, and anything a controller reconciles. Those are covered by the
render, policy and install gates; this module exists for the behaviour none of them can see.
Anything the translation does not understand is refused with an error naming it, never skipped:
a test that quietly ran something other than what the chart renders would be worse than none.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKLOAD_KINDS = ("Deployment", "StatefulSet")

# A container the runtime restarted more often than this is crash-looping. Kubernetes would keep
# trying forever; a test has to stop and say so.
MAX_RESTARTS = 3

_QUANTITY = re.compile(r"^([0-9.]+)([A-Za-z]*)$")
_BINARY_SUFFIXES = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
_DECIMAL_SUFFIXES = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "m": 10**-3}
_ENV_REFERENCE = re.compile(r"\$\$|\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


class ReleaseError(RuntimeError):
    """The release could not be run, or the render asks for something this module cannot honour."""


def docker(*args: str, env: dict[str, str] | None = None, timeout: float | None = None) -> str:
    """Run a docker command and return its stdout, raising ReleaseError on a non-zero exit."""
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"`docker {' '.join(args[:2])}` exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def parse_quantity(quantity: str | int | float) -> float:
    """Parse a Kubernetes resource quantity (`512Mi`, `1G`, `250m`, `2`) into a plain number."""
    match = _QUANTITY.match(str(quantity))
    if not match:
        raise ReleaseError(f"unrecognised resource quantity {quantity!r}")
    number, suffix = float(match.group(1)), match.group(2)
    if suffix in _BINARY_SUFFIXES:
        return number * _BINARY_SUFFIXES[suffix]
    if suffix in _DECIMAL_SUFFIXES:
        return number * _DECIMAL_SUFFIXES[suffix]
    raise ReleaseError(f"unrecognised resource quantity suffix in {quantity!r}")


def image_reference(image: str) -> str:
    """Return a reference Docker accepts for an image the chart pins as `repo:tag@digest`.

    Docker refuses a tag and a digest together; the digest is the half worth keeping.
    """
    if "@" not in image:
        return image
    name, digest = image.split("@", 1)
    repository = name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name
    return f"{repository}@{digest}"


def expand_references(value: str, env: dict[str, str]) -> str:
    """Apply the kubelet's `$(VAR)` expansion: defined names are substituted, `$$` escapes `$`,
    and an undefined reference is left as written."""

    def substitute(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1)
        return env.get(name, match.group(0))

    return _ENV_REFERENCE.sub(substitute, value)


class Manifests:
    """The documents one `helm template` produced, indexed for lookup."""

    def __init__(self, documents: list[dict[str, Any]], namespace: str = "default") -> None:
        self.documents = [document for document in documents if document]
        self.namespace = namespace

    @classmethod
    def from_yaml(cls, text: str, namespace: str = "default") -> Manifests:
        return cls(list(yaml.safe_load_all(text)), namespace)

    def of_kind(self, *kinds: str) -> list[dict[str, Any]]:
        return [document for document in self.documents if document.get("kind") in kinds]

    def find(self, kind: str, name: str) -> dict[str, Any] | None:
        for document in self.of_kind(kind):
            if document["metadata"]["name"] == name:
                return document
        return None

    def config_map(self, name: str, optional: bool = False) -> dict[str, str]:
        document = self.find("ConfigMap", name)
        if document is None:
            if optional:
                return {}
            raise ReleaseError(f"ConfigMap {name!r} is referenced but not in the render")
        return {key: str(value) for key, value in (document.get("data") or {}).items()}

    def secret(self, name: str, optional: bool = False) -> dict[str, str]:
        document = self.find("Secret", name)
        if document is None:
            if optional:
                return {}
            raise ReleaseError(
                f"Secret {name!r} is referenced but not in the render; an `existingSecret` "
                "has to be rendered by the driver as well"
            )
        data = {
            key: base64.b64decode(value).decode("utf-8")
            for key, value in (document.get("data") or {}).items()
        }
        # The API server merges `stringData` over `data` on write; so does this.
        data.update({key: str(value) for key, value in (document.get("stringData") or {}).items()})
        return data


@dataclass
class Container:
    """One container as Docker will run it."""

    name: str
    spec: dict[str, Any]
    docker_name: str


@dataclass
class Pod:
    """One replica of a workload, with everything resolved that Docker needs to start it."""

    name: str
    kind: str
    workload: str
    spec: dict[str, Any]
    labels: dict[str, str]
    aliases: list[str] = field(default_factory=list)
    init_containers: list[Container] = field(default_factory=list)
    containers: list[Container] = field(default_factory=list)
    claim_templates: set[str] = field(default_factory=set)

    @property
    def owner(self) -> Container:
        """The container that owns the pod's network namespace."""
        return self.containers[0]

    def container_port(self, port: int | str) -> int:
        """Resolve a numeric or named port against every container in the pod."""
        if isinstance(port, int) or str(port).isdigit():
            return int(port)
        for container in self.containers:
            for declared in container.spec.get("ports", []):
                if declared.get("name") == port:
                    return int(declared["containerPort"])
        raise ReleaseError(f"pod {self.name}: no container declares a port named {port!r}")


class Release:
    """A rendered release running on the local Docker daemon.

    Use as a context manager: everything it creates — containers, volumes, the network and the
    files it bind-mounts — is removed on exit, whether the run passed or not.
    """

    def __init__(self, manifests: Manifests, prefix: str | None = None) -> None:
        self.manifests = manifests
        self.prefix = prefix or f"e2e-{os.getpid()}"
        self.network = self.prefix
        self.pods = [self._pod(workload) for workload in manifests.of_kind(*WORKLOAD_KINDS)]
        self._volumes: set[str] = set()
        self._containers: list[str] = []
        self._files: Path | None = None
        self._assign_aliases()

    # ---------------------------------------------------------------------------------------------
    # Translation.
    # ---------------------------------------------------------------------------------------------

    def _pod(self, workload: dict[str, Any]) -> Pod:
        kind, name = workload["kind"], workload["metadata"]["name"]
        replicas = workload["spec"].get("replicas", 1)
        if replicas != 1:
            raise ReleaseError(f"{kind} {name} asks for {replicas} replicas; only 1 is supported")
        template = workload["spec"]["template"]
        pod_name = f"{name}-0" if kind == "StatefulSet" else name
        pod = Pod(
            name=pod_name,
            kind=kind,
            workload=name,
            spec=template["spec"],
            labels=template.get("metadata", {}).get("labels", {}),
        )
        pod.init_containers = [
            Container(c["name"], c, f"{self.prefix}-{pod_name}-init-{c['name']}")
            for c in template["spec"].get("initContainers", [])
        ]
        pod.containers = [
            Container(c["name"], c, f"{self.prefix}-{pod_name}-{c['name']}")
            for c in template["spec"]["containers"]
        ]
        pod.claim_templates = {
            claim["metadata"]["name"] for claim in workload["spec"].get("volumeClaimTemplates", [])
        }
        return pod

    def _assign_aliases(self) -> None:
        namespace = self.manifests.namespace
        for service in self.manifests.of_kind("Service"):
            name = service["metadata"]["name"]
            selector = service["spec"].get("selector") or {}
            if not selector:
                continue
            for pod in self.pods:
                if not all(pod.labels.get(key) == value for key, value in selector.items()):
                    continue
                for port in service["spec"].get("ports", []):
                    target = pod.container_port(port.get("targetPort", port["port"]))
                    if int(port["port"]) != target:
                        raise ReleaseError(
                            f"Service {name} maps port {port['port']} to {target}; Docker "
                            "network aliases cannot remap a port"
                        )
                pod.aliases += [
                    name,
                    f"{name}.{namespace}",
                    f"{name}.{namespace}.svc",
                    f"{name}.{namespace}.svc.cluster.local",
                ]
                if pod.kind == "StatefulSet" and service["spec"].get("clusterIP") == "None":
                    pod.aliases.append(f"{pod.name}.{name}")
                    pod.aliases.append(f"{pod.name}.{name}.{namespace}.svc.cluster.local")

    def _environment(self, pod: Pod, container: dict[str, Any]) -> dict[str, str]:
        """The container's environment as the kubelet would assemble it: `envFrom` first, then
        `env` in order, each `value` expanded against what was defined before it."""
        env: dict[str, str] = {}
        for source in container.get("envFrom", []):
            prefix = source.get("prefix", "")
            if "configMapRef" in source:
                reference = source["configMapRef"]
                data = self.manifests.config_map(
                    reference["name"], reference.get("optional", False)
                )
            elif "secretRef" in source:
                reference = source["secretRef"]
                data = self.manifests.secret(reference["name"], reference.get("optional", False))
            else:
                raise ReleaseError(f"pod {pod.name}: unsupported envFrom source {source}")
            env.update({f"{prefix}{key}": value for key, value in data.items()})

        for variable in container.get("env", []):
            name = variable["name"]
            if "value" in variable:
                env[name] = expand_references(str(variable["value"]), env)
                continue
            source = variable.get("valueFrom", {})
            if "secretKeyRef" in source:
                reference = source["secretKeyRef"]
                data = self.manifests.secret(reference["name"], reference.get("optional", False))
            elif "configMapKeyRef" in source:
                reference = source["configMapKeyRef"]
                data = self.manifests.config_map(
                    reference["name"], reference.get("optional", False)
                )
            elif "fieldRef" in source:
                path = source["fieldRef"]["fieldPath"]
                fields = {"metadata.name": pod.name, "metadata.namespace": self.manifests.namespace}
                if path not in fields:
                    raise ReleaseError(f"pod {pod.name}: unsupported fieldRef {path!r}")
                env[name] = fields[path]
                continue
            else:
                # An `env` entry with neither is legal and means an empty string.
                env[name] = ""
                continue
            key = reference["key"]
            if key in data:
                env[name] = data[key]
            elif not reference.get("optional", False):
                raise ReleaseError(f"pod {pod.name}: {reference['name']} has no key {key!r}")
        return env

    def _user_flags(self, pod: Pod, container: dict[str, Any], image: str) -> list[str]:
        pod_context = pod.spec.get("securityContext") or {}
        context = container.get("securityContext") or {}

        def setting(key: str) -> Any:
            return context.get(key, pod_context.get(key))

        flags: list[str] = []
        user, group = setting("runAsUser"), setting("runAsGroup")
        if setting("runAsNonRoot"):
            effective = user
            if effective is None:
                configured = docker("image", "inspect", "--format", "{{.Config.User}}", image)
                effective = configured.strip().split(":", 1)[0] or "0"
            # The kubelet only verifies a numeric user; a name it cannot resolve is refused too.
            if str(effective) in ("0", "root") or not str(effective).isdigit():
                raise ReleaseError(
                    f"pod {pod.name}: runAsNonRoot is set but the container would run as "
                    f"{effective!r}; the kubelet refuses to start it"
                )
        if user is not None:
            flags += ["--user", f"{user}:{group}" if group is not None else str(user)]
        for extra in [pod_context.get("fsGroup"), *pod_context.get("supplementalGroups", [])]:
            if extra is not None:
                flags += ["--group-add", str(extra)]

        if context.get("readOnlyRootFilesystem"):
            flags.append("--read-only")
        if context.get("privileged"):
            flags.append("--privileged")
        if context.get("allowPrivilegeEscalation") is False:
            flags += ["--security-opt", "no-new-privileges=true"]
        capabilities = context.get("capabilities") or {}
        for capability in capabilities.get("drop", []):
            flags += ["--cap-drop", capability]
        for capability in capabilities.get("add", []):
            flags += ["--cap-add", capability]
        seccomp = (setting("seccompProfile") or {}).get("type", "RuntimeDefault")
        if seccomp == "Unconfined":
            flags += ["--security-opt", "seccomp=unconfined"]
        elif seccomp != "RuntimeDefault":
            raise ReleaseError(f"pod {pod.name}: unsupported seccompProfile {seccomp!r}")
        return flags

    def _resource_flags(self, container: dict[str, Any]) -> list[str]:
        limits = (container.get("resources") or {}).get("limits") or {}
        flags: list[str] = []
        if "memory" in limits:
            flags += ["--memory", str(int(parse_quantity(limits["memory"])))]
        if "cpu" in limits:
            flags += ["--cpus", f"{parse_quantity(limits['cpu']):g}"]
        return flags

    def _volume_source(self, pod: Pod, name: str) -> tuple[str, str]:
        """Return (`volume` or `bind`, source) for one of the pod's volumes."""
        for volume in pod.spec.get("volumes", []):
            if volume["name"] != name:
                continue
            if "persistentVolumeClaim" in volume:
                claim = volume["persistentVolumeClaim"]["claimName"]
                return "volume", f"{self.prefix}-pvc-{claim}"
            if "emptyDir" in volume:
                if (volume["emptyDir"] or {}).get("medium") == "Memory":
                    raise ReleaseError(f"pod {pod.name}: emptyDir medium Memory is not supported")
                return "volume", f"{self.prefix}-{pod.name}-{name}"
            if "configMap" in volume or "secret" in volume:
                return "bind", str(self._materialise(pod, volume))
            raise ReleaseError(f"pod {pod.name}: unsupported volume type in {volume}")
        if name in pod.claim_templates:
            return "volume", f"{self.prefix}-pvc-{name}-{pod.name}"
        raise ReleaseError(f"pod {pod.name}: volumeMount names undeclared volume {name!r}")

    def _materialise(self, pod: Pod, volume: dict[str, Any]) -> Path:
        """Write a configMap or secret volume's keys to disk, for a read-only bind mount."""
        assert self._files is not None
        if "configMap" in volume:
            source = volume["configMap"]
            data = self.manifests.config_map(source["name"], source.get("optional", False))
        else:
            source = volume["secret"]
            data = self.manifests.secret(source["secretName"], source.get("optional", False))
        directory = self._files / pod.name / volume["name"]
        directory.mkdir(parents=True, exist_ok=True)
        items = source.get("items") or [{"key": key, "path": key} for key in data]
        for item in items:
            if item["key"] not in data:
                raise ReleaseError(f"pod {pod.name}: volume {volume['name']} has no key {item}")
            target = directory / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data[item["key"]], encoding="utf-8")
            target.chmod(0o644)
        return directory

    def _mount_flags(self, pod: Pod, container: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        for mount in container.get("volumeMounts", []):
            kind, source = self._volume_source(pod, mount["name"])
            options = [f"destination={mount['mountPath']}"]
            if kind == "volume":
                options = [f"type=volume,source={source}", *options, "volume-nocopy=true"]
                if mount.get("subPath"):
                    options.append(f"volume-subpath={mount['subPath']}")
            else:
                path = Path(source) / mount["subPath"] if mount.get("subPath") else Path(source)
                options = [f"type=bind,source={path}", *options]
            if mount.get("readOnly") or kind == "bind":
                options.append("readonly")
            flags += ["--mount", ",".join(options)]
        return flags

    def _run_flags(self, pod: Pod, container: Container, *, init: bool) -> list[str]:
        spec = container.spec
        image = image_reference(spec["image"])
        flags = ["--name", container.docker_name, "--label", f"{self.prefix}=1"]
        if init:
            flags += ["--network", self.network]
        elif container is pod.owner:
            flags += ["--network", self.network, "--hostname", pod.name]
            for alias in pod.aliases:
                flags += ["--network-alias", alias]
            # Every port of every container in the pod lands on the owner's namespace, so this is
            # the only container that can publish them. Loopback only: they exist for probes.
            for member in pod.containers:
                for port in member.spec.get("ports", []):
                    flags += ["--publish", f"127.0.0.1::{port['containerPort']}"]
        else:
            flags += ["--network", f"container:{pod.owner.docker_name}"]
        flags += self._user_flags(pod, spec, image)
        flags += self._resource_flags(spec)
        flags += self._mount_flags(pod, spec)
        if spec.get("workingDir"):
            flags += ["--workdir", spec["workingDir"]]
        command = spec.get("command") or []
        if command:
            flags += ["--entrypoint", command[0]]
        return [*flags, image, *command[1:], *[str(arg) for arg in spec.get("args", [])]]

    # ---------------------------------------------------------------------------------------------
    # Lifecycle.
    # ---------------------------------------------------------------------------------------------

    def images(self) -> list[str]:
        """Every image the release would pull, as Docker references, in render order."""
        seen: dict[str, None] = {}
        for pod in self.pods:
            for container in [*pod.init_containers, *pod.containers]:
                seen[image_reference(container.spec["image"])] = None
        return list(seen)

    def pull(self) -> None:
        """Pull every image, except one pinned by digest that is already present: a digest names
        exactly one image, so a local copy cannot be stale. A tag can move, so it is always
        pulled."""
        for image in self.images():
            present = "@" in image and subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, check=False
            ).returncode == 0
            if present:
                print(f"   have  {image}", flush=True)
                continue
            print(f"   pull  {image}", flush=True)
            docker("pull", "--quiet", image, timeout=1800)

    def __enter__(self) -> Release:
        self._files = Path(tempfile.mkdtemp(prefix=f"{self.prefix}-"))
        docker("network", "create", self.network)
        try:
            self._prepare_volumes()
            # Pods start concurrently, as a scheduler starts them: an init container waiting on
            # another pod's Service must not deadlock behind it.
            with ThreadPoolExecutor(max_workers=max(1, len(self.pods))) as executor:
                for future in [executor.submit(self._start, pod) for pod in self.pods]:
                    future.result()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *_: object) -> None:
        for name in reversed(self._containers):
            subprocess.run(["docker", "rm", "--force", "--volumes", name], capture_output=True)
        for volume in self._volumes:
            subprocess.run(["docker", "volume", "rm", "--force", volume], capture_output=True)
        subprocess.run(["docker", "network", "rm", self.network], capture_output=True)
        if self._files is not None:
            shutil.rmtree(self._files, ignore_errors=True)

    def _prepare_volumes(self) -> None:
        """Create every named volume and leave it as the kubelet leaves a fresh one: empty,
        group-owned and group-writable by the pod's fsGroup with the setgid bit (2775), or
        world-writable without one."""
        prepared: set[str] = set()
        for pod in self.pods:
            fs_group = (pod.spec.get("securityContext") or {}).get("fsGroup")
            for container in [*pod.init_containers, *pod.containers]:
                for mount in container.spec.get("volumeMounts", []):
                    kind, source = self._volume_source(pod, mount["name"])
                    if kind != "volume" or source in prepared:
                        continue
                    prepared.add(source)
                    docker("volume", "create", "--label", f"{self.prefix}=1", source)
                    self._volumes.add(source)
                    script = "chmod 0777 /volume"
                    if fs_group is not None:
                        script = f"chgrp {int(fs_group)} /volume && chmod 2775 /volume"
                    # Any image in the pod will do, so long as it has a shell; none is pulled
                    # that the release does not already need.
                    docker(
                        "run", "--rm", "--user", "0:0", "--network", "none",
                        "--mount", f"type=volume,source={source},destination=/volume",
                        "--entrypoint", "/bin/sh",
                        image_reference(container.spec["image"]), "-c", script,
                    )  # fmt: skip

    def _environ(self, pod: Pod, container: Container) -> tuple[list[str], dict[str, str]]:
        env = self._environment(pod, container.spec)
        flags = [flag for name in env for flag in ("--env", name)]
        return flags, {**os.environ, **env}

    def _start(self, pod: Pod) -> None:
        for container in pod.init_containers:
            env_flags, environ = self._environ(pod, container)
            self._containers.append(container.docker_name)
            try:
                docker("run", *env_flags, *self._run_flags(pod, container, init=True), env=environ)
            except ReleaseError as error:
                raise ReleaseError(
                    f"init container {container.name} of {pod.name} failed:\n"
                    f"{self.logs(container.docker_name)}"
                ) from error
        for container in pod.containers:
            env_flags, environ = self._environ(pod, container)
            self._containers.append(container.docker_name)
            docker(
                "run", "--detach", "--restart", "on-failure",
                *env_flags, *self._run_flags(pod, container, init=False),
                env=environ,
            )  # fmt: skip

    # ---------------------------------------------------------------------------------------------
    # Readiness and access.
    # ---------------------------------------------------------------------------------------------

    def pod(self, workload: str) -> Pod:
        for pod in self.pods:
            if pod.workload == workload:
                return pod
        raise ReleaseError(f"no workload named {workload!r} in the release")

    def host_port(self, workload: str, port: int | str) -> int:
        """The loopback port on the host that one of a pod's ports is published on."""
        pod = self.pod(workload)
        number = pod.container_port(port)
        published = docker("port", pod.owner.docker_name, f"{number}/tcp").splitlines()[0]
        return int(published.rsplit(":", 1)[1])

    def url(self, workload: str, port: int | str, scheme: str = "http") -> str:
        """A base URL on the host that reaches one of a pod's ports."""
        return f"{scheme}://127.0.0.1:{self.host_port(workload, port)}"

    def exec(self, workload: str, *command: str, container: str | None = None) -> str:
        pod = self.pod(workload)
        target = next(
            (c for c in pod.containers if container is None or c.name == container), None
        )
        if target is None:
            raise ReleaseError(f"pod {pod.name} has no container {container!r}")
        return docker("exec", target.docker_name, *command)

    def _probe(self, pod: Pod, container: Container, probe: dict[str, Any]) -> bool:
        timeout = float(probe.get("timeoutSeconds", 1))
        try:
            if "httpGet" in probe:
                action = probe["httpGet"]
                scheme = action.get("scheme", "HTTP").lower()
                url = self.url(pod.workload, action["port"], scheme) + action.get("path", "/")
                request = urllib.request.Request(url)
                for header in action.get("httpHeaders", []):
                    request.add_header(header["name"], header["value"])
                context = None
                if scheme == "https":
                    # The kubelet does not verify a probe's certificate, and neither does this.
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    return 200 <= response.status < 400
            if "tcpSocket" in probe:
                port = self.host_port(pod.workload, probe["tcpSocket"]["port"])
                with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                    return True
            if "exec" in probe:
                result = subprocess.run(
                    ["docker", "exec", container.docker_name, *probe["exec"]["command"]],
                    capture_output=True,
                    timeout=timeout + 5,
                    check=False,
                )
                return result.returncode == 0
        except (OSError, urllib.error.URLError, subprocess.TimeoutExpired, ReleaseError):
            return False
        raise ReleaseError(f"pod {pod.name}: unsupported probe {probe}")

    def _state(self, container: Container) -> tuple[str, int]:
        status, restarts = docker(
            "inspect", "--format", "{{.State.Status}} {{.RestartCount}}", container.docker_name
        ).split()
        return status, int(restarts)

    def wait_ready(self, timeout: float = 600) -> None:
        """Block until every container's readiness (or else startup) probe passes.

        Fails early, with logs, on a container that exited or is crash-looping: either is a
        verdict, and waiting out the timeout would only hide it behind a slower one.
        """
        deadline = time.monotonic() + timeout
        pending = {(pod.name, c.name): (pod, c) for pod in self.pods for c in pod.containers}
        while pending:
            for key, (pod, container) in list(pending.items()):
                status, restarts = self._state(container)
                if status in ("exited", "dead") or restarts > MAX_RESTARTS:
                    raise ReleaseError(
                        f"{pod.name}/{container.name} is {status} after {restarts} restart(s):\n"
                        f"{self.logs(container.docker_name)}"
                    )
                probe = container.spec.get("readinessProbe") or container.spec.get(
                    "startupProbe"
                )
                if status == "running" and (probe is None or self._probe(pod, container, probe)):
                    print(f"   ready {pod.name}/{container.name}", flush=True)
                    del pending[key]
            if not pending:
                return
            if time.monotonic() > deadline:
                names = ", ".join("/".join(key) for key in pending)
                logs = "\n".join(self.logs(c.docker_name) for _, c in pending.values())
                raise ReleaseError(f"not ready after {timeout:.0f}s: {names}\n{logs}")
            time.sleep(2)

    def logs(self, docker_name: str, tail: int = 80) -> str:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), docker_name],
            capture_output=True,
            text=True,
            check=False,
        )
        body = (result.stdout + result.stderr).rstrip()
        return f"--- logs: {docker_name} ---\n{body}\n--- end: {docker_name} ---"

    def all_logs(self, tail: int = 80) -> Iterator[str]:
        for name in self._containers:
            yield self.logs(name, tail)
