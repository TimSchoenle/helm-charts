"""Tests for bump-common-version.py, against a throwaway git repository.

Like the chart version bump, the script is a comparison with a base branch, so each test commits a
library, two consumers and a fixture, then edits the working tree the way a branch would and runs
the script from inside it.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bump-common-version.py"

spec = importlib.util.spec_from_file_location("bump_common_version", SCRIPT)
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)

LIBRARY = """\
apiVersion: v2
name: common
type: library
version: 2.4.1
"""

CONSUMER = """\
name: {name}
version: 1.2.3
apiVersion: v2
dependencies:
  - name: common
    version: 2.4.1
    repository: "file://../common"
  - name: other
    version: 9.9.9
    repository: https://example.invalid/charts
"""

FIXTURE = """\
apiVersion: v2
name: common-fixture
type: application
version: 0.0.0
dependencies:
  - name: common
    version: 2.4.1
    repository: "file://../../../charts/common"
"""

NAMES = '{{{{- define "common.name" -}}}}{body}{{{{- end -}}}}\n'


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class BumpCommonVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "commit.gpgsign", "false")
        git(self.root, "config", "core.autocrlf", "false")
        self.write("charts/common/Chart.yaml", LIBRARY)
        self.write("charts/common/templates/_names.tpl", NAMES.format(body=""))
        self.write("charts/alpha/Chart.yaml", CONSUMER.format(name="alpha"))
        self.write("charts/beta/Chart.yaml", CONSUMER.format(name="beta"))
        self.write("testdata/fixture/Chart.yaml", FIXTURE)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def read(self, relative: str) -> str:
        return (self.root / relative).read_bytes().decode("utf-8")

    def run_script(self, *extra: str) -> tuple[int, str]:
        argv = ["bump-common-version.py", "--base", "main", "--fixture", "testdata/fixture"]
        out = io.StringIO()
        old_argv = sys.argv
        sys.argv = [*argv, *extra]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                bump.main()
            code = 0
        except SystemExit as exit_:
            code = int(exit_.code or 0)
        finally:
            sys.argv = old_argv
        return code, out.getvalue()

    def edit_library(self) -> None:
        self.write("charts/common/templates/_names.tpl", NAMES.format(body="x"))

    def test_untouched_library_passes_the_check(self) -> None:
        code, out = self.run_script("--check")
        self.assertEqual(code, 0, out)
        self.assertIn("in step", out)

    def test_library_edit_without_a_bump_fails_the_check(self) -> None:
        self.edit_library()
        code, out = self.run_script("--check")
        self.assertEqual(code, 1)
        self.assertIn("common: version 2.4.1 -> 2.4.2", out)
        self.assertIn("charts/common/templates/_names.tpl", out)
        self.assertEqual(self.read("charts/common/Chart.yaml"), LIBRARY, "--check must not write")

    def test_library_edit_bumps_library_repins_and_bumps_consumers(self) -> None:
        self.edit_library()
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn("version: 2.4.2\n", self.read("charts/common/Chart.yaml"))
        for name in ("alpha", "beta"):
            text = self.read(f"charts/{name}/Chart.yaml")
            self.assertIn("\nversion: 1.2.4\n", text)
            self.assertIn("    version: 2.4.2\n", text)
            self.assertIn("    version: 9.9.9\n", text, "another dependency must not be touched")
        fixture = self.read("testdata/fixture/Chart.yaml")
        self.assertIn("    version: 2.4.2\n", fixture)
        self.assertIn("\nversion: 0.0.0\n", fixture, "the fixture is never versioned")
        self.assertEqual(self.run_script("--check")[0], 0)

    def test_kind_selects_how_far_the_library_moves(self) -> None:
        self.edit_library()
        self.run_script("--kind", "minor")
        self.assertIn("version: 2.5.0\n", self.read("charts/common/Chart.yaml"))
        self.assertIn("    version: 2.5.0\n", self.read("charts/alpha/Chart.yaml"))

    def test_hand_bumped_library_is_kept_and_consumers_follow(self) -> None:
        self.edit_library()
        self.write("charts/common/Chart.yaml", LIBRARY.replace("2.4.1", "3.0.0"))
        code, _ = self.run_script("--check")
        self.assertEqual(code, 1, "consumers still pin 2.4.1")
        self.run_script()
        self.assertIn("version: 3.0.0\n", self.read("charts/common/Chart.yaml"))
        self.assertIn("    version: 3.0.0\n", self.read("charts/beta/Chart.yaml"))
        self.assertIn("\nversion: 1.2.4\n", self.read("charts/beta/Chart.yaml"))

    def test_hand_bumped_consumer_is_not_bumped_again(self) -> None:
        self.edit_library()
        self.write(
            "charts/alpha/Chart.yaml",
            CONSUMER.format(name="alpha").replace("version: 1.2.3", "version: 1.3.0"),
        )
        self.run_script()
        self.assertIn("\nversion: 1.3.0\n", self.read("charts/alpha/Chart.yaml"))

    def test_repinned_consumer_without_its_own_bump_fails_the_check(self) -> None:
        self.write("charts/common/Chart.yaml", LIBRARY.replace("2.4.1", "2.4.2"))
        self.edit_library()
        for name in ("alpha", "beta"):
            self.write(
                f"charts/{name}/Chart.yaml",
                CONSUMER.format(name=name).replace("    version: 2.4.1", "    version: 2.4.2"),
            )
        self.write("testdata/fixture/Chart.yaml", FIXTURE.replace("2.4.1", "2.4.2"))
        code, out = self.run_script("--check")
        self.assertEqual(code, 1)
        self.assertIn("alpha: version 1.2.3 -> 1.2.4", out)

    def test_stale_pin_alone_fails_the_check(self) -> None:
        self.write(
            "charts/alpha/Chart.yaml",
            CONSUMER.format(name="alpha").replace("    version: 2.4.1", "    version: 2.4.0"),
        )
        code, out = self.run_script("--check")
        self.assertEqual(code, 1)
        self.assertIn("alpha: common pin 2.4.0 -> 2.4.1", out)

    def test_run_is_idempotent(self) -> None:
        self.edit_library()
        self.run_script()
        first = {name: self.read(f"charts/{name}/Chart.yaml") for name in ("common", "alpha")}
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn("in step", out)
        for name, text in first.items():
            self.assertEqual(self.read(f"charts/{name}/Chart.yaml"), text)

    def test_untracked_library_file_counts_as_a_change(self) -> None:
        self.write("charts/common/templates/_new.tpl", NAMES.format(body="new"))
        code, out = self.run_script("--check")
        self.assertEqual(code, 1)
        self.assertIn("charts/common/templates/_new.tpl", out)

    def test_crlf_chart_keeps_its_line_endings(self) -> None:
        self.write("charts/beta/Chart.yaml", CONSUMER.format(name="beta").replace("\n", "\r\n"))
        git(self.root, "commit", "-qam", "crlf")
        self.edit_library()
        self.run_script()
        text = self.read("charts/beta/Chart.yaml")
        self.assertIn("\r\nversion: 1.2.4\r\n", text)
        self.assertIn("    version: 2.4.2\r\n", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))


if __name__ == "__main__":
    unittest.main()
