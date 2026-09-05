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

Printed and never written:

    the `unbound` entries      a key nobody surfaces needs a reason, and a canned one is not a
                               reason. `config_declaration.Unbound` is where that is argued
    the `derivedConfig` lines  the chart's own template, whose hand-written shape — its `with`
                               blocks, its `if` gates — this cannot safely edit
    the `secretData` lines     the same, for a credential

Which means a `--write` run leaves the chart *rendering* exactly what it rendered before. The
value exists, it is typed, it is documented and it is bound; nothing reaches the image until
somebody wires the projection, which is the one step that needs to know what the chart already
does. `just check-config-bindings` goes green at the end of this command and `just check-config`
never went red, so the state in between is a chart with a value nothing reads — visible in the
diff, and named line by line in the closing output.

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


@dataclass
class Adoption:
    """What one chart is owed, and the edit that would settle it."""

    chart: str
    values: Path
    plan: sc.Plan = field(default_factory=sc.Plan)
    insertions: list[Insertion] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    @property
    def owed(self) -> bool:
        return bool(self.insertions or self.refusals or self.blocked)

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
            refusals.append(Refusal(placement.path, _occupied(placement)))
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

    adoption = Adoption(chart=chart_dir.name, values=values_path)
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
    return adoption


def write(adoption: Adoption) -> None:
    """Apply one chart's insertions, bottom-up, keeping the file's line endings.

    Read and written as bytes for the reason `config_shapes` records: this working tree is CRLF,
    and a rewrite that normalised it would reflow every line of every file it touched.
    """
    text = adoption.values.read_bytes().decode("utf-8")
    ending = "\r\n" if text.count("\r\n") else "\n"
    lines = text.splitlines(keepends=True)

    # A last line with no terminator would otherwise have the first inserted line appended to it.
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += ending

    for insertion in sorted(adoption.insertions, key=lambda item: item.after, reverse=True):
        above = [""] if insertion.spaced else []
        body = [f"{line}{ending}" for line in [*above, *insertion.lines]]
        lines[insertion.after : insertion.after] = body

    adoption.values.write_bytes("".join(lines).encode("utf-8"))


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

    still_owed(adoption)


def still_owed(adoption: Adoption) -> None:
    """The three hand edits this command deliberately does not make.

    Printed on a dry run as well, because the values block on its own is the half that reads as
    done — a value that is typed, documented and bound but that no template projects is a setting
    an operator can set and nothing honours, which is the state this output exists to make loud.
    """
    plan = adoption.placed

    if plan.projected:
        print(f"\n  still owed — {adoption.chart}/templates/_helpers.tpl, in `derivedConfig`:\n")
        for line in sc.derived_for(plan.projected, "  "):
            print(f"  {line}")

    if plan.secrets:
        print(f"\n  still owed — {adoption.chart}/templates/_helpers.tpl, in `secretData`:\n")
        for line in sc.secret_data_for(plan.secrets):
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

    groups = sc.write_off_groups(plan)
    if groups:
        print(f"\n  still owed — {adoption.chart}/config-contract.yaml, under `unbound:`:\n")
        for paths, reason in groups:
            print("    - keys:")
            for path in paths:
                print(f"        - {path}")
            print("      reason: >-")
            for line in sc.wrap(reason, "        ", sc.WIDTH):
                print(line)


def closing(adoptions: list[Adoption], *, written: bool) -> None:
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
        "    Then, in order:\n"
        "      1. wire each `derivedConfig` and `secretData` line above into that chart's\n"
        "         templates/_helpers.tpl — until that is done the values reach nothing\n"
        "      2. replace every `TODO` description and every invented default named above\n"
        "      3. add the `unbound` entries above, with reasons somebody stands behind\n"
        "      4. bump the chart version, then `just schema` and `just docs`\n"
        "      5. `just check-config-bindings`, then `just check-config`"
    )


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
        if args.write and adoption.insertions:
            write(adoption)
        report_chart(adoption, written=args.write)

    closing(adoptions, written=args.write)

    if any(item.blocked or item.refusals for item in adoptions):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
