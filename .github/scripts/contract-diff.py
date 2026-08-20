#!/usr/bin/env python3
"""Report how a chart's configuration contract changed against another revision, and what it costs.

`just contracts` runs inside the Renovate pull request that repins the digest, so the refreshed
contract and the bump arrive together — which is the design's whole point, and also why the
reviewer is looking at a large reordered JSON diff instead of a sentence. This is the sentence:
what the image gained, what it dropped, what moved out from under the chart, and the smallest
chart version bump that set of findings justifies.

The loop, the git plumbing and the output live here; every rule about what a difference means and
what it costs lives in `config_diff.py`, so each rule is testable by calling it with two
dictionaries. The split is the one `check-config.py` already draws.

**Offline, like every gate that reads a contract.** The old bytes come from `git show <ref>:<path>`
and never from a registry, so this answers the same way on a laptop with no network and on a
re-run of an old commit, and it cannot disagree with the committed file about what the previous
revision said.

**The working tree is one of the two sides, deliberately.** Not the index and not `HEAD`: the
refresh writes files, and a contract for a newly declared document is untracked until someone adds
it — the case `just check-contracts` already goes out of its way to catch. Comparing `HEAD` would
report nothing for exactly the change most worth reporting.

**Exit status separates "there are differences" from "this could not be answered."** Differences
are the normal outcome — every Renovate bump has them — so by default they exit 0 and only a
failure to answer exits 1. A caller that wants the distinction as a status rather than as output
passes `--exit-code` and gets three: 0 identical, 1 error, 2 differences. Three rather than
`git diff --exit-code`'s two, because git's 1 means both "differences" and "something broke" and a
caller cannot tell a changed contract from an unreadable one. The JSON carries the impact anyway,
so an automated consumer never needs the status at all.

`--json` is shaped for the pull request comment this is meant to feed: a top-level impact for the
comment's headline, per-chart drivers for its first paragraph, and the full per-contract change
list for the collapsed detail. The workflow step that posts it is deliberately not built here —
the reporting has to be trustworthy before anything comments with it.

Usage: python3 .github/scripts/contract-diff.py
       python3 .github/scripts/contract-diff.py tankovault --ref HEAD~1
       python3 .github/scripts/contract-diff.py --json --exit-code
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_diff as cd
from config_paths import CHARTS_DIR
from config_report import Report, error

DEFAULT_REF = "origin/main"

# The JSON's own version, on the same reasoning as `terrace_contract`: a consumer that does not
# recognise the shape should refuse it by name rather than misread it.
JSON_VERSION = 1


class GitError(Exception):
    """A revision that does not exist, or a repository this cannot be run from."""


# --------------------------------------------------------------------------------------------
# Reading the other revision
# --------------------------------------------------------------------------------------------


class Revision:
    """One git revision, as a source of file bytes.

    Every read is a `git show`, which is the only reason this needs a subprocess at all — and the
    reason there is no fallback to the network. A revision that cannot be resolved is refused
    once, up front, rather than turning every contract into a separate confusing failure.
    """

    def __init__(self, ref: str):
        self.ref = ref
        self.root = Path(self._git("rev-parse", "--show-toplevel").strip())
        resolved = self._try("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if resolved is None:
            raise GitError(
                f"{ref}: no such revision. Pass --ref, or run `git fetch origin` if the default "
                f"{DEFAULT_REF} is simply not present in this clone."
            )
        self.commit = resolved.strip()

    def spec(self, path: Path) -> str:
        """A repository-relative, forward-slashed path — the only spelling `git show` accepts."""
        return path.resolve().relative_to(self.root).as_posix()

    def read(self, path: Path) -> str | None:
        """The file's contents at this revision, or `None` if it did not exist there."""
        return self._try("show", f"{self.commit}:{self.spec(path)}")

    def contracts(self, charts: Path) -> set[str]:
        """Every vendored contract path that existed at this revision, repository-relative."""
        listed = self._git("ls-tree", "-r", "--name-only", self.commit, "--", self.spec(charts))
        return {
            line
            for line in listed.splitlines()
            if line.endswith(".json") and "/contracts/" in line
        }

    def _git(self, *args: str) -> str:
        result = self._run(args)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def _try(self, *args: str) -> str | None:
        result = self._run(args)
        return result.stdout if result.returncode == 0 else None

    @staticmethod
    def _run(args) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", check=False
        )


