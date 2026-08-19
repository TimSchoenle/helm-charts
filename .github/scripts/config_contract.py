#!/usr/bin/env python3
"""The configuration contract an image publishes, and the rules for reading it.

An image built on `terrace-config` publishes one JSON document describing every setting the
binary inside it actually reads: the TOML paths, the environment spellings derived from them,
the secret-file spellings derived from those, the variables it reads that are nobody's
configuration (`PORT`, `RUST_LOG`), and a JSON Schema for the document itself. The document is
attached to the image digest as an OCI referrer and embedded in the image; this repository
vendors a copy per chart under `charts/<chart>/contracts/`.

This module is the half of the pipeline worth testing without a registry, a cluster or a render:
parsing the envelope, unioning the contracts of several images that read one document, and
classifying a single environment variable. `check-config.py` supplies the manifests and the
reporting; `refresh-contracts.py` supplies the network.

Three rules in here are normative rather than convenient, and a consumer that gets any of them
wrong disagrees with the loader about whether a deployment boots:

**The classification order** (`classify`) is copied from `External`'s own documentation. First
match wins, and the step that rejects an unaccounted-for prefixed variable sits *above* both
external lists — so neither an `external.env` entry nor an `ignore` pattern can reach into the
loader's namespace and quietly exempt a real key. The producer refuses to build a contract that
tries, and this order is the consumer-side half of the same guarantee.

**A value is checked twice** (`check_text`, then `check_parsed`), and **`text_form` decides the
parse**. The form step puts the raw characters against `text_constraint` and rejects
`PORT: "http"`. The range step reads the text as `text_form` names — `integer` means parse it as
an integer — and checks the result against `constraint`, which is the only place a `minimum` or
`maximum` is reachable from: a pattern matches characters, so `99999` is a well-formed integer
and only the bound catches it not fitting a `u16`. Checking only the text leaves every bound
decorative; checking `constraint` against the raw text instead rejects `"0"` for an integer key,
which is a correct deployment refused.

`text_form` rather than the shape of the constraint object is load-bearing. Inferring "a
`pattern` means integer, an `enum` means boolean" was right while there were two shapes and wrong
the moment `structured` arrived — and `PORTFOLIO_GITHUB__REPOS=a,b`, which reads like a list and
is not one, is exactly the deployment that inference waved through.

**A file can only supply a `text` key.** The secrets directory and `_FILE` indirection deliver
their contents as strings with no parse, and `Figment::extract` does not coerce a string into a
number, a boolean or a TOML literal. So a key of any other form cannot be supplied by either —
not "must match a pattern", cannot be supplied — and `file_supplyable` says so from `text_form`
alone.

One limit is deliberate and cannot be closed here: a 64-bit range is not checkable. `u64::MAX`
is not representable as an IEEE double, so the producer publishes no `maximum` rather than a
wrong one, and `18446744073709551616` satisfies every constraint in the document and still fails
to load. `text_form: unknown` is the same situation stated explicitly — a domain newtype, a
float, a type the producer refuses to guess at — and means "no check possible", never an error.
It is deliberately distinct from `text`, which means "any text is correct": one is a gap, the
other is an answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------------------------
# What the image publishes
# --------------------------------------------------------------------------------------------

# The OCI artifact type the contract is attached to the image digest under, and the labels on the
# image config that make it discoverable from the config blob alone. Only `refresh-contracts.py`
# uses these; they live here so the two scripts cannot disagree about a spelling.
#
# Three labels, and all three are constants a Dockerfile can carry in one `LABEL` block. An
# earlier draft added a fourth holding the document's hash, which was the only dynamic one — and
# a multi-stage build cannot feed it from a generator that runs inside a builder stage without
# running the generator twice. What it bought was that the embedded file and the attached
# artifact are the same document, which is a *build* failure the pipeline holding both copies
# catches for nothing. Everything downstream reads the registry artifact, and a registry
# content-addresses its blobs, so the integrity anchor was never the label.
ARTIFACT_TYPE = "application/vnd.terrace.config-schema.v1+json"
LABEL_VERSION = "dev.terrace.config.contract.version"
LABEL_PATH = "dev.terrace.config.contract.path"
LABEL_PREFIX = "dev.terrace.config.prefix"

# The envelope's own version, independent of the `schema_version` inside it. A consumer that does
# not recognise it refuses the document by name rather than misreading it.
ENVELOPE_VERSION = 1

# Keywords a flat `constraint` / `text_constraint` may carry. Anything outside both sets is a
# vocabulary this validator does not implement, and that is an error rather than a silent skip:
# under-checking a value is exactly the failure this whole pipeline exists to remove.
ASSERTIONS = frozenset(
    {
        "type",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "minLength",
        "maxLength",
    }
)
ANNOTATIONS = frozenset({"description", "title", "default", "examples", "$comment", "format"})

# How to read a variable's text, as `text_form` names it. Every key and every declared external
# variable carries one, and it — not the shape of the constraint object — is what says which
# parse to use. A consumer inferring "a `pattern` means integer, an `enum` means boolean" was
# right while there were two shapes and wrong the moment `structured` arrived.
TEXT_FORMS = frozenset({"text", "integer", "boolean", "choice", "structured", "unknown"})

# Forms with no second step: `text` because there is nothing to parse, `unknown` because nothing
# is known to parse it as. The difference matters — one is an answer, the other is a gap.
NO_RANGE_FORMS = frozenset({"text", "structured", "unknown"})

# The text figment's `Env` provider accepts, as a superset. Measured against the loader rather
# than derived from TOML's grammar: its parse is neither TOML's nor `str::parse`'s. A pattern
# that rejected text the loader accepts would stop a deployment that was correct.
INTEGER_TEXT = re.compile(r"^\s*[+-]?[0-9]+\s*$")
BOOLEAN_TEXT = {"true": True, "false": False}

# A `structured` value is a TOML literal — an array or an inline table — and the brackets are the
# whole point. `PORTFOLIO_GITHUB__REPOS=a,b` is the defect this form exists to name: it reads
# like a list, is not one, and passed every gate that predated `text_form`.
STRUCTURED_TEXT = re.compile(r"^\s*(\[.*\]|\{.*\})\s*$", re.DOTALL)

# The one field that unions rather than having to agree: a key any reader requires must be
# present. Everything else about an entry — and every keyword of a schema — must match, which is
# a catch-all on purpose. Enumerating the fields that matter means the next field the producer
# adds falls through the gap in silence, and two images disagreeing about a key's `maximum` is
# the same defect as disagreeing about its `type`: one contract accepts a value the other
# refuses.
UNIONED_FIELDS = frozenset({"required"})

# This module's own bookkeeping, threaded through merged entries and never compared.
INTERNAL_FIELDS = frozenset({"_sources"})

# `external.unknown` policies, weakest first. A union takes the strictest: the document and the
# pod are shared, so a variable any reader refuses is a variable the deployment cannot carry.
UNKNOWN_POLICIES = ("allow", "warn", "reject")


class ContractError(Exception):
    """A contract that cannot be read, or two that cannot be reconciled."""


# --------------------------------------------------------------------------------------------
# Reading a vendored contract
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Vendored:
    """One `charts/<chart>/contracts/<name>.json`: a published contract and its provenance.

    The published document deliberately carries no image digest — a digest is what building the
    image *produces*, so a field holding it would have to be written after the push, changing the
    bytes the `dev.terrace.config.contract.sha256` label was computed over. The tie is the
    attachment instead: whatever comes back from asking a digest for its referrers belongs to
    that digest. The chart repository records which digest that was on the way in, and that
    record is what the staleness interlock in `check-config.py` reads.
    """

    path: Path
    image: str
    digest: str
    sha256: str
    fetched: str
    contract: dict[str, Any]


def load_vendored(path: Path) -> Vendored:
    """Read and shape-check one vendored contract.

    `source.sha256` is over the document *as published*, so it is verifiable only against the
    image label, which needs the registry — `refresh-contracts.py` is what checks it, before it
    ever writes the file. Offline it is provenance: it records which bytes were verified, so a
    later networked run can prove the committed copy is still the one the image carries.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ContractError(f"{path}: cannot be read: {error}") from error
    except json.JSONDecodeError as error:
        raise ContractError(f"{path}: is not valid JSON: {error}") from error

    if not isinstance(document, dict):
        raise ContractError(f"{path}: expected an object at the top level")

    source = document.get("source")
    if not isinstance(source, dict):
        raise ContractError(f"{path}: missing the `source` provenance object")
    for name in ("image", "digest", "sha256", "fetched"):
        if not isinstance(source.get(name), str) or not source[name]:
            raise ContractError(f"{path}: `source.{name}` is missing or not a string")
    if not source["digest"].startswith("sha256:"):
        raise ContractError(f"{path}: `source.digest` is not a sha256 digest: {source['digest']}")

    contract = document.get("contract")
    if not isinstance(contract, dict):
        raise ContractError(f"{path}: missing the `contract` document")
    check_envelope(contract, str(path))

    return Vendored(
        path=path,
        image=source["image"],
        digest=source["digest"],
        sha256=source["sha256"],
        fetched=source["fetched"],
        contract=contract,
    )


