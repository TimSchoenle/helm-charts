#!/usr/bin/env python3
r"""The chart scaffold, over hand-built contracts rather than over a registry.

Every other generator in this repository writes a file that is then held against a chart. This one
writes the chart, so the usual safety net is inverted: there is nothing to compare its output
against except the gates, and by the time a gate sees it the wrong file is already committed. The
cases below are therefore about the rules rather than about the bytes.

Four of them are rules a plausible implementation gets wrong, and each was measured on a real
contract rather than imagined:

**A credential carries no marker.** `check-config-bindings` refuses a key that is both bound by a
`# @config` marker and written off in `unbound`, and a file-delivered credential is written off —
so emitting both makes every chart with a credential fail the gate on its first run. The first
draft did exactly that; `netcup-offer-bot`'s contract is what showed it.

**Settings and credentials share one values tree.** `telemetry.log_level` is ordinary and
`telemetry.sentry_dsn` is a credential, so rendering them as two sections emits `telemetry:` twice
and YAML keeps only the second — silently dropping every value in the first. The values file
parses, the chart renders, and one setting has vanished.

**A required key with no published default still needs a legal value.** Writing a typed zero fails
`just check-config` wherever the constraint has a lower bound, on the chart's first run, for a
reason that reads like a bug in the gate.

**An optional key is absent rather than empty.** To the loader an empty value is a *supplied*
value, so an unset optional setting has to be omitted from the document entirely — which is what
the `with` wrapper in the derived helper is for, and what its absence would silently undo.

The plain form is asserted for what it does *not* write. A chart whose image publishes no contract
must not carry `configMount`, `config` or a ConfigMap: each describes a loader nothing knows this
image has, and a value an operator can set that nothing honours is worse than an absent one.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_bindings as cb  # noqa: E402
import config_contract as cc  # noqa: E402
import config_scaffold as sc  # noqa: E402

TEMPLATES = SCRIPTS.parent / "templates" / "chart"


def key(path: str, **overrides: Any) -> dict[str, Any]:
    """One contract key, with the fields every consumer reads and nothing else."""
    spelt = path.replace(".", "__")
    entry: dict[str, Any] = {
        "path": path,
        "env": f"APP_{spelt.upper()}",
        "env_file": f"APP_{spelt.upper()}_FILE",
        "secrets_file": spelt,
        "docs": f"Documentation for {path}.",
        "constraint": {"type": "string"},
        "text_form": "text",
        "default": None,
        "default_value": None,
        "required": False,
        "secret": False,
        "reserved": False,
    }
    entry.update(overrides)
    return entry


def union_of(*keys: dict[str, Any]) -> cc.Union:
    """A `Union` carrying these keys and the dialect every contract here declares."""
    return cc.Union(
        sources=["test/app.json"],
        dialect={"prefix": "APP_", "nesting_separator": "__", "indirection_suffix": "_FILE"},
        keys={entry["path"]: entry for entry in keys},
    )


def values_of(surface: sc.Surface, chart: str = "app") -> dict[str, Any]:
    """The generated `values.yaml`, parsed. Proves it is YAML as well as what it says."""
    text = sc.render_values(chart, surface, "org/app", "v1.0.0", "", "config.toml")
    return yaml.safe_load(text) or {}


class Naming(unittest.TestCase):
    def test_camel_case_per_segment(self):
        self.assertEqual(sc.values_path_for("feed.check_interval_secs"), "feed.checkIntervalSecs")
        self.assertEqual(sc.values_path_for("discord.webhook_url"), "discord.webhookUrl")

    def test_a_segment_without_an_underscore_is_untouched(self):
        self.assertEqual(sc.values_path_for("metrics.ip"), "metrics.ip")

    def test_the_env_prefix_drops_the_separator_the_partial_appends(self):
        # `common.fileConfig.env` appends the separator itself, so passing the contract's own
        # spelling through would produce `APP__CONFIG`.
        self.assertEqual(sc.env_prefix(union_of(key("a.b"))), "APP")

    def test_a_name_helm_would_refuse_is_refused_here(self):
        for name in ("Foo", "foo_bar", "-foo", "foo-", ""):
            with self.subTest(name=name), self.assertRaises(sc.ScaffoldError):
                sc.check_chart_name(name)
        sc.check_chart_name("foo-bar-1")


class Placement(unittest.TestCase):
    def test_a_file_supplyable_credential_becomes_a_secret_file(self):
        plan = sc.plan_keys(union_of(key("a.token", secret=True)))
        self.assertEqual([item.path for item in plan.secrets], ["a.token"])
        self.assertEqual(plan.projected, [])

    def test_a_credential_no_file_can_supply_is_written_off(self):
        # `file_supplyable` is false for everything but `text`, so this credential has no channel
        # but the document or the environment — and both are refused for a credential.
        plan = sc.plan_keys(
            union_of(key("a.port", secret=True, text_form="integer",
                         constraint={"type": "integer"}))
        )
        self.assertEqual([item.path for item in plan.written_off], ["a.port"])
        self.assertEqual(plan.secrets, [])

    def test_a_reserved_key_is_written_off(self):
        plan = sc.plan_keys(union_of(key("a.b", reserved=True)))
        self.assertEqual([item.path for item in plan.written_off], ["a.b"])

    def test_every_written_off_key_carries_a_reason(self):
        plan = sc.plan_keys(
            union_of(
                key("a.b", reserved=True),
                key("c.d", secret=True, text_form="integer", constraint={"type": "integer"}),
            )
        )
        for item in plan.written_off:
            self.assertTrue(item.reason.strip(), item.path)


class Markers(unittest.TestCase):
    def test_a_projected_key_carries_a_marker_the_parser_accepts(self):
        surface = sc.from_contract(union_of(key("a.b")))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        markers = self._markers(text)
        self.assertEqual(len(markers), 1)
        cls, documents, target, optional, condition = cb.parse_marker(markers[0])
        self.assertEqual((cls, target, optional), (cb.PROJECTION, "a.b", True))
        self.assertIsNone(documents)
        self.assertIsNone(condition)

    def test_a_required_key_is_not_marked_optional(self):
        surface = sc.from_contract(union_of(key("a.b", required=True)))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        _, _, _, optional, _ = cb.parse_marker(self._markers(text)[0])
        self.assertFalse(optional)

    def test_a_structured_key_is_marked_structured(self):
        surface = sc.from_contract(
            sc_union := union_of(key("a.b", text_form="structured", constraint={"type": "array"}))
        )
        del sc_union
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        cls, _, _, _, _ = cb.parse_marker(self._markers(text)[0])
        self.assertEqual(cls, cb.STRUCTURED)

    def test_a_credential_carries_no_marker(self):
        """`check-config-bindings` refuses a key that is both marked and written off.

        The rule this scaffold got wrong first. A file-delivered credential goes into `unbound`,
        so a marker on it makes the gate fail on the chart's first run — with a message about the
        two disagreeing, on a chart nobody wrote by hand.
        """
        surface = sc.from_contract(union_of(key("a.token", secret=True)))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        self.assertEqual(self._markers(text), [])
        # ...and the value still exists, because the Secret is rendered from it.
        self.assertIn("token:", text)

    @staticmethod
    def _markers(text: str) -> list[str]:
        """Every marker body in a values file, read the way `config_bindings` reads them."""
        found = []
        for line in text.splitlines():
            _, comment = cb.split_comment(line)
            inner = cb.schema_comment(comment)
            if cb.is_marker(inner):
                found.append(inner[len(cb.MARKER):].strip())
        return found


class ValuesTree(unittest.TestCase):
    def test_a_setting_and_a_credential_under_one_prefix_share_one_block(self):
        """Two sections would emit the prefix twice and YAML would keep only the second."""
        surface = sc.from_contract(
            union_of(key("telemetry.log_level"), key("telemetry.sentry_dsn", secret=True))
        )
        values = values_of(surface)
        self.assertEqual(
            sorted(values["telemetry"]), ["logLevel", "sentryDsn"],
            "one of the two was dropped by a duplicate top-level key",
        )

    def test_a_key_whose_parent_is_another_key_is_refused(self):
        # `a` cannot be both a scalar value and the map `a.b` lives in. Refused rather than
        # rendered as whichever YAML happens to win.
        surface = sc.from_contract(union_of(key("a"), key("a.b")))
        with self.assertRaises(sc.ScaffoldError):
            values_of(surface)

    def test_the_image_default_is_written_through(self):
        surface = sc.from_contract(key_union := union_of(key("a.b", default_value="chosen")))
        del key_union
        self.assertEqual(values_of(surface)["a"]["b"], "chosen")

    def test_an_optional_key_with_no_default_is_null(self):
        self.assertIsNone(values_of(sc.from_contract(union_of(key("a.b"))))["a"]["b"])

    def test_a_required_key_with_no_default_gets_a_value_its_constraint_accepts(self):
        """A typed zero would fail `check-config` on the chart's first run."""
        entry = key(
            "a.port", required=True, text_form="integer",
            constraint={"type": "integer", "minimum": 8000, "maximum": 9000},
        )
        value = values_of(sc.from_contract(union_of(entry)))["a"]["port"]
        self.assertIsNone(cc.assert_value(entry["constraint"], value))

    def test_every_invented_value_is_reported(self):
        plan = sc.plan_keys(
            union_of(key("a.b", required=True), key("c.d", required=True, default_value="known"))
        )
        self.assertEqual([item.path for item in sc.invented(plan)], ["a.b"])

    def test_the_tag_is_quoted_so_a_numeric_one_stays_a_string(self):
        text = sc.render_values("app", sc.plain(), "library/redis", "8.0", "", "config.toml")
        self.assertEqual(yaml.safe_load(text)["image"]["tag"], "8.0")

    def test_the_chassis_is_appended_verbatim(self):
        chassis = (TEMPLATES / "values.chassis.yaml").read_text(encoding="utf-8")
        text = sc.render_values("app", sc.plain(), "org/app", "v1", chassis, "config.toml")
        values = yaml.safe_load(text)
        for name in ("podSecurityContextPreset", "serviceAccount", "networkPolicy", "resources"):
            self.assertIn(name, values)


