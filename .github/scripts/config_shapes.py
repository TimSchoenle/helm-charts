#!/usr/bin/env python3
"""Deriving a chart value's `@schema` block from the element schema its image publishes.

A configuration contract states a key's type as a JSON Schema object, and until
`schema_version: 2` that object was flat: `type`, `enum`, `minimum` and nine more, with no way to
say what one element of a container held. A `Vec<RouteConfig>` arrived as `{"type": "array"}`,
which is all the producer could say, and a chart that wanted its operators' editors to catch a
misspelt field before the service did had to transcribe the struct by hand — a copy of a fact
somebody else owns, and a copy nothing regenerates is the one that goes stale.

`schema_version: 2` publishes that shape. A container-typed key whose element type describes
itself carries the element under `items` for a sequence and `additionalProperties` for a map,
composed through both when they are stacked, so a `HashMap<String, HashSet<Method>>` reaches the
enum. That is the fact this module turns into the `@schema` block the chart ships.

--------------------------------------------------------------------------------------------
Generated is the default, and the markers are the departures from it
--------------------------------------------------------------------------------------------

**Every value carrying a `# @config projection`, `structured` or `external` marker has its
`@schema` block written from the contract.** Nothing enrols it. The marker naming the contract key
is the enrolment, because a value that says which key it feeds has said everything a generator
needs, and a second marker repeating "yes, really" is one more thing to forget.

It was the other way round until this change, and what that cost was measured rather than argued:
nine values were enrolled and 185 were not, while 114 of the 185 carried a block byte-identical to
the one their contract describes. Those 114 were hand copies that happened to still be right, with
nothing holding them there — precisely the state this repository keeps discovering after an
automated bump. The other 71 were not identical, and every one of those differences was invisible.

Three markers remain, and each one now says the *opposite* of what the retired `generated` said:
not "derive this", but "here is where the chart departs from what was derived".

    # @config-shape <values-path> handwritten <release> <source>
    # @config-shape-except <values-path> <sub-path> <reason>
    # @config-shape-narrow <values-path> <sub-path> <reason>

written as plain comments above the value's `@schema` block, separated from it by a blank line —
the placement `config_bindings.py` measured to be invisible to helm-schema and helm-docs alike,
and the reason a `@config` marker cannot use it: inside the delimiters the text *is* the schema.

`handwritten` says the chart owns this block, and is the escape hatch generation is refused for.
Two things earn it: a container whose element the producer has not described, where a block built
from `{"type": "array"}` alone would type-check nothing, and a key the producer publishes no
constraint for at all, where the derived block would name all six JSON types and check nothing.
Five keys in this repository are the second kind, three of them log-level enumerations the chart
knows the members of and the contract calls `unknown`. The marker asserts "read from `<source>` at
`<release>`" and fails when an image publishing the key moves past it, which is what an automated
bump does — and fails for a second reason once the contract *does* describe the key, because a
hand copy the image has superseded is a stale copy that looks maintained.

`<release>` is the *image's*, not the chart's, and the two are the same only in a chart shipping
one image. `tankovault` ships nine under one `appVersion`, bumped as each upstream release
publishes them, so a bump that moved the frontend to 8.9.1 and left the other eight at 8.8.0 used
to fail every transcription in the chart — including `legal.documents`, owned by an `api` image
that had not moved, against a release no `api` image was ever built at. Each transcription is held
against the newest image publishing *its own* key instead, which the vendored contracts record;
see `check_handwritten`.

`@config-shape-except` overrides one position the contract *does* describe. `discord-alertmanager`
is why it exists: `routes[].guild_id` is a Discord snowflake, the contract types it `integer`, and
the chart types it as a quoted string of digits because Helm parses a values file through
`encoding/json` and a snowflake is above 2^53 — `123456789012345678` reaches the chart as
`123456789012345680`, silently. Every other field of that struct is still generated, so a field
the next release adds still lands automatically.

`@config-shape-narrow` keeps one position the contract does *not* describe: a bound the chart
knows and the producer has not published. `sentry.sample_rate` is a `f64` the contract types
`number`, and every chart here writes `minimum: 0` and `maximum: 1` above it, because a sample rate
of 5 is a values file nobody should get past `helm install`. Generation would have dropped all
sixteen of those keywords across four charts.

The two are told apart by where the position resolves, and each is refused when written as the
other — see `apply_divergences` for why that is worth a second marker rather than one that accepts
either. Both are refused outright when the sub-path addresses nothing in the chart's own block,
which is how a departure that outlived the field it protected is caught.

--------------------------------------------------------------------------------------------
What is deliberately not generated
--------------------------------------------------------------------------------------------

**Annotations.** `description`, `title`, `default` and `examples` are dropped at every level. The
producer's descriptions are Rust doc comments — multi-paragraph, and several carry fenced TOML
examples — and folding one into a YAML comment inside a YAML comment produces something nobody
can read and that helm-docs then renders into the published README. The value's own `# --` line
is the description this repository publishes, and `just explain` is where the producer's prose is
read in full.

**A block for a value with no `@config` marker.** The marker is what says which contract key the
value feeds, and it is hand-written on purpose — `config_bindings.py` says why in bold. A
`@config-shape` marker naming a value that binds nothing has no key to generate from, and
guessing one from the value's name is the failure that module refuses to build.

--------------------------------------------------------------------------------------------
One file, and it runs
--------------------------------------------------------------------------------------------

The writer and the gate are one program, the way `schema-presets.py` is: what `just config-shapes`
writes is exactly what `just check-config-shapes` demands, and neither can drift from the other.
They are also in the same file as the rules they apply, because `config_scaffold` imports those
rules to write a new chart's blocks — and the alternative was `config-shapes.py` beside
`config_shapes.py`, a pair differing by one character, which `entry.py` already names as the wart
it is for `config-secrets.py`. The recipes spell the path either way.

--------------------------------------------------------------------------------------------
Resolving a value to its key
--------------------------------------------------------------------------------------------

Through the `# @config` marker the value already carries, and through nothing else. That marker is
the one statement in this repository of which contract key a chart value feeds; a second one here
would be a copy of it, and the copy nothing holds in step is the one that goes stale. It also
means a chart that is not bindings-enrolled cannot enrol a shape — `cloudflare-access-webhook-
redirect` is the one such chart, and its `webhook.paths` is exactly the map this format describes,
so it is the first thing enrolling that chart would buy.

A marker binds a key in every document whose contract declares it, which for `tankovault` is up to
nine. Their constraints have to agree: two images describing one key's element differently is the
same defect as disagreeing about its type, and generating from either would be picking a winner
silently. `just check-config-bindings` is where the scope itself is held to account, so the report
here is deliberately short — it names the disagreement and stops.

--------------------------------------------------------------------------------------------
Deliberately not run by `just contracts`
--------------------------------------------------------------------------------------------

The refresh repins the vendored contract, and this reads it. Chaining them would mean one command
that fetches a document and rewrites the chart from it in the same breath, which is the shape of
change a reviewer cannot check: the contract diff and the schema diff would arrive as one commit
with no way to see which caused which. They stay two recipes, and the Documentation job runs both.

Usage: .github/scripts/config_shapes.py [--charts DIR] [--check]
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

import config_bindings as cb
import config_contract as cc
import config_declaration as cd
from config_declaration import Bound, DeclarationError, load_declaration
from config_paths import CHARTS_DIR, read_yaml

# The one mode a marker still declares. `generated` was the other, and is now what every bound
# value is without declaring anything — see the module docstring. The word is kept because the
# marker it used to spell is refused *by name*, which is the whole of the migration diagnostic.
GENERATED = "generated"
HANDWRITTEN = "handwritten"
MODES = (HANDWRITTEN,)

SHAPE_MARKER = "@config-shape"
EXCEPT_MARKER = "@config-shape-except"
NARROW_MARKER = "@config-shape-narrow"

# The binding classes whose value has a constraint of its own to be generated from. `composed` is
# absent because such a value is one input to the key's text rather than the key's value; see
# `Chart._generated`.
DERIVED_CLASSES = (cb.PROJECTION, cb.STRUCTURED, cb.EXTERNAL)

# The two kinds of declared divergence, told apart by where the position they name resolves: an
# override replaces one the contract also describes, a narrowing keeps one it does not describe at
# all. See `apply_divergences` for why that distinction is worth a second marker.
OVERRIDE = "override"
NARROWING = "narrowing"

# Anchored as whole comment lines, so a marker mentioned in prose — this file's own docstring, a
# chart's `# --` description — is not mistaken for a declaration. The two suffixed spellings are
# matched before the bare one by the caller, since both their names begin with it.
_SHAPE = re.compile(r"^\s*#\s*@config-shape\s+(?P<rest>\S.*?)\s*$")
_EXCEPT = re.compile(r"^\s*#\s*@config-shape-except\s+(?P<rest>\S.*?)\s*$")
_NARROW = re.compile(r"^\s*#\s*@config-shape-narrow\s+(?P<rest>\S.*?)\s*$")

# Keywords in the order a generated block spells them: what the value *is*, then its bounds, then
# what it holds. Anything outside this list is appended alphabetically rather than dropped —
# under-reporting a constraint is the failure this whole pipeline exists to remove, and it would
# be no better coming from a renderer.
ORDER = (
    "type",
    "enum",
    "const",
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


class ShapeError(Exception):
    """A marker, an exception or a block this module cannot read as one."""


@dataclass(frozen=True)
class Shape:
    """One value whose `@schema` block this module owns, generated or transcribed.

    A `handwritten` one is declared by a marker and `line` is that marker's. A generated one is
    declared by nothing — every value carrying a `@config projection`, `structured` or `external`
    marker is generated — and `line` is the opening delimiter of the `@schema` block itself, which
    is where a reader of the message has to go either way.

    `declared` tells the two apart for the one message that has to know: a generated shape cannot
    be told to move a marker it does not have.
    """

    chart: str
    line: int
    values_path: str
    mode: str
    version: str | None
    source: str | None
    declared: bool = True

    @property
    def where(self) -> str:
        """`chart/values.yaml:LINE`, for a message a reader can jump to."""
        return f"{self.chart}/values.yaml:{self.line}"


@dataclass(frozen=True)
class Producer:
    """One image publishing a key, and the release its vendored contract was published at."""

    document: str
    version: str


@dataclass(frozen=True)
class Resolved:
    """The contract key one value feeds, and everything a caller needs about it.

    `ty` is the producer's own name for the type — `SentryLevel`, `BTreeMap<String,
    LegalDocument>` — and is carried for one purpose: a refusal that asks for a transcription can
    name what is being transcribed, so the marker it asks for can be pasted rather than composed.
    Empty when the contract publishes none, or when the documents disagree about it.

    `producers` is every document that carries the key, paired with the release its image was
    built at. A single-image chart has one and it is the chart's `appVersion`; `tankovault` has up
    to nine and they move independently, which is the whole reason this field exists rather than
    the caller reading `Chart.yaml`. Empty when no contract carries the key.
    """

    constraint: dict[str, Any] | None
    optional: bool
    structured: bool
    ty: str
    producers: tuple[Producer, ...] = ()

    @property
    def newest(self) -> str:
        """The furthest-ahead release publishing this key — what a transcription is read at.

        A hand copy is one description of a type several images share, so the release to read it
        at is the newest of them: a block read at the newest is a true statement about that image
        and at worst a narrowing of an older one, whereas a block read at the oldest says nothing
        about the field the newest added. Empty when nothing publishes the key.
        """
        return max((one.version for one in self.producers), key=_ordinal, default="")


def _ordinal(version: str) -> tuple[int, ...]:
    """A release as a comparable tuple, for the one comparison this module makes.

    Leading numeric segments only, and everything from the first non-numeric one is dropped:
    `8.9.1` sorts under `8.10.0`, which a string comparison gets backwards, and a tag nobody here
    writes — a pre-release, a date, a bare word — degrades to `()` rather than raising. Two of
    those compare equal, which the caller turns into "re-read it" rather than "it is current":
    see `check_handwritten`.
    """
    parts: list[int] = []
    for segment in cd.release(version).split("."):
        if not segment.isdigit():
            break
        parts.append(int(segment))
    return tuple(parts)


@dataclass(frozen=True)
class Divergence:
    """One declared departure from the generated shape: a position the chart keeps as its own.

    `kind` is `OVERRIDE` for a `@config-shape-except`, which replaces a position the contract also
    describes, and `NARROWING` for a `@config-shape-narrow`, which keeps one it does not describe
    at all. The marker states which, and is held to it — see `apply_divergences`.
    """

    chart: str
    line: int
    values_path: str
    sub_path: str
    reason: str
    kind: str = OVERRIDE

    @property
    def marker(self) -> str:
        """The marker this divergence was written as, for a message that names it."""
        return EXCEPT_MARKER if self.kind == OVERRIDE else NARROW_MARKER

    @property
    def where(self) -> str:
        """`chart/values.yaml:LINE`, for a message a reader can jump to."""
        return f"{self.chart}/values.yaml:{self.line}"


# --------------------------------------------------------------------------------------------
# Reading the markers
# --------------------------------------------------------------------------------------------


def parse_markers(text: str, chart: str) -> tuple[list[Shape], list[Divergence]]:
    """Every `@config-shape`, `-except` and `-narrow` marker in one values.yaml.

    Every problem is collected before any is raised, which is the posture every gate in this group
    takes: one broken line must not hide the state of the rest.
    """
    shapes: list[Shape] = []
    divergences: list[Divergence] = []
    problems: list[str] = []

    for number, line in enumerate(text.splitlines(), start=1):
        where = f"{chart}/values.yaml:{number}"

        for pattern, kind, marker in (
            (_EXCEPT, OVERRIDE, EXCEPT_MARKER),
            (_NARROW, NARROWING, NARROW_MARKER),
        ):
            found = pattern.match(line)
            if not found:
                continue
            words = found["rest"].split(maxsplit=2)
            if len(words) < 3:
                problems.append(
                    f"{where}: `{marker}` takes a values path, a sub-path inside the schema and "
                    "the reason the chart keeps its own — a departure from the contract without a "
                    "reason is one nobody can review"
                )
                break
            divergences.append(
                Divergence(
                    chart=chart,
                    line=number,
                    values_path=words[0],
                    sub_path=words[1],
                    reason=words[2].strip(),
                    kind=kind,
                )
            )
            break
        else:
            found = _SHAPE.match(line)
            if not found:
                continue

            words = found["rest"].split()
            if len(words) >= 2 and words[1] == GENERATED:
                problems.append(
                    f"{where}: `{SHAPE_MARKER} {words[0]} {GENERATED}` is obsolete — every value "
                    f"carrying a `{cb.MARKER}` marker has its `@schema` block generated from the "
                    "contract now, without enrolling. Delete this line; declare any position the "
                    f"chart keeps with `{EXCEPT_MARKER}` or `{NARROW_MARKER}`"
                )
                continue
            if len(words) < 2 or words[1] not in MODES:
                problems.append(
                    f"{where}: `{SHAPE_MARKER}` takes a values path and then "
                    f"`{HANDWRITTEN} <release> <source>`, which is the one thing a marker "
                    "still declares"
                )
                continue

            values_path, mode = words[0], words[1]
            if len(words) != 4:
                problems.append(
                    f"{where}: `{SHAPE_MARKER} {values_path} {HANDWRITTEN}` takes the release "
                    "the struct was read at and the file it was read from"
                )
                continue

            shapes.append(
                Shape(
                    chart=chart,
                    line=number,
                    values_path=values_path,
                    mode=mode,
                    version=words[2],
                    source=words[3],
                )
            )

    if problems:
        raise ShapeError("\n".join(problems))
    return shapes, divergences


# --------------------------------------------------------------------------------------------
# Reading a block
# --------------------------------------------------------------------------------------------


def split_block(block: cb.Block) -> tuple[list[str], list[str]]:
    """One block's lines, as `(the marker run, the schema)`.

    The marker run is the `# # @config ...` lines the block opens with — hand-written, and none of
    this module's business. Everything after it is the schema, and is what a regeneration
    replaces.
    """
    markers: list[str] = []
    rest: list[str] = []
    in_run = True
    for line in block.lines:
        _, comment = cb.split_comment(line)
        if in_run and cb.is_marker(cb.schema_comment(comment)):
            markers.append(line)
            continue
        in_run = False
        rest.append(line)
    return markers, rest


def block_schema(block: cb.Block) -> dict[str, Any]:
    """The schema half of one block, as the mapping helm-schema reads it as.

    Compared rather than the text, so a difference in quoting or in key order is not reported as
    drift: what the block *means* is what the contract has an opinion about. The writer normalises
    the spelling, so a block this function calls equal is one a regeneration leaves alone.
    """
    _, lines = split_block(block)
    body = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#"):
            raise ShapeError(f"line {line!r} inside an `@schema` block is not a comment")
        body.append(_dedent(stripped[1:]))
    loaded = yaml.safe_load("\n".join(body)) if any(part.strip() for part in body) else {}
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ShapeError("an `@schema` block has to be a mapping")
    return loaded


def _dedent(text: str) -> str:
    """Drop the single space helm-schema's `# ` prefix carries, keeping the schema's own indent."""
    return text[1:] if text.startswith(" ") else text


# --------------------------------------------------------------------------------------------
# Building the expected schema
# --------------------------------------------------------------------------------------------


def describing(constraint: dict[str, Any] | None) -> bool:
    """Whether a constraint says anything a schema could hold a value to.

    A key the producer publishes no constraint for reaches `expected` as `None` and leaves with a
    block naming all six JSON types, which is a property helm-schema emits and nothing rejects. As
    an opt-in that was harmless: a value nobody enrolled kept its hand-written block. As the
    default it is a downgrade — five keys in this repository are typed only by the chart, and three
    of them are log-level enumerations the contract calls `unknown` — so the generator refuses the
    key instead and asks for the transcription to be declared as one.
    """
    return bool(constraint) and any(
        keyword in constraint for keyword in ("type", "enum", "const")
    )


def expected(constraint: dict[str, Any] | None, *, optional: bool, structured: bool) -> dict:
    """The `@schema` mapping one contract key's constraint calls for.

    Two shapes are not a straight copy, and both were measured against helm-schema rather than
    reasoned about:

    **An `enum` is the whole block.** helm-schema refuses one carrying both — `Error while
    validating jsonschema of key backend: cannot use both 'enum' and 'type' in the same schema`,
    which exits `just schema` fatally and leaves every chart's `values.schema.json` unwritten. So a
    key whose constraint names an `enum` emits its members alone, with `null` joining them where
    the value is optional. Measured again for `schema_version: 2`: the refusal applies to the *top
    level of the block only*. A nested subschema carrying both passes through verbatim, and that is
    what makes a generated element schema possible at all — every enum inside a struct is an `enum`
    beside a `type`.

    **A `structured` key whose element is undescribed is opened rather than described.** Such a
    constraint says the value is a table and nothing about what is in it — `internal.peers` is a
    `BTreeMap<String, PeerConfig>` whose constraint is `{"type": "object"}` — so the block accepts
    any table. A key whose element *is* described takes the description instead, which is the whole
    of what `schema_version: 2` buys: the same `additionalProperties` field, carrying a schema
    rather than `true`.

    **A `structured` key whose constraint names an array is neither.** It carries its own `items`,
    and the value beside it is a list, so describing it as an object made the chart reject its own
    defaults the moment `just schema` ran — nine keys in `discord-alertmanager`, every one of which
    had to be corrected by hand.
    """
    constraint = constraint or {}

    if "enum" in constraint:
        members = list(constraint["enum"])
        if optional and None not in members:
            members.append(None)
        return {"enum": members}

    declared = constraint.get("type")
    names = declared if isinstance(declared, list) else [declared]
    types = [str(name) for name in names if name is not None]

    schema: dict[str, Any] = {}
    element = cc.element_schema(constraint)

    if structured and (not types or "object" in types) and "items" not in constraint:
        types = ["object"]
        # The producer's own fields, where it published any. A table key is normally split into
        # one contract key per field, so this is the hand-written `Sink` case rather than the
        # derive's — and dropping the fields there would describe a documented struct as an open
        # table.
        for keyword in ("required", "properties"):
            if keyword in constraint:
                schema[keyword] = _copy(constraint[keyword])
        # `additionalProperties: true` is the open flag, and an element schema replaces it: a map
        # whose values are all one shape is not open, it is uniform. Written even beside enumerated
        # properties, because helm-schema injects `additionalProperties: false` into a top-level
        # block that enumerates them and says nothing — and the contract's silence here means open,
        # not closed: `serde` accepts a field nobody declared unless the struct says otherwise.
        schema["additionalProperties"] = _copy(element) if element is not None else True
    else:
        for keyword in ORDER:
            if keyword in ("type", "enum") or keyword not in constraint:
                continue
            schema[keyword] = _copy(constraint[keyword])

    if not types:
        # The constraint names no type. Rather than guess one, accept every JSON type — the
        # contract is still the authority on the value, and `just check-config` holds the rendered
        # document against it either way. A `@schema` block is not optional here: the marker lives
        # inside one, and helm-schema emits no property for a value without one.
        types = ["string", "integer", "boolean", "array", "object", "null"]

    # An optional value is one the chart may legitimately leave unset, and the derived helper omits
    # it rather than writing it empty — so `null` has to be a value the schema accepts.
    if optional and "null" not in types:
        types.append("null")

    return {"type": types[0] if len(types) == 1 else types, **schema}


def _copy(value: Any) -> Any:
    """One constraint keyword's value, with every annotation dropped at every level.

    See the module docstring for why the prose does not come across. `format` is kept: it is the
    one member of `ANNOTATIONS` a validator acts on. Everything else is copied rather than
    translated — `constraint` is JSON Schema and so is an `@schema` block, so a `minimum` means the
    same thing on both sides.
    """
    if isinstance(value, dict):
        return {
            name: _copy(inner)
            for name, inner in value.items()
            if name not in cc.ANNOTATIONS or name == "format"
        }
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


# --------------------------------------------------------------------------------------------
# Declared divergences
# --------------------------------------------------------------------------------------------


def apply_divergences(
    generated: dict[str, Any], present: dict[str, Any], divergences: list[Divergence]
) -> tuple[dict[str, Any], list[str]]:
    """The generated schema with each declared position taken from the chart's own block.

    Returns the result and the problems found, rather than raising on the first: a value with two
    stale departures should report both.

    A sub-path is a dotted walk of *schema keywords* — `items.properties.guild_id`,
    `additionalProperties.items` — because that is what the thing being addressed is. It always has
    to resolve in the chart's own block, since that is where the kept text comes from; whether it
    resolves in the *generated* schema is what the two markers disagree about, and holding each to
    its own answer is what makes a departure age visibly:

    | Marker                 | In the generated shape | What it says                              |
    |------------------------|------------------------|-------------------------------------------|
    | `@config-shape-except` | present                | the contract describes this, and the chart |
    |                        |                        | overrides it — `guild_id` as a string      |
    | `@config-shape-narrow` | absent                 | the contract describes nothing here, and   |
    |                        |                        | the chart adds a bound — `sampleRate` 0..1 |

    Written the wrong way round, each is refused with the other named. That is not pedantry: a
    narrowing whose position the contract *starts* describing is exactly the moment somebody has
    to decide whether the producer's own bound supersedes the chart's, and an override whose
    position the contract *stops* describing is a keyword nothing upstream stands behind any more.
    Both are silent under a single marker that accepts either.
    """
    result = _deep_copy(generated)
    problems: list[str] = []

    for divergence in divergences:
        parts = divergence.sub_path.split(".")
        kept = _dig(present, parts)
        if kept is None:
            problems.append(
                f"{divergence.where}: keeps {divergence.sub_path!r} of "
                f"{divergence.values_path!r}, which the block below does not declare — there is "
                "nothing to keep"
            )
            continue

        described = _dig(generated, parts) is not None
        if divergence.kind == OVERRIDE and not described:
            problems.append(
                f"{divergence.where}: overrides {divergence.sub_path!r} of "
                f"{divergence.values_path!r}, which the generated schema does not contain. Either "
                f"the contract no longer describes it — in which case this is a narrowing and the "
                f"marker is `{NARROW_MARKER}` — or the sub-path is a typo"
            )
            continue
        if divergence.kind == NARROWING and described:
            problems.append(
                f"{divergence.where}: narrows {divergence.sub_path!r} of "
                f"{divergence.values_path!r}, which the contract now describes itself. Decide "
                f"between them: `{EXCEPT_MARKER}` keeps the chart's, and deleting the marker takes "
                "the image's"
            )
            continue

        if not described and _dig(result, parts[:-1]) is None:
            problems.append(
                f"{divergence.where}: narrows {divergence.sub_path!r} of "
                f"{divergence.values_path!r}, whose enclosing position the generated schema does "
                "not contain either, so there is nowhere to put it"
            )
            continue

        _put(result, parts, _deep_copy(kept))

    return _settle(result), problems


def _settle(schema: dict[str, Any]) -> dict[str, Any]:
    """One assembled block, with the pair helm-schema refuses resolved in the enum's favour.

    `expected` never emits `enum` beside `type` at the top level, because helm-schema exits
    `just schema` fatally on it and leaves every chart without a `values.schema.json`. A narrowing
    can reintroduce the pair — a chart that enumerates the members of a key the contract only types
    as a string is the ordinary case, and `telemetry.logLevel` is three of them — so the same rule
    is applied after the departures rather than only before. The enum wins because it is the
    stricter of the two and because it is the half somebody wrote down deliberately.
    """
    if "enum" in schema and "type" in schema:
        return {name: value for name, value in schema.items() if name != "type"}
    return schema


def _dig(schema: Any, parts: list[str]) -> Any:
    for part in parts:
        if not isinstance(schema, dict) or part not in schema:
            return None
        schema = schema[part]
    return schema


def _put(schema: dict[str, Any], parts: list[str], value: Any) -> None:
    for part in parts[:-1]:
        schema = schema[part]
    schema[parts[-1]] = value


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {name: _deep_copy(inner) for name, inner in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


# --------------------------------------------------------------------------------------------
# Writing it back out
# --------------------------------------------------------------------------------------------


def render(schema: dict[str, Any], indent: str) -> list[str]:
    """One `@schema` mapping as the comment lines a values.yaml carries it as."""
    return [f"{indent}# {line}" if line else f"{indent}#" for line in _lines(schema)]


def _lines(schema: dict[str, Any]) -> list[str]:
    """The schema as YAML, in `ORDER`, block form throughout.

    Block form rather than flow, because that is what every hand-written `@schema` in this
    repository uses and these lines are read by people first. Written here rather than handed to
    `yaml.dump`, which sorts keys alphabetically, wraps mid-value at 80 columns and quotes strings
    on rules of its own — three things that would each show up as churn in a generated file.
    """
    lines: list[str] = []
    for name in _ordered(schema):
        lines.extend(_keyword(name, schema[name]))
    return lines


def _ordered(schema: dict[str, Any]) -> list[str]:
    """The keywords of one level, canonical ones first and the rest alphabetically after."""
    known = [name for name in ORDER if name in schema]
    return known + sorted(name for name in schema if name not in ORDER)


def _keyword(name: str, value: Any) -> list[str]:
    """One JSON Schema keyword as the YAML lines an `@schema` comment carries."""
    if name == "type":
        return [_type_line(value)]
    if name == "properties" and isinstance(value, dict) and value:
        # Field names, not keywords. Kept in the order the producer declared them — that is the
        # order the struct is written in and the order its documentation reads in, and sorting
        # them would make a twenty-field element unrecognisable against the source it came from.
        lines = ["properties:"]
        for field, subschema in value.items():
            # Rendered here rather than through `_keyword`, which reads its first argument as a
            # schema keyword: a struct with a field called `type` or `items` would otherwise be
            # spelt as the keyword of that name and come out as something else entirely.
            if isinstance(subschema, dict) and subschema:
                lines.append(f"  {_field(field)}:")
                lines.extend(f"    {line}" for line in _lines(subschema))
            else:
                lines.append(f"  {_field(field)}: {{}}")
        return lines
    if isinstance(value, dict):
        if not value:
            return [f"{name}: {{}}"]
        return [f"{name}:"] + [f"  {line}" for line in _lines(value)]
    if isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
        lines = [f"{name}:"]
        for item in value:
            if isinstance(item, dict):
                rendered = _lines(item)
                lines.append(f"  - {rendered[0]}")
                lines.extend(f"    {line}" for line in rendered[1:])
            else:
                lines.append(f"  - {scalar(item)}")
        return lines
    return [f"{name}: {scalar(value)}"]


# Field names YAML 1.1 reads as something other than a string. `yes`, `no`, `on` and `off` are the
# ones that bite — a struct field called `on` is an ordinary field name in every language a
# producer here is written in, and unquoted it is the boolean true.
_PLAIN_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_YAML_WORDS = frozenset(
    {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "none", "~"}
)


def _field(name: str) -> str:
    """One struct field name, as the mapping key an `@schema` block carries it as."""
    if _PLAIN_FIELD.match(name) and name.lower() not in _YAML_WORDS:
        return name
    return quoted(name)


def _type_line(value: Any) -> str:
    """The `type` keyword, whose members are schema vocabulary rather than data.

    Written bare for that reason — `type: [array, 'null']`, the spelling every hand-written block
    in this repository uses. `null` is the one member that has to be quoted: unquoted it is YAML's
    null, and the schema would then declare no type at all where it meant to declare the null type.
    """
    names = value if isinstance(value, list) else [value]
    rendered = ", ".join("'null'" if str(name) == "null" else str(name) for name in names)
    return f"type: {rendered}" if len(names) == 1 else f"type: [{rendered}]"


def scalar(value: Any) -> str:
    """One JSON Schema keyword's value, as the YAML an `@schema` comment carries.

    `null` is the one that has to be quoted where it names a *type*: unquoted it is YAML's null,
    and a schema would then declare no type at all where it meant to declare the null type. Inside
    an `enum` it is the null value and is written bare, which is the spelling `image.pullPolicy` in
    the chassis already uses.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(scalar(item) for item in value) + "]"
    return quoted(str(value))


