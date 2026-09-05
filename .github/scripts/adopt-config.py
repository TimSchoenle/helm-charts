#!/usr/bin/env python3
"""Writing the chart values for the contract keys a chart surfaces nothing for.

`just check-config-bindings` reports the failure this repository keeps hitting: an image release
adds a setting, the automated bump repins the digest and omits everything else, and the new key is
reached by no chart value. It reports it per key and names the two repairs — bind the key to a
value, or write it off with a reason — and both are hand work. The first is the same block every
time:

    # @schema
    # # @config projection telemetry.sentry.sample_rate optional
    # type: [number, 'null']
    # minimum: 0
    # maximum: 1
    # @schema
    # -- Fraction of events sent to Sentry (`telemetry.sentry.sample_rate`).
    sampleRate: null

Every line of that is in the contract already. The constraint is `config_shapes`' rendering of the
key's own JSON Schema, the description is the first paragraph of the prose the producer published,
the default is the one the binary compiles in, and the marker names the key the block was derived
from. So it is written from there, and what is left for a person is the judgement this cannot
make: whether the setting deserves a first-class value at all, and what the chart's template does
with it.

Reads the vendored contract and nothing else, so what it can see is whatever the last
`just contracts` fetched — and that refresh resolves the image `values.yaml` pins, digest and
all. A newer build therefore reaches this command only once the pin moves, which is what
`just sync-config` does the two halves of in order.

--------------------------------------------------------------------------------------------
Why this may generate a marker when `config_bindings.py` says nothing does
--------------------------------------------------------------------------------------------

`config_bindings.py` says markers are hand-written, and it is right about the case it describes:
on a value that already exists, the *value* is the fact and a marker is a claim about it. A
generator guessing whether `telemetry.logLevel` or `logging.level` feeds `telemetry.log_level`
produces a wrong guess that reads exactly like a right one, and no gate can tell the two apart.

This never touches a value that exists. It creates one, from a key, and emits the marker in the
same breath — so there is no prior mapping for the marker to be wrong about, because the marker
*is* the mapping and the value was named to match it. That is the argument `config_scaffold.py`
already makes for a chart being scaffolded from scratch; the only thing new here is that the chart
is already on disk.

The boundary is enforced rather than assumed. A contract key whose chart value already exists is
**refused**, with the marker to write by hand — that is the one case where the two facts could
disagree, and it is exactly the case the rule above is about.

--------------------------------------------------------------------------------------------
What it writes, and what it only prints
--------------------------------------------------------------------------------------------

Written into `values.yaml`, under `--write`:

    an ordinary key            a value typed and defaulted from the contract, with its marker
    a credential               a value with no marker, for the Secret rather than the document
    a missing grouping block   the `telemetry:` the new value needs, with a `TODO` description

Written into `config-contract.yaml`, under `--write`:

    an `unbound` group         for a `reserved` key and for a credential — the two write-offs
                               whose reason the contract states rather than a person deciding it.
                               `declaration_edits` is where that licence is drawn, and why it
                               goes no further
    a `credentials` row        the `value:` column, which is the chart value just written

Written into `templates/_helpers.tpl`, under `--write`:

    the `derivedConfig` lines  appended at the define's own depth, and only for a key whose root
                               the define does not already write — a second `telemetry:` beside
                               an existing one is dropped by `fromYaml` in silence
    the `secretData` lines     the same rule, by secrets-file name

Printed and never written:

    a projection whose root    the define already carries it, so where the new lines belong
    the define already has     inside it is a question about a hand-written block
    a credential's `note:`     when a release needs one at all is the chart's knowledge
    a `TODO` description       a contract says nothing about the blocks a chart groups keys into
    an invented default        for a required key whose image publishes none

So a `--write` run leaves the chart binding, rendering and testing the new settings, and what is
left for a person is prose and judgement. Two generated files it does *not* touch are owed
immediately afterwards, and the closing output leads with them: `values.schema.json`, without
which every render fails on a values block the schema has not seen, and the round-trip suite,
whose new cases are what prove the projection above actually arrived.

--------------------------------------------------------------------------------------------
Where the block goes
--------------------------------------------------------------------------------------------

After the last sibling that binds a contract key, inside the deepest ancestor of the new value
that already exists. Both halves of that are deliberate.

Alphabetically would be wrong: these files are ordered by how often a value is edited, not by
name — the image first, the settings after it, thirty blocks of chassis at the bottom — and a
`sentry` inserted between `image` and `imagePullSecrets` would be sorted and unreadable. At the
end of the file would be worse: it lands under the chassis, a screen and a half below the values
it belongs with.

The last *bound* sibling is where the configuration surface ends, and it is derived from the
markers rather than declared — which is what makes it right for a chart nobody wrote this rule
for. A new top-level block lands after the last top-level block that binds anything, which in
every chart here is the last block before the chassis.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_bindings as cb
import config_contract as cc
import config_scaffold as sc
from config_declaration import (
    Bound,
    Declaration,
    DeclarationError,
    chart_dirs,
    load_declaration,
)
from config_paths import CHARTS_DIR, dig, read_yaml
from config_report import Report

# The fields of a contract key that decide what its chart value looks like. Two documents
# declaring one path have to agree on every one of them before a single value can feed both, and
# the list is the placement's inputs rather than a subset chosen by hand: `plan_keys` branches on
# `reserved`, `secret` and `text_form`, `schema_lines` reads `constraint` and `required`,
# `default_for` reads `default_value`, and `secrets_file_name` reads `secrets_file`.
SHAPE_FIELDS = (
    "constraint",
    "text_form",
    "required",
    "secret",
    "reserved",
    "default_value",
    "secrets_file",
)


def gate_module():
    """`check-config-bindings.py` as a module.

    Loaded by path because the file name is hyphenated and a plain `import` cannot name it — the
    same six lines `new-chart.py` needs for `refresh-contracts.py`, and for the same reason.

    Imported at all because rule 5 is its rule. `missing_keys` is the set this command acts on,
    and a second implementation of "which keys are owed" would disagree with the gate the first
    time a marker's scope or an `unbound` entry changed — leaving this writing a value for a key
    somebody deliberately wrote off, or reporting success on a chart the gate still fails.
    """
    import importlib.util

    name = "check_config_bindings"
    if name in sys.modules:
        return sys.modules[name]

    path = Path(__file__).resolve().parent / "check-config-bindings.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path}: cannot be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = gate_module()


# --------------------------------------------------------------------------------------------
# What one chart is owed
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """One key this command will not write a value for, and what to do instead.

    Reported per key rather than failing the chart, so a contract that adds five ordinary keys and
    one awkward one still gets the five. Every refusal names the hand edit that settles it.
    """

    path: str
    reason: str
    # Whether the refusal is "the chart already has this value". A credential refused that way is
    # still owed its `unbound` entry — the write-off says the key does not travel the
    # configuration document, which is true of the key and not of whoever wrote the value — so
    # the two refusal kinds are told apart rather than counted together.
    occupied: bool = False


@dataclass
class Insertion:
    """One run of lines, and the line of `values.yaml` it goes after.

    Several per file is the ordinary case — a chart that gains keys under two existing blocks gets
    one insertion each — so they are applied from the bottom up, exactly as `config_shapes`
    rewrites blocks, and for the same reason: an earlier edit must not move a line number a later
    one was read at.
    """

    after: int
    lines: list[str]
    paths: tuple[str, ...]
    branches: tuple[str, ...]
    # A blank line above the block, except where it would be the first thing under the mapping
    # key it belongs to: every values file here opens a block with its first value rather than
    # with an empty line.
    spaced: bool = True


@dataclass(frozen=True)
class FileEdit:
    """One run of lines for a file other than values.yaml, and the line it goes after.

    An adoption touches three files, and the two beyond `values.yaml` are the ones a chart is red
    without. A key surfaced by no value is owed an `unbound` entry; a value bound to a key is owed
    the template line that projects it. A run that wrote the values and left either alone leaves
    the chart failing the very gates it was run to satisfy — which is what happened to
    `discord-alertmanager`, twice: once on the declaration and once on the helper.

    `what` names the block for the report, so a reader is told what grew and where.
    """

    path: Path
    after: int
    lines: list[str]
    what: str


@dataclass
class Adoption:
    """What one chart is owed, and the edit that would settle it."""

    chart: str
    values: Path
    declaration_path: Path
    plan: sc.Plan = field(default_factory=sc.Plan)
    insertions: list[Insertion] = field(default_factory=list)
    edits: list[FileEdit] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    # The projections that could not be appended, because the define already carries their root.
    # Printed rather than written; `projection_edits` is where that line is drawn.
    owed_projected: list[sc.Placement] = field(default_factory=list)
    owed_secrets: list[sc.Placement] = field(default_factory=list)

    @property
    def owed(self) -> bool:
        return bool(self.insertions or self.edits or self.refusals or self.blocked)

    @property
    def occupied(self) -> list[sc.Placement]:
        """The credentials whose chart value was already there, so nothing was written for them.

        Still owed the write-off, which is why they are kept: a credential travels the secrets
        directory rather than the configuration document, and that is a property of the key. The
        reason written for one of these names the secrets file and not the chart value — naming
        the value would be the claim the refusal was issued to avoid making.
        """
        refused = {item.path for item in self.refusals if item.occupied}
        return [item for item in self.plan.secrets if item.path in refused]

    @property
    def placed(self) -> sc.Plan:
        """The plan restricted to the values that are actually being written.

        Everything the closing report says is about these. A refused key is one the chart already
        has a value for, so its projection line is a line the template most likely already carries
        and its "invented default" is a default nobody is about to write — printing either would
        be asking for an edit that has no place to go.

        The write-offs stay whole: nothing inserts them, and they are owed a reason whether or not
        anything else in the chart could be written.
        """
        written = {path for insertion in self.insertions for path in insertion.paths}
        return sc.Plan(
            projected=[item for item in self.plan.projected if item.path in written],
            secrets=[item for item in self.plan.secrets if item.path in written],
            written_off=list(self.plan.written_off),
        )


def owed_keys(
    bound: Bound, missing: dict[str, list[str]], markers: list[cb.Marker]
) -> tuple[list[dict[str, Any]], list[Refusal]]:
    """The keys of one chart that are owed a value, each resolved to a single description.

    A key declared by several documents is one value — that is what an unscoped marker means, and
    what every multi-document chart here already does — so the documents that declare it have to
    agree about what it is. `check-config-bindings` refuses a marker whose documents disagree
    about `text_form`; this refuses the same disagreement one field wider, because the value it
    would write is typed, defaulted and delivered from all seven fields rather than from one.

    A key that is missing in one document and *bound* in another is refused as well, and it is the
    subtler case: a scoped marker already maps it onto a value, so what is owed is the scope of
    that marker rather than a second value under the same name.
    """
    owed: list[dict[str, Any]] = []
    refusals: list[Refusal] = []

    everywhere = sorted({path for paths in missing.values() for path in paths})
    already = {marker.target for marker in markers if marker.cls in cb.KEY_CLASSES}

    for path in everywhere:
        declares = sorted(name for name in missing if path in missing[name])

        if path in already:
            elsewhere = sorted(
                name
                for name in bound.documents
                if path in bound.unions[name].keys and path not in missing[name]
            )
            refusals.append(
                Refusal(
                    path,
                    f"is bound in {', '.join(elsewhere)} and unbound in {', '.join(declares)}. A "
                    "scoped marker already maps it onto a value, so what is owed is that marker's "
                    "scope — a second value under the same name would be two chart values for one "
                    "setting, which rule 4 refuses",
                )
            )
            continue

        entries = [bound.unions[name].keys[path] for name in declares]
        shapes = {tuple(repr(entry.get(name)) for name in SHAPE_FIELDS) for entry in entries}
        if len(shapes) > 1:
            refusals.append(
                Refusal(
                    path,
                    f"is declared by {', '.join(declares)}, which do not agree what it is. One "
                    "path in two shapes is two settings that share a name, so the values that "
                    "feed them are written by hand, one per document, with a scoped marker each",
                )
            )
            continue

        owed.append(entries[0])

    return owed, refusals


def plan_for(owed: list[dict[str, Any]]) -> sc.Plan:
    """Sort the owed keys the way the scaffold sorts a whole contract's.

    Through `plan_keys` on a `Union` carrying nothing but these keys, rather than through a copy
    of its table. The destination rules — a reserved key gets no value, a credential goes to the
    Secret and never to the document, a credential no file can supply gets no channel at all — are
    judgements this repository has already made once, and making them twice is how two generators
    come to disagree about where a credential belongs.
    """
    return sc.plan_keys(cc.Union(keys={str(entry["path"]): entry for entry in owed}))


# --------------------------------------------------------------------------------------------
# Where the blocks go
# --------------------------------------------------------------------------------------------


def parent_of(values_path: str) -> str:
    return values_path.rpartition(".")[0]


def bearing(regions: list[cb.Region], markers: list[cb.Marker]) -> set[str]:
    """The values paths whose subtree binds at least one contract key.

    A block bears a binding if it or anything under it carries a marker, which is what makes
    `telemetry` part of the configuration surface when the markers are three levels down.
    """
    targets = {marker.values_path for marker in markers}
    return {
        region.values_path
        for region in regions
        if any(
            target == region.values_path or target.startswith(f"{region.values_path}.")
            for target in targets
        )
    }


def insertion_point(
    regions: list[cb.Region], bound_paths: set[str], ancestor: str, lines: int
) -> int:
    """The line a new child of `ancestor` goes after.

    After the last child that binds something, so the new value lands at the end of the
    configuration surface rather than at the end of the file; after the ancestor's own last line
    when none of its children binds anything; and at the end of the file for a top-level block in
    a chart whose markers are all nested — which no chart here is, and which would otherwise have
    no derivable answer at all.
    """
    children = [region for region in regions if parent_of(region.values_path) == ancestor]
    binding = [region for region in children if region.values_path in bound_paths]
    if binding:
        return max(region.end for region in binding)

    if ancestor:
        return next(region.end for region in regions if region.values_path == ancestor)
    return lines


def insertions_for(
    values: dict[str, Any],
    regions: list[cb.Region],
    markers: list[cb.Marker],
    plan: sc.Plan,
    lines: int,
) -> tuple[list[Insertion], list[Refusal]]:
    """The blocks to insert, and the keys no block can be written for, for one chart.

    Placements are grouped by the existing block they land in, so two keys arriving under one new
    `telemetry.sentry` produce one `sentry:` heading rather than two — what
    `config_scaffold.tree_of` does for a whole chart, restricted to the part that does not exist
    yet.
    """
    placed = {region.values_path for region in regions}
    bound_paths = bearing(regions, markers)
    refusals: list[Refusal] = []
    groups: dict[str, list[sc.Placement]] = {}

    # A mapping key written with nothing under it — `feed:` whose only child was removed — is
    # a parent to insert into rather than a leaf to refuse: `None` there is the absence of
    # children, and every other scalar is a value that cannot also be a mapping.
    for placement in plan.projected + plan.secrets:
        if placement.values_path in placed or cb.has_path(values, placement.values_path):
            refusals.append(Refusal(placement.path, _occupied(placement), occupied=True))
            continue

        ancestor = ""
        segments = placement.values_path.split(".")
        for cut in range(len(segments) - 1, 0, -1):
            candidate = ".".join(segments[:cut])
            if candidate in placed:
                ancestor = candidate
                break

        occupant = dig(values, ancestor) if ancestor else None
        if ancestor and occupant is not None and not isinstance(occupant, dict):
            refusals.append(
                Refusal(
                    placement.path,
                    f"needs the chart value {placement.values_path!r}, and {ancestor!r} is a "
                    "leaf value on the way there. The two cannot both exist, so this mapping has "
                    "to be written by hand",
                )
            )
            continue

        groups.setdefault(ancestor, []).append(placement)

    insertions: list[Insertion] = []
    by_path = {region.values_path: region for region in regions}
    for ancestor, placements in groups.items():
        depth = len(ancestor.split(".")) if ancestor else 0
        # Read off the block being inserted into rather than computed from the depth, so a file
        # indented by something other than two spaces is extended in its own style rather than in
        # this script's idea of one.
        indent = " " * (by_path[ancestor].indent + 2) if ancestor else ""
        tree = _subtree(placements, depth)
        after = insertion_point(regions, bound_paths, ancestor, lines)
        insertions.append(
            Insertion(
                after=after,
                lines=sc.render_tree(tree, indent, depth),
                paths=tuple(sorted(placement.path for placement in placements)),
                branches=tuple(_branches(tree, ancestor)),
                spaced=not ancestor or after != by_path[ancestor].line,
            )
        )

    return sorted(insertions, key=lambda item: item.after), refusals


def _occupied(placement: sc.Placement) -> str:
    """Why a key whose chart value already exists is refused, and what to write instead.

    The one refusal that is a rule rather than an obstacle — see this module's second section. A
    credential gets a different sentence because it never carries a marker: what an existing value
    is owed there is the write-off and the Secret line, not a binding.
    """
    if placement.where == sc.SECRET_FILE:
        return (
            f"needs the chart value {placement.values_path!r}, which this chart already has. A "
            "credential carries no marker, so what is owed is an `unbound` entry naming this key "
            "and a `secretData` line delivering that value"
        )
    optional = " optional" if placement.optional else ""
    return (
        f"needs the chart value {placement.values_path!r}, which this chart already has. Whether "
        "that value feeds this key is a claim only a person can make, so write "
        f"`# # {cb.MARKER} {placement.marker_class} {placement.path}{optional}` into its "
        "`@schema` block by hand"
    )


def _subtree(placements: list[sc.Placement], depth: int) -> dict[str, Any]:
    """The placements as a tree rooted at their existing ancestor rather than at the file.

    `render_tree` renders a level at a time and reads a branch's contract prefix off the depth it
    was given, so a subtree handed to it has to be paired with the depth it hangs at — which is
    the ancestor's, and the reason this is not `tree_of` with the first segments dropped.
    """
    tree: dict[str, Any] = {}
    for placement in placements:
        segments = placement.values_path.split(".")[depth:]
        node = tree
        for segment in segments[:-1]:
            node = node.setdefault(segment, {})
        node[segments[-1]] = placement
    return tree


def _branches(tree: dict[str, Any], prefix: str) -> list[str]:
    """The grouping blocks one insertion creates, as values paths.

    Reported at the end of the run because `render_tree` writes each of them a `TODO` description:
    a contract says nothing about the blocks a chart groups its keys into, so there is no honest
    sentence to derive, and a placeholder nobody was told about is one helm-docs publishes into
    the README table. `config_scaffold.undescribed` answers the same question for a whole chart
    and cannot be used here — it would name the blocks that already exist as well, which this
    command did not write and must not claim.
    """
    found: list[str] = []
    for name in sorted(tree):
        node = tree[name]
        if isinstance(node, sc.Placement):
            continue
        path = f"{prefix}.{name}" if prefix else name
        found.append(path)
        found.extend(_branches(node, path))
    return found


# --------------------------------------------------------------------------------------------
# The declaration: the write-offs and the credential columns
# --------------------------------------------------------------------------------------------

# The block a chart gets when it has none, in the spelling `config_scaffold.render_declaration`
# writes for a chart being created. One wording for the same fact, so a chart that grew its
# declaration here reads like one that was scaffolded with it.
UNBOUND_HEADER = (
    "# Contract keys no chart value binds, each group with the reason it does not.",
    "#",
    "# Chart-level: a key nothing surfaces is unsurfaced in every document that declares",
    "# it. `documents:` would narrow an entry where that is untrue.",
    "unbound:",
)

CREDENTIALS_HEADER = (
    "# What this chart adds to the credential reference in its README, which",
    "# `just config-readme` generates from the contract. The rows are the contract's — every",
    "# key it marks `secret: true`, and whether the image requires it — and these columns are",
    "# the chart's: the value an operator may set instead of pre-creating the Secret, and the",
    "# condition under which a release needs the credential at all. The second is nobody's to",
    "# derive and is written by hand, as a `note:` beside the `value:`.",
    "credentials:",
)


def declaration_edits(
    chart_dir: Path,
    declaration: Declaration,
    plan: sc.Plan,
    occupied: list[sc.Placement],
) -> list[FileEdit]:
    """The `unbound` groups and `credentials` rows this adoption owes `config-contract.yaml`.

    **Every reason written here is read off the contract rather than decided.** That is the whole
    licence for writing them, and it is narrower than it looks: a write-off says *why no chart
    value surfaces this key*, and this command produces exactly two answers to that — `reserved`,
    which is the image saying the loader sets the key itself, and `secret`, where the repository's
    standing rule sends a credential down the secrets directory and never through a ConfigMap.
    Both are facts in the document. The third kind of write-off — a chart choosing not to expose
    an ordinary setting — is a judgement, and it is never produced here, because an ordinary key
    gets a value instead. `config_scaffold.render_declaration` already writes these same two
    sentences for a chart being created; this is that, one key at a time.

    The `credentials` row is the same argument once more. `value` is the chart value carrying the
    credential, which is a fact this command has because it just wrote that value — the reason
    `CredentialNote` calls it underivable is that a credential carries no marker, and at the
    moment of writing there is nothing to derive it *from* except the placement that produced it.
    `note` — when a release needs the credential at all — is genuinely the chart's, and is left
    for a person.
    """
    path = chart_dir / "config-contract.yaml"
    regions = {region.values_path: region for region in cb.regions(path)}

    edits: list[FileEdit] = []
    fresh: list[tuple[str, list[str]]] = []

    rows = credential_rows(declaration, plan)
    if rows:
        if "credentials" in regions:
            edits.append(FileEdit(path, regions["credentials"].end, rows, "credentials"))
        else:
            fresh.append(("credentials", [*CREDENTIALS_HEADER, *rows]))

    groups = write_off_lines(plan, occupied, declaration)
    if groups:
        if "unbound" in regions:
            edits.append(FileEdit(path, regions["unbound"].end, groups, "unbound"))
        else:
            fresh.append(("unbound", [*UNBOUND_HEADER, *groups]))

    if fresh:
        body: list[str] = []
        for _, lines in fresh:
            body.extend(["", *lines])
        edits.append(
            FileEdit(
                path, before_documents(regions), body, " and ".join(name for name, _ in fresh)
            )
        )

    return edits


def before_documents(regions: dict[str, cb.Region]) -> int:
    """The line a block that must sit above `documents:` goes after.

    The end of the last top-level key before it, rather than the line above `documents:` itself:
    every declaration here opens each of its blocks with a paragraph of prose, and an insertion
    between that paragraph and the key it explains would attach it to the wrong one.
    """
    documents = regions["documents"].line
    above = [region.end for region in regions.values() if region.line < documents]
    return max(above) if above else 0


def written_off_anywhere(declaration: Declaration) -> set[str]:
    """Every key the declaration already writes off, in any document.

    A second entry for a key that has one is not a worse file than a missing entry — it is the
    same drift the `unbound` docstring is about, one reason saying what another already said —
    and `check-config-bindings` would not catch it, because both are true.
    """
    return {key for entry in declaration.unbound for key in entry.keys}


def occupied_reason(placement: sc.Placement) -> str:
    """Why a credential the chart already carries a value for is bound by no marker."""
    return (
        "Delivered as a file in the secrets directory — from the Secret this chart renders, or "
        f"from `existingSecret` under the file name `{sc.secrets_file_name(placement)}` — and "
        "never written into the configuration document, because a credential in a ConfigMap is "
        "readable by anything that can read the namespace. `just check-config-secrets` is what "
        "reconciles that channel, and it does it from the rendered manifests rather than from a "
        "comment."
    )


def credential_rows(declaration: Declaration, plan: sc.Plan) -> list[str]:
    """A `credentials` row per credential this adoption surfaced and the chart has not described."""
    lines: list[str] = []
    for item in plan.secrets:
        if item.path in declaration.credentials:
            continue
        lines.append(f"  - key: {item.path}")
        lines.append(f"    value: {item.values_path}")
    return lines


def write_off_lines(
    plan: sc.Plan, occupied: list[sc.Placement], declaration: Declaration
) -> list[str]:
    """The `unbound` groups for this adoption, in `render_declaration`'s layout.

    `occupied` are the credentials whose value the chart already had. They get the same sentence
    with one clause removed — the one naming the chart value that carries the credential, which
    is the claim this command refuses to make about a value it did not write.
    """
    already = written_off_anywhere(declaration)
    groups = [
        (paths, reason)
        for paths, reason in sc.write_off_groups(plan)
        if not set(paths) <= already
    ]
    for item in occupied:
        if item.path in already:
            continue
        groups.append(([item.path], occupied_reason(item)))

    lines: list[str] = []
    for paths, reason in groups:
        lines.append("  - keys:")
        lines.extend(f"      - {path}" for path in paths)
        lines.append("    reason: >-")
        lines.extend(sc.wrap(reason, "      ", sc.WIDTH))
    return lines


# --------------------------------------------------------------------------------------------
# The template: projecting the values into the document
# --------------------------------------------------------------------------------------------

# The Go-template actions that open a nesting level, and the one that closes it. `else` is
# neither. Read per action rather than per line, because a line may carry several.
OPENERS = ("if", "with", "range", "define", "block")
ACTION = re.compile(r"\{\{-?\s*(\w+)")
TOP_LEVEL_KEY = re.compile(r"^(?P<name>[A-Za-z0-9_][A-Za-z0-9_.\-]*):")


@dataclass(frozen=True)
class Define:
    """One `{{- define }}` in a chart's `_helpers.tpl`, as the lines a caller may append to.

    `end` is the line of the define's own closing action, so a new top-level entry is inserted
    after `end - 1` and lands inside the define. `roots` are the mapping keys already written at
    the define's own nesting depth — the ones a second block of the same name would shadow.
    """

    end: int
    roots: frozenset[str]


def read_define(text: str, name: str) -> Define | None:
    """Find one define and the top-level keys it already writes; `None` when it cannot be read.

    A helper is hand-written Go template, and this reads exactly as much of it as an append needs:
    where the define ends, and which keys sit at its own depth. Anything deeper is inside an `if`
    or a `with` and is none of an appender's business — the point of tracking depth at all is to
    land *outside* every one of them, so that a projection is not silently written under somebody
    else's condition.

    `None` when the define is absent, or when the actions do not balance — a template shape this
    cannot read is one it must not edit, and the caller prints the lines instead.
    """
    lines = text.splitlines()
    opening = f'define "{name}"'
    start = next((number for number, line in enumerate(lines) if opening in line), None)
    if start is None:
        return None

    depth = 0
    roots: set[str] = set()
    for number in range(start, len(lines)):
        line = lines[number]
        if depth == 1:
            key = TOP_LEVEL_KEY.match(line)
            if key is not None:
                roots.add(key.group("name"))

        for action in ACTION.findall(line):
            if action in OPENERS:
                depth += 1
            elif action == "end":
                depth -= 1
                if depth == 0:
                    return Define(end=number + 1, roots=frozenset(roots))

    return None


def projection_edits(
    chart_dir: Path, chart: str, plan: sc.Plan
) -> tuple[list[FileEdit], list[sc.Placement], list[sc.Placement]]:
    """The `derivedConfig` and `secretData` lines to append, and the ones that must be printed.

    **The one rule that decides whether a line can be written: does the tree already have this
    key's root?** A projection is appended at the define's own depth, so it cannot land inside
    somebody's `if`; what it *can* do is write a second `telemetry:` beside an existing one, and
    the document is built by `fromYaml`, which keeps the last of two mapping keys and drops
    everything under the first. That is the defect `test_contract_scaffold` records from the other
    direction — settings and credentials sharing one values tree — and it is silent: the values
    file parses, the chart renders, and a setting has vanished. So a key whose root is already in
    the define is printed for a person to place, and only a new root is written.

    That leaves the common case automatic and the ambiguous one manual, which is the same split
    every other writer here takes. `discord-alertmanager` is the measured case: fifteen new keys
    under a `telemetry` root the helper had never heard of, fifteen round-trip cases red until
    somebody typed them out.
    """
    path = chart_dir / "templates" / "_helpers.tpl"
    if not path.is_file():
        return [], list(plan.projected), list(plan.secrets)

    text = path.read_bytes().decode("utf-8")
    edits: list[FileEdit] = []

    derived = read_define(text, f"{chart}.derivedConfig")
    if derived is None:
        projected, owed_projected = [], list(plan.projected)
    else:
        projected = [
            item for item in plan.projected if item.path.split(".")[0] not in derived.roots
        ]
        owed_projected = [item for item in plan.projected if item not in projected]
        if projected:
            edits.append(
                FileEdit(path, derived.end - 1, sc.derived_for(projected), "derivedConfig")
            )

    secrets = read_define(text, f"{chart}.secretData")
    if secrets is None:
        written, owed_secrets = [], list(plan.secrets)
    else:
        written = [
            item for item in plan.secrets if sc.secrets_file_name(item) not in secrets.roots
        ]
        owed_secrets = [item for item in plan.secrets if item not in written]
        if written:
            edits.append(
                FileEdit(path, secrets.end - 1, sc.secret_data_for(written), "secretData")
            )

    return edits, owed_projected, owed_secrets


# --------------------------------------------------------------------------------------------
# Reading one chart
# --------------------------------------------------------------------------------------------


def adopt(chart_dir: Path, wanted: set[str]) -> Adoption | None:
    """What one chart is owed; `None` when its declaration does not enrol it.

    The gate's own first four rules run before anything is planned, and a chart that fails one of
    them is blocked rather than written to. A marker naming a key that does not exist means the
    chart's mapping is already wrong somewhere, and the keys this would then take to be unbound
    are whatever that mistake left over — so it would write values for keys that are bound, under
    names nobody chose.
    """
    values_path = chart_dir / "values.yaml"
    declaration = load_declaration(chart_dir)
    if declaration is None or not declaration.bindings or not values_path.is_file():
        return None

    adoption = Adoption(
        chart=chart_dir.name,
        values=values_path,
        declaration_path=chart_dir / "config-contract.yaml",
    )
    if not declaration.documents:
        adoption.blocked.append(
            "declares `bindings: true` and no configuration contract, so there is nothing to "
            "adopt keys from"
        )
        return adoption

    bound = Bound(chart_dir, declaration)
    markers = cb.parse_values(values_path, chart_dir.name)
    values = read_yaml(values_path)

    report = Report()
    rules = gate.Gate(report)
    resolved = rules.resolve(bound, markers, values)
    rules.check_uniqueness(resolved)
    rules.check_write_offs(bound)
    if report.errors:
        adoption.blocked.extend(finding.message for _, finding in report.errors)
        return adoption

    owed, refusals = owed_keys(bound, gate.missing_keys(bound, resolved), markers)
    adoption.refusals.extend(refusals)

    if wanted:
        known = {str(entry["path"]) for entry in owed} | {item.path for item in refusals}
        for path in sorted(wanted - known):
            adoption.refusals.append(
                Refusal(path, f"is not a key {chart_dir.name} is owed a value for")
            )
        owed = [entry for entry in owed if str(entry["path"]) in wanted]
        adoption.refusals = [item for item in adoption.refusals if item.path in wanted]

    adoption.plan = plan_for(owed)
    lines = len(values_path.read_text(encoding="utf-8").splitlines())
    insertions, conflicts = insertions_for(
        values, cb.regions(values_path), markers, adoption.plan, lines
    )
    adoption.insertions = insertions
    adoption.refusals.extend(conflicts)
    # After the insertions, because the write-offs owed are the ones for values that will be
    # written: a credential whose value was refused is a credential this chart already delivers,
    # and an entry claiming otherwise would be describing an edit nobody made.
    adoption.edits = declaration_edits(
        chart_dir, declaration, adoption.placed, adoption.occupied
    )
    projections, owed_projected, owed_secrets = projection_edits(
        chart_dir, chart_dir.name, adoption.placed
    )
    adoption.edits.extend(projections)
    adoption.owed_projected = owed_projected
    adoption.owed_secrets = owed_secrets
    return adoption


def write(adoption: Adoption) -> None:
    """Apply one chart's edits: the values, the declaration and the helper they oblige.

    All three, because that set is what a green chart needs. Values with no write-off fail
    `check-config-bindings`; values with no projection fail the round trip the same run
    regenerates. A command that wrote one of the three would be a command whose output is never
    the finished state.
    """
    _apply(
        adoption.values,
        [
            (insertion.after, ([""] if insertion.spaced else []) + insertion.lines)
            for insertion in adoption.insertions
        ],
    )
    for path in sorted({edit.path for edit in adoption.edits}):
        _apply(path, [(edit.after, edit.lines) for edit in adoption.edits if edit.path == path])


def _apply(path: Path, edits: list[tuple[int, list[str]]]) -> None:
    """Insert each run of lines after the line it names, bottom-up, keeping the line endings.

    Bottom-up so an earlier insertion cannot move a line number a later one was read at, which is
    how `config_shapes` rewrites blocks and for the same reason. Read and written as bytes for the
    reason it records too: this working tree is CRLF, and a rewrite that normalised it would
    reflow every line of every file it touched.
    """
    if not edits:
        return

    text = path.read_bytes().decode("utf-8")
    ending = "\r\n" if text.count("\r\n") else "\n"
    lines = text.splitlines(keepends=True)

    # A last line with no terminator would otherwise have the first inserted line appended to it.
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += ending

    for after, body in sorted(edits, key=lambda item: item[0], reverse=True):
        lines[after:after] = [f"{line}{ending}" for line in body]

    path.write_bytes("".join(lines).encode("utf-8"))


# --------------------------------------------------------------------------------------------
# Saying what happened
# --------------------------------------------------------------------------------------------


def report_chart(adoption: Adoption, *, written: bool) -> None:
    """One chart's result: what was inserted, what was refused, and what is still owed."""
    rule = "=" * 92
    print(f"\n{rule}\n{adoption.chart}\n{rule}")

    if adoption.blocked:
        for message in adoption.blocked:
            print(f"  blocked: {message}")
        print("\n  Nothing was planned. `just check-config-bindings` reports the same thing.")
        return

    for insertion in adoption.insertions:
        where = f"{adoption.values.as_posix()}:{insertion.after}"
        print(f"\n  {'written after' if written else 'goes after'} {where}\n")
        for line in insertion.lines:
            print(f"    {line}" if line else "")

    for refusal in adoption.refusals:
        print(f"\n  refused: {refusal.path} {refusal.reason}")

    still_owed(adoption, written=written)


