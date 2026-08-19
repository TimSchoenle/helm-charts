#!/usr/bin/env python3
r"""Probe synthesis and the suites it produces, over hand-built keys rather than over a chart.

The generator's whole value rests on one property: a case it emits fails when the chart stops
delivering the setting. Two ways of losing that property are silent — a probe equal to the key's
default, which passes whether or not anything was delivered, and a pattern loose enough to match
the leaf under some other table, which passes when the chart writes the value in the wrong place.
Neither shows up as a red test; both show up as a suite that has quietly stopped proving
anything. So they are asserted here, key by key, alongside the refusals: a probe must never be
invented for a secret, for a `structured` key or for an `unknown` one, and the generated file has
to say which of the three applied.

The rest is determinism. The staleness gate is a comparison of bytes, so a generator that sorted
by dictionary order or wrote a timestamp into the header would fail a pull request that changed
nothing — which is the fastest way to have the gate disabled.

`test_contract_union.py`'s fixtures describe an image; the keys below are written in place
instead, because what is under test is one key at a time and a fixture would put five files
between the reader and the rule.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_contract as cc  # noqa: E402
import config_testgen as tg  # noqa: E402
from config_declaration import DeclarationError  # noqa: E402


def load_entry_point():
    """The hyphenated entry point, which is not importable by name.

    Registered in `sys.modules` before it is executed: `dataclasses` resolves a field's
    annotation by looking the defining module up there, and a module that is not registered
    fails to define its first frozen dataclass.
    """
    spec = importlib.util.spec_from_file_location(
        "generate_contract_tests", SCRIPTS / "generate-contract-tests.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_entry_point()


def key(path: str, **overrides: Any) -> dict[str, Any]:
    """One contract key, with the fields every entry carries and no others."""
    entry: dict[str, Any] = {
        "path": path,
        "env": "APP_" + path.upper().replace(".", "__"),
        "text_form": "text",
        "constraint": {"type": "string"},
        "default_value": None,
        "required": False,
        "secret": False,
        "reserved": False,
        "values": [],
    }
    entry.update(overrides)
    return entry


class TestProbeSynthesis(unittest.TestCase):
    def test_a_boolean_probe_is_the_opposite_of_the_default(self):
        probe, reason = tg.probe_for(
            key("csp.hashes", text_form="boolean", constraint={"type": "boolean"},
                default_value=True)
        )
        self.assertIsNone(reason)
        self.assertIs(probe.value, False)
        self.assertEqual(probe.text, "false")

    def test_a_boolean_with_no_default_still_gets_a_probe(self):
        probe, _ = tg.probe_for(
            key("csp.hashes", text_form="boolean", constraint={"type": "boolean"})
        )
        self.assertIs(probe.value, True)

    def test_an_integer_probe_is_distinctive_rather_than_adjacent_to_the_default(self):
        probe, _ = tg.probe_for(
            key("isr.ttl", text_form="integer", constraint={"type": "integer", "minimum": 0},
                default_value=0)
        )
        self.assertEqual(probe.value, tg.DISTINCTIVE_INTEGER)

    def test_an_integer_probe_respects_an_upper_bound(self):
        probe, _ = tg.probe_for(
            key("server.port", text_form="integer",
                constraint={"type": "integer", "minimum": 1, "maximum": 99}, default_value=8080)
        )
        self.assertGreaterEqual(probe.value, 1)
        self.assertLessEqual(probe.value, 99)

    def test_an_integer_probe_respects_multiple_of(self):
        probe, _ = tg.probe_for(
            key("tuning.step", text_form="integer",
                constraint={"type": "integer", "minimum": 0, "multipleOf": 5}, default_value=0)
        )
        self.assertEqual(probe.value % 5, 0)
        self.assertNotEqual(probe.value, 0)

    def test_a_text_probe_names_the_key_it_probes(self):
        probe, _ = tg.probe_for(key("assets.dist_dir", default_value="public"))
        self.assertEqual(probe.value, "contract-probe-assets-dist-dir")
        self.assertEqual(probe.text, '"contract-probe-assets-dist-dir"')

    def test_a_text_probe_is_shortened_to_fit_a_maximum_length(self):
        probe, _ = tg.probe_for(
            key("assets.dist_dir", constraint={"type": "string", "maxLength": 12})
        )
        self.assertEqual(len(probe.value), 12)

    def test_the_environment_spelling_steers_the_choice(self):
        """A probe the file could carry and a variable could not is not deliverable both ways."""
        probe, _ = tg.probe_for(
            key("isr.ttl", text_form="integer", constraint={"type": "integer", "minimum": 0},
                text_constraint={"type": "string", "pattern": r"^\s*[0-9]{2}\s*$"})
        )
        self.assertNotEqual(probe.value, tg.DISTINCTIVE_INTEGER)
        self.assertRegex(str(probe.value), r"^[0-9]{2}$")

    def test_a_choice_probe_is_a_declared_value_other_than_the_default(self):
        probe, _ = tg.probe_for(
            key("rate_limit.backend", text_form="choice", values=["memory", "redis"],
                constraint={"type": "string", "enum": ["memory", "redis"]},
                default_value="memory")
        )
        self.assertEqual(probe.value, "redis")


class TestProbesThatMustNotBeInvented(unittest.TestCase):
    """Every refusal, because a probe invented here is worse than a key left uncovered."""

    def test_a_secret_key_is_refused(self):
        probe, reason = tg.probe_for(key("database.password", secret=True))
        self.assertIsNone(probe)
        self.assertIn("secret", reason)

    def test_a_structured_key_is_refused_for_its_own_reason(self):
        probe, reason = tg.probe_for(
            key("internal.peers", text_form="structured", constraint={"type": "object"})
        )
        self.assertIsNone(probe)
        self.assertIn("structured", reason)
        self.assertIn("operator's own names", reason)

    def test_an_unknown_key_is_refused_for_a_different_reason(self):
        probe, reason = tg.probe_for(key("tuning.ratio", text_form="unknown", constraint={}))
        self.assertIsNone(probe)
        self.assertIn("no constraint", reason)

    def test_a_key_pinned_by_const_carries_no_probe(self):
        probe, reason = tg.probe_for(
            key("app.mode", constraint={"type": "string", "const": "server"},
                default_value="server")
        )
        self.assertIsNone(probe)
        self.assertIn("const", reason)

    def test_a_text_key_constrained_by_a_pattern_carries_no_probe(self):
        probe, reason = tg.probe_for(
            key("app.slug", constraint={"type": "string", "pattern": "^[0-9]{4}$"})
        )
        self.assertIsNone(probe)
        self.assertIn("pattern", reason)

    def test_a_single_valued_choice_carries_no_probe(self):
        probe, reason = tg.probe_for(
            key("app.mode", text_form="choice", values=["server"],
                constraint={"type": "string", "enum": ["server"]}, default_value="server")
        )
        self.assertIsNone(probe)
        self.assertIn("differing from the default", reason)

    def test_a_constraint_outside_the_vocabulary_carries_no_probe(self):
        probe, reason = tg.probe_for(
            key("app.slug", constraint={"type": "string", "allOf": [{"minLength": 1}]})
        )
        self.assertIsNone(probe)
        self.assertIn("allOf", reason)

    def test_a_probe_no_environment_spelling_can_carry_is_refused(self):
        """`text_constraint` governs the variable, and a key has to be deliverable both ways."""
        probe, reason = tg.probe_for(
            key("isr.ttl", text_form="integer", constraint={"type": "integer"},
                text_constraint={"type": "string", "pattern": r"^\s*[0-9]{40}\s*$"})
        )
        self.assertIsNone(probe)
        self.assertIn("differing from the default", reason)

    def test_the_vocabulary_matches_the_one_contracts_are_read_under(self):
        """A keyword `config_contract` accepts and this refuses would be an unexplained skip."""
        for keyword in sorted(cc.ASSERTIONS):
            with self.subTest(keyword=keyword):
                self.assertNotIn(
                    keyword, tg.out_of_vocabulary({keyword: 1, "type": "integer"})
                )


class TestValueChecking(unittest.TestCase):
    def test_a_boolean_does_not_satisfy_an_integer_bound(self):
        """`True` is an `int` in Python and is not one in JSON Schema."""
        self.assertIsNotNone(tg.unmet(True, {"type": "integer", "minimum": 0}))

    def test_an_annotation_is_not_an_assertion(self):
        self.assertIsNone(tg.unmet("anything", {"type": "string", "default": "other"}))

    def test_an_uncompilable_pattern_is_a_failure_rather_than_a_pass(self):
        self.assertIsNotNone(tg.unmet("x", {"pattern": "([unclosed"}))


class TestAssertionSpelling(unittest.TestCase):
    """The pattern has to reject what a careless one would accept."""

    DOCUMENT = (
        '[assets]\ndist_dir = "public"\n'
        "\n[csp]\nhash_inline_scripts = true\n"
        "\n[csp.cloudflare]\nscript_nonce = true\nturnstile = false\nweb_analytics = false\n"
        "\n[isr]\ncache_dir = \"/tmp/isr\"\nttl_secs = 0\n"
    )

    def matches(self, path: str, text: str) -> bool:
        return re.search(tg.document_pattern(path, text), self.DOCUMENT) is not None

    def test_a_leaf_under_its_own_table_matches(self):
        self.assertTrue(self.matches("csp.cloudflare.turnstile", "false"))
        self.assertTrue(self.matches("csp.hash_inline_scripts", "true"))
        self.assertTrue(self.matches("isr.ttl_secs", "0"))

    def test_the_first_leaf_under_a_table_matches(self):
        self.assertTrue(self.matches("csp.cloudflare.script_nonce", "true"))

    def test_a_leaf_under_the_wrong_table_does_not_match(self):
        """The defect a pattern keyed on the leaf name alone would wave through."""
        self.assertFalse(self.matches("isr.turnstile", "false"))
        self.assertFalse(self.matches("csp.turnstile", "false"))

    def test_a_wrong_value_does_not_match(self):
        self.assertFalse(self.matches("csp.cloudflare.turnstile", "true"))

    def test_a_leaf_whose_name_is_a_suffix_of_another_does_not_match(self):
        self.assertFalse(self.matches("isr.secs", "0"))

    def test_a_quoted_value_is_matched_as_the_renderer_writes_it(self):
        self.assertTrue(self.matches("isr.cache_dir", tg.toml_scalar("/tmp/isr")))

    def test_only_shared_metacharacters_are_escaped(self):
        """`re.escape` also escapes punctuation Go's parser is not obliged to accept."""
        self.assertEqual(tg.escape("a-b~c#d e"), "a-b~c#d e")
        self.assertEqual(tg.escape("[a].b"), r"\[a\]\.b")

    def test_a_key_outside_toml_s_bare_alphabet_is_quoted(self):
        self.assertEqual(tg.toml_key("docs/handbook"), '"docs/handbook"')
        self.assertEqual(tg.toml_key("dist_dir"), "dist_dir")

    def test_a_top_level_key_is_anchored_at_the_document(self):
        pattern = tg.document_pattern("level", '"info"')
        self.assertTrue(re.search(pattern, 'level = "info"\n\n[isr]\nttl_secs = 0\n'))
        self.assertFalse(re.search(pattern, '[isr]\nlevel = "info"\n'))


