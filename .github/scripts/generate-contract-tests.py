#!/usr/bin/env python3
"""Generate the helm-unittest suites that prove a chart delivers what its contract declares.

`just check-config` validates the configuration a chart *renders*. Nothing validates the round
trip — that a setting an operator writes into the chart's values arrives in the application's
document, at the path the image reads it from, carrying the value that was asked for. A typo in
a template helper produces a document that satisfies the contract perfectly, because every key
it does contain is legal, and the setting is simply absent. There are 491 key declarations
across this repository and the hand-written suites exercise a handful of them, which is not a
gap anybody closes by hand.

This is the entry point: the walk over the charts, the file IO and the exit status. The model —
choosing a probe, refusing to invent one, and spelling the assertion — lives in
`config_testgen.py`, so every rule in it is testable by calling it.

**Enrolment is a file, not a list.** A chart is generated for when it carries
`charts/<chart>/contract-tests.yaml`, exactly as `config-contract.yaml` is what enrols it in
`just check-config`. That is what makes the phased rollout a property of the tree rather than of
a constant somebody has to remember to edit, and it is also where a chart says which values every
generated case has to carry before it will render at all, and — where the operator-facing
configuration tree is not the layer that wins — which values path a probe is to be written to.

**A baseline and a render prerequisite are two different fields because they are two different
things.** A `baseline` states configuration — flat dotted paths under the document's probe root —
and is dropped from the one case probing the key it sets, because a baseline supplying the probed
value would make that case pass whether or not the chart delivered anything. A `prerequisites`
block states the chart's own first-class values, the ones a `validateValues` guard refuses to
render without, and is carried by every case including the identity one, because a case that does
not render proves nothing either. Being undroppable is exactly why it may not name a path inside
that same probe root: that is refused here with the file's name on it, and refused again by
`config_testgen.plan`, so the guarantee does not depend on this loader having been the caller.

**How a document is told from its siblings is derived, not declared.** A suite finds its document
by the key the declaration names; a chart rendering that key into several documents needs the
labels as well, and `config-contract.yaml` already carries them per document. Asking the
enrolment to repeat them would let the two disagree, and the gate reading the first would then be
validating an object the suite never selects.

**The vendored contracts are read directly rather than through `config_declaration.bind`.** The
binding exists to refuse validating a document against a contract for some other digest, and it
is right for a gate. It is wrong here: the suite is a function of the contract's *keys* and of
nothing else, so tying generation to the digest interlock would block a regeneration during the
window between a Renovate bump and the contract refresh — and would report the same staleness
`just check-config` already reports, with a worse message.

Line endings are the platform's, deliberately. This repository is developed from a Windows
checkout with `core.autocrlf` on, so the working tree holds CRLF and the index holds LF; a
generator writing `\\n` unconditionally would produce a file that differs from its own committed
copy on every line and a staleness gate that can never pass. Writing through Python's text mode
and comparing through universal newlines makes both ends agnostic.

Usage: python3 .github/scripts/generate-contract-tests.py [CHART] [--check] [--charts DIR]
"""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_contract as cc
import config_testgen as tg
from config_declaration import (
    Declaration,
    DeclarationError,
    Document,
    dig,
    load_declaration,
    reject_unknown,
    union_for,
)
from config_paths import CHARTS_DIR

# The file that enrols a chart, and the keys it may carry at each level. Anything else is a typo
# that would otherwise be ignored in silence — the same rule `config-contract.yaml` is read under,
# and for the same reason.
ENROLMENT = "contract-tests.yaml"
ENROLMENT_KEYS = {"documents"}
ENROLMENT_DOCUMENT_KEYS = {"name", "baseline", "probe", "reason", "prerequisites"}
ENROLMENT_PREREQUISITE_KEYS = {"values", "reason"}

