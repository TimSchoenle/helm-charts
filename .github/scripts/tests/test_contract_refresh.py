#!/usr/bin/env python3
"""The declaration, the staleness interlock, and the refresh — against a fake registry.

`refresh-contracts.py` is the one script here that talks to a registry, and no image publishes a
contract yet, so it cannot be exercised against a real one. Every call that would leave this
machine goes through `RegistryClient`, which is exactly what lets the verification order be
tested without one: the fake below records what was asked for and answers with recorded shapes,
and the tests assert that each of the checks refuses what it is supposed to refuse.

That matters more here than anywhere else in this pipeline. A contract that cannot be proven to
belong to the pinned digest is worse than none, because every gate downstream would trust it —
so "verification is skipped" has to be a test failure rather than a thing nobody noticed until
the first real image shipped.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from entry import load  # noqa: E402

import config_contract as cc  # noqa: E402
from config_declaration import (  # noqa: E402
    DeclarationError,
    bind,
    load_declaration,
    resolve_image,
)

FIXTURES = SCRIPTS.parent / "testdata" / "contracts"
DIGEST = "sha256:" + "ab" * 32
OTHER_DIGEST = "sha256:" + "cd" * 32


refresh = load("refresh_contracts", "refresh-contracts.py")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


AMD64 = "sha256:" + "11" * 32
ARM64 = "sha256:" + "22" * 32
LAYER = "sha256:" + "33" * 32


def layer_with(payload: bytes, path: str = "config/contract.json") -> bytes:
    """A gzipped tar layer holding one file, as an image layer actually is."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(path)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return gzip.compress(buffer.getvalue())


class FakeRegistry:
    """Every network answer the refresh can receive, and a record of what it asked.

    Shaped after what `docker.io/timschoenle/portfolio` actually returns: the digest a chart pins
    is an OCI *index*, the labels live on the per-platform manifests one level down, and Docker's
    build attestations ride in the same index with no platform.
    """

    def __init__(self, document: dict | None = None, **overrides):
        self.payload = json.dumps(document or fixture("api")).encode("utf-8")
        self.blob_digest = "sha256:" + hashlib.sha256(self.payload).hexdigest()
        base = {
            cc.LABEL_VERSION: "1",
            cc.LABEL_PATH: "/config/contract.json",
            cc.LABEL_PREFIX: "FIXTURE_",
        }
        base.update(overrides.pop("labels", {}))
        # Per-platform labels; `per_platform_labels` overrides one architecture only.
        self.labels = {AMD64: dict(base), ARM64: dict(base)}
        for platform, extra in (overrides.pop("per_platform_labels", {})).items():
            self.labels[platform] = {**base, **extra}

        self.is_index = overrides.pop("is_index", True)
        self.referrers = overrides.pop("referrers", [{"digest": "sha256:" + "ee" * 32}])
        self.platform_referrers = overrides.pop("platform_referrers", [])
        self.claimed = overrides.pop("claimed", self.blob_digest)
        # The image's own layers. `None` means the image embeds no document.
        self.embedded = overrides.pop("embedded", None)
        self.verified: list[tuple[str, str]] = []
        self.asked: list[str] = []

    def require(self):
        return None

    def verify(self, reference, identity):
        self.verified.append((reference, identity))

    def platforms(self, repository, digest):
        return [AMD64, ARM64] if self.is_index else [digest]

    def image_labels(self, reference):
        self.asked.append(reference)
        return dict(self.labels.get(reference.split("@", 1)[1], {}))

    def discover(self, reference, artifact_type):
        digest = reference.split("@", 1)[1]
        if digest == DIGEST:
            return list(self.referrers)
        return list(self.platform_referrers)

    def manifest(self, reference):
        digest = reference.split("@", 1)[1]
        if digest in (AMD64, ARM64):
            return {"layers": [{"digest": LAYER}]}
        return {"layers": [{"digest": self.claimed}]}

    def blob(self, reference):
        if reference.split("@", 1)[1] == LAYER:
            return layer_with(self.embedded) if self.embedded is not None else b"not-a-tar"
        return self.payload

    def resolve(self, reference):
        return DIGEST