class TestPlanning(unittest.TestCase):
    KEYS = [
        key("isr.ttl_secs", text_form="integer", constraint={"type": "integer", "minimum": 0},
            default_value=0),
        key("assets.dist_dir", default_value="public"),
        key("database.password", secret=True),
    ]

    def test_cases_and_skips_are_sorted_by_contract_path(self):
        plan = tg.plan(self.KEYS, [])
        self.assertEqual([case.path for case in plan.cases], ["assets.dist_dir", "isr.ttl_secs"])
        self.assertEqual([entry.path for entry in plan.skipped], ["database.password"])

    def test_a_case_writes_its_key_under_the_raw_configuration_tree(self):
        plan = tg.plan(self.KEYS, [])
        case = next(case for case in plan.cases if case.path == "isr.ttl_secs")
        self.assertEqual(case.set_values[-1], ("config.isr.ttl_secs", tg.DISTINCTIVE_INTEGER))

    def test_the_baseline_is_carried_by_every_case(self):
        baseline = [("config.csp.cloudflare.script_nonce", False)]
        for case in tg.plan(self.KEYS, baseline).cases:
            self.assertIn(baseline[0], case.set_values)

    def test_the_baseline_never_supplies_the_value_the_case_is_probing(self):
        """Otherwise the case passes whether or not the chart delivered anything."""
        probed = tg.values_path("isr.ttl_secs")
        plan = tg.plan(self.KEYS, [(probed, 99)])
        case = next(case for case in plan.cases if case.path == "isr.ttl_secs")
        self.assertEqual([name for name, _ in case.set_values], [probed])
        self.assertEqual(case.set_values[0][1], tg.DISTINCTIVE_INTEGER)


