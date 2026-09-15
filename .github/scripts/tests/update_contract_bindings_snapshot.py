#!/usr/bin/env python3
"""Regenerate `snapshots/contract_bindings_gate.json`.

`test_every_enrolled_chart_passes_the_gate` is the one binding test that walks the real chart
tree instead of a fixture, so it is also the one that legitimately drifts every time a chart's
`@config` markers gain or lose a key or a document: a new marker changes the (chart, keys,
external) tuple the gate reports, and there is no way to assert "whatever the tree currently
says" without also throwing away the regression check.

Run this after a change to a chart's markers, review the diff it produces like any other
generated file, and let the change explain itself in the PR rather than in a hand-edited number.
This script does not judge whether the new counts are *right* — only `just check-config-bindings`
and the eyes of a reviewer do that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from config_report import Report  # noqa: E402
from entry import load  # noqa: E402

entry = load("config_bindings_entry", "check-config-bindings.py")

CHARTS = SCRIPTS.parents[1] / "charts"
SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "contract_bindings_gate.json"


def main() -> int:
    report = Report()
    enrolled = entry.run(CHARTS, report)
    problems = "\n".join(finding.message for _, finding in report.errors)
    if problems:
        print(problems, file=sys.stderr)
        print("refusing to snapshot a gate that is not passing", file=sys.stderr)
        return 1

    rows = [[chart, keys, external] for chart, keys, external in enrolled]
    SNAPSHOT.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
