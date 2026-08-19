#!/usr/bin/env python3
"""Navigating the manifests `just render` produced.

Nothing here knows what a configuration contract is. It answers the questions the gates ask of a
rendered chart — which object does this selector name, which containers does that workload run,
what does this container mount and what file names will appear there — and it answers them the
same way for every chart, so a gate never grows a special case for one.

Rendering goes through `just render`, never a direct `helm template`: that recipe carries the CRD
`--api-versions` declarations and the retry around the network `$ref`s in `values.schema.json`,
and it is what guarantees the manifests read here are byte-identical to the ones kubeconform and
kube-linter see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_manifests(path: Path) -> list[dict[str, Any]]:
    documents = yaml.safe_load_all(path.read_text(encoding="utf-8"))
    return [document for document in documents if isinstance(document, dict)]


def select(manifests: list[dict[str, Any]], kind: str, selector: dict[str, str]) -> list[dict]:
    """Every object of `kind` whose labels are a superset of `selector`.

    Matched by label rather than by rendered name: `fullnameOverride` moves the name of every
    object a chart creates, so a selector written against one would silently match nothing under
    a values file that sets it. The caller reports a selector that matches zero or several
    objects rather than resolving it, which is what keeps a stale selector from turning into a
    skipped check.
    """
    matched = []
    for manifest in manifests:
        if manifest.get("kind") != kind:
            continue
        labels = (manifest.get("metadata") or {}).get("labels") or {}
        if all(labels.get(name) == value for name, value in selector.items()):
            matched.append(manifest)
    return matched


def names_of(manifests: list[dict[str, Any]]) -> str:
    return ", ".join((entry.get("metadata") or {}).get("name", "?") for entry in manifests)


def pod_spec(workload: dict[str, Any]) -> dict[str, Any]:
    """The pod spec of a Deployment, StatefulSet, Job or CronJob, whichever nesting it uses."""
    spec = workload.get("spec") or {}
    template = spec.get("template") or spec.get("jobTemplate") or {}
    if "spec" in template and "template" in (template.get("spec") or {}):
        template = template["spec"]["template"]
    return (template.get("spec") or {}) if template else spec


def containers_of(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every container by name, init containers included — they read the same configuration."""
    found = {}
    for group in ("initContainers", "containers"):
        for container in spec.get(group) or []:
            if isinstance(container, dict) and container.get("name"):
                found[container["name"]] = container
    return found


def environment_of(container: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    """A container's environment as `(name -> value, the names whose value is not visible)`.

    A `valueFrom` entry is a name the gate can classify and a value it cannot see until the pod
    runs. Both halves are returned so a caller checks the spelling and skips the value, rather
    than either ignoring the variable or checking an empty string against a constraint.
    """
    values: dict[str, str] = {}
    opaque: set[str] = set()
    for entry in container.get("env") or []:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        name = str(entry["name"])
        if "value" in entry:
            values[name] = str(entry["value"])
        else:
            values[name] = ""
            opaque.add(name)
    return values, opaque


def mount_paths(container: dict[str, Any]) -> list[str]:
    return [
        str(mount.get("mountPath"))
        for mount in container.get("volumeMounts") or []
        if mount.get("mountPath")
    ]


def volume_at(container: dict[str, Any], path: str) -> str | None:
    """The volume mounted exactly at `path`, which is what makes its file names readable."""
    for mount in container.get("volumeMounts") or []:
        if str(mount.get("mountPath")).rstrip("/") == path.rstrip("/"):
            return str(mount.get("name"))
    return None


def inside_a_mount(container: dict[str, Any], path: str) -> bool:
    """Whether `path` names something under a volume this container actually mounts."""
    for mounted in mount_paths(container):
        prefix = mounted.rstrip("/") + "/"
        if path == mounted or path.startswith(prefix):
            return True
    return False


def projected_file_names(
    manifests: list[dict[str, Any]], spec: dict[str, Any], volume_name: str
) -> list[str]:
    """The file names one volume actually presents, following it back to the rendered object.

    `common.fileConfig` mounts credentials as a `projected` volume of `secret` sources with
    explicit `items`, so the names are usually right there. A source without `items` presents
    every key of the object it names, which is why the rendered Secret is looked up rather than
    assumed empty — a chart pointing at an `existingSecret` is the case that would otherwise go
    unchecked.
    """
    volume = next(
        (entry for entry in spec.get("volumes") or [] if entry.get("name") == volume_name), None
    )
    if volume is None:
        return []

    sources: list[dict[str, Any]] = []
    if "projected" in volume:
        sources = volume["projected"].get("sources") or []
    elif "secret" in volume:
        sources = [{"secret": {**volume["secret"], "name": volume["secret"].get("secretName")}}]
    elif "configMap" in volume:
        sources = [{"configMap": volume["configMap"]}]

    names: list[str] = []
    for source in sources:
        for field_name, kind in (("secret", "Secret"), ("configMap", "ConfigMap")):
            body = source.get(field_name)
            if not isinstance(body, dict):
                continue
            if body.get("items"):
                names.extend(str(item.get("path") or item.get("key")) for item in body["items"])
                continue
            for manifest in manifests:
                metadata = manifest.get("metadata") or {}
                if manifest.get("kind") == kind and metadata.get("name") == body.get("name"):
                    names.extend(manifest.get("data") or {})
                    names.extend(manifest.get("stringData") or {})
    return sorted(set(names))


def digest_of(reference: str) -> str | None:
    """The `sha256:...` a rendered container image pins, if it pins one."""
    return reference.split("@", 1)[1] if "@" in reference else None