def quoted(text: str) -> str:
    """A YAML double-quoted scalar. Used wherever a value could be read as something else."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

# --------------------------------------------------------------------------------------------
# The walk over the charts
# --------------------------------------------------------------------------------------------


class Chart:
    """One chart's shape markers, resolved against its contracts.

    Everything that can fail before a single block is looked at fails here, with the chart named
    once: a `@config-shape` marker naming a value that does not exist, an exception with no shape
    to except from, a value that binds no key. The two callers then differ only in what they do
    with a block that does not match.
    """

    def __init__(self, chart_dir: Path):
        self.dir = chart_dir
        self.name = chart_dir.name
        self.values = chart_dir / "values.yaml"
        # Read without newline translation: this working tree is CRLF and a rewrite that
        # normalised it would reflow every line of every file it touched. `Path.open`
        # rather than `read_text(newline=...)`: that keyword is 3.13, and the recipes
        # resolve to whatever python is on PATH — the runner image's system one, in CI.
        with self.values.open(encoding="utf-8", newline="") as handle:
            self.text = handle.read()
        self.problems: list[str] = []

        self.transcribed, self.divergences = parse_markers(self.text, self.name)
        self.blocks = {
            block.values_path: block
            for block in cb.parse_blocks(self.values, self.name)
        }
        self.markers: dict[str, list[cb.Marker]] = {}
        for marker in cb.parse_values(self.values, self.name):
            self.markers.setdefault(marker.values_path, []).append(marker)

        declaration = load_declaration(chart_dir)
        self.bound = Bound(chart_dir, declaration) if declaration is not None else None
        self.app_version = str(read_yaml(chart_dir / "Chart.yaml").get("appVersion", "")).strip()

        self.generated = self._generated()
        self.shapes = self.generated + self.transcribed
        self._check_markers()

    def _generated(self) -> list[Shape]:
        """Every value whose block is derived, which is every bound value that keeps no marker.

        Enrolment was per value and opt-in until the `generated` marker was retired, and what that
        cost was measured rather than guessed: nine values across one chart were enrolled and 185
        were not, while 114 of those 185 carried a block byte-identical to the one the contract
        describes — hand copies that happened to still be right, with nothing holding them there.
        The default is now the other way round, so a key the image retypes moves the chart on the
        next `just config-shapes` whether or not anybody remembered to enrol the value.

        `composed` is the one binding class left out, and the reason is in `config_bindings.py`:
        such a value is *an input* to the key's text — `printf "0.0.0.0:%v"` over a port — so the
        key's constraint describes the composition and not the value, and generating from it would
        type the part as the whole.
        """
        shapes: list[Shape] = []
        declared = {shape.values_path for shape in self.transcribed}

        for values_path, markers in self.markers.items():
            if values_path in declared:
                continue
            if not any(marker.cls in DERIVED_CLASSES for marker in markers):
                continue
            block = self.blocks.get(values_path)
            shapes.append(
                Shape(
                    chart=self.name,
                    line=block.start if block is not None else markers[0].line,
                    values_path=values_path,
                    mode=GENERATED,
                    version=None,
                    source=None,
                    declared=False,
                )
            )

        return sorted(shapes, key=lambda shape: shape.line)

    # ------------------------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------------------------

    def _check_markers(self) -> None:
        """What can be decided from the markers alone, before a contract is opened.

        A value carries at most one shape marker, and a departure belongs to a generated block and
        says so by naming the same value.
        """
        seen: dict[str, Shape] = {}
        for shape in self.transcribed:
            first = seen.get(shape.values_path)
            if first is not None:
                self.problems.append(
                    f"{shape.where}: {shape.values_path!r} already declares a shape on line "
                    f"{first.line}. One value has one block, so the second marker is either a "
                    "duplicate or the migration of the first left half-done"
                )
                continue
            seen[shape.values_path] = shape

        generated = {shape.values_path for shape in self.generated}
        for divergence in self.divergences:
            if divergence.values_path in generated:
                continue
            if divergence.values_path in seen:
                self.problems.append(
                    f"{divergence.where}: departs from the generated shape of "
                    f"{divergence.values_path!r}, whose block is `{HANDWRITTEN}` and so is not "
                    "generated at all. A departure from a hand-written block is just the block"
                )
                continue
            self.problems.append(
                f"{divergence.where}: departs from the generated shape of "
                f"{divergence.values_path!r}, which carries no `{cb.MARKER}` marker naming a "
                "contract key, so nothing is generated there to depart from"
            )

    def constraint_for(self, shape: Shape, *, report: bool = True) -> Resolved | None:
        """The contract key one value feeds, resolved against every document that carries it.

        `None` when the value cannot be resolved. Reported unless the caller is only asking
        whether a resolution exists — a hand transcription in a chart with no contract at all
        is the ordinary state of one, not a defect.
        """
        def refuse(message: str) -> None:
            if report:
                self.problems.append(message)

        markers = [
            marker
            for marker in self.markers.get(shape.values_path, [])
            if marker.cls in DERIVED_CLASSES
        ]
        if not markers:
            refuse(
                f"{shape.where}: {shape.values_path!r} carries no `{cb.MARKER}` marker naming the "
                f"contract key it feeds, so there is nothing to derive its schema from"
            )
            return None
        if len(markers) > 1:
            refuse(
                f"{shape.where}: {shape.values_path!r} binds "
                f"{', '.join(repr(marker.target) for marker in markers)}, and a value feeding "
                "several keys has no single constraint to be derived from"
            )
            return None

        marker = markers[0]
        if self.bound is None:
            refuse(
                f"{shape.where}: {shape.values_path!r} binds {marker.target!r}, and this chart has "
                "no config-contract.yaml naming the document that declares it"
            )
            return None

        candidates = (
            list(marker.documents) if marker.documents is not None else sorted(self.bound.documents)
        )
        # Kept paired with the document that carried it, rather than flattened: which images
        # publish the key is what decides the release a hand transcription is held against, and
        # for a multi-service chart that is a strict subset of the documents it declares.
        carried = [
            (name, self.bound.namespace(name, marker.cls)[marker.target])
            for name in candidates
            if name in self.bound.documents
            and marker.target in self.bound.namespace(name, marker.cls)
        ]
        entries = [entry for _name, entry in carried]
        if not entries:
            # `check-config-bindings` reports this one properly, with the suggestions and the
            # scope diagnosti Repeating that here would print the same defect twice.
            refuse(
                f"{shape.where}: {shape.values_path!r} binds {marker.target!r}, which no contract "
                "this chart declares carries — see `just check-config-bindings`"
            )
            return None

        constraints = [entry.get("constraint") for entry in entries]
        if any(other != constraints[0] for other in constraints[1:]):
            refuse(
                f"{shape.where}: the documents this chart declares describe {marker.target!r} "
                "differently, so no one schema is derivable from them"
            )
            return None

        forms = {cc.text_form(entry) for entry in entries}
        names = {str(entry.get("ty") or "") for entry in entries}
        producers = tuple(
            Producer(document=name, version=version)
            for name, _entry in carried
            for version in self.bound.releases.get(name, ())
        )
        return Resolved(
            constraint=constraints[0],
            optional=marker.optional,
            structured=forms == {"structured"},
            ty=names.pop() if len(names) == 1 else "",
            producers=producers,
        )

    # ------------------------------------------------------------------------------------
    # One shape
    # ------------------------------------------------------------------------------------

    def block_for(self, shape: Shape) -> cb.Block | None:
        """The `@schema` block one shape describes, refusing a marker that is not above it.

        Placement is checked rather than assumed, for a shape a marker declares. The marker is
        matched to its value by name, so one written at the other end of the file would still
        resolve — and would then assert something about a block nobody reading that block can see.
        The rule is the one the surviving markers already follow: above the block, with nothing but
        comments and blank lines in between.

        A generated shape is not declared anywhere, so there is nothing to place: it *is* the
        block, found through the `@config` marker written inside it.
        """
        block = self.blocks.get(shape.values_path)
        if block is None:
            self.problems.append(
                f"{shape.where}: names {shape.values_path!r}, which has no `@schema` block for "
                "this marker to describe"
            )
            return None

        if not shape.declared:
            return block

        lines = self.text.splitlines()
        between = lines[shape.line : block.start - 1]
        if shape.line >= block.start or any(
            line.strip() and not line.lstrip().startswith("#") for line in between
        ):
            self.problems.append(
                f"{shape.where}: sits away from the `@schema` block for {shape.values_path!r} on "
                f"line {block.start}. A marker is read next to the thing it describes or it is "
                "not read at all; write it in the comment run directly above the block"
            )
            return None
        return block

    def target(self, shape: Shape) -> list[str] | None:
        """The lines one generated block's schema half should carry, or `None` on a problem."""
        block = self.block_for(shape)
        if block is None:
            return None

        resolved = self.constraint_for(shape)
        if resolved is None:
            return None
        constraint = resolved.constraint
        structured = resolved.structured
        ty = resolved.ty
        source = ty if ty and not re.search(r"[\s,]", ty) else "<source>"
        # The release the marker being asked for should record: the newest image publishing the
        # key, not the chart's `appVersion`, which in a multi-service chart is one service's.
        version = resolved.newest or self.app_version or "<version>"

        if structured and not cc.describes_element(constraint):
            self.problems.append(
                f"{shape.where}: the contract describes {shape.values_path!r} as a container and "
                "says nothing about what one element holds, so a generated block would type-check "
                f"nothing. Transcribe it and declare `{SHAPE_MARKER} {shape.values_path} "
                f"{HANDWRITTEN} {version} {source}` until the image publishes the element"
            )
            return None

        if not describing(constraint):
            self.problems.append(
                f"{shape.where}: the contract publishes no constraint for {shape.values_path!r}, "
                "so a generated block would accept every JSON type and check nothing. Transcribe "
                f"it and declare `{SHAPE_MARKER} {shape.values_path} {HANDWRITTEN} {version} "
                f"{source}`, which is held against the version it was read at"
            )
            return None

        generated = expected(constraint, optional=resolved.optional, structured=structured)
        try:
            present = block_schema(block)
        except ShapeError as failure:
            self.problems.append(f"{shape.where}: {failure}")
            return None

        mine = [
            divergence
            for divergence in self.divergences
            if divergence.values_path == shape.values_path
        ]
        wanted, problems = apply_divergences(generated, present, mine)
        self.problems.extend(problems)
        if problems:
            return None

        return render(wanted, " " * block.indent)