class TestFetchVerification(unittest.TestCase):
    def fetch(self, registry):
        return refresh.fetch_contract(registry, "docker.io/example/app", DIGEST, "signer-regexp")

    def test_a_well_formed_image_is_fetched(self):
        registry = FakeRegistry()
        payload, digest = self.fetch(registry)
        self.assertEqual(json.loads(payload)["schema"]["dialect"]["prefix"], "FIXTURE_")
        self.assertEqual(f"sha256:{digest}", registry.blob_digest)

    def test_the_signature_is_verified_against_the_digest(self):
        registry = FakeRegistry()
        self.fetch(registry)
        self.assertEqual(registry.verified, [(f"docker.io/example/app@{DIGEST}", "signer-regexp")])

    def test_labels_are_read_through_the_index(self):
        # The digest a chart pins is an index and has no config blob of its own; asking it for
        # labels is what a first implementation did, and it fails outright against a real image.
        registry = FakeRegistry()
        self.fetch(registry)
        self.assertEqual(
            registry.asked, [f"docker.io/example/app@{AMD64}", f"docker.io/example/app@{ARM64}"]
        )

    def test_platforms_disagreeing_about_the_labels_is_refused(self):
        registry = FakeRegistry(per_platform_labels={ARM64: {cc.LABEL_PREFIX: "OTHER_"}})
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("per platform", str(raised.exception))

    def test_a_contract_attached_to_a_platform_manifest_is_found(self):
        # Which of the two a producer attaches to is a real choice, and a consumer that only
        # looked at the index would report "no contract" for an image that publishes one.
        registry = FakeRegistry(referrers=[], platform_referrers=[{"digest": "sha256:" + "ee" * 32}])
        payload, _ = self.fetch(registry)
        self.assertEqual(json.loads(payload)["schema"]["dialect"]["prefix"], "FIXTURE_")

    def test_a_single_platform_image_still_works(self):
        registry = FakeRegistry(is_index=False)
        registry.labels[DIGEST] = registry.labels[AMD64]
        payload, _ = self.fetch(registry)
        self.assertEqual(json.loads(payload)["terrace_contract"], 1)

    def test_an_image_with_no_version_label_publishes_no_contract(self):
        registry = FakeRegistry()
        for platform in (AMD64, ARM64):
            del registry.labels[platform][cc.LABEL_VERSION]
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn(cc.LABEL_VERSION, str(raised.exception))

    def test_an_unreadable_contract_version_is_refused(self):
        registry = FakeRegistry(labels={cc.LABEL_VERSION: "2"})  # noqa: E501
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("version 2", str(raised.exception))

    def test_a_blob_that_does_not_match_its_descriptor_is_refused(self):
        # The registry content-addresses the blob, so this is the integrity check — and with the
        # hash label gone it is the only one there is.
        registry = FakeRegistry(claimed="sha256:" + "11" * 32)
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("descriptor", str(raised.exception))

    def test_a_prefix_label_disagreeing_with_the_document_is_refused(self):
        registry = FakeRegistry(labels={cc.LABEL_PREFIX: "OTHER_"})
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("different builds", str(raised.exception))

    def test_an_image_publishing_through_neither_carrier_is_refused(self):
        registry = FakeRegistry(referrers=[], platform_referrers=[], embedded=None)
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("publishes no document", str(raised.exception))

    def test_the_embedded_file_alone_is_enough(self):
        # What `timschoenle/portfolio:v2.6.0` actually does today: the three labels are set and
        # the document is embedded, but nothing is attached to the digest. Reading the image is
        # what makes that image usable rather than a build everyone has to wait on.
        payload = json.dumps(fixture("api")).encode("utf-8")
        registry = FakeRegistry(referrers=[], platform_referrers=[], embedded=payload)
        fetched, digest = self.fetch(registry)
        self.assertEqual(fetched, payload)
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_both_carriers_agreeing_is_accepted(self):
        payload = json.dumps(fixture("api")).encode("utf-8")
        registry = FakeRegistry(embedded=payload)
        registry.payload = payload
        registry.claimed = "sha256:" + hashlib.sha256(payload).hexdigest()
        fetched, _ = self.fetch(registry)
        self.assertEqual(fetched, payload)

    def test_both_carriers_disagreeing_is_refused(self):
        # This is what the dropped `sha256` label used to buy, recovered for free by reading both
        # rather than trusting one: an image whose two halves came from different builds.
        other = fixture("api")
        other["app"]["version"] = "9.9.9"
        registry = FakeRegistry(embedded=json.dumps(other).encode("utf-8"))
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("two different documents", str(raised.exception))

    def test_the_embedded_path_comes_from_the_label(self):
        payload = json.dumps(fixture("api")).encode("utf-8")
        registry = FakeRegistry(
            referrers=[],
            platform_referrers=[],
            embedded=payload,
            labels={cc.LABEL_PATH: "/somewhere/else.json"},
        )
        # The layer holds `config/contract.json`, the label names a different path, so nothing is
        # found — the label is what says where to look and it is not guessed at.
        with self.assertRaises(refresh.RefreshError) as raised:
            self.fetch(registry)
        self.assertIn("publishes no document", str(raised.exception))

    def test_several_attached_contracts_are_refused(self):
        registry = FakeRegistry(
            referrers=[{"digest": "sha256:" + "ee" * 32}, {"digest": "sha256:" + "ff" * 32}]
        )
        with self.assertRaises(refresh.RefreshError):
            self.fetch(registry)

    def test_an_unreadable_envelope_is_refused_before_it_is_written(self):
        broken = fixture("api")
        broken["terrace_contract"] = 99
        with self.assertRaises(cc.ContractError):
            self.fetch(FakeRegistry(broken))