class TestSuiteRendering(unittest.TestCase):
    TARGET = tg.Target(
        chart="portfolio",
        name="server",
        kind="ConfigMap",
        selector={"app.kubernetes.io/instance": "portfolio"},
        key="config.toml",
        declaration="charts/portfolio/config-contract.yaml",
        contracts=("charts/portfolio/contracts/server.json",),
    )

    def render(self, keys, baseline=(), reason=None) -> str:
        return tg.render_suite(self.TARGET, tg.plan(keys, baseline), baseline, reason)

    def test_the_suite_is_valid_yaml_shaped_like_a_helm_unittest_suite(self):
        suite = yaml.safe_load(self.render(TestPlanning.KEYS))
        self.assertEqual(suite["release"]["name"], "portfolio")
        self.assertEqual([test["it"] for test in suite["tests"]][0].split()[0], "renders")
        self.assertEqual(len(suite["tests"]), 3)

    def test_the_pattern_survives_yaml_with_its_backslashes_intact(self):
        suite = yaml.safe_load(self.render(TestPlanning.KEYS))
        probe = next(test for test in suite["tests"] if "isr.ttl_secs" in test["it"])
        self.assertEqual(
            probe["asserts"][0]["matchRegex"]["pattern"],
            tg.document_pattern("isr.ttl_secs", str(tg.DISTINCTIVE_INTEGER)),
        )

    def test_the_header_says_the_file_is_generated_and_names_its_sources(self):
        rendered = self.render(TestPlanning.KEYS)
        self.assertTrue(rendered.startswith(f"# {tg.BANNER}"))
        self.assertIn("charts/portfolio/contracts/server.json", rendered)
        self.assertIn("charts/portfolio/config-contract.yaml", rendered)

    def test_every_skipped_key_is_named_in_the_file_with_its_reason(self):
        rendered = self.render(TestPlanning.KEYS)
        self.assertIn("database.password:", rendered)
        self.assertIn("credential", rendered)

    def test_rendering_is_a_pure_function_of_its_inputs(self):
        """The staleness gate is a comparison of bytes, so a second run has to be identical."""
        first = self.render(TestPlanning.KEYS, [("config.a.b", False)], "because")
        second = self.render(TestPlanning.KEYS, [("config.a.b", False)], "because")
        self.assertEqual(first, second)

    def test_nothing_that_churns_on_its_own_is_written_into_the_file(self):
        """A digest, a timestamp or an appVersion would put a diff here on every image bump."""
        rendered = self.render(TestPlanning.KEYS)
        self.assertNotIn("sha256", rendered)
        self.assertNotRegex(rendered, r"\d{4}-\d{2}-\d{2}T")


