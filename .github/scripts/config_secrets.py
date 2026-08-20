#!/usr/bin/env python3
"""The credential surface: what the images declare secret, and what the charts deliver.

Forty-seven key declarations across this repository carry `secret: true`, and until this module
existed no document anywhere said what they are, how each one reaches the process that reads it,
or which pods end up holding it. That gap is not cosmetic. The gates in `config_gate_container.py`
are structurally unable to close it: gate 3 asks whether every *delivered* file name is known to
the contract, so it rejects a name nothing declares and is blind by construction to a declared key
nothing supplies. A credential that no channel delivers renders cleanly, passes every gate, and
fails at the first request that needs it.

Two answers live here, and they are deliberately separate:

**The inventory** is what the contracts say, and needs no render. It reads the vendored documents
and nothing else, so it stays cheap enough to run while writing a values file. It deliberately
skips the staleness interlock `config_declaration.bind` applies — a Renovate digest bump produces
one run where the pinned digest and the vendored contract disagree, and refusing to print the
inventory then would withhold the document at exactly the moment a reviewer is reading a
credential change. Whether the vendored copy is current is `just check-contracts`' question.

**The reconciliation** is what a rendered chart actually delivers, against that inventory. It does
apply the interlock, because it reasons about which image a container runs and an answer derived
from a contract that belongs to a different digest is worse than no answer.

Three conclusions, and the scope of each is load-bearing:

  undeliverable   a contract key no rendered configuration can supply through any channel. Judged
                  across *every* `ci/` fixture the chart ships, because a credential delivered
                  only under one fixture is delivered — the chart has a way to supply it.

  unclaimed       a delivered Secret file name that no contract of the chart names, by neither an
                  exact spelling nor a dynamic-map leaf. Scoped against every contract the chart
                  vendors, which is what keeps it disjoint from over-projection below.

  over-projected  a Secret file whose name a *sibling* image's contract claims and the container's
                  own image's contract does not. A pod holding a credential its binary never reads
                  is a least-privilege defect, and it is scoped per container against that one
                  image's contract — never against the union, for the reason
                  `config_gate_container.py`'s docstring gives at length.

**Not duplicating gate 3.** Gate 3 already errors on a name unknown to the contract, but only for
a file inside a *resolved* secrets directory on a container the declaration lists as a consumer of
that document. Both halves of that condition are recorded per file here, and a file gate 3 would
have judged is reported by neither `unclaimed` nor `over-projected` — it is already one line in
`just check-config`, and a second line saying the same thing in a different report trains a reader
to skim both. What remains is precisely gate 3's blind spot, and it is not hypothetical: several
of this repository's Deployments run an init container that no `config-contract.yaml` names as a
consumer, so nothing checks what it mounts.

**Nothing here builds a union, and the word is worth avoiding.** `tankovault` declares nine
documents, one per service, each with exactly one image — so `union_contracts` is the identity for
every one of them and there is no shared namespace to merge. Every judgement below is made against
one image's own contract, which is the only scope that can distinguish a key `notifier` reads from
one `api` does. The single chart-wide structure is a digest-to-contract lookup, and it exists to
*find* the right contract for a container rather than to widen one: an init container inside the
`api` Deployment runs the `bootstrap` image, whose contract the `api` document never mentions.

**Every container, not only the declared consumers.** The reconciliation walks every container of
every rendered workload and identifies its image by digest, because the question "who can see this
credential" is about the pod rather than about what the declaration chose to describe. A container
whose image the chart does not pin a contract for is skipped: nothing here can say what it reads.

**Aliases are deliberately not consulted.** A key may carry `env_aliases`, `env_file_aliases` and
`secrets_file_aliases`; `config_contract.classify` and `Union.key_by` ignore them, and this module
agrees with the normative reader rather than being independently cleverer than the gate whose
findings it sits beside. No contract in this repository declares one today, so the two readings
cannot yet diverge; if that changes, it changes in `config_contract.py` first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

import config_contract as cc
from config_declaration import (
    Binding,
    Declaration,
    Document,
    bind,
    declared,
    vendored_for,
)
from config_gate_container import ContainerView
from config_gate_document import parse_document
from config_paths import read_yaml
from config_manifests import containers_of, digest_of, load_manifests, pod_spec, select

# The four ways a value can reach the loader, named as the report prints them. The rendered
# document is among them and is not a mistake: a key written into the plaintext ConfigMap *is*
# supplied, and leaving that channel out would report a key as undeliverable while a running pod
# reads it. Whether a credential belongs in a ConfigMap is a different question from whether it
# arrives.
SECRETS_DIRECTORY = "the secrets directory"
INDIRECTION = "`_FILE` indirection"
ENVIRONMENT = "the environment"
DOCUMENT = "the rendered document"

# Files under a chart that describe what it *delivers*. The vendored contracts are excluded on
# purpose: they are the other side of the comparison, and every credential is named in one of them
# by definition.
CHART_TEXT = ("*.yaml", "*.yml", "*.tpl", "*.json", "*.md", "*.txt")
EXCLUDED_DIRS = ("contracts", "charts")


# --------------------------------------------------------------------------------------------
# The inventory — what the vendored contracts declare
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Declared:
    """One `secret: true` key, as one image's contract spells it."""

    chart: str
    document: str
    # The vendored file as `config_declaration.bind` labels it — `<chart>/contracts/<name>.json`.
    # The same spelling `cc.Union.sources` carries, so a declaration and the contract a rendered
    # container was matched against are comparable without a second naming convention.
    contract: str
    image: str
    path: str
    secrets_file: str
    env: str
    env_file: str
    text_form: str
    required: bool
    # The contract's own first line about the key. Carried into the report because it is what
    # turns a triage question into an answered one: `internal.token` is undeliverable here and its
    # own documentation says it is retired, which no amount of chart reading would have said.
    summary: str


