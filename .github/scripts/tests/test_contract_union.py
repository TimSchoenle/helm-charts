#!/usr/bin/env python3
"""The union of several images' contracts, over fixtures rather than over a registry.

A document read by several binaries is the case the union exists for. Each contract covers only
the keys its own binary consumes, so validating that document against one of them with
`additionalProperties: false` would reject a perfectly correct deployment: every key belonging to
the others would be "unknown".

No chart declares one. Every document in this repository binds exactly one image — `tankovault`
declares nine, one per service, each with its own ConfigMap and its own contract — so the merge
is the identity in production and these fixtures are the only place its rules run at all. That is
a reason to keep them rather than a reason to drop them: a document that gained a second reader
lands straight on those rules, and this is the one place where getting them wrong turns a correct
chart red.

The fixtures in `.github/testdata/contracts/` are two contracts that share a key (`api`,
`worker`), one that describes a shared key differently (`conflicting`), one under a foreign
dialect, and one carrying a remote `$ref`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_contract as cc

FIXTURES = Path(__file__).resolve().parents[2] / "testdata" / "contracts"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def union(*names: str) -> cc.Union:
    return cc.union_contracts([(name, fixture(name)) for name in names])


class TestKeyUnion(unittest.TestCase):
    def test_a_single_contract_is_its_own_union(self):
        merged = union("api")
        self.assertIn("auth.session_ttl", merged.keys)
        self.assertEqual(merged.prefix, "FIXTURE_")

    def test_keys_from_every_contract_are_kept(self):
        merged = union("api", "worker")
        self.assertEqual(
            sorted(merged.keys),
            [
                "auth.session_ttl",
                "database.url",
                "github.repos",
                "log.level",
                "tuning.ratio",
                "worker.concurrency",
                "worker.debug",
            ],
        )

    def test_a_shared_key_is_kept_once(self):
        merged = union("api", "worker")
        self.assertEqual(merged.keys["database.url"]["ty"], "String")
        self.assertEqual(merged.keys["database.url"]["_sources"], ["api", "worker"])

    def test_two_descriptions_of_one_key_is_a_hard_error(self):
        with self.assertRaises(cc.ContractError) as raised:
            union("api", "conflicting")
        message = str(raised.exception)
        self.assertIn("auth.session_ttl", message)
        self.assertIn("api", message)
        self.assertIn("conflicting", message)

    def test_a_foreign_dialect_is_a_hard_error(self):
        with self.assertRaises(cc.ContractError) as raised:
            union("api", "foreign-dialect")
        self.assertIn("dialect", str(raised.exception))

    def test_required_is_unioned(self):
        left, right = fixture("api"), fixture("worker")
        _key(left, "database.url")["required"] = False
        merged = cc.union_contracts([("api", left), ("worker", right)])
        self.assertTrue(merged.keys["database.url"]["required"])

    def test_a_differing_field_outside_required_is_a_hard_error(self):
        # The catch-all: naming the fields that matter means the next one the producer adds falls
        # through the gap in silence, so everything but `required` has to agree.
        left, right = fixture("api"), fixture("worker")
        _key(right, "database.url")["docs"] = "Something else entirely."
        with self.assertRaises(cc.ContractError) as raised:
            cc.union_contracts([("api", left), ("worker", right)])
        self.assertIn("`docs`", str(raised.exception))

    def test_an_empty_union_is_refused(self):
        with self.assertRaises(cc.ContractError):
            cc.union_contracts([])


class TestExternalUnion(unittest.TestCase):
    def test_ignore_patterns_are_unioned_and_sorted(self):
        self.assertEqual(union("api", "worker").ignore, ["HOSTNAME", "KUBERNETES_*"])

    def test_the_strictest_unknown_policy_wins(self):
        # `api` rejects, `worker` warns: a variable any reader refuses is one the pod cannot
        # carry, so the union rejects regardless of the order the two are listed in.
        self.assertEqual(union("api", "worker").unknown, "reject")
        self.assertEqual(union("worker", "api").unknown, "reject")

    def test_external_variables_are_kept(self):
        self.assertIn("PORT", union("api", "worker").external_env)

    def test_two_descriptions_of_one_external_variable_is_a_hard_error(self):
        left, right = fixture("api"), fixture("worker")
        right["external"]["env"] = [
            dict(left["external"]["env"][0], ty="String", constraint={"type": "string"})
        ]
        with self.assertRaises(cc.ContractError) as raised:
            cc.union_contracts([("api", left), ("worker", right)])
        self.assertIn("PORT", str(raised.exception))


class TestSchemaUnion(unittest.TestCase):
    def test_properties_from_every_contract_are_merged(self):
        merged = union("api", "worker").json_schema
        self.assertEqual(
            sorted(merged["properties"]),
            ["auth", "database", "github", "log", "tuning", "worker"],
        )

    def test_every_level_is_closed_after_the_union(self):
        merged = union("api", "worker").json_schema
        self.assertIs(merged["additionalProperties"], False)
        for name in ("auth", "database", "worker"):
            self.assertIs(merged["properties"][name]["additionalProperties"], False)

    def test_a_single_contract_is_closed_at_every_level_too(self):
        # The first contract merged takes the same path as every one after it: a document read by
        # one image must come out as closed as one read by eight, or a producer that left a level
        # open would leave the gate open there.
        left = fixture("api")
        left["json_schema"]["properties"]["auth"]["additionalProperties"] = True
        merged = cc.union_contracts([("api", left)]).json_schema
        self.assertIs(merged["properties"]["auth"]["additionalProperties"], False)

    def test_required_is_unioned_per_object(self):
        left, right = fixture("api"), fixture("worker")
        right["json_schema"]["properties"]["database"]["required"] = ["url", "pool_size"]
        right["json_schema"]["properties"]["database"]["properties"]["pool_size"] = {
            "type": "integer"
        }
        merged = cc.union_contracts([("api", left), ("worker", right)]).json_schema
        self.assertEqual(merged["properties"]["database"]["required"], ["pool_size", "url"])

    def test_two_types_for_one_path_is_a_hard_error(self):
        with self.assertRaises(cc.ContractError) as raised:
            cc.union_contracts(
                [("api", fixture("api")), ("conflicting", _schema_only(fixture("conflicting")))]
            )
        self.assertIn("$.auth.session_ttl", str(raised.exception))

    def test_a_differing_bound_is_a_hard_error(self):
        left, right = fixture("api"), _schema_only(fixture("worker"))
        right["json_schema"]["properties"]["auth"] = {
            "type": "object",
            "properties": {"session_ttl": {"type": "integer", "minimum": 60, "default": 3600}},
        }
        with self.assertRaises(cc.ContractError) as raised:
            cc.union_contracts([("api", left), ("worker", right)])
        self.assertIn("minimum", str(raised.exception))

    def test_a_differing_description_is_a_hard_error_too(self):
        # The catch-all covers annotations. See the note in the module docstring of the report:
        # this is the rule as specified, and it is what makes a differing root `title` fatal.
        left, right = fixture("api"), _schema_only(fixture("worker"))
        left["json_schema"]["properties"]["auth"]["properties"]["session_ttl"]["description"] = "a"
        right["json_schema"]["properties"]["auth"] = {
            "type": "object",
            "properties": {
                "session_ttl": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 3600,
                    "description": "b",
                }
            },
        }
        with self.assertRaises(cc.ContractError) as raised:
            cc.union_contracts([("api", left), ("worker", right)])
        self.assertIn("description", str(raised.exception))

    def test_a_differing_root_title_is_a_hard_error(self):
        # Recorded deliberately: every generated contract carries `title: "<app> configuration"`,
        # so under the catch-all no two contracts of one document can be unioned until the
        # producer omits it or the rule exempts it. `tankovault` is where this will bite.
        left = fixture("api")
        right = _schema_only(fixture("worker"))
        right["json_schema"]["title"] = "worker configuration"
        with self.assertRaises(cc.ContractError) as raised:
            cc.union_contracts([("api", left), ("worker", right)])
        self.assertIn("title", str(raised.exception))


class TestRemoteReferences(unittest.TestCase):
    def test_a_remote_ref_is_reported(self):
        offenders = cc.local_refs_only(fixture("remote-ref")["json_schema"])
        self.assertEqual(len(offenders), 1)
        self.assertIn("example.invalid", offenders[0])

    def test_a_local_ref_is_allowed(self):
        self.assertEqual(cc.local_refs_only({"$ref": "#/definitions/thing"}), [])

    def test_the_real_fixtures_carry_none(self):
        self.assertEqual(cc.local_refs_only(union("api", "worker").json_schema), [])


class TestEnvelope(unittest.TestCase):
    def test_an_unrecognised_envelope_version_is_refused_by_name(self):
        document = fixture("api")
        document["terrace_contract"] = 2
        with self.assertRaises(cc.ContractError) as raised:
            cc.check_envelope(document, "api.json")
        self.assertIn("terrace_contract", str(raised.exception))

    def test_a_missing_section_is_refused(self):
        document = fixture("api")
        del document["external"]
        with self.assertRaises(cc.ContractError):
            cc.check_envelope(document, "api.json")

    def test_an_unknown_policy_is_refused(self):
        document = fixture("api")
        document["external"]["unknown"] = "ignore"
        with self.assertRaises(cc.ContractError):
            cc.check_envelope(document, "api.json")

    def test_a_text_form_this_repository_cannot_read_is_refused(self):
        document = fixture("api")
        _key(document, "database.url")["text_form"] = "duration"
        with self.assertRaises(cc.ContractError) as raised:
            cc.check_envelope(document, "api.json")
        self.assertIn("duration", str(raised.exception))

    def test_the_vendored_portfolio_contract_reads(self):
        path = Path("charts/portfolio/contracts/server.json")
        if not path.is_file():  # pragma: no cover - only when run from another directory
            self.skipTest(f"{path} is not reachable from {Path.cwd()}")
        vendored = cc.load_vendored(path)
        self.assertTrue(vendored.digest.startswith("sha256:"))
        merged = cc.union_contracts([("server", vendored.contract)])
        self.assertIn("isr.ttl_secs", merged.keys)
        self.assertEqual(cc.local_refs_only(merged.json_schema), [])


def _key(document: dict, path: str) -> dict:
    return next(entry for entry in document["schema"]["keys"] if entry["path"] == path)


def _schema_only(document: dict) -> dict:
    """Strip a fixture's keys so a `json_schema` conflict is what the union reports first."""
    document["schema"]["keys"] = []
    return document


if __name__ == "__main__":
    unittest.main()
