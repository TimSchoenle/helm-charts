#!/usr/bin/env python3
"""Name the chart lines a contract change leaves behind, which no generator here edits.

`just sync-config` turns an image release into chart values, and it does that for exactly one kind
of change: a key the chart does not yet surface. `adopt-config.py` writes the value, `config_shapes`
rewrites the `@schema` block of a value that already exists, and the chain regenerates everything
derived from both. None of it touches a template, a hand-written test suite or a CI values file —
deliberately, for the reason `adopt-config.py` gives: an edit made blind to a template's `with`
blocks and `if` gates is one nobody can review.

So a change that *takes something away* reaches none of them:

    a key removed                  `legal.dir` — the template still renders `dir:`, and the
                                   service refuses to boot on it
    an element property removed    `legal.documents.*.sources` — the regenerated schema is closed,
                                   so every test and CI file that sets it fails the render, and
                                   the template still reads it

Measured on the `tankovault` 11 bump: `sync-config` wrote the new values and regenerated the
schema correctly, then died inside `update-snapshots` on six schema refusals in
`tests/legal_test.yaml`, with nothing saying that the chart's own legal surface was the thing still
on the old contract. This is the sentence that was missing, printed before anything is written.

It reads `terrace-contract diff --json` and the chart's markers *at the compared revision* — the
value a removed key was bound to has often been deleted by the time anyone looks — and reports each
line under `templates/`, `tests/` and `ci/` that still names what went away. The generated
round-trip suites are skipped: `just contract-tests` rewrites them from the contract.

Deliberately textual, like `check-config-wired.py` and for the same reason: nothing here parses Go
templates, so a line-based search is the only reader that cannot silently disagree with itself. A
hit is a place to look, not a proof the line is wrong; no hit for a removed property is not a proof
of safety either, which is why each finding prints even when it has no reference.

Exit status: 0 nothing left to migrate, 2 a chart still references something removed, 1 could not
answer — the same three-way split `terrace-contract diff --exit-code` uses, so a caller can tell
"work to do" from "broken".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_bindings as cb
from config_paths import CHARTS_DIR

# Generated from the contract by `just contract-tests`, so a stale reference in one is repaired by
# regenerating, not by hand.
GENERATED_SUITE = re.compile(r"^contract_roundtrip_.*_test\.yaml$")


@dataclass(frozen=True)
class Reference:
    """One line that still names something the contract took away."""

    path: Path
    line: int
    text: str


@dataclass
class Migration:
    """One contract change the chart has to follow by hand."""

    chart: str
    key: str
    what: str
    values_path: str | None
    references: list[Reference] = field(default_factory=list)


def removed_properties(old: Any, new: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every `properties` entry `old` declares at a position `new` no longer does.

    Positions are walks of schema keywords — `('additionalProperties', 'properties', 'sources')` —
    the vocabulary `@config-shape-narrow` already uses for a sub-path. Only positions both
    documents describe are compared below the removal, so a property whose whole parent vanished
    is reported once, at the parent.
    """
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    found: list[tuple[str, ...]] = []
    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}
    for name in sorted(old_props):
        where = (*prefix, "properties", name)
        if name not in new_props:
            found.append(where)
        else:
            found.extend(removed_properties(old_props[name], new_props[name], where))
    for keyword in ("items", "additionalProperties"):
        found.extend(removed_properties(old.get(keyword), new.get(keyword), (*prefix, keyword)))
    return found


def set_pattern(values_path: str, position: tuple[str, ...]) -> re.Pattern[str]:
    """The dotted `--set` spelling of a schema position under a chart value.

    `legal.documents` and `('additionalProperties', 'properties', 'sources')` become
    `legal.documents.<any>.sources`, which is how a helm-unittest `set:` block and a template's
    `.Values` access both spell it.
    """
    parts = [re.escape(values_path)]
    for keyword in position:
        if keyword == "properties":
            continue
        if keyword == "additionalProperties":
            parts.append(r"[^.\s:\"']+")
        elif keyword == "items":
            parts[-1] += r"(?:\[\d+\])?"
        else:
            parts.append(re.escape(keyword))
    return re.compile(r"(?<![\w-])" + r"\.".join(parts) + r"(?![\w-])")


