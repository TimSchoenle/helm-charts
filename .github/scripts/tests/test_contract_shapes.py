#!/usr/bin/env python3
"""Derived `@schema` blocks: the vocabulary, the renderer, the markers and the two gates.

Every case here has a plausible-looking wrong implementation that a run over this repository would
not expose, because no image publishes a `schema_version: 2` contract yet — which is the whole
problem with preparing for a format before it arrives. The fixture is
`.github/testdata/contracts/deep.json`, and it is the only place the nested vocabulary is
exercised.

`config_shapes` is imported rather than loaded by path, unlike every other entry point tested
here: the writer, the gate and the rules they apply are one module, so that `config_scaffold` can
import the rules without the pair of near-identical file names `entry.py` complains about.

Five of them are worth naming, because getting any of them wrong makes the generator confidently
write a schema that is not what the image published:

  the open/closed flag  `additionalProperties` is `true`/`false` in a hand-written block and a
                        *schema* in a version-2 constraint. Every reader has to branch on the
                        type, and the union dropped the field outright until this landed —
                        silently discarding the one fact it exists to preserve

  the enum placement    helm-schema refuses `enum` beside `type` at the top level of a block and
                        accepts it at every level below, measured against 0.18.1. Generate the
                        top level like the nested one and `just schema` exits fatally, leaving
                        every chart's values.schema.json unwritten

  the array structured  a `structured` key whose constraint names an array is not the open-map
                        case. Nine keys in `discord-alertmanager` were written with an object
                        schema and a list default before that was separated

  the superseded copy   a hand transcription stops being maintainable the moment the producer
                        publishes the same struct, and nothing about the chart changes to say so

  the stale exception   an exception naming a field the contract dropped keeps a schema the
                        contract no longer describes, which is the drift the whole marker exists
                        to prevent
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_bindings as cb  # noqa: E402
import config_contract as cc  # noqa: E402
import config_shapes as cs  # noqa: E402

FIXTURES = SCRIPTS.parent / "testdata" / "contracts"

DIGEST = "sha256:" + "1" * 64

def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def constraint_of(contract: dict, path: str) -> dict:
    return next(key["constraint"] for key in contract["schema"]["keys"] if key["path"] == path)


# --------------------------------------------------------------------------------------------
# The contract vocabulary
# --------------------------------------------------------------------------------------------


class TestVocabulary(unittest.TestCase):
    def test_a_document_above_the_implemented_version_is_refused(self):
        contract = fixture("deep")
        contract["schema"]["schema_version"] = cc.SCHEMA_VERSION + 1
        with self.assertRaises(cc.ContractError) as raised:
            cc.check_envelope(contract, "deep.json")
        self.assertIn("schema_version", str(raised.exception))

    def test_a_version_one_document_is_still_read(self):
        # One-sided on purpose: nothing was removed between 1 and 2, so an older document is a
        # newer one that happens to nest nowhere.
        cc.check_envelope(fixture("api"), "api.json")

    def test_a_missing_schema_version_is_refused(self):
        contract = fixture("deep")
        del contract["schema"]["schema_version"]
        with self.assertRaises(cc.ContractError):
            cc.check_envelope(contract, "deep.json")

    def test_the_element_of_a_sequence_and_of_a_map_are_both_found(self):
        deep = fixture("deep")
        self.assertEqual(
            cc.element_schema(constraint_of(deep, "github.repos")), {"type": "string"}
        )
        self.assertEqual(
            cc.element_schema(constraint_of(deep, "webhook.paths"))["type"], "array"
        )

    def test_the_open_flag_is_not_an_element(self):
        # `true` and `false` are the open/closed flag. A reader that took either for an element
        # schema would generate `additionalProperties: {}` and accept anything.
        self.assertIsNone(cc.element_schema({"type": "object", "additionalProperties": True}))
        self.assertIsNone(cc.element_schema({"type": "object", "additionalProperties": False}))
        self.assertFalse(cc.describes_element({"type": "array"}))


class TestAssertValue(unittest.TestCase):
    """The validator, one level down. Every case here passed before it learned to recurse."""

    ELEMENT: ClassVar[dict] = {
        "type": "array",
        "items": {"type": "string", "enum": ["GET", "POST"]},
    }

    def test_an_item_of_the_wrong_type_is_caught(self):
        failure = cc.assert_value(self.ELEMENT, ["GET", 3])
        self.assertIsNotNone(failure)
        self.assertIn("[1]", failure)

    def test_an_item_outside_the_enum_is_caught(self):
        self.assertIsNotNone(cc.assert_value(self.ELEMENT, ["GET", "TRACE"]))

    def test_a_correct_container_passes(self):
        self.assertIsNone(cc.assert_value(self.ELEMENT, ["GET", "POST"]))

    def test_the_array_bounds_are_checked(self):
        schema = {"type": "array", "minItems": 2, "maxItems": 3, "uniqueItems": True}
        self.assertIsNotNone(cc.assert_value(schema, ["a"]))
        self.assertIsNotNone(cc.assert_value(schema, ["a", "b", "c", "d"]))
        self.assertIsNotNone(cc.assert_value(schema, ["a", "a"]))
        self.assertIsNone(cc.assert_value(schema, ["a", "b"]))

    def test_unique_items_compares_tables_by_value(self):
        # A contract's elements are JSON, so a list of tables is a list of dicts and `set()`
        # refuses them. An implementation reaching for a set raises here rather than answering.
        schema = {"type": "array", "uniqueItems": True}
        self.assertIsNotNone(cc.assert_value(schema, [{"a": 1}, {"a": 1}]))
        self.assertIsNone(cc.assert_value(schema, [{"a": 1}, {"a": 2}]))

    def test_a_required_field_of_an_element_is_checked(self):
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        self.assertIsNotNone(cc.assert_value(schema, {}))
        self.assertIsNone(cc.assert_value(schema, {"name": "a"}))

    def test_a_map_value_is_checked_against_the_element(self):
        schema = {"type": "object", "additionalProperties": {"type": "integer"}}
        failure = cc.assert_value(schema, {"a": 1, "b": "two"})
        self.assertIsNotNone(failure)
        self.assertIn(".b", failure)

    def test_a_declared_property_is_not_also_checked_as_an_additional_one(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": {"type": "integer"},
        }
        self.assertIsNone(cc.assert_value(schema, {"name": "a", "count": 1}))

    def test_a_bound_that_does_not_apply_to_the_value_is_not_a_second_failure(self):
        # `type` is what says the value is the wrong shape. Reporting `minItems` about a string
        # as well makes the first line harder to find.
        self.assertIn("expected", cc.assert_value({"type": "array", "minItems": 2}, "text"))

    def test_a_keyword_outside_the_whole_vocabulary_is_still_refused(self):
        with self.assertRaises(cc.ContractError):
            cc.assert_value({"type": "string", "anyOf": []}, "x")

    def test_a_top_level_failure_still_reads_as_it_did(self):
        # Every message this module produced before the recursion landed is unchanged, which is
        # what keeps the gates' output stable for a version-1 contract.
        self.assertEqual(
            cc.assert_value({"type": "integer", "minimum": 5}, 1), "1 is below the minimum 5"
        )


class TestUnionKeepsTheElement(unittest.TestCase):
    def test_a_map_element_survives_the_union(self):
        # `additionalProperties` was dropped outright before this: the open/closed flag and the
        # element schema are the same field, and the union overruled both.
        union = cc.union_contracts([("deep", fixture("deep"))])
        paths = union.json_schema["properties"]["webhook"]["properties"]["paths"]
        self.assertEqual(paths["additionalProperties"]["items"]["enum"], ["GET", "POST"])

    def test_an_enumerated_object_is_still_closed(self):
        union = cc.union_contracts([("deep", fixture("deep"))])
        self.assertIs(union.json_schema["properties"]["webhook"]["additionalProperties"], False)

    def test_an_element_struct_is_left_as_the_producer_published_it(self):
        # `serde` accepts a field nobody declared unless the struct says otherwise, and no derive
        # can see `#[serde(deny_unknown_fields)]` — so the producer decides an element's openness.
        # Closing it here would refuse a document the image accepts.
        union = cc.union_contracts([("deep", fixture("deep"))])
        element = union.json_schema["properties"]["routes"]["items"]
        self.assertIn("properties", element)
        self.assertNotIn("additionalProperties", element)

    def test_two_contracts_describing_one_element_differently_are_refused(self):
        first = fixture("deep")
        second = copy.deepcopy(first)
        second["json_schema"]["properties"]["github"]["properties"]["repos"]["items"] = {
            "type": "integer"
        }
        with self.assertRaises(cc.ContractError):
            cc.union_contracts([("a", first), ("b", second)])


# --------------------------------------------------------------------------------------------
# Building the schema
# --------------------------------------------------------------------------------------------


class TestExpected(unittest.TestCase):
    def test_an_enum_is_the_whole_block(self):
        # helm-schema: "cannot use both 'enum' and 'type' in the same schema", fatal for the whole
        # run. The `null` member is what an optional value needs, since Helm deletes a null during
        # coalescing and the validator is never shown one.
        self.assertEqual(
            cs.expected({"type": "string", "enum": ["a", "b"]}, optional=True, structured=False),
            {"enum": ["a", "b", None]},
        )

    def test_a_required_enum_gains_no_null(self):
        self.assertEqual(
            cs.expected({"type": "string", "enum": ["a"]}, optional=False, structured=False),
            {"enum": ["a"]},
        )

    def test_an_undescribed_map_is_opened(self):
        deep = fixture("deep")
        self.assertEqual(
            cs.expected(constraint_of(deep, "internal.peers"), optional=False, structured=True),
            {"type": "object", "additionalProperties": True},
        )

    def test_a_described_map_takes_the_element_instead_of_the_open_flag(self):
        deep = fixture("deep")
        built = cs.expected(constraint_of(deep, "webhook.paths"), optional=False, structured=True)
        self.assertEqual(built["type"], "object")
        self.assertEqual(built["additionalProperties"]["items"]["enum"], ["GET", "POST"])
        self.assertTrue(built["additionalProperties"]["uniqueItems"])

    def test_a_table_key_that_publishes_its_own_fields_keeps_them(self):
        # The hand-written `Sink` case: the derive splits a table into one key per field, so a
        # single key carrying `properties` is a producer describing a struct in one row. Dropping
        # them would describe a documented struct as an open table.
        built = cs.expected(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            optional=False,
            structured=True,
        )
        self.assertEqual(built["properties"], {"name": {"type": "string"}})
        self.assertEqual(built["required"], ["name"])
        # Explicit, because helm-schema injects `additionalProperties: false` into a top-level
        # block that enumerates properties and says nothing — and the contract's silence is
        # `serde`'s, which accepts a field nobody declared.
        self.assertIs(built["additionalProperties"], True)

    def test_a_structured_array_is_not_treated_as_a_map(self):
        deep = fixture("deep")
        built = cs.expected(constraint_of(deep, "github.repos"), optional=True, structured=True)
        self.assertEqual(built["type"], ["array", "null"])
        self.assertEqual(built["items"], {"type": "string"})
        self.assertNotIn("additionalProperties", built)

    def test_a_struct_element_comes_across_whole(self):
        deep = fixture("deep")
        built = cs.expected(constraint_of(deep, "routes"), optional=True, structured=True)
        element = built["items"]
        self.assertEqual(element["required"], ["name", "guild_id"])
        self.assertEqual(
            element["properties"]["min_severity"]["enum"], ["info", "warning", "critical"]
        )
        self.assertEqual(
            element["properties"]["target"]["properties"]["kind"]["enum"], ["text", "forum"]
        )

    def test_prose_is_dropped_at_every_level(self):
        deep = fixture("deep")
        built = cs.expected(constraint_of(deep, "routes"), optional=True, structured=True)
        self.assertNotIn("description", built["items"])
        self.assertNotIn("description", built["items"]["properties"]["name"])

    def test_a_constraint_naming_no_type_accepts_every_one(self):
        built = cs.expected({}, optional=False, structured=False)
        self.assertEqual(
            built["type"], ["string", "integer", "boolean", "array", "object", "null"]
        )


class TestRender(unittest.TestCase):
    def test_the_type_members_are_schema_vocabulary(self):
        # Bare, except `null`: unquoted it is YAML's null and the schema would then declare no
        # type at all where it meant to declare the null type.
        self.assertEqual(
            cs.render({"type": ["array", "null"]}, ""), ["# type: [array, 'null']"]
        )

    def test_a_field_yaml_reads_as_a_boolean_is_quoted(self):
        lines = cs.render({"type": "object", "properties": {"on": {"type": "boolean"}}}, "")
        self.assertIn('#   "on":', lines)

    def test_a_field_named_like_a_keyword_is_still_a_field(self):
        lines = cs.render({"type": "object", "properties": {"type": {"type": "string"}}}, "")
        self.assertEqual(
            lines, ["# type: object", "# properties:", "#   type:", "#     type: string"]
        )

    def test_fields_keep_the_order_the_producer_declared_them_in(self):
        properties = {"zebra": {"type": "string"}, "alpha": {"type": "string"}}
        lines = cs.render({"type": "object", "properties": properties}, "")
        self.assertLess(lines.index("#   zebra:"), lines.index("#   alpha:"))

    def test_every_rendered_block_parses_back_to_what_it_was_built_from(self):
        deep = fixture("deep")
        for path in ("routes", "webhook.paths", "github.repos", "internal.peers"):
            built = cs.expected(constraint_of(deep, path), optional=True, structured=True)
            text = "\n".join(line[2:] for line in cs.render(built, ""))
            self.assertEqual(yaml.safe_load(text), built, path)

    def test_the_indent_is_carried_through(self):
        self.assertEqual(cs.render({"type": "string"}, "    "), ["    # type: string"])


# --------------------------------------------------------------------------------------------
# The markers
# --------------------------------------------------------------------------------------------


class TestMarkers(unittest.TestCase):
    def parse(self, text: str):
        return cs.parse_markers(text, "fixture")

    def test_the_one_surviving_mode_is_read(self):
        shapes, _ = self.parse(
            "# @config-shape links.buttons handwritten v1.2.3 src/links.rs\n"
        )
        self.assertEqual([shape.mode for shape in shapes], [cs.HANDWRITTEN])
        self.assertEqual(shapes[0].version, "v1.2.3")
        self.assertEqual(shapes[0].source, "src/links.rs")
        self.assertTrue(shapes[0].declared)

    def test_a_mode_nobody_defined_is_refused(self):
        with self.assertRaises(cs.ShapeError):
            self.parse("# @config-shape routes derived\n")

    def test_the_retired_enrolment_marker_is_refused_by_name(self):
        # Generation is the default now, so the marker that used to enrol a value asserts nothing.
        # Refused rather than ignored: accepting it silently would leave every migrated chart
        # carrying a line whose meaning nobody could look up.
        with self.assertRaises(cs.ShapeError) as raised:
            self.parse("# @config-shape routes generated\n")
        self.assertIn("obsolete", str(raised.exception))

    def test_the_older_three_word_form_is_refused_by_name(self):
        # The form two charts carried before the mode was written down. Refused rather than
        # guessed at, so the migration is a message rather than a silent reinterpretation.
        with self.assertRaises(cs.ShapeError) as raised:
            self.parse("# @config-shape routes v0.3.0 src/routes.rs\n")
        self.assertIn("handwritten", str(raised.exception))

    def test_a_departure_without_a_reason_is_refused(self):
        for marker in (cs.EXCEPT_MARKER, cs.NARROW_MARKER):
            with self.assertRaises(cs.ShapeError):
                self.parse(f"# {marker} routes items.properties.guild_id\n")

    def test_a_narrowing_is_read_as_its_own_kind(self):
        _, divergences = self.parse(
            "# @config-shape-narrow sampleRate minimum a fraction the contract does not bound\n"
        )
        self.assertEqual(divergences[0].kind, cs.NARROWING)
        self.assertEqual(divergences[0].sub_path, "minimum")
        self.assertEqual(divergences[0].marker, cs.NARROW_MARKER)

    def test_an_exception_reason_may_be_a_sentence(self):
        _, divergences = self.parse(
            "# @config-shape-except routes items.properties.guild_id snowflake: above 2^53\n"
        )
        self.assertEqual(divergences[0].sub_path, "items.properties.guild_id")
        self.assertEqual(divergences[0].reason, "snowflake: above 2^53")

    def test_the_exception_is_not_read_as_the_shape_marker(self):
        # Its name begins with the other's, so a parser trying `@config-shape` first reads
        # `routes` as the values path and `items.properties.guild_id` as the mode.
        shapes, divergences = self.parse(
            "# @config-shape-except routes items type: a reason\n"
        )
        self.assertEqual(shapes, [])
        self.assertEqual(len(divergences), 1)

    def test_a_marker_named_in_prose_is_not_a_declaration(self):
        shapes, _ = self.parse("# -- Written as `# @config-shape x generated`, above the block.\n")
        self.assertEqual(shapes, [])


class TestDivergences(unittest.TestCase):
    GENERATED: ClassVar[dict] = {
        "type": "array",
        "items": {"type": "object", "properties": {"guild_id": {"type": "integer"}}},
    }
    PRESENT: ClassVar[dict] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"guild_id": {"type": "string", "pattern": "^[0-9]+$"}},
        },
    }

    def divergence(self, sub_path: str, kind: str = cs.OVERRIDE) -> cs.Divergence:
        return cs.Divergence(
            chart="fixture",
            line=1,
            values_path="routes",
            sub_path=sub_path,
            reason="because",
            kind=kind,
        )

    def test_the_named_position_is_taken_from_the_chart(self):
        result, problems = cs.apply_divergences(
            self.GENERATED, self.PRESENT, [self.divergence("items.properties.guild_id")]
        )
        self.assertEqual(problems, [])
        self.assertEqual(result["items"]["properties"]["guild_id"]["type"], "string")

    def test_everything_else_still_comes_from_the_contract(self):
        generated = copy.deepcopy(self.GENERATED)
        generated["items"]["properties"]["added_later"] = {"type": "boolean"}
        result, _ = cs.apply_divergences(
            generated, self.PRESENT, [self.divergence("items.properties.guild_id")]
        )
        self.assertIn("added_later", result["items"]["properties"])

    def test_a_sub_path_the_contract_no_longer_describes_is_refused(self):
        present = copy.deepcopy(self.PRESENT)
        present["items"]["properties"]["gone"] = {"type": "string"}
        _, problems = cs.apply_divergences(
            self.GENERATED, present, [self.divergence("items.properties.gone")]
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("does not contain", problems[0])
        self.assertIn(cs.NARROW_MARKER, problems[0])

    def test_a_narrowing_adds_a_position_the_contract_does_not_describe(self):
        result, problems = cs.apply_divergences(
            {"type": "number"},
            {"type": "number", "minimum": 0, "maximum": 1},
            [
                cs.Divergence("fixture", 1, "sampleRate", "minimum", "a fraction", cs.NARROWING),
                cs.Divergence("fixture", 2, "sampleRate", "maximum", "a fraction", cs.NARROWING),
            ],
        )
        self.assertEqual(problems, [])
        self.assertEqual(result, {"type": "number", "minimum": 0, "maximum": 1})

    def test_a_narrowing_the_contract_has_caught_up_with_is_refused(self):
        _, problems = cs.apply_divergences(
            self.GENERATED,
            self.PRESENT,
            [self.divergence("items.properties.guild_id", cs.NARROWING)],
        )
        self.assertEqual(len(problems), 1)
        self.assertIn(cs.EXCEPT_MARKER, problems[0])

    def test_a_narrowed_enum_takes_the_top_level_from_the_type(self):
        # helm-schema refuses the pair at the top level, so a chart enumerating the members of a
        # key the contract only types has to lose the `type` rather than the enum.
        result, problems = cs.apply_divergences(
            {"type": "string"},
            {"enum": ["trace", "debug"]},
            [cs.Divergence("fixture", 1, "logLevel", "enum", "the five members", cs.NARROWING)],
        )
        self.assertEqual(problems, [])
        self.assertEqual(result, {"enum": ["trace", "debug"]})

    def test_a_sub_path_the_chart_does_not_declare_keeps_nothing(self):
        _, problems = cs.apply_divergences(
            self.GENERATED, {"type": "array"}, [self.divergence("items.properties.guild_id")]
        )
        self.assertIn("nothing to keep", problems[0])

    def test_every_stale_exception_is_reported(self):
        _, problems = cs.apply_divergences(
            self.GENERATED,
            self.PRESENT,
            [self.divergence("items.properties.gone"), self.divergence("items.properties.also")],
        )
        self.assertEqual(len(problems), 2)


# --------------------------------------------------------------------------------------------
# The two gates, over a chart on disk
# --------------------------------------------------------------------------------------------


DECLARATION = """\
bindings: true
documents:
  - name: deep
    source:
      kind: ConfigMap
      selector: { app: fixture }
      key: config.toml
    images:
      - values: image
        contract: contracts/deep.json