# --------------------------------------------------------------------------------------------
# Holding a transcription against a release
# --------------------------------------------------------------------------------------------


def _covers(read_at: str, published: str) -> bool:
    """Whether a block read at `read_at` still describes an image published at `published`.

    True when the two are the same release, and when `read_at` is the later of them: a copy read
    at 8.9.1 covers a service still at 8.9.0, at worst by narrowing it, and demanding a re-read
    there would fail a chart the moment its images stopped moving in lockstep.

    Ordering is consulted only when both releases parse as one. A tag nobody in this estate writes
    — a pre-release, a date, a bare word — degrades to `()` in `_ordinal`, and two of those would
    compare equal and pass; such a pair is held to equality instead, which errs towards asking for
    a re-read rather than towards a stale copy that looks maintained.
    """
    if cd.release(read_at) == cd.release(published):
        return True
    mine, theirs = _ordinal(read_at), _ordinal(published)
    return bool(mine) and bool(theirs) and mine >= theirs


def superseded(resolved: Resolved) -> bool:
    """Whether a hand-transcribed shape is one the contract now publishes itself.

    Two ways for that to become true, and they are the two reasons a transcription is allowed in
    the first place: a container whose element the producer had not described, and a key it
    published no constraint for at all. Either one arriving retires the hand copy.
    """
    if resolved.structured:
        return cc.describes_element(resolved.constraint)
    return describing(resolved.constraint)


