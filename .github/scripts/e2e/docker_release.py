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
                            kubelet's `$(VAR)` expansion, which also applies to `command` and
                            `args`. Handed to Docker in a private env file, never through argv,
                            where a secret would show in a process listing, and never through the
                            Docker client's own environment, where a rendered `PATH`, `HOME` or
                            `DOCKER_HOST` would redirect the client itself
  securityContext           runAsUser/runAsGroup (a group alone keeps the image's user, as the
                            kubelet does), fsGroup and supplementalGroups as groups,
                            readOnlyRootFilesystem, capabilities, allowPrivilegeEscalation, and the
                            kubelet's refusal to start a runAsNonRoot container as root
  resources.limits          memory and cpu, so a limit too small for the workload fails here as it
                            would be OOM-killed there; requests have no Docker equivalent
  volumes                   persistentVolumeClaim and volumeClaimTemplates as named volumes shared
                            by claim name; emptyDir as a named volume per pod; configMap and secret
                            as a read-only volume per pod holding their keys with `defaultMode` or
                            per-item `mode`. All start empty (`volume-nocopy`) and are owned as the
                            kubelet leaves them — see `_prepare_volumes` — and a `subPath` is
                            created before the mount, as the kubelet creates it
  probes                    a startup probe gates the readiness probe, as it does in Kubernetes:
                            httpGet and tcpSocket from the host through a loopback-published port,
                            exec through `docker exec`

What is not
-----------
NetworkPolicy, scheduling, RBAC, and anything a controller reconciles. Those are covered by the
render, policy and install gates; this module exists for the behaviour none of them can see.
Anything the translation does not understand is refused with an error naming it, never skipped
and never approximated more permissively than Kubernetes: a test that quietly ran something other
than what the chart renders, or passed where the cluster would fail, would be worse than none.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
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

# The kubelet's default for configMap and secret volume files when neither `defaultMode` nor an
# item's `mode` says otherwise.
DEFAULT_FILE_MODE = 0o644

