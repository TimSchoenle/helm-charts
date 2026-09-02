#!/usr/bin/env python3
"""What changed between two versions of a vendored contract, and what it costs the chart.

Renovate opens a pull request that repins a digest, the Documentation job runs `just contracts`
into the same branch, and the reviewer is handed several hundred lines of reordered JSON. The
question they actually have — did the application gain a setting the chart has to write, drop one
the chart still writes, or move a default out from under it — is answerable from those bytes and
nobody answers it. This module turns the two documents into that answer, and into a defensible
suggestion about the chart's own version number.

Pure and offline: it takes two parsed documents and returns findings. Reading the old bytes out of
git, walking the charts and printing anything belongs to `contract-diff.py`, which is what lets
every rule in here be tested by calling it.

Four decisions in here are deliberate rather than incidental.

**This does not run `check_envelope`.** Every other consumer of a vendored contract refuses a
document whose `terrace_contract` it does not recognise, which is right for a gate: misreading a
document is worse than not reading it. It is wrong here, because "the envelope version changed"
is one of the findings this tool exists to report, and a tool that crashes on the one document it
most needs to describe is useless on the day it matters. So both sides are read defensively,
field by field, and an envelope this repository could not validate is reported as the major
change it is.

**The severity table is a suggestion with its reasons attached, never an edit.** `SUGGESTION` maps
a finding to the smallest chart version bump that finding justifies, and the chart's impact is the
largest of them. Nothing here writes `Chart.yaml`: the mapping is a defensible default and the
reviewer is the one who knows whether the chart writes the key that moved. A tool that edited the
version would be asserting it knows that, and would be wrong the first time a removed key was one
no template ever emitted.

**Removing something is graded by what the loader does next, not by the shape of the diff.**
`external.unknown` is `reject` across every contract in this repository, so an unrecognised
variable is a boot failure rather than a no-op. A removed *key* falls to step 4 of
`config_contract.classify` — inside the prefix and spelling nothing — which sits above both
external lists, so nothing can absorb it and it is always major. A removed *external* entry falls
past step 4 to the ignore patterns, so it is major only when no surviving pattern catches it; that
downgrade is decided by calling `matches_ignore` rather than by a second opinion about the
language.

**Rename detection is deliberately absent.** A renamed key would arrive here as one removal and
one addition — a major finding and a minor one, for a change that is neither. The producer's
`aliases`, `env_aliases`, `env_file_aliases` and `secrets_file_aliases` are what would collapse
the pair, and they are empty in all 491 key declarations this repository vendors today, so any
rename detector written now would be untestable against a real document. The model is shaped so
that it becomes a small addition rather than a rewrite: `Change.kind` is a string with `RENAMED`
reserved beside `ADDED` and `REMOVED`, and the alias fields are already compared, so the whole
addition is one pass over the added keys looking for a removed key's path among their aliases,
collapsing the pair into one `RENAMED` change. Until a producer populates the field there is
nothing to collapse and the pass would only ever be dead code.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

import config_contract as cc

# --------------------------------------------------------------------------------------------
# Severity
# --------------------------------------------------------------------------------------------

# The three semver impacts a finding can justify, plus the absence of any change. Ordered, because
# a chart's impact is the largest of its findings and a contract's is the largest of its changes.
MAJOR = "major"
MINOR = "minor"
PATCH = "patch"
NONE = "none"

RANK = {NONE: 0, PATCH: 1, MINOR: 2, MAJOR: 3}


def worst(severities: Iterable[str]) -> str:
    """The largest impact in a set of findings, or `none` for an empty one."""
    return max(severities, key=lambda level: RANK[level], default=NONE)


# What each area of the document is, for a reader scanning one column of output.
ENVELOPE = "envelope"
LOADER = "loader"
KEY = "key"
EXTERNAL = "external"

# What happened to a subject. `RENAMED` is reserved rather than emitted — see the module
# docstring for why nothing can produce it yet and what would.
ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"
RENAMED = "renamed"

# What happened to a whole contract file between the two revisions.
STATUS_ADDED = "added"
STATUS_REMOVED = "removed"
STATUS_CHANGED = "changed"
STATUS_UNCHANGED = "unchanged"


@dataclass(frozen=True)
class Change:
    """One difference, and the smallest chart version bump it justifies.

    `area`, `kind`, `subject` and `field` are the machine-readable half — enough for a pull
    request comment to group findings without parsing English — and `message` is the sentence a
    reviewer reads. `old` and `new` are the raw JSON values, so a consumer that wants to render a
    before/after does not have to recover them from the prose.
    """

    severity: str
    area: str
    kind: str
    subject: str
    field: str | None
    old: Any
    new: Any
    message: str

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContractDiff:
    """One `charts/<chart>/contracts/<name>.json`, across the two revisions."""

    chart: str
    name: str
    path: str
    status: str
    changes: list[Change] = field(default_factory=list)
    old_image: str | None = None
    new_image: str | None = None

    @property
    def impact(self) -> str:
        return worst(change.severity for change in self.changes)

    def as_json(self) -> dict[str, Any]:
        return {
            "chart": self.chart,
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "impact": self.impact,
            "image": {"old": self.old_image, "new": self.new_image},
            "changes": [change.as_json() for change in self.changes],
        }


@dataclass
class ChartDiff:
    """Every contract a chart carries, and what the set of them implies for its version.

    `old_version` and `new_version` are the chart's own `Chart.yaml` version at the comparison
    revision and in the working tree. Reporting both is what turns the suggestion into a review
    finding: the reviewer's real question is not "what bump does this deserve" but "is the bump
    already in this branch big enough", and only the pair answers that.
    """

    chart: str
    contracts: list[ContractDiff] = field(default_factory=list)
    old_version: str | None = None
    new_version: str | None = None

    @property
    def impact(self) -> str:
        return worst(contract.impact for contract in self.contracts)

    @property
    def drivers(self) -> list[Change]:
        """The findings that set the impact — the "because" the suggestion is shown with."""
        level = self.impact
        if level == NONE:
            return []
        return [
            change
            for contract in self.contracts
            for change in contract.changes
            if change.severity == level
        ]

    def as_json(self) -> dict[str, Any]:
        return {
            "chart": self.chart,
            "impact": self.impact,
            # References rather than copies: every driver is already in `contracts`, and a
            # driver is an added or removed declaration whose full entry is the largest object in
            # the document. A headline paragraph needs the sentence and the name, not the entry.
            "drivers": [
                {
                    "area": change.area,
                    "kind": change.kind,
                    "subject": change.subject,
                    "field": change.field,
                    "message": change.message,
                }
                for change in self.drivers
            ],
            "version": {
                "old": self.old_version,
                "new": self.new_version,
                "bumped": self.old_version != self.new_version,
                "suggested": suggest_version(self.new_version, self.impact),
                "satisfied": self.satisfied,
            },
            "contracts": [contract.as_json() for contract in self.contracts],
        }

    @property
    def satisfied(self) -> bool | None:
        """Whether the bump already in the branch is at least as large as the suggested one.

        `None` when it cannot be decided — an unparseable version, or a chart whose `Chart.yaml`
        did not exist at the comparison revision.
        """
        if self.impact == NONE:
            return True
        observed = observed_bump(self.old_version, self.new_version)
        if observed is None:
            return None
        return RANK[observed] >= RANK[self.impact]


# --------------------------------------------------------------------------------------------
# The chart's own version
# --------------------------------------------------------------------------------------------


def parse_version(version: str | None) -> tuple[int, int, int] | None:
    """The `major.minor.patch` of a chart version, or `None` if it is not one.

    Deliberately not a full semver parser: chart versions in this repository are three integers,
    and a pre-release or build suffix is a case that has never occurred here. Returning `None`
    rather than guessing keeps the suggestion honest — a version this cannot read produces no
    suggestion instead of a wrong one.
    """
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if len(parts) != 3:
        return None
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        return None
    if major < 0 or minor < 0 or patch < 0:
        return None
    return major, minor, patch


def suggest_version(current: str | None, impact: str) -> str | None:
    """The version the suggested impact would produce from the working tree's current one."""
    parsed = parse_version(current)
    if parsed is None or impact == NONE:
        return None
    major, minor, patch = parsed
    if impact == MAJOR:
        return f"{major + 1}.0.0"
    if impact == MINOR:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def observed_bump(old: str | None, new: str | None) -> str | None:
    """Which component of the chart version already moved between the two revisions."""
    before, after = parse_version(old), parse_version(new)
    if before is None or after is None:
        return None
    if after[0] != before[0]:
        return MAJOR
    if after[1] != before[1]:
        return MINOR
    if after[2] != before[2]:
        return PATCH
    return NONE


