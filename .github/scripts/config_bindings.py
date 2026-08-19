#!/usr/bin/env python3
"""Configuration binding markers: which contract key a chart value feeds, stated in `values.yaml`.

A contract says what the image reads. `config-contract.yaml` says which contract describes which
rendered document. Neither says which *chart value* an operator sets to move a given setting, and
that is the fact this file gives a name to:

    # @schema
    # # @config projection telemetry.sentry_dsn optional
    # type: string
    # @schema
    # -- Sentry DSN (`telemetry.sentry_dsn`). Empty disables Sentry entirely.
    sentryDsn: ""

One line inside the value's `@schema` block, naming the contract key that value feeds and how it
gets there. Nothing generates these — they are written by whoever maps the chart onto the image,
and `check-config-bindings.py` is the gate that holds them against the vendored contract. Why that
line and not the value's own is the longest section below, because it was measured rather than
chosen and the obvious answers are all wrong.

Why the fact is worth writing down at all, in the order the payoffs arrive:

**1. Key-level coverage — the only one this file serves today.**
`just check-contract-coverage` is chart-level: it reports whether a chart carries a
`config-contract.yaml`, and its `unconfigured` escape hatch lists *images*. Nothing in this
repository notices when an image release adds a setting that no chart value reaches. That is the
documented recurring failure here — an automated bump repins the digest and silently omits
everything else — and a marker per value is what turns "the chart has a contract" into "every key
of that contract has an operator-facing value, or a written reason why it has none".

**2. Retargeting the generated round-trip probes — later, and an addition rather than a rewrite.**
The generated suites write their probes into the raw `config` escape hatch, so they pass whether
or not the chart's hand-written camelCase-to-snake_case mapping is right. `Marker.values_path` is
the dotted path of the chart value itself, so a probe can be written *there* instead and finally
exercise the mapping. `condition` and `optional` are carried for the same reason: a probe on a
gated value has to switch its gate on first, and a probe on an optional one has to assert the key
is *absent* when the value is empty rather than present and empty. Nothing here uses either field;
both are recorded because a generator that had to re-derive them would have to re-parse the
templates.

**3. Carrying contract prose into `values.schema.json` — later, likewise an addition.**
`keys[].docs` and a value's schema `description` carry the same sentences today, written twice.
A marker is the join: the description of the value at `Marker.values_path` is the `docs` of the key
at `Marker.target`.

So the model is deliberately (values_path -> target) plus enough about *how* to reproduce the
mapping, and not a list of covered keys. A coverage-only model would have to be replaced for both
later payoffs; this one is read by them.

--------------------------------------------------------------------------------------------
The grammar
--------------------------------------------------------------------------------------------

    # @schema
    # # @config <class> <target> [optional] [when <values-path>]
    # <the rest of the schema, untouched>
    # @schema
    # -- <the description helm-docs already reads>
    <key>: <value>

`class` is one of four shapes, `target` is `[<scope>:]<name>`, and the two suffixes are
conditions. In fixed order, and with exactly one spelling of everything: a format whose parser
accepts several orderings is a format two charts write differently. The same rule fixes the
marker's line — first inside the opening `# @schema`, never second and never last — so a reader
looking for a value's binding looks in one place.

| Class        | What it asserts                                                          |
|--------------|---------------------------------------------------------------------------|
| `projection` | the value is written to the key, one to one, as a scalar                   |
| `structured` | the value is a map or list written *under* the key; inside is the operator's |
| `composed`   | the key's text is built from this value and something else                 |
| `external`   | the value reaches the image as a declared `external.env` variable          |

`<scope>` is one document name, or several separated by commas, and is left off in the ordinary
case — a bare `<name>` binds the key in **every** document whose contract declares it. That is
what a chart does: one template line writes `metrics.enabled` into all eight of `tankovault`'s
services. A scope is written only where the default over-claims, meaning the chart writes the key
for some of the documents that declare it and not others.

| Suffix        | What it asserts                                                         |
|---------------|--------------------------------------------------------------------------|
| `optional`    | omitted rather than written empty when the value is falsy (a `with` guard) |
| `when <path>` | written only when the chart value at `<path>` is truthy (an `if` guard)   |

--------------------------------------------------------------------------------------------
The six relationships that actually occur, and where each one lands
--------------------------------------------------------------------------------------------

Measured across all five charts that map values onto a contract, not designed from one:

| Relationship      | Real example                            | How it is said                 |
|-------------------|-----------------------------------------|--------------------------------|
| projection        | all seven of `portfolio`'s keys         | `projection <key>`             |
| optional          | `telemetry.sentry_dsn`, in three charts | `projection <key> optional`    |
| structured        | `bucket.entries`, `webhook.paths`       | `structured <key>`             |
| composition       | `mp-stats` `server.bind_addr`           | `composed <key>` on each input |
| gated subtree     | `netcup` `metrics.*`                    | `projection <key> when <path>` |
| one value, one    | `tankovault` `metrics.listen`           | `composed <key>`, on its own   |
| shared key        | `tankovault` `metrics.enabled`, ×8      | `projection <key>`, unscoped   |
| partial scope     | a key some documents declare unwritten  | `projection <a>,<b>:<key>`     |
| not surfaced      | `netcup` `discord.webhook_url`          | no marker; `unbound` instead   |

Two of those six are conditions rather than shapes, and collapsing them into classes was the first
thing this format got wrong. A `gated` class would have said nothing about `metrics.ip` being a
one-to-one projection, and a value that is both gated *and* optional would have been inexpressible.
Orthogonal suffixes cost one token each and compose.

`composed` and `structured` are the two the mapping cannot be *derived* from, and the marker is
honest about that. `printf "%s:%v" host port` is not recoverable from a comment, and the keys under
`bucket.entries` are the operator's own names; in both cases the marker asserts the binding and
claims nothing about the transformation. That is still the whole of what coverage needs, and it is
what tells payoff 2 that a round-trip probe on such a value has to be written by hand.

**A lone `composed` is legal, and used to be refused.** The rule was "a composition of one is a
projection", which sounded right against `mp-stats`' `server.bind_addr` — `printf "%s:%v"` over
two values — and is false: `tankovault`'s `metrics.listen` is `printf "0.0.0.0:%v"` over one
value and a literal the chart supplies. That is not a projection, because the key's text is not
the value's text, and it is not two values either. So `composed` says "this value is *an* input",
where the other inputs may be other values or the chart's own literals, and the arity is read off
the number of markers on the key rather than asserted by the class. What is lost with the rule is
a check on a single value labelled `composed` when it is really a projection; nothing offline
could tell those apart anyway, and the rule was rejecting a shape that occurs.

--------------------------------------------------------------------------------------------
Two namespaces a marker may name, and the ones it deliberately may not
--------------------------------------------------------------------------------------------

`schema.keys` — the settings the loader owns — and `external.env` — the variables the contract
declares that some *other* library reads. `portfolio` needs the second: `server.host`,
`server.port` and `logLevel` are `IP`, `PORT` and `RUST_LOG`, owned by the Dioxus toolchain and by
`tracing`, and they are the three values in that chart whose entire purpose is to reach the image.
Refusing to bind them would leave a reader unable to tell "no marker because this is not
configuration" from "no marker because someone forgot", which is the distinction the whole gate
exists to make. They validate identically — an `external.env` entry carries the same `docs`,
`default`, `required` and `constraint` fields a key does — so the gate is the same code, and
payoff 3 works on them unchanged. Payoff 2 does not: an external variable is not in the document,
so a round-trip probe through `config` was never available for it either way.

They are **not** counted by the coverage rule. The loader does not own that namespace; a chart
that surfaces no value for `RUST_LOG` is not delinquent, it simply does not offer to set it.
Coverage is over `schema.keys` alone.

Not nameable, on purpose:

- **The loader variables** (`NETCUP_OFFER_BOT_CONFIG`, `..._SECRETS_DIR`). `configMount.configDir`
  does feed one, but it is the chart's own mount plumbing rather than a setting of the
  application, and no coverage rule will ever be written over it.
- **The secrets directory.** A key delivered as a mounted file — `netcup`'s
  `discord.webhook_url` — is genuinely reached by a chart value, and a `secret` class was drafted
  for it and dropped: `config-secrets.py` already reconciles credential delivery across every
  channel, and it does so from the *rendered manifests*. A comment asserting the same thing would
  be a second and strictly weaker opinion about a fact something else derives. Such keys are
  written off in the declaration's `unbound` list instead, with a reason that names the gate which
  does check them.

--------------------------------------------------------------------------------------------
Why this parser reads text rather than YAML
--------------------------------------------------------------------------------------------

Not a shortcut, and not a thing to replace with a real YAML parse later: PyYAML discards comments
entirely, and the marker *is* a comment. `yaml.safe_load` on `values.yaml` returns a mapping in
which no marker has ever existed. Nothing in the stdlib round-trips YAML comments, and this
repository's scripts are stdlib plus PyYAML by rule, so a line-by-line reader is the only
implementation available. It still has to track indentation, because `Marker.values_path` is the
value's full dotted path and the leaf name alone is useless to both later payoffs.

--------------------------------------------------------------------------------------------
Where the marker must sit, which was measured rather than chosen
--------------------------------------------------------------------------------------------

**The first line inside the value's `# @schema` block, written as a YAML comment.** Two other
tools read the comments above a chart value and both regenerate a file CI commits, so a placement
here is a claim about them, not a matter of taste. Ten placements were measured on both pilots, by
moving all fifteen markers and regenerating `values.schema.json` and `README.md` scoped to each
chart:

| Placement                                    | values.schema.json | README.md         |
|----------------------------------------------|--------------------|-------------------|
| first line inside `# @schema`, as `# # ...`  | identical          | identical         |
| last line inside `# @schema`, as `# # ...`   | identical          | identical         |
| appended to a schema line as a YAML comment  | identical          | identical         |
| as a schema key, `# config: projection ...`  | identical          | identical         |
| bare `# @config` inside `# @schema`          | **helm schema fails**  | —             |
| own comment line anywhere above the `# --`   | **15 leading `\\n`**| identical         |
| own comment line last in the block           | identical          | **15 rows fouled**|
| own comment line, blank line, then the block | identical          | identical         |
| blank line between the block and the value   | **every description lost** | 15 rows fouled |
| trailing comment on the value line           | identical          | identical         |

The mechanism explains every row. helm-docs starts a description at `# --` and ends it at the next
non-comment line, so anything above the description is invisible to it and anything below is
appended to it. helm-schema collects the *whole* comment block as that description, and for an
`@`-prefixed line drops the content but keeps the line — a leading newline in fifteen generated
descriptions:

    -  "description": "Log level (`telemetry.log_level`)."
    +  "description": "\\nLog level (`telemetry.log_level`)."

But inside its own delimiters the content is not description, it is the schema, parsed as YAML.
A YAML comment there is discarded by the parser that reads it, which is why the first four rows
are byte-identical and why this is a property of YAML rather than of a helm-schema version. Bare
`# @config` is the same position spelt as YAML *content*, and it fails the plugin outright: `@` is
a reserved indicator in YAML, so the block stops being a document.

Of the five clean rows this file takes the first and refuses the other four. `# config:` survives
only because helm-schema ignores keys it does not know, which is lenience rather than a rule, and
it asserts a schema keyword that is not one. Appending to `# type: string` hangs the marker off a
token it says nothing about. And the blank-line placement makes a blank line load-bearing in a
file where the row below it shows what a misplaced one costs — every description in the chart.

Available everywhere it is needed: all 1,128 documented values across the five charts that map
values onto a contract carry a `@schema` block — 179 in `portfolio`, 114 in `netcup-offer-bot`,
172, 173 and 490 in the other three. Not one is description-only, so no chart has to be told to
add a schema block in order to bind a value.

What this placement gives up, stated because the first draft of this file used it as the reason
for the other one: attachment becomes positional again. A trailing comment is *on* the line it
describes and nothing can re-target it, whereas a marker in the block is attached by contiguity —
the same contiguity the description already depends on, but the marker now has to be read across
the closing delimiter and the description to reach its value. `parse_values` therefore refuses
every way that can go wrong rather than guessing: a blank line before the value, and a block that
ends at something other than a mapping key.

--------------------------------------------------------------------------------------------
One value, several keys
--------------------------------------------------------------------------------------------

A value's markers are the **run of lines directly inside the opening `# @schema`**, and there may
be more than one. Under the trailing-comment placement there could not be — a line has one comment
— and that was written up as a property of the grammar that made "one value binds one key" free.
It is not a property of anything: `tankovault`'s `internal.tls.certDir` is the directory three
contract keys are built from, `internal.tls.cert`, `internal.tls.key` and `internal.tls.ca`, by
three `printf`s in one template. Refusing the second marker would have forced two of those three
bindings to go unsaid, and coverage would then have owed them from somewhere else.

So a value may bind several keys, one marker each, and the parser refuses only the two ways that
cannot be meant: the same target twice on one value, and a marker below the schema rather than in
the run above it. The converse — one key bound by several values — is still `check-config-bindings`
rule 4's, and still needs `composed` on every one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The comment introducer. `@config` rather than `@contract` because a marker names the *setting* a
# value feeds; the contract is merely the document that happens to describe it.
MARKER = "@config"

PROJECTION = "projection"
STRUCTURED = "structured"
COMPOSED = "composed"
EXTERNAL = "external"

CLASSES = (PROJECTION, STRUCTURED, COMPOSED, EXTERNAL)

# Classes whose target is a `schema.keys` path; `external` is the one that is not.
KEY_CLASSES = (PROJECTION, STRUCTURED, COMPOSED)

# The delimiter helm-schema encloses a value's schema in, and the only region of `values.yaml`
# whose comment content is YAML rather than prose. That is what makes it the one place a marker
# can sit without either generator seeing it — see the module docstring.
_SCHEMA_DELIM = re.compile(r"^ *# @schema *$")

_MAPPING_KEY = re.compile(r"^(?P<indent> *)(?P<name>[A-Za-z0-9_][A-Za-z0-9_.\-]*):(?P<rest> .*|)$")
_SEQUENCE_ITEM = re.compile(r"^ *-( .*|)$")
_BLOCK_SCALAR = re.compile(r"^[|>][-+0-9]*$")
_VALUES_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_TARGET = re.compile(r"^(?:(?P<documents>[A-Za-z0-9_.\-]+(?:,[A-Za-z0-9_.\-]+)*):)?"
                     r"(?P<name>[A-Za-z0-9_.\-]+)$")


class BindingError(Exception):
    """A `values.yaml` whose markers cannot be read as ones."""


@dataclass(frozen=True)
class Marker:
    """One `# @config` comment, and the chart value whose `@schema` block it was written in.

    `values_path` is the dotted path of that value — `csp.cloudflare.scriptNonce` — derived from
    the indentation of the *value* the block belongs to, not of the marker, so it cannot disagree
    with the file. It is the field the two later payoffs read: the probe target for payoff 2, the
    description target for payoff 3.

    `line` is the marker's own line, because that is the line a reader has to edit; the value it
    binds is a few lines below it and named in every message.

    `documents` is the scope of the binding: the `config-contract.yaml` documents the value feeds,
    or `None` for the default — *every* document whose contract declares the target. The default is
    the common case rather than the ambiguous one, because a chart writes one value into every
    service that reads it: `tankovault`'s `metrics.enabled` reaches all eight of its services from
    one line of the template, and eight of its key paths are declared by eight documents each.
    A scope is written only where the default would over-claim.
    """

    chart: str
    values_path: str
    line: int
    cls: str
    documents: tuple[str, ...] | None
    target: str
    optional: bool
    condition: str | None

    @property
    def where(self) -> str:
        """`chart/values.yaml:LINE`, for a message a reader can jump to."""
        return f"{self.chart}/values.yaml:{self.line}"


def split_comment(line: str) -> tuple[str, str | None]:
    """Split one line into its YAML and its trailing comment, respecting quotes.

    A `#` inside a quoted scalar is data — `password: "a#b"` is an ordinary value — so a naive
    `line.split("#")` would truncate it and then fail to find the mapping key it was reading.
    Cheap to do properly, and the alternative is a parser that is wrong on a chart nobody has
    written yet.
    """
    single = double = False
    index = 0
    while index < len(line):
        character = line[index]
        if single:
            single = character != "'"
        elif double:
            if character == "\\":
                index += 1
            elif character == '"':
                double = False
        elif character == "'":
            single = True
        elif character == '"':
            double = True
        elif character == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index], line[index + 1 :].strip()
        index += 1
    return line, None


def is_marker(comment: str | None) -> bool:
    """Whether a comment body is a marker, and not merely a word beginning the same way."""
    return comment is not None and (comment == MARKER or comment.startswith(MARKER + " "))


def parse_marker(text: str) -> tuple[str, str | None, str, bool, str | None]:
    """Read one marker's body — everything after `@config` — into its five parts.

    Raises `BindingError` on anything else. There is no lenient reading of a marker: a comment that
    was meant to be one and is not understood must fail here, because the alternative is a value
    that looks bound to a reviewer and is bound to nothing.
    """
    tokens = text.split()
    if not tokens:
        raise BindingError(f"`{MARKER}` names nothing; expected `{MARKER} <class> <target>`")

    cls = tokens.pop(0)
    if cls not in CLASSES:
        raise BindingError(f"unknown class {cls!r}; known classes are {', '.join(CLASSES)}")

    if not tokens:
        raise BindingError(f"`{MARKER} {cls}` names no target")

    matched = _TARGET.match(tokens.pop(0))
    if matched is None:
        raise BindingError(
            "a target is `<key.path>`, or `<document>[,<document>...]:<key.path>` to scope it, "
            "in the contract's own spelling"
        )
    target = matched.group("name")

    documents = None
    if matched.group("documents") is not None:
        documents = tuple(matched.group("documents").split(","))
        if len(set(documents)) != len(documents):
            raise BindingError(f"the scope {','.join(documents)!r} names a document twice")

    optional = bool(tokens) and tokens[0] == "optional"
    if optional:
        tokens.pop(0)

    condition = None
    if tokens and tokens[0] == "when":
        tokens.pop(0)
        if not tokens:
            raise BindingError("`when` names no values path")
        condition = tokens.pop(0)
        if not _VALUES_PATH.match(condition):
            raise BindingError(f"`when {condition}` is not a dotted values path")

    if tokens:
        # Including `when ... optional`, the other order. One relationship has one spelling;
        # accepting both would make two files that wrote it differently look like they said
        # different things.
        raise BindingError(
            f"unexpected {' '.join(tokens)!r}; the form is "
            f"`{MARKER} <class> <target> [optional] [when <values-path>]`, in that order"
        )

    return cls, documents, target, optional, condition


def schema_comment(comment: str | None) -> str | None:
    """The YAML comment carried by one line *inside* a `# @schema` block, if it carries one.

    Inside the delimiters the text after `# ` is the schema, parsed as YAML, so a marker written
    there is a comment within that YAML and the line reads `# # @config ...`. This returns the
    inner comment — `@config ...` — for such a line, and `None` for a line whose schema content is
    real schema and therefore none of this file's business.
    """
    if comment is None or not comment.startswith("#"):
        return None
    return comment[1:].strip()


def parse_values(path: Path, chart: str | None = None) -> list[Marker]:
    """Every marker in one `values.yaml`, each carrying the dotted path of the value it binds.

    Walks the file as lines, because the marker is a comment and no YAML reader available here
    keeps one — see the module docstring. Every problem in the file is collected before any is
    raised, which is the posture every gate in this group takes: one broken line must not hide the
    state of the rest.

    Reading a marker is a small state machine rather than a line test, because the marker sits in
    the value's `@schema` block: open the block, take its first line, close it, cross the `# --`
    description, and bind at the mapping key that ends the run. Every way that walk can end
    somewhere other than a value is refused rather than guessed at — the price of a placement that
    is attached by contiguity instead of by being on the line it describes.
    """
    chart = chart or path.parent.name
    problems: list[str] = []
    markers: list[Marker] = []

    # The mapping nesting, as (indent, name). A key at indent i closes everything at indent >= i.
    stack: list[tuple[int, str]] = []
    # Regions whose interior this parser does not model: sequence items and block scalars. Neither
    # holds a values path a marker may be written against.
    skip_deeper_than: int | None = None

    in_schema = False
    # True while the reader is still in the block's opening run of marker lines.
    in_marker_run = False
    # The markers read out of a block, waiting for the value that block belongs to.
    pending: list[tuple[int, tuple]] = []

    def strand(what: str) -> None:
        """Markers whose block never reached a value bind nothing. Say so, naming both."""
        nonlocal pending
        for line_number, _ in pending:
            problems.append(
                f"{chart}/values.yaml:{line_number}: this `{MARKER}` marker's `@schema` block is "
                f"followed by {what} rather than by the value it binds, so it binds nothing"
            )
        pending = []

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()

        if not line.strip():
            # A blank line ends the comment run, which is exactly how both generators lose the
            # description too — measured: every description in the chart disappears.
            if not in_schema:
                strand("a blank line")
            continue

        indent = len(line) - len(line.lstrip(" "))
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                continue
            skip_deeper_than = None

        code, comment = split_comment(line)

        if _SCHEMA_DELIM.match(line):
            if in_schema:
                in_schema = False
                in_marker_run = False
            else:
                strand("a second `@schema` block")
                in_schema = True
                in_marker_run = True
            continue

        if in_schema:
            nested = schema_comment(comment)
            if is_marker(nested):
                if not in_marker_run:
                    problems.append(
                        f"{chart}/values.yaml:{number}: this `{MARKER}` marker is below the "
                        "schema rather than above it. A value's markers are the run of lines "
                        "directly inside the opening `# @schema`, so that a reader looking for "
                        "what a value binds reads the top of one block and stops"
                    )
                else:
                    try:
                        parsed = parse_marker(nested[len(MARKER) :].strip())
                    except BindingError as failure:
                        problems.append(f"{chart}/values.yaml:{number}: {failure}")
                    else:
                        duplicate = next(
                            (
                                line_number
                                for line_number, other in pending
                                if (other[1], other[2]) == (parsed[1], parsed[2])
                            ),
                            None,
                        )
                        if duplicate is not None:
                            problems.append(
                                f"{chart}/values.yaml:{number}: this value already binds "
                                f"{parsed[2]!r} on line {duplicate}; one binding said twice is "
                                "one of the two being out of date"
                            )
                        else:
                            pending.append((number, parsed))
            elif is_marker(comment):
                problems.append(
                    f"{chart}/values.yaml:{number}: this `{MARKER}` marker is schema *content* "
                    "rather than a comment on it, and `@` is a reserved indicator in YAML, so "
                    "`helm schema` fails on the block outright. Write it as a comment within the "
                    f"schema: `# # {MARKER} ...`"
                )
                in_marker_run = False
            else:
                in_marker_run = False
            continue

        if not code.strip():
            if is_marker(comment):
                problems.append(
                    f"{chart}/values.yaml:{number}: this `{MARKER}` marker is a comment line of "
                    "its own, outside the `@schema` delimiters. Both generators read that run as "
                    "the value's description: above the `# --` helm-schema emits a leading "
                    "newline into values.schema.json, and below it helm-docs appends the marker "
                    f"to the value's row in README.md. Write it as `# # {MARKER} ...` on the "
                    "first line inside the block instead"
                )
            continue

        if _SEQUENCE_ITEM.match(code):
            strand("a sequence item")
            _refuse(problems, chart, number, comment, "a sequence item")
            skip_deeper_than = indent
            continue

        key = _MAPPING_KEY.match(code.rstrip())
        if key is None:
            strand("a line this parser cannot read as a value")
            _refuse(problems, chart, number, comment, "a line this parser cannot read as a value")
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key.group("name")))

        if _BLOCK_SCALAR.match(key.group("rest").strip()):
            skip_deeper_than = indent

        if is_marker(comment):
            problems.append(
                f"{chart}/values.yaml:{number}: this `{MARKER}` marker is the value's trailing "
                "comment. Both generators ignore it there, so this is a convention rather than a "
                "measurement: every other piece of metadata about a value lives above it, and the "
                f"marker's place is the first line inside the `@schema` block, as `# # {MARKER} "
                "...`"
            )

        values_path = ".".join(name for _, name in stack)
        for marker_line, (cls, documents, target, optional, condition) in pending:
            markers.append(
                Marker(
                    chart=chart,
                    values_path=values_path,
                    line=marker_line,
                    cls=cls,
                    documents=documents,
                    target=target,
                    optional=optional,
                    condition=condition,
                )
            )
        pending = []

    strand("the end of the file")

    if problems:
        raise BindingError("\n".join(problems))
    return markers


def _refuse(problems: list[str], chart: str, number: int, comment: str | None, what: str) -> None:
    if is_marker(comment):
        problems.append(
            f"{chart}/values.yaml:{number}: this `{MARKER}` marker sits on {what}; a marker "
            "describes one mapping value and is written inside that value's `@schema` block"
        )


def has_path(values: object, path: str) -> bool:
    """Whether a dotted values path exists, including one whose value is `null` or `false`.

    Existence rather than truth: a `when metrics.enabled` marker is checked against a chart whose
    own default is `enabled: false`, and the question the gate can answer offline is whether the
    gate value is still spelt that way — not whether it happens to be on in `values.yaml`.

    Deliberately not `config_declaration.dig`, whose `None` return cannot tell a missing path from
    one set to `null`, and which this module would otherwise import a YAML reader to reach.
    """
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True
