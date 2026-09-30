"""Tests for `contract-migrations.py`: the removals `just sync-config` cannot follow on its own."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from entry import load  # noqa: E402

migrations = load("contract_migrations", "contract-migrations.py")

# The shape TankoVault 11 moved away from: a key of its own, and a closed element that lost a
# property. Both bound, so the values the removals reach can be found at the compared revision.
VALUES_BEFORE = """\
# @schema
# additionalProperties: true
# @schema
# -- Legal documents.
legal:
  # @schema
  # # @config projection legal.dir optional
  # type: [string, 'null']
  # @schema
  # -- Root the sources resolve against.
  dir: /etc/legal

  # @schema
  # # @config structured legal.documents optional
  # type: [object, 'null']
  # @schema
  # -- The published documents.
  documents: null
"""

OLD_ELEMENT = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"sources": {"type": "object"}, "url": {"type": "string"}},
    },
}
NEW_ELEMENT = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"body": {"type": "object"}, "url": {"type": "string"}},
    },
}

DIFF = {
    "charts": [
        {
            "chart": "fixture",
            "contracts": [
                {
                    "changes": [
                        {"area": "envelope", "kind": "changed", "field": "digest",
                         "subject": "source.digest", "old": "a", "new": "b"},
                        {"area": "key", "kind": "removed", "field": None,
                         "subject": "legal.dir", "old": {"path": "legal.dir"}, "new": None},
                        {"area": "key", "kind": "changed", "field": "constraint",
                         "subject": "legal.documents", "old": OLD_ELEMENT, "new": NEW_ELEMENT},
                        {"area": "key", "kind": "added", "field": None,
                         "subject": "legal.default_locale", "old": None, "new": {}},
                    ]
                }
            ],
        }
    ]
}

TEMPLATE_STALE = """\
{{- define "fixture.legal" -}}
{{- range $slug, $doc := .Values.legal.documents -}}
{{- range $locale, $path := ($doc.sources | default dict) -}}{{- end -}}
{{- end -}}
dir: {{ .Values.legal.dir | quote }}
{{- if not $tls.ca.bundle.sources -}}{{- end -}}
{{- end -}}
"""

TEMPLATE_MIGRATED = """\
{{- define "fixture.legal" -}}
{{- range $slug, $doc := .Values.legal.documents -}}
{{- range $locale, $body := ($doc.body | default dict) -}}{{- end -}}
{{- end -}}
{{- if not $tls.ca.bundle.sources -}}{{- end -}}
{{- end -}}
"""

# Unrelated to `legal`, and spelling `sources:` as a projected volume does. It must not be reported.
TEMPLATE_UNRELATED = """\
- name: secrets
  projected:
    sources:
      - secret: {}
"""

SUITE_STALE = """\
tests:
  - it: passes a path through
    set:
      legal.documents.privacy.sources.en: /mnt/privacy.md
"""

CI_STALE = """\
legal:
  documents:
    privacy:
      sources:
        en: /mnt/privacy.md
"""

CI_UNRELATED = """\
networkPolicy:
  sources:
    - a
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class RemovedPropertiesTest(unittest.TestCase):
    def test_reports_a_property_the_element_lost(self) -> None:
        self.assertEqual(
            migrations.removed_properties(OLD_ELEMENT, NEW_ELEMENT),
            [("additionalProperties", "properties", "sources")],
        )

    def test_reports_nothing_for_an_addition(self) -> None:
        self.assertEqual(migrations.removed_properties(NEW_ELEMENT, NEW_ELEMENT), [])

    def test_reports_a_vanished_parent_once(self) -> None:
        old = {"properties": {"tls": {"properties": {"ca": {}, "cert": {}}}}}
        self.assertEqual(
            migrations.removed_properties(old, {"properties": {}}), [("properties", "tls")]
        )

    def test_walks_items(self) -> None:
        old = {"items": {"properties": {"a": {}, "b": {}}}}
        new = {"items": {"properties": {"a": {}}}}
        self.assertEqual(migrations.removed_properties(old, new), [("items", "properties", "b")])


