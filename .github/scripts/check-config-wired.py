#!/usr/bin/env python3
"""Check that every `# @config` binding marker's key is wired into some template, at all.

`check-config-bindings.py` (now largely superseded by `terrace-contract bindings`, which is what
`just check-config-bindings` actually runs) holds a marker against the *contract* — does the key
exist, does the class match, is the scope right. None of that reads a single template file, so a
marker can pass every one of those checks while the chart renders nothing for it: the value
exists, is typed, documented and bound, and the define nobody edited still emits exactly what it
emitted before. `feat: add new contract values` (chart `tankovault`, commit `ee094b4`) is the
measured case — `scheduler.*` and `statementTimeouts.*` got markers, schema and round-trip tests,
and reached no `[scheduler]` or `[statement_timeouts]` table anywhere, silently, until the
generated round-trip suite happened to be run.

This is the other half: for every `projection`/`structured`/`composed` marker (the three classes
whose target is a `schema.keys` path — an `external` marker feeds an env var, not a template, and
is out of scope here), confirm the contract key's own leaf name appears, as a key, somewhere under
the chart's `templates/*.tpl`. Deliberately crude — a plain text search for `"<leaf>:"`, nothing
that parses Go templates or TOML — because that crudeness is this repository's own conclusion
about markers in `config_bindings.py`: nothing here round-trips template-plus-comments, so a
line-based reader is the only implementation that does not silently disagree with itself.

**What this proves, and what it does not.** A hit means the key's name appears somewhere as a
rendered key; it does not mean it is wired to the *right* value, gated on the *right* service, or
guarded against the *right* falsy-vs-null distinction. Getting the wiring right is `adopt-config.py
--write`'s job for a brand-new top-level root, and a person's job for everything else — this gate's
only job is turning "nobody wired it at all" from silence into a hard failure, for every marker,
including the ~80% whose wiring can never be generated (a shared guard, a computed default, a
batched dict literal) and therefore has no other net under it at all.

Enrolment is the same switch `check-config-bindings` reads: `bindings: true` in
`config-contract.yaml`. A chart that does not set it is not checked, for the same reason — opt-in
so a gate that failed unenrolled charts is not red for work nobody has scheduled.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_bindings as cb
from config_declaration import chart_dirs, load_declaration
from config_paths import CHARTS_DIR
from config_report import Report


def leaf_of(target: str) -> str:
    """The final path segment of a marker's target — the literal key a template has to render.

    `scheduler.chapter_rollup_verify_interval_secs` renders as a
    `chapter_rollup_verify_interval_secs` key nested under a `scheduler` table, and it is the
    leaf a template line actually spells — the dotted path itself never appears verbatim
    anywhere, YAML-nested or TOML-nested alike.
    """
    return target.rsplit(".", 1)[-1]


def template_text(chart_dir: Path) -> str:
    """Every `templates/*.tpl` file's text, concatenated, for one crude containment check."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(chart_dir.glob("templates/*.tpl"))
    )


def check_chart(chart_dir: Path, report: Report) -> int:
    """Check one chart's markers against its templates; 0 when the chart is not enrolled.

    Enrolment and marker parsing both come straight from `config_bindings.py`/
    `config_declaration.py` rather than a second reading of either file, for the reason
    `check-config-bindings.py` gives for the same choice: two readers of one format agree until
    the day one of them is not updated.
    """
    values_path = chart_dir / "values.yaml"
    if not values_path.is_file():
        return 0

    declaration = load_declaration(chart_dir)
    if declaration is None or not declaration.bindings:
        return 0

    markers = [
        marker
        for marker in cb.parse_values(values_path, chart_dir.name)
        if marker.cls in cb.KEY_CLASSES
    ]
    if not markers:
        return 0

    text = template_text(chart_dir)
    for marker in markers:
        leaf = leaf_of(marker.target)
        # Two idioms cover every hand-written chart: a YAML-style mapping key (`leaf: ...`), and
        # a Go-template `dict`/`set` call building the same table from quoted string arguments
        # (`dict "leaf" .Values.x`, `set $out "leaf" .`) — `tankovault`'s `branding` and
        # `telemetry.sentry` blocks are built entirely the second way, and a check for only the
        # first flags every key in both as unwired. A key rendered some third way is a false
        # negative this crude a check accepts, in exchange for never crying wolf on a real one.
        if f"{leaf}:" not in text and f'"{leaf}"' not in text:
            report.fail(
                marker.where,
                f"binds contract key {marker.target!r}, but no template under "
                f"{chart_dir.name}/templates/ renders a {leaf!r} key. The value exists and is "
                "typed, but nothing projects it — indistinguishable, to an operator, from the "
                "setting never having been added",
            )

    return len(markers)


def run(charts: Path, report: Report) -> list[tuple[str, int]]:
    """Check every enrolled chart; returns one `(chart, keys checked)` row per enrolment."""
    checked: list[tuple[str, int]] = []
    for chart_dir in chart_dirs(charts):
        count = check_chart(chart_dir, report)
        if count:
            checked.append((chart_dir.name, count))
    return checked


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Check that every `# @config` binding marker's key is wired into some template"
    )
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    args = parser.parse_args(argv)

    report = Report()
    try:
        checked = run(Path(args.charts), report)
    except cb.BindingError as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    for chart, count in checked:
        print(f"checked: {chart} ({count} bound key(s))")
    if not checked:
        print("==> no chart carries a `# @config` binding marker; nothing to check")

    report.print(sys.stdout, sys.stderr)

    if report.errors:
        print(f"\n{len(report.errors)} unwired configuration key(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
