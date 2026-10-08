#!/usr/bin/env python3
"""Keep the `common` library's version, and every chart's pin on it, in step with its source.

Every chart in this repository takes `common` from `file://../common` at an exact version, and the
library is excluded from chart-testing. Two things followed from that, and both had happened:

  * A change under `charts/common/` needed no version bump to pass CI. #623 rewrote the library's
    `values.yaml` and `values.schema.json` after `common-2.4.1` was tagged, and nothing moved, so
    the source on main no longer matched the release of the same version.
  * A chart's pin could stay where it was while the library changed underneath it. The pin is
    resolved from the working tree, so the chart silently picked up the new code anyway, but its
    own version did not move and nothing released it. A fix in `common` reached a published chart
    only when that chart next changed for another reason, and two releases could both claim
    `common 2.4.1` while bundling different code.

The rules, all relative to the base branch:

  * If anything under `charts/common/` differs from the base, the library's `version` must be
    above the base's. This script bumps it from the *base* version (a patch by default, `--kind`
    for more), so a run is idempotent and a rebase recomputes the number.
  * Every chart that takes `common` from a `file://` repository must pin exactly the library's
    current version. The fixture under `.github/testdata/` is held to the same rule.
  * A chart whose pin moved must itself move above its base version (a patch), or `ct lint`
    rejects it and the release workflow never publishes it. Bumping the pin is what puts the chart
    on chart-testing's changed list, so the library change gets the install and upgrade tests of
    every chart that renders it.

With `--check` nothing is written: the script reports what it would change and exits 1 if
anything, which is the CI gate. Without it, the changes are made, which is what the documentation
job runs on Renovate branches and what a contributor runs after editing the library.

Edits are byte-level substitutions of one line each, so comments and line endings survive.

Usage: .github/scripts/bump-common-version.py --base origin/main [--check] [--kind patch]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

LIBRARY = "common"
VERSION_LINE = re.compile(r"^(?P<key>version:[ \t]*)(?P<value>\S+)(?=[ \t]*\r?$)", re.MULTILINE)
DEPENDENCY_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)- name:[ \t]*[\"']?" + LIBRARY + r"[\"']?[ \t]*\r?\n"
    r"(?P<body>(?:(?P=indent)[ \t]+\S[^\r\n]*(?:\r?\n|$))*)",
    re.MULTILINE,
)
DEPENDENCY_VERSION = re.compile(
    r"^(?P<key>[ \t]+version:[ \t]*)(?P<open>[\"']?)(?P<value>[^\"'\s]+)(?P=open)(?=[ \t]*\r?$)",
    re.MULTILINE,
)
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

PATCH = "patch"
MINOR = "minor"
MAJOR = "major"

Version = tuple[int, int, int]


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], capture_output=True, check=False)


def read_at_base(base: str, path: Path) -> str | None:
    """The file as committed on `base`, or None when it does not exist there."""
    result = git("show", f"{base}:{path.as_posix()}")
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8")


def changed_since(base: str, path: Path) -> list[str]:
    """Files under `path` that differ between `base` and the working tree, untracked included."""
    diff = git("diff", "--name-only", base, "--", path.as_posix())
    if diff.returncode != 0:
        fail(f"git diff against {base} failed: {diff.stderr.decode().strip()}")
    untracked = git("ls-files", "--others", "--exclude-standard", "--", path.as_posix())
    names = diff.stdout.decode().splitlines() + untracked.stdout.decode().splitlines()
    return sorted(name for name in set(names) if name)


def parse_yaml(text: str, origin: str) -> dict[str, Any]:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        fail(f"{origin} does not contain a YAML mapping")
    return document


def parse_semver(version: str, origin: str) -> Version:
    match = SEMVER.match(version)
    if not match:
        fail(f"{origin}: version '{version}' is not MAJOR.MINOR.PATCH")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bumped(version: Version, kind: str) -> Version:
    major, minor, patch = version
    if kind == MAJOR:
        return major + 1, 0, 0
    if kind == MINOR:
        return major, minor + 1, 0
    return major, minor, patch + 1


def format_semver(version: Version) -> str:
    return ".".join(str(part) for part in version)


def replace_top_level_version(text: str, value: str, origin: str) -> str:
    if len(VERSION_LINE.findall(text)) != 1:
        fail(f"{origin}: expected exactly one top-level `version:` line")
    return VERSION_LINE.sub(lambda m: m.group("key") + value, text, count=1)


def library_pin(chart: dict[str, Any]) -> str | None:
    """The version a chart pins `common` at, when it takes it from a `file://` repository."""
    for dependency in chart.get("dependencies") or []:
        if not isinstance(dependency, dict) or dependency.get("name") != LIBRARY:
            continue
        if str(dependency.get("repository", "")).startswith("file://"):
            return str(dependency.get("version", ""))
    return None


def replace_library_pin(text: str, value: str, origin: str) -> str:
    items = list(DEPENDENCY_ITEM.finditer(text))
    if len(items) != 1:
        fail(f"{origin}: expected exactly one `- name: {LIBRARY}` dependency entry")
    item = items[0]
    body = item.group("body")
    if len(DEPENDENCY_VERSION.findall(body)) != 1:
        fail(f"{origin}: the `{LIBRARY}` dependency entry has no single `version:` line")
    new_body = DEPENDENCY_VERSION.sub(
        lambda m: m.group("key") + m.group("open") + value + m.group("open"), body, count=1
    )
    return text[: item.start("body")] + new_body + text[item.end("body") :]


class Plan:
    """The edits a run makes, collected first so `--check` can report them without writing."""

    def __init__(self) -> None:
        self.writes: dict[Path, str] = {}
        self.changes: list[str] = []

    def text(self, path: Path) -> str:
        return self.writes.get(path) or path.read_bytes().decode("utf-8")

    def write(self, path: Path, text: str, change: str) -> None:
        self.writes[path] = text
        self.changes.append(change)

    def apply(self) -> None:
        for path, text in self.writes.items():
            path.write_bytes(text.encode("utf-8"))


def plan_library(plan: Plan, library_dir: Path, base: str, kind: str) -> str:
    """Bump the library if its sources moved and its version did not; return its version."""
    chart_path = library_dir / "Chart.yaml"
    head_text = plan.text(chart_path)
    head_version = parse_semver(
        str(parse_yaml(head_text, str(chart_path))["version"]), str(chart_path)
    )

    base_text = read_at_base(base, chart_path)
    if base_text is None:
        return format_semver(head_version)
    base_origin = f"{base}:{chart_path.as_posix()}"
    base_version = parse_semver(str(parse_yaml(base_text, base_origin)["version"]), base_origin)

    changed = changed_since(base, library_dir)
    if not changed or head_version > base_version:
        return format_semver(head_version)

    new_version = format_semver(bumped(base_version, kind))
    plan.write(
        chart_path,
        replace_top_level_version(head_text, new_version, str(chart_path)),
        f"{LIBRARY}: version {format_semver(base_version)} -> {new_version} ({kind}); "
        f"changed since {base}: {', '.join(changed)}",
    )
    return new_version


def plan_consumer(plan: Plan, chart_dir: Path, base: str, library_version: str, release: bool):
    """Repin one chart on the library, and bump its own version when the pin moved."""
    chart_path = chart_dir / "Chart.yaml"
    head_text = plan.text(chart_path)
    head_chart = parse_yaml(head_text, str(chart_path))
    if head_chart.get("type") == "library":
        return
    pin = library_pin(head_chart)
    if pin is None:
        return

    name = str(head_chart.get("name", chart_dir.name))
    text = head_text
    if pin != library_version:
        text = replace_library_pin(text, library_version, str(chart_path))
        plan.write(chart_path, text, f"{name}: {LIBRARY} pin {pin} -> {library_version}")

    if not release:
        return
    base_text = read_at_base(base, chart_path)
    if base_text is None:
        return
    base_origin = f"{base}:{chart_path.as_posix()}"
    base_chart = parse_yaml(base_text, base_origin)
    if library_pin(base_chart) == library_version:
        return
    base_version = parse_semver(str(base_chart["version"]), base_origin)
    head_version = parse_semver(str(head_chart["version"]), str(chart_path))
    if head_version > base_version:
        return
    new_version = format_semver(bumped(base_version, PATCH))
    plan.write(
        chart_path,
        replace_top_level_version(text, new_version, str(chart_path)),
        f"{name}: version {format_semver(base_version)} -> {new_version} "
        f"({LIBRARY} {library_pin(base_chart)} -> {library_version})",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--base", required=True, help="git ref holding the base branch, e.g. origin/main"
    )
    parser.add_argument(
        "--charts", default="charts", type=Path, help="directory holding the charts"
    )
    parser.add_argument(
        "--fixture",
        action="append",
        default=[],
        type=Path,
        help="a test-only chart held to the pin rule but never versioned (repeatable)",
    )
    parser.add_argument(
        "--kind",
        choices=(PATCH, MINOR, MAJOR),
        default=PATCH,
        help="how far to move the library when it needs a bump (default: patch)",
    )
    parser.add_argument(
        "--check", action="store_true", help="report what would change and exit 1 if anything"
    )
    args = parser.parse_args()

    library_dir = args.charts / LIBRARY
    if not (library_dir / "Chart.yaml").is_file():
        fail(f"{library_dir}/Chart.yaml does not exist")

    plan = Plan()
    library_version = plan_library(plan, library_dir, args.base, args.kind)
    for chart_yaml in sorted(args.charts.glob("*/Chart.yaml")):
        if chart_yaml.parent != library_dir:
            plan_consumer(plan, chart_yaml.parent, args.base, library_version, release=True)
    for fixture in args.fixture:
        plan_consumer(plan, fixture, args.base, library_version, release=False)

    if not plan.changes:
        print(f"{LIBRARY} {library_version}: version and every pin are in step")
        return
    if args.check:
        print(f"error: {LIBRARY} versioning is out of step with {args.base}:", file=sys.stderr)
        for line in plan.changes:
            print(f"  {line}", file=sys.stderr)
        print("Run `just bump-common-version` and commit the result.", file=sys.stderr)
        raise SystemExit(1)
    plan.apply()
    for line in plan.changes:
        print(line)


if __name__ == "__main__":
    main()
