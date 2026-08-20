#!/usr/bin/env python3
"""Report where a chart's shared values blocks have drifted from the scaffold's copy.

`just new-chart` writes a new chart from `.github/templates/chart/values.chassis.yaml` — the
twenty-nine `values.yaml` blocks that are the same in every chart because `charts/common` owns the
logic and these files only name it. Nothing keeps that copy and the charts in step. A block gains a
sentence in one chart, the scaffold keeps the old wording, and the next chart created inherits it.

That is the gap this reports, and it is worth being precise about which direction it runs in. The
scaffold is **not** the authority. It was seeded from the majority text of the eight existing
charts, which means it is right about most blocks by construction and has no claim to be right
about any particular one. So this prints differences and exits 0: a chart that has deliberately
diverged is a normal thing for a chart to do, and a gate that failed on it would be red for a
decision somebody made on purpose.

What the report is *for* is the other case — a block edited in one chart and nowhere else, which
is the same drift `just new-chart` exists to stop and which nothing else in this repository can
see. Reading the list and deciding, per block, whether the chart or the scaffold is behind is the
work; this only makes the list.

--------------------------------------------------------------------------------------------
Why the comparison is textual
--------------------------------------------------------------------------------------------

A block is compared as the lines it occupies, comments included, not as the value it parses to.
That is deliberate twice over.

The `@schema` blocks *are* the interesting part: they are what `values.schema.json` is generated
from, so a chart whose `podSecurityContext` block lost a `type: integer` has a materially
different schema while parsing to the same mapping. And the `# --` descriptions are published —
helm-docs renders them into every chart's README — so a description that improved in one chart and
nowhere else is exactly the drift worth seeing.

Whitespace at the end of a line is normalised away and nothing else is. Two blocks that differ
only in trailing spaces are the same block, and a reader told otherwise would stop reading the
report.

--------------------------------------------------------------------------------------------
What is deliberately not reported
--------------------------------------------------------------------------------------------

**A chart that does not carry a block at all.** `teamspeak` has no `extraVolumeMounts` and does
not need one; "this chart is missing a block the scaffold writes" is a fact about what the chart
does, not about drift. Only blocks present on both sides are compared.

**Anything outside the chassis.** A chart's own configuration surface is per image and has no
shared copy to drift from.

Usage: python3 .github/scripts/check-chassis.py
       python3 .github/scripts/check-chassis.py --charts charts --chassis <path>
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_paths import CHARTS_DIR

CHASSIS = Path(".github/templates/chart/values.chassis.yaml")

# A top-level mapping key in a values file. Anchored at column zero, which is what makes it a
# *top-level* key and not any of the several hundred nested ones.
_TOP_LEVEL = re.compile(r"^[A-Za-z][A-Za-z0-9]*:")

# The library chart, which has no chassis: it renders nothing and carries no values of its own.
LIBRARY = "common"


def blocks(text: str) -> dict[str, list[str]]:
    """Split a values file into its top-level blocks, each with the comment run above its key.

    The comment run is part of the block and not of the one before it, because that run is the
    `@schema` block and the `# --` description — the two things a reader is comparing. Attaching
    it to the preceding key would report every difference against the wrong neighbour.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if _TOP_LEVEL.match(line)]

    found: dict[str, list[str]] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)

        # Give the next block back the comment run and blank lines directly above its own key.
        while end > start and (lines[end - 1].startswith("#") or not lines[end - 1].strip()):
            end -= 1

        top = start
        while top > 0 and lines[top - 1].startswith("#"):
            top -= 1

        found[lines[start].split(":")[0]] = [line.rstrip() for line in lines[top:end]]
    return found


def compare(chassis: dict[str, list[str]], chart: str, values: dict[str, list[str]]) -> list[str]:
    """A unified diff per shared block that differs. Empty when the chart matches the scaffold."""
    report: list[str] = []
    for name in sorted(chassis):
        theirs = values.get(name)
        if theirs is None or theirs == chassis[name]:
            continue
        report.extend(
            difflib.unified_diff(
                chassis[name],
                theirs,
                fromfile=f"scaffold/{name}",
                tofile=f"{chart}/{name}",
                lineterm="",
                n=1,
            )
        )
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("chart", nargs="?", default="", help="one chart, or every chart")
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    parser.add_argument("--chassis", default=str(CHASSIS))
    parser.add_argument(
        "--summary", action="store_true", help="count the drifted blocks instead of diffing them"
    )
    args = parser.parse_args(argv)

    chassis_path = Path(args.chassis)
    if not chassis_path.is_file():
        print(f"error: {chassis_path}: no chassis to compare against", file=sys.stderr)
        return 1

    chassis = blocks(chassis_path.read_text(encoding="utf-8"))
    charts = Path(args.charts)
    if not charts.is_dir():
        print(f"error: {charts}: no such directory", file=sys.stderr)
        return 1

    # Walked here rather than through `config_declaration.chart_dirs`, because this reads no
    # declaration and a chart without one is exactly as interesting to it as a chart with one.
    directories = [charts / args.chart] if args.chart else sorted(charts.iterdir())

    drifted = 0
    shared = 0
    for chart_dir in directories:
        values_path = chart_dir / "values.yaml"
        if chart_dir.name == LIBRARY or not values_path.is_file():
            continue

        values = blocks(values_path.read_text(encoding="utf-8"))
        present = [name for name in chassis if name in values]
        shared += len(present)
        differing = [name for name in present if values[name] != chassis[name]]

        if not differing:
            print(f"in step: {chart_dir.name} ({len(present)} shared block(s))")
            continue

        drifted += len(differing)
        print(
            f"drifted: {chart_dir.name} "
            f"({len(differing)} of {len(present)} shared block(s)): {', '.join(differing)}"
        )
        if not args.summary:
            for line in compare(chassis, chart_dir.name, values):
                print(f"    {line}")

    print(f"\n==> {drifted} of {shared} shared block(s) differ from the scaffold's copy")
    if drifted:
        # Report only. The scaffold was seeded from the majority text of the existing charts, so
        # it is right about most blocks by construction and has no claim to be right about any
        # particular one — a chart that diverged on purpose is a normal thing, and failing on it
        # would be red for somebody's decision. Deciding which side is behind is the work; this
        # only makes the list.
        print("    Each is a chart or a scaffold that is behind. Read them and decide which.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
