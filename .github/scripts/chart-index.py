#!/usr/bin/env python3
"""Emit the chart index the root README is rendered from, as one line of JSON on stdout.

Every value comes from a chart's own Chart.yaml, so the table in the README cannot drift from
what the charts actually declare: adding a chart or bumping a version is enough. Application
and library charts are reported separately because the README presents them differently —
library charts are not published to the Helm repository.

Usage: .github/scripts/chart-index.py [charts-dir]
"""

import json
import sys
from pathlib import Path

import yaml

# Without these a chart cannot be listed at all, and a silently empty table cell is worse than
# a failed pipeline.
REQUIRED_FIELDS = ("name", "description", "version")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_chart(chart_yaml: Path) -> dict[str, str]:
    with chart_yaml.open(encoding="utf-8") as handle:
        metadata = yaml.safe_load(handle)

    if not isinstance(metadata, dict):
        fail(f"{chart_yaml} does not contain a YAML mapping")

    missing = [field for field in REQUIRED_FIELDS if not str(metadata.get(field, "")).strip()]
    if missing:
        fail(f"{chart_yaml} is missing required field(s): {', '.join(missing)}")

    return {
        "name": str(metadata["name"]),
        # Folded and multi-line descriptions become one line: the README renders them into a
        # table cell, where a line break would split the row.
        "description": " ".join(str(metadata["description"]).split()),
        "version": str(metadata["version"]),
        "appVersion": str(metadata.get("appVersion", "")),
        # Helm defaults an absent `type` to `application`.
        "type": str(metadata.get("type", "application")),
    }


def main(argv: list[str]) -> None:
    # Descriptions are not ASCII, and the rendered README must not depend on the locale the
    # script happens to run under.
    sys.stdout.reconfigure(encoding="utf-8")

    charts_dir = Path(argv[1] if len(argv) > 1 else "charts")
    if not charts_dir.is_dir():
        fail(f"{charts_dir} is not a directory")

    charts = [
        read_chart(entry / "Chart.yaml")
        for entry in sorted(charts_dir.iterdir())
        if (entry / "Chart.yaml").is_file()
    ]
    if not charts:
        fail(f"no charts found under {charts_dir}")

    index = {
        "charts": [chart for chart in charts if chart["type"] != "library"],
        "libraryCharts": [chart for chart in charts if chart["type"] == "library"],
    }

    json.dump(index, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main(sys.argv)
