#!/usr/bin/env python3
"""Derive the `@schema` blocks for the alert presets from the rules they act on.

`metrics.prometheusRule` carries seven presets, and every one of them is keyed by something the
chart already declares somewhere else: an alert name, a rule group name, a threshold in
`rules/tunables.yaml`. Written by hand into `values.yaml`, those lists are a fourth copy of a fact
that already had three, and the copy nothing regenerates is the one that goes stale.

So they are generated. This walks each chart's `rules/*.yml` and `rules/tunables.yaml` and rewrites
the `@schema` block above each preset key with constraints derived from them:

    disabledAlerts        items enumerate the alerts the chart ships
    disabledGroups        items enumerate its *alerting* groups, recording groups left out
    severityOverrides     propertyNames enumerate the alerts
    forOverrides          the same, plus a Prometheus duration pattern on the values
    additionalRuleLabels  propertyNames constrained to Prometheus' label grammar
    thresholds            every declared tunable, with its own type and bounds
    additionalRuleGroups  the shape of a Prometheus rule group

Only the block between the two `# @schema` fences is touched. The `# --` description under it is
prose a human wrote, helm-docs renders it into the chart README, and nothing here reads or
rewrites it.

Why here rather than in `values.schema.json`
--------------------------------------------
That file is helm-schema's output, and helm-schema is a Go program whose JSON serialisation this
cannot reproduce byte for byte — a post-processing pass would rewrite the whole file on every run
and the two writers would fight forever, each undoing the other's formatting. Editing the input
instead leaves exactly one writer of the schema, and `just schema` runs this first so helm-schema
sees the generated blocks.

What this buys over the render-time validation
----------------------------------------------
The chart already refuses an unknown alert name when it renders. That is the backstop, not the
first line: a schema rejection arrives from `helm install` before anything is applied, shows up in
an editor through the `# yaml-language-server` reference at the top of every values.yaml, and names
the values path rather than the rule. The two disagree only if this generator is stale, which
`--check` is here to prevent.

One hole is left open deliberately. `helm --set x=0.25` hands the chart a *string*, because Helm's
`--set` parser promotes integers and booleans and leaves every float as text, and JSON Schema's
`minimum` does not apply to strings. The string branch therefore carries a numeric `pattern`, which
rejects `abc` but cannot reject `99.9` where the maximum is 1. Bounds on the string spelling stay
the render-time check's job; a values *file* is typed by YAML and gets the full check here.

Usage:
  schema-presets.py --charts charts            rewrite the blocks
  schema-presets.py --charts charts --check    fail if any block is stale
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

# The keys this owns, in the order they appear in values.yaml. A key absent from a chart's
# values.yaml is skipped rather than inserted: which presets a chart exposes is the chart's
# decision, and this only maintains the ones it already made.
PRESET_KEYS = (
    "disabledGroups",
    "disabledAlerts",
    "additionalRuleLabels",
    "severityOverrides",
    "forOverrides",
    "thresholds",
    "additionalRuleGroups",
)

# Prometheus' own duration grammar: one or more count+unit pairs, `500ms`, `15m`, `1h30m`. Zero has
# to be written with a unit (`0m`), which is also what stops YAML turning it into an integer.
DURATION_PATTERN = "^([0-9]+(ms|[smhdwy]))+$"

# A label name as Prometheus accepts it. A key outside this is rejected by the rule loader, which
# would take down the whole group rather than the one label.
LABEL_NAME_PATTERN = "^[a-zA-Z_][a-zA-Z0-9_]*$"

# A number written as text, which is what `--set` produces for anything with a decimal point.
NUMERIC_STRING_PATTERN = "^-?[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$"

FENCE = "# @schema"


class Chart:
    """One chart's rule inventory, as the presets key on it."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.name = path.name
        self.alerts: list[str] = []
        self.alerting_groups: list[str] = []
        self.recording_groups: list[str] = []
        for rule_file in sorted((path / "rules").glob("*.yml")):
            doc = yaml.safe_load(rule_file.read_text(encoding="utf-8")) or {}
            for group in doc.get("groups") or []:
                rules = group.get("rules") or []
                names = [r["alert"] for r in rules if "alert" in r]
                self.alerts.extend(names)
                # A group is one or the other in every chart here, and `disabledGroups` refuses a
                # group holding recording rules — dashboards and other alerts read the series they
                # produce. Enumerating only the alerting groups puts that refusal in the schema too,
                # so the rejection arrives before the render rather than from it.
                (self.alerting_groups if names else self.recording_groups).append(group["name"])
        self.alerts.sort()
        self.alerting_groups.sort()
        self.tunables = self._tunables()

    def _tunables(self) -> dict[str, dict]:
        path = self.path / "rules" / "tunables.yaml"
        if not path.is_file():
            return {}
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return doc.get("tunables") or {}

    @property
    def values_path(self) -> pathlib.Path:
        return self.path / "values.yaml"


