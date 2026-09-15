"""Tests for `check-config-wired.py`: the presence gate `ee094b4` would have failed on."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from entry import load  # noqa: E402

wired = load("check_config_wired", "check-config-wired.py")

from config_report import Report  # noqa: E402

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

VALUES = """\
# @schema
# additionalProperties: true
# @schema
# -- The `scheduler` settings.
scheduler:
  # @schema
  # # @config projection scheduler.interval_secs optional
  # type: [integer, 'null']
  # @schema
  # -- Seconds between passes. 0 disables (`scheduler.interval_secs`).
  intervalSecs: 300
"""


class Chart:
    def __init__(self, root: Path, helpers: str):
        self.dir = root / "fixture"
        (self.dir / "templates").mkdir(parents=True)
        self.declaration(DECLARATION)
        self.values(VALUES)
        (self.dir / "templates" / "_helpers.tpl").write_text(helpers, encoding="utf-8")

    def declaration(self, text: str) -> Chart:
        (self.dir / "config-contract.yaml").write_text(text, encoding="utf-8")
        return self

    def values(self, text: str) -> Chart:
        (self.dir / "values.yaml").write_text(text, encoding="utf-8")
        return self

    def check(self) -> Report:
        report = Report()
        wired.check_chart(self.dir, report)
        return report


class LeafOf(unittest.TestCase):
    def test_the_leaf_is_the_final_path_segment(self):
        self.assertEqual(wired.leaf_of("scheduler.interval_secs"), "interval_secs")
        self.assertEqual(wired.leaf_of("metrics.enabled"), "enabled")


class CheckChart(unittest.TestCase):
    def test_a_key_rendered_as_a_yaml_style_mapping_is_found(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(
                Path(workspace),
                '{{- define "fixture.derivedConfig" -}}\n'
                "scheduler:\n"
                "  interval_secs: {{ .Values.scheduler.intervalSecs }}\n"
                "{{- end -}}\n",
            )
            report = chart.check()
            self.assertEqual(report.errors, [])

    def test_a_key_built_through_a_go_template_dict_call_is_found(self):
        """`tankovault`'s `branding` and `telemetry.sentry` blocks are built this way, entirely —
        a plain search for `leaf:` flags every key in both as unwired, which is the false
        positive this case exists to rule out."""
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(
                Path(workspace),
                '{{- define "fixture.derivedConfig" -}}\n'
                "{{- $out := dict \"interval_secs\" .Values.scheduler.intervalSecs -}}\n"
                "scheduler:\n"
                "  {{- toYaml $out | nindent 2 }}\n"
                "{{- end -}}\n",
            )
            report = chart.check()
            self.assertEqual(report.errors, [])

    def test_a_key_no_template_renders_is_reported(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(
                Path(workspace),
                '{{- define "fixture.derivedConfig" -}}\n'
                "auth:\n"
                "  session_ttl: {{ .Values.auth.sessionTtl }}\n"
                "{{- end -}}\n",
            )
            report = chart.check()
            self.assertEqual(len(report.errors), 1)
            where, finding = report.errors[0]
            self.assertIn("fixture/values.yaml", where)
            self.assertIn("scheduler.interval_secs", finding.message)
            self.assertIn("interval_secs", finding.message)

    def test_an_unenrolled_chart_is_not_checked(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace), '{{- define "fixture.derivedConfig" -}}\n{{- end -}}\n')
            chart.declaration(DECLARATION.replace("bindings: true", "bindings: false"))
            report = chart.check()
            self.assertEqual(report.errors, [])

    def test_a_chart_with_no_declaration_is_not_checked(self):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Chart(Path(workspace), '{{- define "fixture.derivedConfig" -}}\n{{- end -}}\n')
            (chart.dir / "config-contract.yaml").unlink()
            report = chart.check()
            self.assertEqual(report.errors, [])


if __name__ == "__main__":
    unittest.main()