@dataclass(frozen=True)
class Credential:
    """One credential of one chart: every declaration of it, merged.

    Merged by spelling rather than by path alone. Two documents of one chart spelling one path
    differently would be two different files on disk and two different variables, so they stay two
    rows — the inventory's job is to name the artefacts an operator has to create, and collapsing
    them would name one that does not exist.
    """

    chart: str
    path: str
    secrets_file: str
    env: str
    env_file: str
    required: bool
    summary: str
    documents: tuple[str, ...]
    images: tuple[str, ...]
    contracts: tuple[str, ...]


def contracted_charts(charts: Path) -> list[tuple[Path, Declaration]]:
    """Every chart that declares at least one document, in directory order."""
    return list(declared(charts, documents_only=True))


def declared_secrets(chart_dir: Path, declaration: Declaration) -> list[Declared]:
    """Every `secret: true` key of every contract one chart vendors."""
    found: list[Declared] = []
    for document in declaration.documents:
        for item in vendored_for(chart_dir, document):
            vendored = item.vendored
            app = (vendored.contract.get("app") or {}).get("name") or vendored.image
            for key in vendored.contract["schema"]["keys"]:
                if not key.get("secret"):
                    continue
                found.append(
                    Declared(
                        chart=declaration.chart,
                        document=document.name,
                        contract=item.label,
                        image=str(app),
                        path=str(key["path"]),
                        secrets_file=str(key.get("secrets_file") or ""),
                        env=str(key.get("env") or ""),
                        env_file=str(key.get("env_file") or ""),
                        text_form=str(key.get("text_form") or ""),
                        required=bool(key.get("required")),
                        summary=_first_line(key.get("docs")),
                    )
                )
    return found


def inventory(charts: Path) -> list[Declared]:
    """Every secret declaration in the repository, chart by chart."""
    found: list[Declared] = []
    for chart_dir, declaration in contracted_charts(charts):
        found.extend(declared_secrets(chart_dir, declaration))
    return found


def credentials(declared: Iterable[Declared]) -> list[Credential]:
    """Collapse declarations into one row per credential, keeping every reader's name."""
    merged: dict[tuple[str, str, str, str, str], list[Declared]] = {}
    for entry in declared:
        key = (entry.chart, entry.path, entry.secrets_file, entry.env, entry.env_file)
        merged.setdefault(key, []).append(entry)

    rows = []
    for (chart, path, secrets_file, env, env_file), entries in merged.items():
        rows.append(
            Credential(
                chart=chart,
                path=path,
                secrets_file=secrets_file,
                env=env,
                env_file=env_file,
                # Unioned exactly as `config_contract` unions it: a key any reader requires is a
                # key the deployment must carry.
                required=any(entry.required for entry in entries),
                # `config_contract._merge_entry` refuses two contracts that document one key
                # differently, so every entry here carries the same line and the first will do.
                summary=entries[0].summary,
                documents=tuple(sorted({entry.document for entry in entries})),
                images=tuple(sorted({entry.image for entry in entries})),
                contracts=tuple(sorted({entry.contract for entry in entries})),
            )
        )
    rows.sort(key=lambda row: (row.chart, row.path, row.secrets_file))
    return rows


