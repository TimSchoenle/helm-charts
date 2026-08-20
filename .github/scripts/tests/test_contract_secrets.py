#!/usr/bin/env python3
"""The credential surface, over hand-built volumes and contracts rather than over a render.

Every rule in `config_secrets.py` is a decision about *which* report a fact belongs in, and each
of them has a plausible-looking wrong answer that a run against this repository would not expose:
the charts here happen to be clean of unclaimed names, so a test that only ran the reconciler over
`rendered/` would pass whether the unclaimed branch worked or not.

Four rules are worth stating as tests, because getting any of them wrong turns the report into
noise nobody reads:

  the Secret-only read      `config.toml` arrives from a ConfigMap in the same projected volume,
                            and counting it as a credential would make every pod look wrong

  the boundary match        `internal__tokens__api` contains `internal__token`, so a substring
                            search calls the retired tier-wide key deliverable on the strength of
                            an unrelated one, and downgrades the only finding that mattered

  gate 3's reach            a file that gate 3 already judged must produce no second line here,
                            and the condition is both halves: a resolved secrets directory *and*
                            a container the declaration lists as a consumer

  own contract, not union   a file a sibling image declares is over-projection; a file nobody
                            declares is unclaimed. Scoring both against the union would collapse
                            the distinction the whole report rests on
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_contract as cc  # noqa: E402
from config_declaration import Consumer, Declaration, Document, Source  # noqa: E402
from config_gate_container import ContainerView  # noqa: E402
from config_secrets import (  # noqa: E402
    Declared,
    Ledger,
    Reconciler,
    credentials,
    document_paths,
    names_credential,
    secret_file_names,
)

FIXTURES = SCRIPTS.parent / "testdata" / "contracts"


def _load(name: str, filename: str):
    """Import a hyphenated entry point, the way `test_contract_refresh` already does."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = _load("config_secrets_entry", "config-secrets.py")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def union(name: str, contract: dict | None = None) -> cc.Union:
    return cc.union_contracts([(name, contract if contract is not None else fixture(name))])


def with_plain_text_key(name: str = "api") -> dict:
    """The `api` fixture plus one `text` key the image does *not* consider secret.

    No fixture on disk carries that combination, and it is the exact shape of the two keys this
    repository's `tankovault` chart elevates — a value the image treats as ordinary configuration
    and the chart delivers as a Secret file anyway.
    """
    contract = copy.deepcopy(fixture(name))
    contract["schema"]["keys"].append(
        {
            "path": "email.username",
            "env": "FIXTURE_EMAIL__USERNAME",
            "env_file": "FIXTURE_EMAIL__USERNAME_FILE",
            "secrets_file": "email__username",
            "docs": "The mailbox login.",
            "ty": "String",
            "values": [],
            "constraint": {"type": "string"},
            "text_constraint": None,
            "text_form": "text",
            "aliases": [],
            "default": None,
            "default_value": None,
            "note": None,
            "required": False,
            "secret": False,
            "reserved": False,
        }
    )
    return contract


def declaration(chart: str = "fixture") -> Declaration:
    return Declaration(
        chart=chart,
        path=Path(chart),
        documents=[],
        reason=None,
        unconfigured=[],
        bindings=False,
        unbound=[],
    )


def container(name: str = "app", env: dict | None = None, mounts: list | None = None) -> dict:
    return {
        "name": name,
        "image": "example/app:v1@sha256:" + "0" * 64,
        "env": [{"name": key, "value": value} for key, value in (env or {}).items()],
        "volumeMounts": list(mounts or []),
    }


# --------------------------------------------------------------------------------------------
# What a volume delivers
# --------------------------------------------------------------------------------------------