def check_envelope(contract: dict[str, Any], origin: str) -> None:
    """Refuse a document this consumer does not recognise, by name rather than by symptom."""
    version = contract.get("terrace_contract")
    if version != ENVELOPE_VERSION:
        raise ContractError(
            f"{origin}: `terrace_contract` is {version!r}, and this repository reads "
            f"{ENVELOPE_VERSION}. Update .github/scripts/config_contract.py before vendoring it."
        )
    for section in ("schema", "json_schema", "external"):
        if not isinstance(contract.get(section), dict):
            raise ContractError(f"{origin}: missing the `{section}` section")

    schema = contract["schema"]
    dialect = schema.get("dialect")
    if not isinstance(dialect, dict):
        raise ContractError(f"{origin}: `schema.dialect` is missing")
    for name in ("prefix", "nesting_separator", "indirection_suffix"):
        if not isinstance(dialect.get(name), str):
            raise ContractError(f"{origin}: `schema.dialect.{name}` is missing")
    for section in ("loader", "keys"):
        if not isinstance(schema.get(section), list):
            raise ContractError(f"{origin}: `schema.{section}` is missing or not a list")

    external = contract["external"]
    for section in ("env", "ignore"):
        if not isinstance(external.get(section), list):
            raise ContractError(f"{origin}: `external.{section}` is missing or not a list")
    if external.get("unknown") not in UNKNOWN_POLICIES:
        raise ContractError(
            f"{origin}: `external.unknown` is {external.get('unknown')!r}, "
            f"expected one of {', '.join(UNKNOWN_POLICIES)}"
        )

    # Every key and every declared external variable carries a `text_form`, and it is what every
    # value check reads. Refusing the document here rather than on the first value that needs it
    # keeps a half-readable contract from validating half a deployment and reporting success.
    for entry in schema["keys"] + external["env"]:
        if isinstance(entry, dict):
            text_form(entry)