# --------------------------------------------------------------------------------------------
# Comparing one contract
# --------------------------------------------------------------------------------------------

# Per-field severity for a key that exists on both sides. Three groups, and the reasoning for each
# is the same reasoning the gates in `check-config.py` apply at deployment time:
#
#   major   the chart's own spelling of the setting stopped being the one the image reads. A
#           `env` / `env_file` / `secrets_file` change is derived from the path and the dialect,
#           so it should be unreachable without one of those moving — and if it happens anyway it
#           is exactly as severe as a dialect change, which is the most severe thing here.
#   minor   the value the chart writes is still spelled right but may no longer be accepted, or a
#           default moved out from under a chart that relied on it.
#   patch   prose. Real, and not a reason to bump anything.
#
# `required` and `text_form` are absent because neither is a fixed severity; both are graded by
# direction below.
KEY_FIELDS = {
    "env": MAJOR,
    "env_file": MAJOR,
    "secrets_file": MAJOR,
    "ty": MINOR,
    "values": MINOR,
    "constraint": MINOR,
    "text_constraint": MINOR,
    "secret": MINOR,
    "reserved": MINOR,
    "aliases": MINOR,
    "env_aliases": MINOR,
    "env_file_aliases": MINOR,
    "secrets_file_aliases": MINOR,
    "docs": PATCH,
    "note": PATCH,
}

