#!/usr/bin/env python3
"""Read a chart's vendored contracts back out, and say what the images it pins actually consume.

Every other consumer of `charts/<chart>/contracts/` is a gate: it takes a rendered manifest, holds
it against the contract and reports the difference. Nothing reads the contract for its own sake —
so the one document in this repository that states, per setting, what the binary calls it, what it
accepts, what it defaults to and whether a file may supply it is only ever consulted by a program.
An operator asking "what can I put in `config.toml`, and why is the value I set being ignored?"
has the chart's `values.yaml`, the chart's README and a 400 KB JSON file, and the answer is in the
JSON file.

This is that file, read out loud. Offline, read-only, no render, no network: it opens the same
committed contracts `just check-config` does, through the same declaration and the same staleness
interlock, and prints them.

Three decisions are worth stating, because each of them is a place where the obvious
implementation says something untrue:

**Readers are derived, never listed.** `tankovault` pins nine images across nine documents, and
which of them reads a given setting exists nowhere in this repository except in the nine contracts
themselves — not in the chart, not in the declaration, not in the README. It is also the fact this
command exists to produce: `internal.peers` being read by three services and `bind_addr` by all
nine is what tells an operator whether a change is local or fleet-wide. So the attribution is
computed by walking the contracts the declaration binds, and a hand-written map is exactly the
thing that would be wrong within one release.

**The merge here is tolerant where `union_contracts` is strict, and that is not a relaxation of
the rule.** `cc.union_contracts` refuses two contracts that describe one key differently, because
its output validates a document that all of them read — an unreconcilable pair there means the
gate has nothing trustworthy to say. It cannot be reused across a chart's *documents*: nine
separate `config.toml` files legitimately carry nine different `json_schema.title`, and merging
them is not a question anyone asked. What this command wants is the opposite posture — a
disagreement between two images about one setting is a finding, not a reason to print nothing —
so the merge below keeps every image's description, shows the divergence, and reports it as a
warning. `tankovault` v8.1.0 has seven, all on `docs`, and no gate can currently see them.

**Loader variables get a section of their own**, beside the external ones the task asks for.
`TANKOVAULT_CONFIG` and `TANKOVAULT_SECRETS_DIR` are neither settings nor foreign variables: they
are what decides which file the settings come from, and an explanation of a configuration surface
that omits how the surface is located is an explanation an operator cannot act on.

Deliberately omitted: the merged `json_schema`. It is the same information as the key list in a
form built for a validator rather than a person, `just check-config` is what consumes it, and
printing 400 KB of it would bury the part that answers the question.

Exit status, which is a decision rather than an accident:

    0   the selection was printed
    1   a pattern was given and matched nothing
    2   no such chart, or a chart with no contract to read
    3   the staleness interlock refused: the vendored contract is not for the pinned digest

`1` is `grep`'s convention and it is the right one here. The question this command is asked is
"does this image read this setting?", a pattern that matches nothing is the answer "no", and an
answer that is indistinguishable from a typo in the pattern is not an answer. `just` prints its
own failure line after a non-zero exit; the message this script prints first says what happened.

Usage: python3 .github/scripts/explain-config.py
       python3 .github/scripts/explain-config.py tankovault
       python3 .github/scripts/explain-config.py tankovault tls
       python3 .github/scripts/explain-config.py tankovault 'internal.*' --json
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_contract as cc
from config_declaration import (
    Declaration,
    DeclarationError,
    bind,
    declared,
    load_declaration,
)
from config_paths import CHARTS_DIR, read_yaml
from config_report import Report, warning

# The contracts' prose is UTF-8 and uses it — em dashes, arrows, typographic quotes — and this
# repository is developed from Git Bash on Windows, where Python's default console encoding is the
# system code page. Left alone, every one of those characters raises `UnicodeEncodeError` and the
# command dies part-way through a paragraph. Reconfiguring the streams is the whole fix, and
# `errors="replace"` is the floor under it: a terminal that genuinely cannot render a character
# should show a placeholder, never truncate the explanation.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Width the prose is re-wrapped to. `docs` arrives hard-wrapped at the producer's own width, which
# is not this one, so paragraphs are re-flowed rather than printed as they came.
WIDTH = 96

# Column ceilings for the compact listing. The columns size themselves to the selection — a
# filtered listing is narrower than the whole chart — and these only stop one 60-character type
# from setting the width for 167 rows.
MAX_PATH = 46
MAX_TYPE = 30
MAX_DEFAULT = 22

# Fields whose representative value is the first *non-empty* one across the images that read a
# setting. 22 of this repository's 491 declarations carry an empty `docs`, and an image that says
# nothing about a key it shares has not contradicted the one that does.
PROSE_FIELDS = ("docs", "note")

# Constraint keywords rendered as prose, in the order they read best. Anything outside this list
# is printed as `keyword=<json>` rather than dropped: under-reporting a constraint is the failure
# this whole pipeline exists to remove, and it would be no better coming from the explainer.
CONSTRAINT_ORDER = (
    "type",
    "const",
    "enum",
    "pattern",
    "format",
    "minimum",
    "exclusiveMinimum",
    "maximum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "required",
    "properties",
    "additionalProperties",
    "items",
)

# Keywords `describe_constraint` folds into another keyword's phrase, or that are said elsewhere
# in the entry. Listed so the catch-all below can tell "already reported" from "unrecognised".
CONSTRAINT_FOLDED = frozenset(
    {
        "exclusiveMinimum",
        "exclusiveMaximum",
        "maximum",
        "maxLength",
        "maxItems",
        "required",
        "description",
        "title",
        "default",
        "examples",
        "$comment",
    }
)


# --------------------------------------------------------------------------------------------
# The surface: every setting, and every image that reads it
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reader:
    """One image a chart pins, named as the declaration names it.

    The name is the vendored contract's stem rather than the repository part of the image
    reference. `contracts/api.json` is what the declaration writes and what a maintainer edits,
    it is short enough to list nine of on one line, and it maps one-to-one to an image — where
    `docker.io/timschoenle/tankovault-api` does not fit and `tankovault-api` repeats the chart
    name nine times. The full reference is in the header and in `--json`.
    """

    name: str
    contract: str
    image: str
    digest: str
    app: str
    version: str
    documents: tuple[str, ...]


@dataclass
class Setting:
    """One setting, and every image that declared it.

    Held as the occurrences rather than as a merged entry because the divergence between two
    images' descriptions is a thing to print, not a thing to resolve. `representative()` is the
    single answer for a compact line; `variants()` is what a full entry shows when there is more
    than one.
    """

    name: str
    occurrences: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def readers(self) -> list[str]:
        return [reader for reader, _ in self.occurrences]

    def value(self, name: str) -> Any:
        """The value to show for one field, across every image that declared this setting.

        `required` unions, which is `cc.UNIONED_FIELDS` restated: a setting any reader requires
        must be present, so an image that does not require it cannot make it optional. Prose
        takes the first non-empty. Everything else takes the first image's, and any disagreement
        is reported separately rather than silently resolved here.
        """
        if name == "required":
            return any(entry.get("required") for _, entry in self.occurrences)
        if name in PROSE_FIELDS:
            for _, entry in self.occurrences:
                if entry.get(name):
                    return entry[name]
            return None
        return self.occurrences[0][1].get(name)

    def representative(self) -> dict[str, Any]:
        """One entry standing for all of them, shaped exactly as a published entry is.

        Shaped that way on purpose: `cc.text_form` and `cc.file_supplyable` are the normative
        readings of an entry, and passing them a merged dictionary rather than reimplementing
        their rules is what keeps this command agreeing with the gates.

        Fields keep the order the producer published them in rather than being sorted, so
        `--json` round-trips into something a reader can diff against the vendored file.
        """
        names: list[str] = []
        for _, entry in self.occurrences:
            for name in entry:
                if name not in names:
                    names.append(name)
        return {name: self.value(name) for name in names}

    def variants(self, name: str) -> list[tuple[Any, list[str]]]:
        """Each distinct value of one field, with the images that published it."""
        seen: list[tuple[Any, list[str]]] = []
        for reader, entry in self.occurrences:
            value = entry.get(name)
            for known, readers in seen:
                if known == value:
                    readers.append(reader)
                    break
            else:
                seen.append((value, [reader]))
        return seen

    def divergent(self) -> list[str]:
        """Fields two images describe differently. `required` is excluded: it unions by rule."""
        fields: set[str] = set()
        for _, entry in self.occurrences:
            fields.update(entry)
        return sorted(
            name
            for name in fields - cc.UNIONED_FIELDS - cc.INTERNAL_FIELDS
            if len(self.variants(name)) > 1
        )


@dataclass
class Surface:
    """Everything one chart's images read, merged across every document the chart declares."""

    chart: str
    dialect: dict[str, str] = field(default_factory=dict)
    readers: list[Reader] = field(default_factory=list)
    keys: dict[str, Setting] = field(default_factory=dict)
    loader: dict[str, Setting] = field(default_factory=dict)
    external: dict[str, Setting] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)
    unknown: str = "reject"