def still_owed(adoption: Adoption, *, written: bool) -> None:
    """What is left for a person, and the one edit that is not.

    Printed on a dry run as well, because the values block on its own is the half that reads as
    done — a value that is typed, documented and bound but that no template projects is a setting
    an operator can set and nothing honours, which is the state this output exists to make loud.
    """
    plan = adoption.placed

    if adoption.owed_projected:
        print(f"\n  still owed — {adoption.chart}/templates/_helpers.tpl, in `derivedConfig`:\n")
        for line in sc.derived_for(adoption.owed_projected, "  "):
            print(f"  {line}")
        print(
            "\n    The define already writes this key's root, and a second one would shadow"
            "\n    the first — `fromYaml` keeps the last. Place these in the block that exists."
        )

    if adoption.owed_secrets:
        print(f"\n  still owed — {adoption.chart}/templates/_helpers.tpl, in `secretData`:\n")
        for line in sc.secret_data_for(adoption.owed_secrets):
            print(f"    {line}")

    invented = sc.invented(plan)
    if invented:
        print("\n  still owed — a chosen default, in place of the one invented for:\n")
        for item in invented:
            print(f"    {item.values_path}: {sc.values_scalar(sc.default_for(item.key))}")
        print(
            "\n    Each is a key its image requires and publishes no default for, so the value"
            "\n    above is a legal one drawn from the constraint rather than a chosen one."
        )

    undescribed = [path for insertion in adoption.insertions for path in insertion.branches]
    if undescribed:
        print("\n  still owed — a `# --` description, in place of the `TODO` written into:\n")
        for path in undescribed:
            print(f"    {path}")

    for edit in adoption.edits:
        state = "written into" if written else "goes into"
        where = f"{edit.path.as_posix()}:{edit.after}"
        print(f"\n  {state} {where}, under `{edit.what}`:\n")
        for line in edit.lines:
            print(f"    {line}" if line else "")