# Where a generated suite goes, and the name that identifies one. The prefix is what lets the
# generator own the removal of a suite whose document is gone: any file matching it under a
# generated chart's `tests/` was written by this script and by nothing else.
SUITES = "tests"
SUITE_PREFIX = "contract_roundtrip_"
SUITE_SUFFIX = "_test.yaml"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------------------------------
# What a chart tells the generator
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """Values every case for one document carries, and why it has to.

    A chart is free to refuse a values combination its image would accept — portfolio fails the
    render when the inline-script hashes are off while the Cloudflare nonce is still on — and a
    probe that walked into such a pair would fail for a reason that has nothing to do with the
    round trip. The chart states the way out of its own guard, because the guard is the chart's
    and no contract knows about it.

    A baseline is a hole in what the generated suite proves, so it carries a `reason` for the
    same cause `config-contract.yaml` demands one for an exemption: an unexplained hole is
    indistinguishable from an oversight.

    Deliberately confined to paths under the document's probe root — the configuration tree by
    default, and whatever higher-precedence layer the enrolment names where the chart merges its
    derived wiring over that tree. Read under the same root the probes are, because the collision
    check that drops a baseline entry compares the two paths as strings: a baseline one layer off
    would never compare equal to a probe, and would quietly supply the value the case exists to
    prove the chart delivered. What a chart needs *outside* that root altogether is a render
    prerequisite, which is `Prerequisites` below and not this.
    """

    values: list[tuple[str, Any]] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class Prerequisites:
    """Chart values every case for one document carries before the chart will render, and why.

    `cloudflare-access-webhook-redirect`, `netcup-offer-bot`, `s3-bucket-perma-link` and
    `tankovault` all refuse their own default render: no target base, no webhook URL, no bucket
    entry, and the guard fails the template before a document exists to assert anything about.
    What unblocks them is the chart's own first-class values, which is a different kind of thing
    from a `Baseline` — that one writes into the configuration document the cases assert against,
    and this one writes into the chart's values.

    Held as flat dotted paths with whatever shape the leaf has, rather than as a nested block, for
    three reasons. A helm-unittest `set` mapping *is* flat dotted paths, so what the enrolment
    states is what the generated file carries and there is no translation between the two to go
    wrong in silence. The refusal that keeps a prerequisite out of the tree the cases probe is
    then a prefix test on one string — the same rule `Baseline` is read under, checkable by eye —
    where a nested block would spell it as a walk over two trees. And the leaves are free to be
    structures because `bucket.entries` is a map of maps keyed by request path: splitting it into
    `bucket.entries.docs/handbook.bucket` would put a `/` inside a dotted path and read worse than
    the map it stands for.

    That is also where `rules-tests/render-values.yaml` stops being the right shape to copy. It is
    nested because `just test-rules` hands it to `helm template -f`, which takes a values file;
    here the consumer is a helm-unittest `set` block, which takes paths. The principle it
    establishes — how to render *this* chart is the chart's own business, stated beside the suite
    that needs it rather than in the harness — is followed exactly.

    A prerequisite carries a mandatory `reason` for the cause a baseline does, and one more: the
    reason is where a chart states that its probe root still wins over anything these values
    derive, which is the half of the guarantee no path comparison can make.
    """

    values: list[tuple[str, Any]] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class Enrolment:
    """What one chart's `contract-tests.yaml` says about one of its documents.

    `probe` is the values path every case for this document writes into, defaulting to the
    operator-facing configuration tree every chart here exposes. It exists because that tree is
    not always the layer that wins: `tankovault.configToml` merges `.Values.config`, then the
    chart-derived wiring, then `services.<name>.config`, so a probe written into the root tree
    for a key the chart derives is overwritten before it reaches the document. Declared rather
    than derived — the document named `control-plane` is configured at
    `services.controlPlane.config`, and there is nothing in the declaration that spells the
    second from the first — and per document rather than per chart, because tankovault's own
    `bootstrap` document has no such layer at all.

    It sits here rather than on `Baseline` because it governs all three fields at once: the
    probes are written under it, the baseline is read under it, and the prerequisites are
    refused from it.
    """

    baseline: Baseline = field(default_factory=Baseline)
    prerequisites: Prerequisites = field(default_factory=Prerequisites)
    probe: str = tg.VALUES_ROOT