"""


class ChartBuilder:
    """A throwaway `charts/` tree holding one chart, for the writer and the gate to walk."""

    def __init__(self, root: Path, name: str = "fixture", app_version: str = "v1"):
        self.root = root
        self.name = name
        self.dir = root / name
        (self.dir / "contracts").mkdir(parents=True)
        (self.dir / "Chart.yaml").write_text(
            f"name: {name}\nversion: 1.0.0\nappVersion: {app_version}\n", encoding="utf-8"
        )
        (self.dir / "config-contract.yaml").write_text(DECLARATION, encoding="utf-8")
        self.contract(fixture("deep"))

    def contract(self, contract: dict, name: str = "deep") -> ChartBuilder:
        (self.dir / "contracts" / f"{name}.json").write_text(
            json.dumps(
                {
                    "source": {
                        "image": "docker.io/example/app",
                        "digest": DIGEST,
                        "sha256": "0" * 64,
                        "fetched": "2026-01-01T00:00:00Z",
                    },
                    "contract": contract,
                }
            ),
            encoding="utf-8",
        )
        return self

    def sibling(self, name: str, version: str) -> ChartBuilder:
        """A second image, reading a second document, published at its own release.

        What a multi-service chart is, and the case a single-document fixture cannot reach:
        `tankovault` ships nine of these under one `appVersion` and they are bumped as each
        upstream release publishes them, so at any moment they hold several different releases.
        The contract is the same one, at a different version, because what is being exercised is
        which release a transcription is held against and not what the keys say.
        """
        contract = fixture("deep")
        contract["app"] = {"name": name, "version": version}
        self.contract(contract, name=name)
        (self.dir / "config-contract.yaml").write_text(
            DECLARATION
            + f"  - name: {name}\n"
            "    source:\n"
            "      kind: ConfigMap\n"
            "      selector: { app: fixture }\n"
            f"      key: {name}.toml\n"
            "    images:\n"
            "      - values: image\n"
            f"        contract: contracts/{name}.json\n",
            encoding="utf-8",
        )
        return self

    def values(self, text: str) -> ChartBuilder:
        (self.dir / "values.yaml").write_text(text, encoding="utf-8")
        return self

    def write(self) -> int:
        return self._run([])

    def check(self) -> int:
        return self._run(["--check"])

    def _run(self, flags: list[str]) -> int:
        # Both halves report to the console, which is the point of them and is noise here: a
        # sixty-case suite that printed every refusal would bury the one line that says which
        # case failed.
        with io.StringIO() as out, io.StringIO() as err:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cs.main(["--charts", str(self.root), *flags])
            self.output = out.getvalue() + err.getvalue()
        return code

    def read(self) -> str:
        return (self.dir / "values.yaml").read_text(encoding="utf-8")


def enrolled(key: str = "github.repos", value: str = "githubRepos") -> str:
    """One bound value whose block is deliberately behind the contract, for the writer to fix.

    Nothing enrols it: the `@config` marker inside the block names the contract key, and that is
    the whole of what makes the block generated. The chart value's own path comes from where the
    block sits in the file, so a test spelling one of the two halves twice would not exercise the
    resolution at all.
    """
    return (
        "image: example\n"
        "# @schema\n"
        f"# # @config structured {key} optional\n"
        "# type: string\n"
        "# @schema\n"
        "# -- Repositories.\n"
        f"{value}: []\n"
    )


class GateCase(unittest.TestCase):
    """Each case gets its own tree, because the writer edits the file it is given."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "charts"
        self.root.mkdir()
        self.chart = ChartBuilder(self.root)