class DerivedHelper(unittest.TestCase):
    def test_an_optional_key_is_wrapped_so_an_unset_value_is_absent(self):
        """To the loader an empty value is a supplied value, not an unset one."""
        text = sc.render_helpers("app", sc.from_contract(union_of(key("a.b"))))
        self.assertIn("{{- with .Values.a.b }}", text)

    def test_a_required_key_is_written_unconditionally(self):
        text = sc.render_helpers("app", sc.from_contract(union_of(key("a.b", required=True))))
        self.assertIn("b: {{ .Values.a.b | quote }}", text)
        self.assertNotIn("with .Values.a.b", text)

    def test_a_numeric_key_is_not_quoted(self):
        text = sc.render_helpers(
            "app",
            sc.from_contract(
                union_of(key("a.port", required=True, text_form="integer",
                             constraint={"type": "integer"}))
            ),
        )
        self.assertIn("port: {{ .Values.a.port }}", text)

    def test_every_credential_reaches_the_secret_under_its_published_file_name(self):
        text = sc.render_helpers(
            "app", sc.from_contract(union_of(key("a.token", secret=True)))
        )
        self.assertIn("a__token: {{ .Values.a.token | quote }}", text)

    def test_a_required_credential_is_guarded_against_the_projected_key_list(self):
        # Against the list rather than the value, so an `existingSecret` counts: the chart cannot
        # see inside one, and a credential it cannot see is not a credential that is missing.
        text = sc.render_helpers(
            "app", sc.from_contract(union_of(key("a.token", secret=True, required=True)))
        )
        self.assertIn('$projected := include "app.secretKeys"', text)
        self.assertIn('has "a__token" $projected', text)

    def test_the_template_braces_are_balanced(self):
        text = sc.render_helpers(
            "app",
            sc.from_contract(union_of(key("a.b"), key("c.token", secret=True, required=True))),
        )
        self.assertEqual(text.count("{{"), text.count("}}"), text)