def check_handwritten(chart: Chart, shape: Shape) -> None:
    """The older marker: a copy of a struct, held against the release it was copied at.

    That release is the newest *image publishing the key*, and only the chart's `appVersion` when
    no contract publishes it at all. The difference is the whole of this check on a multi-service
    chart: `tankovault` is nine images under one `appVersion`, and an automated bump moves the one
    that had a release — so holding every transcription against `appVersion` demanded that a
    struct owned by the eight that did not move be re-read at a release those eight were never
    built at. Two of `tankovault`'s three transcriptions were failing that way, and the version
    the message named did not exist for the image that owns them.

    Held against the newest rather than each one separately because the chart ships one `@schema`
    block per value: see `Resolved.newest`.
    """
    if chart.block_for(shape) is None:
        return

    resolved = chart.constraint_for(shape, report=False)
    if resolved is not None and superseded(resolved):
        chart.problems.append(
            f"{shape.where}: {shape.values_path!r} was transcribed by hand, and the contract now "
            f"describes it itself. Delete the marker and run `just config-shapes`, which "
            f"generates every bound value's block; declare any position the chart keeps with "
            f"`{EXCEPT_MARKER}` or `{NARROW_MARKER}`"
        )
        return

    published = resolved.newest if resolved is not None else ""
    if not published and not chart.app_version:
        chart.problems.append(
            f"{shape.where}: declares a shape for {shape.values_path!r}, but no contract records "
            "the release the key was published at and the chart has no appVersion either, so "
            "there is nothing to hold the transcription against"
        )
        return

    if not published:
        # No contract carries the key — an unbound transcription, whose only release is the
        # chart's own. Nothing here can be more precise than that, and equality is the check.
        if shape.version is not None and cd.release(shape.version) != cd.release(chart.app_version):
            chart.problems.append(
                f"{shape.where}: {shape.values_path!r} was transcribed at {shape.version}, and "
                f"the chart now pins appVersion {chart.app_version}\n"
                f"    re-read {shape.source} at {chart.app_version}, bring the `@schema` block "
                f"for {shape.values_path!r} up to it, and move the marker"
            )
        return

    read_at = shape.version or ""
    if _covers(read_at, published):
        return

    ahead = sorted(
        {one.document for one in resolved.producers if not _covers(read_at, one.version)}
    )
    chart.problems.append(
        f"{shape.where}: {shape.values_path!r} was transcribed at {shape.version}, and "
        f"{', '.join(ahead)} now publish{'es' if len(ahead) == 1 else ''} it at {published}\n"
        f"    re-read {shape.source} at {published}, bring the `@schema` block for "
        f"{shape.values_path!r} up to it, and move the marker"
    )


