#!/usr/bin/env python3
"""Adopting owed contract keys into a chart that already exists.

The scaffold in `test_contract_scaffold` writes a chart nobody has edited, where every rule is
about the *output*. This one writes into a file somebody else wrote, so half of what can go wrong
is about the file rather than about the contract, and none of it is visible from a run over this
repository — every enrolled chart here is complete, which is exactly why a key going unbound is
worth a command in the first place.

Four of the cases are rules a plausible implementation gets wrong:

**A value that already exists is refused.** The marker is the only statement of which key a value
feeds, and on a value that predates the contract a generator would be *guessing* that
`telemetry.logLevel` is what `telemetry.log_level` reads. `config_bindings.py` refuses to have
markers generated for exactly this case, and the exemption this command claims — the value and the
marker being written together, from one key — evaporates the moment the value is already there.

**The block lands after the last bound sibling.** Not at the end of the file, where the chassis
is: thirty blocks that are the same in every chart, and a new setting a screen and a half below
the settings it belongs with. Derived from the markers rather than from a list of block names, so
it is right for a chart nobody wrote the rule against.

**One value per key, however many documents declare it.** `tankovault` declares `metrics.enabled`
in eight of its nine contracts and binds all eight from one unscoped marker. A writer that emitted
one value per document would produce eight values for one setting, which rule 4 refuses — and it
would look correct on every single-document chart here.

**A credential carries no marker.** The same rule the scaffold has, and the same reason: the gate
refuses a key that is both bound by a marker and written off in `unbound`, and a file-delivered
credential is written off.

The round trip is the assertion that ties the rest together. What comes out of this command is
handed to `check-config-bindings` and to `config_shapes --check` — the two gates that decide
whether the chart is complete and whether its blocks match the contract — rather than compared
against bytes this test wrote out by hand, which would only prove the generator agrees with
itself.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_bindings as cb  # noqa: E402
import config_shapes as cs  # noqa: E402
from config_declaration import load_declaration  # noqa: E402
from config_report import Report  # noqa: E402
from entry import load  # noqa: E402

FIXTURES = SCRIPTS.parent / "testdata" / "contracts"

DIGEST = "sha256:" + "1" * 64

adopt = load("adopt_config", "adopt-config.py")


def key(path: str, **overrides: Any) -> dict[str, Any]:
    """One contract key, with the fields every consumer of one reads."""
    spelt = path.replace(".", "__")
    entry: dict[str, Any] = {
        "path": path,
        "env": f"FIXTURE_{spelt.upper()}",
        "env_file": f"FIXTURE_{spelt.upper()}_FILE",
        "secrets_file": spelt,
        "docs": f"The {path} setting.",
        "ty": "String",
        "text_form": "text",
        "values": [],
        "constraint": {"type": "string"},
        "text_constraint": None,
        "aliases": [],
        "default": None,
        "default_value": None,
        "note": None,
        "required": False,
        "secret": False,
        "reserved": False,
    }
    entry.update(overrides)
    return entry


def contract_of(*keys: dict[str, Any]) -> dict[str, Any]:
    """A contract document carrying these keys.

    Built from the committed `api.json` rather than from a literal, so the envelope every reader
    walks — the dialect, the loader variables, `external`, `json_schema` — is the one the format
    actually publishes and not this test's idea of it.
    """
    document = json.loads((FIXTURES / "api.json").read_text(encoding="utf-8"))
    document["schema"]["keys"] = [copy.deepcopy(entry) for entry in keys]
    return document


DECLARATION = """\
bindings: true
documents:
  - name: api
    source:
      kind: ConfigMap
      selector: { app: fixture }
      key: config.toml
    images:
      - values: image
        contract: contracts/api.json
"""

TWO_DOCUMENTS = DECLARATION + """\
  - name: worker
    source:
      kind: ConfigMap
      selector: { app: fixture-worker }
      key: config.toml
    images:
      - values: image
        contract: contracts/worker.json
"""

# One bound value under a grouping block, then a block with no marker at all — the shape of every
# chart here: the configuration surface first, the chassis after it.
VALUES = """\
# @schema
# additionalProperties: true
# @schema
# -- The authentication settings.
auth:
  # @schema
  # # @config projection auth.session_ttl
  # type: integer
  # @schema
  # -- How long a session lives.
  sessionTtl: 3600

