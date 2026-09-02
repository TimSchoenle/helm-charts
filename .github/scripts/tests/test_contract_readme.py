#!/usr/bin/env python3
"""The credential reference a chart's README carries, and the two columns the chart still owns.

Every case here is a way for the generated table to be confidently wrong, which is worse than the
hand-written one it replaces: a reader trusts a generated artefact and an operator creates the
Secret it describes. Four are worth naming.

  the stale entry     a `credentials` entry for a key the image stopped declaring secret keeps a
                      row in the table that no longer corresponds to a file anything reads

  the renamed value   the `Chart value` column tells an operator what to set instead of creating
                      the Secret, and a path the chart no longer exposes sends them to a value
                      that does nothing at all — silently, since Helm accepts any unknown key
                      that the schema's `additionalProperties` allows

  the splice          the block sits inside a hand-written README, so anything but exactly one
                      marker pair has to be refused rather than guessed at: two pairs make "the
                      block" ambiguous and an unclosed one swallows the rest of the file

  the empty column    `Read by` on a single-document chart is that chart's name repeated once per
                      row, and every empty column is one more thing between an operator and the
                      key name they came for
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_readme as cr  # noqa: E402

from config_declaration import (  # noqa: E402
    DeclarationError,
    load_declaration,
)

DIGEST = "sha256:" + "1" * 64


def secret(path: str, **overrides: Any) -> dict[str, Any]:
    """One contract key the image declares a credential."""
    entry: dict[str, Any] = {
        "path": path,
        "env": "APP_" + path.upper().replace(".", "__"),
        "env_file": "APP_" + path.upper().replace(".", "__") + "_FILE",
        "secrets_file": path.replace(".", "__"),
        "docs": "A credential.",
        "text_form": "text",
        "constraint": {"type": "string"},
        "default_value": None,
        "required": False,
        "secret": True,
        "reserved": False,
        "values": [],
    }
    entry.update(overrides)
    return entry


def contract(*keys: dict[str, Any]) -> dict[str, Any]:
    return {
        "terrace_contract": 1,
        "app": {"name": "app", "version": "1.0.0"},
        "schema": {
            "schema_version": 1,
            "dialect": {
                "prefix": "APP_",
                "nesting_separator": "__",
                "indirection_suffix": "_FILE",
            },
            "loader": [
                {
                    "env": "APP_CONFIG",
                    "role": "config",
                    "docs": "Names the TOML layer.",
                    "default": "config.toml",
                },
                {
                    "env": "APP_SECRETS_DIR",
                    "role": "secrets_dir",
                    "docs": "Names a directory of key-named files.",
                    "default": None,
                },
            ],
            "keys": list(keys),
        },
        "json_schema": {"type": "object"},
        "external": {"env": [], "ignore": [], "unknown": "reject"},
    }


DECLARATION = """\
documents:
  - name: one
    source:
      kind: ConfigMap
      selector: { app: fixture }
      key: config.toml
    images:
      - values: image
        contract: contracts/one.json
"""

VALUES = """\
image:
  repository: example/app
  tag: v1@{digest}

secretValue: ""
""".replace("{digest}", DIGEST)

TEMPLATE = """\
# Fixture

## Credentials

<!-- @config-credentials -->
<!-- @config-credentials end -->

