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
from typing import Any, Iterable, Iterator

import yaml

from config_paths import dig

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
DECLARATION_KEYS = {"documents", "reason", "unconfigured", "bindings", "unbound"}
DOCUMENT_KEYS = {"name", "source", "images", "consumers", "exempt"}
SOURCE_KEYS = {"kind", "selector", "key", "format"}
IMAGE_KEYS = {"values", "contract"}
CONSUMER_KEYS = {"workload", "containers"}
EXEMPT_KEYS = {"values", "gates", "reason"}
UNBOUND_KEYS = {"keys", "reason", "documents"}

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
class Unbound:
    """Contract keys this chart deliberately surfaces no value for, and why.

    A different axis from `Exemption` above, and the two are not interchangeable. An exemption is
    per `ci/` values file and per gate: it says one rendered *fixture* is not held to one of the
    four checks. This says a set of *keys* has no chart value binding it, which is a property of
    the chart and of every fixture at once, and it relaxes no gate — `check-config` still validates
    whatever those keys' rendered values turn out to be.

    **Several keys to one reason, and the keys still listed one by one.** The first draft was one
    entry per key, which reads well for the two keys `s3-bucket-perma-link` writes off and does not
    survive `tankovault`: 127 of its key paths are surfaced by nothing, and per-document entries
    made that 345 of them, all repeating a handful of sentences. A shared reason is what those keys
    actually have in common. What is *not* offered is a pattern or a prefix: every key is written
    out, so an image release that adds one turns the gate red until somebody puts it in a list on
    purpose, which is the whole reason this file exists.

    `documents` scopes the write-off the way a marker's scope does — absent means every document
    whose contract declares the key. Naming documents says the key is surfaced in some of them and
    not others.

    The `reason` is mandatory for the reason an exemption's is: a key no value reaches is either a
    considered decision or the exact oversight `check-config-bindings` exists to catch, and nothing
    but a sentence tells the two apart.
    """

    keys: tuple[str, ...]
    reason: str
    documents: tuple[str, ...] | None


@dataclass(frozen=True)
class Document:
    name: str
    source: Source
    images: list[ImageRef]
    consumers: list[Consumer]
    exempt: list[Exemption]

    def relaxed(self, values_file: str) -> set[str]:
        """The gates exempted for one `ci/` values file.

        `values: "*"` covers every one of them. That is for a gap in the *contract* rather than in
        one fixture — a key the chart renders and the image's document does not describe — which
        is a property of the pair and not of any particular values file. Naming all fourteen
        would say the same thing fourteen times and grow silently stale as fixtures are added.
        """
        relaxed: set[str] = set()
        for exemption in self.exempt:
            if exemption.values == "*" or Path(exemption.values).name == Path(values_file).name:
                relaxed.update(exemption.gates)
        return relaxed