def counted(charts: Path, adoptions: list[Adoption]) -> list[str]:
    """The rows the tree-level test asserts, for the charts this run touched.

    `test_every_enrolled_chart_passes_the_gate` holds the per-chart marker counts as literals, so
    every adoption moves one of them and the suite goes red until somebody edits it. The number is
    the gate's own — markers times the documents each binds — and re-deriving it here would be a
    second implementation of exactly the arithmetic that test exists to pin. So it is read back
    out of the gate, after the write, and printed as the line to paste.

    Not written. A test whose expected values a generator edits silently is a test nobody reads,
    which is the same argument that keeps `just contract-tests` out of the Documentation job.
    """
    touched = {item.chart for item in adoptions if item.insertions}
    rows = gate.run(charts, Report())
    return [
        f'        ("{chart}", {keys}, {external}),'
        for chart, keys, external in rows
        if chart in touched
    ]


def closing(adoptions: list[Adoption], charts_dir: Path, *, written: bool) -> None:
    """What the run added up to, and the work that has to follow it."""
    values = sum(len(item.placed.projected) + len(item.placed.secrets) for item in adoptions)
    charts = [item.chart for item in adoptions if item.insertions]

    if not written:
        print(
            f"\n==> {values} value(s) across {len(charts)} chart(s); nothing was written.\n"
            "    Re-run with --write to insert them."
        )
        return

    print(
        f"\n==> wrote {values} value(s) into {len(charts)} chart(s): {', '.join(charts)}\n\n"
        "    Then, in order — `just sync-config` does 1 and 5 for you:\n"
        "      1. `just schema`. values.schema.json is committed and Helm validates every\n"
        "         render against it, so a new block it does not know fails every job\n"
        "      2. read the `derivedConfig` and `secretData` lines that were written, and\n"
        "         place by hand any the output above says could not be\n"
        "      3. replace the text after each `TODO: ` — keep the `# --` line, since a\n"
        "         value with no description fails `just check-values-docs`\n"
        "      4. add a `note:` to each credential row saying when a release needs it, and\n"
        "         choose a default for each invented value named above\n"
        "      5. `just contract-tests <chart>` — the round trip gains a case per marker\n"
        "      6. paste the row(s) below into tests/test_contract_bindings.py, in\n"
        "         `test_every_enrolled_chart_passes_the_gate`\n"
        "      7. bump the chart version, then `just docs`\n"
        "      8. `just check-config-bindings`, `just check-values-docs`, `just test`"
    )

    rows = counted(charts_dir, adoptions)
    if rows:
        print()
        for row in rows:
            print(row)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("chart", nargs="?", default="", help="one chart, or every enrolled chart")
    parser.add_argument("--charts", default=CHARTS_DIR, type=Path, help="the charts directory")
    parser.add_argument(
        "--key",
        action="append",
        default=[],
        metavar="PATH",
        help="adopt only this contract key; repeatable",
    )
    parser.add_argument(
        "--write", action="store_true", help="insert the blocks rather than printing them"
    )
    args = parser.parse_args(argv)

    directories = [
        item for item in chart_dirs(args.charts) if not args.chart or item.name == args.chart
    ]
    if args.chart and not directories:
        print(f"error: {args.charts / args.chart} is not a chart", file=sys.stderr)
        return 2
    if args.key and not args.chart:
        print("error: --key selects keys of one chart, so name the chart", file=sys.stderr)
        return 2

    adoptions: list[Adoption] = []
    try:
        for chart_dir in directories:
            adoption = adopt(chart_dir, set(args.key))
            if adoption is not None and adoption.owed:
                adoptions.append(adoption)
    except (cb.BindingError, DeclarationError, cc.ContractError, sc.ScaffoldError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    if not adoptions:
        print("==> every contract key of every enrolled chart is bound or written off")
        return 0

    for adoption in adoptions:
        if args.write and (adoption.insertions or adoption.edits):
            write(adoption)
        report_chart(adoption, written=args.write)

    closing(adoptions, args.charts, written=args.write)

    if any(item.blocked or item.refusals for item in adoptions):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