# --------------------------------------------------------------------------------------------
# The union
# --------------------------------------------------------------------------------------------


@dataclass
class Union:
    """The contracts of every image that reads one document, merged into one description.

    `tankovault` renders a single `config.toml` read by eight separate binaries under one prefix.
    Each binary's contract covers only the keys it consumes, so validating that document against
    one binary's schema with `additionalProperties: false` would reject a perfectly correct
    deployment: every key belonging to the other seven would be "unknown". The object to validate
    against is the union.
    """

    sources: list[str] = field(default_factory=list)
    dialect: dict[str, str] = field(default_factory=dict)
    loader: dict[str, dict[str, Any]] = field(default_factory=dict)
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)
    external_env: dict[str, dict[str, Any]] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)
    unknown: str = "reject"
    json_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def prefix(self) -> str:
        return self.dialect["prefix"]

    @property
    def indirection_suffix(self) -> str:
        return self.dialect["indirection_suffix"]

    def key_by(self, spelling: str, name: str) -> dict[str, Any] | None:
        """The key whose `env` / `env_file` / `secrets_file` is `name`, if any."""
        for key in self.keys.values():
            if key.get(spelling) == name:
                return key
        return None


def union_contracts(items: Sequence[tuple[str, dict[str, Any]]]) -> Union:
    """Merge the contracts of every image that reads one document.

    | Situation                                         | Rule                              |
    |---------------------------------------------------|-----------------------------------|
    | a path in one contract only                       | keep                              |
    | a path in several, identical                      | keep once                         |
    | `required`                                        | union — any reader requiring wins |
    | `additionalProperties`                            | `false` at every level            |
    | **any other keyword in two schemas, different**   | **hard error**                    |

    The last row is a catch-all on purpose, and it is the row to get right. Naming `type` and
    `enum` and leaving the rest to last-one-wins is a rule nobody wrote down: two images
    disagreeing about a key's `maximum` is the same defect as disagreeing about its `type` — one
    contract accepts a value the other refuses — and enumerating keywords means the next one the
    producer adds falls through the gap in silence. `minimum`, `maximum`, `items`, `default`,
    `description`, `$schema` and `$id` are all covered by saying it once.

    A `dialect` mismatch is the same failure one level up: two images reading one document under
    different spelling rules do not share a namespace at all.

    `items` is `(label, contract)` pairs; the label names the vendored file in every message.
    """
    if not items:
        raise ContractError("no contracts to union")

    merged = Union(sources=[label for label, _ in items])
    first_label, first = items[0]
    merged.dialect = dict(first["schema"]["dialect"])
    merged.unknown = first["external"]["unknown"]

    ignore: set[str] = set()
    for label, contract in items:
        schema = contract["schema"]

        if schema["dialect"] != merged.dialect:
            raise ContractError(
                f"{label} and {first_label} read one document under different dialects: "
                f"{json.dumps(schema['dialect'], sort_keys=True)} vs "
                f"{json.dumps(merged.dialect, sort_keys=True)}"
            )

        for entry in schema["loader"]:
            _merge_entry(merged.loader, entry, "env", label, "loader variable")
        for entry in schema["keys"]:
            _merge_entry(merged.keys, entry, "path", label, "key")
        for entry in contract["external"]["env"]:
            _merge_entry(merged.external_env, entry, "name", label, "external variable")

        ignore.update(contract["external"]["ignore"])
        if UNKNOWN_POLICIES.index(contract["external"]["unknown"]) > UNKNOWN_POLICIES.index(
            merged.unknown
        ):
            merged.unknown = contract["external"]["unknown"]

        merged.json_schema = _merge_schema(
            merged.json_schema, contract["json_schema"], label, first_label, "$"
        )

    merged.ignore = sorted(ignore)
    return merged