def rewrite(chart: Chart) -> tuple[str, int]:
    """The chart's values.yaml with every generated block written from its contract.

    Blocks are replaced from the bottom up so that an earlier replacement cannot move the line
    numbers a later one was read at.
    """
    lines = chart.text.splitlines(keepends=True)
    written = 0

    pending: list[tuple[cb.Block, list[str]]] = []
    for shape in chart.shapes:
        if shape.mode != GENERATED:
            continue
        target = chart.target(shape)
        if target is None:
            continue
        pending.append((chart.blocks[shape.values_path], target))

    ending = "\r\n" if chart.text.count("\r\n") else "\n"
    for block, target in sorted(pending, key=lambda item: item[0].start, reverse=True):
        markers, current = split_block(block)
        if current == target:
            continue
        body = [f"{line}{ending}" for line in markers + target]
        lines[block.start : block.end - 1] = body
        written += 1

    return "".join(lines), written


def check(chart: Chart) -> None:
    """Every generated block against what its contract calls for, reported onto the chart."""
    for shape in chart.shapes:
        if shape.mode != GENERATED:
            continue
        target = chart.target(shape)
        if target is None:
            continue

        block = chart.blocks[shape.values_path]
        _, current = split_block(block)
        if current == target:
            continue

        # Compared as text rather than as meaning, and the writer compares the same way. A block
        # that differs only in quoting is still a block a regeneration would rewrite, so a gate
        # that passed it would leave `just config-shapes` with a diff to commit on a tree it had
        # just called current — which is how a generated artefact ends up perpetually dirty.
        difference = "\n".join(
            difflib.unified_diff(
                current, target, fromfile="values.yaml", tofile="the contract", lineterm=""
            )
        )
        chart.problems.append(
            f"{shape.where}: the `@schema` block for {shape.values_path!r} is not what its "
            f"contract describes; run `just config-shapes`\n{difference}"
        )


