"""Tests for bump-chart-versions.py, against a throwaway git repository.

The script's whole job is a comparison with a base branch, so the tests build one: a chart is
committed, a branch moves a pin, and the script is run from inside the working tree the way CI
runs it.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bump-chart-versions.py"

spec = importlib.util.spec_from_file_location("bump_chart_versions", SCRIPT)
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64

CHART = """\
name: demo
version: 1.2.3
appVersion: 3.0.5
apiVersion: v2
dependencies:
  - name: common
    version: 2.4.1
"""

VALUES = """\
image:
  repository: ghcr.io/example/demo
  # -- The tag.
  tag: {main}
postgres:
  image:
    repository: postgres
    tag: {db}
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class BumpChartVersionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "commit.gpgsign", "false")
        git(self.root, "config", "core.autocrlf", "false")
        self.chart_dir = self.root / "charts" / "demo"
        self.chart_dir.mkdir(parents=True)
        self.write_chart(CHART)
        self.write_values(f"3.0.5@{DIGEST_A}", f"18.6@{DIGEST_A}")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def write_chart(self, text: str) -> None:
        (self.chart_dir / "Chart.yaml").write_bytes(text.encode("utf-8"))

    def write_values(self, main: str, db: str) -> None:
        (self.chart_dir / "values.yaml").write_text(
            VALUES.format(main=main, db=db), encoding="utf-8"
        )

    def run_script(self) -> list[str]:
        return bump.process_chart(Path("charts/demo"), "main")

    def chart_text(self) -> str:
        return (self.chart_dir / "Chart.yaml").read_bytes().decode("utf-8")

    def test_untouched_pins_leave_the_chart_alone(self) -> None:
        self.assertEqual(self.run_script(), [])
        self.assertEqual(self.chart_text(), CHART)

    def test_digest_only_update_is_a_patch(self) -> None:
        self.write_values(f"3.0.5@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.run_script()
        self.assertIn("version: 1.2.4\n", self.chart_text())
        self.assertIn("appVersion: 3.0.5\n", self.chart_text())

    def test_minor_image_update_is_a_patch_and_moves_app_version(self) -> None:
        self.write_values(f"3.1.0@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.run_script()
        text = self.chart_text()
        self.assertIn("version: 1.2.4\n", text)
        self.assertIn("appVersion: 3.1.0\n", text)
        self.assertIn("    version: 2.4.1\n", text, "the dependency version must not be touched")

    def test_major_image_update_is_a_chart_minor(self) -> None:
        self.write_values(f"4.0.0@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.run_script()
        self.assertIn("version: 1.3.0\n", self.chart_text())

    def test_sidecar_update_bumps_version_but_not_app_version(self) -> None:
        self.write_values(f"3.0.5@{DIGEST_A}", f"18.7@{DIGEST_B}")
        self.run_script()
        text = self.chart_text()
        self.assertIn("version: 1.2.4\n", text)
        self.assertIn("appVersion: 3.0.5\n", text)

    def test_already_bumped_chart_is_not_bumped_again(self) -> None:
        self.write_chart(CHART.replace("version: 1.2.3", "version: 1.2.4"))
        self.write_values(f"3.0.5@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.assertEqual(self.run_script(), [])
        self.assertIn("version: 1.2.4\n", self.chart_text())

    def test_run_is_idempotent(self) -> None:
        self.write_values(f"3.0.5@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.run_script()
        first = self.chart_text()
        self.run_script()
        self.assertEqual(self.chart_text(), first)

    def test_app_version_not_tracking_the_image_is_left_alone(self) -> None:
        git(self.root, "checkout", "-q", "-b", "other")
        self.write_chart(CHART.replace("appVersion: 3.0.5", "appVersion: 9.9.9"))
        git(self.root, "commit", "-qam", "diverge")
        git(self.root, "branch", "-f", "main", "other")
        self.write_values(f"3.1.0@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.run_script()
        self.assertIn("appVersion: 9.9.9\n", self.chart_text())

    def test_non_version_tag_is_a_patch(self) -> None:
        self.write_values(f"3.0.5@{DIGEST_A}", f"pg18@{DIGEST_B}")
        self.run_script()
        self.assertIn("version: 1.2.4\n", self.chart_text())

    def test_crlf_line_endings_survive(self) -> None:
        crlf = CHART.replace("\n", "\r\n")
        self.write_chart(crlf)
        git(self.root, "commit", "-qam", "crlf")
        self.write_values(f"3.1.0@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.run_script()
        text = self.chart_text()
        self.assertIn("version: 1.2.4\r\n", text)
        self.assertIn("appVersion: 3.1.0\r\n", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_library_chart_is_skipped(self) -> None:
        self.write_chart(CHART + "type: library\n")
        git(self.root, "commit", "-qam", "library")
        self.write_values(f"3.1.0@{DIGEST_B}", f"18.6@{DIGEST_A}")
        self.assertEqual(self.run_script(), [])

    def test_bump_kind_ignores_new_pins(self) -> None:
        self.assertIsNone(bump.bump_kind({}, {"image": f"1.0.0@{DIGEST_C}"}))


if __name__ == "__main__":
    unittest.main()