class PatternTest(unittest.TestCase):
    def test_set_pattern_spans_the_map_key(self) -> None:
        pattern = migrations.set_pattern(
            "legal.documents", ("additionalProperties", "properties", "sources")
        )
        self.assertTrue(pattern.search("legal.documents.privacy.sources.en: /x"))
        self.assertFalse(pattern.search("legal.documents.privacy.body.en: x"))
        self.assertFalse(pattern.search("legal.documents.privacy.sourcesExtra: x"))

    def test_field_pattern_needs_a_range_variable(self) -> None:
        pattern = migrations.field_pattern("sources")
        self.assertTrue(pattern.search("{{ $doc.sources }}"))
        self.assertTrue(pattern.search('{{ set $entry "sources" . }}'))
        self.assertFalse(pattern.search("{{ $tls.ca.bundle.sources }}"))
        self.assertFalse(pattern.search("{{ $sources := dict }}"))


class MigrationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.charts = self.root / "charts"
        self.chart = self.charts / "fixture"
        (self.chart / "templates").mkdir(parents=True)
        (self.chart / "tests").mkdir()
        (self.chart / "ci").mkdir()
        (self.chart / "values.yaml").write_text(VALUES_BEFORE, encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "before")
        # The value blocks are gone by the time anyone looks; the markers must come from the ref.
        (self.chart / "values.yaml").write_text("legal: {}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        (self.chart / relative).write_text(text, encoding="utf-8")

    def found(self) -> list:
        return migrations.run(DIFF, self.charts, "HEAD")

    def test_names_every_stale_line(self) -> None:
        self.write("templates/_legal.tpl", TEMPLATE_STALE)
        self.write("templates/_volumes.tpl", TEMPLATE_UNRELATED)
        self.write("tests/legal_test.yaml", SUITE_STALE)
        self.write("ci/legal.yaml", CI_STALE)
        self.write("ci/netpol.yaml", CI_UNRELATED)

        found = {m.key: m for m in self.found()}
        self.assertEqual(set(found), {"legal.dir", "legal.documents"})

        self.assertEqual(found["legal.dir"].values_path, "legal.dir")
        self.assertEqual(
            [(r.path.name, r.line) for r in found["legal.dir"].references], [("_legal.tpl", 5)]
        )

        self.assertEqual(found["legal.documents"].values_path, "legal.documents")
        self.assertEqual(
            sorted((r.path.name, r.line) for r in found["legal.documents"].references),
            [("_legal.tpl", 3), ("legal.yaml", 4), ("legal_test.yaml", 4)],
        )

    def test_a_migrated_chart_passes(self) -> None:
        self.write("templates/_legal.tpl", TEMPLATE_MIGRATED)
        found = self.found()
        self.assertEqual(len(found), 2)
        self.assertTrue(all(not m.references for m in found))

    def test_skips_the_generated_round_trip_suites(self) -> None:
        self.write("tests/contract_roundtrip_api_test.yaml", SUITE_STALE)
        self.assertTrue(all(not m.references for m in self.found()))

    def test_exit_status_follows_the_references(self) -> None:
        report = self.root / "diff.json"
        report.write_text(json.dumps(DIFF), encoding="utf-8")
        argv = [str(report), "--since", "HEAD", "--charts", str(self.charts)]

        self.write("templates/_legal.tpl", TEMPLATE_MIGRATED)
        self.assertEqual(migrations.main(argv), 0)

        self.write("templates/_legal.tpl", TEMPLATE_STALE)
        self.assertEqual(migrations.main(argv), 2)

        report.write_text("{", encoding="utf-8")
        self.assertEqual(migrations.main(argv), 1)


if __name__ == "__main__":
    unittest.main()
