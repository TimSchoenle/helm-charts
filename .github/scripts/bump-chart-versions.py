#!/usr/bin/env python3
"""Bump a chart's version, and its appVersion, when a pull request moved one of its image pins.

An image update rewrites `values.yaml` and nothing else, yet it changes what the chart deploys, so
`ct lint` demands a new `version` and the release workflow only publishes a chart whose version
moved. Renovate cannot own that: the pin and the version live in different files, and the release
pipelines that bump the first-party charts are not involved for a third-party image. This script
is the missing half, run by the CI documentation job on Renovate branches before anything is
generated from `Chart.yaml`.

What it does, per application chart, against the chart as it was on the base branch:

  * Finds every image pin in `values.yaml` — a mapping holding both `repository` and `tag` — and
    compares its tag, digest included, with the base. No changed pin, no change to the chart.
  * Bumps `version` from the *base* version: a patch, or a minor when a pin moved to a different
    major of the image, which is the update most likely to need a config change. Deriving it from
    the base rather than the working tree makes the run idempotent, and it means a rebase onto a
    newer main recomputes the bump instead of colliding with a version another pull request took.
    A chart whose version is already above the base's is left alone: a release pipeline or a
    contributor already did this.
  * Sets `appVersion` to the version part of the top-level `image.tag`, but only where the chart
    followed that convention at the base (appVersion equal to the tag's version). A chart that
    tracks something else in appVersion, or has no top-level image at all, is not touched.

The digest itself is not this script's concern: Renovate rewrites `tag: <version>@sha256:<digest>`
as one unit, and this only reads what it wrote.

Edits are byte-level substitutions of one line, so the rest of Chart.yaml, comments and line
endings included, is untouched.

Usage: .github/scripts/bump-chart-versions.py --base origin/main [--charts charts]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

VERSION_LINE = re.compile(r"^(?P<key>version:[ \t]*)(?P<value>\S+)(?=[ \t]*\r?$)", re.MULTILINE)
APP_VERSION_LINE = re.compile(
    r"^(?P<key>appVersion:[ \t]*)(?P<open>[\"']?)(?P<value>[^\"'\s]+)(?P<close>[\"']?)(?=[ \t]*\r?$)",
    re.MULTILINE,
)
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
LEADING_INTEGER = re.compile(r"^v?(\d+)")

PATCH = "patch"
MINOR = "minor"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_at_base(base: str, path: Path) -> str | None:
    """The file as committed on `base`, or None when it does not exist there (a new chart)."""
    result = subprocess.run(
        ["git", "show", f"{base}:{path.as_posix()}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8")


def parse_yaml(text: str, origin: str) -> dict[str, Any]:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        fail(f"{origin} does not contain a YAML mapping")
    return document


def collect_pins(node: Any, path: str = "") -> dict[str, str]:
    """Every image pin under `node`, as `values path -> tag`."""
    pins: dict[str, str] = {}
    if isinstance(node, dict):
        tag = node.get("tag")
        if isinstance(node.get("repository"), str) and isinstance(tag, str) and tag:
            pins[path or "."] = tag
        for key, child in node.items():
            pins.update(collect_pins(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            pins.update(collect_pins(child, f"{path}[{index}]"))
    return pins


def version_of(tag: str) -> str:
    """The tag without its digest: `3.0.5@sha256:...` -> `3.0.5`."""
    return tag.split("@", 1)[0]


def major_of(tag_version: str) -> int | None:
    """The leading integer of a tag, or None for one that does not start with a version (`pg18`)."""
    match = LEADING_INTEGER.match(tag_version)
    return int(match.group(1)) if match else None


def bump_kind(base_pins: dict[str, str], head_pins: dict[str, str]) -> str | None:
    """How far the chart version has to move, or None when no pin changed."""
    kind: str | None = None
    for path, head_tag in head_pins.items():
        base_tag = base_pins.get(path)
        if base_tag is None or base_tag == head_tag:
            continue
        kind = kind or PATCH
        base_major = major_of(version_of(base_tag))
        head_major = major_of(version_of(head_tag))
        if base_major is not None and head_major is not None and base_major != head_major:
            kind = MINOR
    return kind


def parse_semver(version: str, origin: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        fail(f"{origin}: version '{version}' is not MAJOR.MINOR.PATCH")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bumped(version: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if kind == MINOR:
        return major, minor + 1, 0
    return major, minor, patch + 1


def format_semver(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def replace_line(text: str, pattern: re.Pattern[str], value: str, origin: str) -> str:
    if len(pattern.findall(text)) != 1:
        fail(f"{origin}: expected exactly one top-level line matching {pattern.pattern!r}")
    return pattern.sub(lambda match: match.group(0).replace(match.group("value"), value, 1), text)


def main_image_version(values: dict[str, Any]) -> str | None:
    image = values.get("image")
    if isinstance(image, dict) and isinstance(image.get("tag"), str) and image["tag"]:
        return version_of(image["tag"])
    return None


def process_chart(chart_dir: Path, base: str) -> list[str]:
    """Bump one chart in place, and return one line per change made."""
    chart_path = chart_dir / "Chart.yaml"
    values_path = chart_dir / "values.yaml"
    base_chart_text = read_at_base(base, chart_path)
    base_values_text = read_at_base(base, values_path)
    if base_chart_text is None or base_values_text is None or not values_path.is_file():
        return []

    head_chart_text = chart_path.read_bytes().decode("utf-8")
    head_chart = parse_yaml(head_chart_text, str(chart_path))
    if head_chart.get("type") == "library":
        return []

    base_chart = parse_yaml(base_chart_text, f"{base}:{chart_path.as_posix()}")
    base_values = parse_yaml(base_values_text, f"{base}:{values_path.as_posix()}")
    head_values = parse_yaml(values_path.read_text(encoding="utf-8"), str(values_path))

    kind = bump_kind(collect_pins(base_values), collect_pins(head_values))
    if kind is None:
        return []

    name = str(head_chart.get("name", chart_dir.name))
    changes: list[str] = []
    text = head_chart_text

    base_version = parse_semver(str(base_chart["version"]), f"{base}:{chart_path.as_posix()}")
    head_version = parse_semver(str(head_chart["version"]), str(chart_path))
    if head_version <= base_version:
        new_version = format_semver(bumped(base_version, kind))
        text = replace_line(text, VERSION_LINE, new_version, str(chart_path))
        changes.append(f"{name}: version {format_semver(base_version)} -> {new_version} ({kind})")

    base_image = main_image_version(base_values)
    head_image = main_image_version(head_values)
    tracked = base_image is not None and base_image == str(base_chart.get("appVersion", ""))
    if tracked and head_image is not None and head_image != str(head_chart.get("appVersion", "")):
        text = replace_line(text, APP_VERSION_LINE, head_image, str(chart_path))
        changes.append(f"{name}: appVersion {base_image} -> {head_image}")

    if text != head_chart_text:
        chart_path.write_bytes(text.encode("utf-8"))
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", required=True, help="git ref holding the base branch, e.g. origin/main")
    parser.add_argument("--charts", default="charts", type=Path, help="directory holding the charts")
    args = parser.parse_args()

    if not args.charts.is_dir():
        fail(f"{args.charts} is not a directory")

    changes: list[str] = []
    for chart_yaml in sorted(args.charts.glob("*/Chart.yaml")):
        changes.extend(process_chart(chart_yaml.parent, args.base))

    for line in changes:
        print(line)
    if not changes:
        print("no image pin moved without a chart version bump")


if __name__ == "__main__":
    main()
