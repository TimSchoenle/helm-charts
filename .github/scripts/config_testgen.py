#!/usr/bin/env python3
r"""Turning a configuration contract into helm-unittest cases that prove the round trip.

`just check-config` validates the document a chart *renders*. What no gate in this repository
can see is the round trip: that a setting an operator writes into the chart's values arrives in
the application's document, at the path the image reads it from, carrying the value that was
asked for. A typo in a template helper produces a document that satisfies the contract perfectly
— every key it does contain is legal — and the setting is simply absent. The pod starts, reports
healthy, and runs on a compiled default nobody chose, which is the same failure the contract
pipeline exists to remove, one layer further up.

Proving the round trip needs a *different* value on each side of it, so this module's whole job
is choosing one: a probe the chart cannot have produced by accident, synthesised from what the
contract says the key will accept. `check-config.py` supplies the reading half of the pipeline;
`generate-contract-tests.py` supplies the walking and the writing; this is the model, and it is
a pure function of a contract key so every rule below is testable by calling it.

Seven rules here are normative rather than convenient. The first four are about the probe, and
the last three about the shape of the chart it is written into:

**A probe must differ from the key's default.** A case that sets a key to the value it already
has passes whether or not the chart delivers anything, which is worse than no case at all: it
reports a proof it has not earned. Where no value other than the default is admissible — a
`const`, a single-entry `values` list — the key is skipped and says so.

**Three forms carry no probe, and the reason differs in each.** `secret: true` is refused
because the probe is committed to the repository, and a plausible-looking credential in a test
file is a credential. `structured` is refused because the keys beneath it are the operator's own
names — `internal.peers` is keyed by caller — so the contract describes the container and not
its contents, and nothing about a leaf inside it can be synthesised. `unknown` is refused
because the producer publishes no constraint at all, which is a gap stated explicitly rather
than an answer: no value can be known to be valid. These are three different situations and the
generated file names which one applies, so a reader is never left to guess whether a missing
case is a decision or an oversight.

**The assertion is scoped to the table the key belongs to, not merely to its leaf name.** In
TOML a key belongs to the most recent `[table]` header, so a pattern matching `ttl_secs = 4242`
anywhere in the document would accept a chart that wrote the value under the wrong table — which
is precisely the defect a round-trip test exists to catch, and the one a careless pattern waves
through. The generated pattern anchors on the header, walks only lines that do not open a new
table, and then matches the leaf; RE2 has no lookahead, so "a line that is not a header" is
spelled as an alternation rather than as `(?!\[)`.

**Regex metacharacters are escaped against the intersection of two engines.** The patterns are
written by Python and evaluated by Go, and the two disagree about which punctuation may carry a
backslash — `re.escape` escapes `-`, `&`, `~`, `#` and the space, and Go's parser is not
obliged to accept every one of those. So only the characters that are metacharacters in both
are escaped, and everything else is passed through as the literal it already is.

**A document is selected by the key it carries, and by its labels only where the key is not
enough.** `check-config.py` reads a document out of the key its declaration names, so selecting
on that same fact is what ties the two gates to one object — where a label selector on its own
matches the Deployment and the Service as well, which was measured rather than assumed. A chart
that renders the same key into several documents breaks that: TankoVault's nine services each
write their own `config.toml`, so the key identifies all nine and none of them. There the labels
the declaration already selects the document on are added to the key, and the two facts go into
one JSONPath filter rather than into two matchers, because helm-unittest's `documentSelector`
carries exactly one `path` and one `value` — measured against plugin 1.1.2, whose selector is a
single `yamlpath` expression evaluated per rendered manifest. The narrowing is applied only where
a key is shared, since a filter over labels that distinguish nothing would be longer without
proving more.

**Which values path a probe is written to is the chart's decision, not this module's.** Every
chart here merges its operator-facing configuration tree *over* whatever it derives, so a probe
written into that tree arrives in the document. One does the opposite: TankoVault merges
`.Values.config`, then the chart-derived wiring, then `services.<name>.config`, so a probe
written into the root tree is overwritten by the chart before it reaches the file and the case
fails for a reason that is not a defect. The root is therefore per document and stated in the
chart's enrolment rather than guessed at — the values key is `services.controlPlane.config` where
the document is named `control-plane`, so there is nothing to derive it from — and a document's
baseline is read under the same root, because the check that stops a baseline from supplying the
very value a case is probing is a comparison of the two paths as strings.

**A render prerequisite may never be written into the tree the probes are written to.** Several
charts here refuse their own default render — no target base, no webhook URL, no bucket entry —
so a suite for one of them needs values supplied before any of its cases renders at all, the case
that only checks the document's identity included. Those are the chart's own first-class values,
and a prerequisite is therefore carried by every case without exception rather than dropped on a
collision the way a baseline is. That is exactly why the collision has to be made unreachable
instead: a prerequisite writing under the probe root would sit in the same tree every case probes,
free to supply the very value a case exists to prove the chart delivered, and the suite would
report a proof it had not earned. The rule is stated against that root rather than against the
literal `config`, because the root is what the rule above made movable, and a refusal naming the
default would stop guarding the one chart that does not use it. A prerequisite the root nests
*inside* is refused for the neighbouring reason: it and the probe would be two entries of one
`set` mapping, one enclosing the other, and a `set` mapping has no order. So the refusal lives
here, in the model, and not only in the loader that reads the enrolment file — the rule belongs to
the suite, and a caller reaching past the loader would otherwise reach past the rule with it.

One limit is deliberate and cannot be closed from here. The probe is chosen to differ from the
*contract's* default, which is the image's compiled-in value; a chart is free to render some
other value when the key is unset, and if that value happens to equal the probe the case passes
without proving anything. For text the probe is a token nothing else would produce, so the
overlap is unreachable in practice; for a boolean there are only two values and the overlap is
real. Closing it would mean rendering the chart, which would make generation depend on helm and
on a values file — and the staleness gate would stop being a pure comparison of committed bytes.

A second limit belongs to the prerequisite rule and is stated rather than papered over. Refusing
the probe root makes a prerequisite unable to reach a probed key *directly*; a first-class chart
value that the chart derives a probed key from is a longer path to the same place, and no mapping
from one to the other exists in the contract for this module to consult. What closes it is what
the probe root is chosen to be — the layer that wins, whether that is `.Values.config` merged over
the derived tree or the per-document layer merged over both — so the probe outranks whatever a
prerequisite set. That is a property of the chart rather than of the generator, and the
enrolment's mandatory `reason` is where a chart says so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import config_contract as cc

# The values key every chart in this repository exposes for the operator's own configuration
# tree, merged over whatever the chart derives from its first-class values. It is what makes a
# chart-agnostic generator possible: every contract key is reachable as `config.<path>` whether
# or not the chart also spells it as a camelCase value of its own. A chart without one cannot be
# probed this way and is reported rather than guessed at.
#
# The default rather than the rule: a chart whose derived wiring outranks this tree names a
# higher-precedence one per document in its enrolment. See the module header.
VALUES_ROOT = "config"

# A values path a probe may be written under: dotted, and nothing else. The path is used twice —
# as a `set` prefix in the generated suite and as a walk into the chart's `values.yaml` — and the
# two agree on dotted segments and on nothing beyond them.
VALUES_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)*$")

# Text probes are built from this stem so a value appearing in a rendered document is
# unmistakably a test fixture and never a plausible setting somebody meant.
PROBE_STEM = "contract-probe"

# The first integer probe tried. A distinctive number rather than "the default plus one": a
# passing assertion should not be explicable as a coincidence, and `1` is a value half the charts
# in this repository render somewhere by accident. Anything the key's bounds refuse falls back to
# a search from the lower bound.
DISTINCTIVE_INTEGER = 4242

# How far the fallback search walks before giving up and skipping the key. A bound this narrow is
# reached only by a constraint no probe fits at all, which is a skip either way.
SEARCH_WIDTH = 16

# TOML's bare-key alphabet, mirroring `common.toml.key` in the library chart. A key outside it is
# emitted by the renderer as a quoted basic string, so the pattern has to expect one too.
BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")

# Characters that are metacharacters in both Python's `re` and Go's RE2. See the module header
# for why the intersection rather than `re.escape`.
METACHARACTERS = frozenset(r"\.+*?()|[]{}^$")

# One or more whole lines that do not open a new table: either a line whose first character is
# not `[`, or an empty one. This is what separates a table header from the key underneath it
# without running past the next header, and it is an alternation because RE2 has no lookahead.
GAP = r"(?:[^\[\n][^\n]*\n|\n)*"

# Forms a probe can be synthesised for. The other three are refused, each for its own reason;
# see `SKIPPED_FORMS` and the module header.
PROBED_FORMS = ("text", "integer", "boolean", "choice")

SKIPPED_FORMS = {
    "structured": (
        "`structured`: the keys beneath it are the operator's own names, so the contract "
        "describes the container and not its contents and no probe addresses a leaf"
    ),
    "unknown": (
        "`unknown`: the producer publishes no constraint for this key, so no probe can be "
        "known-valid"
    ),
}

SECRET_REASON = (
    "`secret: true`: the probe would be committed to this repository, and a credential-shaped "
    "value in a test file is a credential"
)

# Why a render prerequisite may not be written. Stated here because two callers refuse it — the
# loader that reads the enrolment file, which can name the file, and the model below, which
# cannot but must refuse it anyway. The two that name a tree are built from the probe root rather
# than from `VALUES_ROOT`, because the root is what a chart's enrolment may move.
PREREQUISITE_EMPTY = "is empty, so it names no value at all"

PREREQUISITE_INSIDE_PROBES = (
    "writes under `{root}`, which is the tree every case probes: a prerequisite there could "
    "supply the value a case exists to prove the chart delivered. A render prerequisite states "
    "the chart's own values, and nothing under `{root}` is one"
)

PREREQUISITE_AROUND_PROBES = (
    "encloses `{root}`, the tree every case probes: the prerequisite and the probe would be two "
    "entries of one `set` mapping with the second nested inside the first, and a `set` mapping "
    "has no order, so which of them survives is stated nowhere"
)


class TestGenError(Exception):
    """A contract or a chart this generator cannot turn into a suite."""


# --------------------------------------------------------------------------------------------
# Checking a candidate against what the contract will accept
# --------------------------------------------------------------------------------------------

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def unmet(value: Any, schema: dict[str, Any] | None) -> str | None:
    """The first assertion of a flat constraint the value fails, or `None` when it satisfies it.

    Deliberately a re-implementation of the keywords `config_contract.ASSERTIONS` names rather
    than a call into the JSON Schema engine: `jv` is an external binary installed by release URL
    for `just check-config`, and a generator that needed it could not run on a shell that has
    only python. The vocabulary is the same one the contract is already refused for stepping
    outside of, so the two cannot drift far — and a keyword outside it is reported by the caller
    as a key that cannot be probed rather than as a key that passes.
    """
    if not schema:
        return None

    for keyword, expected in sorted(schema.items()):
        if keyword in cc.ANNOTATIONS:
            continue
        failure = _unmet_keyword(value, keyword, expected)
        if failure is not None:
            return failure
    return None


def _unmet_keyword(value: Any, keyword: str, expected: Any) -> str | None:
    if keyword == "type":
        wanted = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, name) for name in wanted):
            return f"type {'/'.join(str(name) for name in wanted)}"
        return None

    if keyword == "enum":
        return None if value in expected else f"enum {json.dumps(expected)}"
    if keyword == "const":
        return None if value == expected else f"const {json.dumps(expected)}"

    if keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{keyword} applies to a number, and the candidate is not one"
        bounds = {
            "minimum": value >= expected,
            "maximum": value <= expected,
            "exclusiveMinimum": value > expected,
            "exclusiveMaximum": value < expected,
            "multipleOf": expected != 0 and value % expected == 0,
        }
        return None if bounds[keyword] else f"{keyword} {expected}"

    if keyword in ("pattern", "minLength", "maxLength"):
        if not isinstance(value, str):
            return f"{keyword} applies to a string, and the candidate is not one"
        if keyword == "pattern":
            try:
                matched = re.search(expected, value) is not None
            except re.error:
                return f"pattern {expected!r}, which cannot be evaluated here"
            return None if matched else f"pattern {expected!r}"
        length = len(value)
        satisfied = length >= expected if keyword == "minLength" else length <= expected
        return None if satisfied else f"{keyword} {expected}"

    return f"the assertion {keyword!r}, which this generator does not implement"


def _is_type(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    types = _TYPES.get(name)
    if types is None:
        return False
    # `True` is an `int` in Python and is not one in JSON Schema, so a boolean has to be excluded
    # from every numeric type explicitly or `true` would satisfy a `u64` bound.
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, types)


def out_of_vocabulary(schema: dict[str, Any] | None) -> list[str]:
    """Keywords in a flat constraint that are neither an assertion nor an annotation."""
    if not schema:
        return []
    return sorted(set(schema) - cc.ASSERTIONS - cc.ANNOTATIONS)


# --------------------------------------------------------------------------------------------
# Choosing a probe
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """One value to write into a chart's values, and what it should render as."""

    value: Any
    text: str


