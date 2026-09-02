#!/usr/bin/env python3
"""Hold every hand-transcribed config shape against the appVersion it was read at.

A configuration contract says a key's type as a flat keyword set — `type`, `enum`, `minimum`,
`pattern` and eight more — and that vocabulary has no `items` and no `properties`. So a setting
whose type is a *struct* arrives with its shape missing: `discord-alertmanager`'s `routes` is a
`Vec<RouteConfig>` and the contract carries `{"type": "array"}`, which is everything the producer
can currently say about it.

The chart types those values anyway, because a bare `type: array` is not type checking and the
whole point of `values.schema.json` is that an operator's editor and `helm install` catch a
misspelt key before the service does. That means transcribing a struct the producer owns, which
is a copy of a fact — and a copy nothing holds in step is the one that goes stale. This is what
holds it in step:

    # @config-shape <values-path> <appVersion> <source>

written as a plain comment above the value's `@schema` block. It asserts "the schema below was
read from `<source>` at `<appVersion>`", and this script fails when the chart's `appVersion` has
moved past it.

The failure it exists to catch is the documented recurring one here: an automated bump repins the
image and its digest and touches nothing else, so a release that added a field to `RouteConfig`
would leave a chart whose schema refuses that field — `additionalProperties: false`, because the
producer's structs are `deny_unknown_fields` — and the operator hits it as a rejected `helm
upgrade` with no clue that the chart is simply behind.

Deliberately *not* a network check. Reading the struct at the new version is a person's job: the
question is not whether the file changed but whether the schema still describes it, and a
diff-watching gate that answered "changed" would be asking the same person to look anyway. What
this removes is the chance of nobody looking at all.

Placement is a plain comment rather than a line inside the `@schema` block, unlike `# @config`:
the block's contents are parsed as YAML and become the schema, so a marker there would have to be
a schema keyword. A comment above, separated by a blank line, is invisible to helm-schema and to
helm-docs alike — the placement table in `config_bindings.py` measured that.

Usage: .github/scripts/check-config-shapes.py [--charts DIR]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from config_paths import CHARTS_DIR, dig, read_yaml

# `# @config-shape <values-path> <appVersion> <source>`, as a whole comment line. Anchored so a
# marker mentioned in prose — this file's own docstring, a chart's `# --` description — is not
# mistaken for a declaration.
MARKER = re.compile(
    r"^\s*#\s*@config-shape\s+(?P<path>\S+)\s+(?P<version>\S+)\s+(?P<source>\S+)\s*$"
)


def markers(path: Path) -> list[tuple[int, str, str, str]]:
    """Every `@config-shape` marker in one values.yaml.

    Each as `(line, values-path, version, source)`.
    """
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = MARKER.match(line)
        if match:
            found.append(
                (number, match["path"], match["version"], match["source"])
            )
    return found


def check_chart(chart: Path) -> tuple[int, list[str]]:
    """Every marker in one chart, against its Chart.yaml and its values."""
    values_file = chart / "values.yaml"
    declared = markers(values_file)
    if not declared:
        return 0, []

    app_version = str(read_yaml(chart / "Chart.yaml").get("appVersion", "")).strip()
    values = read_yaml(values_file)
    problems = []

    for number, values_path, version, source in declared:
        where = f"{values_file.as_posix()}:{number}"

        # A marker naming a value that does not exist is a rename nobody carried across, and it
        # would otherwise sit there asserting something about nothing.
        if dig(values, values_path) is None and not _declared(values, values_path):
            problems.append(
                f"{where}: names `{values_path}`, which this chart's values.yaml does not declare"
            )
            continue

        if not app_version:
            problems.append(
                f"{where}: declares a shape for `{values_path}`, but the chart has no appVersion "
                f"to hold it against"
            )
            continue

        if version != app_version:
            problems.append(
                f"{where}: `{values_path}` was transcribed at {version}, and the chart now pins "
                f"appVersion {app_version}\n"
                f"    re-read {source} at {app_version}, bring the `@schema` block for "
                f"`{values_path}` up to it, and move the marker"
            )

    return len(declared), problems


def _declared(values: object, path: str) -> bool:
    """Whether the path exists at all, including when it holds `null` or an empty list.

    `dig` answers with `None` for both "absent" and "present and empty", and `routes: []` is the
    ordinary state of exactly the values this gate covers.
    """
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--charts", default=CHARTS_DIR, type=Path, help="charts directory (default: charts)"
    )
    args = parser.parse_args(argv)

    if not args.charts.is_dir():
        print(f"error: {args.charts} is not a directory", file=sys.stderr)
        return 1

    total = 0
    charts = 0
    problems: list[str] = []
    for chart in sorted(args.charts.iterdir()):
        if not (chart / "values.yaml").is_file():
            continue
        count, found = check_chart(chart)
        problems += found
        total += count
        charts += 1 if count else 0

    # Every problem is reported before it exits, matching every other gate here: a run that
    # stopped at the first would hide the second on a bump that moved two charts.
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            f"\nerror: {len(problems)} hand-transcribed config shape(s) are behind their chart",
            file=sys.stderr,
        )
        return 1

    print(f"==> {total} hand-transcribed config shape(s) in {charts} chart(s), all current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
