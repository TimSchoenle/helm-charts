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

`SchemaBlocks` and `Descriptions` are a fifth kind of case, and the reason they exist is the same
for all three defects they cover: none of them is reachable from the scaffold's own run.
`values.schema.json` is generated from `values.yaml` by `just schema`, and at the moment the
scaffold finishes there is no schema for anything to disagree with — so a block typed `object`
above an array default, a block helm-schema refuses outright, and a grouping key with no `# --`
line all left the generator looking correct and were found on a chart. They are asserted against
the two readers that decide it: `blocks` parses the `@schema` comments the way helm-schema does,
and the description assertions call `check-values-docs` rather than re-implementing its rule.
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
from entry import load  # noqa: E402

TEMPLATES = SCRIPTS.parent / "templates" / "chart"

# The gate the generated values file has to satisfy, run over the generated text rather than
# re-implemented here: a second reader of `# --` placement would agree with this scaffold and
# disagree with the gate, which is the failure the assertion exists to catch.
values_docs = load("check_values_docs", "check-values-docs.py")


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


def blocks(text: str) -> list[tuple[str, dict[str, Any], Any]]:
    """Every `@schema` block in a values file, with the key it describes and that key's default.

    Read the way helm-schema reads them — the text between the two delimiters is YAML, and the
    `# @config` marker inside it is a YAML comment — so this sees exactly what `just schema` will
    be handed, which is what makes an assertion about one worth anything.
    """
    lines = text.splitlines()
    found: list[tuple[str, dict[str, Any], Any]] = []
    number = 0
    while number < len(lines):
        if lines[number].strip() != "# @schema":
            number += 1
            continue

        indent = len(lines[number]) - len(lines[number].lstrip())
        body: list[str] = []
        number += 1
        while number < len(lines) and lines[number].strip() != "# @schema":
            body.append(lines[number][indent:].removeprefix("#").removeprefix(" "))
            number += 1

        # Past the closing delimiter, past the description, and onto the key the run belongs to.
        number += 1
        while number < len(lines) and lines[number].lstrip().startswith("#"):
            number += 1

        line = lines[number].strip() if number < len(lines) else ""
        name, _, written = line.partition(":")
        default = yaml.safe_load(written) if written.strip() else None
        found.append((name, yaml.safe_load("\n".join(body)) or {}, default))
    return found