def collect(chart_dir: Path, declaration: Declaration, report: Report) -> Surface | None:
    """Bind every declared document, then read the contracts binding proved belong to it.

    `bind` is called for its interlock and for nothing else. It answers whether the vendored file
    is for the digest the chart pins, which is the only question that decides whether anything
    printed below describes the deployed image — and it is answered here exactly as the gates
    answer it, so this command cannot report facts a gate would refuse to trust. What it returns
    is shaped for validation (a union per document, a union per digest) and carries no
    provenance, so the contracts are re-read afterwards for the image reference and the app
    version. That is a second read of a file already proven, not a second opinion about it.
    """
    values = read_yaml(chart_dir / "values.yaml")
    app_version = read_yaml(chart_dir / "Chart.yaml").get("appVersion")

    surface = Surface(chart=declaration.chart)
    by_contract: dict[str, list[str]] = {}
    refused = False

    for document in declaration.documents:
        where = f"{declaration.chart}: {document.name}"
        binding, problems = bind(chart_dir, document, values, app_version)
        for problem in problems:
            report.fail(where, problem)
        if binding is None:
            refused = True
            continue
        for reference in document.images:
            by_contract.setdefault(reference.contract, []).append(document.name)

    if refused:
        return None

    # Sorted, so the order every reader list and every "the images disagree" side is printed in is
    # a property of the contracts rather than of where a maintainer happened to add a document.
    for contract_path, documents in sorted(by_contract.items()):
        vendored = cc.load_vendored(chart_dir / contract_path)
        _absorb(surface, vendored, Path(contract_path).stem, tuple(documents), report)

    return surface


