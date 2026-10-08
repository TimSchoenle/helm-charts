#!/usr/bin/env python3
"""Compare two directories of rendered manifests object by object, ignoring formatting.

The question this answers is whether two Helm engines render the same chart to the same objects.
They do not always: a chart that round-trips the root context through `toYaml | fromYaml` reads
`.Chart.AppVersion` under Helm 4 and gets nothing under Helm 3, because Helm 3 serialises `.Chart`
by its JSON field names. paperless-ngx shipped exactly that, and nothing in CI noticed. Every other
gate renders with one Helm, and helm-unittest renders with the SDK compiled into the plugin
(Helm 3.21 up to 1.1.x), whichever Helm runs it.

A text diff of the two renders is useless here. Helm 4 emits blank lines Helm 3 does not, and any
chart that generates a secret with `randAlphaNum` differs on every render, Helm version or not.
So both sides are parsed, each object is keyed by kind, namespace and name, and the objects are
compared as data. The values under a Secret's `data` and `stringData` are replaced by a marker
before comparing; their keys still are compared. That is the one blind spot, and it is the place
a random value legitimately lives.

Usage: .github/scripts/compare-renders.py <left-dir> <right-dir> [--left-label L] [--right-label R]
"""

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

import yaml

MASK = "<secret value>"

Key = tuple[str, str, str]


def masked(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("kind") != "Secret":
        return document
    copy = dict(document)
    for field in ("data", "stringData"):
        if isinstance(copy.get(field), dict):
            copy[field] = dict.fromkeys(copy[field], MASK)
    return copy


def load(path: Path) -> dict[Key, dict[str, Any]]:
    objects: dict[Key, dict[str, Any]] = {}
    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not isinstance(document, dict):
            continue
        metadata = document.get("metadata") or {}
        key = (
            str(document.get("kind", "")),
            str(metadata.get("namespace", "")),
            str(metadata.get("name", "")),
        )
        objects[key] = masked(document)
    return objects


def dump(document: dict[str, Any]) -> list[str]:
    return yaml.safe_dump(document, sort_keys=True, default_flow_style=False).splitlines()


def compare(left: Path, right: Path, left_label: str, right_label: str) -> list[str]:
    report: list[str] = []
    names = sorted({p.name for p in left.glob("*.yaml")} | {p.name for p in right.glob("*.yaml")})
    for name in names:
        left_file, right_file = left / name, right / name
        if not left_file.is_file() or not right_file.is_file():
            missing = left_label if not left_file.is_file() else right_label
            report.append(f"{name}: not rendered by {missing}")
            continue
        left_objects, right_objects = load(left_file), load(right_file)
        for key in sorted(left_objects.keys() | right_objects.keys()):
            label = f"{name}: {key[0]} {key[1] + '/' if key[1] else ''}{key[2]}"
            if key not in right_objects or key not in left_objects:
                only = left_label if key in left_objects else right_label
                report.append(f"{label}: only rendered by {only}")
                continue
            if left_objects[key] == right_objects[key]:
                continue
            report.append(f"{label}: differs")
            report.extend(
                "    " + line
                for line in difflib.unified_diff(
                    dump(left_objects[key]),
                    dump(right_objects[key]),
                    left_label,
                    right_label,
                    lineterm="",
                    n=2,
                )
            )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--left-label", default="left")
    parser.add_argument("--right-label", default="right")
    args = parser.parse_args()

    for directory in (args.left, args.right):
        if not directory.is_dir():
            print(f"error: {directory} is not a directory", file=sys.stderr)
            raise SystemExit(2)

    report = compare(args.left, args.right, args.left_label, args.right_label)
    if not report:
        count = len(list(args.left.glob("*.yaml")))
        print(f"{count} renders: {args.left_label} and {args.right_label} produce the same objects")
        return
    print("\n".join(report))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