Everything after the block.
"""


class ChartBuilder:
    """A throwaway `charts/` tree holding one contracted chart."""

    def __init__(self, root: Path, name: str = "fixture"):
        self.root = root
        self.dir = root / name
        (self.dir / "contracts").mkdir(parents=True)
        (self.dir / "Chart.yaml").write_text(
            f"name: {name}\nversion: 1.0.0\nappVersion: v1\n", encoding="utf-8"
        )
        self.declaration(DECLARATION)
        self.contract(contract(secret("app.token")))
        self.values(VALUES)
        self.template(TEMPLATE)
        self.output = ""

    def declaration(self, text: str) -> ChartBuilder:
        (self.dir / "config-contract.yaml").write_text(text, encoding="utf-8")
        return self

    def contract(self, document: dict) -> ChartBuilder:
        (self.dir / "contracts" / "one.json").write_text(
            json.dumps(
                {
                    "source": {
                        "image": "docker.io/example/app",
                        "digest": DIGEST,
                        "sha256": "0" * 64,
                        "fetched": "2026-01-01T00:00:00Z",
                    },
                    "contract": document,
                }
            ),
            encoding="utf-8",
        )
        return self

    def values(self, text: str) -> ChartBuilder:
        (self.dir / "values.yaml").write_text(text, encoding="utf-8")
        return self

    def template(self, text: str) -> ChartBuilder:
        (self.dir / "README.md.gotmpl").write_text(text, encoding="utf-8")
        return self

    def write(self) -> int:
        return self._run([])

    def check(self) -> int:
        return self._run(["--check"])

    def _run(self, flags: list[str]) -> int:
        with io.StringIO() as out, io.StringIO() as err:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cr.main(["--charts", str(self.root), *flags])
            self.output = out.getvalue() + err.getvalue()
        return code

    def read(self) -> str:
        return (self.dir / "README.md.gotmpl").read_text(encoding="utf-8")


class Case(unittest.TestCase):
    """Each case gets its own tree, because the writer edits the files it is given."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "charts"
        self.root.mkdir()
        self.chart = ChartBuilder(self.root)


# --------------------------------------------------------------------------------------------
# The splice
# --------------------------------------------------------------------------------------------


class TestSplice(unittest.TestCase):
    def test_only_the_lines_between_the_markers_move(self):
        text = (
            "before\n<!-- @config-credentials -->\nold\n"
            "<!-- @config-credentials end -->\nafter\n"
        )
        result = cr.splice(text, ["new"], "fixture")
        self.assertEqual(
            result,
            "before\n<!-- @config-credentials -->\nnew\n"
            "<!-- @config-credentials end -->\nafter\n",
        )

    def test_the_markers_indentation_is_carried_onto_the_block(self):
        text = "  <!-- @config-credentials -->\n  <!-- @config-credentials end -->\n"
        self.assertIn("  new", cr.splice(text, ["new"], "fixture"))

    def test_a_blank_generated_line_is_not_indented_into_trailing_whitespace(self):
        text = "  <!-- @config-credentials -->\n  <!-- @config-credentials end -->\n"
        self.assertIn("\n\n", cr.splice(text, ["one", "", "two"], "fixture"))

    def test_a_crlf_template_keeps_its_endings(self):
        text = "a\r\n<!-- @config-credentials -->\r\n<!-- @config-credentials end -->\r\n"
        result = cr.splice(text, ["new"], "fixture")
        self.assertIn("\r\nnew\r\n", result)
        # No bare newline survives: every one of them still carries its carriage return.
        self.assertEqual(result.count("\n"), result.count("\r\n"))

    def test_a_second_pair_is_refused_rather_than_guessed_at(self):
        text = (
            "<!-- @config-credentials -->\n<!-- @config-credentials end -->\n"
            "<!-- @config-credentials -->\n<!-- @config-credentials end -->\n"
        )
        with self.assertRaises(cr.ReadmeError):
            cr.splice(text, ["new"], "fixture")

    def test_an_unclosed_block_is_refused(self):
        with self.assertRaises(cr.ReadmeError):
            cr.splice("<!-- @config-credentials -->\nrest\n", ["new"], "fixture")

    def test_a_reversed_pair_is_refused(self):
        text = "<!-- @config-credentials end -->\n<!-- @config-credentials -->\n"
        with self.assertRaises(cr.ReadmeError):
            cr.splice(text, ["new"], "fixture")


# --------------------------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------------------------