def probe_for(key: dict[str, Any]) -> tuple[Probe | None, str | None]:
    """The probe for one contract key, or the reason it carries none.

    Exactly one of the two is ever set, and the reason is written into the generated file
    verbatim — an unexplained absence is indistinguishable from an oversight, which for a file
    whose whole job is to be exhaustive is the worst possible failure mode.
    """
    path = key.get("path")
    if not isinstance(path, str) or not path:
        raise TestGenError("a contract key has no `path`")

    if key.get("secret"):
        return None, SECRET_REASON

    form = cc.text_form(key)
    if form in SKIPPED_FORMS:
        return None, SKIPPED_FORMS[form]
    if form not in PROBED_FORMS:
        return None, f"the form `{form}`, which this generator does not know how to probe"

    constraint = key.get("constraint") or {}
    text_constraint = key.get("text_constraint") or {}
    for name, schema in (("constraint", constraint), ("text_constraint", text_constraint)):
        outside = out_of_vocabulary(schema)
        if outside:
            return None, (
                f"`{name}` carries {', '.join(outside)}, which is outside the vocabulary this "
                "generator can satisfy"
            )

    default = key.get("default_value")
    for candidate in _candidates(form, path, key, constraint):
        if candidate == default and type(candidate) is type(default):
            continue
        failure = unmet(candidate, constraint)
        if failure is not None:
            continue
        text = toml_scalar(candidate)
        # The environment spelling of the same setting is what `text_constraint` governs, and a
        # probe that the chart could deliver through the file but not through the environment
        # would be a value the deployment cannot actually carry both ways. Checked against the
        # bare text rather than the TOML literal: `text_constraint` describes what a variable
        # holds, and a variable holds `4242`, not `"4242"`.
        if unmet(_environment_text(candidate), text_constraint) is not None:
            continue
        return Probe(value=candidate, text=text), None

    refused = f"`constraint` {json.dumps(constraint, sort_keys=True)}"
    if text_constraint:
        refused += f" and `text_constraint` {json.dumps(text_constraint, sort_keys=True)}"
    return None, (
        f"no value this generator can synthesise satisfies {refused} while also differing from "
        f"the default {json.dumps(default)}"
    )


