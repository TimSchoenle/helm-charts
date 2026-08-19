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
generated case has to carry before it will render at all.

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

import config_contract as cc  # noqa: E402
import config_testgen as tg  # noqa: E402
from config_declaration import (  # noqa: E402
    Declaration,
    DeclarationError,
    Document,
    load_declaration,
    reject_unknown,
)

# The file that enrols a chart, and the keys it may carry at each level. Anything else is a typo
# that would otherwise be ignored in silence — the same rule `config-contract.yaml` is read under,
# and for the same reason.
ENROLMENT = "contract-tests.yaml"
ENROLMENT_KEYS = {"documents"}
ENROLMENT_DOCUMENT_KEYS = {"name", "baseline", "reason"}

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

    Deliberately confined to paths under the configuration tree, and that is what the rest of
    this repository's charts run into: `cloudflare-access-webhook-redirect`, `netcup-offer-bot`,
    `s3-bucket-perma-link` and `tankovault` all refuse to render on their default values, so
    enrolling them needs chart values — a target base, a webhook URL, a bucket entry — which is a
    render prerequisite rather than a configuration baseline. The two are different things and
    the second one wants its own field, shaped like `rules-tests/render-values.yaml`, designed
    against a chart that is actually being enrolled rather than in advance of one.
    """

    values: list[tuple[str, Any]] = field(default_factory=list)
    reason: str | None = None


def load_enrolment(chart_dir: Path) -> dict[str, Baseline] | None:
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

    baselines: dict[str, Baseline] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeclarationError(f"{path}: every entry of `documents` must be a mapping")
        reject_unknown(path, "documents[]", entry, ENROLMENT_DOCUMENT_KEYS)

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise DeclarationError(f"{path}: an entry of `documents` has no `name`")
        if name in baselines:
            raise DeclarationError(f"{path}: `{name}` is declared twice")

        baselines[name] = Baseline(
            values=_load_baseline(path, name, entry.get("baseline") or {}),
            reason=entry.get("reason"),
        )
        if baselines[name].values and not baselines[name].reason:
            raise DeclarationError(
                f"{path}: the baseline for `{name}` has no `reason`; it narrows what every "
                "generated case proves, and an unexplained hole in a gate is indistinguishable "
                "from an oversight"
            )

    return baselines


def _load_baseline(path: Path, name: str, baseline: Any) -> list[tuple[str, Any]]:
    """One baseline, as sorted `set` path / value pairs.

    Flat dotted paths rather than a nested tree, because the generator has to be able to tell
    whether an entry collides with the key a case is probing — and comparing one string is a rule
    a reader can check by eye, where walking two trees is not.
    """
    if not isinstance(baseline, dict):
        raise DeclarationError(f"{path}: {name}: `baseline` must be a mapping")

    values: list[tuple[str, Any]] = []
    for key, value in sorted(baseline.items()):
        if not isinstance(key, str) or not key.startswith(f"{tg.VALUES_ROOT}."):
            raise DeclarationError(
                f"{path}: {name}: the baseline entry {key!r} is not a path under "
                f"`{tg.VALUES_ROOT}`; a baseline states configuration, not chart values"
            )
        if isinstance(value, (dict, list)):
            raise DeclarationError(
                f"{path}: {name}: the baseline entry {key!r} is not a scalar; state each leaf "
                "as its own dotted path so a collision with a probe is visible"
            )
        values.append((key, value))
    return values


# --------------------------------------------------------------------------------------------
# Building one chart's suites
# --------------------------------------------------------------------------------------------


def raw_config_tree(chart_dir: Path) -> None:
    """Refuse a chart whose values expose no raw configuration tree.

    Every contract key is reachable as `config.<path>` only because each of these charts merges
    an operator-supplied tree over whatever it derives from its own first-class values. A chart
    without one can still be probed — through whichever camelCase value happens to spell each
    key — but not by a generator that knows nothing about it, and guessing would produce a suite
    that silently asserts nothing.
    """
    path = chart_dir / "values.yaml"
    values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(values.get(tg.VALUES_ROOT), dict):
        raise DeclarationError(
            f"{path}: has no `{tg.VALUES_ROOT}` mapping, so a contract key cannot be written "
            "into this chart's values by its contract path alone"
        )


def union_for(chart_dir: Path, document: Document) -> cc.Union:
    """The contracts of every image that reads one document, merged."""
    contracts = []
    for reference in document.images:
        vendored = cc.load_vendored(chart_dir / reference.contract)
        contracts.append((f"{chart_dir.name}/{reference.contract}", vendored.contract))
    return cc.union_contracts(contracts)


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


def build(chart_dir: Path, declaration: Declaration, baselines: dict[str, Baseline]) -> dict:
    """Every suite one chart owns, as a path to text mapping."""
    declared = {document.name for document in declaration.documents}
    unknown = sorted(set(baselines) - declared)
    if unknown:
        raise DeclarationError(
            f"{chart_dir / ENROLMENT}: names document(s) {', '.join(unknown)}, which "
            f"{declaration.path} does not declare"
        )

    raw_config_tree(chart_dir)

    suites: dict[Path, str] = {}
    for document in declaration.documents:
        baseline = baselines.get(document.name, Baseline())
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
        )
        plan = tg.plan(union.keys.values(), baseline.values)
        suites[suite_path(chart_dir, document)] = tg.render_suite(
            target, plan, baseline.values, baseline.reason
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

        baselines = load_enrolment(chart_dir)
        if baselines is None:
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
        wanted = build(chart_dir, declaration, baselines)
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
        "--charts", default="charts", type=Path, help="charts directory (default: charts)"
    )
    args = parser.parse_args(argv[1:])

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
    raise SystemExit(main(sys.argv))