def _absorb(
    surface: Surface,
    vendored: cc.Vendored,
    name: str,
    documents: tuple[str, ...],
    report: Report,
) -> None:
    """Fold one image's contract into the chart-wide surface."""
    contract = vendored.contract
    app = contract.get("app") or {}
    surface.readers.append(
        Reader(
            name=name,
            contract=str(vendored.path.name),
            image=vendored.image,
            digest=vendored.digest,
            app=str(app.get("name") or name),
            version=str(app.get("version") or "?"),
            documents=documents,
        )
    )

    dialect = contract["schema"]["dialect"]
    if not surface.dialect:
        surface.dialect = dict(dialect)
        surface.unknown = contract["external"]["unknown"]
    elif dialect != surface.dialect:
        # `union_contracts` makes this fatal, and per document it must be: two images reading one
        # file under different spelling rules do not share a namespace. Across documents it is
        # merely surprising — nine separate files could legitimately use nine prefixes — so it is
        # said rather than raised, and the header reports the first image's dialect.
        report.add(
            surface.chart,
            warning(
                f"{name} reads its configuration under the dialect "
                f"{json.dumps(dialect, sort_keys=True)}, where the other images this chart pins "
                f"use {json.dumps(surface.dialect, sort_keys=True)}; the header below reports "
                "the latter"
            ),
        )

    for entry in contract["schema"]["keys"]:
        _record(surface.keys, entry, "path", name)
    for entry in contract["schema"]["loader"]:
        _record(surface.loader, entry, "env", name)
    for entry in contract["external"]["env"]:
        _record(surface.external, entry, "name", name)

    for pattern in contract["external"]["ignore"]:
        if pattern not in surface.ignore:
            surface.ignore.append(pattern)
    if cc.UNKNOWN_POLICIES.index(contract["external"]["unknown"]) > cc.UNKNOWN_POLICIES.index(
        surface.unknown
    ):
        surface.unknown = contract["external"]["unknown"]


def _record(into: dict[str, Setting], entry: dict[str, Any], key_field: str, reader: str) -> None:
    name = entry.get(key_field)
    if not isinstance(name, str):
        raise cc.ContractError(f"{reader}: an entry has no `{key_field}`")
    into.setdefault(name, Setting(name=name)).occurrences.append((reader, entry))


def report_divergences(surface: Surface, pattern: str | None, report: Report) -> None:
    """Say where two images describe one setting differently, within the selection.

    Nothing else in this repository can see these. Each of a chart's documents is validated
    against its own images' union, so two images that never share a document never have their
    descriptions of a shared setting compared — and `tankovault`'s nine services share 77 of
    their 167 settings without sharing a single file.

    Scoped to the selection, because a warning about a setting the reader did not ask about is
    noise on every run and would train them to ignore the one that matters.
    """
    for section, settings in (
        ("key", surface.keys),
        ("loader variable", surface.loader),
        ("external variable", surface.external),
    ):
        for setting in select(settings, pattern):
            for name in setting.divergent():
                sides = "; ".join(
                    f"{', '.join(readers)}: {_terse(value)}"
                    for value, readers in setting.variants(name)
                )
                report.add(
                    surface.chart,
                    warning(
                        f"the images pinned here describe the {section} {setting.name!r} "
                        f"differently: `{name}` is {sides}"
                    ),
                )


