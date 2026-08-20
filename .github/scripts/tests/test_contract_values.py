#!/usr/bin/env python3
"""The classification order and the two-step value check.

Both are normative rather than convenient. Two consumers running the classification steps in
different orders disagree about whether a deployment is valid, and a consumer that runs only one
of the two value checks either leaves every bound in the document decorative or refuses a
correct deployment. Neither failure is visible from the outside — the gate goes green either way
— so these are the tests that say the implementation is the specified one.
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


def key_of(merged: cc.Union, path: str) -> dict:
    return merged.keys[path]


class TestClassificationOrder(unittest.TestCase):
    """First match wins, and the order is copied from `External`'s own documentation."""

    def setUp(self):
        self.union = union("api", "worker")

    def test_1_a_loader_variable(self):
        self.assertEqual(cc.classify(self.union, "FIXTURE_CONFIG").kind, cc.LOADER)
        self.assertEqual(cc.classify(self.union, "FIXTURE_SECRETS_DIR").kind, cc.LOADER)

    def test_2_a_key_supplied_by_the_environment(self):
        decision = cc.classify(self.union, "FIXTURE_AUTH__SESSION_TTL")
        self.assertEqual(decision.kind, cc.KEY_ENV)
        self.assertEqual(decision.entry["path"], "auth.session_ttl")

    def test_3_a_key_supplied_by_indirection(self):
        decision = cc.classify(self.union, "FIXTURE_AUTH__SESSION_TTL_FILE")
        self.assertEqual(decision.kind, cc.KEY_ENV_FILE)
        self.assertEqual(decision.entry["path"], "auth.session_ttl")

    def test_4_a_prefixed_variable_spelling_nothing_is_rejected(self):
        self.assertEqual(cc.classify(self.union, "FIXTURE_NOPE").kind, cc.PREFIXED)

    def test_5_a_declared_external_variable(self):
        self.assertEqual(cc.classify(self.union, "PORT").kind, cc.EXTERNAL)

    def test_6_an_ignored_variable(self):
        self.assertEqual(cc.classify(self.union, "KUBERNETES_SERVICE_HOST").kind, cc.IGNORED)
        self.assertEqual(cc.classify(self.union, "HOSTNAME").kind, cc.IGNORED)

    def test_7_everything_else(self):
        self.assertEqual(cc.classify(self.union, "PATH").kind, cc.UNKNOWN)

    def test_step_4_outranks_both_external_lists(self):
        """The load-bearing part: neither list can exempt something inside the namespace.

        An `external.env` entry and an `ignore` pattern that reach into the prefix are both
        refused by the producer. This is the consumer-side half: even if one arrived, step 4
        would have rejected the variable before either list was consulted.
        """
        merged = union("api")
        merged.external_env["FIXTURE_AUTH__NOPE"] = {"name": "FIXTURE_AUTH__NOPE"}
        merged.ignore = ["FIXTURE_*"]
        self.assertEqual(cc.classify(merged, "FIXTURE_AUTH__NOPE").kind, cc.PREFIXED)


class TestIgnorePatterns(unittest.TestCase):
    """The whole language: a trailing `*` matches any suffix, otherwise exact."""

    def test_a_trailing_star_is_a_prefix_match(self):
        self.assertTrue(cc.matches_ignore("KUBERNETES_*", "KUBERNETES_SERVICE_HOST"))
        self.assertTrue(cc.matches_ignore("KUBERNETES_*", "KUBERNETES_"))

    def test_anything_else_is_exact(self):
        self.assertTrue(cc.matches_ignore("HOSTNAME", "HOSTNAME"))
        self.assertFalse(cc.matches_ignore("HOSTNAME", "HOSTNAME_2"))

    def test_a_star_in_the_middle_is_not_a_wildcard(self):
        self.assertFalse(cc.matches_ignore("A*B", "AXB"))

    def test_it_is_not_fnmatch(self):
        self.assertFalse(cc.matches_ignore("PORT?", "PORTS"))
        self.assertFalse(cc.matches_ignore("[AB]", "A"))