class TestWriter(GateCase):
    def test_a_block_behind_its_contract_is_written(self):
        self.chart.values(enrolled())
        self.assertEqual(self.chart.check(), 1)
        self.assertEqual(self.chart.write(), 0)
        self.assertIn("# items:", self.chart.read())
        self.assertEqual(self.chart.check(), 0)

    def test_writing_twice_changes_nothing(self):
        self.chart.values(enrolled())
        self.chart.write()
        once = self.chart.read()
        self.chart.write()
        self.assertEqual(self.chart.read(), once)

    def test_the_marker_run_inside_the_block_is_left_alone(self):
        self.chart.values(enrolled())
        self.chart.write()
        self.assertIn("# # @config structured github.repos optional", self.chart.read())

    def test_the_description_below_the_block_is_left_alone(self):
        self.chart.values(enrolled())
        self.chart.write()
        self.assertIn("# -- Repositories.", self.chart.read())

    def test_a_map_element_reaches_the_block(self):
        self.chart.values(
            enrolled("webhook.paths", "paths")
        )
        self.chart.write()
        self.assertIn('#     enum: ["GET", "POST"]', self.chart.read())


class TestGate(GateCase):
    def test_an_undescribed_container_is_refused_rather_than_opened(self):
        # A block generated from `{"type": "object"}` alone type-checks nothing, and writing one
        # would delete whatever hand transcription was there. The refusal names the marker that
        # keeps it.
        self.chart.values(
            enrolled("internal.peers", "peers")
        )
        self.assertEqual(self.chart.check(), 1)
        self.assertIn(cs.HANDWRITTEN, self.chart.output)

    def test_a_value_binding_nothing_is_left_alone(self):
        # The `@config` marker is the enrolment, so a value without one is not this module's
        # business — and a gate that walked it would rewrite every chart value in the repository.
        self.chart.values(
            "# @schema\n"
            "# type: string\n"
            "# @schema\n"
            "# -- Orphan.\n"
            "orphan: \"\"\n"
        )
        self.assertEqual(self.chart.check(), 0)
        self.assertEqual(self.chart.write(), 0)
        self.assertIn("# type: string", self.chart.read())

    def test_a_value_bound_only_by_composition_is_left_alone(self):
        # `composed` says the value is *an input* to the key's text, so the key's constraint
        # describes the composition rather than this value: generating from it would type the
        # part as the whole.
        self.chart.values(
            "image: example\n"
            "# @schema\n"
            "# # @config composed github.repos\n"
            "# type: integer\n"
            "# @schema\n"
            "# -- One input.\n"
            "githubRepos: 1\n"
        )
        self.assertEqual(self.chart.check(), 0)

    def test_a_handwritten_marker_naming_a_value_that_does_not_exist_is_refused(self):
        self.chart.values(
            enrolled() + "\n# @config-shape nowhere handwritten v1 src/nowhere.rs\n"
        )
        self.assertEqual(self.chart.check(), 1)

    def test_a_handwritten_marker_away_from_its_block_is_refused(self):
        # Matched to its value by name, so one written at the other end of the file resolves — and
        # then asserts something about a block nobody reading that block can see.
        self.chart.values(
            "# @config-shape githubRepos handwritten v1 src/repos.rs\n"
            "other: 1\n"
            "\n"
            "# @schema\n"
            "# type: string\n"
            "# @schema\n"
            "# -- Repositories.\n"
            "githubRepos: []\n"
        )
        self.assertEqual(self.chart.check(), 1)

    def test_an_exception_with_no_generated_shape_is_refused(self):
        self.chart.values(enrolled() + "\n# @config-shape-except other items a reason\n")
        self.assertEqual(self.chart.check(), 1)

    def test_a_handwritten_marker_at_the_pinned_appversion_passes(self):
        self.chart.values(
            "# @config-shape peers handwritten v1 src/peers.rs\n"
            "\n"
            "# @schema\n"
            "# type: object\n"
            "# additionalProperties: true\n"
            "# @schema\n"
            "# -- Peers.\n"
            "peers: {}\n"
        )
        self.assertEqual(self.chart.check(), 0)

    def test_a_handwritten_marker_behind_the_pinned_appversion_is_refused(self):
        self.chart.values(
            "# @config-shape peers handwritten v0 src/peers.rs\n"
            "\n"
            "# @schema\n"
            "# type: object\n"
            "# @schema\n"
            "# -- Peers.\n"
            "peers: {}\n"
        )
        self.assertEqual(self.chart.check(), 1)

    def test_a_handwritten_shape_the_contract_now_publishes_is_refused(self):
        # The interlock the whole marker pair exists for: the image caught up, and nothing about
        # the chart changed to say the hand copy is no longer the authority.
        self.chart.values(
            "image: example\n"
            "# @config-shape githubRepos handwritten v1 src/repos.rs\n"
            "\n"
            "# @schema\n"
            "# # @config structured github.repos optional\n"
            "# type: [array, 'null']\n"
            "# @schema\n"
            "# -- Repositories.\n"
            "githubRepos: []\n"
        )
        self.assertEqual(self.chart.check(), 1)

    def test_a_declared_divergence_survives_a_check(self):
        self.chart.values(
            "image: example\n"
            "# @config-shape-except githubRepos items snowflakes do not survive a float64\n"
            "\n"
            "# @schema\n"
            "# # @config structured github.repos optional\n"
            "# type: [array, 'null']\n"
            "# items:\n"
            "#   type: string\n"
            "#   pattern: \"^[0-9]+$\"\n"
            "# @schema\n"
            "# -- Repositories.\n"
            "githubRepos: []\n"
        )
        self.assertEqual(self.chart.check(), 0)

    def test_a_chart_with_no_markers_is_not_walked(self):
        self.chart.values("image: example\n")
        self.assertEqual(self.chart.check(), 0)


