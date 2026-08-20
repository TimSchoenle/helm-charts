#!/usr/bin/env python3
"""Keep every chart's Kubernetes `$ref` pinned to one Kubernetes release.

Charts type their Kubernetes-shaped values by URL, in an `@schema` block:

    # @schema
    # $ref: https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/v1.34.0/_definitions.json#/definitions/io.k8s.api.core.v1.PodSecurityContext
    # @schema

helm-schema copies those URLs verbatim into `values.schema.json`, so one release number ends up
written out several thousand times across the repository with nothing deriving it from the version
the manifests are actually validated against. A bump that misses them leaves every chart validating
its values against the superseded release, and silently: the old URL keeps resolving, so nothing
fails — the schema is simply the wrong one. This script is the derivation, driven by `kube_version`
in the justfile.

Rewrites are byte-level on purpose. The files are checked out with native line endings, and
decoding to text and writing back would normalise them repository-wide on a Windows shell — an
enormous diff for a URL substitution, and one that breaks the snapshot tests. Only the pinned
segment of a matched URL is ever replaced; every other byte is copied through untouched.

Usage: .github/scripts/kube-schema-refs.py VERSION [--check] [--charts DIR]
"""

import argparse
import re
import sys
from pathlib import Path

# The pinned segment of a Kubernetes `$ref`, with the release as the middle group.
#
# `_definitions.json` is part of the pattern, so no other URL on that host is matched — notably
# not the `-standalone-strict` catalog `check-immutable-fields.py` reads, which pins its own
# version and is not a chart reference.
REF = re.compile(rb"(kubernetes-json-schema/master/)v\d+\.\d+\.\d+(/_definitions\.json)")

# A release as it appears in a reference, so a typo is rejected before it is written into every
# chart rather than surfacing later as a schema that fails to resolve.
VERSION = re.compile(r"\d+\.\d+\.\d+\Z")

# Where references live, relative to the charts directory. `values.schema.json` is generated from
# `values.yaml`, but is rewritten alongside it rather than left to `helm schema`: the generator
# would produce this exact substitution, so doing it here keeps the tree consistent without the
# plugin installed, and a following `just docs` is a no-op on these references.
SOURCES = ("*/values.yaml", "*/values.schema.json")

# The extracted dependency trees. Gitignored build output that `just deps` rebuilds from
# `charts/common`, so rewriting them is not what makes the repository correct — it is what makes
# the rewrite order-independent, instead of leaving a `helm schema` run against an extracted copy
# from before the bump to reintroduce the superseded release. Never reported as drifted: such a
# copy is stale by construction and flagging it would be noise.
BUILD_OUTPUT = ("*/charts/*/values.yaml", "*/charts/*/values.schema.json")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def collect(charts: Path, patterns: tuple[str, ...]) -> list[Path]:
    return sorted({match for pattern in patterns for match in charts.glob(pattern)})


def stale_versions(content: bytes, wanted: bytes) -> dict[str, int]:
    """How many references name each release other than the wanted one."""
    counts: dict[str, int] = {}
    for match in REF.finditer(content):
        found = match.group(0).rsplit(b"/", 2)[1]
        if found != wanted:
            key = found.decode()
            counts[key] = counts.get(key, 0) + 1
    return counts


def report(path: Path, counts: dict[str, int], wanted: str) -> None:
    for found, count in sorted(counts.items()):
        print(
            f"{path.as_posix()}: {count} reference(s) pinned to {found}, wanted v{wanted}",
            file=sys.stderr,
        )


def check(charts: Path, version: str) -> int:
    wanted = f"v{version}".encode()

    drifted = False
    for path in collect(charts, SOURCES):
        counts = stale_versions(path.read_bytes(), wanted)
        if counts:
            drifted = True
            report(path, counts, version)

    if drifted:
        print("run `just sync-kube-refs` to repin them", file=sys.stderr)
        return 1

    print(f"==> every Kubernetes reference is pinned to v{version}")
    return 0


def sync(charts: Path, version: str) -> int:
    wanted = f"v{version}".encode()
    replacement = rb"\g<1>" + wanted + rb"\g<2>"

    repinned = False
    for path in collect(charts, SOURCES + BUILD_OUTPUT):
        content = path.read_bytes()
        counts = stale_versions(content, wanted)
        if not counts:
            continue

        # Written only when something actually changes, so a no-op run leaves every timestamp and
        # every byte alone.
        path.write_bytes(REF.sub(replacement, content))

        total = sum(counts.values())
        print(f"==> {path.as_posix()}: {total} reference(s) repinned to v{version}")
        repinned = True

    if not repinned:
        print(f"==> every Kubernetes reference is already pinned to v{version}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="Kubernetes release to pin to, without the `v` prefix")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drifted references and exit non-zero instead of rewriting them",
    )
    parser.add_argument(
        # The one script here that does not share `config_paths.CHARTS_DIR`, and deliberately:
        # that module imports PyYAML, and this script is stdlib-only on purpose — `maintain.just`
        # records that it reuses `resolve_python` "even though this script imports nothing beyond
        # the standard library". Trading that for one shared constant is the wrong way round.
        "--charts", default="charts", type=Path, help="charts directory (default: charts)"
    )
    args = parser.parse_args(argv)

    if not VERSION.match(args.version):
        fail(f"{args.version!r} is not a Kubernetes release of the form MAJOR.MINOR.PATCH")
    if not args.charts.is_dir():
        fail(f"{args.charts} is not a directory")
    if not collect(args.charts, SOURCES):
        fail(f"no chart values found under {args.charts}")

    return check(args.charts, args.version) if args.check else sync(args.charts, args.version)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