# An external variable carries a smaller entry, and its spellings are its own name rather than
# something derived — so there is no equivalent of the major group here.
EXTERNAL_FIELDS = {
    "owner": PATCH,
    "ty": MINOR,
    "values": MINOR,
    "constraint": MINOR,
    "text_constraint": MINOR,
    "secret": MINOR,
    "docs": PATCH,
}

# Compared as a pair rather than as two fields: `default` is the text the loader would use and
# `default_value` is that text parsed, so they move together and reporting both is one fact
# printed twice.
DEFAULT_FIELDS = ("default", "default_value")

# Fields a key entry carries that are compared by name above, plus the ones handled specially.
# Anything outside the union is a field the producer added under an envelope version this
# repository still claims to read, and it is reported rather than dropped: a diff that silently
# ignores what it does not recognise is the failure mode this whole pipeline exists to remove.
KEY_KNOWN = set(KEY_FIELDS) | set(DEFAULT_FIELDS) | {"path", "required", "text_form"}
EXTERNAL_KNOWN = set(EXTERNAL_FIELDS) | {"name", "default", "required", "text_form"}


def _version_severity(old: Any, new: Any) -> str:
    """How severe a `schema.schema_version` move is, graded by direction rather than by shape.

    A rise to a version this repository implements is additive by the producer's own statement:
    nothing was removed and nothing changed meaning between 1 and 2, so every key a chart already
    writes is spelt and checked exactly as before and what arrives is a constraint that says more.
    Minor, and worth a line in the report — a container key that gains an element schema is the
    signal to move its `@config-shape` marker from `handwritten` to `generated`.

    Everything else is major. A version *above* what `config_contract` implements is one whose
    keywords this repository would walk past, and going backwards means the producer withdrew
    something. Neither is a difference a reviewer should be able to skim.
    """
    if isinstance(old, int) and isinstance(new, int) and old < new <= cc.SCHEMA_VERSION:
        return MINOR
    return MAJOR