def _terse(value: Any, limit: int = 60) -> str:
    text = json.dumps(value)
    return text if len(text) <= limit else text[:limit] + "..."


# --------------------------------------------------------------------------------------------
# Selecting
# --------------------------------------------------------------------------------------------


def select(settings: dict[str, Setting], pattern: str | None) -> list[Setting]:
    """The settings a pattern names, in path order.

    A pattern carrying a glob metacharacter is matched as a glob against the whole name; anything
    else is a case-insensitive substring, because the name an operator has in hand is usually a
    fragment of a path they read in a values file. Both are anchored on the name alone — the
    environment spelling is derived from it, so a pattern that matches the path matches the
    variable a reader is actually holding.
    """
    chosen = sorted(settings.values(), key=lambda setting: setting.name)
    if not pattern:
        return chosen
    if any(character in pattern for character in "*?["):
        return [item for item in chosen if fnmatch.fnmatchcase(item.name, pattern)]
    lowered = pattern.lower()
    return [item for item in chosen if lowered in item.name.lower()]


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


def describe_constraint(constraint: Any) -> str:
    """A JSON Schema constraint object, as a phrase.

    Bounds are folded into one range because that is how they are read — `0 to 65535`, not
    `integer, minimum 0, maximum 65535` — and an unrecognised keyword is printed rather than
    dropped, so a vocabulary this renderer does not know still reaches the reader.

    Recurses, as of `schema_version: 2`. A container-typed key carries what one element holds
    under `items` or `additionalProperties`, and printing that as raw JSON — which the catch-all
    below did before the keywords were named — turns the one line an operator reads about a
    setting into a wall of braces. `array of string one of "GET" | "POST"` is the same fact.

    A struct element is named by its fields rather than described in full: a `RouteConfig` has
    twenty of them, each with a type and a bound of its own, and a table cell is not where that
    belongs. `--json` emits the constraint verbatim for a reader that wants all of it.
    """
    if not isinstance(constraint, dict) or not constraint:
        return ""

    parts: list[str] = []
    for keyword in CONSTRAINT_ORDER:
        if keyword not in constraint:
            continue
        value = constraint[keyword]
        if keyword == "type":
            parts.append(value if isinstance(value, str) else " or ".join(value))
        elif keyword == "const":
            parts.append(f"exactly {json.dumps(value)}")
        elif keyword == "enum":
            parts.append("one of " + " | ".join(json.dumps(item) for item in value))
        elif keyword == "pattern":
            parts.append(f"matching {value}")
        elif keyword == "format":
            parts.append(f"format {value}")
        elif keyword == "multipleOf":
            parts.append(f"a multiple of {value}")
        elif keyword in ("minimum", "exclusiveMinimum"):
            parts.append(_range(constraint))
        elif keyword in ("maximum", "exclusiveMaximum"):
            if "minimum" not in constraint and "exclusiveMinimum" not in constraint:
                parts.append(_range(constraint))
        elif keyword in ("minLength", "maxLength"):
            if keyword == "minLength" or "minLength" not in constraint:
                parts.append(_length(constraint))
        elif keyword in ("minItems", "maxItems"):
            if keyword == "minItems" or "minItems" not in constraint:
                parts.append(_count(constraint))
        elif keyword == "uniqueItems":
            if value:
                parts.append("distinct")
        elif keyword == "properties":
            parts.append(_fields(value, constraint.get("required") or []))
        elif keyword in ("items", "additionalProperties"):
            if isinstance(value, dict) and value:
                inner = describe_constraint(value)
                if inner:
                    # Parenthesised where the element has more than one thing to say about it, so
                    # `object, of (array, distinct)` cannot be read as a distinct object.
                    parts.append(f"of ({inner})" if "," in inner else f"of {inner}")

    for keyword, value in sorted(constraint.items()):
        if keyword not in CONSTRAINT_ORDER and keyword not in CONSTRAINT_FOLDED:
            parts.append(f"{keyword}={json.dumps(value)}")

    return ", ".join(parts)