# --------------------------------------------------------------------------------------------
# Walking the charts
# --------------------------------------------------------------------------------------------


def collect(
    charts: Path, revision: Revision, only: str | None, report: Report
) -> list[cd.ChartDiff]:
    """Every chart carrying a contract at either revision, and the diff of each of its contracts.

    Contracts are found by walking the files rather than by reading `config-contract.yaml`. The
    unit being compared is the vendored document, and a document whose declaration was deleted is
    one of the cases most worth reporting — reading the declaration to find the files would hide
    exactly that.
    """
    old_paths = revision.contracts(charts)
    new_paths = {
        revision.spec(path) for path in sorted(charts.glob("*/contracts/*.json")) if path.is_file()
    }

    charted: dict[str, list[str]] = {}
    for spec in sorted(old_paths | new_paths):
        chart = Path(spec).parent.parent.name
        if only is not None and chart != only:
            continue
        charted.setdefault(chart, []).append(spec)

    diffs: list[cd.ChartDiff] = []
    for chart, specs in sorted(charted.items()):
        diff = cd.ChartDiff(
            chart,
            old_version=chart_version(revision.read(charts / chart / "Chart.yaml")),
            new_version=chart_version(_read(charts / chart / "Chart.yaml")),
        )
        for spec in specs:
            contract = compare(chart, spec, revision, report)
            if contract is not None:
                diff.contracts.append(contract)
        diffs.append(diff)
    return diffs


def compare(chart: str, spec: str, revision: Revision, report: Report) -> cd.ContractDiff | None:
    """One contract at both revisions, or `None` when either side cannot be parsed as JSON.

    A document that is not readable JSON is reported and skipped rather than raised, matching what
    every other gate in this repository does: one broken file must not hide the state of the rest.
    Nothing beyond "it is an object with a `contract` object in it" is asserted, because reporting
    an envelope this repository cannot validate is this tool's job rather than its failure mode —
    see `config_diff`'s module docstring.
    """
    old = _parse(revision.read(revision.root / spec), f"{spec} at {revision.ref}", report)
    new = _parse(_read(revision.root / spec), spec, report)
    if old is None and new is None:
        return None
    return cd.diff_contract(chart, Path(spec).stem, spec, old, new)


def _parse(text: str | None, origin: str, report: Report) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError as failure:
        report.add(origin, error(f"is not valid JSON: {failure}"))
        return None
    if not isinstance(document, dict) or not isinstance(document.get("contract"), dict):
        report.add(origin, error("is not a vendored contract: no `contract` object"))
        return None
    return document


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def chart_version(text: str | None) -> str | None:
    """The `version` out of a `Chart.yaml`, or `None` when there is not one to read."""
    if text is None:
        return None
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    version = parsed.get("version") if isinstance(parsed, dict) else None
    return version if isinstance(version, str) else None


# --------------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------------

# `docs` and `note` are prose. They are real changes and they are shown, but one line per key
# would bury the findings a reviewer is here for, so they collapse into a single line per contract.
PROSE_FIELDS = {"docs", "note"}