class Declaration(unittest.TestCase):
    def test_the_declaration_is_one_the_loader_accepts(self):
        plan = sc.plan_keys(union_of(key("a.b"), key("c.token", secret=True)))
        text = sc.render_declaration("app", "app", "contracts/app.json", "config.toml", plan)
        with tempfile.TemporaryDirectory() as directory:
            chart_dir = Path(directory) / "app"
            chart_dir.mkdir()
            (chart_dir / sc.DECLARATION).write_text(text, encoding="utf-8")
            from config_declaration import load_declaration

            declaration = load_declaration(chart_dir)

        self.assertIsNotNone(declaration)
        self.assertTrue(declaration.bindings)
        self.assertEqual([document.name for document in declaration.documents], ["app"])
        written_off = {path for entry in declaration.unbound for path in entry.keys}
        self.assertEqual(written_off, {"c.token"})

    def test_write_offs_sharing_a_reason_are_one_entry(self):
        plan = sc.plan_keys(
            union_of(key("a.x", reserved=True), key("a.y", reserved=True), key("a.z"))
        )
        groups = sc._write_off_groups(plan)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], ["a.x", "a.y"])


class PlainForm(unittest.TestCase):
    """What a chart gets when its image publishes no contract — and what it must not get."""

    def test_no_configuration_surface_is_asserted(self):
        values = values_of(sc.plain())
        for name in ("config", "configExtraToml", "configMount", "existingSecret"):
            self.assertNotIn(
                name, values, f"{name} describes a loader nothing knows this image has"
            )

    def test_the_image_block_is_still_written(self):
        values = values_of(sc.plain())
        self.assertEqual(values["image"]["repository"], "org/app")

    def test_the_helpers_file_defines_nothing(self):
        text = sc.render_helpers("app", sc.plain())
        self.assertNotIn("{{- define", text)
        self.assertIn("{{/*", text)

    def test_the_fixture_installs_the_defaults(self):
        self.assertEqual(yaml.safe_load(sc.render_ci_values("app", sc.plain())), {})

    def test_the_form_selects_the_template_subtree(self):
        self.assertEqual(sc.plain().form, "plain")
        self.assertEqual(sc.from_contract(union_of(key("a.b"))).form, "contract")


