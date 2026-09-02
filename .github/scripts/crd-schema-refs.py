#!/usr/bin/env python3
"""Keep every chart's CRD `$ref` pinned to one commit of the community CRD catalog.

Values shaped by a *custom* resource are typed the same way Kubernetes-shaped ones are, by URL in
an `@schema` block — but the definitions come from the community catalog rather than from the
Kubernetes API surface, because a CRD is not part of it:

    # @schema
    # type: array
    # items:
    #   $ref: https://raw.githubusercontent.com/datreeio/CRDs-catalog/<commit>/gateway.networking.k8s.io/httproute_v1.json#/properties/spec/properties/rules/items
    # @schema

This is the derivation of `<commit>`, driven by `crd_catalog_ref` in the justfile — the same
variable `kubeconform` already resolves its CRD schemas at, so the schema a value is validated
against and the schema the rendered object is validated against cannot come from two different
states of the catalog.

Why a commit and not a branch, which is what `kubeconform` used before any chart referenced it:
the catalog is a third party's branch. An unpinned reference lets a push there change what CI
accepts and what an operator's values are checked against, with nothing in this repository
recording that it moved. That is a strictly worse position than the Kubernetes references are in,
where the pinned segment is a release number that never changes meaning.

So a bump here is not the same kind of act as a `kube_version` bump. `v1.34.0` names one immutable
document; a catalog commit names whatever the catalog said that day, and the schemas it carries are
generated from upstream CRDs on the catalog's own schedule and channel. Read its log before moving
the pin.

Rewrites are byte-level for the reason `kube-schema-refs.py` gives: the files are checked out with
native line endings, and decoding to text and writing back would normalise them repository-wide on
a Windows shell. Only the pinned segment of a matched URL is ever replaced.

Usage: .github/scripts/crd-schema-refs.py REF [--check] [--charts DIR]
"""

import argparse
import re
import sys
from pathlib import Path

# The pinned segment of a CRD `$ref`, with the catalog commit as the middle group.
#
# `main` is matched as well as a commit, so a reference written by hand against the branch is
# reported as drift rather than passing unnoticed — an unpinned reference is the defect this
# script exists to prevent, not a shape it should tolerate.
REF = re.compile(rb"(datreeio/CRDs-catalog/)([0-9a-f]{40}|main)(/)")

# A catalog commit as it appears in a reference. Full SHA-1 only: an abbreviated one resolves on
# raw.githubusercontent.com today and stops resolving the moment the prefix becomes ambiguous, so
# it is a reference that rots without anybody touching it.
CATALOG_REF = re.compile(r"[0-9a-f]{40}\Z")

# Where references live, relative to the charts directory. Identical to the Kubernetes script's
# list and for the same reason: `values.schema.json` is generated, but rewriting it here keeps the
# tree consistent without the generator installed and makes a following `just docs` a no-op.
SOURCES = ("*/values.yaml", "*/values.schema.json")

# The extracted dependency trees. Gitignored build output that `just deps` rebuilds, rewritten so
# the repin is order-independent rather than because these copies are what makes the repository
# correct. Never reported as drifted.
BUILD_OUTPUT = ("*/charts/*/values.yaml", "*/charts/*/values.schema.json")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def collect(charts: Path, patterns: tuple[str, ...]) -> list[Path]:
    return sorted({match for pattern in patterns for match in charts.glob(pattern)})


def stale_refs(content: bytes, wanted: bytes) -> dict[str, int]:
    """How many references name each catalog commit other than the wanted one."""
    counts: dict[str, int] = {}
    for match in REF.finditer(content):
        found = match.group(2)
        if found != wanted:
            key = found.decode()
            counts[key] = counts.get(key, 0) + 1
    return counts


def report(path: Path, counts: dict[str, int], wanted: str) -> None:
    for found, count in sorted(counts.items()):
        print(
            f"{path.as_posix()}: {count} reference(s) pinned to {found}, wanted {wanted}",
            file=sys.stderr,
        )


def check(charts: Path, ref: str) -> int:
    wanted = ref.encode()

    drifted = False
    for path in collect(charts, SOURCES):
        counts = stale_refs(path.read_bytes(), wanted)
        if counts:
            drifted = True
            report(path, counts, ref)

    if drifted:
        print("run `just sync-crd-refs` to repin them", file=sys.stderr)
        return 1

    print(f"==> every CRD reference is pinned to {ref}")
    return 0


def sync(charts: Path, ref: str) -> int:
    wanted = ref.encode()
    replacement = rb"\g<1>" + wanted + rb"\g<3>"

    repinned = False
    for path in collect(charts, SOURCES + BUILD_OUTPUT):
        content = path.read_bytes()
        counts = stale_refs(content, wanted)
        if not counts:
            continue

        # Written only when something actually changes, so a no-op run leaves every timestamp and
        # every byte alone.
        path.write_bytes(REF.sub(replacement, content))

        total = sum(counts.values())
        print(f"==> {path.as_posix()}: {total} reference(s) repinned to {ref}")
        repinned = True

    if not repinned:
        print(f"==> every CRD reference is already pinned to {ref}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ref", help="CRD catalog commit to pin to, as a full 40-character SHA-1")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drifted references and exit non-zero instead of rewriting them",
    )
    parser.add_argument(
        # Stdlib-only for the reason its Kubernetes sibling is: `maintain.just` reuses
        # `resolve_python` for both, and one answer to "which interpreter" is worth more to a
        # contributor than a marginally looser prerequisite here.
        "--charts", default="charts", type=Path, help="charts directory (default: charts)"
    )
    args = parser.parse_args(argv)

    if not CATALOG_REF.match(args.ref):
        fail(f"{args.ref!r} is not a full 40-character catalog commit")
    if not args.charts.is_dir():
        fail(f"{args.charts} is not a directory")
    if not collect(args.charts, SOURCES):
        fail(f"no chart values found under {args.charts}")

    return check(args.charts, args.ref) if args.check else sync(args.charts, args.ref)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