class TestReferrerParsing(unittest.TestCase):
    def test_the_referrers_key(self):
        self.assertEqual(refresh.parse_referrers('{"referrers": [{"digest": "a"}]}'), [{"digest": "a"}])

    def test_the_older_manifests_key(self):
        self.assertEqual(refresh.parse_referrers('{"manifests": [{"digest": "a"}]}'), [{"digest": "a"}])

    def test_a_bare_list(self):
        self.assertEqual(refresh.parse_referrers('[{"digest": "a"}]'), [{"digest": "a"}])


class TestWriteVendored(unittest.TestCase):
    def test_the_wrapper_records_the_digest_it_was_fetched_for(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "server.json"
            payload = json.dumps(fixture("api")).encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()

            self.assertTrue(refresh.write_vendored(path, "docker.io/x/y", DIGEST, payload, digest))
            vendored = cc.load_vendored(path)
            self.assertEqual(vendored.digest, DIGEST)
            self.assertEqual(vendored.image, "docker.io/x/y")
            self.assertEqual(vendored.sha256, digest)

    def test_a_rerun_that_changes_only_the_timestamp_does_not_rewrite(self):
        # `fetched` moves on every run by construction; rewriting for it alone would put a commit
        # on every Documentation job, in exactly the place a real contract change should stand out.
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "server.json"
            payload = json.dumps(fixture("api")).encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()

            refresh.write_vendored(path, "docker.io/x/y", DIGEST, payload, digest)
            self.assertFalse(refresh.write_vendored(path, "docker.io/x/y", DIGEST, payload, digest))

    def test_a_new_digest_does_rewrite(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "server.json"
            payload = json.dumps(fixture("api")).encode("utf-8")
            digest = hashlib.sha256(payload).hexdigest()

            refresh.write_vendored(path, "docker.io/x/y", DIGEST, payload, digest)
            self.assertTrue(
                refresh.write_vendored(path, "docker.io/x/y", OTHER_DIGEST, payload, digest)
            )


class TestRegistryReference(unittest.TestCase):
    """oras reads a bare first segment as a hostname, so the refresh always qualifies it."""

    def test_a_docker_hub_name_is_qualified(self):
        repository, reference, digest = refresh.reference_for(
            {"registry": "", "repository": "x/y", "tag": f"v1@{DIGEST}"}, None
        )
        self.assertEqual(repository, "docker.io/x/y")
        self.assertEqual(reference, f"docker.io/x/y:v1@{DIGEST}")
        self.assertEqual(digest, DIGEST)

    def test_an_explicit_registry_is_left_alone(self):
        repository, _, _ = refresh.reference_for(
            {"registry": "ghcr.io", "repository": "x/y", "tag": "v1"}, None
        )
        self.assertEqual(repository, "ghcr.io/x/y")

    def test_the_tag_falls_back_to_app_version_for_resolution(self):
        _, reference, digest = refresh.reference_for({"repository": "x/y"}, "v9")
        self.assertEqual(reference, "docker.io/x/y:v9")
        self.assertIsNone(digest)


class TestImageResolution(unittest.TestCase):
    def test_docker_hub_has_no_registry_in_the_reference(self):
        values = {"image": {"registry": "", "repository": "x/y", "tag": f"v1@{DIGEST}"}}
        pinned = resolve_image(values, "image", None)
        self.assertEqual(pinned.reference, f"x/y:v1@{DIGEST}")
        self.assertEqual(pinned.normalized, "docker.io/x/y")
        self.assertEqual(pinned.digest, DIGEST)

    def test_an_explicit_registry_is_kept(self):
        values = {"image": {"registry": "ghcr.io", "repository": "x/y", "tag": "v1"}}
        self.assertEqual(resolve_image(values, "image", None).normalized, "ghcr.io/x/y")

    def test_the_tag_falls_back_to_app_version(self):
        values = {"image": {"repository": "x/y"}}
        self.assertEqual(resolve_image(values, "image", "v9").reference, "x/y:v9")

    def test_a_nested_values_path(self):
        values = {"services": {"api": {"image": {"repository": "x/y", "tag": "v1"}}}}
        self.assertEqual(resolve_image(values, "services.api.image", None).reference, "x/y:v1")

    def test_a_path_naming_no_image_is_an_error(self):
        with self.assertRaises(DeclarationError):
            resolve_image({}, "nope", None)


class TestStalenessInterlock(unittest.TestCase):
    """Run 1 of a digest bump must never report a pass it cannot justify."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.chart = Path(self.work.name) / "demo"
        (self.chart / "contracts").mkdir(parents=True)
        payload = json.dumps(fixture("api")).encode("utf-8")
        refresh.write_vendored(
            self.chart / "contracts" / "server.json",
            "docker.io/x/y",
            DIGEST,
            payload,
            hashlib.sha256(payload).hexdigest(),
        )
        (self.chart / "config-contract.yaml").write_text(
            "documents:\n"
            "  - name: server\n"
            "    source: { kind: ConfigMap, key: config.toml }\n"
            "    images:\n"
            "      - values: image\n"
            "        contract: contracts/server.json\n",
            encoding="utf-8",
        )
        self.declaration = load_declaration(self.chart)

    def tearDown(self):
        self.work.cleanup()

    def bind_with(self, tag):
        return bind(
            self.chart,
            self.declaration.documents[0],
            {"image": {"repository": "x/y", "tag": tag}},
            None,
        )

    def test_a_matching_digest_binds(self):
        binding, problems = self.bind_with(f"v1@{DIGEST}")
        self.assertEqual(problems, [])
        self.assertIn("auth.session_ttl", binding.union.keys)
        self.assertIn(DIGEST, binding.by_digest)

    def test_a_bumped_digest_refuses_to_validate_at_all(self):
        binding, problems = self.bind_with(f"v2@{OTHER_DIGEST}")
        self.assertIsNone(binding)
        self.assertIn("The Documentation job refreshes it", problems[0])

    def test_a_mutable_tag_refuses_to_validate(self):
        binding, problems = self.bind_with("latest")
        self.assertIsNone(binding)
        self.assertIn("not pinned by digest", problems[0])

    def test_a_contract_for_another_image_refuses_to_validate(self):
        binding, problems = bind(
            self.chart,
            self.declaration.documents[0],
            {"image": {"registry": "ghcr.io", "repository": "x/y", "tag": f"v1@{DIGEST}"}},
            None,
        )
        self.assertIsNone(binding)
        self.assertIn("ghcr.io/x/y", problems[0])


class TestDeclaration(unittest.TestCase):
    def load(self, body):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        chart = Path(work.name) / "demo"
        chart.mkdir()
        (chart / "config-contract.yaml").write_text(body, encoding="utf-8")
        return load_declaration(chart)

    def test_a_chart_with_no_file_is_skipped(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.assertIsNone(load_declaration(Path(work.name)))

    def test_an_opt_out_needs_a_reason(self):
        with self.assertRaises(DeclarationError) as raised:
            self.load("documents: []\n")
        self.assertIn("reason", str(raised.exception))

    def test_an_opt_out_with_a_reason_is_accepted(self):
        declaration = self.load("reason: nothing publishes one yet\ndocuments: []\n")
        self.assertEqual(declaration.documents, [])

    def test_an_unknown_key_is_refused_rather_than_ignored(self):
        with self.assertRaises(DeclarationError) as raised:
            self.load("documnets: []\nreason: typo\n")
        self.assertIn("documnets", str(raised.exception))

    def test_an_exemption_needs_a_reason(self):
        with self.assertRaises(DeclarationError) as raised:
            self.load(
                "documents:\n"
                "  - name: s\n"
                "    source: { kind: ConfigMap, key: c.toml }\n"
                "    images: [{ values: image, contract: c.json }]\n"
                "    exempt: [{ values: ci/x.yaml, gates: [closed] }]\n"
            )
        self.assertIn("reason", str(raised.exception))

    def test_an_exemption_naming_a_gate_that_does_not_exist_is_refused(self):
        with self.assertRaises(DeclarationError) as raised:
            self.load(
                "documents:\n"
                "  - name: s\n"
                "    source: { kind: ConfigMap, key: c.toml }\n"
                "    images: [{ values: image, contract: c.json }]\n"
                "    exempt: [{ values: ci/x.yaml, gates: [everything], reason: because }]\n"
            )
        self.assertIn("everything", str(raised.exception))

    def test_an_exemption_applies_to_the_values_file_it_names(self):
        declaration = self.load(
            "documents:\n"
            "  - name: s\n"
            "    source: { kind: ConfigMap, key: c.toml }\n"
            "    images: [{ values: image, contract: c.json }]\n"
            "    exempt: [{ values: ci/extra.yaml, gates: [closed], reason: verbatim toml }]\n"
        )
        document = declaration.documents[0]
        self.assertEqual(document.relaxed("extra.yaml"), {"closed"})
        self.assertEqual(document.relaxed("default-values.yaml"), set())


if __name__ == "__main__":
    unittest.main()