def load_enrolment(chart_dir: Path) -> dict[str, Enrolment] | None:
    """Read one chart's enrolment; `None` when the chart is not enrolled."""
    path = chart_dir / ENROLMENT
    if not path.is_file():
        return None

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise DeclarationError(f"{path}: expected a mapping at the top level")
    reject_unknown(path, "", document, ENROLMENT_KEYS)

    entries = document.get("documents") or []
    if not isinstance(entries, list):
        raise DeclarationError(f"{path}: `documents` is not a list")

    enrolments: dict[str, Enrolment] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeclarationError(f"{path}: every entry of `documents` must be a mapping")
        reject_unknown(path, "documents[]", entry, ENROLMENT_DOCUMENT_KEYS)

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise DeclarationError(f"{path}: an entry of `documents` has no `name`")
        if name in enrolments:
            raise DeclarationError(f"{path}: `{name}` is declared twice")

        probe = _load_probe(path, name, entry.get("probe"))
        baseline = Baseline(
            values=_load_baseline(path, name, entry.get("baseline") or {}, probe),
            reason=entry.get("reason"),
        )
        if (baseline.values or probe != tg.VALUES_ROOT) and not baseline.reason:
            raise DeclarationError(
                f"{path}: the entry for `{name}` sets a baseline or a probe path and has no "
                "`reason`; either narrows what every generated case proves, and an unexplained "
                "hole in a gate is indistinguishable from an oversight"
            )

        enrolments[name] = Enrolment(
            baseline=baseline,
            prerequisites=_load_prerequisites(path, name, entry.get("prerequisites"), probe),
            probe=probe,
        )

    return enrolments


def _load_probe(path: Path, name: str, probe: Any) -> str:
    """The values path this document's cases write into, defaulting to the configuration tree."""
    if probe is None:
        return tg.VALUES_ROOT
    if not isinstance(probe, str) or not tg.VALUES_PATH.match(probe):
        raise DeclarationError(
            f"{path}: {name}: `probe` {probe!r} is not a dotted values path; it is used both as "
            "a `set` prefix and as a walk into the chart's values, and neither reads anything "
            "else"
        )
    return probe


def _load_baseline(path: Path, name: str, baseline: Any, probe: str) -> list[tuple[str, Any]]:
    """One baseline, as sorted `set` path / value pairs.

    Flat dotted paths rather than a nested tree, because the generator has to be able to tell
    whether an entry collides with the key a case is probing — and comparing one string is a rule
    a reader can check by eye, where walking two trees is not. Read under the document's own
    probe path for the same cause: a baseline written one layer below the probes would never
    compare equal to one, so the collision it exists to be dropped for would go unnoticed and
    the baseline would quietly supply the value a case is meant to prove the chart delivered.
    """
    if not isinstance(baseline, dict):
        raise DeclarationError(f"{path}: {name}: `baseline` must be a mapping")

    values: list[tuple[str, Any]] = []
    for key, value in sorted(baseline.items()):
        if not isinstance(key, str) or not key.startswith(f"{probe}."):
            raise DeclarationError(
                f"{path}: {name}: the baseline entry {key!r} is not a path under `{probe}`; a "
                "baseline states configuration at the layer the probes are written to, not "
                "chart values"
            )
        if isinstance(value, (dict, list)):
            raise DeclarationError(
                f"{path}: {name}: the baseline entry {key!r} is not a scalar; state each leaf "
                "as its own dotted path so a collision with a probe is visible"
            )
        values.append((key, value))
    return values