# @schema
# type: object
# @schema
# -- Requests and limits for the container.
resources: {}
"""


class Chart:
    """A throwaway `charts/` tree holding one chart, for the command to read and write."""

    def __init__(self, root: Path, name: str = "fixture"):
        self.root = root
        self.name = name
        self.dir = root / name
        (self.dir / "contracts").mkdir(parents=True)
        (self.dir / "Chart.yaml").write_text(
            f"name: {name}\nversion: 1.0.0\nappVersion: v1\n", encoding="utf-8"
        )
        self.declaration(DECLARATION)
        self.values(VALUES)

    def contract(self, document: str, contract: dict) -> Chart:
        (self.dir / "contracts" / f"{document}.json").write_text(
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

    def declaration(self, body: str) -> Chart:
        (self.dir / "config-contract.yaml").write_text(body, encoding="utf-8")
        return self

    def values(self, text: str, *, crlf: bool = False) -> Chart:
        body = text.replace("\n", "\r\n") if crlf else text
        (self.dir / "values.yaml").write_bytes(body.encode("utf-8"))
        return self

    # ------------------------------------------------------------------------------------

    def plan(self) -> adopt.Adoption:
        planned = adopt.adopt(self.dir, set())
        assert planned is not None, "the fixture chart is enrolled"
        return planned

    def write(self) -> adopt.Adoption:
        planned = self.plan()
        adopt.write(planned)
        return planned

    def text(self) -> str:
        return (self.dir / "values.yaml").read_bytes().decode("utf-8")

    def parsed(self) -> dict[str, Any]:
        return yaml.safe_load(self.text()) or {}

    def gate(self) -> Report:
        """`check-config-bindings` over the chart as it now stands."""
        report = Report()
        adopt.gate.run(self.root, report)
        return report


def messages(report: Report) -> str:
    return "\n".join(finding.message for _, finding in report.errors)


def refusals(planned: adopt.Adoption) -> str:
    return "\n".join(f"{item.path} {item.reason}" for item in planned.refusals)


def blocks(text: str) -> dict[str, dict[str, Any]]:
    """Every `@schema` block, keyed by the value it describes, read as helm-schema reads it.

    The marker is a YAML comment inside the block, so what comes back here is the schema alone —
    which is the half `just schema` turns into `values.schema.json`, and the half worth asserting
    a type against.
    """
    lines = text.splitlines()
    found: dict[str, dict[str, Any]] = {}
    number = 0
    while number < len(lines):
        if lines[number].strip() != "# @schema":
            number += 1
            continue
        start = number + 1
        end = start
        while lines[end].strip() != "# @schema":
            end += 1
        body = "\n".join(line.strip().removeprefix("#").strip() for line in lines[start:end])
        value = end + 1
        while lines[value].strip().startswith("#"):
            value += 1
        found[lines[value].split(":")[0].strip()] = yaml.safe_load(body) or {}
        number = value
    return found


# --------------------------------------------------------------------------------------------
# What a new key becomes
# --------------------------------------------------------------------------------------------


class NewValues(unittest.TestCase):
    def test_a_new_key_becomes_a_value_with_its_marker_type_and_description(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key(
                        "log.level",
                        docs="Least severe level that is logged.",
                        constraint={"type": "string", "enum": ["debug", "info"]},
                        default_value="info",
                    ),
                ),
            )
            chart.write()

            self.assertEqual(chart.parsed()["log"]["level"], "info")
            self.assertIn(
                "# # @config projection log.level optional", chart.text()
            )
            self.assertIn("Least severe level that is logged (`log.level`)", chart.text())
            self.assertEqual(blocks(chart.text())["level"], {"enum": ["debug", "info", None]})

    def test_the_default_is_the_one_the_image_publishes(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key(
                        "worker.concurrency",
                        text_form="integer",
                        constraint={"type": "integer"},
                        default_value=8,
                    ),
                ),
            )
            chart.write()
            self.assertEqual(chart.parsed()["worker"]["concurrency"], 8)

    def test_two_keys_under_one_new_branch_share_a_grouping_block(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("sentry.dsn_url"),
                    key("sentry.environment"),
                ),
            )
            planned = chart.write()

            self.assertEqual(len(planned.insertions), 1)
            self.assertEqual(chart.text().count("\nsentry:"), 1)
            self.assertEqual(sorted(chart.parsed()["sentry"]), ["dsnUrl", "environment"])
            # The block a contract says nothing about, and the placeholder it therefore carries.
            self.assertIn("TODO: what the `sentry` settings have in common", chart.text())
            self.assertEqual(planned.insertions[0].branches, ("sentry",))

    def test_a_credential_gets_a_value_with_no_marker_and_a_write_off(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("database.url", secret=True, required=True),
                ),
            )
            planned = chart.write()

            self.assertEqual(chart.parsed()["database"]["url"], "")
            self.assertNotIn("@config projection database.url", chart.text())
            self.assertIn("`database__url`", chart.text())
            self.assertEqual(
                [item.path for item in planned.plan.secrets], ["database.url"]
            )

    def test_a_reserved_key_gets_no_value_at_all(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("runtime.node_id", reserved=True),
                ),
            )
            planned = chart.write()

            self.assertNotIn("runtime", chart.parsed())
            self.assertEqual(
                [item.path for item in planned.plan.written_off], ["runtime.node_id"]
            )
            self.assertIn("Reserved by the loader", planned.plan.written_off[0].reason)


# --------------------------------------------------------------------------------------------
# Where the block goes
# --------------------------------------------------------------------------------------------


class Placement(unittest.TestCase):
    def test_a_new_top_level_block_lands_after_the_last_bound_block(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level"),
                ),
            )
            chart.write()

            lines = chart.text().splitlines()
            self.assertLess(lines.index("log:"), lines.index("resources: {}"))
            self.assertGreater(lines.index("log:"), lines.index("auth:"))

    def test_a_new_leaf_lands_inside_the_block_it_belongs_to(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("auth.issuer"),
                ),
            )
            chart.write()

            self.assertEqual(sorted(chart.parsed()["auth"]), ["issuer", "sessionTtl"])
            lines = chart.text().splitlines()
            self.assertLess(lines.index("  issuer: null"), lines.index("resources: {}"))

    def test_the_file_keeps_its_line_endings(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.values(VALUES, crlf=True)
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level"),
                ),
            )
            chart.write()

            body = (chart.dir / "values.yaml").read_bytes()
            self.assertEqual(body.count(b"\n"), body.count(b"\r\n"))

    def test_planning_writes_nothing(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level"),
                ),
            )
            before = chart.text()
            planned = chart.plan()

            self.assertTrue(planned.insertions)
            self.assertEqual(chart.text(), before)


# --------------------------------------------------------------------------------------------
# What it refuses to write
# --------------------------------------------------------------------------------------------


class Refusals(unittest.TestCase):
    def test_a_key_whose_value_already_exists_is_refused_and_names_the_marker(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            # The chart has `auth.sessionTtl`; the contract's key is the one it feeds, and the
            # marker that says so has been lost.
            chart.values(VALUES.replace("  # # @config projection auth.session_ttl\n", ""))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"})
                ),
            )
            planned = chart.write()

            self.assertEqual(planned.insertions, [])
            self.assertIn("which this chart already has", refusals(planned))
            self.assertIn(
                "# # @config projection auth.session_ttl optional", refusals(planned)
            )

    def test_a_leaf_on_the_way_to_the_value_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    # `resources` is a scalar-valued block in the fixture, so `resources.limit`
                    # cannot exist beside it.
                    key("resources.limit"),
                ),
            )
            chart.values(VALUES.replace("resources: {}", "resources: none"))
            planned = chart.write()

            self.assertEqual(planned.insertions, [])
            self.assertIn("is a leaf value on the way there", refusals(planned))

    def test_documents_that_disagree_about_a_key_are_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.declaration(TWO_DOCUMENTS)
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level", constraint={"type": "string"}),
                ),
            )
            chart.contract(
                "worker",
                contract_of(key("log.level", constraint={"type": "integer"})),
            )
            planned = chart.write()

            self.assertEqual(planned.insertions, [])
            self.assertIn("do not agree what it is", refusals(planned))

    def test_a_key_bound_in_one_document_and_missing_in_another_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.declaration(TWO_DOCUMENTS)
            # The marker is scoped to `api`, so `worker` declares the key and nothing binds it
            # there. A second value under the same name is not the repair; the scope is.
            chart.values(
                VALUES.replace(
                    "@config projection auth.session_ttl",
                    "@config projection api:auth.session_ttl",
                )
            )
            shared = key("auth.session_ttl", text_form="integer", constraint={"type": "integer"})
            chart.contract("api", contract_of(shared))
            chart.contract("worker", contract_of(shared))
            planned = chart.write()

            self.assertEqual(planned.insertions, [])
            self.assertIn("what is owed is that marker's scope", refusals(planned))

    def test_a_chart_whose_markers_are_broken_is_not_written_to(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.values(
                VALUES.replace("auth.session_ttl", "auth.session_ttl_typo")
                + "\n# @schema\n# type: string\n# @schema\n# -- A value.\nspare: \"\"\n"
            )
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level"),
                ),
            )
            before = chart.text()
            planned = chart.plan()

            self.assertEqual(planned.insertions, [])
            self.assertTrue(planned.blocked)
            self.assertIn("auth.session_ttl_typo", "\n".join(planned.blocked))
            adopt.write(planned)
            self.assertEqual(chart.text(), before)


# --------------------------------------------------------------------------------------------
# One value per key, and the round trip
# --------------------------------------------------------------------------------------------


class Documents(unittest.TestCase):
    def test_a_key_several_documents_declare_becomes_one_value(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.declaration(TWO_DOCUMENTS)
            shared = key("log.level", default_value="info")
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    shared,
                ),
            )
            chart.contract("worker", contract_of(shared))
            planned = chart.write()

            self.assertEqual(len(planned.insertions), 1)
            self.assertEqual(chart.text().count("@config projection log.level"), 1)
            self.assertEqual(chart.parsed()["log"]["level"], "info")


class TheDeclaration(unittest.TestCase):
    """The second file an adoption writes, and the one licence it takes to write it.

    A write-off says why no chart value surfaces a key, and that is a judgement in general — which
    is why `config_declaration.Unbound` requires a sentence somebody stands behind. The two this
    command writes are not: `reserved` is the image saying the loader sets the key itself, and a
    credential's channel is this repository's standing rule, already written in the same words by
    `config_scaffold.render_declaration` for a chart being created. An ordinary key never reaches
    the declaration at all, because it gets a value instead — so there is no path here that writes
    a judgement, and these cases are what holds that shut.
    """

    def declaration_of(self, chart: Chart) -> str:
        return (chart.dir / "config-contract.yaml").read_text(encoding="utf-8")

    def test_a_credential_is_written_off_and_given_its_value_column(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("database.url", secret=True, required=True),
                ),
            )
            chart.write()
            body = self.declaration_of(chart)

            self.assertIn("unbound:", body)
            self.assertIn("      - database.url", body)
            self.assertIn("`database__url`", body)
            self.assertIn("  - key: database.url", body)
            self.assertIn("    value: database.url", body)

            declaration = load_declaration(chart.dir)
            self.assertEqual([entry.keys for entry in declaration.unbound], [("database.url",)])
            self.assertEqual(declaration.credentials["database.url"].value, "database.url")
            self.assertIsNone(declaration.credentials["database.url"].note)

    def test_a_reserved_key_is_written_off_with_the_loader_reason(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("runtime.node_id", reserved=True),
                ),
            )
            chart.write()

            declaration = load_declaration(chart.dir)
            self.assertEqual([entry.keys for entry in declaration.unbound], [("runtime.node_id",)])
            self.assertIn("Reserved by the loader", declaration.unbound[0].reason)
            self.assertEqual(declaration.credentials, {})

    def test_a_credential_the_chart_already_has_a_value_for_is_still_written_off(self):
        """The state a run leaves behind if it wrote the value before this file was written to.

        The refusal stands — nothing claims that value feeds this key — but the write-off does not
        depend on who wrote the value, so the reason drops the clause naming it and the gate is
        satisfied. Without this, running the command twice leaves a chart it could not finish.
        """
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.values(VALUES + 'database:\n  url: ""\n')
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("database.url", secret=True, required=True),
                ),
            )
            planned = chart.write()

            self.assertEqual(planned.insertions, [])
            self.assertTrue(planned.refusals)
            declaration = load_declaration(chart.dir)
            self.assertEqual([entry.keys for entry in declaration.unbound], [("database.url",)])
            self.assertNotIn("is the value that carries it", declaration.unbound[0].reason)
            # The `value:` column names a value this command did not write, so it is not claimed.
            self.assertEqual(declaration.credentials, {})
            self.assertEqual(messages(chart.gate()), "")

    def test_a_key_already_written_off_is_not_written_off_twice(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.declaration(
                DECLARATION
                + "unbound:\n"
                + "  - keys:\n"
                + "      - database.url\n"
                + "    reason: Already decided.\n"
            )
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("database.url", secret=True, required=True),
                ),
            )
            chart.write()

            declaration = load_declaration(chart.dir)
            self.assertEqual([entry.reason for entry in declaration.unbound], ["Already decided."])

    def test_a_group_appends_to_the_block_a_chart_already_has(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.declaration(
                DECLARATION
                + "unbound:\n"
                + "  - keys:\n"
                + "      - log.level\n"
                + "    reason: Not surfaced.\n"
            )
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level"),
                    key("runtime.node_id", reserved=True),
                ),
            )
            chart.write()

            declaration = load_declaration(chart.dir)
            self.assertEqual(
                [entry.keys for entry in declaration.unbound],
                [("log.level",), ("runtime.node_id",)],
            )

    def test_planning_writes_no_declaration(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("runtime.node_id", reserved=True),
                ),
            )
            before = self.declaration_of(chart)
            planned = chart.plan()

            self.assertTrue(planned.edits)
            self.assertEqual(self.declaration_of(chart), before)


class RoundTrip(unittest.TestCase):
    """What comes out satisfies the two gates that decide whether a chart is complete."""

    def adopted(self, workspace: str) -> Chart:
        chart = Chart(Path(workspace))
        chart.contract(
            "api",
            contract_of(
                key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                key("log.level", constraint={"type": "string", "enum": ["debug", "info"]}),
                key("sentry.environment", default_value="production"),
                key(
                    "sentry.max_events", text_form="integer", constraint={"type": "integer"}
                ),
            ),
        )
        chart.write()
        return chart

    def test_the_gate_passes_over_what_was_written(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = self.adopted(workspace)
            self.assertEqual(messages(chart.gate()), "")

    def test_the_shape_gate_passes_over_what_was_written(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = self.adopted(workspace)
            shapes = cs.Chart(chart.dir)
            cs.check(shapes)
            self.assertEqual(shapes.problems, [])

    def test_the_values_file_still_parses_and_keeps_what_it_had(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = self.adopted(workspace)
            values = chart.parsed()
            self.assertEqual(values["auth"]["sessionTtl"], 3600)
            self.assertEqual(values["resources"], {})
            self.assertEqual(sorted(values["sentry"]), ["environment", "maxEvents"])

    def test_a_second_run_has_nothing_left_to_do(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = self.adopted(workspace)
            again = chart.plan()
            self.assertEqual(again.insertions, [])
            self.assertEqual(again.refusals, [])


# --------------------------------------------------------------------------------------------
# Selecting one key
# --------------------------------------------------------------------------------------------


class Selection(unittest.TestCase):
    def test_a_named_key_is_adopted_and_the_rest_are_left(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"}),
                    key("log.level"),
                    key("sentry.environment"),
                ),
            )
            planned = adopt.adopt(chart.dir, {"log.level"})
            adopt.write(planned)

            self.assertIn("log", chart.parsed())
            self.assertNotIn("sentry", chart.parsed())

    def test_a_key_that_is_not_owed_is_reported_rather_than_ignored(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.contract(
                "api",
                contract_of(
                    key("auth.session_ttl", text_form="integer", constraint={"type": "integer"})
                ),
            )
            planned = adopt.adopt(chart.dir, {"log.level"})

            self.assertEqual(planned.insertions, [])
            self.assertIn("is not a key fixture is owed a value for", refusals(planned))


# --------------------------------------------------------------------------------------------
# Enrolment
# --------------------------------------------------------------------------------------------


class Enrolment(unittest.TestCase):
    def test_a_chart_that_is_not_enrolled_is_passed_over(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            chart.declaration(DECLARATION.replace("bindings: true", "bindings: false"))
            chart.contract("api", contract_of(key("log.level")))
            self.assertIsNone(adopt.adopt(chart.dir, set()))

    def test_a_marker_is_never_written_for_a_chart_with_no_contract(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace))
            (chart.dir / "config-contract.yaml").unlink()
            self.assertIsNone(adopt.adopt(chart.dir, set()))
            self.assertNotIn(cb.MARKER, chart.text().replace("@config projection", ""))


if __name__ == "__main__":
    unittest.main()