@dataclass(frozen=True)
class Declaration:
    """One chart's `config-contract.yaml`.

    `bindings` is the chart's enrolment in `check-config-bindings`, and it is a declared fact
    rather than an inferred one on purpose. Enrolment used to be "the chart carries at least one
    marker", which reads well until a chart *stops* carrying them: a botched edit, a rewritten
    `values.yaml`, a marker spelt in a way the parser does not recognise, and the chart drops out
    of the report without a word — measured, not imagined. With the switch in the declaration the
    two disagreements are both errors: markers without `bindings: true`, and `bindings: true`
    without markers.
    """

    chart: str
    path: Path
    documents: list[Document]
    reason: str | None
    unconfigured: list[str]
    bindings: bool
    unbound: list[Unbound]


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

    bindings = document.get("bindings", False)
    if not isinstance(bindings, bool):
        raise DeclarationError(
            f"{path}: `bindings` is the chart's enrolment in check-config-bindings and must be "
            f"true or false, not {bindings!r}"
        )

    # Named apart from `document`, the parsed mapping this function is reading, so the two are
    # never mistaken for each other while the file is being edited.
    declared = [_load_document(path, entry) for entry in raw_documents]
    names = {entry.name for entry in declared}

    return Declaration(
        chart=chart_dir.name,
        path=path,
        documents=declared,
        reason=reason,
        unconfigured=list(document.get("unconfigured") or []),
        bindings=bindings,
        unbound=_load_unbound(path, document.get("unbound") or [], names),
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


def _load_unbound(path: Path, entries: Iterable[Any], documents: set[str]) -> list[Unbound]:
    """The contract keys this chart binds no value to, each group with a written reason.

    Read here rather than in `config_bindings.py` for the reason everything else about this file
    is: `config-contract.yaml` has one reader, and a second parser of the same document would be
    free to disagree with this one about what an unknown key means. A marker states a fact about a
    value and belongs beside the value; the decision not to surface a key is a decision about the
    key, and belongs beside the contract that declares it.

    Chart-level rather than per document, because that is what the fact is: a key nothing surfaces
    is unsurfaced everywhere it is declared. `documents` narrows it where that is untrue.
    """
    loaded = []
    seen: dict[tuple[str, str | None], int] = {}

    for position, entry in enumerate(entries):
        where = f"unbound[{position}]"
        if not isinstance(entry, dict):
            raise DeclarationError(f"{path}: every entry of `unbound` must be a mapping")
        reject_unknown(path, where, entry, UNBOUND_KEYS)

        keys = entry.get("keys")
        if not isinstance(keys, list) or not keys:
            raise DeclarationError(
                f"{path}: {where}: `keys` is missing or empty; an entry writes off at least one "
                "key, listed by name — there is no pattern form, so a key an image release adds "
                "cannot be written off by something somebody typed before it existed"
            )
        for key in keys:
            if not isinstance(key, str) or not key:
                raise DeclarationError(f"{path}: {where}: every entry of `keys` must be a name")

        if not entry.get("reason"):
            raise DeclarationError(
                f"{path}: {where}: no `reason`; a key nothing surfaces is either a decision or "
                "the oversight this gate exists to catch, and silence cannot say which"
            )

        scope = entry.get("documents")
        if scope is not None:
            if not isinstance(scope, list) or not scope:
                raise DeclarationError(
                    f"{path}: {where}: `documents` scopes the write-off and must be a non-empty "
                    "list; leave it out to write the keys off wherever they are declared"
                )
            unknown = sorted({str(name) for name in scope} - documents)
            if unknown:
                raise DeclarationError(
                    f"{path}: {where}: `documents` names {', '.join(repr(n) for n in unknown)}, "
                    f"which this chart does not declare (declared: {', '.join(sorted(documents))})"
                )
            scope = tuple(str(name) for name in scope)

        for key in keys:
            for name in scope or (None,):
                if (key, name) in seen:
                    raise DeclarationError(
                        f"{path}: {where}: {key!r} is already written off by "
                        f"unbound[{seen[(key, name)]}]; two reasons for one key means one of them "
                        "is not the reason"
                    )
                seen[(key, name)] = position

        loaded.append(
            Unbound(
                keys=tuple(str(key) for key in keys),
                reason=str(entry["reason"]),
                documents=scope,
            )
        )
    return loaded


# --------------------------------------------------------------------------------------------
# Walking the tree
# --------------------------------------------------------------------------------------------


def chart_dirs(charts: Path) -> Iterator[Path]:
    """Every chart directory under `charts`, in directory order.

    "Is this a chart" is `Chart.yaml` and nothing else — not a name list, not an exclusion set —
    so a chart added to the tree is picked up by every gate at once with nothing to update. That
    was already true five times over; this is the one copy of it.

    Sorted, so every report, every failure list and every generated file is ordered by the tree
    rather than by whatever order the filesystem happened to return.
    """
    for chart_dir in sorted(charts.iterdir()):
        if (chart_dir / "Chart.yaml").is_file():
            yield chart_dir


def declared(charts: Path, *, documents_only: bool = False) -> Iterator[tuple[Path, Declaration]]:
    """Every chart carrying a declaration, paired with it.

    `documents_only` is the distinction the five hand-written copies of this loop disagreed on,
    and it is a real one rather than a convenience. A chart with `documents: []` has *opted out*
    explicitly and carries a reason: `just explain` must see it, because "this chart opted out,
    and here is why" is an answer somebody ran the command to get. `check-config` and the
    credential inventory must not, because there is no document for them to read.

    Passed as a keyword because at a call site `declared(charts, True)` says nothing about which
    of the two it means.
    """
    for chart_dir in chart_dirs(charts):
        declaration = load_declaration(chart_dir)
        if declaration is None:
            continue
        if documents_only and not declaration.documents:
            continue
        yield chart_dir, declaration


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
class Loaded:
    """One vendored contract, the reference that named it, and the label it is reported under.

    The label is `<chart>/<path>` — `tankovault/contracts/api.json` — and it is built here rather
    than at each call site because it is the string every message, every union source list and
    every credential row identifies a contract by. Four callers were spelling it themselves.
    """

    reference: ImageRef
    label: str
    vendored: cc.Vendored


def vendored_for(chart_dir: Path, document: Document) -> list[Loaded]:
    """Load every contract one document binds. **Applies no staleness interlock.**

    The bold part is the whole reason this function exists rather than each caller writing its own
    three lines. Whether the vendored file is for the digest the chart *currently* pins is a
    separate question from what the file says, and the four callers legitimately answer it four
    different ways:

    | Caller                          | Interlock | Why                                          |
    |---------------------------------|-----------|----------------------------------------------|
    | `bind`, for the gates           | yes       | a pass it cannot justify is worse than a fail |
    | `check-config-bindings`         | no        | compares values.yaml against the committed    |
    |                                 |           | copy; whether that copy is current is         |
    |                                 |           | `check-contracts`' question                   |
    | `config-secrets`, the inventory | no        | withholding it during a bump removes the      |
    |                                 |           | document at the moment it is being read       |
    | `generate-contract-tests`       | no        | would block regeneration in the window        |
    |                                 |           | between a bump and the refresh                |

    Each of those three is a considered decision with a paragraph behind it, and each was
    previously expressed as *the absence* of code — a four-line loop that looked like a helper and
    was in fact a deliberate bypass. A fifth consumer copying one of them would have inherited the
    bypass without inheriting the reason. Now the absence has a name, and `bind` is the one that
    visibly adds something.

    Raises `ContractError` on a file that cannot be read or is not shaped like a contract; that is
    not an interlock but a parse, and no caller wants to proceed past it.
    """
    return [
        Loaded(
            reference=reference,
            label=f"{chart_dir.name}/{reference.contract}",
            vendored=cc.load_vendored(chart_dir / reference.contract),
        )
        for reference in document.images
    ]


def union_for(chart_dir: Path, document: Document) -> cc.Union:
    """The contracts of every image that reads one document, merged. No interlock — see above."""
    return cc.union_contracts(
        [(item.label, item.vendored.contract) for item in vendored_for(chart_dir, document)]
    )


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

    try:
        loaded = vendored_for(chart_dir, document)
    except cc.ContractError as failure:
        return None, [str(failure)]

    for item in loaded:
        reference, label, vendored = item.reference, item.label, item.vendored

        try:
            pinned = resolve_image(values, reference.values, app_version)
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