def _load_prerequisites(path: Path, name: str, block: Any, probe: str) -> Prerequisites:
    """One document's render prerequisites, as sorted `set` path / value pairs and their reason.

    Every refusal below is loud rather than lenient, and the first of them is the one the whole
    field turns on: a prerequisite inside the tree the cases probe is undroppable *and* able to
    supply what they assert, which is the one combination that lets a generated suite pass
    without having proven anything. That tree is the document's `probe` root rather than the
    default configuration tree, which is why the root is read before this is called: on a chart
    probing `services.api.config`, a prerequisite under `config` is harmless and one under
    `services.api.config` is not, and a rule spelled against the default would have had it
    exactly backwards. `config_testgen.prerequisite_conflict` owns the rule so the message is
    the same one the model raises; what this adds is the name of the file to fix.
    """
    if block is None:
        return Prerequisites()
    if not isinstance(block, dict):
        raise DeclarationError(f"{path}: {name}: `prerequisites` must be a mapping")
    reject_unknown(path, f"documents[{name}].prerequisites", block, ENROLMENT_PREREQUISITE_KEYS)

    declared = block.get("values")
    if declared is not None and not isinstance(declared, dict):
        raise DeclarationError(
            f"{path}: {name}: `prerequisites.values` must be a mapping of chart values path to "
            "the value to set"
        )

    values: list[tuple[str, Any]] = []
    for key, value in sorted((declared or {}).items()):
        if not isinstance(key, str):
            raise DeclarationError(
                f"{path}: {name}: the render prerequisite {key!r} is not a values path"
            )
        conflict = tg.prerequisite_conflict(key, probe)
        if conflict is not None:
            raise DeclarationError(f"{path}: {name}: the render prerequisite {key!r} {conflict}")
        values.append((key, value))

    # Sorted, so a path and anything nested under it are adjacent in spirit but not always in
    # fact — `a`, `a.b` and `ab` sort in that order, and `a` precedes both of its own extensions
    # without being next to the later one. Compared pairwise for that reason; the count here is a
    # handful and the alternative is a rule that holds only for the pairs that happened to be
    # adjacent. Overlapping entries are refused because a `set` mapping has no order, so which of
    # the two survives is stated nowhere.
    for index, (outer, _) in enumerate(values):
        for inner, _ in values[index + 1 :]:
            if inner.startswith(f"{outer}."):
                raise DeclarationError(
                    f"{path}: {name}: the render prerequisites {outer!r} and {inner!r} overlap; "
                    "a `set` mapping has no order, so which of the two survives is stated "
                    "nowhere — write the one subtree that carries both"
                )

    reason = block.get("reason")
    if not values:
        raise DeclarationError(
            f"{path}: {name}: `prerequisites` declares no `values`; a block that sets nothing is "
            "a field somebody meant to fill in"
        )
    if not reason:
        raise DeclarationError(
            f"{path}: {name}: the render prerequisites have no `reason`; they are carried by "
            "every generated case, and a value nobody explained is one nobody can tell from a "
            "workaround somebody stopped needing"
        )
    return Prerequisites(values=values, reason=reason)


# --------------------------------------------------------------------------------------------
# Building one chart's suites
# --------------------------------------------------------------------------------------------


def probe_tree(chart_dir: Path, values: dict, name: str, probe: str) -> None:
    """Refuse a document whose probe path is not a configuration tree in the chart's values.

    Every contract key is reachable as `<probe>.<path>` only because each of these charts
    exposes an operator-supplied tree that some layer of its render merges. A chart without one
    can still be probed — through whichever camelCase value happens to spell each key — but not
    by a generator that knows nothing about it, and guessing would produce a suite that silently
    asserts nothing. The same check catches the likelier mistake by far: an enrolment naming a
    `probe` path this chart does not have, which would otherwise generate a whole suite of cases
    setting values nothing reads.
    """
    if not isinstance(dig(values, probe), dict):
        raise DeclarationError(
            f"{chart_dir / 'values.yaml'}: {name}: has no `{probe}` mapping, so a contract key "
            "cannot be written into this chart's values by its contract path alone"
        )