def satisfying(key: dict[str, Any]) -> Any | None:
    """Any value one key's `constraint` accepts, or `None` when nothing here can synthesise one.

    The candidate walk without `probe_for`'s two extra demands. A probe has to *differ* from the
    published default — otherwise the case proves the chart delivered a value it would have got
    anyway — and it has to survive the environment spelling as well, so that what it asserts is a
    value the deployment could actually carry both ways. Neither applies to a caller that simply
    needs a legal value: `config_scaffold` writes one into a new chart's `values.yaml` for a
    required key whose image publishes no default, where "differs from the default" is vacuous
    and the environment layer is not involved at all.

    Public rather than left to a caller reaching for `_candidates`: the walk knows about
    `multipleOf`, exclusive bounds and the choice vocabulary, and a second implementation of it
    would be wrong in exactly the places this one was fixed.
    """
    constraint = key.get("constraint") or {}
    if out_of_vocabulary(constraint):
        return None
    try:
        form = cc.text_form(key)
    except cc.ContractError:
        return None
    if form not in PROBED_FORMS:
        return None

    for candidate in _candidates(form, str(key.get("path") or ""), key, constraint):
        if unmet(candidate, constraint) is None:
            return candidate
    return None


def _candidates(
    form: str, path: str, key: dict[str, Any], constraint: dict[str, Any]
) -> list[Any]:
    """Every value worth trying for one key, in the order they are preferred."""
    if form == "boolean":
        default = key.get("default_value")
        return [not default] if isinstance(default, bool) else [True, False]

    if form == "choice":
        values = key.get("values")
        if not isinstance(values, list):
            return []
        return list(values)

    if form == "integer":
        return _integer_candidates(constraint)

    return _text_candidates(path, constraint)


