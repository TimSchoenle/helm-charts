#!/usr/bin/env python3
"""Hold every `# @config` marker in a chart's values.yaml against the contract it names.

`just check-contract-coverage` answers a chart-level question — does this chart carry a
`config-contract.yaml` at all — and its `unconfigured` escape hatch lists whole *image
blocks*. So the
repository's documented recurring failure is invisible to it: an image release adds a setting, the
automated bump repins the digest and omits everything else, and no gate anywhere notices that the
new key has no chart value. `check-config` will not catch it either, because a key nothing renders
is not a key rendered wrongly.

This is the key-level half. It reads the markers described in `config_bindings.py`, resolves each
one against the vendored contract, and fails when a marker is wrong or when a key is reached by
nothing and explained by nothing.

Five rules, and every one of them is a way for a marker to be worse than no marker at all:

1. **A marker's target exists.** A typo binds a value to a key that is not there while looking
   like coverage, so it is a hard failure with a `did you mean` — the same courtesy every other
   gate in this group extends.
2. **The class matches the key.** `structured` must name a key the contract calls `structured`,
   and `projection` must not: a scalar written where a map belongs is a defect the marker is in a
   position to state and therefore has to be held to.
3. **A `when` clause names a values path that exists.** A gate value that was renamed leaves the
   marker asserting a condition nothing evaluates.
4. **One key is bound by one value — except under `composed`.** That exception is the entire
   reason `composed` is a class: `mp-stats`' `server.bind_addr` is `printf "%s:%v" host port`, so
   two values legitimately feed one key, and both must say so. The converse rule this used to
   carry — a lone `composed` is really a projection — was **removed, because `tankovault`
   falsifies it**: `metrics.listen` is `printf "0.0.0.0:%v" .Values.metrics.port`, one value and a
   literal the chart supplies, whose text is not the value's text. `composed` now says "an input",
   and the arity is counted here rather than asserted by the class.
5. **Every key of a bound chart is bound, or `unbound` with a reason.** This is the rule the whole
   file exists for; the other four keep it from being satisfied by fiction.

The converse of rule 4 — one value binds one key — is enforced by `parse_values` rather than here,
and it is a rule rather than a property of the grammar. An earlier draft put the marker on the
value line, where a line has exactly one trailing comment and a value carrying two markers is
unwriteable; the marker now sits inside the value's `@schema` block, which can hold as many lines
as someone types, so the parser refuses a second one. Stated here because this is where a reader
looks for the rules, and "the grammar makes it impossible" is no longer the answer.

**Enrolment is opt-in and declared, by `bindings: true` in `config-contract.yaml`.** A gate that
failed unenrolled charts would be red for work nobody has scheduled and would end up disabled,
which is worse than absent. A chart that does not set the switch is not checked and not reported.
All five charts that map values onto a contract set it today: `portfolio`, `netcup-offer-bot`,
`s3-bucket-perma-link`, `mp-stats-legacy-viewer` and `tankovault`.

**What a nine-document chart changed about all of this.** The format was designed against two
single-document charts, and `tankovault` — nine documents, 183 distinct key paths in 582
declarations — falsified four of its rules at once. Recorded here because each rule read as
obviously right until a chart of that shape was actually put through it:

1. **A value that feeds several documents could not say so.** 93 of those 183 paths are declared
   by several documents at once, twenty-four of them by eight documents each — `bind_addr`, the
   five `metrics.*` keys and the whole of `telemetry.*`, `telemetry.sentry.*` included. One
   template line writes each into every service.
   The first draft refused an unqualified target that resolved in more than one document and asked
   for a qualifier, which made the case inexpressible: a value carries one marker, and a qualified
   marker covers one document and leaves the rest owing. An unscoped marker now binds **every**
   document declaring the key, and a scope narrows it where the chart really does write the key
   for only some of them.
2. **`unbound` was per document.** Only 53 of the 183 paths are written by any rendered document
   at all; the other 130 are settings the image reads and this chart does not surface. Per
   document that was 378 entries repeating a handful of sentences, so `unbound` moved to the chart
   and takes a list of keys against one reason. Still one key per line, and still no patterns.
3. **A lone `composed` was refused.** See rule 4: `metrics.listen` is one value and a literal.
4. **One value could bind only one key.** `internal.tls.certDir` is the directory
   `internal.tls.cert`, `internal.tls.key` and `internal.tls.ca` are each built from, by three
   `printf`s in one template. A value carries a run of markers now — see `config_bindings.py`.

The measurement forced all four. What the chart ended up with: 53 markers resolving to 219
bindings, and 145 keys written off in six `unbound` entries — eleven the chart derives from its
own topology, sixteen delivered through the secrets directory, and the rest settings the image
reads that this chart offers no value for.

Inferring enrolment from "the chart carries at least one marker" is the obvious alternative, and
it was measured to fail in the direction that matters. Feeding this gate a `portfolio` whose ten
markers had been rewritten into a spelling the parser does not recognise produced exit 0 and a
report the chart had simply vanished from — no error, no mention, and the coverage rule it was
enrolled for silently not run. The same happens to a chart whose markers are deleted. With the
switch in the declaration, both disagreements are errors: markers without `bindings: true`, and
`bindings: true` without markers.

Enrolment is per *chart*, not per document, and that is deliberate for a chart like `tankovault`
that declares nine. A coverage figure over some of a chart's documents is not a coverage figure —
the keys the reader cares about are exactly the ones nobody got to yet. Setting the switch commits
the chart to all nine at once, which is a real cost and is meant to be one.

**No staleness interlock, unlike `check-config`.** `bind()` refuses to validate when the vendored
contract is not for the digest the chart pins, because reporting a pass it cannot justify is worse
than reporting nothing. That reasoning does not carry here: this gate compares two committed files
— `values.yaml` and `contracts/*.json` — and whether the second is current is
`just check-contracts`' question, answered in one line. Refusing to run during the single CI run
where a bump holds a new digest and the old contract would also remove this report from the exact
pull request it was written for. Same posture, and the same paragraph, as `config-secrets.py`.

Offline and render-free: two committed files per chart, no cluster, no network, no `helm template`.

Usage: python3 .github/scripts/check-config-bindings.py
       python3 .github/scripts/check-config-bindings.py --charts charts
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_bindings as cb
import config_contract as cc
from config_declaration import (
    Declaration,
    DeclarationError,
    Document,
    chart_dirs,
    load_declaration,
    union_for,
)
from config_paths import CHARTS_DIR
from config_report import Report


class Bound:
    """One chart's documents, resolved to the contracts that describe them.

    Built without the staleness interlock `config_declaration.bind` applies — see the module
    docstring — so this is deliberately not that function and does not pretend to be.
    """

    def __init__(self, chart_dir: Path, declaration: Declaration):
        self.chart = declaration.chart
        self.declaration = declaration
        self.documents: dict[str, Document] = {}
        self.unions: dict[str, cc.Union] = {}

        for document in declaration.documents:
            self.documents[document.name] = document
            self.unions[document.name] = union_for(chart_dir, document)

    def namespace(self, name: str, cls: str) -> dict[str, dict[str, Any]]:
        """The half of one document's contract a marker of this class may name."""
        union = self.unions[name]
        return union.keys if cls in cb.KEY_CLASSES else union.external_env


