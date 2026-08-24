#!/usr/bin/env python3
"""Fail when a chart value carries no `# --` description.

The `# --` comments in `values.yaml` are the user-facing documentation of these charts. helm-docs
renders them into every chart's README table and helm-schema copies them into
`values.schema.json`, which is what an editor shows on hover — so a value without one is a value
an operator meets with no explanation in either place.

Nothing enforced that. Coverage was complete for the values a reader *sets* and absent for the
mappings that hold them: 134 grouping keys — `image`, `serviceAccount`, `resources`, every
`services.<name>.service` in tankovault — had no description of their own, and their
`values.schema.json` properties were emitted with an empty one. This gate is what stops the next
undocumented block landing on a Tuesday.

--------------------------------------------------------------------------------------------
Which keys are required to carry one
--------------------------------------------------------------------------------------------

**A key that has an `@schema` block, or a key that has children.** Both halves are needed and
neither is arbitrary.

An `@schema` block means the key is a value somebody sets: helm-schema emits a schema property for
it, and a property with no description is the hover that says nothing. Having children means the
key names a group, which is exactly what was missing everywhere — helm-docs gives a documented
group its own README row, above the rows of the values inside it.

What is left out by the same rule is the *content* of a documented mapping. `kubernetes.io/
metadata.name: kube-system` under a documented `namespaceSelector`, the seven service names under
`internal.tls.existingSecrets`, the `cpu` and `memory` numbers inside a `resources` block written
without `@schema` blocks: no schema property, no children, and a parent whose description already
says what the whole mapping means. helm-docs renders no row for any of them either — it stops
descending at the description. A rule that demanded one would produce `# -- The name.` above
`name`, which costs a line and teaches the next reader that these comments are decoration.

--------------------------------------------------------------------------------------------
Why this parser reads text rather than YAML
--------------------------------------------------------------------------------------------

The same reason `config_bindings` does: PyYAML discards comments, and the description *is* a
comment. `yaml.safe_load` returns a mapping in which no `# --` has ever existed.

The description has to be the last comment run directly above the key, with no blank line between
it and the key — the placement helm-docs and helm-schema both require, measured and recorded in
`config_bindings`' header. This reader therefore walks back from the key over contiguous comment
lines and stops at anything else, blank lines included, so a description that has drifted away
from its value reads as missing here rather than passing a check it would fail in the generators.

Usage: python3 .github/scripts/check-values-docs.py
       python3 .github/scripts/check-values-docs.py --charts charts --chassis <path>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_paths import CHARTS_DIR

CHASSIS = Path(".github/templates/chart/values.chassis.yaml")

# A mapping key and its indentation. List entries (`- name: x`) are not matched on purpose: a list
# item is content of the list, and the list is the documented thing.
_KEY = re.compile(r"^(\s*)([A-Za-z0-9_.\-/]+):(\s.*)?$")

# The line that opens and closes a helm-schema block.
_SCHEMA = "# @schema"

# What a block scalar header ends with, after which the indented lines below are text rather than
# YAML and can hold anything, `key: value` included.
_BLOCK_SCALARS = ("|", ">", "|-", ">-", "|+", ">+")


class Key:
    """One mapping key in a values file, with what the lines around it say about it."""

    def __init__(self, line: int, indent: int, name: str) -> None:
        self.line = line
        self.indent = indent
        self.name = name
        self.path = name
        self.described = False
        self.schema = False
        self.children = False


def keys(text: str) -> list[Key]:
    """Every mapping key in a values file, in file order, with its dotted path."""
    lines = text.splitlines()
    found: list[Key] = []
    block_indent: int | None = None

    for number, line in enumerate(lines):
        if not line.strip():
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Inside a block scalar nothing is YAML. It ends at the first line indented no further
        # than the key that opened it.
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None

        if stripped.startswith("#"):
            continue

        match = _KEY.match(line)
        if not match:
            continue

        if (match.group(3) or "").strip() in _BLOCK_SCALARS:
            block_indent = indent

        key = Key(number + 1, indent, match.group(2))

        # The comment run directly above, read from the key upwards. `# --` opens the description
        # and `# @schema` delimits the schema block; a blank line or any other content ends the
        # run, because that is where the generators stop reading too.
        above = number - 1
        while above >= 0 and lines[above].lstrip().startswith("#"):
            comment = lines[above].lstrip()
            if comment.startswith("# --"):
                key.described = True
            elif comment.startswith(_SCHEMA):
                key.schema = True
            above -= 1

        found.append(key)

    stack: list[Key] = []
    for key in found:
        while stack and stack[-1].indent >= key.indent:
            stack.pop()
        if stack:
            stack[-1].children = True
            key.path = f"{stack[-1].path}.{key.name}"
        stack.append(key)

    return found


def undocumented(path: Path) -> list[Key]:
    """The keys in one values file that owe a description and do not have one."""
    return [
        key
        for key in keys(path.read_text(encoding="utf-8"))
        if (key.schema or key.children) and not key.described
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    parser.add_argument("--chassis", default=str(CHASSIS))
    args = parser.parse_args(argv)

    charts = Path(args.charts)
    if not charts.is_dir():
        print(f"error: {charts}: no such directory", file=sys.stderr)
        return 1

    files = sorted(charts.glob("*/values.yaml"))

    # The scaffold `just new-chart` copies from. Checked with the charts, so a block that lands
    # there undocumented is caught once rather than in every chart created from it afterwards.
    chassis = Path(args.chassis)
    if chassis.is_file():
        files.append(chassis)

    missing = 0
    for path in files:
        found = undocumented(path)
        if not found:
            print(f"documented: {path}")
            continue

        missing += len(found)
        print(f"undocumented: {path} ({len(found)} value(s))")
        for key in found:
            print(f"    {path}:{key.line}: {key.path}")

    if missing:
        print(
            f"\n==> {missing} value(s) carry no `# --` description.\n"
            "    Add one directly above the key, below the closing `# @schema`, with no blank\n"
            "    line between it and the key. It is what the chart README and the hover text in\n"
            "    an editor show for that value.",
            file=sys.stderr,
        )
        return 1

    print(f"\n==> every value in {len(files)} file(s) carries a description")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