def _merge_entry(
    into: dict[str, dict[str, Any]],
    entry: dict[str, Any],
    key_field: str,
    label: str,
    kind: str,
) -> None:
    """Merge one key / loader / external entry, keyed by name, into the accumulator.

    `required` unions — a key any reader requires must be present. Every other field must agree,
    including `docs`, `default` and `note`. That is stricter than it looks and it is deliberate:
    two binaries that document one key differently have either drifted or are describing two
    different things, and neither is something a merged document should paper over by picking
    whichever contract happened to be listed first.
    """
    name = entry.get(key_field)
    if not isinstance(name, str):
        raise ContractError(f"{label}: a {kind} has no `{key_field}`")

    existing = into.get(name)
    if existing is None:
        merged = dict(entry)
        merged["_sources"] = [label]
        into[name] = merged
        return

    fields = (set(existing) | set(entry)) - UNIONED_FIELDS - INTERNAL_FIELDS
    for field_name in sorted(fields):
        mine, theirs = existing.get(field_name), entry.get(field_name)
        if mine != theirs:
            raise ContractError(
                f"{kind} {name!r} is described differently by "
                f"{' and '.join(existing['_sources'])} and {label}: `{field_name}` is "
                f"{json.dumps(mine)} in one and {json.dumps(theirs)} in the other"
            )

    existing["required"] = bool(existing.get("required")) or bool(entry.get("required"))
    existing["_sources"].append(label)