def render(diffs: list[cd.ChartDiff], ref: str, stream) -> None:
    """The human report: one block per chart, and the suggestion with its reasons under it."""
    changed = [diff for diff in diffs if diff.impact != cd.NONE]
    if not changed:
        print(f"==> no vendored contract differs from {ref}", file=stream)
        return

    for diff in changed:
        print(f"==> {diff.chart}", file=stream)
        for contract in diff.contracts:
            if contract.status == cd.STATUS_UNCHANGED:
                continue
            print(f"  {contract.path}  ({contract.status})", file=stream)
            prose = []
            # Severity order, stable within a level. The reviewer's first question is whether
            # anything here is major, and a document-order listing buries the answer under the
            # digest line that every single refresh produces.
            for change in sorted(contract.changes, key=lambda c: -cd.RANK[c.severity]):
                if change.field in PROSE_FIELDS and change.severity == cd.PATCH:
                    prose.append(change.subject)
                    continue
                print(f"    {change.severity:<5}  {change.area:<8}  {change.message}", file=stream)
            if prose:
                print(
                    f"    {cd.PATCH:<5}  {'docs':<8}  "
                    f"{len(prose)} documentation-only change(s): {', '.join(sorted(set(prose)))}",
                    file=stream,
                )

        print(f"  impact: {diff.impact}", file=stream)
        # Named rather than repeated in full: every driver has already been printed above, and the
        # question this block answers is which of those lines set the impact.
        for change in diff.drivers:
            print(f"    because  {change.area} {change.kind}  {change.subject}", file=stream)
        print(f"  {_version_line(diff, ref)}", file=stream)
        print(file=stream)

    print(
        f"==> {len(changed)} of {len(diffs)} chart(s) changed against {ref}; "
        f"suggested impact {cd.worst(diff.impact for diff in changed)}",
        file=stream,
    )


def _version_line(diff: cd.ChartDiff, ref: str) -> str:
    """What the chart's version is, and whether the bump already in the branch is large enough."""
    suggested = cd.suggest_version(diff.new_version, diff.impact)
    where = f"version: {diff.old_version} at {ref}, {diff.new_version} in the working tree"
    if suggested is None:
        return f"{where}; suggested impact is {diff.impact}"
    if diff.satisfied is None:
        return f"{where}; a {diff.impact} change suggests {suggested}"
    if diff.satisfied:
        return f"{where}; the bump in this branch already covers a {diff.impact} change"
    return (
        f"{where}; a {diff.impact} change suggests {suggested}, which this branch does not carry"
    )


def as_json(diffs: list[cd.ChartDiff], ref: str, commit: str, report: Report) -> dict[str, Any]:
    """The automation surface: everything the report says, with nothing to parse out of prose."""
    return {
        "tool": "contract-diff",
        "version": JSON_VERSION,
        "ref": ref,
        "commit": commit,
        "changed": any(diff.impact != cd.NONE for diff in diffs),
        "impact": cd.worst(diff.impact for diff in diffs),
        "charts": [diff.as_json() for diff in diffs],
        "problems": [
            {"where": where, "level": finding.level, "message": finding.message}
            for where, finding in report.findings
        ],
    }


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Report how vendored configuration contracts changed against another revision"
    )
    parser.add_argument("chart", nargs="?", help="one chart; omit for every chart with contracts")
    parser.add_argument(
        "--ref", default=DEFAULT_REF, help=f"revision to compare against (default {DEFAULT_REF})"
    )
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit 2 when contracts differ, so a caller can tell that from an error (1)",
    )
    args = parser.parse_args(argv)

    report = Report()
    try:
        revision = Revision(args.ref)
        charts = Path(args.charts)
        if not charts.is_dir():
            raise GitError(f"{charts}: no such directory")
        diffs = collect(charts, revision, args.chart, report)
    except GitError as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    if args.chart is not None and not diffs:
        print(
            f"error: {args.chart} carries no vendored contract, at {args.ref} or in the working "
            f"tree",
            file=sys.stderr,
        )
        return 1

    if args.json:
        json.dump(as_json(diffs, args.ref, revision.commit, report), sys.stdout, indent=2)
        print()
    else:
        render(diffs, args.ref, sys.stdout)
        report.print(sys.stdout, sys.stderr)

    if report.errors:
        return 1
    if args.exit_code and any(diff.impact != cd.NONE for diff in diffs):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