# --------------------------------------------------------------------------------------------
# What a rendered volume actually delivers
# --------------------------------------------------------------------------------------------


def secret_file_names(
    manifests: list[dict[str, Any]], spec: dict[str, Any], volume_name: str
) -> list[str]:
    """The file names one volume presents *from Secret sources only*.

    `config_manifests.projected_file_names` answers the neighbouring question and deliberately
    merges Secret and ConfigMap sources, because gate 3 asks whether a name spells a key and that
    does not depend on where the bytes came from. Here it does: the whole subject is a credential's
    blast radius, and `config.toml` arriving from a ConfigMap in the same projected volume is not
    one. Same walk, one source kind.
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

    names: list[str] = []
    for source in sources:
        body = source.get("secret")
        if not isinstance(body, dict):
            continue
        if body.get("items"):
            names.extend(str(item.get("path") or item.get("key")) for item in body["items"])
            continue
        # No `items` means every key of the object, which is the `existingSecret` case — the one a
        # gate would otherwise have nothing to say about. Readable only when the chart renders the
        # Secret itself; when it names one the operator supplies, there is nothing to read and the
        # volume contributes no names.
        for manifest in manifests:
            metadata = manifest.get("metadata") or {}
            if manifest.get("kind") == "Secret" and metadata.get("name") == body.get("name"):
                names.extend(manifest.get("data") or {})
                names.extend(manifest.get("stringData") or {})
    return sorted(set(names))


def document_paths(instance: Any, prefix: str = "") -> set[str]:
    """Every dotted path the parsed configuration document sets, leaves and tables alike.

    Tables are included so a `structured` key supplied as a whole table counts as supplied, which
    is what the loader sees.
    """
    paths: set[str] = set()
    if not isinstance(instance, dict):
        return paths
    for name, value in instance.items():
        full = f"{prefix}.{name}" if prefix else str(name)
        paths.add(full)
        paths |= document_paths(value, full)
    return paths


# --------------------------------------------------------------------------------------------
# The reconciliation
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mount:
    """One Secret-sourced file, as one rendered container receives it."""

    chart: str
    values_file: str
    workload: str
    container: str
    image: str
    mount_path: str
    file_name: str
    # Whether gate 3 already judged this file: it inspects the contents of a *resolved* secrets
    # directory on a container the declaration lists as a consumer, and nothing else.
    judged_by_gate_three: bool


@dataclass(frozen=True)
class Undeliverable:
    """A credential the chart declares and no rendering of it supplies.

    `named_by_chart` separates the two very different reasons that happens, and it is the whole
    difference between a defect and a coverage gap. A chart that names a credential somewhere in
    its values, templates or documentation has a channel for it and simply no `ci/` fixture that
    exercises one; a chart that names it nowhere has no channel at all, and an operator reading
    the values file will never learn the credential exists.
    """

    credential: Credential
    values_files: tuple[str, ...]
    named_by_chart: bool
    named_in: tuple[str, ...]


@dataclass(frozen=True)
class Elevated:
    """A key the contract calls ordinary configuration and the chart delivers as a Secret."""

    chart: str
    path: str
    file_name: str
    text_form: str
    containers: tuple[str, ...]


@dataclass
class Ledger:
    """What one chart's renders were found to deliver, accumulated across every values file.

    The same shape `config_gate_container.Suppliers` takes one level down, and for the same
    reason: neither question is answerable from a single container, so the answer has to outlive
    the walk over one. Keyed by the vendored contract as well as the config path, because "who
    supplies this" is a question about one image — two images declaring one path are two
    credentials to deliver, and collapsing them would let either one cover for the other.
    """

    supplied: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    elevated: dict[tuple[str, str, str], set[str]] = field(default_factory=dict)

    def supply(self, contract: str, path: str, channel: str) -> None:
        self.supplied.setdefault((contract, path), set()).add(channel)

    def supplies(self, contract: str, path: str) -> bool:
        return (contract, path) in self.supplied

    def elevate(self, key: dict[str, Any], file_name: str, container: str) -> None:
        identity = (str(key["path"]), file_name, str(key.get("text_form") or ""))
        self.elevated.setdefault(identity, set()).add(container)


@dataclass
class Surface:
    """Everything one pass over the rendered manifests found."""

    charts: list[str] = field(default_factory=list)
    values_files: list[str] = field(default_factory=list)
    undeliverable: list[Undeliverable] = field(default_factory=list)
    unclaimed: list[Mount] = field(default_factory=list)
    over_projected: list[Mount] = field(default_factory=list)
    elevated: list[Elevated] = field(default_factory=list)
    # Anything that stopped a chart being reconciled, rather than anything found wrong in one.
    notes: list[str] = field(default_factory=list)


class Reconciler:
    """One chart at a time: bind its contracts, walk its renders, compare the two."""

    def __init__(self, charts: Path, rendered: Path):
        self.charts = charts
        self.rendered = rendered
        self.surface = Surface()

    def run(self) -> Surface:
        for chart_dir, declaration in contracted_charts(self.charts):
            self.reconcile(chart_dir, declaration)
        self.surface.charts.sort()
        self.surface.values_files.sort()
        return self.surface

    # -- one chart ---------------------------------------------------------------------------

    def reconcile(self, chart_dir: Path, declaration: Declaration) -> None:
        values = read_yaml(chart_dir / "values.yaml")
        app_version = read_yaml(chart_dir / "Chart.yaml").get("appVersion")

        bindings: dict[str, Binding] = {}
        for document in declaration.documents:
            binding, problems = bind(chart_dir, document, values, app_version)
            if binding is None:
                for problem in problems:
                    self.surface.notes.append(
                        f"{declaration.chart}: {document.name}: not reconciled: {problem}"
                    )
                continue
            bindings[document.name] = binding

        if not bindings:
            return

        # Which contract describes the image a container runs, chart-wide rather than per
        # document. An init container inside the `api` Deployment may run the `bootstrap` image,
        # and the `api` document's binding has never heard of it — which is exactly the case the
        # over-projection check exists to see.
        by_digest: dict[str, cc.Union] = {}
        for binding in bindings.values():
            by_digest.update(binding.by_digest)

        contracts = [
            (label, union)
            for binding in bindings.values()
            for label, union in _labelled(binding).items()
        ]

        renders = sorted(self.rendered.glob(f"{declaration.chart}--*.yaml"))
        if not renders:
            self.surface.notes.append(
                f"{declaration.chart}: no rendered manifests; nothing to reconcile against"
            )
            return

        self.surface.charts.append(declaration.chart)
        ledger = Ledger()
        seen_values: list[str] = []

        for render in renders:
            values_file = render.name.split("--", 1)[1]
            seen_values.append(values_file)
            if values_file not in self.surface.values_files:
                self.surface.values_files.append(values_file)
            manifests = load_manifests(render)
            self.scan_render(
                declaration, bindings, by_digest, contracts, manifests, values_file, ledger
            )

        self.collect(chart_dir, declaration, ledger, tuple(seen_values))

    def scan_render(
        self,
        declaration: Declaration,
        bindings: dict[str, Binding],
        by_digest: dict[str, cc.Union],
        contracts: list[tuple[str, cc.Union]],
        manifests: list[dict[str, Any]],
        values_file: str,
        ledger: Ledger,
    ) -> None:
        judged = self.gate_three_reach(declaration, manifests)

        for document in declaration.documents:
            binding = bindings.get(document.name)
            if binding is not None:
                self.scan_document(document, binding, manifests, ledger)

        # Every workload, found by shape rather than by a list of kinds: anything carrying a pod
        # template has containers, and anything else yields none. A kind list here would need
        # editing the first time a chart grew a workload nobody thought of.
        for manifest in manifests:
            spec = pod_spec(manifest)
            found = containers_of(spec)
            if not found:
                continue
            workload = (
                f"{manifest.get('kind')} "
                f"{(manifest.get('metadata') or {}).get('name', '?')}"
            )
            for name, container in sorted(found.items()):
                mine = by_digest.get(digest_of(str(container.get("image") or "")) or "")
                if mine is None:
                    continue
                self.scan_container(
                    declaration, contracts, manifests, spec, mine, values_file, workload,
                    name, container, judged, ledger,
                )

    def scan_document(
        self,
        document: Document,
        binding: Binding,
        manifests: list[dict[str, Any]],
        ledger: Ledger,
    ) -> None:
        """Record the keys the rendered plaintext document sets, as one delivery channel."""
        matched = select(manifests, document.source.kind, document.source.selector)
        if len(matched) != 1:
            return
        text = (matched[0].get("data") or {}).get(document.source.key)
        if not isinstance(text, str):
            return
        try:
            instance = parse_document(text, document.source.format)
        except Exception:  # noqa: BLE001 — an unparseable document is gate 1's finding, not ours
            return

        present = document_paths(instance)
        for label, union in _labelled(binding).items():
            for path in union.keys:
                if path in present:
                    ledger.supply(label, path, DOCUMENT)

    def scan_container(
        self,
        declaration: Declaration,
        contracts: list[tuple[str, cc.Union]],
        manifests: list[dict[str, Any]],
        spec: dict[str, Any],
        mine: cc.Union,
        values_file: str,
        workload: str,
        name: str,
        container: dict[str, Any],
        judged: set[tuple[str, str]],
        ledger: Ledger,
    ) -> None:
        label = mine.sources[0]
        view = ContainerView.read(container, mine)
        secrets_dir = view.secrets_dir.rstrip("/") if view.secrets_dir else None

        for variable in sorted(view.values):
            decision = cc.classify(mine, variable)
            entry = decision.entry or {}
            if decision.kind == cc.KEY_ENV:
                ledger.supply(label, entry["path"], ENVIRONMENT)
            elif decision.kind == cc.KEY_ENV_FILE:
                ledger.supply(label, entry["path"], INDIRECTION)

        for mount in container.get("volumeMounts") or []:
            volume = str(mount.get("name") or "")
            path = str(mount.get("mountPath") or "").rstrip("/")
            if not volume:
                continue
            for file_name in secret_file_names(manifests, spec, volume):
                self.scan_file(
                    declaration, contracts, mine, label, view, secrets_dir, values_file,
                    workload, name, path, file_name, judged, ledger,
                )

    def scan_file(
        self,
        declaration: Declaration,
        contracts: list[tuple[str, cc.Union]],
        mine: cc.Union,
        label: str,
        view: ContainerView,
        secrets_dir: str | None,
        values_file: str,
        workload: str,
        container: str,
        mount_path: str,
        file_name: str,
        judged: set[tuple[str, str]],
        ledger: Ledger,
    ) -> None:
        in_secrets_dir = secrets_dir is not None and mount_path == secrets_dir
        by_indirection = f"{mount_path}/{file_name}" in view.indirect

        key = mine.key_by("secrets_file", file_name)
        dynamic = mine.container_of("secrets_file", file_name) if key is None else None

        if key is not None or dynamic is not None:
            owned = key if key is not None else dynamic
            if in_secrets_dir:
                ledger.supply(label, owned["path"], SECRETS_DIRECTORY)
            elif by_indirection:
                ledger.supply(label, owned["path"], INDIRECTION)
            # A file whose exact key the contract calls ordinary configuration, delivered as a
            # Secret anyway. Legitimate — chart policy may treat a value as more sensitive than
            # the image does, and gate 3 permits it for any `text` key — so it is stated and never
            # counted against the chart. Only an exact match is judged: a dynamic map's leaves
            # have no sensitivity of their own for the contract to state. And only a
            # file-supplyable key, because the same mount of a key of any other form is gate 3's
            # error rather than a policy choice, and it already says so at length.
            if key is not None and not key.get("secret") and cc.file_supplyable(key):
                ledger.elevate(key, file_name, f"{container} ({_short(label)})")
            return

        mount = Mount(
            chart=declaration.chart,
            values_file=values_file,
            workload=workload,
            container=container,
            image=_short(label),
            mount_path=mount_path,
            file_name=file_name,
            judged_by_gate_three=in_secrets_dir and (workload, container) in judged,
        )
        if mount.judged_by_gate_three:
            return

        if _named_anywhere(contracts, file_name):
            self.surface.over_projected.append(mount)
        else:
            self.surface.unclaimed.append(mount)

    # -- gate 3's reach ----------------------------------------------------------------------

    def gate_three_reach(
        self, declaration: Declaration, manifests: list[dict[str, Any]]
    ) -> set[tuple[str, str]]:
        """The `(workload, container)` pairs `just check-config` already inspects the mounts of.

        `check-config.py` walks the consumers a `config-contract.yaml` declares and the containers
        each one names, so a container absent from that list — an init container, a sidecar, a
        workload whose selector matches nothing — is a container gate 3 never opens. Recomputing
        the same selection here is what lets this report stay silent about files gate 3 has
        already reported and speak about the ones it cannot see.
        """
        reach: set[tuple[str, str]] = set()
        for document in declaration.documents:
            for consumer in document.consumers:
                for workload in select(manifests, consumer.kind, consumer.selector):
                    identity = (
                        f"{workload.get('kind')} "
                        f"{(workload.get('metadata') or {}).get('name', '?')}"
                    )
                    present = containers_of(pod_spec(workload))
                    for name in consumer.containers:
                        if name in present:
                            reach.add((identity, name))
        return reach

    # -- turning the scan into findings ------------------------------------------------------

    def collect(
        self,
        chart_dir: Path,
        declaration: Declaration,
        ledger: Ledger,
        values_files: tuple[str, ...],
    ) -> None:
        for credential in credentials(declared_secrets(chart_dir, declaration)):
            # Delivered by any one of the images that declare it is delivered: the credential is
            # one artefact an operator creates, and the report is about that artefact existing.
            # Which image reads it where is the over-projection question, one report down.
            if any(
                ledger.supplies(contract, credential.path) for contract in credential.contracts
            ):
                continue
            named_in = names_credential(chart_dir, credential)
            self.surface.undeliverable.append(
                Undeliverable(
                    credential=credential,
                    values_files=values_files,
                    named_by_chart=bool(named_in),
                    named_in=named_in,
                )
            )

        for (path, file_name, text_form), containers in sorted(ledger.elevated.items()):
            self.surface.elevated.append(
                Elevated(
                    chart=declaration.chart,
                    path=path,
                    file_name=file_name,
                    text_form=text_form,
                    containers=tuple(sorted(containers)),
                )
            )


def _labelled(binding: Binding) -> dict[str, cc.Union]:
    """One image's contract by the label that names its vendored file."""
    return {union.sources[0]: union for union in binding.by_digest.values()}