class TestSecretFileNames(unittest.TestCase):
    def test_a_projected_volume_reports_only_its_secret_sources(self):
        spec = {
            "volumes": [
                {
                    "name": "config",
                    "projected": {
                        "sources": [
                            {"configMap": {"name": "cm", "items": [{"key": "config.toml"}]}},
                            {"secret": {"name": "s", "items": [{"key": "k", "path": "db__url"}]}},
                        ]
                    },
                }
            ]
        }
        self.assertEqual(secret_file_names([], spec, "config"), ["db__url"])

    def test_a_source_without_items_presents_every_key_of_the_rendered_secret(self):
        spec = {"volumes": [{"name": "creds", "secret": {"secretName": "existing"}}]}
        manifests = [
            {
                "kind": "Secret",
                "metadata": {"name": "existing"},
                "data": {"database__url": "eA=="},
                "stringData": {"auth__jwt_secret": "x"},
            }
        ]
        self.assertEqual(
            secret_file_names(manifests, spec, "creds"), ["auth__jwt_secret", "database__url"]
        )

    def test_a_secret_the_operator_supplies_contributes_no_names(self):
        spec = {"volumes": [{"name": "creds", "secret": {"secretName": "not-rendered"}}]}
        self.assertEqual(secret_file_names([], spec, "creds"), [])

    def test_a_volume_the_pod_does_not_have_is_not_an_error(self):
        self.assertEqual(secret_file_names([], {"volumes": []}, "missing"), [])


class TestDocumentPaths(unittest.TestCase):
    def test_every_table_and_every_leaf_is_a_path(self):
        paths = document_paths({"auth": {"jwt_secret": "x"}, "port": 8080})
        self.assertEqual(paths, {"auth", "auth.jwt_secret", "port"})


# --------------------------------------------------------------------------------------------
# The inventory
# --------------------------------------------------------------------------------------------


def declared(chart="c", document="d", contract="c/contracts/a.json", image="a", required=False):
    return Declared(
        chart=chart,
        document=document,
        contract=contract,
        image=image,
        path="database.url",
        secrets_file="database__url",
        env="FIXTURE_DATABASE__URL",
        env_file="FIXTURE_DATABASE__URL_FILE",
        text_form="text",
        required=required,
        summary="The connection string.",
    )


class TestCredentials(unittest.TestCase):
    def test_two_images_reading_one_key_are_one_row_naming_both(self):
        rows = credentials(
            [
                declared(document="api", contract="c/contracts/api.json", image="api"),
                declared(document="worker", contract="c/contracts/worker.json", image="worker"),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].images, ("api", "worker"))
        self.assertEqual(rows[0].documents, ("api", "worker"))

    def test_required_unions_the_way_the_contract_model_unions_it(self):
        rows = credentials([declared(image="a"), declared(image="b", required=True)])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].required)

    def test_two_spellings_of_one_path_stay_two_rows(self):
        renamed = replace(declared(image="b"), secrets_file="db__url")
        rows = credentials([declared(image="a"), renamed])
        self.assertEqual(len(rows), 2)