def flow_list(items: list[str], indent: str, per_line: int = 4) -> list[str]:
    """A YAML flow sequence wrapped over several lines.

    Forty alert names on one line is valid YAML and unreadable in a review. Flow style keeps the
    generated block a fraction of the height that block style would take while still diffing
    a few names at a time.
    """
    if not items:
        return ["[]"]
    if len(items) <= per_line and sum(len(i) + 2 for i in items) <= 72:
        return ["[" + ", ".join(items) + "]"]
    lines = ["["]
    for start in range(0, len(items), per_line):
        chunk = items[start : start + per_line]
        suffix = "," if start + per_line < len(items) else ""
        lines.append(f"{indent}  " + ", ".join(chunk) + suffix)
    lines.append(f"{indent}]")
    return lines


def scalar(value) -> str:
    """A Python value as YAML would spell it inside a flow mapping."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def threshold_branches(spec: dict) -> str:
    """The `anyOf` for one tunable: the typed form, and the string `--set` produces."""
    numeric = {"type": spec.get("type", "number")}
    for bound in ("minimum", "maximum"):
        if bound in spec:
            numeric[bound] = spec[bound]
    typed = ", ".join(f"{k}: {scalar(v)}" for k, v in numeric.items())
    return f"[{{{typed}}}, {{type: string, pattern: '{NUMERIC_STRING_PATTERN}'}}]"


def enum_lines(items: list[str], indent: str) -> list[str]:
    """`enum: [...]` at the given indent, wrapped over as many lines as it needs."""
    wrapped = flow_list(items, indent)
    return [f"{indent}enum: {wrapped[0]}"] + [f"{indent}{line}" for line in wrapped[1:]]


def _keyed_by_alert(chart: Chart, leaf: list[str]) -> list[str]:
    """A map whose keys are the alerts this chart ships, each value constrained by `leaf`.

    Spelled out per alert rather than as `propertyNames` + `additionalProperties`, which says the
    same thing far more briefly — helm-schema drops `propertyNames` on the floor, and a constraint
    that vanishes between the input and the schema is worse than one never written, because the
    values.yaml still claims it. Enumerating also earns something the shorter form does not: an
    editor offers the alert names as completions rather than only rejecting the wrong ones.
    """
    lines = ["type: object", "additionalProperties: false"]
    if not chart.alerts:
        return lines
    lines.append("properties:")
    for alert in chart.alerts:
        lines.append(f"  {alert}:")
        lines.extend(f"    {line}" for line in leaf)
    return lines


def build_block(key: str, chart: Chart) -> list[str]:
    """The YAML body of one preset's `@schema` block, as lines without the comment prefix."""
    if key == "disabledAlerts":
        return ["type: array", "items:", *enum_lines(chart.alerts, "  ")]

    if key == "disabledGroups":
        return ["type: array", "items:", *enum_lines(chart.alerting_groups, "  ")]

    if key == "additionalRuleLabels":
        # The keys here are label names an operator invents, not names this chart knows, so there
        # is nothing to enumerate. `propertyNames` would constrain them to Prometheus' grammar but
        # helm-schema drops the keyword, so the rule loader remains the only thing that checks it.
        return ["type: object", "additionalProperties:", "  type: string"]

    if key == "severityOverrides":
        return _keyed_by_alert(chart, ["type: string", "minLength: 1"])

    if key == "forOverrides":
        return _keyed_by_alert(chart, ["type: string", f"pattern: '{DURATION_PATTERN}'"])

    if key == "thresholds":
        # `additionalProperties: false` at both levels is what turns a typo into a rejection
        # instead of a silently ignored key.
        lines = ["type: object", "additionalProperties: false"]
        if not chart.tunables:
            return lines
        lines.append("properties:")
        for alert in sorted(chart.tunables):
            declarations = chart.tunables[alert] or {}
            lines.append(f"  {alert}:")
            lines.append("    type: object")
            lines.append("    additionalProperties: false")
            lines.append("    properties:")
            for tunable in sorted(declarations):
                spec = declarations[tunable] or {}
                lines.append(f"      {tunable}:")
                if "default" in spec:
                    lines.append(f"        default: {scalar(spec['default'])}")
                lines.append(f"        anyOf: {threshold_branches(spec)}")
        return lines

    if key == "additionalRuleGroups":
        return [
            "type: array",
            "items:",
            "  type: object",
            "  required: [name, rules]",
            "  properties:",
            "    name:",
            "      type: string",
            "      minLength: 1",
            "    interval:",
            "      type: string",
            f"      pattern: '{DURATION_PATTERN}'",
            "    limit:",
            "      type: integer",
            "      minimum: 0",
            "    rules:",
            "      type: array",
            "      minItems: 1",
            "      items:",
            "        type: object",
        ]

    raise KeyError(key)