def diff_contract(
    chart: str,
    name: str,
    path: str,
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> ContractDiff:
    """Compare two vendored documents — the `{source, contract}` envelope, not the contract alone.

    Either side may be `None`: a contract that did not exist at the comparison revision, or one
    the working tree no longer carries. Both are ordinary outcomes of a refresh rather than
    errors, and both are graded — gaining coverage is minor, losing it is major.
    """
    if old is None and new is None:
        raise ValueError(f"{path}: compared two absent documents")

    if old is None:
        diff = ContractDiff(chart, name, path, STATUS_ADDED, new_image=_source(new, "image"))
        diff.changes.append(
            Change(
                MINOR,
                ENVELOPE,
                ADDED,
                name,
                None,
                None,
                _source(new, "image"),
                f"contract is new: {_source(new, 'image')} publishes a document this chart did "
                f"not vendor before, so its settings are gated from now on",
            )
        )
        return diff

    if new is None:
        diff = ContractDiff(chart, name, path, STATUS_REMOVED, old_image=_source(old, "image"))
        diff.changes.append(
            Change(
                MAJOR,
                ENVELOPE,
                REMOVED,
                name,
                None,
                _source(old, "image"),
                None,
                "contract is gone: nothing validates the settings this document covered, and a "
                "chart that still writes them is no longer checked against anything",
            )
        )
        return diff

    changes: list[Change] = []
    changes += _diff_source(old, new)
    changes += _diff_envelope(_contract(old), _contract(new))
    changes += _diff_loader(_contract(old), _contract(new))
    key_changes = _diff_keys(_contract(old), _contract(new))
    changes += key_changes
    changes += _diff_external(_contract(old), _contract(new))

    # The published JSON Schema is derived from the keys, so it moves whenever they do and saying
    # so again would be noise. It is worth a line in exactly one case: it moved and the keys did
    # not, which means the document changed in a way nothing above models.
    if not key_changes and _contract(old).get("json_schema") != _contract(new).get("json_schema"):
        changes.append(
            Change(
                PATCH,
                ENVELOPE,
                CHANGED,
                "json_schema",
                None,
                None,
                None,
                "the published JSON Schema changed while no key declaration did; this diff does "
                "not model whatever moved, so read the raw document",
            )
        )

    status = STATUS_CHANGED if changes else STATUS_UNCHANGED
    return ContractDiff(
        chart,
        name,
        path,
        status,
        changes,
        old_image=_source(old, "image"),
        new_image=_source(new, "image"),
    )


def _source(document: dict[str, Any] | None, name: str) -> Any:
    source = (document or {}).get("source")
    return source.get(name) if isinstance(source, dict) else None


def _contract(document: dict[str, Any]) -> dict[str, Any]:
    contract = document.get("contract")
    return contract if isinstance(contract, dict) else {}


def _diff_source(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """The provenance envelope the chart repository writes, not the published document.

    `fetched` is not compared: `refresh-contracts.py` refuses to rewrite a file whose only
    difference is that timestamp, so a `fetched` that moved is always accompanied by something
    that matters, and reporting it would manufacture a finding out of a field the producer already
    treats as noise. `sha256` is not compared either — it is over the published bytes, so it is
    true exactly when something below is, and it names nothing.
    """
    changes: list[Change] = []
    if _source(old, "image") != _source(new, "image"):
        changes.append(
            Change(
                MINOR,
                ENVELOPE,
                CHANGED,
                "source.image",
                "image",
                _source(old, "image"),
                _source(new, "image"),
                f"the chart pins a different image: {_source(old, 'image')} -> "
                f"{_source(new, 'image')}",
            )
        )
    if _source(old, "digest") != _source(new, "digest"):
        changes.append(
            Change(
                PATCH,
                ENVELOPE,
                CHANGED,
                "source.digest",
                "digest",
                _source(old, "digest"),
                _source(new, "digest"),
                f"image digest {_short(_source(old, 'digest'))} -> "
                f"{_short(_source(new, 'digest'))}",
            )
        )
    return changes


def _diff_envelope(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """The published document's own identity: its version, the application, and the dialect."""
    changes: list[Change] = []

    if old.get("terrace_contract") != new.get("terrace_contract"):
        changes.append(
            Change(
                MAJOR,
                ENVELOPE,
                CHANGED,
                "terrace_contract",
                None,
                old.get("terrace_contract"),
                new.get("terrace_contract"),
                f"envelope version {old.get('terrace_contract')!r} -> "
                f"{new.get('terrace_contract')!r}; this repository reads "
                f"{cc.ENVELOPE_VERSION}, so every gate reading this document is affected",
            )
        )

    old_app = old.get("app") if isinstance(old.get("app"), dict) else {}
    new_app = new.get("app") if isinstance(new.get("app"), dict) else {}
    for name, severity in (("name", MINOR), ("version", PATCH), ("source", PATCH)):
        if old_app.get(name) != new_app.get(name):
            changes.append(
                Change(
                    severity,
                    ENVELOPE,
                    CHANGED,
                    f"app.{name}",
                    name,
                    old_app.get(name),
                    new_app.get(name),
                    f"app.{name} {old_app.get(name)!r} -> {new_app.get(name)!r}",
                )
            )

    old_schema = old.get("schema") if isinstance(old.get("schema"), dict) else {}
    new_schema = new.get("schema") if isinstance(new.get("schema"), dict) else {}
    old_version = old_schema.get("schema_version")
    new_version = new_schema.get("schema_version")
    if old_version != new_version:
        changes.append(
            Change(
                _version_severity(old_version, new_version),
                ENVELOPE,
                CHANGED,
                "schema.schema_version",
                None,
                old_version,
                new_version,
                f"schema version {old_version!r} -> {new_version!r}"
                + (
                    ", a widening this repository reads"
                    if _version_severity(old_version, new_version) is MINOR
                    else ""
                ),
            )
        )

    # The single most severe thing this tool can find. The prefix, the nesting separator and the
    # indirection suffix are what every environment variable name and every secret file name the
    # chart writes is built from, so one character moving here renames all of them at once — and
    # every one of the old names lands on step 4 of `classify`, which `external.unknown: reject`
    # turns into a pod that does not start. Nothing in the rendered manifests looks different.
    old_dialect = old_schema.get("dialect") if isinstance(old_schema.get("dialect"), dict) else {}
    new_dialect = new_schema.get("dialect") if isinstance(new_schema.get("dialect"), dict) else {}
    for name in ("prefix", "nesting_separator", "indirection_suffix"):
        if old_dialect.get(name) != new_dialect.get(name):
            changes.append(
                Change(
                    MAJOR,
                    ENVELOPE,
                    CHANGED,
                    f"dialect.{name}",
                    name,
                    old_dialect.get(name),
                    new_dialect.get(name),
                    f"dialect {name} {old_dialect.get(name)!r} -> {new_dialect.get(name)!r}; "
                    f"every environment and secret-file spelling this chart writes is derived "
                    f"from it, so all of them change at once and none of them renders differently",
                )
            )

    old_external = old.get("external") if isinstance(old.get("external"), dict) else {}
    new_external = new.get("external") if isinstance(new.get("external"), dict) else {}
    if old_external.get("unknown") != new_external.get("unknown"):
        # Tightening is what breaks a running deployment: a variable the image used to tolerate
        # now stops it from booting. Relaxing can only turn a rejection into acceptance.
        tightened = new_external.get("unknown") == "reject"
        changes.append(
            Change(
                MAJOR if tightened else MINOR,
                ENVELOPE,
                CHANGED,
                "external.unknown",
                None,
                old_external.get("unknown"),
                new_external.get("unknown"),
                f"unknown-variable policy {old_external.get('unknown')!r} -> "
                f"{new_external.get('unknown')!r}",
            )
        )

    for kind, name in _list_delta(old_external.get("ignore"), new_external.get("ignore")):
        changes.append(
            Change(
                MINOR,
                ENVELOPE,
                kind,
                f"ignore {name}",
                "ignore",
                name if kind == REMOVED else None,
                name if kind == ADDED else None,
                f"ignore pattern {name!r} {kind}",
            )
        )

    return changes


def _diff_loader(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """The variables that tell the loader where its layers are.

    These are the variables the chart sets to point the image at its ConfigMap and its mounted
    Secret. A renamed or removed one is not a validation failure anywhere — the chart keeps
    setting the old name, the pod keeps starting, and the application silently loads none of the
    configuration the chart mounted. That is why a removal here is graded with a dialect change
    rather than with a key change.
    """
    old_entries = _by(old.get("schema"), "loader", "env")
    new_entries = _by(new.get("schema"), "loader", "env")
    changes: list[Change] = []

    for name in sorted(set(new_entries) - set(old_entries)):
        entry = new_entries[name]
        changes.append(
            Change(
                MINOR,
                LOADER,
                ADDED,
                name,
                None,
                None,
                entry,
                f"loader variable {name} added (role {entry.get('role')!r})",
            )
        )
    for name in sorted(set(old_entries) - set(new_entries)):
        entry = old_entries[name]
        changes.append(
            Change(
                MAJOR,
                LOADER,
                REMOVED,
                name,
                None,
                entry,
                None,
                f"loader variable {name} (role {entry.get('role')!r}) is gone; a chart still "
                f"setting it points the image at a layer it no longer reads, and nothing fails",
            )
        )
    for name in sorted(set(old_entries) & set(new_entries)):
        before, after = old_entries[name], new_entries[name]
        for field_name, severity in (("role", MAJOR), ("default", MINOR), ("docs", PATCH)):
            if before.get(field_name) != after.get(field_name):
                changes.append(
                    Change(
                        severity,
                        LOADER,
                        CHANGED,
                        name,
                        field_name,
                        before.get(field_name),
                        after.get(field_name),
                        f"loader variable {name}: {field_name} {before.get(field_name)!r} -> "
                        f"{after.get(field_name)!r}",
                    )
                )
    return changes


def _diff_keys(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """The key declarations — the half of the document a chart's rendered settings answer to."""
    old_keys = _by(old.get("schema"), "keys", "path")
    new_keys = _by(new.get("schema"), "keys", "path")
    changes: list[Change] = []

    for path in sorted(set(new_keys) - set(old_keys)):
        entry = new_keys[path]
        # A new *required* key is the same deployment failure as one that became required: the
        # chart does not write it, and the image refuses to start without it. The stated rule
        # calls an added key minor, which is right for the optional case and wrong for this one.
        required = bool(entry.get("required"))
        changes.append(
            Change(
                MAJOR if required else MINOR,
                KEY,
                ADDED,
                path,
                None,
                None,
                entry,
                f"{path} added ({_shape(entry)}); the chart writes nothing for it"
                + (
                    ", and it is required, so the image will not start until the chart does"
                    if required
                    else ""
                ),
            )
        )

    for path in sorted(set(old_keys) - set(new_keys)):
        entry = old_keys[path]
        # No downgrade is possible. The old spelling still begins with the prefix, so `classify`
        # stops at step 4 — above both external lists — and `external.unknown: reject` turns it
        # into a container that does not start.
        changes.append(
            Change(
                MAJOR,
                KEY,
                REMOVED,
                path,
                None,
                entry,
                None,
                f"{path} is gone; a chart still emitting {entry.get('env')} or mounting "
                f"{entry.get('secrets_file')} is refused at boot, not ignored",
            )
        )

    for path in sorted(set(old_keys) & set(new_keys)):
        changes += _diff_entry(KEY, path, old_keys[path], new_keys[path], KEY_FIELDS, KEY_KNOWN)

    return changes


def _diff_external(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """The variables the image reads that are nobody's configuration — `PORT`, `RUST_LOG`.

    A removal is graded by what `classify` does with the name afterwards. Having fallen past the
    prefix step, it reaches the ignore patterns, so a surviving pattern that matches absorbs it
    and the removal costs nothing; with no pattern matching, it reaches `external.unknown` and a
    chart still setting it stops booting.
    """
    old_env = _by(old.get("external"), "env", "name")
    new_env = _by(new.get("external"), "env", "name")
    policy = (new.get("external") or {}).get("unknown")
    patterns = [
        pattern
        for pattern in ((new.get("external") or {}).get("ignore") or [])
        if isinstance(pattern, str)
    ]
    changes: list[Change] = []

    for name in sorted(set(new_env) - set(old_env)):
        entry = new_env[name]
        required = bool(entry.get("required"))
        changes.append(
            Change(
                MAJOR if required else MINOR,
                EXTERNAL,
                ADDED,
                name,
                None,
                None,
                entry,
                f"external variable {name} added, owned by {entry.get('owner')!r}"
                + (" and required" if required else ""),
            )
        )

    for name in sorted(set(old_env) - set(new_env)):
        absorbed = any(cc.matches_ignore(pattern, name) for pattern in patterns)
        rejected = policy == "reject" and not absorbed
        changes.append(
            Change(
                MAJOR if rejected else MINOR,
                EXTERNAL,
                REMOVED,
                name,
                None,
                old_env[name],
                None,
                f"external variable {name} is no longer declared; "
                + (
                    f"a chart still setting it is refused at boot (external.unknown is "
                    f"{policy!r})"
                    if rejected
                    else "a surviving ignore pattern still absorbs it"
                    if absorbed
                    else f"the image's unknown-variable policy is {policy!r}"
                ),
            )
        )

    for name in sorted(set(old_env) & set(new_env)):
        changes += _diff_entry(
            EXTERNAL, name, old_env[name], new_env[name], EXTERNAL_FIELDS, EXTERNAL_KNOWN
        )

    return changes


def _diff_entry(
    area: str,
    subject: str,
    old: dict[str, Any],
    new: dict[str, Any],
    fields: dict[str, str],
    known: set[str],
) -> list[Change]:
    """One declaration that exists on both sides, field by field."""
    changes: list[Change] = []

    for name, severity in sorted(fields.items()):
        if old.get(name) != new.get(name):
            changes.append(
                Change(
                    severity,
                    area,
                    CHANGED,
                    subject,
                    name,
                    old.get(name),
                    new.get(name),
                    f"{subject}: {name} {_render(old.get(name))} -> {_render(new.get(name))}",
                )
            )

    changes += _diff_required(area, subject, old, new)
    changes += _diff_text_form(area, subject, old, new)
    changes += _diff_default(area, subject, old, new)

    # A field the producer added that this diff has no opinion about. Reported at patch so it is
    # visible without claiming a severity nobody has reasoned about.
    for name in sorted((set(old) | set(new)) - known):
        if old.get(name) != new.get(name):
            changes.append(
                Change(
                    PATCH,
                    area,
                    CHANGED,
                    subject,
                    name,
                    old.get(name),
                    new.get(name),
                    f"{subject}: {name} changed, and this diff models no severity for that "
                    f"field: {_render(old.get(name))} -> {_render(new.get(name))}",
                )
            )

    return changes


def _diff_required(
    area: str, subject: str, old: dict[str, Any], new: dict[str, Any]
) -> list[Change]:
    """`required` is graded by direction: gaining it breaks a chart, losing it cannot."""
    before, after = bool(old.get("required")), bool(new.get("required"))
    if before == after:
        return []
    if after:
        return [
            Change(
                MAJOR,
                area,
                CHANGED,
                subject,
                "required",
                before,
                after,
                f"{subject} is now required; a chart that does not write it will not start",
            )
        ]
    return [
        Change(
            MINOR,
            area,
            CHANGED,
            subject,
            "required",
            before,
            after,
            f"{subject} is no longer required",
        )
    ]


def _diff_text_form(
    area: str, subject: str, old: dict[str, Any], new: dict[str, Any]
) -> list[Change]:
    """`text_form` decides the parse, and with it whether a file can supply the setting at all.

    The escalation is the point. A file — the secrets directory or `_FILE` indirection — delivers
    its contents as a string with no parse, so only a `text` key can be supplied by one. A key
    that moves from `text` to `integer` is still spelled the same and still renders the same, and
    the Secret the chart mounts for it stops loading. The stated severity table has no rule for
    `text_form` at all; graded flat it would be minor, which is wrong for exactly this case.
    """
    before, after = old.get("text_form"), new.get("text_form")
    if before == after:
        return []

    lost_file = _file_supplyable(old) and not _file_supplyable(new)
    return [
        Change(
            MAJOR if lost_file else MINOR,
            area,
            CHANGED,
            subject,
            "text_form",
            before,
            after,
            f"{subject}: text_form {before!r} -> {after!r}"
            + (
                "; it can no longer be supplied by a mounted file, because a file delivers text "
                "and the loader does not coerce it"
                if lost_file
                else ""
            ),
        )
    ]


def _file_supplyable(entry: dict[str, Any]) -> bool:
    """`config_contract`'s rule, tolerating an entry this repository could not otherwise read.

    The gates are right to refuse an unknown `text_form` outright. Here a document carrying one is
    the thing being described, so the question "could a file supply this" simply has no answer and
    `False` is the safe one: it cannot produce a false major, only miss one.
    """
    try:
        return cc.file_supplyable(entry)
    except cc.ContractError:
        return False


def _diff_default(
    area: str, subject: str, old: dict[str, Any], new: dict[str, Any]
) -> list[Change]:
    """`default` and `default_value` are one fact — the text, and that text parsed.

    Compared as a pair because they move together, and reporting both is one change printed
    twice. A moved default is minor rather than patch: a chart that deliberately writes nothing
    for a key is relying on the value the image chooses, and the value it chooses just changed.
    """
    before = tuple(old.get(name) for name in DEFAULT_FIELDS)
    after = tuple(new.get(name) for name in DEFAULT_FIELDS)
    if before == after:
        return []
    return [
        Change(
            MINOR,
            area,
            CHANGED,
            subject,
            "default",
            {name: old.get(name) for name in DEFAULT_FIELDS},
            {name: new.get(name) for name in DEFAULT_FIELDS},
            f"{subject}: default {_render(before[0])} -> {_render(after[0])}"
            + (
                ""
                if before[0] != after[0]
                else f" (text unchanged; parsed {_render(before[1])} -> {_render(after[1])})"
            ),
        )
    ]


# --------------------------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------------------------


def _by(section: Any, name: str, key: str) -> dict[str, dict[str, Any]]:
    """Index one list of declarations by the field that names it, dropping what is not one.

    Defensive by design: this reads documents the gates would refuse, so a section that is
    missing, is not a list, or holds something other than named objects has to produce an empty
    index rather than an exception. Whatever is wrong with such a document shows up as the
    envelope or dialect finding that explains it.
    """
    entries = (section or {}).get(name) if isinstance(section, dict) else None
    if not isinstance(entries, list):
        return {}
    return {
        entry[key]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get(key), str)
    }


def _list_delta(old: Any, new: Any) -> list[tuple[str, str]]:
    """Added and removed members of a list of strings, order-insensitively."""
    before = {item for item in (old or []) if isinstance(item, str)}
    after = {item for item in (new or []) if isinstance(item, str)}
    return [(ADDED, item) for item in sorted(after - before)] + [
        (REMOVED, item) for item in sorted(before - after)
    ]


def _shape(entry: dict[str, Any]) -> str:
    """A key's one-line shape, for a message that would otherwise say only its name."""
    parts = [str(entry.get("text_form") or "unknown")]
    if entry.get("secret"):
        parts.append("secret")
    if entry.get("default") is not None:
        parts.append(f"default {_render(entry.get('default'))}")
    else:
        parts.append("no default")
    return ", ".join(parts)


def _render(value: Any) -> str:
    """One JSON value on one line, short enough to sit inside a sentence."""
    if value is None:
        return "unset"
    if isinstance(value, str):
        return repr(value) if len(value) <= 60 else repr(value[:57] + "...")
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= 60 else text[:57] + "..."


def _short(digest: Any) -> str:
    if not isinstance(digest, str):
        return "unset"
    return digest[:19] + "..." if len(digest) > 22 else digest