def discriminator_for(
    declaration: Declaration, document: Document
) -> tuple[tuple[str, str], ...]:
    """The labels a document's selector needs beyond its key, empty where the key suffices.

    A generated suite finds its document by the key the declaration names, which is what
    `check-config.py` reads it from. That stops identifying anything the moment a chart renders
    the same key into several documents — tankovault's nine services each write their own
    `config.toml` — so where a key is shared the declaration's own `source.selector` is added to
    it. Derived here rather than declared in the enrolment because the declaration already
    states it: a second copy could disagree with the first, and the gate reading the first would
    then be validating an object the suite never selects.
    """
    shared = [
        other
        for other in declaration.documents
        if other.name != document.name and other.source.key == document.source.key
    ]
    if not shared:
        return ()

    if not document.source.selector:
        raise DeclarationError(
            f"{declaration.path}: `{document.name}` shares the key `{document.source.key}` "
            f"with {', '.join(sorted(other.name for other in shared))} and carries no "
            "`source.selector`, so nothing in this declaration tells the documents apart"
        )

    twins = sorted(
        other.name for other in shared if other.source.selector == document.source.selector
    )
    if twins:
        raise DeclarationError(
            f"{declaration.path}: `{document.name}` shares both the key `{document.source.key}` "
            f"and its `source.selector` with {', '.join(twins)}, so nothing in this "
            "declaration tells the documents apart"
        )

    return tuple(sorted(document.source.selector.items()))


def suite_path(chart_dir: Path, document: Document) -> Path:
    return chart_dir / SUITES / f"{SUITE_PREFIX}{document.name}{SUITE_SUFFIX}"


def repository_path(chart_dir: Path, *parts: str) -> str:
    """One path as it is spelled from the repository root, whatever `--charts` was given as.

    The generated header names the files it was generated from, and a path built from the
    filesystem would be absolute whenever the caller passed an absolute `--charts` — which would
    put the checkout directory into a committed file and make the staleness gate fail on any
    machine but the one that last ran the generator.
    """
    return "/".join((chart_dir.parent.name, chart_dir.name, *parts))


def build(chart_dir: Path, declaration: Declaration, enrolments: dict[str, Enrolment]) -> dict:
    """Every suite one chart owns, as a path to text mapping."""
    declared = {document.name for document in declaration.documents}
    unknown = sorted(set(enrolments) - declared)
    if unknown:
        raise DeclarationError(
            f"{chart_dir / ENROLMENT}: names document(s) {', '.join(unknown)}, which "
            f"{declaration.path} does not declare"
        )

    values = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8")) or {}

    suites: dict[Path, str] = {}
    for document in declaration.documents:
        enrolment = enrolments.get(document.name, Enrolment())
        probe_tree(chart_dir, values, document.name, enrolment.probe)

        union = union_for(chart_dir, document)
        target = tg.Target(
            chart=chart_dir.name,
            name=document.name,
            kind=document.source.kind,
            selector=document.source.selector,
            key=document.source.key,
            declaration=repository_path(chart_dir, declaration.path.name),
            contracts=tuple(
                repository_path(chart_dir, reference.contract) for reference in document.images
            ),
            discriminator=discriminator_for(declaration, document),
            root=enrolment.probe,
        )
        plan = tg.plan(
            union.keys.values(),
            enrolment.baseline.values,
            enrolment.prerequisites.values,
            enrolment.probe,
        )
        suites[suite_path(chart_dir, document)] = tg.render_suite(
            target,
            plan,
            enrolment.baseline.values,
            enrolment.baseline.reason,
            enrolment.prerequisites.values,
            enrolment.prerequisites.reason,
        )
    return suites