class Enrolment(unittest.TestCase):
    def test_a_chart_with_no_required_credential_needs_no_prerequisite(self):
        plan = sc.plan_keys(union_of(key("a.b"), key("c.token", secret=True)))
        parsed = yaml.safe_load(sc.render_enrolment("app", "app", plan))
        self.assertNotIn("prerequisites", parsed["documents"][0])

    def test_a_required_credential_becomes_a_prerequisite_with_a_reason(self):
        plan = sc.plan_keys(union_of(key("a.token", secret=True, required=True)))
        parsed = yaml.safe_load(sc.render_enrolment("app", "app", plan))
        prerequisites = parsed["documents"][0]["prerequisites"]
        self.assertEqual(list(prerequisites["values"]), ["a.token"])
        self.assertTrue(prerequisites["reason"].strip())

    def test_no_prerequisite_names_a_path_under_the_probe_root(self):
        """A prerequisite is dropped from no case, so one under `config` would supply a probe."""
        plan = sc.plan_keys(union_of(key("a.token", secret=True, required=True)))
        parsed = yaml.safe_load(sc.render_enrolment("app", "app", plan))
        for path in parsed["documents"][0]["prerequisites"]["values"]:
            self.assertIsNone(sc.__dict__.get("prerequisite_conflict") or None)
            self.assertFalse(path == sc.CONFIG_ROOT or path.startswith(sc.CONFIG_ROOT + "."))

    def test_an_invented_credential_can_never_authenticate(self):
        plan = sc.plan_keys(union_of(key("a.token", secret=True, required=True)))
        parsed = yaml.safe_load(sc.render_enrolment("app", "app", plan))
        value = parsed["documents"][0]["prerequisites"]["values"]["a.token"]
        self.assertTrue(value.endswith(".invalid"), value)


if __name__ == "__main__":
    unittest.main()