_QUANTITY = re.compile(r"^([0-9.]+)([A-Za-z]*)$")
_BINARY_SUFFIXES = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
_DECIMAL_SUFFIXES = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "m": 10**-3}
_ENV_REFERENCE = re.compile(r"\$\$|\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
_SUBPATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+$")


class ReleaseError(RuntimeError):
    """The release could not be run, or the render asks for something this module cannot honour."""


def docker(*args: str, timeout: float | None = None) -> str:
    """Run a docker command and return its stdout, raising ReleaseError on a non-zero exit.

    The client always runs with this process's own environment: nothing a chart renders reaches
    it, so a rendered variable can never change which daemon, config or binary it uses.
    """
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout
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
    """The documents one `helm template` produced, indexed for lookup.

    Every namespaced object has to be in the release namespace: a reference from a pod resolves
    only within its own namespace, and resolving one across namespaces would start a pod the API
    server never could.
    """

    def __init__(self, documents: list[dict[str, Any]], namespace: str = "default") -> None:
        self.documents = [document for document in documents if document]
        self.namespace = namespace
        for document in self.documents:
            declared = (document.get("metadata") or {}).get("namespace")
            if declared is not None and declared != namespace:
                raise ReleaseError(
                    f"{document.get('kind')} {document['metadata'].get('name')} is in namespace "
                    f"{declared!r}, not the release namespace {namespace!r}; cross-namespace "
                    "references cannot be modelled"
                )

    @classmethod
    def from_yaml(cls, text: str, namespace: str = "default") -> Manifests:
        """Parse a multi-document YAML stream as `helm template` prints it."""
        return cls(list(yaml.safe_load_all(text)), namespace)

    def of_kind(self, *kinds: str) -> list[dict[str, Any]]:
        """Every document of one of the given kinds, in render order."""
        return [document for document in self.documents if document.get("kind") in kinds]

    def find(self, kind: str, name: str) -> dict[str, Any] | None:
        """The document of this kind and name, or None."""
        for document in self.of_kind(kind):
            if document["metadata"]["name"] == name:
                return document
        return None

    def config_map(self, name: str, optional: bool = False) -> dict[str, str]:
        """A ConfigMap's `data`; an absent optional one is empty, an absent required one fails."""
        document = self.find("ConfigMap", name)
        if document is None:
            if optional:
                return {}
            raise ReleaseError(f"ConfigMap {name!r} is referenced but not in the render")
        return {key: str(value) for key, value in (document.get("data") or {}).items()}

    def secret(self, name: str, optional: bool = False) -> dict[str, str]:
        """A Secret's decoded data, with `stringData` merged over `data` as the API server does."""
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
        data.update({key: str(value) for key, value in (document.get("stringData") or {}).items()})
        return data


@dataclass
class Container:
    """One container as Docker will run it."""

    name: str
    spec: dict[str, Any]
    docker_name: str


@dataclass
class Volume:
    """A Docker named volume backing one pod volume.

    `kind` is `claim` (a PersistentVolumeClaim, shared by claim name), `empty` (an emptyDir) or
    `projected` (a configMap or secret, filled with its keys and mounted read-only).
    """

    name: str
    kind: str
    image: str
    fs_group: int | None
    definition: dict[str, Any] = field(default_factory=dict)
    subpaths: set[str] = field(default_factory=set)


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

    @property
    def fs_group(self) -> int | None:
        value = (self.spec.get("securityContext") or {}).get("fsGroup")
        return None if value is None else int(value)

    def container_port(self, port: int | str) -> int:
        """Resolve a numeric or named port against every container in the pod."""
        if isinstance(port, int) or str(port).isdigit():
            return int(port)
        for container in self.containers:
            for declared in container.spec.get("ports", []):
                if declared.get("name") == port:
                    return int(declared["containerPort"])
        raise ReleaseError(f"pod {self.name}: no container declares a port named {port!r}")

    def published_ports(self) -> list[int]:
        """Every port the host has to reach: those declared, and those any probe targets, which
        a probe may name by number without the container declaring it."""
        ports: dict[int, None] = {}
        for container in self.containers:
            for declared in container.spec.get("ports", []):
                ports[int(declared["containerPort"])] = None
            for kind in ("startupProbe", "readinessProbe", "livenessProbe"):
                probe = container.spec.get(kind) or {}
                action = probe.get("httpGet") or probe.get("tcpSocket")
                if action is not None:
                    ports[self.container_port(action["port"])] = None
        return list(ports)


class Release:
    """A rendered release running on the local Docker daemon.

    Use as a context manager: everything it creates — containers, volumes, the network and the
    env files — is removed on exit, whether the run passed or not.
    """

    def __init__(self, manifests: Manifests, prefix: str | None = None) -> None:
        self.manifests = manifests
        self.prefix = prefix or f"e2e-{os.getpid()}"
        self.network = self.prefix
        self.pods = [self._pod(workload) for workload in manifests.of_kind(*WORKLOAD_KINDS)]
        self._volumes: dict[str, Volume] = {}
        self._containers: list[str] = []
        self._files: Path | None = None
        self._image_users: dict[str, str] = {}
        self._image_volumes: dict[str, list[str]] = {}
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
            source = variable.get("valueFrom")
            if not source:
                # An entry with neither `value` nor `valueFrom` is legal and means "".
                env[name] = ""
                continue
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
                raise ReleaseError(f"pod {pod.name}: unsupported valueFrom for {name}: {source}")
            key = reference["key"]
            if key in data:
                env[name] = data[key]
            elif not reference.get("optional", False):
                raise ReleaseError(f"pod {pod.name}: {reference['name']} has no key {key!r}")
        return env

    def _image_user(self, image: str) -> str:
        """The user an image runs as when nothing overrides it; `0` when it declares none."""
        if image not in self._image_users:
            configured = docker("image", "inspect", "--format", "{{.Config.User}}", image)
            self._image_users[image] = configured.strip().split(":", 1)[0] or "0"
        return self._image_users[image]

    def _image_volume_paths(self, image: str) -> list[str]:
        """The paths an image declares with `VOLUME`."""
        if image not in self._image_volumes:
            declared = docker("image", "inspect", "--format", "{{json .Config.Volumes}}", image)
            self._image_volumes[image] = sorted(yaml.safe_load(declared) or {})
        return self._image_volumes[image]

    def _user_flags(self, pod: Pod, container: dict[str, Any], image: str) -> list[str]:
        pod_context = pod.spec.get("securityContext") or {}
        context = container.get("securityContext") or {}

        def setting(key: str) -> Any:
            return context.get(key, pod_context.get(key))

        flags: list[str] = []
        user, group = setting("runAsUser"), setting("runAsGroup")
        if user is not None or group is not None or setting("runAsNonRoot"):
            # A group without a user keeps the image's user with that primary group, which is
            # what the kubelet does, rather than dropping the group.
            effective = str(user) if user is not None else self._image_user(image)
            if setting("runAsNonRoot") and (effective in ("0", "root") or not effective.isdigit()):
                # The kubelet only verifies a numeric user; a name it cannot verify is refused.
                raise ReleaseError(
                    f"pod {pod.name}: runAsNonRoot is set but the container would run as "
                    f"{effective!r}; the kubelet refuses to start it"
                )
            if user is not None or group is not None:
                flags += ["--user", effective if group is None else f"{effective}:{group}"]
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

    def _volume(self, pod: Pod, name: str, image: str) -> Volume:
        """The named volume backing one of the pod's volumes, registered on first use."""
        definition: dict[str, Any] | None = None
        for candidate in pod.spec.get("volumes", []):
            if candidate["name"] == name:
                definition = candidate
                break
        if definition is None:
            if name not in pod.claim_templates:
                raise ReleaseError(f"pod {pod.name}: volumeMount names undeclared volume {name!r}")
            docker_name, kind = f"{self.prefix}-pvc-{name}-{pod.name}", "claim"
        elif "persistentVolumeClaim" in definition:
            claim = definition["persistentVolumeClaim"]["claimName"]
            docker_name, kind = f"{self.prefix}-pvc-{claim}", "claim"
        elif "emptyDir" in definition:
            if (definition["emptyDir"] or {}).get("medium") == "Memory":
                raise ReleaseError(f"pod {pod.name}: emptyDir medium Memory is not supported")
            docker_name, kind = f"{self.prefix}-{pod.name}-{name}", "empty"
        elif "configMap" in definition or "secret" in definition:
            docker_name, kind = f"{self.prefix}-{pod.name}-{name}", "projected"
        else:
            raise ReleaseError(f"pod {pod.name}: unsupported volume type in {definition}")

        volume = self._volumes.get(docker_name)
        if volume is None:
            volume = Volume(docker_name, kind, image, pod.fs_group, definition or {})
            self._volumes[docker_name] = volume
        elif kind == "claim" and volume.fs_group != pod.fs_group:
            raise ReleaseError(
                f"claim volume {docker_name} is mounted by pods with different fsGroups "
                f"({volume.fs_group} and {pod.fs_group}); the kubelet's ownership is ambiguous"
            )
        return volume

    def _mount_flags(self, pod: Pod, container: dict[str, Any]) -> list[str]:
        image = image_reference(container["image"])
        flags: list[str] = []
        for mount in container.get("volumeMounts", []):
            volume = self._volume(pod, mount["name"], image)
            options = [
                f"type=volume,source={volume.name}",
                f"destination={mount['mountPath']}",
                "volume-nocopy=true",
            ]
            subpath = mount.get("subPath")
            if subpath:
                if not _SUBPATH.match(subpath):
                    raise ReleaseError(f"pod {pod.name}: subPath {subpath!r} escapes its volume")
                volume.subpaths.add(subpath)
                options.append(f"volume-subpath={subpath}")
            if mount.get("readOnly") or volume.kind == "projected":
                options.append("readonly")
            flags += ["--mount", ",".join(options)]

        # A path the image declares with `VOLUME` is an ordinary directory of the image to the
        # kubelet, but Docker mounts a writable anonymous volume there. Under a read-only root
        # filesystem that would let a container write where the cluster would refuse, so such a
        # path is mounted read-only, with the image's content, as the cluster presents it.
        mounts = container.get("volumeMounts", [])
        mounted = [str(mount["mountPath"]).rstrip("/") for mount in mounts]
        read_only = bool((container.get("securityContext") or {}).get("readOnlyRootFilesystem"))
        for declared in self._image_volume_paths(image):
            path = declared.rstrip("/")
            if path in mounted:
                continue
            if any(path.startswith(f"{parent}/") for parent in mounted):
                raise ReleaseError(
                    f"pod {pod.name}: image VOLUME {path} lies inside a mounted volume, where "
                    "Docker would shadow the mount's content and the kubelet would not"
                )
            if read_only:
                flags += ["--mount", f"type=volume,destination={path},readonly"]
        return flags

    def _run_flags(
        self, pod: Pod, container: Container, env_file: Path, env: dict[str, str], *, init: bool
    ) -> list[str]:
        spec = container.spec
        image = image_reference(spec["image"])
        flags = [
            "--name", container.docker_name,
            "--label", f"{self.prefix}=1",
            "--env-file", str(env_file),
        ]  # fmt: skip
        if init:
            flags += ["--network", self.network]
        elif container is pod.owner:
            flags += ["--network", self.network, "--hostname", pod.name]
            for alias in pod.aliases:
                flags += ["--network-alias", alias]
            # Every port of every container in the pod lands on the owner's namespace, so this is
            # the only container that can publish them. Loopback only: they exist for probes and
            # for the driver.
            for port in pod.published_ports():
                flags += ["--publish", f"127.0.0.1::{port}"]
        else:
            flags += ["--network", f"container:{pod.owner.docker_name}"]
        flags += self._user_flags(pod, spec, image)
        flags += self._resource_flags(spec)
        flags += self._mount_flags(pod, spec)
        if spec.get("workingDir"):
            flags += ["--workdir", spec["workingDir"]]
        # The kubelet expands `$(VAR)` in both; Docker expands neither.
        command = [expand_references(str(part), env) for part in spec.get("command") or []]
        args = [expand_references(str(part), env) for part in spec.get("args") or []]
        if command:
            flags += ["--entrypoint", command[0]]
        return [*flags, image, *command[1:], *args]

    def _env_file(self, container: Container, env: dict[str, str]) -> Path:
        """Write a container's environment to an owner-only file in the private work directory.

        Docker's env-file format has no quoting, so a value holding a line break cannot be
        represented; it is refused rather than truncated.
        """
        assert self._files is not None
        lines = []
        for name, value in env.items():
            if "\n" in value or "\r" in value or "\0" in value:
                raise ReleaseError(
                    f"{container.docker_name}: env {name} holds a line break, which Docker's "
                    "env-file format cannot carry"
                )
            lines.append(f"{name}={value}\n")
        path = self._files / f"{container.docker_name}.env"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        return path

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
            # Translating every container first registers every volume and subPath, and fails on
            # anything unsupported before a single container has started.
            plans = {pod.name: self._plan(pod) for pod in self.pods}
            self._prepare_volumes()
            # Pods start concurrently, as a scheduler starts them: an init container waiting on
            # another pod's Service must not deadlock behind it.
            with ThreadPoolExecutor(max_workers=max(1, len(self.pods))) as executor:
                futures = [executor.submit(self._start, pod, plans[pod.name]) for pod in self.pods]
                for future in futures:
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

    def _plan(self, pod: Pod) -> list[tuple[Container, list[str], bool]]:
        """Every container's `docker run` arguments, init containers first."""
        plan = []
        for container, init in [
            *((c, True) for c in pod.init_containers),
            *((c, False) for c in pod.containers),
        ]:
            env = self._environment(pod, container.spec)
            env_file = self._env_file(container, env)
            flags = self._run_flags(pod, container, env_file, env, init=init)
            plan.append((container, flags, init))
        return plan

    def _projected_files(self, volume: Volume) -> list[tuple[str, str, int]]:
        """(path, content, mode) for every file a configMap or secret volume projects."""
        definition = volume.definition
        if "configMap" in definition:
            source = definition["configMap"]
            data = self.manifests.config_map(source["name"], source.get("optional", False))
        else:
            source = definition["secret"]
            data = self.manifests.secret(source["secretName"], source.get("optional", False))
        default_mode = int(source.get("defaultMode", DEFAULT_FILE_MODE))
        items = source.get("items") or [{"key": key, "path": key} for key in data]
        files = []
        for item in items:
            if item["key"] not in data:
                if source.get("optional", False):
                    continue
                raise ReleaseError(f"volume {volume.name} has no key {item['key']!r}")
            if not _SUBPATH.match(item["path"]):
                raise ReleaseError(f"volume {volume.name}: item path {item['path']!r} escapes it")
            files.append((item["path"], data[item["key"]], int(item.get("mode", default_mode))))
        return files

    def _prepare_volumes(self) -> None:
        """Create every named volume and leave it as the kubelet leaves a fresh one.

          emptyDir     0777; with an fsGroup, group-owned by it with the setgid bit (2777)
          claim        root-owned 0755, as most storage presents a fresh volume; with an fsGroup,
                       group-owned and group-writable with the setgid bit (2775). Deliberately not
                       the 0777 some local provisioners hand out: a pod that can only write to its
                       volume because the test was more permissive than the cluster is a false pass
          projected    root-owned files with their `defaultMode` or item `mode`; with an fsGroup,
                       group-owned by it and group-readable, as the kubelet adjusts them

        A `subPath` is created beforehand with its volume's mode, as the kubelet creates it, so
        Docker can mount it.
        """
        assert self._files is not None
        for volume in self._volumes.values():
            docker("volume", "create", "--label", f"{self.prefix}=1", volume.name)
            group = volume.fs_group
            if volume.kind == "empty":
                mode = 0o2777 if group is not None else 0o777
            elif volume.kind == "claim":
                mode = 0o2775 if group is not None else 0o755
            else:
                mode = 0o755
            owner = f"0:{group if group is not None else 0}"
            # Recursive for a projected volume, whose staged files arrive owned by this host's user.
            chown = "chown -R" if volume.kind == "projected" else "chown"
            commands = [f"{chown} {owner} /volume", f"chmod {mode:o} /volume"]
            for subpath in sorted(volume.subpaths):
                target = shlex.quote(f"/volume/{subpath}")
                commands += [f"mkdir -p {target}", f"chmod {mode:o} {target}"]

            staging: Path | None = None
            if volume.kind == "projected":
                staging = self._files / "projected" / volume.name
                staging.mkdir(parents=True)
                for path, content, file_mode in self._projected_files(volume):
                    (staging / path).parent.mkdir(parents=True, exist_ok=True)
                    (staging / path).write_text(content, encoding="utf-8")
                    if group is not None:
                        file_mode |= 0o040
                    target = shlex.quote(f"/volume/{path}")
                    commands.append(f"chmod {file_mode:o} {target}")

            # Any image the pod already needs will do, so long as it has a shell, chown and chmod.
            helper = f"{volume.name}-prepare"
            docker(
                "create", "--name", helper, "--user", "0:0", "--network", "none",
                "--mount", f"type=volume,source={volume.name},destination=/volume",
                "--entrypoint", "/bin/sh", volume.image, "-c", " && ".join(commands),
            )  # fmt: skip
            try:
                if staging is not None:
                    docker("cp", f"{staging}/.", f"{helper}:/volume")
                docker("start", "--attach", helper)
            finally:
                # `--volumes` also removes the anonymous volumes Docker made for the image's
                # own `VOLUME` paths.
                subprocess.run(
                    ["docker", "rm", "--force", "--volumes", helper], capture_output=True
                )

    def _start(self, pod: Pod, plan: list[tuple[Container, list[str], bool]]) -> None:
        for container, flags, init in plan:
            self._containers.append(container.docker_name)
            if init:
                try:
                    docker("run", *flags)
                except ReleaseError as error:
                    raise ReleaseError(
                        f"init container {container.name} of {pod.name} failed:\n"
                        f"{self.logs(container.docker_name)}"
                    ) from error
            else:
                docker("run", "--detach", "--restart", "on-failure", *flags)

    # ---------------------------------------------------------------------------------------------
    # Readiness and access.
    # ---------------------------------------------------------------------------------------------

    def pod(self, workload: str) -> Pod:
        """The pod a workload runs."""
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

    def _probe(self, pod: Pod, container: Container, probe: dict[str, Any]) -> bool:
        """Run one probe once, as the kubelet would; False on any failure to get a pass."""
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

    def _check_alive(self, pod: Pod, container: Container) -> str:
        """Return the container's status, raising on one that exited or is crash-looping."""
        status, restarts = docker(
            "inspect", "--format", "{{.State.Status}} {{.RestartCount}}", container.docker_name
        ).split()
        if status in ("exited", "dead") or int(restarts) > MAX_RESTARTS:
            raise ReleaseError(
                f"{pod.name}/{container.name} is {status} after {restarts} restart(s):\n"
                f"{self.logs(container.docker_name)}"
            )
        return status

    def wait_ready(self, timeout: float = 600) -> None:
        """Block until every container is ready: its startup probe has passed, if it has one,
        and then its readiness probe passes, if it has one.

        Every container is re-inspected on every round, ready or not, and once more before
        returning, so one that dies while another is still starting fails the wait. An exited or
        crash-looping container fails it at once: either is a verdict, and waiting out the
        timeout would only hide it behind a slower one. A release with no container at all is
        refused rather than declared ready.
        """
        members = [(pod, container) for pod in self.pods for container in pod.containers]
        if not members:
            raise ReleaseError("the render holds no Deployment or StatefulSet to wait for")
        deadline = time.monotonic() + timeout
        started: set[str] = set()
        ready: set[str] = set()
        while True:
            for pod, container in members:
                status = self._check_alive(pod, container)
                key = container.docker_name
                if key in ready or status != "running":
                    continue
                startup = container.spec.get("startupProbe")
                if startup is not None and key not in started:
                    if not self._probe(pod, container, startup):
                        continue
                    started.add(key)
                readiness = container.spec.get("readinessProbe")
                if readiness is None or self._probe(pod, container, readiness):
                    print(f"   ready {pod.name}/{container.name}", flush=True)
                    ready.add(key)
            if len(ready) == len(members):
                for pod, container in members:
                    if self._check_alive(pod, container) != "running":
                        raise ReleaseError(f"{pod.name}/{container.name} stopped running")
                return
            if time.monotonic() > deadline:
                pending = [(p, c) for p, c in members if c.docker_name not in ready]
                names = ", ".join(f"{p.name}/{c.name}" for p, c in pending)
                logs = "\n".join(self.logs(c.docker_name) for _, c in pending)
                raise ReleaseError(f"not ready after {timeout:.0f}s: {names}\n{logs}")
            time.sleep(2)

    def logs(self, docker_name: str, tail: int = 80) -> str:
        """The last lines a container wrote, framed for a failure report."""
        result = subprocess.run(
            ["docker", "logs", "--tail", str(tail), docker_name],
            capture_output=True,
            text=True,
            check=False,
        )
        body = (result.stdout + result.stderr).rstrip()
        return f"--- logs: {docker_name} ---\n{body}\n--- end: {docker_name} ---"

    def all_logs(self, tail: int = 80) -> Iterator[str]:
        """The logs of every container this release started."""
        for name in self._containers:
            yield self.logs(name, tail)