class TestEnrolment(unittest.TestCase):
    """The file that enrols a chart, and the mistakes it must not be read past."""

    def enrolment(self, body: str):
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory)
            (chart / generator.ENROLMENT).write_text(body, encoding="utf-8")
            return generator.load_enrolment(chart)

    def test_a_chart_without_the_file_is_not_enrolled(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(generator.load_enrolment(Path(directory)))

    def test_an_enrolment_with_no_baseline_is_accepted(self):
        self.assertEqual(self.enrolment("documents: []"), {})

    def test_a_baseline_is_read_as_sorted_pairs(self):
        baselines = self.enrolment(
            "documents:\n"
            "  - name: server\n"
            "    baseline:\n"
            "      config.b: 1\n"
            "      config.a: false\n"
            "    reason: because\n"
        )
        self.assertEqual(baselines["server"].values, [("config.a", False), ("config.b", 1)])

    def test_a_baseline_without_a_reason_is_refused(self):
        with self.assertRaises(DeclarationError) as raised:
            self.enrolment(
                "documents:\n  - name: server\n    baseline:\n      config.a: false\n"
            )
        self.assertIn("reason", str(raised.exception))

    def test_a_baseline_outside_the_configuration_tree_is_refused(self):
        with self.assertRaises(DeclarationError):
            self.enrolment(
                "documents:\n  - name: server\n    baseline:\n      image.tag: v1\n"
                "    reason: because\n"
            )

    def test_a_nested_baseline_entry_is_refused(self):
        """A collision with a probe is a string comparison, which a tree would hide."""
        with self.assertRaises(DeclarationError):
            self.enrolment(
                "documents:\n  - name: server\n    baseline:\n      config.csp: {a: 1}\n"
                "    reason: because\n"
            )

    def test_an_unknown_key_is_refused_rather_than_ignored(self):
        with self.assertRaises(DeclarationError):
            self.enrolment("documents: []\nreason: stray")

    def test_a_document_declared_twice_is_refused(self):
        with self.assertRaises(DeclarationError):
            self.enrolment("documents:\n  - name: server\n  - name: server\n")


class TestOrphanedSuites(unittest.TestCase):
    """A suite left behind keeps asserting keys nothing reads, which is the drift to catch."""

    def sweep(self, wanted_names: list[str], present: list[str]) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory)
            (chart / "tests").mkdir()
            for name in present:
                (chart / "tests" / name).write_text("suite: x\n", encoding="utf-8")
            wanted = {chart / "tests" / name: "" for name in wanted_names}
            return [path.name for path in generator.orphans(chart, wanted)]

    def test_a_suite_whose_document_is_gone_is_reported(self):
        self.assertEqual(
            self.sweep(
                ["contract_roundtrip_api_test.yaml"],
                ["contract_roundtrip_api_test.yaml", "contract_roundtrip_worker_test.yaml"],
            ),
            ["contract_roundtrip_worker_test.yaml"],
        )

    def test_un_enrolling_a_chart_orphans_every_suite_it_had(self):
        self.assertEqual(
            self.sweep([], ["contract_roundtrip_api_test.yaml"]),
            ["contract_roundtrip_api_test.yaml"],
        )

    def test_a_hand_written_suite_is_never_touched(self):
        self.assertEqual(self.sweep([], ["configmap_test.yaml"]), [])


class TestGeneratedTree(unittest.TestCase):
    """What is actually committed, because the suite is only useful if it is in step."""

    CHARTS = SCRIPTS.parents[1] / "charts"

    def test_the_committed_suites_match_what_the_generator_produces(self):
        # The walk narrates which charts it skipped, which is useful from a recipe and noise in a
        # test report.
        with contextlib.redirect_stdout(io.StringIO()):
            suites, stale = generator.collect(self.CHARTS, "")
        self.assertEqual(stale, [])
        self.assertTrue(suites, "no chart is enrolled, so nothing here is being proven")
        for path, text in suites.items():
            with self.subTest(suite=path.name):
                self.assertTrue(path.is_file(), f"{path} has never been generated")
                self.assertEqual(path.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