def _merge_schema(
    into: dict[str, Any] | None,
    other: dict[str, Any],
    label: str,
    first_label: str,
    where: str,
) -> dict[str, Any]:
    """Structurally merge two JSON Schema subtrees, closing every object as it goes.

    The first contract merged is merged into nothing, and takes the same path as every one after
    it rather than being copied wholesale: a document read by a single image must come out just
    as closed as one read by eight, or a producer that left a level open would leave the gate
    open there too.
    """
    merged = dict(into or {})
    for keyword, value in other.items():
        if keyword == "properties":
            properties = dict(merged.get("properties") or {})
            for name, subschema in value.items():
                properties[name] = _merge_schema(
                    properties.get(name), subschema, label, first_label, f"{where}.{name}"
                )
            merged["properties"] = properties
        elif keyword == "required":
            required = list(merged.get("required") or [])
            for name in value:
                if name not in required:
                    required.append(name)
            merged["required"] = sorted(required)
        elif keyword in ("definitions", "$defs"):
            defs = dict(merged.get(keyword) or {})
            for name, subschema in value.items():
                defs[name] = _merge_schema(
                    defs.get(name), subschema, label, first_label, f"{where}#{name}"
                )
            merged[keyword] = defs
        elif keyword == "additionalProperties":
            # Forced to false below regardless, so an explicit `true` from either side is not a
            # disagreement worth failing on — it is simply overruled by the union.
            continue
        elif keyword in merged and merged[keyword] != value:
            raise ContractError(
                f"{first_label} and {label} disagree about {where}: `{keyword}` is "
                f"{json.dumps(merged[keyword])} in one and {json.dumps(value)} in the other"
            )
        else:
            merged[keyword] = value

    return _close(merged)


def _close(schema: dict[str, Any]) -> dict[str, Any]:
    """After the union an unknown property is unknown to *every* reader, so it is refused."""
    if "properties" in schema:
        schema["additionalProperties"] = False
    return schema


def strip_internals(value: Any) -> Any:
    """Drop the `_sources` bookkeeping this module threads through merged entries."""
    if isinstance(value, dict):
        return {k: strip_internals(v) for k, v in value.items() if k != "_sources"}
    if isinstance(value, list):
        return [strip_internals(item) for item in value]
    return value


