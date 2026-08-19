#!/usr/bin/env python3
"""`charts/<chart>/config-contract.yaml`, and binding it to the contracts it names.

Per-chart and self-describing, so adding a chart never edits a central file — the same principle
`just chart-index` already follows. A chart with no such file is skipped; a chart that has one
with `documents: []` has opted out explicitly and must say why, and that is the only permitted
opt-out.

Two jobs live here, and they are the same job at two removes: reading what a chart declares, and
turning that into the contracts a document is actually validated against. The second is where the
staleness interlock sits, because refusing to validate is a decision about the declaration — the
chart pins a digest, the vendored file is for a digest, and if the two differ nothing downstream
has anything trustworthy to say.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

import config_contract as cc

DECLARATION = "config-contract.yaml"

# Gate names an `exempt` entry may relax, and what each one drops.
GATES = {
    "document": "gate 1 entirely: the rendered document is not validated",
    "closed": "only gate 1's `additionalProperties: false`: unknown keys are tolerated",
    "env": "gate 2: the container environment is not classified or checked",
    "files": "gate 3: secret file names and `_FILE` targets are not checked",
}

# Keys each level may carry. Anything else is a typo that would otherwise be ignored in silence,
# which for a file whose whole job is to be exhaustive is the worst possible failure mode.
DECLARATION_KEYS = {"documents", "reason", "unconfigured"}
DOCUMENT_KEYS = {"name", "source", "images", "consumers", "exempt"}
SOURCE_KEYS = {"kind", "selector", "key", "format"}
IMAGE_KEYS = {"values", "contract"}
CONSUMER_KEYS = {"workload", "containers"}
EXEMPT_KEYS = {"values", "gates", "reason"}

FORMATS = ("toml", "json", "yaml")


class DeclarationError(Exception):
    """A `config-contract.yaml` that cannot be read as one."""


# --------------------------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    kind: str
    selector: dict[str, str]
    key: str
    format: str


@dataclass(frozen=True)
class ImageRef:
    values: str
    contract: str


@dataclass(frozen=True)
class Consumer:
    kind: str
    selector: dict[str, str]
    containers: list[str]


@dataclass(frozen=True)
class Exemption:
    values: str
    gates: list[str]
    reason: str


@dataclass(frozen=True)
class Document:
    name: str
    source: Source
    images: list[ImageRef]
    consumers: list[Consumer]
    exempt: list[Exemption]

    def relaxed(self, values_file: str) -> set[str]:
        """The gates exempted for one `ci/` values file."""
        relaxed: set[str] = set()
        for exemption in self.exempt:
            if Path(exemption.values).name == Path(values_file).name:
                relaxed.update(exemption.gates)
        return relaxed


@dataclass(frozen=True)
class Declaration:
    chart: str
    path: Path
    documents: list[Document]
    reason: str | None
    unconfigured: list[str]


def load_declaration(chart_dir: Path) -> Declaration | None:
    """Read one chart's declaration; `None` when it has none."""
    path = chart_dir / DECLARATION
    if not path.is_file():
        return None

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise DeclarationError(f"{path}: expected a mapping at the top level")
    reject_unknown(path, "", document, DECLARATION_KEYS)

    raw_documents = document.get("documents")
    if not isinstance(raw_documents, list):
        raise DeclarationError(f"{path}: `documents` is missing or not a list")

    reason = document.get("reason")
    if not raw_documents and not reason:
        raise DeclarationError(
            f"{path}: `documents` is empty, which is an explicit opt-out and needs a `reason`"
        )

    return Declaration(
        chart=chart_dir.name,
        path=path,
        documents=[_load_document(path, entry) for entry in raw_documents],
        reason=reason,
        unconfigured=list(document.get("unconfigured") or []),
    )


def _load_document(path: Path, entry: Any) -> Document:
    if not isinstance(entry, dict):
        raise DeclarationError(f"{path}: every entry of `documents` must be a mapping")
    reject_unknown(path, "documents[]", entry, DOCUMENT_KEYS)

    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise DeclarationError(f"{path}: a document has no `name`")

    return Document(
        name=name,
        source=_load_source(path, name, entry.get("source")),
        images=_load_images(path, name, entry.get("images")),
        consumers=_load_consumers(path, name, entry.get("consumers") or []),
        exempt=_load_exemptions(path, name, entry.get("exempt") or []),
    )