def transcribed(key: str, version: str, value: str = "peers") -> str:
    """One value whose block is a hand copy, declared against the release it was read at.

    `internal.peers` is the key throughout: a container whose element the producer does not
    describe, which is one of the two things that earn a `handwritten` marker in the first place.
    Generated from `{"type": "object"}` alone it would type-check nothing, so the marker is the
    only thing keeping the block — and the only thing this suite is asking about.
    """
    return (
        "image: example\n"
        f"# @config-shape {value} {cs.HANDWRITTEN} {version} src/peers.rs\n"
        "\n"
        "# @schema\n"
        f"# # @config structured {key} optional\n"
        "# type: object\n"
        "# additionalProperties: true\n"
        "# @schema\n"
        "# -- Peers.\n"
        f"{value}: {{}}\n"
    )


class TestPartialBump(GateCase):
    """A chart shipping several images, bumped one at a time.

    The case a single-image chart cannot produce and this repository hits on every automated
    `tankovault` pull request: one service has a release, the other eight do not, and the chart's
    `appVersion` follows the one that moved. Holding every transcription against `appVersion`
    then asks for a re-read of a struct whose own image is still exactly where it was — at a
    release that image was never built at, so there is nothing to re-read it from.

    The fixture's first document is published at 2.0.0 and its sibling at 2.1.0, which is that
    situation with the nine reduced to two.
    """

    def setUp(self):
        super().setUp()
        self.chart.sibling("shallow", "2.1.0")

    def test_a_transcription_whose_own_image_did_not_move_is_left_alone(self):
        # `tankovault`'s `legal.documents`: an `api:` key, an `api` image that did not move, and
        # a bump of the frontend that used to fail it.
        self.chart.values(transcribed("deep:internal.peers", "2.0.0"))
        self.assertEqual(self.chart.check(), 0, self.chart.output)

    def test_a_transcription_whose_image_moved_is_refused(self):
        self.chart.values(transcribed("shallow:internal.peers", "2.0.0"))
        self.assertEqual(self.chart.check(), 1)
        self.assertIn("2.1.0", self.chart.output)

    def test_the_refusal_names_the_image_that_moved(self):
        # Which of nine to go and read is the whole of what the message is for.
        self.chart.values(transcribed("shallow:internal.peers", "2.0.0"))
        self.chart.check()
        self.assertIn("shallow now publishes it at 2.1.0", self.chart.output)

    def test_a_key_several_images_publish_is_held_against_the_newest(self):
        # One `@schema` block per value, so the release to read at is the furthest ahead: a block
        # read there is true of that image and at worst narrows an older one.
        self.chart.values(transcribed("internal.peers", "2.0.0"))
        self.assertEqual(self.chart.check(), 1)

    def test_reading_at_the_newest_covers_the_image_still_behind(self):
        self.chart.values(transcribed("internal.peers", "2.1.0"))
        self.assertEqual(self.chart.check(), 0, self.chart.output)

    def test_the_charts_appversion_does_not_decide(self):
        # The regression this class exists for. The fixture's `appVersion` is `v1` and neither
        # image was built at it; a transcription current with its own image passes regardless.
        self.assertEqual(
            (self.chart.dir / "Chart.yaml").read_text(encoding="utf-8").count("appVersion: v1"), 1
        )
        self.chart.values(transcribed("deep:internal.peers", "2.0.0"))
        self.assertEqual(self.chart.check(), 0, self.chart.output)

    def test_a_transcription_binding_nothing_is_still_held_against_the_chart(self):
        # No contract carries the key, so the chart's own release is the only one there is —
        # unchanged, and the reason the `appVersion` path is kept rather than deleted.
        self.chart.values(
            "# @config-shape peers handwritten v0 src/peers.rs\n"
            "\n"
            "# @schema\n"
            "# type: object\n"
            "# @schema\n"
            "# -- Peers.\n"
            "peers: {}\n"
        )
        self.assertEqual(self.chart.check(), 1)
        self.assertIn("appVersion v1", self.chart.output)