class Gate:
    """The five rules, over one chart at a time."""

    def __init__(self, report: Report):
        self.report = report

    def check_chart(self, chart_dir: Path) -> tuple[int, int] | None:
        """Check one chart; `None` when its declaration does not enrol it.

        Enrolment is read from `config-contract.yaml`, and both disagreements between the switch
        and the file are failures — see the module docstring for the drift that inferring it let
        through.
        """
        values_path = chart_dir / "values.yaml"
        if not values_path.is_file():
            return None

        markers = cb.parse_values(values_path, chart_dir.name)
        where = f"{chart_dir.name}: values.yaml"
        declaration = load_declaration(chart_dir)

        if declaration is None or not declaration.bindings:
            if markers:
                self.report.fail(
                    where,
                    f"carries {len(markers)} `{cb.MARKER}` marker(s), and its "
                    + (
                        "config-contract.yaml does not declare `bindings: true`"
                        if declaration is not None
                        else "chart has no config-contract.yaml to declare `bindings: true` in"
                    )
                    + ". Enrolment is declared rather than inferred, so that a chart which loses "
                    "its markers goes red instead of quietly leaving the report",
                )
                return 0, 0
            return None

        if not declaration.documents:
            self.report.fail(
                where,
                f"declares `bindings: true` but no configuration contract, so there is nothing to "
                f"hold a `{cb.MARKER}` marker against; declare a document, or drop the switch",
            )
            return 0, 0

        if not markers:
            self.report.fail(
                where,
                f"declares `bindings: true` and carries no `{cb.MARKER}` marker. Either the "
                "markers were lost — which is the drift this switch exists to catch — or the "
                "chart was never bound and the switch should not be set",
            )
            return 0, 0

        bound = Bound(chart_dir, declaration)
        values = yaml.safe_load(values_path.read_text(encoding="utf-8")) or {}

        resolved = self.resolve(bound, markers, values)
        self.check_uniqueness(resolved)
        self.check_write_offs(bound)
        self.check_coverage(bound, resolved)

        keys = sum(1 for marker, _ in resolved if marker.cls in cb.KEY_CLASSES)
        return keys, len(resolved) - keys

    # ------------------------------------------------------------------------------------
    # Rules 1 to 3 — one marker at a time
    # ------------------------------------------------------------------------------------

    def resolve(
        self, bound: Bound, markers: list[cb.Marker], values: dict[str, Any]
    ) -> list[tuple[cb.Marker, str]]:
        """Every marker that named something real, once per document it binds the key in."""
        resolved: list[tuple[cb.Marker, str]] = []

        for marker in markers:
            documents = self.resolve_documents(bound, marker)
            if not documents:
                continue
            if not self.check_shape(bound, marker, documents):
                continue
            self.check_condition(marker, values)
            resolved.extend((marker, document) for document in documents)

        return resolved

    def resolve_documents(self, bound: Bound, marker: cb.Marker) -> list[str]:
        """Which documents the marker binds its target in; empty when it named nothing real.

        An unscoped marker binds **every** document whose contract declares the target, which is
        what a chart does — `tankovault` writes `metrics.enabled` into all eight of its services
        from one template line, and eight of its key paths are declared by eight documents each.
        An earlier draft refused that case and asked for a qualifier, which made a value feeding
        several documents inexpressible: a value carries one marker, and a qualified marker covers
        one document and leaves the rest owing.

        A scope narrows it, for the case the default over-claims: the chart writes the key for
        some of the documents that declare it and not others. Naming a document that does not
        declare the target is refused rather than ignored, because that is how a scope goes stale.
        """
        if marker.documents is not None:
            unknown = [name for name in marker.documents if name not in bound.documents]
            if unknown:
                self.report.fail(
                    marker.where,
                    f"is scoped to {', '.join(repr(name) for name in unknown)}, which this chart "
                    f"does not declare (declared: {', '.join(sorted(bound.documents))})",
                )
                return []
            candidates = list(marker.documents)
        else:
            candidates = sorted(bound.documents)

        matched = [
            name for name in candidates if marker.target in bound.namespace(name, marker.cls)
        ]
        if matched and marker.documents is not None and len(matched) != len(candidates):
            missing = sorted(set(candidates) - set(matched))
            self.report.fail(
                marker.where,
                f"is scoped to {', '.join(missing)}, whose contract does not declare "
                f"{marker.target!r}; a scope that names a document the key is absent from is a "
                "scope nobody has re-read since the contract changed",
            )
            return []
        if matched:
            return matched

        namespace = "external.env variable" if marker.cls == cb.EXTERNAL else "contract key"
        known: list[str] = []
        for name in candidates:
            known.extend(bound.namespace(name, marker.cls))
        scope = (
            f"the document(s) {', '.join(marker.documents)} do not declare"
            if marker.documents
            else "no contract this chart declares carries"
        )
        self.report.fail(
            marker.where,
            f"binds the value {marker.values_path!r} to the {namespace} {marker.target!r}, which "
            f"{scope}{cc.suggest(marker.target, known)}",
        )
        return []

    def check_shape(self, bound: Bound, marker: cb.Marker, documents: list[str]) -> bool:
        """Rule 2. A `structured` key holds a map the operator names; a scalar key does not.

        Checked against every document the marker binds, and those documents are first required to
        agree with each other. Two contracts declaring one path in two different shapes are two
        different settings that happen to share a name, and binding both from one value would be
        the marker asserting something no reader could have meant.
        """
        forms = {
            document: bound.namespace(document, marker.cls)[marker.target].get("text_form")
            for document in documents
        }
        if len(set(forms.values())) > 1:
            spelt = ", ".join(f"{document}: {form}" for document, form in sorted(forms.items()))
            self.report.fail(
                marker.where,
                f"binds {marker.target!r} in documents that do not agree what it is ({spelt}); "
                "one path in two shapes is two settings, so scope the marker to the one this "
                "value feeds",
            )
            return False

        structured = next(iter(forms.values())) == "structured"

        if marker.cls == cb.STRUCTURED and not structured:
            self.report.fail(
                marker.where,
                f"is `{cb.STRUCTURED}`, but the contract calls {marker.target!r} a scalar "
                f"({next(iter(forms.values()))}); a scalar key takes `{cb.PROJECTION}`",
            )
            return False

        if marker.cls == cb.PROJECTION and structured:
            self.report.fail(
                marker.where,
                f"is `{cb.PROJECTION}`, but the contract calls {marker.target!r} structured "
                "— the keys underneath it are the operator's own names, which is what "
                f"`{cb.STRUCTURED}` says",
            )
            return False

        return True

    def check_condition(self, marker: cb.Marker, values: dict[str, Any]) -> None:
        """Rule 3. The gate value has to still be spelt the way the marker says it is."""
        if marker.condition is None:
            return
        if not cb.has_path(values, marker.condition):
            self.report.fail(
                marker.where,
                f"is written only `when {marker.condition}`, and this chart has no value at that "
                "path; a condition nothing evaluates is a subtree nobody can predict",
            )

    # ------------------------------------------------------------------------------------
    # Rule 4 — markers against each other
    # ------------------------------------------------------------------------------------

    def check_uniqueness(self, resolved: list[tuple[cb.Marker, str]]) -> None:
        """One key, one value — unless every value binding it says `composed`.

        Only this direction is checked here. The other one — a value binding two keys — is
        `parse_values`' business, because two markers in one `@schema` block never reach this far.
        """
        by_target: dict[tuple[str, str], list[cb.Marker]] = {}
        for marker, document in resolved:
            # Keyed by the document the target *resolved* to rather than by how it was written,
            # so a qualified and an unqualified marker naming one key collide as they should.
            by_target.setdefault((document, marker.target), []).append(marker)

        for (document, target), markers in sorted(by_target.items()):
            composed = [marker for marker in markers if marker.cls == cb.COMPOSED]

            if len(markers) > 1 and len(composed) != len(markers):
                self.report.fail(
                    markers[0].where,
                    f"{document}: {target!r} is bound by {len(markers)} values "
                    f"({', '.join(marker.values_path for marker in markers)}); only "
                    f"`{cb.COMPOSED}` admits several, and then every one of them must say so",
                )

    # ------------------------------------------------------------------------------------
    # Rule 5 — the coverage rule
    # ------------------------------------------------------------------------------------

    def check_write_offs(self, bound: Bound) -> None:
        """Every `unbound` key names something some contract in its scope declares.

        A chart-level question rather than a per-document one, and the distinction is the whole
        reason this is its own pass. An unscoped entry writes a key off *wherever it is declared*,
        so a document that does not declare it is not an error — it is the ordinary case for a
        chart whose nine services share a configuration crate and read different halves of it. An
        entry no document at all recognises is a different thing: a key that was renamed or
        removed upstream, still written off here, quietly covering nothing.
        """
        declared: dict[str, set[str]] = {}
        for name in bound.documents:
            for key in bound.unions[name].keys:
                declared.setdefault(key, set()).add(name)

        where = f"{bound.chart}: config-contract.yaml"
        for entry in bound.declaration.unbound:
            scope = set(entry.documents) if entry.documents else set(bound.documents)
            for key in entry.keys:
                holders = declared.get(key, set()) & scope
                if holders:
                    continue
                if entry.documents:
                    self.report.fail(
                        where,
                        f"`unbound` writes off {key!r} in {', '.join(sorted(scope))}, whose "
                        "contracts do not declare it"
                        + cc.suggest(key, sorted(declared)),
                    )
                else:
                    self.report.fail(
                        where,
                        f"`unbound` writes off {key!r}, which no contract this chart declares "
                        f"carries{cc.suggest(key, sorted(declared))}",
                    )

    def check_coverage(self, bound: Bound, resolved: list[tuple[cb.Marker, str]]) -> None:
        """Every contract key of an enrolled chart is bound, or written off with a reason.

        Over `schema.keys` only. `external.env` is a namespace the loader does not own — `PORT`
        belongs to the Dioxus toolchain and `RUST_LOG` to `tracing` — so a chart that offers no
        value for one of them has declined to expose somebody else's variable, which is not the
        omission this gate is looking for.
        """
        for name in sorted(bound.documents):
            union = bound.unions[name]
            where = f"{bound.chart}: {name}"

            bound_here = {
                marker.target
                for marker, in_document in resolved
                if in_document == name and marker.cls in cb.KEY_CLASSES
            }
            written_off = written_off_in(bound.declaration, name)

            for key in sorted(written_off & set(union.keys) & bound_here):
                self.report.fail(
                    f"{where}: config-contract.yaml",
                    f"`unbound` names {key!r} while a marker binds it; one of the two is out "
                    "of date, and the reason recorded here is the one that will be believed",
                )

            for key in sorted(set(union.keys) - bound_here - written_off):
                entry = union.keys[key]
                self.report.fail(
                    where,
                    f"the contract declares {key!r} and no chart value binds it. Add a "
                    f"`# # {cb.MARKER} ...` marker to the `@schema` block of the value that feeds "
                    "it, or an `unbound` entry with a reason in config-contract.yaml"
                    + (" (it is a credential; `just check-config-secrets` is what checks how "
                       "those are delivered)" if entry.get("secret") else ""),
                )