def _load_source(path: Path, name: str, source: Any) -> Source:
    if not isinstance(source, dict):
        raise DeclarationError(f"{path}: {name}: `source` is missing")
    reject_unknown(path, f"{name}.source", source, SOURCE_KEYS)
    for required in ("kind", "key"):
        if not isinstance(source.get(required), str):
            raise DeclarationError(f"{path}: {name}: `source.{required}` is missing")

    form = source.get("format", "toml")
    if form not in FORMATS:
        raise DeclarationError(
            f"{path}: {name}: `source.format` {form!r} is not one of {', '.join(FORMATS)}"
        )

    return Source(
        kind=source["kind"],
        selector=dict(source.get("selector") or {}),
        key=source["key"],
        format=form,
    )


def _load_images(path: Path, name: str, images: Any) -> list[ImageRef]:
    if not isinstance(images, list) or not images:
        raise DeclarationError(f"{path}: {name}: `images` is missing or empty")

    loaded = []
    for image in images:
        if not isinstance(image, dict):
            raise DeclarationError(f"{path}: {name}: every entry of `images` must be a mapping")
        reject_unknown(path, f"{name}.images[]", image, IMAGE_KEYS)
        for required in ("values", "contract"):
            if not isinstance(image.get(required), str):
                raise DeclarationError(f"{path}: {name}: `images[].{required}` is missing")
        loaded.append(ImageRef(values=image["values"], contract=image["contract"]))
    return loaded


def _load_consumers(path: Path, name: str, consumers: Iterable[Any]) -> list[Consumer]:
    loaded = []
    for consumer in consumers:
        if not isinstance(consumer, dict):
            raise DeclarationError(f"{path}: {name}: every entry of `consumers` must be a mapping")
        reject_unknown(path, f"{name}.consumers[]", consumer, CONSUMER_KEYS)

        workload = consumer.get("workload")
        if not isinstance(workload, dict) or not isinstance(workload.get("kind"), str):
            raise DeclarationError(f"{path}: {name}: `consumers[].workload.kind` is missing")

        containers = consumer.get("containers")
        if not isinstance(containers, list) or not containers:
            raise DeclarationError(f"{path}: {name}: `consumers[].containers` is missing or empty")

        loaded.append(
            Consumer(
                kind=workload["kind"],
                selector=dict(workload.get("selector") or {}),
                containers=[str(container) for container in containers],
            )
        )
    return loaded


def _load_exemptions(path: Path, name: str, exemptions: Iterable[Any]) -> list[Exemption]:
    loaded = []
    for exemption in exemptions:
        if not isinstance(exemption, dict):
            raise DeclarationError(f"{path}: {name}: every entry of `exempt` must be a mapping")
        reject_unknown(path, f"{name}.exempt[]", exemption, EXEMPT_KEYS)

        gates = exemption.get("gates")
        if not isinstance(gates, list) or not gates:
            raise DeclarationError(f"{path}: {name}: an `exempt` entry names no `gates`")
        unknown = set(gates) - set(GATES)
        if unknown:
            raise DeclarationError(
                f"{path}: {name}: `exempt` names gates that do not exist: "
                f"{', '.join(sorted(unknown))} (known: {', '.join(sorted(GATES))})"
            )
        if not exemption.get("reason"):
            raise DeclarationError(
                f"{path}: {name}: the exemption for {exemption.get('values')!r} has no `reason`; "
                "an unexplained hole in a gate is indistinguishable from an oversight"
            )

        loaded.append(
            Exemption(
                values=str(exemption.get("values") or ""),
                gates=[str(gate) for gate in gates],
                reason=str(exemption["reason"]),
            )
        )
    return loaded


def reject_unknown(path: Path, where: str, mapping: dict, allowed: Iterable[str]) -> None:
    unknown = set(mapping) - set(allowed)
    if unknown:
        location = f"{where}: " if where else ""
        raise DeclarationError(
            f"{path}: {location}unknown key(s) {', '.join(sorted(unknown))}; "
            f"known keys are {', '.join(sorted(allowed))}"
        )


# --------------------------------------------------------------------------------------------
# Resolving the image a chart pins
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PinnedImage:
    """What `common.image` would render from one values path, split into its parts."""

    reference: str
    normalized: str
    digest: str | None