class TestCovers(unittest.TestCase):
    """Which releases a transcription read at one of them still describes."""

    def test_the_same_release_is_covered(self):
        self.assertTrue(cs._covers("2.0.0", "2.0.0"))

    def test_the_prefix_the_estate_spells_inconsistently_is_not_a_difference(self):
        # `tankovault` writes `8.9.1` and `netcup-offer-bot` writes `v3.1.0`, in `Chart.yaml` and
        # in the markers both. A gate reporting drift between the two spellings reports nothing.
        self.assertTrue(cs._covers("v8.9.1", "8.9.1"))
        self.assertTrue(cs._covers("8.9.1", "v8.9.1"))

    def test_an_older_read_does_not_cover_a_newer_image(self):
        self.assertFalse(cs._covers("2.0.0", "2.1.0"))

    def test_a_newer_read_covers_an_older_image(self):
        self.assertTrue(cs._covers("2.1.0", "2.0.0"))

    def test_the_comparison_is_numeric_and_not_lexical(self):
        # `8.9.1` sorts above `8.10.0` as text, which would pass a transcription a release behind.
        self.assertFalse(cs._covers("8.9.1", "8.10.0"))
        self.assertTrue(cs._covers("8.10.0", "8.9.1"))

    def test_a_release_that_does_not_parse_is_held_to_equality(self):
        # Two unorderable tags would otherwise compare equal and pass each other.
        self.assertFalse(cs._covers("nightly", "2.0.0"))
        self.assertFalse(cs._covers("2.0.0", "nightly"))
        self.assertTrue(cs._covers("nightly", "nightly"))