def _named_anywhere(contracts: list[tuple[str, cc.Union]], file_name: str) -> bool:
    """Whether any contract this chart vendors claims the file name, exactly or as a map leaf.

    Deliberately not `cc.union_contracts`: unioning nine contracts is a merge that can fail on a
    disagreement between two of them, and a report that refuses to run because two images document
    one key differently has traded the answer for a check `just check-config` already makes.
    """
    for _, union in contracts:
        if union.key_by("secrets_file", file_name) is not None:
            return True
        if union.container_of("secrets_file", file_name) is not None:
            return True
    return False


def names_credential(chart_dir: Path, credential: Credential) -> tuple[str, ...]:
    """The chart's own files that mention any spelling of one credential.

    Answers the question no fixed set of renders can: could *some* values file supply this? A
    chart that writes `telemetry.sentry_dsn` into its document when an operator sets a value names
    the key in a template and in `values.yaml`, and the only thing missing is a `ci/` fixture that
    sets it. A chart that names it nowhere has no channel at all.

    Matched with boundaries rather than as a substring, because the spellings nest: this
    repository's `internal__tokens__api` contains `internal__token`, and a substring search would
    report the retired tier-wide key as deliverable on the strength of an unrelated one. The
    boundary is "not another character the spelling could continue with", which is what makes
    those two distinguishable at all.

    A heuristic, and deliberately one that only chooses between two reports rather than producing
    one: a chart that assembled a file name by concatenation would name no spelling and be read as
    having no channel. Every finding is still grounded in what the render did — this decides how
    loudly to say it, never whether there is anything to say.
    """
    spellings = [
        spelling
        for spelling in (
            credential.path,
            credential.secrets_file,
            credential.env,
            credential.env_file,
        )
        if spelling
    ]
    pattern = re.compile(
        "|".join(rf"(?<![\w.]){re.escape(spelling)}(?![\w])" for spelling in spellings)
    )

    found: list[str] = []
    for path in sorted(_chart_files(chart_dir)):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(text):
            found.append(path.relative_to(chart_dir).as_posix())
    return tuple(found)


def _chart_files(chart_dir: Path) -> Iterable[Path]:
    """Everything under a chart that describes what it delivers, dependencies excluded."""
    for pattern in CHART_TEXT:
        for path in chart_dir.rglob(pattern):
            relative = path.relative_to(chart_dir).parts
            if relative and relative[0] in EXCLUDED_DIRS:
                continue
            yield path


def _first_line(docs: Any) -> str:
    if not isinstance(docs, str):
        return ""
    for line in docs.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _short(label: str) -> str:
    """`tankovault/contracts/api.json` as `api` — the vendored file, without the path around it."""
    return Path(label).stem