def _json_type(value: Any) -> str:
    """The JSON Schema type name for one rendered default."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


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


class SchemaBlocks(unittest.TestCase):
    """What the `@schema` block above a generated value says.

    Three defects lived here at once, all found scaffolding `discord-alertmanager` and all worked
    around in that chart by hand rather than fixed. Two of them are below; the third is
    `Descriptions`. None was caught by the scaffold's own run, because `values.schema.json` does
    not exist yet at that point and every gate that would have noticed reads one.
    """

    def test_a_structured_array_keeps_the_constraint_the_contract_published(self):
        """`type: [object]` beside an array default is a chart that rejects its own values."""
        entry = key(
            "a.hosts", text_form="structured", default_value=["one"],
            constraint={"type": "array", "items": {"type": "string"}},
        )
        block = self._block(entry, "hosts")
        self.assertEqual(block["type"], ["array", "null"])
        self.assertEqual(block["items"], {"type": "string"})
        self.assertNotIn("additionalProperties", block)

    def test_a_described_element_is_written_without_enrolling_it(self):
        """A new chart's deepest schemas are owned from the moment they are written.

        No marker says so, and none is needed: the `# @config` marker inside the block is what
        makes it generated, so the interlock holding it to the contract is there from the first
        `just config-shapes` without a second line asserting it.
        """
        entry = key(
            "a.hosts", text_form="structured", default_value=["one"],
            constraint={"type": "array", "items": {"type": "string"}},
        )
        surface = sc.from_contract(union_of(entry))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        self.assertIn("# # @config structured a.hosts optional", text)
        self.assertNotIn("@config-shape", text)

    def test_an_undescribed_container_is_written_without_a_marker_either(self):
        entry = key("a.peers", text_form="structured", constraint={"type": "object"})
        surface = sc.from_contract(union_of(entry))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        self.assertNotIn("@config-shape", text)

    def test_a_structured_arrays_default_is_a_value_its_own_block_accepts(self):
        entry = key(
            "a.hosts", text_form="structured", default_value=["one"],
            constraint={"type": "array", "items": {"type": "string"}},
        )
        surface = sc.from_contract(union_of(entry))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        for name, block, default in blocks(text):
            if name == "hosts":
                self.assertIn(_json_type(default), block["type"])
                break
        else:  # pragma: no cover - the loop above always finds it
            self.fail("the generated values file carries no block for `hosts`")

    def test_a_required_structured_array_defaults_to_an_array_rather_than_a_table(self):
        entry = key(
            "a.hosts", text_form="structured", required=True,
            constraint={"type": "array", "items": {"type": "string"}},
        )
        self.assertEqual(sc.default_for(entry), [])

    def test_a_structured_object_is_still_opened_rather_than_described(self):
        """`internal.peers` is a `BTreeMap<String, PeerConfig>` and its constraint says only that.

        The contract states the value is a table and nothing about what is in it, so the block
        accepts any table. Inventing properties here would refuse a peer nobody declared.
        """
        entry = key("a.peers", text_form="structured", constraint={"type": "object"})
        block = self._block(entry, "peers")
        self.assertEqual(block["type"], ["object", "null"])
        self.assertIs(block["additionalProperties"], True)

    def test_a_structured_key_with_no_declared_type_is_opened_too(self):
        entry = key("a.peers", text_form="structured", constraint={})
        block = self._block(entry, "peers")
        self.assertEqual(block["type"], ["object", "null"])
        self.assertIs(block["additionalProperties"], True)

    def test_an_enum_is_the_whole_block(self):
        """helm-schema refuses one carrying both, fatally, and writes no schema for any chart.

        `level=fatal msg="Error while validating jsonschema of key backend: cannot use both
        'enum' and 'type' in the same schema"` — measured against helm-schema 0.18.1.
        """
        entry = key(
            "a.backend", text_form="choice", default_value="sqlite",
            constraint={"type": "string", "enum": ["sqlite", "postgres"]},
        )
        block = self._block(entry, "backend")
        self.assertNotIn("type", block)
        self.assertEqual(block["enum"], ["sqlite", "postgres", None])

    def test_a_required_enum_gains_no_null_member(self):
        entry = key(
            "a.backend", text_form="choice", required=True, default_value="sqlite",
            constraint={"type": "string", "enum": ["sqlite", "postgres"]},
        )
        self.assertEqual(self._block(entry, "backend")["enum"], ["sqlite", "postgres"])

    def test_no_generated_block_carries_both_enum_and_type(self):
        """The property, over a surface holding one of every shape rather than over one key."""
        surface = sc.from_contract(
            union_of(
                key("a.text"),
                key("a.port", text_form="integer", constraint={"type": "integer", "minimum": 1}),
                key("a.backend", text_form="choice",
                    constraint={"type": "string", "enum": ["sqlite", "postgres"]}),
                key("a.hosts", text_form="structured",
                    constraint={"type": "array", "items": {"type": "string"}}),
                key("a.peers", text_form="structured", constraint={"type": "object"}),
                key("a.token", secret=True),
            )
        )
        chassis = (TEMPLATES / "values.chassis.yaml").read_text(encoding="utf-8")
        text = sc.render_values("app", surface, "org/app", "v1", chassis, "config.toml")
        for name, block, _ in blocks(text):
            with self.subTest(value=name):
                self.assertFalse("enum" in block and "type" in block, block)

    def test_a_bounded_scalar_still_copies_its_bounds(self):
        entry = key(
            "a.port", text_form="integer",
            constraint={"type": "integer", "minimum": 1, "maximum": 65535},
        )
        block = self._block(entry, "port")
        self.assertEqual(block["type"], ["integer", "null"])
        self.assertEqual((block["minimum"], block["maximum"]), (1, 65535))

    def _block(self, entry: dict[str, Any], leaf: str) -> dict[str, Any]:
        """The parsed `@schema` block the scaffold writes above one key's value."""
        surface = sc.from_contract(union_of(entry))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        for name, block, _ in blocks(text):
            if name == leaf:
                return block
        self.fail(f"the generated values file carries no block for `{leaf}`")