def _integer_candidates(constraint: dict[str, Any]) -> list[int]:
    """The distinctive probe first, then a walk up from the constraint's lower bound."""
    low = constraint.get("minimum")
    if low is None and "exclusiveMinimum" in constraint:
        low = constraint["exclusiveMinimum"] + 1
    start = int(low) if isinstance(low, (int, float)) else 0

    candidates = [DISTINCTIVE_INTEGER]
    candidates.extend(start + step for step in range(SEARCH_WIDTH))

    # A `multipleOf` refuses almost every value a linear walk produces, so the multiples are
    # offered as well rather than relying on the walk happening to land on one.
    step = constraint.get("multipleOf")
    if isinstance(step, int) and step > 0:
        candidates.extend(step * factor for factor in range(1, SEARCH_WIDTH + 1))

    return candidates


def _text_candidates(path: str, constraint: dict[str, Any]) -> list[str]:
    """A token naming the key it probes, adjusted to whatever length bounds the contract sets.

    Naming the key in the value is what makes a failure readable: the rendered document shows
    which setting went missing without a reader opening the contract.
    """
    stem = f"{PROBE_STEM}-{re.sub(r'[^A-Za-z0-9]+', '-', path).strip('-')}"

    candidates = [stem]
    minimum = constraint.get("minLength")
    if isinstance(minimum, int) and len(stem) < minimum:
        candidates.append(stem.ljust(minimum, "x"))
    maximum = constraint.get("maxLength")
    if isinstance(maximum, int) and 0 < maximum < len(stem):
        candidates.append(stem[:maximum])
    return candidates


