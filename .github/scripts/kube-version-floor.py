#!/usr/bin/env python3
"""Keep every chart's `kubeVersion` pinned to the floor `validate-manifests` actually proves.

No chart declares `kubeVersion`, so `helm install` never rejects a cluster the chart cannot run
on — it just fails at apply time with a missing-API error, on whichever object first needed a
newer surface. Every piece of knowledge needed to prevent that already exists: `charts/common`
ships `common.capabilities.*` that branch on the cluster's API surface, and
`.github/workflows/ci.yaml`'s `validate-manifests` job proves every chart against a matrix of
Kubernetes releases. None of it reaches the operator.

The floor is read from that matrix rather than from a second constant, so the two cannot
disagree: whatever the lowest entry in `jobs.validate-manifests.strategy.matrix.kubernetes` is,
that is what gets written. A matrix entry removed or added moves every chart's `kubeVersion` the
next time this runs, with nothing else to update.

`-0` is appended to the floor so the constraint (parsed by Helm with Masterminds/semver) still
matches a cluster whose version string carries a pre-release-shaped suffix, such as a managed
provider's `v1.28.3-eks-...` — a bare `>=1.28.0` would otherwise reject it, because a semver
range excludes pre-release versions unless the range itself names one.

Rewrites are byte-level for the reason `kube-schema-refs.py` gives: the files are checked out
with native line endings, and decoding to text and writing back would normalise them
repository-wide on a Windows shell.

Usage: .github/scripts/kube-version-floor.py [--check] [--charts DIR] [--workflow FILE]
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

# A release as it appears in the matrix, so a malformed entry is rejected before it is treated as
# the floor.
VERSION = re.compile(r"\d+\.\d+\.\d+")

# The top-level `version:` line, so the inserted `kubeVersion:` lands next to the field it is
# most closely related to rather than at the end of the file. Anchored at column 0: every other
# `version:` in a Chart.yaml (the `dependencies` entry) is indented, so this cannot match it.
VERSION_LINE = re.compile(rb"^version:[^\r\n]*\r?\n", re.MULTILINE)

# An existing `kubeVersion:` line, wherever helm-schema or a previous run of this script left it.
KUBE_VERSION_LINE = re.compile(rb"^kubeVersion:[^\r\n]*\r?\n", re.MULTILINE)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def floor_from_matrix(workflow: Path) -> str:
    """The lowest Kubernetes release `validate-manifests` proves, read from its own matrix."""
    document = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}

    try:
        matrix = document["jobs"]["validate-manifests"]["strategy"]["matrix"]["kubernetes"]
    except (KeyError, TypeError):
        fail(f"{workflow}: jobs.validate-manifests.strategy.matrix.kubernetes not found")

    if not matrix:
        fail(f"{workflow}: jobs.validate-manifests.strategy.matrix.kubernetes is empty")

    for entry in matrix:
        if not VERSION.fullmatch(str(entry)):
            fail(f"{workflow}: matrix entry {entry!r} is not MAJOR.MINOR.PATCH")

    return min(matrix, key=lambda entry: tuple(int(part) for part in entry.split(".")))


def charts_with_version(charts: Path) -> list[Path]:
    return sorted(
        path
        for path in charts.glob("*/Chart.yaml")
        if VERSION_LINE.search(path.read_bytes())
    )


def current_kube_version(content: bytes) -> str | None:
    match = KUBE_VERSION_LINE.search(content)
    return match.group(0).decode().strip() if match else None


def check(charts: Path, floor: str) -> int:
    wanted = f'kubeVersion: ">={floor}-0"'

    drifted = []
    for path in charts_with_version(charts):
        found = current_kube_version(path.read_bytes())
        if found != wanted:
            drifted.append((path, found))

    if drifted:
        for path, found in drifted:
            state = found if found is not None else "not declared"
            print(f"{path.as_posix()}: {state}, wanted `{wanted}`", file=sys.stderr)
        print("run `just sync-kube-floor` to repin them", file=sys.stderr)
        return 1

    print(f"==> every chart's kubeVersion is pinned to the {floor} floor")
    return 0


def sync(charts: Path, floor: str) -> int:
    wanted_line = f'kubeVersion: ">={floor}-0"'

    repinned = False
    for path in charts_with_version(charts):
        content = path.read_bytes()
        found = current_kube_version(content)
        if found == wanted_line:
            continue

        pattern = VERSION_LINE if found is None else KUBE_VERSION_LINE
        match = pattern.search(content)

        # Native line ending of the file's matched line, so the written line matches its
        # neighbours rather than the platform this script happens to run on.
        eol = b"\r\n" if match.group(0).endswith(b"\r\n") else b"\n"
        replacement = wanted_line.encode() + eol
        if found is None:
            replacement = match.group(0) + replacement

        content = content[: match.start()] + replacement + content[match.end() :]

        path.write_bytes(content)
        print(f"==> {path.as_posix()}: kubeVersion set to `>={floor}-0`")
        repinned = True

    if not repinned:
        print(f"==> every chart's kubeVersion is already pinned to the {floor} floor")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drifted kubeVersion fields and exit non-zero instead of rewriting them",
    )
    parser.add_argument(
        "--charts", default="charts", type=Path, help="charts directory (default: charts)"
    )
    parser.add_argument(
        "--workflow",
        default=".github/workflows/ci.yaml",
        type=Path,
        help="workflow the validate-manifests matrix is read from",
    )
    args = parser.parse_args(argv)

    if not args.charts.is_dir():
        fail(f"{args.charts} is not a directory")
    if not args.workflow.is_file():
        fail(f"{args.workflow} is not a file")
    if not charts_with_version(args.charts):
        fail(f"no Chart.yaml with a top-level version found under {args.charts}")

    floor = floor_from_matrix(args.workflow)
    return check(args.charts, floor) if args.check else sync(args.charts, floor)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