class Descriptions(unittest.TestCase):
    """Every generated value carries the `# --` line `just check-values-docs` demands.

    The third defect, and the one a scaffold could not see: the gate reads `values.yaml` alone,
    but nothing ran it here, so a freshly scaffolded chart failed it on every grouping block —
    sixteen of them for `discord-alertmanager`. `new-chart.py` now runs it before printing that
    the chart passes `just check`.
    """

    def test_the_contracted_form_leaves_nothing_undocumented(self):
        surface = sc.from_contract(
            union_of(
                key("alertmanager.endpoints", text_form="structured",
                    constraint={"type": "array", "items": {"type": "string"}}),
                key("discord.capabilities.view", text_form="structured",
                    constraint={"type": "array", "items": {"type": "string"}}),
                key("storage.backend", text_form="choice",
                    constraint={"type": "string", "enum": ["sqlite", "postgres"]}),
                key("routes", text_form="structured", constraint={"type": "array"}),
                key("discord.token", secret=True),
            )
        )
        self.assertEqual(self._undocumented(surface), [])

    def test_the_plain_form_leaves_nothing_undocumented(self):
        self.assertEqual(self._undocumented(sc.plain()), [])

    def test_a_grouping_block_names_the_prefix_it_holds(self):
        surface = sc.from_contract(union_of(key("discord.capabilities.view")))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        self.assertIn("# -- TODO: what the `discord` settings have in common", text)
        self.assertIn("# -- TODO: what the `discord.capabilities` settings have in common", text)

    def test_the_prefix_is_the_contract_spelling_not_the_values_one(self):
        """`values_path_for` camel-cases each segment, and the description names the key."""
        surface = sc.from_contract(union_of(key("rate_limit.per_ip.burst")))
        text = sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        self.assertIn("`rate_limit.per_ip`", text)
        self.assertNotIn("`rateLimit.perIp`", text)

    def test_every_placeholder_is_reported(self):
        plan = sc.plan_keys(union_of(key("a.b.c"), key("d.token", secret=True), key("e")))
        self.assertEqual(sc.undescribed(plan), ["a", "a.b", "d"])

    def test_a_surface_with_no_nesting_reports_none(self):
        self.assertEqual(sc.undescribed(sc.plan_keys(union_of(key("a")))), [])

    def test_the_chassis_blocks_carry_a_sentence_rather_than_a_placeholder(self):
        """`image` and `configMount` are the same in every chart, so neither is a TODO."""
        surface = sc.from_contract(union_of(key("a.b")))
        described = self._described(
            sc.render_values("app", surface, "org/app", "v1", "", "config.toml")
        )
        for name in ("image", "configMount"):
            self.assertIn(name, described)
            self.assertNotIn("TODO", described[name], name)

    @staticmethod
    def _undocumented(surface: sc.Surface) -> list[str]:
        """The values `just check-values-docs` would report, asked of the gate rather than of a
        second reader written here — which is the only way this assertion can be wrong in the
        contributor's favour rather than in its own.
        """
        chassis = (TEMPLATES / "values.chassis.yaml").read_text(encoding="utf-8")
        text = sc.render_values("app", surface, "org/app", "v1", chassis, "config.toml")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.yaml"
            path.write_text(text, encoding="utf-8", newline="\n")
            return [found.path for found in values_docs.undocumented(path)]

    @staticmethod
    def _described(text: str) -> dict[str, str]:
        """Each top-level key's `# --` line, keyed by the key it sits above."""
        lines = text.splitlines()
        found: dict[str, str] = {}
        for number, line in enumerate(lines):
            if line.startswith("#") or not line.endswith(":"):
                continue
            above = number - 1
            while above >= 0 and lines[above].startswith("#"):
                if lines[above].startswith("# -- "):
                    found[line[:-1]] = lines[above]
                    break
                above -= 1
        return found


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