def _environment_text(value: Any) -> str:
    """The candidate as a variable would hold it, which is what `text_constraint` describes."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --------------------------------------------------------------------------------------------
# Spelling the assertion
# --------------------------------------------------------------------------------------------


def escape(text: str) -> str:
    """Escape only what is a metacharacter to both Python and Go. See the module header."""
    return "".join(
        "\\" + character if character in METACHARACTERS else character for character in text
    )


def toml_key(name: str) -> str:
    """One TOML key as `common.toml.key` renders it: bare where TOML allows, quoted where not."""
    return name if BARE_KEY.match(name) else json.dumps(name, ensure_ascii=False)


def toml_scalar(value: Any) -> str:
    """One TOML scalar as `common.toml` renders it, which is `toRawJson`.

    A TOML basic string, integer, float and boolean are spelled exactly as their JSON
    equivalents, which is why the library chart emits every leaf through one conversion. Python's
    `json.dumps` is that same conversion; `ensure_ascii` is off because Go's marshaller does not
    escape non-ASCII either, and a `\\uXXXX` in the pattern would match a document that does not
    contain one.
    """
    return json.dumps(value, ensure_ascii=False)


def document_pattern(path: str, text: str) -> str:
    """A regex matching the leaf under the table its contract path names, and nothing else.

    In TOML a key belongs to the most recent `[table]` header, so the leaf name alone would
    accept a chart that wrote the value under the wrong table — which is one of the defects this
    suite exists to catch.
    """
    segments = path.split(".")
    if any(not segment for segment in segments):
        raise TestGenError(f"the contract path {path!r} has an empty segment")

    leaf = escape(f"{toml_key(segments[-1])} = {text}")
    if len(segments) == 1:
        # A key with no table sits above every header, so the walk starts at the document.
        return rf"(?m)\A{GAP}^{leaf}$"

    header = escape("[" + ".".join(toml_key(segment) for segment in segments[:-1]) + "]")
    return rf"(?m)^{header}$\n{GAP}^{leaf}$"


# --------------------------------------------------------------------------------------------
# Spelling the document selector
# --------------------------------------------------------------------------------------------


def jsonpath_string(text: str) -> str:
    """One string literal inside a JSONPath expression, double-quoted.

    Double quotes rather than single ones so the whole expression survives the single-quoted YAML
    scalar it is written into without every quote in it being doubled. A label or a key carrying
    a double quote or a backslash is refused instead of escaped: `yamlpath`'s lexer is not this
    repository's to reason about, and no object in it has ever carried one — a guess here would
    produce a selector that silently matches nothing.
    """
    if '"' in text or "\\" in text:
        raise TestGenError(
            f"{text!r} cannot be written into a document selector: a JSONPath string literal "
            "here carries neither a double quote nor a backslash"
        )
    return f'"{text}"'


def selector_path(key: str, discriminator: Sequence[tuple[str, str]]) -> str:
    """The `documentSelector` path for one document: its key, narrowed by labels where needed.

    helm-unittest evaluates this as a `yamlpath` expression against each rendered manifest and
    selects the manifest when it resolves to anything at all, so putting the labels into a filter
    over the document root and the key into the step after it is what makes one selector out of
    two facts. Its `documentSelector` has room for exactly one `path` and one `value`, which is
    why they are not spelled as two matchers.
    """
    if not discriminator:
        return f"data[{jsonpath_string(key)}]"

    predicate = " && ".join(
        f"@.metadata.labels[{jsonpath_string(label)}]=={jsonpath_string(value)}"
        for label, value in discriminator
    )
    return f"$[?({predicate})].data[{jsonpath_string(key)}]"


# --------------------------------------------------------------------------------------------
# The suite
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One probe, as the case that proves the chart delivers it."""

    path: str
    set_values: list[tuple[str, Any]]
    pattern: str