class TestTwoStepCheck(unittest.TestCase):
    """Form, then range — and `text_form` decides the parse, not the constraint's shape."""

    def setUp(self):
        self.union = union("api", "worker")

    def test_a_well_formed_integer_passes_both(self):
        entry = key_of(self.union, "worker.concurrency")
        self.assertIsNone(cc.check_text(entry, "8080"))
        self.assertIsNone(cc.check_parsed(entry, "8080"))

    def test_the_form_step_rejects_text_that_is_not_an_integer(self):
        entry = key_of(self.union, "worker.concurrency")
        self.assertIsNotNone(cc.check_text(entry, "http"))

    def test_the_range_step_is_the_only_one_a_bound_is_reachable_from(self):
        # 99999 is a perfectly well-formed integer; only the bound catches it not fitting a u16.
        # This is the check a form-only implementation silently omits.
        entry = key_of(self.union, "worker.concurrency")
        self.assertIsNone(cc.check_text(entry, "99999"))
        failure = cc.check_parsed(entry, "99999")
        self.assertIsNotNone(failure)
        self.assertIn("65535", failure)

    def test_the_range_step_does_not_reject_a_correct_integer_as_text(self):
        # Applying `constraint` to the raw text would fail "0" against {"type": "integer"} and
        # refuse a correct deployment.
        entry = key_of(self.union, "auth.session_ttl")
        self.assertIsNone(cc.check_text(entry, "0"))
        self.assertIsNone(cc.check_parsed(entry, "0"))

    def test_the_loaders_integer_grammar(self):
        entry = key_of(self.union, "auth.session_ttl")
        for accepted in ("0", "42", "007", "+5", " 7 "):
            self.assertIsNone(cc.check_text(entry, accepted), accepted)
        for refused in ("1_000", "0x1F", "0b1", "1e3", ""):
            self.assertIsNotNone(cc.check_text(entry, refused), refused)

    def test_a_boolean_takes_only_the_two_spellings(self):
        entry = key_of(self.union, "worker.debug")
        self.assertIsNone(cc.check_text(entry, "true"))
        self.assertIsNone(cc.check_text(entry, "false"))
        for refused in ("TRUE", "1", "yes", "True"):
            self.assertIsNotNone(cc.check_text(entry, refused), refused)

    def test_a_choice_takes_only_the_listed_spellings(self):
        entry = key_of(self.union, "log.level")
        self.assertIsNone(cc.check_text(entry, "debug"))
        self.assertIsNotNone(cc.check_text(entry, "verbose"))

    def test_text_is_unconstrained_and_has_no_range_step(self):
        entry = key_of(self.union, "database.url")
        self.assertIsNone(cc.check_text(entry, "anything at all"))
        self.assertIsNone(cc.check_parsed(entry, "anything at all"))

    def test_unknown_skips_both_steps_rather_than_guessing(self):
        entry = key_of(self.union, "tuning.ratio")
        self.assertIsNone(cc.check_text(entry, "whatever"))
        self.assertIsNone(cc.check_parsed(entry, "whatever"))

    def test_a_structured_value_must_carry_its_brackets(self):
        # The newest shape in the document and the least exercised. `a,b` reads like a list, is
        # not one, and is refused by the loader at boot.
        entry = key_of(self.union, "github.repos")
        failure = cc.check_text(entry, "a,b")
        self.assertIsNotNone(failure)
        self.assertIn("brackets", failure)

    def test_a_structured_value_with_brackets_is_accepted(self):
        entry = key_of(self.union, "github.repos")
        self.assertIsNone(cc.check_text(entry, '["a", "b"]'))
        self.assertIsNone(cc.check_text(entry, "{ a = 1 }"))

    def test_the_form_is_read_from_text_form_not_inferred(self):
        # A key whose constraint looks integral but whose form says text must not be parsed as an
        # integer: the inference "a pattern means integer" was right for two shapes and wrong for
        # three, and this is the case that separates them.
        entry = dict(key_of(self.union, "database.url"))
        entry["constraint"] = {"type": "integer", "maximum": 10}
        self.assertIsNone(cc.check_parsed(entry, "99999"))

    def test_an_external_variable_is_checked_the_same_two_ways(self):
        entry = self.union.external_env["PORT"]
        self.assertIsNotNone(cc.check_text(entry, "http"))
        self.assertIsNone(cc.check_text(entry, "99999"))
        self.assertIsNotNone(cc.check_parsed(entry, "99999"))


class TestFileSupplyable(unittest.TestCase):
    """A file layer delivers a string with no parse, so only a `text` key can use one."""

    def setUp(self):
        self.union = union("api", "worker")

    def test_a_text_key_can_be_file_supplied(self):
        self.assertTrue(cc.file_supplyable(key_of(self.union, "database.url")))

    def test_a_numeric_key_cannot(self):
        self.assertFalse(cc.file_supplyable(key_of(self.union, "auth.session_ttl")))

    def test_a_boolean_key_cannot(self):
        self.assertFalse(cc.file_supplyable(key_of(self.union, "worker.debug")))

    def test_a_structured_key_cannot(self):
        self.assertFalse(cc.file_supplyable(key_of(self.union, "github.repos")))

    def test_a_choice_key_cannot(self):
        self.assertFalse(cc.file_supplyable(key_of(self.union, "log.level")))

    def test_an_unknown_key_cannot(self):
        # Refused deliberately: nothing is known about how the loader reads it, so nothing says a
        # raw string will do. A false report is one line in review; a missing one is a credential
        # silently unread.
        self.assertFalse(cc.file_supplyable(key_of(self.union, "tuning.ratio")))


class TestConstraintVocabulary(unittest.TestCase):
    def test_an_unimplemented_keyword_is_an_error_rather_than_a_silent_skip(self):
        with self.assertRaises(cc.ContractError) as raised:
            cc.assert_value({"type": "string", "contentEncoding": "base64"}, "x")
        self.assertIn("contentEncoding", str(raised.exception))

    def test_annotations_are_not_assertions(self):
        self.assertIsNone(cc.assert_value({"type": "string", "description": "x"}, "y"))

    def test_a_boolean_is_not_an_integer(self):
        self.assertIsNotNone(cc.assert_value({"type": "integer"}, True))


class TestSuggest(unittest.TestCase):
    def test_a_near_miss_is_offered(self):
        self.assertIn("isr.ttl_secs", cc.suggest("isr.ttl_sec", ["isr.ttl_secs", "isr.cache_dir"]))

    def test_nothing_close_offers_nothing(self):
        self.assertEqual(cc.suggest("completely.different", ["isr.ttl_secs"]), "")

    def test_the_name_itself_is_never_offered(self):
        self.assertEqual(cc.suggest("isr.ttl_secs", ["isr.ttl_secs"]), "")


if __name__ == "__main__":
    unittest.main()