# --------------------------------------------------------------------------------------------
# The block reader, which the two above stand on
# --------------------------------------------------------------------------------------------


class TestBlocks(unittest.TestCase):
    def blocks(self, text: str) -> dict[str, cb.Block]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "values.yaml"
            path.write_text(text, encoding="utf-8")
            return {block.values_path: block for block in cb.parse_blocks(path, "fixture")}

    def test_a_block_is_bound_to_the_value_below_it(self):
        blocks = self.blocks(
            "top:\n  # @schema\n  # type: string\n  # @schema\n  # -- Leaf.\n  leaf: \"\"\n"
        )
        self.assertEqual(list(blocks), ["top.leaf"])
        self.assertEqual(blocks["top.leaf"].indent, 2)

    def test_a_block_followed_by_a_blank_line_binds_nothing(self):
        # The same contiguity rule a marker follows, and the reason both generators lose a
        # description that way.
        self.assertEqual(self.blocks("# @schema\n# type: string\n# @schema\n\nleaf: \"\"\n"), {})

    def test_the_marker_run_is_separated_from_the_schema(self):
        blocks = self.blocks(
            "# @schema\n"
            "# # @config projection a.b optional\n"
            "# type: string\n"
            "# @schema\n"
            "# -- Leaf.\n"
            "leaf: \"\"\n"
        )
        markers, schema = cs.split_block(blocks["leaf"])
        self.assertEqual(markers, ["# # @config projection a.b optional"])
        self.assertEqual(schema, ["# type: string"])

    def test_a_block_with_no_marker_is_still_a_block(self):
        blocks = self.blocks("# @schema\n# type: string\n# @schema\n# -- Leaf.\nleaf: \"\"\n")
        self.assertEqual(cs.block_schema(blocks["leaf"]), {"type": "string"})


if __name__ == "__main__":
    unittest.main()