def local_refs_only(schema: Any, where: str = "$") -> list[str]:
    """Report every `$ref` that would take the validator off this machine.

    Chart `values.schema.json` files legitimately state their Kubernetes types by URL, which is
    why `just render-chart` carries a retry. The *app config* schema must not: an offline gate
    that resolves a remote reference silently becomes a networked one, and a third party gains a
    say in what CI accepts.
    """
    offenders: list[str] = []
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            offenders.append(f"{where}: $ref {ref!r} is not a local reference")
        for name, value in schema.items():
            offenders.extend(local_refs_only(value, f"{where}.{name}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            offenders.extend(local_refs_only(value, f"{where}[{index}]"))
    return offenders


# --------------------------------------------------------------------------------------------
# Classifying one environment variable
# --------------------------------------------------------------------------------------------

# What `classify` decided, named after the step that decided it.
LOADER = "loader"  # 1. a variable the loader reads to decide what the layers are
KEY_ENV = "key_env"  # 2. a key, supplied by the environment layer
KEY_ENV_FILE = "key_env_file"  # 3. a key, by `_FILE` indirection
PREFIXED = "prefixed"  # 4. inside the namespace and spelling nothing — reject
EXTERNAL = "external"  # 5. declared as read by this image but owned elsewhere
IGNORED = "ignored"  # 6. matched an ignore pattern
UNKNOWN = "unknown"  # 7. everything else, per `external.unknown`


@dataclass(frozen=True)
class Classification:
    kind: str
    entry: dict[str, Any] | None = None


def classify(union: Union, name: str) -> Classification:
    """Decide what one environment variable is. First match wins, and the order is normative.

    Copied from `External`'s documentation in the crate. Two consumers running these steps in
    different orders disagree about whether a deployment is valid, which is the whole reason the
    list is written down — and step 4 sitting above steps 5 and 6 is what stops either external
    list from exempting a variable inside the loader's own namespace.
    """
    if name in union.loader:
        return Classification(LOADER, union.loader[name])

    key = union.key_by("env", name)
    if key is not None:
        return Classification(KEY_ENV, key)

    key = union.key_by("env_file", name)
    if key is not None:
        return Classification(KEY_ENV_FILE, key)

    if name.startswith(union.prefix):
        return Classification(PREFIXED)

    if name in union.external_env:
        return Classification(EXTERNAL, union.external_env[name])

    for pattern in union.ignore:
        if matches_ignore(pattern, name):
            return Classification(IGNORED)

    return Classification(UNKNOWN)


def matches_ignore(pattern: str, name: str) -> bool:
    """The whole ignore pattern language: a trailing `*` matches any suffix, otherwise exact.

    Deliberately not fnmatch and deliberately not a regex. The producer refuses any pattern that
    reaches into the loader's namespace, and it can only make that judgement about a language
    small enough to reason about — `ignore("PORT*")` carries no prefix, reads as a pattern about
    the external `PORT`, and subsumes `PORTFOLIO_` entirely.
    """
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


# --------------------------------------------------------------------------------------------
# Checking one value
# --------------------------------------------------------------------------------------------


def text_form(entry: dict[str, Any]) -> str:
    """The `text_form` of one key or external variable, refusing one this consumer cannot read.

    `TextForm` is `#[non_exhaustive]`, so a form this repository has never seen is a real
    possibility — and it is a hard error rather than a silent downgrade to "skip both steps".
    Under-checking a value while reporting success is the failure this pipeline exists to remove,
    and `terrace_contract` is the lever a producer has for saying the envelope changed.
    """
    form = entry.get("text_form")
    if form not in TEXT_FORMS:
        name = entry.get("path") or entry.get("name")
        raise ContractError(
            f"{name!r} has text_form {form!r}, which this repository does not implement "
            f"(known: {', '.join(sorted(TEXT_FORMS))})"
        )
    return form


def check_text(entry: dict[str, Any], text: str) -> str | None:
    """Step 1, the form: the raw characters, against `text_constraint` and against the grammar.

    `text_constraint: null` means only that there is no pattern to match — `text_form` is what
    says whether that is because any text is correct or because nothing is known.
    """
    form = text_form(entry)
    if form == "unknown":
        return None

    constraint = entry.get("text_constraint")
    if constraint is not None:
        failure = assert_value(constraint, text)
        if failure is not None:
            return failure

    return _grammar(form, entry, text)


def check_parsed(entry: dict[str, Any], text: str) -> str | None:
    """Step 2, the range: the text read as `text_form` says, against `constraint`.

    The only step a `minimum` or `maximum` is reachable from. Assumes `check_text` already ran
    and passed, so text that does not parse is somebody else's report rather than a second line
    about the same value.
    """
    form = text_form(entry)
    if form in NO_RANGE_FORMS:
        return None

    constraint = entry.get("constraint")
    if constraint is None:
        return None

    value, parsed = parse_env_text(form, text)
    if not parsed:
        return None
    return assert_value(constraint, value)


def _grammar(form: str, entry: dict[str, Any], text: str) -> str | None:
    """Whether the text is a well-formed value of this form, before anything is read from it."""
    if form == "text":
        return None

    if form == "choice":
        values = entry.get("values") or []
        if values and text not in values:
            allowed = ", ".join(json.dumps(value) for value in values)
            return f"{json.dumps(text)} is not one of {allowed}"
        return None

    if form == "structured":
        if not STRUCTURED_TEXT.match(text):
            return (
                f"{json.dumps(text)} is not a TOML array or inline table: a `structured` value "
                "carries its brackets, so a list is [\"a\", \"b\"] and never a,b"
            )
        return None

    _, parsed = parse_env_text(form, text)
    if parsed:
        return None
    if form == "integer":
        return f"{json.dumps(text)} is not an integer"
    if form == "boolean":
        return (
            f"{json.dumps(text)} is not a boolean: the loader accepts `true` and `false`, and "
            "neither `TRUE` nor `1` nor `yes`"
        )
    return None


def parse_env_text(form: str, text: str) -> tuple[Any, bool]:
    """Read an environment value as `form` names, returning `(value, parsed)`.

    Only the forms with a second step parse to anything. `text` and `structured` have no range to
    check — the first because a string is already its own value, the second because a TOML
    literal's contents are beyond what a flat constraint can describe — and `unknown` never
    reaches here.
    """
    if form == "integer":
        if not INTEGER_TEXT.match(text):
            return None, False
        return int(text), True
    if form == "boolean":
        if text not in BOOLEAN_TEXT:
            return None, False
        return BOOLEAN_TEXT[text], True
    if form == "choice":
        return text, True
    return text, False


def file_supplyable(entry: dict[str, Any]) -> bool:
    """Whether a key can be supplied by a file at all.

    The secrets directory and `_FILE` targets deliver their contents as strings with no parse,
    and figment does not coerce one into a number, a boolean or a TOML literal. So only a `text`
    key can be file-supplied, whatever the file contains — a chart mounting `isr__ttl_secs` as a
    secret file has made a mistake no file contents can fix.

    `unknown` is refused too, and deliberately: nothing is known about how the loader will read
    it, so nothing says a raw string will do. That is the direction to be wrong in — a false
    report on a mount is one line in review, and a missing one is a credential silently unread.
    """
    return text_form(entry) == "text"


def assert_value(constraint: dict[str, Any], value: Any) -> str | None:
    """Validate one scalar against the flat JSON Schema subset a contract may use."""
    unsupported = set(constraint) - ASSERTIONS - ANNOTATIONS
    if unsupported:
        raise ContractError(
            "constraint uses keywords this validator does not implement: "
            f"{', '.join(sorted(unsupported))}"
        )

    if "type" in constraint and not _is_type(value, constraint["type"]):
        return f"expected {constraint['type']}, got {json.dumps(value)}"
    if "enum" in constraint and value not in constraint["enum"]:
        allowed = ", ".join(json.dumps(option) for option in constraint["enum"])
        return f"{json.dumps(value)} is not one of {allowed}"
    if "const" in constraint and value != constraint["const"]:
        return f"{json.dumps(value)} is not {json.dumps(constraint['const'])}"

    # `bool` is a subclass of `int` in Python, and a bound on a boolean is meaningless anyway.
    numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if numeric is not None:
        if "minimum" in constraint and numeric < constraint["minimum"]:
            return f"{json.dumps(value)} is below the minimum {constraint['minimum']}"
        if "maximum" in constraint and numeric > constraint["maximum"]:
            return f"{json.dumps(value)} is above the maximum {constraint['maximum']}"
        if "exclusiveMinimum" in constraint and numeric <= constraint["exclusiveMinimum"]:
            return f"{json.dumps(value)} is not above {constraint['exclusiveMinimum']}"
        if "exclusiveMaximum" in constraint and numeric >= constraint["exclusiveMaximum"]:
            return f"{json.dumps(value)} is not below {constraint['exclusiveMaximum']}"
        if "multipleOf" in constraint and numeric % constraint["multipleOf"] != 0:
            return f"{json.dumps(value)} is not a multiple of {constraint['multipleOf']}"

    if isinstance(value, str):
        if "pattern" in constraint and not re.search(constraint["pattern"], value):
            # The pattern is printed as written rather than repr'd: it is full of backslashes,
            # and doubling every one of them makes the one line a reader has to compare their
            # value against harder to read than the value itself.
            return f"{json.dumps(value)} does not match {constraint['pattern']}"
        if "minLength" in constraint and len(value) < constraint["minLength"]:
            return f"{json.dumps(value)} is shorter than {constraint['minLength']} characters"
        if "maxLength" in constraint and len(value) > constraint["maxLength"]:
            return f"{json.dumps(value)} is longer than {constraint['maxLength']} characters"

    return None


def _is_type(value: Any, declared: Any) -> bool:
    names = declared if isinstance(declared, list) else [declared]
    for name in names:
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "string" and isinstance(value, str):
            return True
        if name == "array" and isinstance(value, list):
            return True
        if name == "object" and isinstance(value, dict):
            return True
        if name == "null" and value is None:
            return True
    return False


# --------------------------------------------------------------------------------------------
# Reporting help
# --------------------------------------------------------------------------------------------


def suggest(name: str, candidates: Iterable[str], limit: int = 3) -> str:
    """`(did you mean ...?)`, or the empty string when nothing is close enough.

    The union already holds every key path and every variable spelling, so this costs nothing
    and turns a rename from a puzzle into a one-line answer — which is the whole point on the
    pull request that bumps a digest.
    """
    scored = []
    for candidate in candidates:
        if candidate == name:
            continue
        distance = _levenshtein(name.lower(), candidate.lower())
        if distance <= max(2, min(len(name), len(candidate)) // 3):
            scored.append((distance, candidate))
    if not scored:
        return ""
    scored.sort()
    return f" (did you mean {' or '.join(name for _, name in scored[:limit])}?)"


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for index, a in enumerate(left, start=1):
        current = [index]
        for position, b in enumerate(right, start=1):
            current.append(
                min(previous[position] + 1, current[position - 1] + 1, previous[position - 1] + (a != b))
            )
        previous = current
    return previous[-1]