def field_pattern(name: str) -> re.Pattern[str]:
    """A field read straight off a range variable (`$doc.sources`), or a dict key (`"sources"`).

    Straight off the variable, because that is how a template holds one element of a map or a
    list, and because a deeper path on some other variable — `$tls.ca.bundle.sources` in a
    template that also reads `legal.documents` — is a different setting that happens to share the
    name. Measured on `tankovault`: the looser `.sources` reported exactly that line, and a
    finding nobody can clear would keep this red on every branch after the migration.
    """
    token = re.escape(name)
    return re.compile(rf"\$\w+\.{token}(?![\w-])|\"{token}\"")


def yaml_key_pattern(name: str) -> re.Pattern[str]:
    """A mapping key (`sources:`) in a values file."""
    return re.compile(rf"^\s*{re.escape(name)}\s*:")


def candidate_files(chart_dir: Path) -> list[Path]:
    """The hand-written files a contract change can leave behind."""
    files = sorted(chart_dir.glob("templates/**/*"))
    files += sorted(
        path
        for path in chart_dir.glob("tests/*.yaml")
        if not GENERATED_SUITE.match(path.name)
    )
    files += sorted(chart_dir.glob("ci/*.yaml"))
    return [path for path in files if path.is_file()]


def lines_of(path: Path, text: str, pattern: re.Pattern[str]) -> list[Reference]:
    """Every line of `text` (read from `path`) that `pattern` matches."""
    return [
        Reference(path, number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def key_references(files: list[Path], values_path: str) -> list[Reference]:
    """Every line naming the chart value a removed key was bound to."""
    dotted = re.compile(rf"(?<![\w-]){re.escape(values_path)}(?![\w-])")
    found: list[Reference] = []
    for path in files:
        found.extend(lines_of(path, path.read_text(encoding="utf-8"), dotted))
    return found


def property_references(
    files: list[Path], values_path: str, position: tuple[str, ...]
) -> list[Reference]:
    """Every line still using an element property removed at `position` under `values_path`.

    Each kind of file spells it differently, so each gets the pattern for its own spelling, and a
    file that never reads the value is searched only for the dotted form — which is what keeps a
    projected volume's `sources:` out of a report about `legal.documents.*.sources`.
    """
    name = position[-1]
    dotted = set_pattern(values_path, position)
    reads_value = re.compile(rf"Values\.{re.escape(values_path)}(?![\w-])")
    top_block = re.compile(rf"^{re.escape(values_path.split('.')[0])}\s*:", re.MULTILINE)
    either = {
        "templates": field_pattern(name),
        "ci": yaml_key_pattern(name),
    }

    found: list[Reference] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        kind = path.parent.name
        # A template that reads the value holds its element in a range variable; a CI values
        # file spells the value nested, never dotted, under the value's top-level block.
        if (kind == "templates" and reads_value.search(text)) or (
            kind == "ci" and top_block.search(text)
        ):
            pattern = re.compile(f"{dotted.pattern}|{either[kind].pattern}", re.MULTILINE)
        else:
            pattern = dotted
        found.extend(lines_of(path, text, pattern))
    return found


def markers_at(chart_dir: Path, ref: str) -> list[cb.Marker]:
    """The chart's markers as `values.yaml` stood at `ref`; empty when it did not exist there."""
    shown = subprocess.run(
        ["git", "show", f"{ref}:./values.yaml"],
        cwd=chart_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if shown.returncode != 0:
        return []
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "values.yaml"
        path.write_text(shown.stdout, encoding="utf-8")
        return cb.parse_values(path, chart_dir.name)


def value_for(markers: list[cb.Marker], key: str) -> str | None:
    """The chart value bound to contract key `key`, if a marker named it."""
    for marker in markers:
        if marker.target == key:
            return marker.values_path
    return None


def migrations_for(chart_dir: Path, changes: list[dict[str, Any]], since: str) -> list[Migration]:
    """The hand migrations one chart's key changes call for."""
    relevant = [
        change
        for change in changes
        if change.get("area") == "key"
        and (
            change.get("kind") == "removed"
            or (change.get("kind") == "changed" and change.get("field") == "constraint")
        )
    ]
    if not relevant:
        return []

    markers = markers_at(chart_dir, since)
    files = candidate_files(chart_dir)
    found: list[Migration] = []

    for change in relevant:
        key = change["subject"]
        values_path = value_for(markers, key)

        if change["kind"] == "removed":
            migration = Migration(chart_dir.name, key, "key removed", values_path)
            if values_path:
                migration.references = key_references(files, values_path)
            found.append(migration)
            continue

        # A `constraint` change carries the two constraints themselves, not the two entries.
        for position in removed_properties(change.get("old"), change.get("new")):
            migration = Migration(
                chart_dir.name, key, f"element property {position[-1]!r} removed", values_path
            )
            if values_path:
                migration.references = property_references(files, values_path, position)
            found.append(migration)

    return found


def run(diff: dict[str, Any], charts: Path, since: str) -> list[Migration]:
    """Every hand migration across the charts a `terrace-contract diff --json` document covers."""
    found: list[Migration] = []
    for chart in diff.get("charts", []):
        chart_dir = charts / chart["chart"]
        if not chart_dir.is_dir():
            continue
        changes = [
            change
            for contract in chart.get("contracts", [])
            for change in contract.get("changes", [])
        ]
        found.extend(migrations_for(chart_dir, changes, since))
    return found


def render(migrations: list[Migration], root: Path) -> str:
    """The report a person reads before deciding what the templates should now say.

    Findings with a remaining reference come first and are the failure. The rest are what the
    contract took away that nothing in the chart still names — listed so a reader can confirm the
    migration is done, and quiet once it is, so a branch that has followed its contract is not red
    for the rest of its life.
    """
    open_ = [migration for migration in migrations if migration.references]
    done = [migration for migration in migrations if not migration.references]
    lines: list[str] = []
    if open_:
        lines += [
            "==> hand migration required: the contract took these away, and no generator edits",
            "    the templates, hand-written tests or CI values that still use them",
        ]
        for migration in open_:
            lines.append(f"  {headline(migration)}")
            for ref in migration.references:
                try:
                    shown = ref.path.resolve().relative_to(root.resolve())
                except ValueError:
                    shown = ref.path
                lines.append(f"      {shown.as_posix()}:{ref.line}: {ref.text}")
    if done:
        if lines:
            lines.append("")
        lines.append("==> removed by the contract, and no longer referenced by the chart:")
        for migration in done:
            suffix = "" if migration.values_path else " (no chart value was bound to it)"
            lines.append(f"  {headline(migration)}{suffix}")
    return "\n".join(lines)


def headline(migration: Migration) -> str:
    """`chart: key: what (chart value path)`, the first line of every finding."""
    bound = f" (chart value {migration.values_path})" if migration.values_path else ""
    return f"{migration.chart}: {migration.key}: {migration.what}{bound}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Name the chart files a contract change leaves behind"
    )
    parser.add_argument(
        "diff", help="a `terrace-contract diff --json` document, or `-` for standard input"
    )
    parser.add_argument("--since", required=True, help="the revision the diff compared against")
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    args = parser.parse_args(argv)

    try:
        text = sys.stdin.read() if args.diff == "-" else Path(args.diff).read_text(encoding="utf-8")
        diff = json.loads(text)
        migrations = run(diff, Path(args.charts), args.since)
    except (OSError, json.JSONDecodeError, cb.BindingError, KeyError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    if not migrations:
        print("==> no contract change removes anything a chart uses")
        return 0

    print(render(migrations, Path.cwd()))
    return 2 if any(migration.references for migration in migrations) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