class TestTable(Case):
    def test_a_credential_reaches_the_table_with_its_file_name(self):
        self.assertEqual(self.chart.write(), 0)
        self.assertIn("| `app__token` | no |", self.chart.read())

    def test_the_environment_prefix_comes_from_the_dialect(self):
        # Not from the longest common prefix of the spellings, which on a chart with exactly one
        # credential is the whole variable presented as a prefix.
        self.chart.write()
        self.assertIn("`APP_<PATH>`", self.chart.read())

    def test_a_required_credential_says_so(self):
        self.chart.contract(contract(secret("app.token", required=True)))
        self.chart.write()
        self.assertIn("| `app__token` | yes |", self.chart.read())

    def test_the_optional_columns_are_absent_until_something_fills_them(self):
        self.chart.write()
        self.assertIn("| Secrets file | Required |", self.chart.read())

    def test_a_declared_value_and_note_become_columns(self):
        self.chart.declaration(
            DECLARATION
            + "credentials:\n  - key: app.token\n    value: secretValue\n    note: always\n"
        )
        self.chart.write()
        rendered = self.chart.read()
        self.assertIn("| Secrets file | Required | Chart value | When |", rendered)
        self.assertIn("| `app__token` | no | `secretValue` | always |", rendered)

    def test_the_reader_column_appears_only_on_a_multi_document_chart(self):
        self.chart.write()
        self.assertNotIn("Read by", self.chart.read())

    def test_writing_twice_changes_nothing(self):
        self.chart.write()
        once = self.chart.read()
        self.chart.write()
        self.assertEqual(self.chart.read(), once)

    def test_everything_outside_the_block_is_left_alone(self):
        self.chart.write()
        rendered = self.chart.read()
        self.assertIn("# Fixture", rendered)
        self.assertIn("Everything after the block.", rendered)


# --------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------


class TestGate(Case):
    def test_a_block_behind_the_contract_fails_the_check(self):
        self.assertEqual(self.chart.check(), 1)
        self.assertEqual(self.chart.write(), 0)
        self.assertEqual(self.chart.check(), 0)

    def test_a_chart_with_credentials_and_no_block_is_refused(self):
        self.chart.template("# Fixture\n")
        self.assertEqual(self.chart.check(), 1)
        self.assertIn("carries no", self.chart.output)

    def test_a_block_in_a_chart_with_no_credential_is_refused(self):
        self.chart.contract(contract(secret("app.token", secret=False)))
        self.assertEqual(self.chart.check(), 1)
        self.assertIn("nothing to generate", self.chart.output)

    def test_an_entry_for_a_key_no_contract_declares_secret_is_refused(self):
        self.chart.declaration(
            DECLARATION + "credentials:\n  - key: app.gone\n    note: always\n"
        )
        self.assertEqual(self.chart.check(), 1)
        self.assertIn("outlived", self.chart.output)

    def test_a_value_the_chart_no_longer_exposes_is_refused(self):
        self.chart.declaration(
            DECLARATION + "credentials:\n  - key: app.token\n    value: renamedValue\n"
        )
        self.assertEqual(self.chart.check(), 1)
        self.assertIn("does not expose", self.chart.output)

    def test_a_chart_with_no_declaration_is_not_walked(self):
        (self.chart.dir / "config-contract.yaml").unlink()
        self.assertEqual(self.chart.check(), 0)


# --------------------------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------------------------


class TestDeclaration(Case):
    def load(self, block: str):
        self.chart.declaration(DECLARATION + block)
        return load_declaration(self.chart.dir)

    def test_an_entry_carrying_neither_a_value_nor_a_note_is_refused(self):
        # The row it describes is generated from the contract without it, so the entry is either
        # a leftover or somebody meant to write one of the two columns.
        with self.assertRaises(DeclarationError):
            self.load("credentials:\n  - key: app.token\n")

    def test_a_key_named_twice_is_refused(self):
        with self.assertRaises(DeclarationError):
            self.load(
                "credentials:\n  - key: app.token\n    note: one\n"
                "  - key: app.token\n    note: two\n"
            )

    def test_an_unknown_field_is_refused(self):
        with self.assertRaises(DeclarationError):
            self.load("credentials:\n  - key: app.token\n    notes: always\n")

    def test_a_note_is_collapsed_onto_one_line(self):
        declaration = self.load(
            "credentials:\n  - key: app.token\n    note: >-\n      wrapped\n      over two\n"
        )
        self.assertEqual(declaration.credentials["app.token"].note, "wrapped over two")


if __name__ == "__main__":
    unittest.main()