class TestNamesCredential(unittest.TestCase):
    """The boundary match, which is what keeps a nested spelling from masking its own prefix."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.chart = Path(self.work.name)
        self.addCleanup(self.work.cleanup)
        (self.chart / "contracts").mkdir()
        (self.chart / "templates").mkdir()

    def credential(self, path="internal.token", secrets_file="internal__token"):
        rows = credentials(
            [
                Declared(
                    chart="c",
                    document="d",
                    contract="c/contracts/a.json",
                    image="a",
                    path=path,
                    secrets_file=secrets_file,
                    env="TANKOVAULT_INTERNAL__TOKEN",
                    env_file="TANKOVAULT_INTERNAL__TOKEN_FILE",
                    text_form="text",
                    required=False,
                    summary="",
                )
            ]
        )
        return rows[0]

    def test_a_longer_spelling_that_contains_the_shorter_one_does_not_name_it(self):
        (self.chart / "templates" / "_secrets.tpl").write_text(
            'key: {{ printf "internal__tokens__%s" . }}\n{{ .Values.internal.tokens }}\n',
            encoding="utf-8",
        )
        self.assertEqual(names_credential(self.chart, self.credential()), ())

    def test_the_spelling_itself_names_it(self):
        (self.chart / "values.yaml").write_text("# internal.token is gone\n", encoding="utf-8")
        self.assertEqual(names_credential(self.chart, self.credential()), ("values.yaml",))

    def test_the_vendored_contracts_are_not_evidence_that_the_chart_delivers_it(self):
        (self.chart / "contracts" / "a.json").write_text(
            '{"secrets_file": "internal__token"}', encoding="utf-8"
        )
        self.assertEqual(names_credential(self.chart, self.credential()), ())


# --------------------------------------------------------------------------------------------
# The reconciliation
# --------------------------------------------------------------------------------------------


class TestScanFile(unittest.TestCase):
    """One mounted file at a time, which is where every one of the three reports is decided."""

    def setUp(self):
        self.mine = union("worker")
        self.sibling = union("api")
        self.contracts = [("worker", self.mine), ("api", self.sibling)]
        self.reconciler = Reconciler(Path("charts"), Path("rendered"))
        self.ledger = Ledger()

    def scan(
        self,
        file_name: str,
        mount_path: str = "/secrets",
        secrets_dir: str | None = "/secrets",
        judged: set | None = None,
        mine: cc.Union | None = None,
        env: dict | None = None,
    ) -> None:
        chosen = mine or self.mine
        view = ContainerView.read(container(env=env or {}), chosen)
        self.reconciler.scan_file(
            declaration(),
            self.contracts,
            chosen,
            chosen.sources[0],
            view,
            secrets_dir,
            "default.yaml",
            "Deployment app",
            "app",
            mount_path,
            file_name,
            judged if judged is not None else set(),
            self.ledger,
        )

    def test_a_key_the_container_reads_is_recorded_as_supplied_and_reported_nowhere(self):
        self.scan("database__url")
        self.assertEqual(
            self.ledger.supplied, {("worker", "database.url"): {"the secrets directory"}}
        )
        self.assertEqual(self.reconciler.surface.unclaimed, [])
        self.assertEqual(self.reconciler.surface.over_projected, [])

    def test_a_file_outside_the_secrets_directory_supplies_nothing_without_indirection(self):
        self.scan("database__url", mount_path="/elsewhere")
        self.assertEqual(self.ledger.supplied, {})

    def test_a_file_named_by_a_file_variable_supplies_its_key_wherever_it_is_mounted(self):
        self.scan(
            "database__url",
            mount_path="/elsewhere",
            env={"FIXTURE_DATABASE__URL_FILE": "/elsewhere/database__url"},
        )
        self.assertEqual(
            self.ledger.supplied, {("worker", "database.url"): {"`_FILE` indirection"}}
        )

    def test_a_sibling_images_key_is_over_projection_rather_than_an_unknown_name(self):
        self.scan("auth__session_ttl", secrets_dir="/other")
        self.assertEqual(self.reconciler.surface.unclaimed, [])
        self.assertEqual(len(self.reconciler.surface.over_projected), 1)
        self.assertEqual(self.reconciler.surface.over_projected[0].file_name, "auth__session_ttl")

    def test_a_name_no_contract_of_the_chart_spells_is_unclaimed(self):
        self.scan("nothing__spells_this", secrets_dir="/other")
        self.assertEqual(self.reconciler.surface.over_projected, [])
        self.assertEqual(len(self.reconciler.surface.unclaimed), 1)

    def test_a_file_gate_three_already_judged_produces_no_second_line(self):
        self.scan("nothing__spells_this", judged={("Deployment app", "app")})
        self.assertEqual(self.reconciler.surface.unclaimed, [])
        self.assertEqual(self.reconciler.surface.over_projected, [])

    def test_gate_three_needs_both_halves_before_it_owns_the_verdict(self):
        # The declaration lists the container, but the file is outside the secrets directory that
        # gate 3 resolves — so gate 3 never inspected it and this report must still speak.
        self.scan(
            "nothing__spells_this",
            mount_path="/elsewhere",
            judged={("Deployment app", "app")},
        )
        self.assertEqual(len(self.reconciler.surface.unclaimed), 1)

    def test_a_non_secret_text_key_delivered_as_a_secret_is_recorded_as_policy(self):
        mine = union("api", with_plain_text_key())
        self.contracts = [("api", mine)]
        self.scan("email__username", mine=mine)
        self.assertEqual(
            list(self.ledger.elevated), [("email.username", "email__username", "text")]
        )
        self.assertEqual(self.reconciler.surface.over_projected, [])

    def test_a_non_secret_key_no_file_can_supply_is_left_to_gate_three(self):
        # `worker.concurrency` is an integer, and gate 3 already refuses the mount at length.
        self.scan("worker__concurrency")
        self.assertEqual(self.ledger.elevated, {})


class TestGateThreeReach(unittest.TestCase):
    """Which containers `just check-config` already opens, recomputed from the declaration."""

    def setUp(self):
        self.reconciler = Reconciler(Path("charts"), Path("rendered"))
        self.manifests = [
            {
                "kind": "Deployment",
                "metadata": {"name": "app", "labels": {"app.kubernetes.io/component": "api"}},
                "spec": {
                    "template": {
                        "spec": {
                            "initContainers": [{"name": "migrate"}],
                            "containers": [{"name": "api"}],
                        }
                    }
                },
            }
        ]

    def declaration_naming(self, *containers: str) -> Declaration:
        document = Document(
            name="api",
            source=Source(kind="ConfigMap", selector={}, key="config.toml", format="toml"),
            images=[],
            consumers=[
                Consumer(
                    kind="Deployment",
                    selector={"app.kubernetes.io/component": "api"},
                    containers=list(containers),
                )
            ],
            exempt=[],
        )
        return Declaration(
            chart="c",
            path=Path("c"),
            documents=[document],
            reason=None,
            unconfigured=[],
            bindings=False,
            unbound=[],
        )

    def test_a_declared_container_is_within_reach(self):
        reach = self.reconciler.gate_three_reach(self.declaration_naming("api"), self.manifests)
        self.assertEqual(reach, {("Deployment app", "api")})

    def test_an_init_container_no_declaration_names_is_not(self):
        reach = self.reconciler.gate_three_reach(self.declaration_naming("api"), self.manifests)
        self.assertNotIn(("Deployment app", "migrate"), reach)


# --------------------------------------------------------------------------------------------
# The rendering
# --------------------------------------------------------------------------------------------


class TestGrouping(unittest.TestCase):
    def test_one_container_over_projecting_several_files_is_one_row(self):
        from config_secrets import Mount

        def mount(file_name: str, values_file: str) -> Mount:
            return Mount(
                chart="c",
                values_file=values_file,
                workload="Deployment app",
                container="migrate",
                image="bootstrap",
                mount_path="/secrets",
                file_name=file_name,
                judged_by_gate_three=False,
            )

        rows = entry._by_container(
            [mount("a", "one.yaml"), mount("b", "one.yaml"), mount("a", "two.yaml")]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4], ("a", "b"))
        self.assertEqual(rows[0][5], ("one.yaml", "two.yaml"))


class TestCommonPrefix(unittest.TestCase):
    def test_the_dialect_prefix_is_what_every_spelling_shares(self):
        self.assertEqual(entry._common_prefix(["FIXTURE_A__B", "FIXTURE_C__D"]), "FIXTURE_")

    def test_nothing_shared_is_no_prefix(self):
        self.assertEqual(entry._common_prefix(["A_ONE", "B_TWO"]), "")


if __name__ == "__main__":
    unittest.main()