def _range(constraint: dict[str, Any]) -> str:
    low, low_open = constraint.get("minimum"), constraint.get("exclusiveMinimum")
    high, high_open = constraint.get("maximum"), constraint.get("exclusiveMaximum")
    lower = f"above {low_open}" if low_open is not None else (None if low is None else f"{low}")
    upper = (
        f"below {high_open}" if high_open is not None else (None if high is None else f"{high}")
    )
    if lower is not None and upper is not None and low_open is None and high_open is None:
        return f"{lower} to {upper}"
    if lower is not None and upper is not None:
        return f"{lower}, {upper}"
    if lower is not None:
        return lower if low_open is not None else f"at least {lower}"
    return upper if high_open is not None else f"at most {upper}"


def _count(constraint: dict[str, Any]) -> str:
    """`minItems` and `maxItems`, folded the way `_length` folds their string equivalents."""
    low, high = constraint.get("minItems"), constraint.get("maxItems")
    if low is not None and high is not None:
        return f"{low} to {high} items"
    if low is not None:
        return f"at least {low} item(s)"
    return f"at most {high} item(s)"


def _fields(properties: dict[str, Any], required: list[Any]) -> str:
    """A struct element's fields, in the order the producer declared them, required ones starred.

    Declaration order rather than alphabetical: that is the order the struct is written in, and
    the order somebody comparing this against the source will read it in.
    """
    names = [f"{name}*" if name in required else str(name) for name in properties]
    return "fields " + ", ".join(names) if names else "no fields"


def _length(constraint: dict[str, Any]) -> str:
    low, high = constraint.get("minLength"), constraint.get("maxLength")
    if low is not None and high is not None:
        return f"{low} to {high} characters"
    if low is not None:
        return f"at least {low} characters"
    return f"at most {high} characters"


def prose(text: str, indent: str) -> list[str]:
    """Re-flow a `docs` block to this terminal's width, keeping its paragraph breaks."""
    lines: list[str] = []
    for index, paragraph in enumerate(text.split("\n\n")):
        collapsed = " ".join(paragraph.split())
        if not collapsed:
            continue
        if index and lines:
            lines.append("")
        lines.extend(textwrap.wrap(collapsed, width=WIDTH - len(indent)) or [""])
    return [indent + line if line else "" for line in lines]


def reader_list(setting: Setting, total: int) -> str:
    """The images that read one setting, or `all N` when that is every image the chart pins.

    `all 9` rather than nine names is not an abbreviation: the nine are listed in the header, and
    the fact worth reading here is that the setting is fleet-wide. It is also what keeps a
    `bind_addr` read by eight visibly different from a `TANKOVAULT_CONFIG` read by all nine.
    """
    if total > 1 and len(setting.readers) == total:
        return f"all {total}"
    return ", ".join(setting.readers)


def markers(entry: dict[str, Any]) -> str:
    """The two-character flag column: `R` required, `S` secret."""
    return ("R" if entry.get("required") else ".") + ("S" if entry.get("secret") else ".")


def shown_default(entry: dict[str, Any]) -> str:
    """The default as the loader would read it, or `-` when the setting has none.

    An empty string is a default, not an absence — `""` and "nothing is set" are two different
    deployments — so it is spelt out rather than folded into the same dash.
    """
    value = entry.get("default")
    if value is None:
        return "-"
    return "(empty)" if value == "" else str(value)


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def compact(settings: list[Setting], total: int) -> list[str]:
    """One line per setting: path, type, markers, default, and the images that read it.

    The columns size themselves to the selection rather than to the chart, so `just explain
    tankovault tls` is narrow where the whole chart is wide, and the default column disappears
    entirely when nothing in the selection has one — which is every `tankovault` setting, since
    that producer publishes no defaults at all. The reader list is last and unpadded: it is the
    widest field and the one that varies most, and truncating it would hide the fact this listing
    exists to show.
    """
    rows: list[tuple[str, str, str, str, str]] = []
    for setting in settings:
        entry = setting.representative()
        rows.append(
            (
                _clip(setting.name, MAX_PATH),
                _clip(str(entry.get("ty") or cc.text_form(entry)), MAX_TYPE),
                markers(entry),
                _clip(shown_default(entry), MAX_DEFAULT),
                reader_list(setting, total) if total > 1 else "",
            )
        )

    widths = [max((len(row[column]) for row in rows), default=0) for column in range(4)]
    defaults = any(row[3] != "-" for row in rows)

    lines = []
    for name, ty, flags, default, readers in rows:
        line = f"    {name:<{widths[0]}}  {ty:<{widths[1]}}  {flags}"
        if defaults:
            line = f"{line}  {default:<{widths[3]}}"
        lines.append(f"{line.rstrip()}  {readers}" if readers else line.rstrip())
    return lines