def rewrite(text: str, chart: Chart) -> tuple[str, list[str]]:
    """Replace the `@schema` body above each preset key. Returns the new text and what changed."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    changed: list[str] = []

    for key in PRESET_KEYS:
        # The key as it appears under `prometheusRule:`. Matched anchored so a mention of the name
        # inside a description cannot be mistaken for the declaration.
        pattern = re.compile(rf"^(\s+){re.escape(key)}:(\s|$)")
        hits = [i for i, line in enumerate(lines) if pattern.match(line)]
        if not hits:
            continue
        if len(hits) > 1:
            raise SystemExit(
                f"{chart.values_path}: `{key}` is declared {len(hits)} times; this rewrites a key "
                f"it can identify uniquely."
            )
        index = hits[0]
        indent = pattern.match(lines[index]).group(1)

        # Walk back over the `# --` description to the closing fence, then to the opening one.
        cursor = index - 1
        while cursor >= 0:
            stripped = lines[cursor].strip()
            if stripped == FENCE or not stripped.startswith("#"):
                break
            cursor -= 1
        if cursor < 0 or lines[cursor].strip() != FENCE:
            raise SystemExit(f"{chart.values_path}: `{key}` has no `@schema` block above it.")
        close = cursor
        cursor -= 1
        while cursor >= 0 and lines[cursor].strip() != FENCE:
            if not lines[cursor].strip().startswith("#"):
                raise SystemExit(
                    f"{chart.values_path}: `{key}` has an unterminated `@schema` block."
                )
            cursor -= 1
        if cursor < 0:
            raise SystemExit(f"{chart.values_path}: `{key}` has an unterminated `@schema` block.")
        open_fence = cursor

        body = [f"{indent}# {line}".rstrip() for line in build_block(key, chart)]
        if lines[open_fence + 1 : close] != body:
            changed.append(key)
            lines[open_fence + 1 : close] = body

    return newline.join(lines), changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts", required=True, type=pathlib.Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report stale blocks and exit non-zero instead of rewriting them",
    )
    args = parser.parse_args()

    stale: list[str] = []
    touched = 0
    for path in sorted(args.charts.iterdir()):
        if not (path / "rules").is_dir() or not (path / "values.yaml").is_file():
            continue
        chart = Chart(path)
        raw = chart.values_path.read_bytes().decode("utf-8")
        updated, changed = rewrite(raw, chart)
        if not changed:
            continue
        if args.check:
            stale.append(f"  - {chart.name}: {', '.join(changed)}")
            continue
        # Byte-level write. These files are CRLF in a Windows checkout and text mode would rewrite
        # every line ending in the file to make one block current.
        chart.values_path.write_bytes(updated.encode("utf-8"))
        touched += 1
        print(f"==> {chart.name}: regenerated {', '.join(changed)}")

    if stale:
        print(
            "The alert preset `@schema` blocks no longer match the rules they constrain:\n"
            + "\n".join(stale)
            + "\n\nThe lists are derived from `rules/*.yml` and `rules/tunables.yaml`, so a rule "
            "added, renamed or\nretired leaves them behind. Run `just schema` to regenerate, and "
            "commit the result.",
            file=sys.stderr,
        )
        return 1
    if not args.check and not touched:
        print("==> alert preset schemas already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
