#!/usr/bin/env python3
"""Gates 2 and 3, over hand-built containers rather than over a render.

Each gate is a small class taking manifests and a contract and returning findings, so a test
constructs the container it wants and reads the list back — no chart, no `helm template`, no
cluster. The end-to-end proof that the gates fire against a real chart is the falsification pass
in the pull request; this is the proof that each rule is the one that fires, and that a rule
which should stay quiet does.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_contract as cc
from config_gate_container import (
    ContainerView,
    EnvironmentGate,
    FileGate,
    LayerCollisionGate,
    ServiceLinkGate,
    Suppliers,
    check_container,
)
from config_report import ERROR, WARNING

FIXTURES = Path(__file__).resolve().parents[2] / "testdata" / "contracts"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def union(*names: str) -> cc.Union:
    return cc.union_contracts([(name, fixture(name)) for name in names])


def container(env=None, mounts=None, name="app"):
    return {
        "name": name,
        "image": "example/app:v1@sha256:" + "0" * 64,
        "env": [{"name": key, "value": value} for key, value in (env or {}).items()],
        "volumeMounts": list(mounts or []),
    }


def messages(findings):
    return " | ".join(finding.message for finding in findings)


class TestEnvironmentGate(unittest.TestCase):
    def setUp(self):
        self.union = union("api")
        self.gate = EnvironmentGate()

    def check(self, env):
        view = ContainerView.read(container(env), self.union)
        return self.gate.check(view, self.union, Suppliers(), set())

    def test_a_correct_environment_is_silent(self):
        self.assertEqual(self.check({"FIXTURE_AUTH__SESSION_TTL": "60", "PORT": "8080"}), [])

    def test_a_prefixed_variable_spelling_nothing_is_reported(self):
        findings = self.check({"FIXTURE_NOPE": "1"})
        self.assertEqual(len(findings), 1)
        self.assertIn("FIXTURE_NOPE", findings[0].message)

    def test_a_near_miss_is_offered_a_suggestion(self):
        findings = self.check({"FIXTURE_AUTH__SESSION_TTLS": "60"})
        self.assertIn("did you mean", messages(findings))

    def test_a_value_of_the_wrong_form_is_reported(self):
        findings = self.check({"PORT": "http"})
        self.assertEqual(len(findings), 1)
        self.assertIn("http", findings[0].message)

    def test_a_value_outside_its_bound_is_reported(self):
        findings = self.check({"PORT": "99999"})
        self.assertEqual(len(findings), 1)
        self.assertIn("65535", findings[0].message)

    def test_one_value_produces_one_line(self):
        # "not an integer" followed by "not below 65535" says nothing the first did not, and the
        # second would be reporting on a parse that never happened.
        self.assertEqual(len(self.check({"PORT": "http"})), 1)

    def test_a_structured_key_without_brackets_is_reported(self):
        findings = self.check({"FIXTURE_GITHUB__REPOS": "a,b"})
        self.assertEqual(len(findings), 1)
        self.assertIn("brackets", findings[0].message)

    def test_a_structured_key_with_brackets_is_silent(self):
        self.assertEqual(self.check({"FIXTURE_GITHUB__REPOS": '["a","b"]'}), [])

    def test_an_ignored_variable_is_silent(self):
        self.assertEqual(self.check({"KUBERNETES_SERVICE_HOST": "10.0.0.1"}), [])

    def test_an_unaccounted_variable_follows_the_unknown_policy(self):
        self.assertEqual(self.check({"NOVEL": "1"})[0].level, ERROR)

        permissive = union("worker")  # `external.unknown` is `warn`
        view = ContainerView.read(container({"NOVEL": "1"}), permissive)
        findings = self.gate.check(view, permissive, Suppliers(), set())
        self.assertEqual(findings[0].level, WARNING)

    def test_a_value_from_entry_is_classified_but_not_value_checked(self):
        view = ContainerView.read(
            {
                "name": "app",
                "env": [{"name": "PORT", "valueFrom": {"fieldRef": {"fieldPath": "x"}}}],
            },
            self.union,
        )
        self.assertEqual(self.gate.check(view, self.union, Suppliers(), set()), [])

    def test_an_exemption_relaxes_the_gate(self):
        view = ContainerView.read(container({"FIXTURE_NOPE": "1"}), self.union)
        self.assertEqual(self.gate.check(view, self.union, Suppliers(), {"env"}), [])


class TestPerImageScope(unittest.TestCase):
    """A variable only a sibling image reads is the defect gate 2 exists for."""

    def test_the_union_would_hide_what_one_images_contract_catches(self):
        merged = union("api", "worker")
        alone = union("api")
        env = {"FIXTURE_WORKER__CONCURRENCY": "4"}

        against_union = EnvironmentGate().check(
            ContainerView.read(container(env), merged), merged, Suppliers(), set()
        )
        against_own = EnvironmentGate().check(
            ContainerView.read(container(env), alone), alone, Suppliers(), set()
        )

        self.assertEqual(against_union, [])
        self.assertEqual(len(against_own), 1)
        self.assertIn("FIXTURE_WORKER__CONCURRENCY", against_own[0].message)


class TestFileGate(unittest.TestCase):
    def setUp(self):
        self.union = union("api")

    def run_gate(self, env, mounts, volumes, manifests=()):
        spec = {"volumes": list(volumes)}
        view = ContainerView.read(container(env, mounts), self.union)
        return FileGate().check(list(manifests), spec, view, self.union, Suppliers(), set())

    def secrets(self, items):
        return {
            "name": "secrets",
            "projected": {"sources": [{"secret": {"name": "app", "items": items}}]},
        }

    def test_a_correctly_named_secret_file_is_silent(self):
        findings = self.run_gate(
            {"FIXTURE_SECRETS_DIR": "/secrets"},
            [{"name": "secrets", "mountPath": "/secrets"}],
            [self.secrets([{"key": "database__url", "path": "database__url"}])],
        )
        self.assertEqual(findings, [])

    def test_a_secret_file_spelling_no_key_is_reported(self):
        findings = self.run_gate(
            {"FIXTURE_SECRETS_DIR": "/secrets"},
            [{"name": "secrets", "mountPath": "/secrets"}],
            [self.secrets([{"key": "database__nope", "path": "database__nope"}])],
        )
        self.assertIn("database__nope", messages(findings))
        self.assertIn("spells no key", messages(findings))

    def test_a_secret_file_for_a_non_text_key_is_reported(self):
        findings = self.run_gate(
            {"FIXTURE_SECRETS_DIR": "/secrets"},
            [{"name": "secrets", "mountPath": "/secrets"}],
            [self.secrets([{"key": "auth__session_ttl", "path": "auth__session_ttl"}])],
        )
        self.assertIn("cannot be supplied by a file", messages(findings))

    def test_key_named_files_mounted_where_nothing_reads_them_are_reported(self):
        # The worse half of the defect: the files exist, the loader never looks, and every
        # credential falls back to a default. Nothing renders wrong and nothing fails to start.
        findings = self.run_gate(
            {},
            [{"name": "secrets", "mountPath": "/elsewhere"}],
            [self.secrets([{"key": "database__url", "path": "database__url"}])],
        )
        self.assertIn("nothing reads it", messages(findings))

    def test_a_file_read_by_indirection_is_not_reported(self):
        findings = self.run_gate(
            {"FIXTURE_DATABASE__URL_FILE": "/elsewhere/database__url"},
            [{"name": "secrets", "mountPath": "/elsewhere"}],
            [self.secrets([{"key": "database__url", "path": "database__url"}])],
        )
        self.assertEqual(findings, [])

    def test_an_indirection_variable_pointing_outside_every_mount_is_reported(self):
        findings = self.run_gate({"FIXTURE_DATABASE__URL_FILE": "/nowhere/x"}, [], [])
        self.assertIn("not inside any volume", messages(findings))

    def test_an_indirection_variable_for_a_non_text_key_is_reported(self):
        findings = self.run_gate(
            {"FIXTURE_AUTH__SESSION_TTL_FILE": "/secrets/x"},
            [{"name": "secrets", "mountPath": "/secrets"}],
            [],
        )
        self.assertIn("cannot be supplied by a file", messages(findings))

    def test_a_loader_path_pointing_outside_every_mount_is_reported(self):
        findings = self.run_gate({"FIXTURE_CONFIG": "/etc/app"}, [], [])
        self.assertIn("not inside any volume", messages(findings))

    def test_a_secrets_dir_below_a_mount_point_warns_that_it_could_not_look(self):
        findings = self.run_gate(
            {"FIXTURE_SECRETS_DIR": "/mnt/sub"},
            [{"name": "secrets", "mountPath": "/mnt"}],
            [self.secrets([{"key": "database__url", "path": "database__url"}])],
        )
        self.assertTrue(any(finding.level == WARNING for finding in findings))

    def test_a_source_without_items_is_followed_back_to_the_rendered_secret(self):
        findings = self.run_gate(
            {"FIXTURE_SECRETS_DIR": "/secrets"},
            [{"name": "secrets", "mountPath": "/secrets"}],
            [{"name": "secrets", "projected": {"sources": [{"secret": {"name": "app"}}]}}],
            manifests=[
                {
                    "kind": "Secret",
                    "metadata": {"name": "app"},
                    "data": {"database__nope": "eA=="},
                }
            ],
        )
        self.assertIn("database__nope", messages(findings))


class TestLayerCollision(unittest.TestCase):
    """The pair `ShadowPolicy::Reject` refuses at boot, which nothing else here can see."""

    def test_one_key_from_two_layers_is_reported(self):
        merged = union("api")
        findings = check_container(
            [
                {
                    "kind": "Secret",
                    "metadata": {"name": "app"},
                    "data": {"database__url": "eA=="},
                }
            ],
            {
                "volumes": [
                    {
                        "name": "secrets",
                        "projected": {
                            "sources": [
                                {
                                    "secret": {
                                        "name": "app",
                                        "items": [
                                            {"key": "database__url", "path": "database__url"}
                                        ],
                                    }
                                }
                            ]
                        },
                    }
                ]
            },
            container(
                {"FIXTURE_SECRETS_DIR": "/secrets", "FIXTURE_DATABASE__URL": "postgres://x"},
                [{"name": "secrets", "mountPath": "/secrets"}],
            ),
            merged,
            set(),
        )
        self.assertIn("supplied by", messages(findings))
        self.assertIn("database.url", messages(findings))

    def test_one_key_from_one_layer_is_silent(self):
        suppliers = Suppliers()
        suppliers.add("database.url", "the environment")
        view = ContainerView.read(container(), union("api"))
        self.assertEqual(LayerCollisionGate().check(view, suppliers, set()), [])


class TestServiceLinks(unittest.TestCase):
    def test_a_pod_without_the_switch_is_reported(self):
        findings = ServiceLinkGate().check({}, union("api"))
        self.assertEqual(len(findings), 1)
        self.assertIn("enableServiceLinks", findings[0].message)

    def test_a_pod_with_the_switch_is_silent(self):
        self.assertEqual(ServiceLinkGate().check({"enableServiceLinks": False}, union("api")), [])

    def test_setting_it_true_is_not_setting_it(self):
        self.assertEqual(
            len(ServiceLinkGate().check({"enableServiceLinks": True}, union("api"))), 1
        )


if __name__ == "__main__":
    unittest.main()
