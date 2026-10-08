"""Tests for compare-renders.py, against two temporary render directories."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "compare-renders.py"

spec = importlib.util.spec_from_file_location("compare_renders", SCRIPT)
compare_renders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_renders)

STATEFULSET = """\
---
# Source: demo/templates/valkey.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: demo-valkey
  namespace: default
spec:
  template:
    metadata:
      labels:
        app.kubernetes.io/name: demo{version}
"""

SECRET = """\
---
apiVersion: v1
kind: Secret
metadata:
  name: demo
  namespace: default
data:
  {key}: {value}
"""


class CompareRendersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.left = Path(self._tmp.name) / "left"
        self.right = Path(self._tmp.name) / "right"
        self.left.mkdir()
        self.right.mkdir()

    def write(self, side: Path, name: str, text: str) -> None:
        (side / name).write_text(text, encoding="utf-8")

    def compare(self) -> list[str]:
        return compare_renders.compare(self.left, self.right, "helm 3", "helm 4")

    def test_identical_renders_compare_clean(self) -> None:
        self.write(self.left, "demo.yaml", STATEFULSET.format(version=""))
        self.write(self.right, "demo.yaml", STATEFULSET.format(version=""))
        self.assertEqual(self.compare(), [])

    def test_blank_lines_and_comments_are_not_a_difference(self) -> None:
        self.write(self.left, "demo.yaml", STATEFULSET.format(version=""))
        self.write(
            self.right, "demo.yaml", "\n\n" + STATEFULSET.format(version="").replace("\n", "\n\n")
        )
        self.assertEqual(self.compare(), [])

    def test_a_label_only_one_engine_renders_is_reported(self) -> None:
        version = "\n        app.kubernetes.io/version: 3.3.0"
        self.write(self.left, "demo.yaml", STATEFULSET.format(version=""))
        self.write(self.right, "demo.yaml", STATEFULSET.format(version=version))
        report = self.compare()
        self.assertIn("demo.yaml: StatefulSet default/demo-valkey: differs", report)
        self.assertTrue(any("+" in line and "app.kubernetes.io/version" in line for line in report))

    def test_secret_values_are_masked_but_keys_compared(self) -> None:
        self.write(self.left, "demo.yaml", SECRET.format(key="pepper", value="YWFh"))
        self.write(self.right, "demo.yaml", SECRET.format(key="pepper", value="YmJi"))
        self.assertEqual(self.compare(), [], "a generated value differs on every render")
        self.write(self.right, "demo.yaml", SECRET.format(key="salt", value="YmJi"))
        self.assertIn("demo.yaml: Secret default/demo: differs", self.compare())

    def test_an_object_or_file_only_one_side_renders_is_reported(self) -> None:
        self.write(self.left, "demo.yaml", STATEFULSET.format(version="") + SECRET.format(
            key="k", value="dg=="
        ))
        self.write(self.right, "demo.yaml", STATEFULSET.format(version=""))
        self.write(self.right, "extra.yaml", STATEFULSET.format(version=""))
        report = self.compare()
        self.assertIn("demo.yaml: Secret default/demo: only rendered by helm 3", report)
        self.assertIn("extra.yaml: not rendered by helm 3", report)


if __name__ == "__main__":
    unittest.main()