def orphans(chart_dir: Path, wanted: dict) -> list[Path]:
    """Generated suites left behind by a document — or a whole chart — that no longer declares one.

    Owned rather than reported: a suite for a removed document keeps asserting keys nothing reads
    any more, and leaving it in place while claiming the tree is generated would be the drift this
    whole mechanism exists to remove. Swept for on every chart rather than only on the enrolled
    ones, because un-enrolling a chart is the case that leaves the most behind and the case an
    enrolled-only sweep cannot see.
    """
    directory = chart_dir / SUITES
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.glob(f"{SUITE_PREFIX}*{SUITE_SUFFIX}") if path not in wanted
    )


# --------------------------------------------------------------------------------------------
# Writing, and reporting what would be written
# --------------------------------------------------------------------------------------------


def sync(suites: dict, stale: list[Path]) -> bool:
    """Write what changed and remove what is orphaned; `True` when the tree moved."""
    moved = False
    for path, text in sorted(suites.items()):
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"==> {path.as_posix()}: written")
        moved = True

    for path in stale:
        path.unlink()
        print(f"==> {path.as_posix()}: removed, its document is no longer declared")
        moved = True

    return moved


def check(suites: dict, stale: list[Path]) -> bool:
    """Report every suite that has drifted; `True` when any has."""
    drifted = False
    for path, text in sorted(suites.items()):
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current == text:
            continue
        drifted = True
        print(f"{path.as_posix()}: {'drifted' if current else 'missing'}", file=sys.stderr)
        for line in difflib.unified_diff(
            current.splitlines(),
            text.splitlines(),
            fromfile="committed",
            tofile="generated",
            lineterm="",
            n=1,
        ):
            print(f"  {line}", file=sys.stderr)

    for path in stale:
        drifted = True
        print(
            f"{path.as_posix()}: is a generated suite for a document no longer declared",
            file=sys.stderr,
        )

    return drifted


# --------------------------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------------------------


def collect(charts: Path, only: str) -> tuple[dict, list[Path]]:
    """Every enrolled chart's suites, and every generated suite that no longer has a document."""
    suites: dict[Path, str] = {}
    stale: list[Path] = []
    enrolled = False

    for chart_dir in sorted(path for path in charts.iterdir() if path.is_dir()):
        if only and chart_dir.name != only:
            continue
        if not (chart_dir / "Chart.yaml").is_file():
            continue

        enrolments = load_enrolment(chart_dir)
        if enrolments is None:
            print(f"==> {chart_dir.name}: not enrolled, skipping")
            stale.extend(orphans(chart_dir, {}))
            continue

        declaration = load_declaration(chart_dir)
        if declaration is None or not declaration.documents:
            raise DeclarationError(
                f"{chart_dir / ENROLMENT}: enrols this chart, but it declares no configuration "
                "document to generate a suite for"
            )

        enrolled = True
        wanted = build(chart_dir, declaration, enrolments)
        suites.update(wanted)
        stale.extend(orphans(chart_dir, wanted))

    if only and not enrolled:
        print(f"==> {only}: not enrolled, nothing to generate")

    return suites, stale


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("chart", nargs="?", default="", help="one chart, or every enrolled chart")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drifted suites and exit non-zero instead of writing them",
    )
    parser.add_argument(
        "--charts",
        default=str(CHARTS_DIR),
        type=Path,
        help=f"charts directory (default: {CHARTS_DIR})",
    )
    args = parser.parse_args(argv)

    if not args.charts.is_dir():
        fail(f"{args.charts} is not a directory")
    if args.chart and not (args.charts / args.chart / "Chart.yaml").is_file():
        fail(f"{args.charts / args.chart} is not a chart")

    try:
        suites, stale = collect(args.charts, args.chart)
    except (cc.ContractError, DeclarationError, tg.TestGenError) as failure:
        fail(str(failure))
        return 1

    if args.check:
        if check(suites, stale):
            print("run `just contract-tests` to regenerate them", file=sys.stderr)
            return 1
        print(f"==> {len(suites)} generated suite(s) are in step with their contracts")
        return 0

    if not sync(suites, stale):
        print(f"==> {len(suites)} generated suite(s) were already in step with their contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