@dataclass(frozen=True)
class Skipped:
    """One key carrying no case, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class Plan:
    """Everything a suite is rendered from, sorted by contract key path."""

    cases: list[Case]
    skipped: list[Skipped]


def values_path(path: str, root: str = VALUES_ROOT) -> str:
    """Where an operator writes one contract key, as a helm-unittest `set` path."""
    return f"{root}.{path}"


def prerequisite_conflict(path: str, root: str = VALUES_ROOT) -> str | None:
    """Why one render prerequisite may not be written, or `None` when it may be.

    The whole of the guarantee named in the module header, in two comparisons of one string, so
    that a reader checks it by eye rather than by reasoning about how two trees merge. A caller
    that reports the conflict wraps this in whatever names the file it came from; `plan` below
    raises on it, because a suite built past this rule is a suite that proves less than it says.

    `root` is the values path the probes are written under, which the enrolment may move off
    `VALUES_ROOT` per document. Comparing against the root rather than against the default is the
    whole point: the rule exists to keep a prerequisite out of the tree the cases probe, and on a
    chart that probes `services.api.config` the tree to stay out of is that one. Both directions
    are refused, because either nesting puts two entries of one unordered `set` mapping inside
    each other.
    """
    if not path:
        return PREREQUISITE_EMPTY
    if path == root or path.startswith(f"{root}."):
        return PREREQUISITE_INSIDE_PROBES.format(root=root)
    if root.startswith(f"{path}."):
        return PREREQUISITE_AROUND_PROBES.format(root=root)
    return None


def plan(
    keys: Iterable[dict[str, Any]],
    baseline: Sequence[tuple[str, Any]],
    prerequisites: Sequence[tuple[str, Any]] = (),
    root: str = VALUES_ROOT,
) -> Plan:
    """Turn a union's keys into the cases and the skips of one suite.

    `baseline` is carried by every case except the one probing the key it sets. A chart may
    refuse to render a values combination the contract considers perfectly legal — portfolio
    fails the render when the inline-script hashes are off while the Cloudflare nonce is on —
    and a probe that walked into such a pair would fail for a reason that has nothing to do with
    the round trip. Dropping the entry that collides with the probe is what stops the baseline
    from supplying the very value the case is meant to prove the chart delivered.

    `prerequisites` is carried by every case with no exception at all, because it is what makes
    the chart render in the first place: a chart whose `validateValues` refuses its own defaults
    has no case that can do without one. So the collision the baseline is protected from by being
    dropped is instead made unreachable — a prerequisite path inside the probes' own tree is
    refused here rather than accommodated. The two fields are different things and this is the
    line between them.

    `root` is the values path a probe is written under: the chart's operator-facing configuration
    tree, or its higher-precedence per-document layer where that tree is outranked by what the
    chart derives. It is what the baseline and the prerequisites are both read against — the
    enrolment states the baseline under it, so the collision check stays a comparison of two
    strings, and the prerequisite refusal is measured against it, so moving the root moves the
    tree a prerequisite has to stay out of.
    """
    for name, _ in prerequisites:
        conflict = prerequisite_conflict(name, root)
        if conflict is not None:
            raise TestGenError(f"the render prerequisite {name!r} {conflict}")

    cases: list[Case] = []
    skipped: list[Skipped] = []

    for key in sorted(keys, key=lambda entry: entry.get("path") or ""):
        path = key["path"]
        probe, reason = probe_for(key)
        if probe is None:
            skipped.append(Skipped(path=path, reason=reason or "no reason given"))
            continue

        target = values_path(path, root)
        set_values = list(prerequisites)
        set_values.extend((name, value) for name, value in baseline if name != target)
        set_values.append((target, probe.value))
        cases.append(
            Case(path=path, set_values=set_values, pattern=document_pattern(path, probe.text))
        )

    return Plan(cases=cases, skipped=skipped)


# --------------------------------------------------------------------------------------------
# Rendering the suite
# --------------------------------------------------------------------------------------------

# Written by hand rather than through `yaml.dump`, and the reason is the header. A generated file
# nobody may edit has to say so on its first line and say why, and a dumper that cannot carry a
# comment would put the explanation nowhere a contributor reads it. Everything emitted below is a
# scalar or a two-level mapping, so what this owes YAML is small and the quoting rules are stated
# once, in `yaml_scalar`.
BANNER = "Generated by `just contract-tests`. Do not edit by hand."

# YAML scalars safe to emit bare. Deliberately narrow: anything outside it — a leading digit, a
# colon, a word YAML would read as a boolean — is single-quoted instead, which is always correct
# and never changes the type helm-unittest sees.
PLAIN_SCALAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")

# Where a wrapped comment line stops. Matches the width the rest of this repository's Python and
# `just` files are written to.
COMMENT_WIDTH = 96

# A selector path YAML reads back unchanged as a plain scalar. `data["config.toml"]` is one by
# YAML's block-context rules; the filter form opens with `$[?(` and carries `&&`, and quoting that
# is cheaper than making every reader confirm none of it is an indicator.
PLAIN_SELECTOR = re.compile(r'^[A-Za-z_][A-Za-z0-9_."\[\]/-]*$')


def yaml_scalar(value: Any) -> str:
    """One scalar, quoted whenever bare would be ambiguous or would change its type."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    return text if PLAIN_SCALAR.match(text) else yaml_quoted(text)


