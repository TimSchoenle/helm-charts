#!/usr/bin/env python3
"""Gates 2 and 3 — one container's environment and file mounts, against its own image's contract.

Never the union. A container runs exactly one image, so a variable set on it that only a sibling
image reads is precisely the defect gate 2 exists to catch, and checking it against the merged
contract of every image reading the shared document reintroduces what splitting the scopes
removed. Gate 1, in `config_gate_document.py`, is the opposite case: a file every binary reads.

The container is scanned once into a `ContainerView`, because the two gates are not independent.
The environment scan finds the secrets directory and every `_FILE` target, which is what gate 3
needs to know; gate 3 finds which keys a mounted file supplies, which is what the layer-collision
check needs on top of what gate 2 found. Three passes over one container, sharing one reading of
it, rather than three readings that could disagree.

Each gate is a small class with one `check` returning findings, so a test constructs a container
dict and a contract and reads the list back — no registry, no cluster, no render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import config_contract as cc
from config_manifests import (
    environment_of,
    inside_a_mount,
    projected_file_names,
    volume_at,
)
from config_report import Finding, error, warning

# The layers that are mutually exclusive per key. The TOML file is deliberately not among them:
# it is outranked rather than refused, and a chart supplying a key by both file and environment
# is legal — only these three collide with one another.
ENVIRONMENT_LAYER = "the environment"
SECRETS_LAYER = "the secrets directory"


@dataclass
class ContainerView:
    """One container, read once: its environment classified, its loader variables resolved."""

    name: str
    container: dict[str, Any]
    values: dict[str, str]
    opaque: set[str]
    secrets_dir: str | None = None
    # Paths named by a `_FILE` variable, so gate 3 can tell a credential read by indirection from
    # one merely lying in a volume nothing will open.
    indirect: dict[str, str] = field(default_factory=dict)

    @classmethod
    def read(cls, container: dict[str, Any], union: cc.Union) -> ContainerView:
        values, opaque = environment_of(container)
        view = cls(
            name=str(container.get("name") or "?"),
            container=container,
            values=values,
            opaque=opaque,
        )
        for variable, value in values.items():
            decision = cc.classify(union, variable)
            if decision.kind == cc.LOADER and (decision.entry or {}).get("role") == "secrets_dir":
                view.secrets_dir = value
            elif decision.kind == cc.KEY_ENV_FILE and value and variable not in view.opaque:
                view.indirect[value] = variable
        return view

    def visible(self, variable: str) -> bool:
        """Whether the value is readable from the manifest, rather than a runtime `valueFrom`."""
        return variable not in self.opaque


@dataclass
class Suppliers:
    """Which layer supplies each key, accumulated across gates 2 and 3.

    A key supplied by two of the environment, the secrets directory and `_FILE` indirection is a
    boot failure under the loader's default `ShadowPolicy::Reject`. Nothing else in this
    repository can see it, because seeing it requires knowing that `PORTFOLIO_GITHUB__TOKEN` and
    the file `github__token` are the same key.
    """

    layers: dict[str, set[str]] = field(default_factory=dict)

    def add(self, path: str, layer: str) -> None:
        self.layers.setdefault(path, set()).add(layer)


class ServiceLinkGate:
    """`enableServiceLinks: false` is a precondition of this gate, not a preference.

    Kubernetes injects `<SERVICE_NAME>_SERVICE_HOST`, `<SERVICE_NAME>_PORT` and five more per
    Service in the namespace, and the service name is the *release* name — so for a release named
    after the chart they land inside the loader's own prefix, and one of them can *supply* a key
    from the environment layer, outranking the mounted file. That is a live misconfiguration, not
    merely a validation nuisance.

    It has to be checked directly rather than falling out of gate 2's step 4, because the kubelet
    injects these at pod admission and `helm template` does not: a rendered manifest never carries
    one, so no amount of classification would find it. This gate can only require the switch that
    stops them existing.

    An image cannot declare them away either — it cannot know the release names it will be
    deployed under, so no declaration written at build time is right for every case. It belongs to
    whatever renders the deployment, which does know.
    """

    def check(self, spec: dict[str, Any], union: cc.Union) -> list[Finding]:
        if spec.get("enableServiceLinks") is False:
            return []
        return [
            error(
                "pod: the pod spec does not set `enableServiceLinks: false`. Kubernetes injects "
                "seven variables per Service in the namespace, named after the release, so a "
                f"release named for this chart puts them inside the {union.prefix} namespace, "
                "where one can supply a key from the environment layer and outrank the mounted "
                "file. The kubelet injects them at admission rather than `helm template` doing "
                "it, so this gate cannot see them and can only require the switch."
            )
        ]


class EnvironmentGate:
    """Gate 2 — every variable on one container, classified and checked.

    The classification order in `config_contract.classify` is normative and first match wins.
    Every variable that names a key is then checked twice: its text against `text_constraint`,
    then the value that text parses to against `constraint`. Both, or the document's bounds are
    decorative and a render that passes every gate still fails at boot.
    """

    def check(
        self,
        view: ContainerView,
        union: cc.Union,
        suppliers: Suppliers,
        relaxed: set[str],
    ) -> list[Finding]:
        findings: list[Finding] = []

        for variable, value in sorted(view.values.items()):
            decision = cc.classify(union, variable)
            entry = decision.entry or {}

            if decision.kind == cc.KEY_ENV:
                suppliers.add(entry["path"], ENVIRONMENT_LAYER)
                if "env" not in relaxed and view.visible(variable):
                    findings.extend(self._value(view, variable, value, entry))

            elif decision.kind == cc.KEY_ENV_FILE:
                suppliers.add(entry["path"], f"the file named by {variable}")

            elif decision.kind == cc.PREFIXED and "env" not in relaxed:
                spellings = [key["env"] for key in union.keys.values() if key.get("env")]
                findings.append(
                    error(
                        f"env: {variable} set by container {view.name!r} matches no key in the "
                        "contract" + cc.suggest(variable, spellings)
                    )
                )

            elif decision.kind == cc.EXTERNAL:
                if "env" not in relaxed and view.visible(variable):
                    findings.extend(self._value(view, variable, value, entry))

            elif decision.kind == cc.UNKNOWN and "env" not in relaxed:
                findings.extend(self._unaccounted(view, variable, union))

        return findings

    def _value(
        self, view: ContainerView, variable: str, value: str, entry: dict[str, Any]
    ) -> list[Finding]:
        """The two-step check, and the range step only when the form step passed.

        One value produces one line: "not an integer" followed by "not below 65535" says nothing
        the first did not, and the second would be reporting on a parse that never happened.
        """
        form = cc.check_text(entry, value)
        if form is not None:
            return [error(f"env: {variable} on container {view.name!r}: {form}")]
        ranged = cc.check_parsed(entry, value)
        if ranged is not None:
            return [error(f"env: {variable} on container {view.name!r}: {ranged}")]
        return []

    def _unaccounted(self, view: ContainerView, variable: str, union: cc.Union) -> list[Finding]:
        message = (
            f"env: {variable} is set by container {view.name!r} and the contract accounts for it "
            "nowhere: it is not a key, not a declared external variable, and matches no ignore "
            "pattern"
        )
        if union.unknown == "reject":
            return [error(message)]
        if union.unknown == "warn":
            return [warning(message)]
        return []


class FileGate:
    """Gate 3 — the file spellings, over every volume rather than only the secrets directory.

    Keying this off `<PREFIX>_SECRETS_DIR` alone leaves the worse half of the defect invisible: a
    chart that mounts key-named credential files and never sets the variable has produced a pod
    where the files exist, the loader never looks at them, and every credential falls back to a
    default. Nothing renders wrong and nothing fails to start. So every mount is inspected, and a
    file whose name spells a key is a finding wherever it turns up — unless a `_FILE` variable
    names it, which is the other legitimate way for a file to be read.
    """

    def check(
        self,
        manifests: list[dict[str, Any]],
        spec: dict[str, Any],
        view: ContainerView,
        union: cc.Union,
        suppliers: Suppliers,
        relaxed: set[str],
    ) -> list[Finding]:
        if "files" in relaxed:
            return []

        findings: list[Finding] = []
        findings.extend(self._loader_paths(view, union))
        findings.extend(self._indirection(view, union))
        findings.extend(self._mounts(manifests, spec, view, union, suppliers))
        return findings

    def _loader_paths(self, view: ContainerView, union: cc.Union) -> list[Finding]:
        """A loader variable naming a path nothing mounts is a boot failure, not an empty layer."""
        findings = []
        for variable, value in sorted(view.values.items()):
            decision = cc.classify(union, variable)
            if decision.kind != cc.LOADER or not value or not view.visible(variable):
                continue
            if (decision.entry or {}).get("role") not in ("config", "secrets_dir"):
                continue
            if not inside_a_mount(view.container, value):
                findings.append(
                    error(
                        f"files: {variable} on container {view.name!r} points at {value!r}, which "
                        "is not inside any volume the container mounts; the loader fails its boot "
                        "on a configured path it cannot read"
                    )
                )
        return findings

    def _indirection(self, view: ContainerView, union: cc.Union) -> list[Finding]:
        findings = []
        for variable, value in sorted(view.values.items()):
            decision = cc.classify(union, variable)
            if decision.kind != cc.KEY_ENV_FILE:
                continue
            key = decision.entry or {}
            findings.extend(
                self._file_typed(key, f"{variable} on container {view.name!r} names a file")
            )
            if not view.visible(variable) or not value:
                continue
            if not inside_a_mount(view.container, value):
                findings.append(
                    error(
                        f"files: {variable} on container {view.name!r} points at {value!r}, which "
                        "is not inside any volume the container mounts"
                    )
                )
        return findings

    def _mounts(
        self,
        manifests: list[dict[str, Any]],
        spec: dict[str, Any],
        view: ContainerView,
        union: cc.Union,
        suppliers: Suppliers,
    ) -> list[Finding]:
        findings: list[Finding] = []
        spellings = [key["secrets_file"] for key in union.keys.values() if key.get("secrets_file")]
        configured = view.secrets_dir.rstrip("/") if view.secrets_dir else None

        for mount in view.container.get("volumeMounts") or []:
            volume = str(mount.get("name") or "")
            path = str(mount.get("mountPath") or "").rstrip("/")
            if not volume:
                continue

            is_secrets_dir = configured is not None and path == configured
            for file_name in projected_file_names(manifests, spec, volume):
                key = union.key_by("secrets_file", file_name)

                if is_secrets_dir:
                    if key is None:
                        findings.append(
                            error(
                                f"files: the secrets directory mounts {file_name!r}, which spells "
                                "no key in the contract" + cc.suggest(file_name, spellings)
                            )
                        )
                        continue
                    suppliers.add(key["path"], SECRETS_LAYER)
                    findings.extend(
                        self._file_typed(key, f"the secrets directory mounts {file_name!r}")
                    )
                    continue

                if key is None or f"{path}/{file_name}" in view.indirect:
                    continue
                findings.append(
                    error(
                        f"files: {path}/{file_name} spells the key {key['path']!r}, but nothing "
                        "reads it: "
                        + (
                            f"the secrets directory is {configured!r} and no `_FILE` variable "
                            "names it"
                            if configured
                            else "no secrets directory is configured on this container and no "
                            "`_FILE` variable names it"
                        )
                    )
                )

        if (
            configured
            and volume_at(view.container, configured) is None
            and inside_a_mount(view.container, configured)
        ):
            findings.append(
                warning(
                    f"files: the secrets directory {configured!r} on container {view.name!r} is "
                    "inside a volume rather than the mount point itself, so the file names it "
                    "will present cannot be read from the manifest and gate 3 checked none of them"
                )
            )

        return findings

    def _file_typed(self, key: dict[str, Any], what: str) -> list[Finding]:
        """A file can only supply a key whose `text_form` is `text`."""
        if cc.file_supplyable(key):
            return []
        return [
            error(
                f"files: {what} for the key {key['path']!r}, whose text_form is "
                f"{key.get('text_form')!r} (Rust type {key.get('ty')!r}): a file's contents arrive "
                "as a string with no parse and the loader does not coerce one into a number, a "
                "boolean or a TOML literal, so this key cannot be supplied by a file at all"
            )
        ]


class LayerCollisionGate:
    """The collision the loader refuses at boot, once both gates have said who supplies what."""

    def check(self, view: ContainerView, suppliers: Suppliers, relaxed: set[str]) -> list[Finding]:
        if "env" in relaxed:
            return []
        findings = []
        for path, layers in sorted(suppliers.layers.items()):
            if len(layers) > 1:
                findings.append(
                    error(
                        f"env: the key {path!r} is supplied by {' and '.join(sorted(layers))} on "
                        f"container {view.name!r}; the loader refuses a key supplied by two of "
                        "the environment, the secrets directory and `_FILE` indirection, and "
                        "fails its boot naming the key"
                    )
                )
        return findings


def check_container(
    manifests: list[dict[str, Any]],
    spec: dict[str, Any],
    container: dict[str, Any],
    union: cc.Union,
    relaxed: set[str],
) -> list[Finding]:
    """Run gates 2 and 3 over one container, against the contract of the image it runs."""
    view = ContainerView.read(container, union)
    suppliers = Suppliers()

    findings = EnvironmentGate().check(view, union, suppliers, relaxed)
    findings += FileGate().check(manifests, spec, view, union, suppliers, relaxed)
    findings += LayerCollisionGate().check(view, suppliers, relaxed)
    return findings