def written_off_in(declaration: Declaration, document: str) -> set[str]:
    """The keys `unbound` writes off in one document.

    An entry without a `documents` scope writes its keys off wherever they are declared, which is
    what "no chart value surfaces this" means for a chart whose services share a configuration
    crate. A scoped entry writes them off only in the documents it names.
    """
    keys: set[str] = set()
    for entry in declaration.unbound:
        if entry.documents is None or document in entry.documents:
            keys.update(entry.keys)
    return keys


def run(charts: Path, report: Report) -> list[tuple[str, int, int]]:
    """Check every enrolled chart; returns one `(chart, keys, external)` row per enrolment.

    Returns rather than prints, so a test can assert what was enrolled without reading stdout and
    so the caller decides what a run looks like — the same separation `config_report` already
    draws between finding something and saying it.
    """
    gate = Gate(report)
    enrolled: list[tuple[str, int, int]] = []

    for chart_dir in chart_dirs(charts):
        counted = gate.check_chart(chart_dir)
        if counted is None:
            continue

        enrolled.append((chart_dir.name, counted[0], counted[1]))

    return enrolled


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Check configuration binding markers against the contracts they name"
    )
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    args = parser.parse_args(argv)

    report = Report()
    try:
        enrolled = run(Path(args.charts), report)
    except (cb.BindingError, DeclarationError, cc.ContractError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    for chart, keys, external in enrolled:
        print(
            f"bound: {chart} ({keys} contract key(s), "
            f"{external} declared external variable(s))"
        )
    if not enrolled:
        print(f"==> no chart carries a `{cb.MARKER}` marker; nothing to check")

    report.print(sys.stdout, sys.stderr)

    if report.errors:
        print(f"\n{len(report.errors)} configuration binding violation(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
