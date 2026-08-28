#!/usr/bin/env python3
"""Declare a threshold in `rules/tunables.yaml`, deriving its anchor from the rule.

Making a threshold tunable is three lines of YAML and one hard part: the `anchor`, the substring of
the alert's expression that contains the number and occurs exactly once. Getting it wrong is not
subtle — an anchor matching nothing makes the override a no-op, an anchor matching twice rewrites a
comparison the author did not mean — but getting it *right* means hand-copying a slice of PromQL
and counting occurrences, which is exactly the sort of thing done wrong at the end of an afternoon.

So it is derived. Point this at an alert and a number and it finds the narrowest window that names
what is being compared, checks it is unique, and writes the declaration.

    just add-tunable tankovault TankoVaultDatabasePoolSaturated saturation 0.9

With no literal it lists what the alert compares against, with the anchor each would get:

    just add-tunable tankovault TankoVaultDatabasePoolSaturated

Numbers that are not thresholds are listed too — this cannot know that `2.5` is a histogram bucket
boundary and must never be offered. That judgement stays with the author, and `rules/tunables.yaml`
has a section explaining which numbers in these rules are deliberately not tunable. Read it before
adding one.

The generated entry carries a `description:` placeholder on purpose. Every other declaration in the
file says what the threshold means and what moving it costs, that prose is what an operator reads
in the chart README, and a generator cannot write it.

After writing, run `just schema` — the values schema enumerates the declared tunables and is stale
until it is regenerated.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from rule_anchors import derive_anchor, literal_positions, numeric_literals


def load_alerts(chart: pathlib.Path) -> dict[str, str]:
    """Every alert the chart ships, mapped to its expression as committed."""
    alerts: dict[str, str] = {}
    for path in sorted((chart / "rules").glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for group in doc.get("groups") or []:
            for rule in group.get("rules") or []:
                if "alert" in rule:
                    alerts[rule["alert"]] = str(rule["expr"])
    return alerts


def declared(chart: pathlib.Path) -> dict[str, dict]:
    path = chart / "rules" / "tunables.yaml"
    if not path.is_file():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("tunables") or {}


def infer_bounds(literal: str, anchor: str) -> tuple[str, list[str]]:
    """A type and bounds worth defaulting to, given how the number is being compared.

    Deliberately timid. A ratio is bounded by 0 and 1 and saying so in the schema turns a
    fat-fingered `9` into a rejection rather than an alert that never fires, but everything else
    gets a floor and no ceiling — a request rate, a task count and a staleness window have no upper
    bound this can know, and inventing one would refuse a value that is merely unusual.
    """
    kind = "number" if "." in literal else "integer"
    value = float(literal)
    if value < 0:
        return kind, []

    # A count is normally floored at one — a threshold of "at least zero tasks" is satisfied by
    # every series and disarms the alert. But the floor can never sit above the default it is
    # bounding: `> 0` is a real, deliberate threshold in several of these rules, and pairing it
    # with `minimum: 1` would ship a schema that rejects the chart's own default.
    floor = 1 if kind == "integer" and value >= 1 else 0
    bounds = [f"minimum: {floor}"]

    looks_like_ratio = any(word in anchor for word in ("ratio", "share", "saturation", "_used"))
    if looks_like_ratio and kind == "number" and 0 <= value <= 1:
        bounds.append("maximum: 1")
    return kind, bounds


def entry_lines(name: str, kind: str, bounds: list[str], literal: str, anchor: str) -> list[str]:
    """One tunable declaration, indented as it sits under its alert."""
    lines = [
        f"    {name}:",
        "      # -- TODO: one line for the chart README, in the form",
        "      # `Fraction of ... above which ...`.",
        "      description: >-",
        "        TODO: what this threshold means, what moving it costs, and what an",
        "        operator should look at before reaching for it.",
        f"      type: {kind}",
        f"      default: {literal}",
    ]
    lines.extend(f"      {bound}" for bound in bounds)
    quoted = "'" + anchor.replace("'", "''") + "'"
    lines.append(f"      anchor: {quoted}")
    return lines


def insert(text: str, alert: str, lines: list[str]) -> str:
    """Place the declaration under its alert, or start a block for it at the end of the file."""
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = text.split(newline)

    header = f"  {alert}:"
    if header in rows:
        start = rows.index(header)
        end = start + 1
        while end < len(rows) and (not rows[end].strip() or rows[end].startswith("    ")):
            end += 1
        # Back up over trailing blank lines so the entry lands inside the block, not after it.
        while end > start + 1 and not rows[end - 1].strip():
            end -= 1
        rows[end:end] = ["", *lines]
        return newline.join(rows)

    while rows and not rows[-1].strip():
        rows.pop()
    rows.extend(["", header, *lines, ""])
    return newline.join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts", default="charts", type=pathlib.Path)
    parser.add_argument("--chart", required=True)
    parser.add_argument("--alert", required=True)
    parser.add_argument("--name", help="the tunable's key under the alert, e.g. `ratio`")
    parser.add_argument(
        "--literal", help="the number to make tunable, spelled as the rule spells it"
    )
    parser.add_argument(
        "--write", action="store_true", help="write the declaration into tunables.yaml"
    )
    args = parser.parse_args()

    chart = args.charts / args.chart
    if not (chart / "rules").is_dir():
        print(f"{args.chart} ships no `rules/` directory.", file=sys.stderr)
        return 2

    alerts = load_alerts(chart)
    if args.alert not in alerts:
        print(
            f"{args.chart} ships no alert named `{args.alert}`. It ships:\n  "
            + "\n  ".join(sorted(alerts)),
            file=sys.stderr,
        )
        return 2
    expr = alerts[args.alert]

    if not args.literal:
        print(f"{args.alert} compares against:\n")
        existing = {
            str(spec.get("default")) for spec in (declared(chart).get(args.alert) or {}).values()
        }
        for literal, position in numeric_literals(expr):
            anchor = derive_anchor(expr, literal, position)
            note = "  (already declared)" if literal in existing else ""
            shown = anchor or "— not addressable; the rule needs rewriting"
            print(f"  {literal}{note}")
            print(f"      anchor: {shown}")
        print(
            "\nPick one and re-run with its value and a name:\n"
            f"  just add-tunable {args.chart} {args.alert} <name> <value>\n\n"
            "Not every number here is a threshold. Read the header of "
            f"{args.chart}/rules/tunables.yaml\nbefore adding one — bucket boundaries and "
            "structural comparisons are deliberately not tunable."
        )
        return 0

    if not args.name:
        print("--name is required once --literal is given.", file=sys.stderr)
        return 2

    if args.name in (declared(chart).get(args.alert) or {}):
        print(
            f"`{args.alert}.{args.name}` is already declared in {args.chart}/rules/tunables.yaml.",
            file=sys.stderr,
        )
        return 2

    positions = literal_positions(expr, args.literal)
    if not positions:
        print(
            f"{args.alert} does not compare against `{args.literal}`. Run without a value to see "
            f"what it does compare against.",
            file=sys.stderr,
        )
        return 2

    anchors = [a for a in (derive_anchor(expr, args.literal, p) for p in positions) if a]
    if not anchors:
        print(
            f"`{args.literal}` occurs in {args.alert} but no unique window contains it: the "
            f"expression compares the same operand against it more than once, so an override "
            f"could not tell which one it meant. The rule has to be rewritten before this "
            f"threshold can be made tunable.",
            file=sys.stderr,
        )
        return 2
    if len({*anchors}) > 1:
        print(
            f"`{args.literal}` occurs {len(positions)} times in {args.alert}, meaning something "
            f"different each time. Which one is the threshold is your call — declare it by hand "
            f"with one of:\n  "
            + "\n  ".join(f"anchor: {a}" for a in anchors),
            file=sys.stderr,
        )
        return 2

    anchor = anchors[0]
    kind, bounds = infer_bounds(args.literal, anchor)
    lines = entry_lines(args.name, kind, bounds, args.literal, anchor)

    path = chart / "rules" / "tunables.yaml"
    if not args.write:
        print(f"# add to {path}, under `tunables:`\n")
        print(f"  {args.alert}:")
        print("\n".join(lines))
        print("\n# Re-run with --write to insert it, then `just schema`.")
        return 0

    original = path.read_bytes().decode("utf-8")
    updated = insert(original, args.alert, lines)
    # Byte-level, so a CRLF checkout keeps its line endings.
    path.write_bytes(updated.encode("utf-8"))
    print(
        f"==> declared {args.alert}.{args.name} in {path}\n"
        f"    anchor: {anchor}\n\n"
        f"Now fill in the `description:` placeholder and run `just schema`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