def yaml_value(value: Any) -> str:
    """One value on the right of a `set` entry, whatever its shape, on a single line.

    A render prerequisite may be a whole subtree — `bucket.entries` is a map of maps keyed by
    request path — and `json.dumps` produces a YAML flow collection as readily as a JSON one, so
    the conversion that already spells a TOML scalar spells a structure here too. Kept to one line
    and sorted, for the two reasons everything in this module is: a `set` block that stays a flat
    list of paths is one a reader scans, and a gate comparing bytes cannot survive a mapping whose
    order is whatever Python's happened to be.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return yaml_scalar(value)


def yaml_quoted(text: str) -> str:
    """A single-quoted YAML scalar, which is the one form that keeps a backslash literal.

    Every pattern this module produces is dense with backslashes, and a double-quoted scalar
    would consume them as YAML escapes before the regex engine ever saw them.
    """
    return "'" + text.replace("'", "''") + "'"


def comment(lines: Iterable[str], indent: str = "") -> list[str]:
    return [f"{indent}#" if not line else f"{indent}# {line}" for line in lines]


@dataclass(frozen=True)
class Target:
    """The document a suite is written for, as the declaration and the enrolment describe it.

    The last two fields carry defaults because they are what every chart but one looks like: a
    document whose key identifies it on its own, probed through the configuration tree the chart
    merges over its derived wiring. Stating them as defaults rather than as required arguments is
    also what keeps a suite for such a chart byte-identical to the one generated before either
    field existed.
    """

    chart: str
    name: str
    kind: str
    selector: dict[str, str]
    key: str
    declaration: str
    contracts: tuple[str, ...]

    # Labels that tell this document from its siblings, empty when the key already does. See
    # `selector_path` and the module header.
    discriminator: tuple[tuple[str, str], ...] = ()

    # The values path a probe is written under. See the module header.
    root: str = VALUES_ROOT


def render_suite(
    target: Target,
    plan: Plan,
    baseline: Sequence[tuple[str, Any]],
    reason: str | None,
    prerequisites: Sequence[tuple[str, Any]] = (),
    prerequisite_reason: str | None = None,
) -> str:
    """The complete helm-unittest suite for one document, as the text to write.

    Deterministic in every part: cases sorted by contract key path, skips with them, selector
    labels sorted, and nothing that churns on its own — a digest, a timestamp, an application
    version — written into the file at all. That is what lets the staleness gate be a comparison
    of bytes, and it is what keeps a Renovate digest bump that changes no key from producing a
    diff here.

    `reason` is the enrolment's explanation for whatever it made non-default about this document
    — a baseline, a probe root, or both — and is rendered once beside them. The prerequisites
    carry their own, because they are a different field explaining a different thing.
    """
    lines = comment(_preamble(target, plan))
    lines.append("")
    lines.append(f"suite: {yaml_scalar(f'contract round trip ({target.name})')}")
    lines.append("")
    lines.extend(comment(_release_note()))
    lines.append("release:")
    lines.append(f"  name: {yaml_scalar(target.chart)}")
    lines.append("")
    if prerequisites:
        lines.extend(comment(_prerequisite_note(target.root, prerequisite_reason)))
    lines.append("tests:")
    lines.extend(_identity_case(target, prerequisites))

    notes = _case_notes(target, baseline, reason)
    if notes:
        lines.append("")
        lines.extend(comment(notes, indent="  "))

    for case in plan.cases:
        lines.append("")
        lines.extend(_probe_case(case, target))

    if plan.skipped:
        lines.append("")
        lines.extend(comment(_skipped_note(plan.skipped), indent="  "))

    return "\n".join(lines) + "\n"


def _preamble(target: Target, plan: Plan) -> list[str]:
    covered = len(plan.cases)
    total = covered + len(plan.skipped)
    return [
        BANNER,
        "",
        *_wrap(
            f"Every case below writes one setting into `{target.root}` and asserts that it "
            f"arrives in `{target.key}`, under the table its contract path names. That is the "
            "round trip no other gate can see: `just check-config` proves the rendered document "
            "satisfies the contract, and a document missing a setting entirely satisfies it "
            "perfectly.",
            COMMENT_WIDTH - 2,
        ),
        "",
        f"Chart:       {target.chart}",
        f"Declaration: {target.declaration} (document `{target.name}`)",
        *[f"Contract:    {path}" for path in target.contracts],
        f"Coverage:    {covered} of {total} contract keys carry a probe",
        "",
        *_wrap(
            "Regenerate with `just contract-tests`. `just check-contract-tests` fails a pull "
            "request whose committed copy has drifted from the contract it was generated "
            "against.",
            COMMENT_WIDTH - 2,
        ),
    ]


def _release_note() -> list[str]:
    return _wrap(
        "`just render` installs every chart under a release named after it, and the "
        "declaration's selector is written against that render. Naming the release the same way "
        "here is what makes `app.kubernetes.io/instance` carry the value the declaration selects "
        "on.",
        COMMENT_WIDTH - 2,
    )


def _identity_case(target: Target, prerequisites: Sequence[tuple[str, Any]]) -> list[str]:
    lines = [
        *comment(_wrap(_selection_note(target), COMMENT_WIDTH - 4), indent="  "),
        f"  - it: renders {target.key} on the {target.kind} the declaration selects",
        *_selector(target),
        # This case sets nothing of its own, and still carries the prerequisites: without them
        # the chart's guard refuses the render, and a case asserting on a document that was never
        # produced fails for a reason that has nothing to do with what it checks.
        *_set_block(prerequisites),
        "    asserts:",
        "      - isKind:",
        f"          of: {yaml_scalar(target.kind)}",
    ]
    for label, value in sorted(target.selector.items()):
        lines.extend(
            [
                "      - equal:",
                f'          path: metadata.labels["{label}"]',
                f"          value: {yaml_scalar(value)}",
            ]
        )
    return lines


def _selector(target: Target) -> list[str]:
    """The document selector, matching on the key existing rather than on its contents."""
    path = selector_path(target.key, target.discriminator)
    scalar = path if PLAIN_SELECTOR.match(path) else yaml_quoted(path)
    return ["    documentSelector:", f"      path: {scalar}"]


def _selection_note(target: Target) -> str:
    """Why every case below selects the document it does, in whichever form actually applies."""
    if not target.discriminator:
        return (
            "Every case below selects its document by the key it carries, which is what "
            "`check-config.py` reads it from — a label would match the Deployment and the "
            "Service too. This case is what ties that selection back to the declaration: the "
            f"object holding `{target.key}` has to be the {target.kind} the declaration names, "
            "carrying the labels the declaration selects on."
        )

    labels = ", ".join(f"`{label}: {value}`" for label, value in target.discriminator)
    return (
        f"Every case below selects its document by the key it carries and by {labels}, because "
        f"this chart renders `{target.key}` into more than one document and the key on its own "
        "identifies all of them at once. The labels on their own would not do it either — they "
        "are on the workload and its Service as well — so the two are spelled as one JSONPath "
        "filter, which is what helm-unittest's single-matcher selector has room for. This case "
        f"is what ties that selection back to the declaration: the object holding `{target.key}` "
        f"has to be the {target.kind} the declaration names, carrying the labels the declaration "
        "selects on."
    )


def _set_block(values: Sequence[tuple[str, Any]]) -> list[str]:
    """One case's `set` mapping, or nothing at all when it has no values to write."""
    if not values:
        return []
    lines = ["    set:"]
    for name, value in values:
        lines.append(f"      {yaml_scalar(name)}: {yaml_value(value)}")
    return lines