def values_charts(charts: Path) -> list[Path]:
    """Every chart directory carrying a values.yaml, in a stable order."""
    return [
        chart
        for chart in sorted(charts.iterdir())
        if chart.is_dir() and (chart / "values.yaml").is_file()
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--charts", default=CHARTS_DIR, type=Path, help="charts directory (default: charts)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when a generated block is behind its contract, rather than writing it",
    )
    args = parser.parse_args(argv)

    if not args.charts.is_dir():
        print(f"error: {args.charts} is not a directory", file=sys.stderr)
        return 1

    problems: list[str] = []
    generated = 0
    transcribed = 0
    touched: list[str] = []

    for chart_dir in values_charts(args.charts):
        try:
            chart = Chart(chart_dir)
        except (ShapeError, cb.BindingError, DeclarationError) as failure:
            # Both parsers collect every problem in a file and raise them as one message. Split
            # again here so the closing count is a count of problems rather than of files.
            problems.extend(str(failure).splitlines())
            continue

        if not chart.shapes and not chart.divergences:
            continue

        for shape in chart.shapes:
            if shape.mode == HANDWRITTEN:
                transcribed += 1
                check_handwritten(chart, shape)
            else:
                generated += 1

        if args.check:
            check(chart)
        else:
            text, written = rewrite(chart)
            if written:
                with chart.values.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                touched.append(f"{chart.name} ({written} block(s))")

        problems.extend(chart.problems)

    # Every problem is reported before it exits, matching every other gate here: a run that stopped
    # at the first would hide the second on a bump that moved two charts.
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"\nerror: {len(problems)} config shape problem(s)", file=sys.stderr)
        return 1

    if args.check:
        print(
            f"==> {generated} generated and {transcribed} hand-transcribed config shape(s), "
            "all current"
        )
    elif touched:
        print(f"==> rewrote: {', '.join(touched)}")
    else:
        print(f"==> {generated} generated config shape(s) already match their contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