def dig(values: Any, path: str) -> Any:
    """Follow a dotted values path, returning `None` at the first missing step."""
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_image(values: dict[str, Any], path: str, app_version: str | None) -> PinnedImage:
    """Build the image reference `common.image` renders for one values path.

    Mirrors `charts/common/templates/_images.tpl`: an empty `registry` means Docker Hub, `tag`
    falls back to the chart's `appVersion`, and the tag may pin a digest inline
    (`v1.2.3@sha256:...`) — which is what actually pins the pull and what a contract is tied to.
    """
    image = dig(values, path)
    if not isinstance(image, dict) or not image.get("repository"):
        raise DeclarationError(
            f"values path {path!r} does not resolve to an image with a repository"
        )

    registry = str(image.get("registry") or "")
    repository = str(image["repository"])
    tag = str(image.get("tag") or app_version or "")

    reference = f"{registry}/{repository}" if registry else repository
    if tag:
        reference = f"{reference}:{tag}"

    return PinnedImage(
        reference=reference,
        normalized=f"{registry or 'docker.io'}/{repository}",
        digest=tag.split("@", 1)[1] if "@" in tag else None,
    )


# --------------------------------------------------------------------------------------------
# Binding a document to the contracts that describe it
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Binding:
    """The contracts one document is validated against, in the two scopes the gates need.

    `union` is every image that reads the document, for gate 1: a file every binary reads, so a
    key belonging to one would otherwise be "unknown" to the schema of another and a correct
    deployment would be rejected.

    `by_digest` is one image each, for gates 2 and 3. A container runs exactly one image, and a
    variable set on it that only a sibling image reads is precisely the defect gate 2 exists to
    catch — checking it against the union reintroduces what splitting the scopes removed. Held as
    single-contract `Union`s so both scopes are the same type to a gate.
    """

    union: cc.Union
    by_digest: dict[str, cc.Union]


def bind(
    chart_dir: Path,
    document: Document,
    values: dict[str, Any],
    app_version: str | None,
) -> tuple[Binding | None, list[str]]:
    """Load, interlock and merge every contract this document is validated against.

    Returns `(None, problems)` rather than a partial answer, and that is the staleness interlock.
    The Documentation job refreshes the vendored contracts and deliberately does not gate this
    one, so a Renovate digest bump produces one run holding a new digest and the *old* contract
    before the refresh commit re-triggers the workflow. Validating anyway would report a pass it
    cannot justify, on exactly the pull request the whole design exists to protect. Deterministic,
    offline, self-healing, and a hard failure rather than a skip.
    """
    problems: list[str] = []
    contracts: list[tuple[str, dict[str, Any]]] = []
    by_digest: dict[str, cc.Union] = {}

    for reference in document.images:
        path = chart_dir / reference.contract
        label = f"{chart_dir.name}/{reference.contract}"

        try:
            pinned = resolve_image(values, reference.values, app_version)
            vendored = cc.load_vendored(path)
        except (cc.ContractError, DeclarationError) as failure:
            return None, [str(failure)]

        if pinned.digest is None:
            problems.append(
                f"the values path {reference.values!r} resolves to {pinned.reference}, which is "
                "not pinned by digest; a contract cannot be tied to a mutable tag"
            )
            continue

        if vendored.digest != pinned.digest:
            problems.append(
                f"{label} is for {vendored.digest}, but the chart pins {pinned.digest}. The "
                "Documentation job refreshes it; re-run after its commit, or run "
                "`just contracts` locally."
            )
            continue

        if vendored.image != pinned.normalized:
            problems.append(
                f"{label} is for the image {vendored.image}, but the values path "
                f"{reference.values!r} resolves to {pinned.normalized}"
            )
            continue

        contracts.append((label, vendored.contract))
        try:
            by_digest[pinned.digest] = cc.union_contracts([(label, vendored.contract)])
        except cc.ContractError as failure:
            return None, [str(failure)]

    if problems:
        return None, problems

    try:
        union = cc.union_contracts(contracts)
    except cc.ContractError as failure:
        return None, [str(failure)]

    offenders = cc.local_refs_only(union.json_schema)
    if offenders:
        return None, [
            "the merged schema carries a remote reference, which would make this offline gate a "
            f"networked one: {offender}"
            for offender in offenders
        ]

    return Binding(union=union, by_digest=by_digest), []
