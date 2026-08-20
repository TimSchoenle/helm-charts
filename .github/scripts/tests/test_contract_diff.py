#!/usr/bin/env python3
"""What a contract diff says changed, and what it says that costs.

The severity table is the part worth testing. Every one of its entries is a claim about what the
loader does at boot with a chart that was written against the older document — a removed key
lands on `classify` step 4 and is refused, a `text` key that became an `integer` one stops being
supplyable by the Secret the chart mounts for it, a removed external variable is absorbed only if
a surviving ignore pattern catches it. Each of those is a sentence a reviewer will act on, and
each is a place where a plausible-looking implementation grades a boot failure as a minor bump.

Everything here is two dictionaries in and findings out. The git plumbing, the chart walk and the
printing live in `contract-diff.py` and are deliberately not exercised from here: they are the
parts that need a repository, and none of them decides anything.

The documents come from `.github/testdata/contracts/`, wrapped in the `source` envelope the
vendored files carry — the same fixtures `test_contract_union.py` uses, so a change to the shape
of a contract is felt in one place rather than two.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_diff as cd

FIXTURES = Path(__file__).resolve().parents[2] / "testdata" / "contracts"

SOURCE = {
    "image": "docker.io/example/api",
    "digest": "sha256:" + "a" * 64,
    "sha256": "b" * 64,
    "fetched": "2026-01-01T00:00:00Z",
}


def vendored(name: str = "api") -> dict[str, Any]:
    """One fixture contract inside the provenance envelope a vendored file carries."""
    contract = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return {"source": copy.deepcopy(SOURCE), "contract": contract}


def diff(old: dict[str, Any], new: dict[str, Any]) -> cd.ContractDiff:
    return cd.diff_contract("demo", "api", "charts/demo/contracts/api.json", old, new)


def key(document: dict[str, Any], path: str) -> dict[str, Any]:
    for entry in document["contract"]["schema"]["keys"]:
        if entry["path"] == path:
            return entry
    raise AssertionError(f"no key {path} in the fixture")


def drop_key(document: dict[str, Any], path: str) -> None:
    keys = document["contract"]["schema"]["keys"]
    keys.remove(key(document, path))


def only(result: cd.ContractDiff, **match: Any) -> cd.Change:
    """The single change matching every named attribute, asserting there is exactly one."""
    found = [
        change
        for change in result.changes
        if all(getattr(change, name) == value for name, value in match.items())
    ]
    if len(found) != 1:
        raise AssertionError(f"expected one change matching {match}, found {found}")
    return found[0]


class TestNoChange(unittest.TestCase):
    def test_two_identical_documents_produce_nothing(self):
        result = diff(vendored(), vendored())
        self.assertEqual(result.status, cd.STATUS_UNCHANGED)
        self.assertEqual(result.changes, [])
        self.assertEqual(result.impact, cd.NONE)

    def test_a_moved_fetched_timestamp_is_not_a_change(self):
        new = vendored()
        new["source"]["fetched"] = "2026-06-06T06:06:06Z"
        self.assertEqual(diff(vendored(), new).changes, [])

    def test_a_moved_sha256_alone_is_not_a_change(self):
        new = vendored()
        new["source"]["sha256"] = "c" * 64
        self.assertEqual(diff(vendored(), new).changes, [])


class TestEnvelope(unittest.TestCase):
    def test_a_repin_alone_is_a_patch(self):
        new = vendored()
        new["source"]["digest"] = "sha256:" + "d" * 64
        result = diff(vendored(), new)
        self.assertEqual(result.impact, cd.PATCH)
        self.assertEqual(only(result, subject="source.digest").severity, cd.PATCH)

    def test_a_different_image_is_a_minor(self):
        new = vendored()
        new["source"]["image"] = "docker.io/example/api-v2"
        self.assertEqual(only(diff(vendored(), new), subject="source.image").severity, cd.MINOR)

    def test_the_application_version_is_a_patch(self):
        new = vendored()
        new["contract"]["app"]["version"] = "2.0.0"
        self.assertEqual(only(diff(vendored(), new), subject="app.version").severity, cd.PATCH)

    def test_the_envelope_version_is_a_major(self):
        new = vendored()
        new["contract"]["terrace_contract"] = 2
        change = only(diff(vendored(), new), subject="terrace_contract")
        self.assertEqual(change.severity, cd.MAJOR)

    def test_an_unreadable_envelope_is_reported_rather_than_raised(self):
        # The gates refuse a document whose envelope they do not recognise. This one has to
        # describe it instead, so the whole contract body being unreadable must still produce a
        # finding rather than an exception.
        new = {"source": copy.deepcopy(SOURCE), "contract": {"terrace_contract": 99}}
        result = diff(vendored(), new)
        self.assertEqual(result.impact, cd.MAJOR)
        self.assertEqual(only(result, subject="terrace_contract").new, 99)

    def test_the_schema_version_is_a_major(self):
        new = vendored()
        new["contract"]["schema"]["schema_version"] = 2
        self.assertEqual(
            only(diff(vendored(), new), subject="schema.schema_version").severity, cd.MAJOR
        )

    def test_every_dialect_field_is_a_major(self):
        for name, value in (
            ("prefix", "OTHER_"),
            ("nesting_separator", "_"),
            ("indirection_suffix", "_PATH"),
        ):
            with self.subTest(name):
                new = vendored()
                new["contract"]["schema"]["dialect"][name] = value
                change = only(diff(vendored(), new), subject=f"dialect.{name}")
                self.assertEqual(change.severity, cd.MAJOR)

    def test_tightening_the_unknown_policy_is_a_major_and_relaxing_it_is_a_minor(self):
        old = vendored()
        old["contract"]["external"]["unknown"] = "warn"
        self.assertEqual(
            only(diff(old, vendored()), subject="external.unknown").severity, cd.MAJOR
        )
        self.assertEqual(
            only(diff(vendored(), old), subject="external.unknown").severity, cd.MINOR
        )

    def test_an_ignore_pattern_is_a_minor_either_way(self):
        new = vendored()
        new["contract"]["external"]["ignore"] = ["OTEL_*"]
        result = diff(vendored(), new)
        self.assertTrue(result.changes)
        self.assertEqual({change.severity for change in result.changes}, {cd.MINOR})


class TestLoader(unittest.TestCase):
    def test_a_removed_loader_variable_is_a_major(self):
        new = vendored()
        loader = new["contract"]["schema"]["loader"]
        loader[:] = [entry for entry in loader if entry["role"] != "secrets_dir"]
        change = only(diff(vendored(), new), area=cd.LOADER, kind=cd.REMOVED)
        self.assertEqual(change.severity, cd.MAJOR)
        self.assertEqual(change.subject, "FIXTURE_SECRETS_DIR")

    def test_an_added_loader_variable_is_a_minor(self):
        new = vendored()
        new["contract"]["schema"]["loader"].append(
            {"env": "FIXTURE_PROFILE", "role": "profile", "docs": "", "default": None}
        )
        self.assertEqual(only(diff(vendored(), new), area=cd.LOADER).severity, cd.MINOR)

    def test_a_loader_role_change_is_a_major(self):
        new = vendored()
        new["contract"]["schema"]["loader"][0]["role"] = "secrets_dir"
        change = only(diff(vendored(), new), area=cd.LOADER, field="role")
        self.assertEqual(change.severity, cd.MAJOR)


class TestKeys(unittest.TestCase):
    def test_a_removed_key_is_a_major_naming_both_spellings(self):
        new = vendored()
        drop_key(new, "database.url")
        change = only(diff(vendored(), new), kind=cd.REMOVED, area=cd.KEY)
        self.assertEqual(change.severity, cd.MAJOR)
        self.assertIn("FIXTURE_DATABASE__URL", change.message)
        self.assertIn("database__url", change.message)

    def test_an_added_optional_key_is_a_minor(self):
        old = vendored()
        drop_key(old, "log.level")
        change = only(diff(old, vendored()), kind=cd.ADDED, area=cd.KEY)
        self.assertEqual(change.severity, cd.MINOR)

    def test_an_added_required_key_is_a_major(self):
        # Not in the stated table, which calls every added key minor. A key the chart does not
        # write and the image will not start without is the same failure as one that became
        # required, and grading it minor would recommend a bump that hides a broken deployment.
        old = vendored()
        drop_key(old, "database.url")
        new = vendored()
        key(new, "database.url")["required"] = True
        change = only(diff(old, new), kind=cd.ADDED, area=cd.KEY)
        self.assertEqual(change.severity, cd.MAJOR)

    def test_becoming_required_is_a_major_and_losing_it_is_a_minor(self):
        new = vendored()
        key(new, "log.level")["required"] = True
        self.assertEqual(only(diff(vendored(), new), field="required").severity, cd.MAJOR)
        self.assertEqual(only(diff(new, vendored()), field="required").severity, cd.MINOR)

    def test_a_moved_default_is_one_minor_change_not_two(self):
        new = vendored()
        entry = key(new, "auth.session_ttl")
        entry["default"], entry["default_value"] = "7200", 7200
        change = only(diff(vendored(), new), field="default")
        self.assertEqual(change.severity, cd.MINOR)
        self.assertEqual(change.old, {"default": "3600", "default_value": 3600})

    def test_a_secret_flip_is_a_minor(self):
        new = vendored()
        key(new, "database.url")["secret"] = False
        self.assertEqual(only(diff(vendored(), new), field="secret").severity, cd.MINOR)

    def test_losing_file_supplyability_escalates_a_text_form_change(self):
        # `database.url` is `text`, so a mounted Secret file can supply it. As an `integer` it
        # cannot: a file delivers a string and the loader does not coerce one into a number, so
        # the Secret the chart already mounts silently stops loading.
        new = vendored()
        entry = key(new, "database.url")
        entry["text_form"] = "integer"
        change = only(diff(vendored(), new), field="text_form")
        self.assertEqual(change.severity, cd.MAJOR)
        self.assertIn("mounted file", change.message)

    def test_a_text_form_change_that_keeps_a_key_unsupplyable_is_a_minor(self):
        new = vendored()
        key(new, "auth.session_ttl")["text_form"] = "boolean"
        self.assertEqual(only(diff(vendored(), new), field="text_form").severity, cd.MINOR)

    def test_constraints_types_and_enums_are_minor(self):
        for name, value in (
            ("constraint", {"type": "integer", "minimum": 60}),
            ("text_constraint", {"type": "string", "pattern": "^[0-9]+$"}),
            ("ty", "u32"),
            ("values", ["a", "b"]),
        ):
            with self.subTest(name):
                new = vendored()
                key(new, "auth.session_ttl")[name] = value
                self.assertEqual(only(diff(vendored(), new), field=name).severity, cd.MINOR)

    def test_a_changed_spelling_is_a_major(self):
        new = vendored()
        key(new, "database.url")["env"] = "FIXTURE_DATABASE__DSN"
        self.assertEqual(only(diff(vendored(), new), field="env").severity, cd.MAJOR)

    def test_prose_is_a_patch(self):
        new = vendored()
        key(new, "database.url")["docs"] = "Rewritten for clarity."
        result = diff(vendored(), new)
        self.assertEqual(result.impact, cd.PATCH)
        self.assertEqual(only(result, field="docs").severity, cd.PATCH)

    def test_a_field_this_diff_does_not_model_is_still_reported(self):
        new = vendored()
        key(new, "database.url")["deprecated_since"] = "1.4.0"
        change = only(diff(vendored(), new), field="deprecated_since")
        self.assertEqual(change.severity, cd.PATCH)
        self.assertIn("models no severity", change.message)


class TestExternal(unittest.TestCase):
    def _with_external(self, entries: list[dict[str, Any]], ignore: list[str]) -> dict[str, Any]:
        document = vendored()
        document["contract"]["external"]["env"] = entries
        document["contract"]["external"]["ignore"] = ignore
        document["contract"]["external"]["unknown"] = "reject"
        return document

    @staticmethod
    def _entry(name: str, **overrides: Any) -> dict[str, Any]:
        entry = {
            "name": name,
            "owner": "runtime",
            "docs": "",
            "ty": "String",
            "values": [],
            "constraint": {"type": "string"},
            "text_constraint": None,
            "text_form": "text",
            "default": None,
            "required": False,
            "secret": False,
        }
        entry.update(overrides)
        return entry

    def test_a_removed_external_variable_no_pattern_absorbs_is_a_major(self):
        old = self._with_external([self._entry("RUST_LOG")], ["KUBERNETES_*"])
        new = self._with_external([], ["KUBERNETES_*"])
        change = only(diff(old, new), area=cd.EXTERNAL, kind=cd.REMOVED)
        self.assertEqual(change.severity, cd.MAJOR)
        self.assertIn("refused at boot", change.message)

    def test_a_surviving_ignore_pattern_downgrades_a_removal(self):
        # `matches_ignore` is the producer's language, not a second opinion about it: a trailing
        # star matches any suffix and everything else is exact.
        old = self._with_external([self._entry("RUST_LOG")], ["RUST_*"])
        new = self._with_external([], ["RUST_*"])
        change = only(diff(old, new), area=cd.EXTERNAL, kind=cd.REMOVED)
        self.assertEqual(change.severity, cd.MINOR)
        self.assertIn("absorbs it", change.message)

    def test_an_added_required_external_variable_is_a_major(self):
        old = self._with_external([], [])
        new = self._with_external([self._entry("OTEL_ENDPOINT", required=True)], [])
        self.assertEqual(only(diff(old, new), area=cd.EXTERNAL).severity, cd.MAJOR)

    def test_an_added_optional_external_variable_is_a_minor(self):
        old = self._with_external([], [])
        new = self._with_external([self._entry("OTEL_ENDPOINT")], [])
        self.assertEqual(only(diff(old, new), area=cd.EXTERNAL).severity, cd.MINOR)

    def test_an_external_constraint_change_is_a_minor(self):
        old = self._with_external([self._entry("PORT")], [])
        new = self._with_external([self._entry("PORT", constraint={"type": "integer"})], [])
        self.assertEqual(only(diff(old, new), field="constraint").severity, cd.MINOR)


class TestWholeFile(unittest.TestCase):
    def test_a_new_contract_is_a_minor(self):
        result = cd.diff_contract("demo", "api", "p.json", None, vendored())
        self.assertEqual(result.status, cd.STATUS_ADDED)
        self.assertEqual(result.impact, cd.MINOR)
        self.assertEqual(result.new_image, SOURCE["image"])

    def test_a_deleted_contract_is_a_major(self):
        result = cd.diff_contract("demo", "api", "p.json", vendored(), None)
        self.assertEqual(result.status, cd.STATUS_REMOVED)
        self.assertEqual(result.impact, cd.MAJOR)
        self.assertEqual(result.old_image, SOURCE["image"])

    def test_comparing_two_absent_documents_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            cd.diff_contract("demo", "api", "p.json", None, None)

    def test_a_json_schema_that_moves_alone_is_reported(self):
        new = vendored()
        new["contract"]["json_schema"]["title"] = "Changed"
        change = only(diff(vendored(), new), subject="json_schema")
        self.assertEqual(change.severity, cd.PATCH)

    def test_a_json_schema_that_moves_with_a_key_is_not_reported_twice(self):
        new = vendored()
        new["contract"]["json_schema"]["title"] = "Changed"
        key(new, "database.url")["ty"] = "Url"
        result = diff(vendored(), new)
        self.assertEqual([change.subject for change in result.changes], ["database.url"])


class TestChartImpact(unittest.TestCase):
    def _chart(self, *impacts: str) -> cd.ChartDiff:
        chart = cd.ChartDiff("demo", old_version="1.2.3", new_version="1.2.3")
        for index, severity in enumerate(impacts):
            contract = cd.ContractDiff("demo", f"c{index}", "p.json", cd.STATUS_CHANGED)
            contract.changes.append(
                cd.Change(severity, cd.KEY, cd.CHANGED, f"k{index}", None, None, None, "")
            )
            chart.contracts.append(contract)
        return chart

    def test_the_largest_finding_sets_the_impact(self):
        self.assertEqual(self._chart(cd.PATCH, cd.MAJOR, cd.MINOR).impact, cd.MAJOR)
        self.assertEqual(self._chart(cd.PATCH, cd.MINOR).impact, cd.MINOR)
        self.assertEqual(self._chart().impact, cd.NONE)

    def test_only_the_findings_at_that_level_are_shown_as_drivers(self):
        chart = self._chart(cd.PATCH, cd.MAJOR, cd.MINOR)
        self.assertEqual([change.subject for change in chart.drivers], ["k1"])

    def test_worst_of_nothing_is_none(self):
        self.assertEqual(cd.worst([]), cd.NONE)


class TestVersionSuggestion(unittest.TestCase):
    def test_each_impact_moves_the_component_it_names(self):
        self.assertEqual(cd.suggest_version("5.1.0", cd.MAJOR), "6.0.0")
        self.assertEqual(cd.suggest_version("5.1.0", cd.MINOR), "5.2.0")
        self.assertEqual(cd.suggest_version("5.1.0", cd.PATCH), "5.1.1")
        self.assertIsNone(cd.suggest_version("5.1.0", cd.NONE))

    def test_a_version_this_cannot_read_produces_no_suggestion(self):
        for version in (None, "5.1", "5.1.0-rc.1", "v5.1.0", "five"):
            with self.subTest(version):
                self.assertIsNone(cd.suggest_version(version, cd.MAJOR))

    def test_the_observed_bump_is_the_largest_component_that_moved(self):
        self.assertEqual(cd.observed_bump("5.1.0", "6.0.0"), cd.MAJOR)
        self.assertEqual(cd.observed_bump("5.1.0", "5.2.0"), cd.MINOR)
        self.assertEqual(cd.observed_bump("5.1.0", "5.1.1"), cd.PATCH)
        self.assertEqual(cd.observed_bump("5.1.0", "5.1.0"), cd.NONE)
        self.assertIsNone(cd.observed_bump("5.1.0", None))

    def test_a_bump_already_in_the_branch_can_satisfy_the_suggestion(self):
        chart = cd.ChartDiff("demo", old_version="5.1.0", new_version="6.0.0")
        contract = cd.ContractDiff("demo", "api", "p.json", cd.STATUS_CHANGED)
        contract.changes.append(
            cd.Change(cd.MAJOR, cd.KEY, cd.REMOVED, "a.b", None, None, None, "")
        )
        chart.contracts.append(contract)
        self.assertTrue(chart.satisfied)

        chart.new_version = "5.2.0"
        self.assertFalse(chart.satisfied)

        chart.new_version = None
        self.assertIsNone(chart.satisfied)

    def test_a_chart_with_no_findings_is_always_satisfied(self):
        self.assertTrue(cd.ChartDiff("demo", old_version="1.0.0", new_version="1.0.0").satisfied)


class TestJson(unittest.TestCase):
    def test_the_report_survives_a_round_trip_through_json(self):
        new = vendored()
        drop_key(new, "database.url")
        chart = cd.ChartDiff("demo", [diff(vendored(), new)], "5.1.0", "5.1.0")
        payload = json.loads(json.dumps(chart.as_json()))
        self.assertEqual(payload["impact"], cd.MAJOR)
        self.assertEqual(payload["version"], {
            "old": "5.1.0",
            "new": "5.1.0",
            "bumped": False,
            "suggested": "6.0.0",
            "satisfied": False,
        })
        self.assertEqual([driver["subject"] for driver in payload["drivers"]], ["database.url"])
        self.assertEqual(payload["contracts"][0]["status"], cd.STATUS_CHANGED)


if __name__ == "__main__":
    unittest.main()