_BASELINE_NOTE = (
    "Every case below also carries the chart's baseline values, minus whichever of them the case "
    "is itself probing — a baseline that supplied the probed value would make the assertion pass "
    "whether or not the chart delivered anything. The baseline exists because a chart may refuse "
    "to render a combination the contract considers legal, and a probe that walked into one "
    "would fail for a reason that has nothing to do with the round trip."
)


def _root_note(root: str) -> str:
    return (
        f"Every case below writes its probe into `{root}` rather than into `{VALUES_ROOT}`, "
        "which the chart's enrolment states because this chart merges its derived wiring *over* "
        f"`{VALUES_ROOT}` rather than under it. A probe written into `{VALUES_ROOT}` for a key "
        "the chart derives would be overwritten before it reached the document, and the case "
        f"would fail on a chart that is behaving correctly. What that costs is real: `{root}` is "
        f"the layer these cases prove, and nothing here proves `{VALUES_ROOT}` still reaches a "
        "key the chart does not derive."
    )


def _case_notes(
    target: Target, baseline: Sequence[tuple[str, Any]], reason: str | None
) -> list[str]:
    """Whatever the enrolment made non-default about these cases, and the reason it gives.

    One block carrying one reason rather than a note per field: the document entry states a
    single `reason` for its baseline and its probe root alike, and a reader who sees the same
    paragraph twice stops reading it. The render prerequisites are not here — they are a separate
    field with a separate reason, and their note sits above `tests:` because the identity case
    carries them too.
    """
    paragraphs: list[list[str]] = []
    if baseline:
        paragraphs.append(_wrap(_BASELINE_NOTE, COMMENT_WIDTH - 4))
    if target.root != VALUES_ROOT:
        paragraphs.append(_wrap(_root_note(target.root), COMMENT_WIDTH - 4))
    if not paragraphs:
        return []
    if reason:
        paragraphs.append(_wrap(reason.strip(), COMMENT_WIDTH - 4))

    lines: list[str] = []
    for paragraph in paragraphs:
        if lines:
            lines.append("")
        lines.extend(paragraph)
    return lines


def _prerequisite_note(root: str, reason: str | None) -> list[str]:
    lines = _wrap(
        "Every case below carries this chart's render prerequisites, the identity case included: "
        "chart values its own `validateValues` insists on before it will render anything at all. "
        "They are first-class chart values rather than configuration, and none of them may be "
        f"written under `{root}` — a prerequisite there would sit in the tree every case probes, "
        "free to supply the value the case exists to prove the chart delivered. Unlike a "
        "baseline, a prerequisite is never dropped for the case it would collide with, because a "
        "case without it does not render at all; the refusal is what takes that dropping's place.",
        COMMENT_WIDTH - 2,
    )
    if reason:
        lines.append("")
        lines.extend(_wrap(reason.strip(), COMMENT_WIDTH - 2))
    return lines


def _probe_case(case: Case, target: Target) -> list[str]:
    lines = [
        f"  - it: delivers {case.path} into {target.key}",
        *_selector(target),
        *_set_block(case.set_values),
    ]
    lines.extend(
        [
            "    asserts:",
            "      - matchRegex:",
            f'          path: data["{target.key}"]',
            f"          pattern: {yaml_quoted(case.pattern)}",
        ]
    )
    return lines


def _skipped_note(skipped: Sequence[Skipped]) -> list[str]:
    lines = _wrap(
        "Contract keys carrying no case, and why. An unexplained absence is indistinguishable "
        "from an oversight, so every one of them is named here rather than simply left out.",
        COMMENT_WIDTH - 4,
    )
    lines.append("")
    for entry in skipped:
        indent = " " * (len(entry.path) + 2)
        wrapped = _wrap(entry.reason, COMMENT_WIDTH - 4 - len(indent))
        lines.append(f"{entry.path}: {wrapped[0]}")
        lines.extend(f"{indent}{line}" for line in wrapped[1:])
    return lines


def _wrap(text: str, width: int) -> list[str]:
    """Greedy wrap on spaces alone, so a reason reads as prose rather than as one long line.

    Hand-rolled rather than `textwrap` because these reasons quote JSON and constraint spellings
    that a general wrapper would break on punctuation this keeps together.
    """
    limit = max(width, 32)
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > limit:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    lines.append(current)
    return lines