def full(setting: Setting, dialect: dict[str, str], total: int) -> list[str]:
    """Everything one contract says about one setting."""
    entry = setting.representative()
    form = cc.text_form(entry)
    divergent = set(setting.divergent())

    lines = [f"    {setting.name}"]

    def row(label: str, value: str) -> None:
        """One labelled line, wrapped under its own label rather than off the terminal.

        A spelling or a type never needs this; a refusal explaining why a file cannot supply the
        key always does, and an entry whose widest line is the one carrying the reason is the one
        an operator will not read.
        """
        if not value:
            return
        margin = " " * 8 + f"{label:<14}"
        # An environment spelling and a `pattern` are single tokens, and a token split across two
        # lines is one nobody can copy — so a value wider than the terminal overflows rather than
        # being broken.
        lines.extend(
            textwrap.wrap(
                value,
                width=WIDTH,
                initial_indent=margin,
                subsequent_indent=" " * len(margin),
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [margin]
        )

    row("type", f"{entry.get('ty') or '?'}  (read as {form})")
    row("required", "yes" if entry.get("required") else "no")
    row("secret", "yes" if entry.get("secret") else "no")
    if entry.get("reserved"):
        row("reserved", "yes — the loader claims this path; a deployment must not set it")
    row("default", shown_default(entry))
    # Beside the default rather than at the end with the prose, because that is what a `note`
    # turns out to be: both of the two in this repository gloss the default rather than the key
    # ("permanent" for a TTL of `0`, "ISR off; the image sets `/tmp/isr`" for no cache directory),
    # and a gloss printed six lines from the thing it glosses is a gloss nobody connects.
    row("note", str(setting.value("note") or ""))
    if entry.get("values"):
        row("values", " | ".join(str(value) for value in entry["values"]))
    row("accepts", describe_constraint(entry.get("constraint")))
    row("as text", describe_constraint(entry.get("text_constraint")))

    if total > 1:
        row("read by", reader_list(setting, total))

    for label, spelling in (
        ("environment", "env"),
        (f"{dialect.get('indirection_suffix', '_FILE')} form", "env_file"),
        ("secrets file", "secrets_file"),
    ):
        if entry.get(spelling):
            row(label, str(entry[spelling]))
    for label, spelling in (
        ("env aliases", "env_aliases"),
        ("aliases", "aliases"),
        ("secrets file aliases", "secrets_file_aliases"),
    ):
        if entry.get(spelling):
            row(label, ", ".join(str(value) for value in entry[spelling]))

    # `file_supplyable` is normative and counter-intuitive: the `_FILE` and secrets-file spellings
    # above exist for every key, and for a key of any form but `text` neither of them can supply
    # it — a file delivers a string and `Figment::extract` will not coerce one into a number, a
    # boolean or a TOML literal. Printing the spelling without printing that is how an operator
    # ends up mounting a Secret that is silently never read.
    if entry.get("env_file") or entry.get("secrets_file"):
        if cc.file_supplyable(entry):
            row("from a file", "yes")
        else:
            row(
                "from a file",
                f"no — a file supplies text, and this key is read as {form}; "
                "set it in the document or the environment",
            )

    if "docs" in divergent:
        lines.append("        docs (the images disagree)")
        for value, readers in setting.variants("docs"):
            lines.append(f"          {', '.join(readers)}:")
            lines.extend(prose(str(value or "(nothing)"), " " * 12))
    elif setting.value("docs"):
        lines.append("        docs")
        lines.extend(prose(str(setting.value("docs")), " " * 12))

    remaining = sorted(divergent - {"docs"})
    if remaining:
        row(
            "disagreement",
            f"the images describe {', '.join(remaining)} differently; the values above are "
            f"{setting.readers[0]}'s",
        )

    lines.append("")
    return lines


def variable(setting: Setting, summary: str, total: int) -> list[str]:
    """One loader or external variable: what it is, what it says, and who reads it.

    Neither is a setting, so neither gets the full entry above — there is no path, no `_FILE`
    spelling and no secrets file to report. What they have in common is a name, a one-line
    summary and prose, which is this block.
    """
    lines = [f"    {setting.name}  ({summary})"]
    if setting.value("docs"):
        lines.extend(prose(str(setting.value("docs")), " " * 8))
    if total > 1:
        lines.append(f"        read by  {reader_list(setting, total)}")
    return lines


def print_surface(surface: Surface, args: argparse.Namespace, out) -> int:
    """The whole report, for a human. Returns the exit status."""
    keys = select(surface.keys, args.pattern)
    loader = select(surface.loader, args.pattern)
    external = select(surface.external, args.pattern)
    total = len(surface.readers)

    prefix = surface.dialect.get("prefix", "")
    print(
        f"==> {surface.chart}: {len(surface.keys)} setting(s) across "
        f"{len(surface.readers)} image(s)",
        file=out,
    )
    print(
        f"    {prefix}* spellings, {surface.dialect.get('nesting_separator')} for nesting, "
        f"{surface.dialect.get('indirection_suffix')} for a file reference",
        file=out,
    )
    readers = sorted(surface.readers, key=lambda item: item.name)
    name_width = max((len(reader.name) for reader in readers), default=0) + 2
    image_width = max((len(reader.image) for reader in readers), default=0) + 2
    for reader in readers:
        print(
            f"    {reader.name:<{name_width}}{reader.image:<{image_width}}"
            f"{reader.version:<10}{reader.digest[:19]}...",
            file=out,
        )

    if args.pattern:
        print(
            f"\n    matching {args.pattern!r}: {len(keys)} setting(s), "
            f"{len(loader)} loader variable(s), {len(external)} external variable(s)",
            file=out,
        )

    if keys:
        print("\n==> settings\n", file=out)
        if not (args.full or args.pattern):
            columns = "path, type, flags, default"
            if total > 1:
                columns += ", and the images that read it"
            print(f"    {columns}; R marks required, S marks secret\n", file=out)
        if args.full or args.pattern:
            for setting in keys:
                print("\n".join(full(setting, surface.dialect, total)), file=out)
        else:
            print("\n".join(compact(keys, total)), file=out)

    if loader:
        print("\n==> loader variables — where the settings are read from\n", file=out)
        blocks = []
        for setting in loader:
            entry = setting.representative()
            summary = f"role {entry.get('role')}, default {shown_default(entry)}"
            blocks.append("\n".join(variable(setting, summary, total)))
        print("\n\n".join(blocks), file=out)

    if external:
        print(
            "\n==> external variables — read by the image, and nobody's configuration\n",
            file=out,
        )
        print(
            "\n".join(
                prose(
                    "These are not settings. They belong to a runtime or a library the image "
                    "carries, they are outside the loader's namespace, and nothing in this "
                    "chart's document can change one. A value set here and expected to reach "
                    "the application's configuration is a value that is ignored.",
                    "    ",
                )
            ),
            file=out,
        )
        print(file=out)
        blocks = []
        for setting in external:
            entry = setting.representative()
            constraint = describe_constraint(entry.get("constraint"))
            summary = (
                f"owner {entry.get('owner')}, {entry.get('ty')}"
                f"{', ' + constraint if constraint else ''}, "
                f"default {shown_default(entry)}"
            )
            blocks.append("\n".join(variable(setting, summary, total)))
        print("\n\n".join(blocks), file=out)

        print(f"\n    unaccounted-for variables under {prefix}*: {surface.unknown}", file=out)
        if surface.ignore:
            print(f"    ignored by pattern: {', '.join(surface.ignore)}", file=out)

    if args.pattern and not (keys or loader or external):
        print(
            f"    nothing this chart's images read matches {args.pattern!r}",
            file=sys.stderr,
        )
        return 1
    return 0


# --------------------------------------------------------------------------------------------
# The machine-readable form
# --------------------------------------------------------------------------------------------


def as_json(surface: Surface, pattern: str | None) -> dict[str, Any]:
    """The same selection, for something other than a person.

    Every entry is emitted as the contract published it, with the readers and the two derived
    readings — `text_form` and `file_supplyable` — added. Deriving those here rather than leaving
    them to the consumer is the point of the format: they are the rules a reimplementation gets
    wrong.
    """
    return {
        "chart": surface.chart,
        "dialect": surface.dialect,
        "unknown": surface.unknown,
        "ignore": surface.ignore,
        "pattern": pattern,
        "images": [
            {
                "name": reader.name,
                "contract": reader.contract,
                "image": reader.image,
                "digest": reader.digest,
                "app": reader.app,
                "version": reader.version,
                "documents": list(reader.documents),
            }
            for reader in sorted(surface.readers, key=lambda item: item.name)
        ],
        "keys": [_setting_json(item, derive=True) for item in select(surface.keys, pattern)],
        "loader": [_setting_json(item) for item in select(surface.loader, pattern)],
        "external": [
            _setting_json(item, derive=True) for item in select(surface.external, pattern)
        ],
    }


def _setting_json(setting: Setting, derive: bool = False) -> dict[str, Any]:
    entry = cc.strip_internals(setting.representative())
    entry["readers"] = setting.readers
    if derive:
        entry["text_form"] = cc.text_form(entry)
        entry["file_supplyable"] = cc.file_supplyable(entry)
    divergent = setting.divergent()
    if divergent:
        entry["divergent"] = {
            name: [
                {"value": value, "readers": readers} for value, readers in setting.variants(name)
            ]
            for name in divergent
        }
    return entry


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def charts_with_contracts(charts: Path) -> list[tuple[str, Declaration]]:
    """Every chart carrying a declaration, whether or not it declares any document.

    Not `documents_only`: a chart that opted out explicitly carries a reason, and printing that
    reason is one of the two things this command exists to do.
    """
    return [(chart_dir.name, declaration) for chart_dir, declaration in declared(charts)]


def list_charts(charts: Path, out) -> None:
    covered = charts_with_contracts(charts)
    if not covered:
        print("==> no chart in this repository declares a configuration contract", file=out)
        return
    print("==> charts with a configuration contract\n", file=out)
    for name, declaration in covered:
        if not declaration.documents:
            print(f"    {name:<38}opted out", file=out)
            continue
        images = sum(len(document.images) for document in declaration.documents)
        print(
            f"    {name:<38}{len(declaration.documents)} document(s), {images} image(s)",
            file=out,
        )
    print("\n    just explain <chart> [pattern]", file=out)


def describe_opt_out(declaration: Declaration, out) -> None:
    print(
        f"==> {declaration.chart} has explicitly opted out of configuration contracts\n",
        file=out,
    )
    print("\n".join(prose(str(declaration.reason), "    ")), file=out)
    if declaration.unconfigured:
        # Values paths, not repository names — `unconfigured` is unioned with every declared
        # image's `values` and compared against the paths a chart pins. This line used to call
        # them images, which is how the field came to have two readers that disagreed.
        print(
            f"\n    values paths pinning an image that carries no contract: "
            f"{', '.join(declaration.unconfigured)}",
            file=out,
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Explain what a chart's pinned images read, from their vendored contracts"
    )
    parser.add_argument("chart", nargs="?", default="", help="the chart to explain")
    parser.add_argument(
        "pattern", nargs="?", default="", help="substring or glob over the setting's path"
    )
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    parser.add_argument(
        "--full", action="store_true", help="print full entries even without a pattern"
    )
    parser.add_argument("--json", action="store_true", help="emit the same selection as JSON")
    args = parser.parse_args(argv)
    args.pattern = args.pattern or None

    charts = Path(args.charts)
    report = Report()

    try:
        if not args.chart:
            list_charts(charts, sys.stdout)
            return 0

        chart_dir = charts / args.chart
        declaration = load_declaration(chart_dir) if chart_dir.is_dir() else None
        if declaration is None:
            print(
                f"error: {args.chart!r} is not a chart with a configuration contract",
                file=sys.stderr,
            )
            list_charts(charts, sys.stderr)
            return 2
        if not declaration.documents:
            describe_opt_out(declaration, sys.stdout)
            return 0

        surface = collect(chart_dir, declaration, report)
        if surface is None:
            # The interlock, and the reason it is worth honouring in a command that changes
            # nothing: a contract that is not for the digest the chart pins describes some other
            # build of the image, and printing its settings as this chart's would be a confident
            # wrong answer to the only question anyone runs this to ask.
            report.print(sys.stderr, sys.stderr)
            print(
                "\nerror: the vendored contracts are not for the images this chart pins, so "
                "nothing here can be shown to describe what is deployed; run `just contracts`",
                file=sys.stderr,
            )
            return 3

        report_divergences(surface, args.pattern, report)
    except (DeclarationError, cc.ContractError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 2

    if args.json:
        # Warnings go to stderr here rather than stdout, so `just explain ... --json | jq` reads
        # JSON and nothing else. `Report.summary` is deliberately not called: this command is an
        # explanation, not a gate, and it has no business writing a CI step summary.
        report.print(sys.stderr, sys.stderr)
        json.dump(as_json(surface, args.pattern), sys.stdout, indent=2, sort_keys=False)
        print(file=sys.stdout)
        selected = (
            len(select(surface.keys, args.pattern))
            + len(select(surface.loader, args.pattern))
            + len(select(surface.external, args.pattern))
        )
        return 1 if args.pattern and not selected else 0

    status = print_surface(surface, args, sys.stdout)
    report.print(sys.stdout, sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
