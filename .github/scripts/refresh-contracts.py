#!/usr/bin/env python3
"""Refresh every vendored contract from the image its chart pins.

The one script in this repository that talks to a container registry. Everything downstream
reads the committed file, so a registry outage cannot fail a pull request that changes no image,
re-running CI on a six-month-old commit validates against what was true then, and the contract
diff lands in the *same* pull request as the digest bump — which is the whole point:

    -  "path": "isr.ttl_secs",
    +  "path": "isr.revalidate_secs",

A human reviewing a Renovate bump sees the removed key next to the chart's failing gate.

Per chart, per image a `config-contract.yaml` declares:

  1. resolve the values path to `registry/repository:tag@digest`, normalizing a registry-less
     Docker Hub name to `docker.io/...` — oras reads a bare first segment as a hostname
  2. `cosign verify` the *image* against `$CONTRACT_SIGNER`
  3. read the image config's labels, through the index when the pinned digest is one and
     requiring every platform to agree: `dev.terrace.config.contract.version` must be a version
     this repository implements, and `dev.terrace.config.prefix` is kept for step 6
  4. read the document from **both carriers**, whichever are present:
       - the OCI referrer with artifact type
         `application/vnd.terrace.config-schema.v1+json` attached to the digest, whose blob is
         checked against the digest its descriptor claims (the registry content-addresses it);
       - the file inside the image at the path `dev.terrace.config.contract.path` names, read by
         walking the layer blobs.
     At least one must be there. If both are, they must be byte-identical.
  5. assert the image's `dev.terrace.config.prefix` equals the fetched document's
     `schema.dialect.prefix`: the label is what a consumer discovers the image by, and a label
     naming one namespace over a document describing another is an image whose two halves came
     from different builds
  6. write `charts/<chart>/contracts/<name>.json` — the published bytes inside a `source`
     envelope recording which digest they were fetched for

**Why both carriers.** The design names the referrer as canonical and the embedded file as the
fallback for registries with no referrers API. Measured against a real image, the embedded file
is the stronger of the two: it lives in a layer whose digest is in a manifest whose digest is
what the chart pins, so a document read from it is provably *inside* the image — while an
attached artifact is a separate object that merely points at the same digest. The referrer is
still tried first, because it is one request rather than a layer walk. Neither is trusted alone
when both exist.

An earlier draft had a fourth label carrying the document's hash and checked the blob against it.
It is gone: it was the only dynamic label, and a multi-stage build cannot feed it from a
generator running inside a builder stage. What it bought — the embedded file and the attached
artifact being the same document — is what step 4 now recovers for free, by reading both.

There is deliberately no path to "fetch it unverified". A contract that cannot be proven to
belong to the pinned digest is worse than none, because every gate downstream would trust it —
and the document is untrusted input until step 6 finishes: it is a JSON blob from a registry.

The tie between a contract and an image is the *attachment*, not a field. The published document
carries no digest and cannot: a digest is what building an image produces, so a field holding it
would have to be written after the push, changing the bytes step 4's label was computed over.
Whatever comes back from asking a digest for its referrers belongs to that digest; step 6 is what
records which digest that was, for the offline staleness interlock in `check-config.py` to read.

Run by the Documentation job. It is the only step in the repository that depends on a service
outside GitHub — signature verification queries the Sigstore transparency log — so a Sigstore
outage fails that job. Deliberate: the alternative is trusting a document that cannot be shown to
belong to the pinned digest, and every gate downstream would then trust it too.

`RegistryClient` is the only part that touches the network and every call goes through it, so
everything above is unit-tested against recorded shapes as well, including the two things only a
real image revealed: the pinned digest being an index rather than a manifest, and the document
arriving through the embedded file rather than an attachment.

Usage: CONTRACT_SIGNER='https://github.com/TimSchoenle/...' refresh-contracts.py [chart]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_contract as cc  # noqa: E402

# `check-config.py` owns the declaration format; importing it by file name is not possible, so
# the two pieces this script needs are re-derived from the same YAML rather than duplicated.
CHARTS_DIR = Path("charts")
DECLARATION = "config-contract.yaml"

# The OIDC issuer a GitHub Actions workflow signs under. Paired with `$CONTRACT_SIGNER`, which
# names the workflow identity itself, so a signature from any other workflow — in any other
# repository — is refused rather than accepted as "signed".
OIDC_ISSUER = "https://token.actions.githubusercontent.com"


class RefreshError(Exception):
    """A contract that could not be fetched, or could not be proven to belong to its image."""


# --------------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------------


class RegistryClient:
    """Every call that leaves this machine, behind one seam.

    `oras` and `cosign` are pinned in `justfile` and installed by release URL exactly as
    `kubeconform` already is. Holding them here rather than scattering `subprocess.run` through
    the flow is what lets the verification order above be tested without a registry.
    """

    def __init__(self, oras: str | None = None, cosign: str | None = None):
        self.oras = oras or os.environ.get("ORAS_BIN") or shutil.which("oras")
        self.cosign = cosign or os.environ.get("COSIGN_BIN") or shutil.which("cosign")

    def require(self) -> None:
        for name, binary in (("oras", self.oras), ("cosign", self.cosign)):
            if not binary:
                raise RefreshError(
                    f"{name} is not on PATH. `just contracts` is the only recipe that talks to a "
                    f"registry and it needs both oras and cosign; install the pinned versions "
                    f"from justfile, or set {name.upper()}_BIN."
                )

    def _run(self, argv: list[str], binary: str) -> bytes:
        result = subprocess.run([binary, *argv], capture_output=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
            raise RefreshError(f"{Path(binary).name} {' '.join(argv)} failed: {detail}")
        return result.stdout

    def resolve(self, reference: str) -> str:
        """The digest a reference resolves to, so a tag is never trusted twice."""
        return self._run(["resolve", reference], self.oras).decode().strip()

    def discover(self, reference: str, artifact_type: str) -> list[dict[str, Any]]:
        """The referrers of one digest with one artifact type."""
        output = self._run(
            ["discover", "--format", "json", "--artifact-type", artifact_type, reference],
            self.oras,
        )
        return parse_referrers(output.decode("utf-8"))

    def image_labels(self, reference: str) -> dict[str, str]:
        """The labels on the image config blob, which is where discovery starts."""
        config = json.loads(self._run(["manifest", "fetch-config", reference], self.oras))
        return dict((config.get("config") or {}).get("Labels") or {})

    def manifest(self, reference: str) -> dict[str, Any]:
        return json.loads(self._run(["manifest", "fetch", reference], self.oras))

    def platforms(self, repository: str, digest: str) -> list[str]:
        """The per-platform image manifests behind a digest, or the digest itself.

        A multi-architecture image is pushed as an index, and the index is what a chart pins —
        so the digest in `values.yaml` is usually *not* an image manifest and has no config blob
        to carry labels. The real manifests are one level down.

        Docker's build attestations ride in the same index as entries with no platform
        (`unknown/unknown`); they are not images and carry none of this, so they are skipped.
        """
        manifest = self.manifest(f"{repository}@{digest}")
        entries = manifest.get("manifests")
        if not entries:
            return [digest]

        found = []
        for entry in entries:
            platform = entry.get("platform") or {}
            if platform.get("os") in (None, "unknown"):
                continue
            found.append(str(entry["digest"]))
        return found or [digest]

    def blob(self, reference: str) -> bytes:
        return self._run(["blob", "fetch", "--output", "-", reference], self.oras)

    def verify(self, reference: str, identity: str) -> None:
        self._run(
            [
                "verify",
                "--certificate-identity-regexp",
                identity,
                "--certificate-oidc-issuer",
                OIDC_ISSUER,
                reference,
            ],
            self.cosign,
        )


def parse_referrers(text: str) -> list[dict[str, Any]]:
    """Read `oras discover --format json`, whose top-level key has changed across releases."""
    document = json.loads(text)
    if isinstance(document, list):
        return document
    for key in ("referrers", "manifests"):
        entries = document.get(key)
        if isinstance(entries, list):
            return entries
    return []


# --------------------------------------------------------------------------------------------
# The refresh
# --------------------------------------------------------------------------------------------


def fetch_contract(
    client: RegistryClient, repository: str, digest: str, signer: str
) -> tuple[bytes, str]:
    """Fetch and verify one image's contract. Returns the published bytes and their sha256.

    Two things are proven here, and they answer different questions. The blob is what its
    descriptor says it is — a registry content-addresses its blobs, so hashing what arrived and
    comparing is the whole integrity check. And the image and the document belong together: the
    `dev.terrace.config.prefix` label is how a consumer discovers the image publishes a contract
    at all, so a label naming one namespace over a document describing another is an image whose
    two halves came from different builds, and vendoring it would tie a chart to a contract for
    something else.

    Neither is a fallback. A contract that cannot be proven to belong to the pinned digest is
    worse than none, because every gate downstream would trust it.
    """
    pinned = f"{repository}@{digest}"

    client.verify(pinned, signer)

    labels = image_labels(client, repository, digest)
    version = labels.get(cc.LABEL_VERSION)
    if version is None:
        raise RefreshError(
            f"{pinned} carries no {cc.LABEL_VERSION} label, so it publishes no contract"
        )
    if str(version) != str(cc.ENVELOPE_VERSION):
        raise RefreshError(
            f"{pinned} publishes contract version {version}, and this repository reads "
            f"{cc.ENVELOPE_VERSION}"
        )

    attached = fetch_attached(client, repository, digest)
    embedded = fetch_embedded(client, repository, digest, labels.get(cc.LABEL_PATH))

    if attached is None and embedded is None:
        raise RefreshError(
            f"{pinned} carries the contract labels but publishes no document: nothing is attached "
            f"with artifact type {cc.ARTIFACT_TYPE}, and there is no file at "
            f"{labels.get(cc.LABEL_PATH)!r} inside the image. The build set the labels without "
            "running the `oras attach` step or the `COPY` that embeds the document."
        )
    if attached is not None and embedded is not None and attached != embedded:
        raise RefreshError(
            f"{pinned} publishes two different documents: the artifact attached to the digest and "
            f"the file at {labels.get(cc.LABEL_PATH)} do not match, so the image and its "
            "attachment came from different builds"
        )

    payload = attached if attached is not None else embedded
    actual = hashlib.sha256(payload).hexdigest()

    document = json.loads(payload.decode("utf-8"))
    cc.check_envelope(document, pinned)

    declared = labels.get(cc.LABEL_PREFIX)
    described = document["schema"]["dialect"]["prefix"]
    if declared != described:
        raise RefreshError(
            f"{pinned} is labelled {cc.LABEL_PREFIX}={declared!r} but the contract attached to it "
            f"describes the namespace {described!r}: the image and its contract came from "
            "different builds"
        )

    return payload, actual


def image_labels(client: RegistryClient, repository: str, digest: str) -> dict[str, str]:
    """The contract labels, read through the index when the pinned digest is one.

    Every platform of one image is built from one Dockerfile and must therefore carry the same
    three labels. They are all read and required to agree rather than trusting the first: two
    architectures disagreeing about which namespace the binary reads is a build that produced two
    different programs under one tag, and vendoring either one would tie the chart to a contract
    that is wrong for half its nodes.
    """
    agreed: dict[str, str] | None = None
    first: str | None = None

    for platform in client.platforms(repository, digest):
        labels = {
            name: value
            for name, value in client.image_labels(f"{repository}@{platform}").items()
            if name.startswith("dev.terrace.config.")
        }
        if agreed is None:
            agreed, first = labels, platform
        elif labels != agreed:
            raise RefreshError(
                f"{repository}@{digest} carries different contract labels per platform: "
                f"{first} says {json.dumps(agreed, sort_keys=True)} and {platform} says "
                f"{json.dumps(labels, sort_keys=True)}"
            )

    return agreed or {}


def discover_contract(
    client: RegistryClient, repository: str, digest: str
) -> list[dict[str, Any]]:
    """The contract attached to a digest, looked for on the index and on each platform.

    Which of the two a producer attaches to is a real choice — the index is what a chart pins, a
    platform manifest is what actually runs — and a consumer that only looked at one would report
    "no contract" for an image that publishes one perfectly well. The index is tried first
    because that is the digest this repository ties everything else to.
    """
    referrers = client.discover(f"{repository}@{digest}", cc.ARTIFACT_TYPE)
    if referrers:
        return referrers

    for platform in client.platforms(repository, digest):
        if platform == digest:
            continue
        referrers = client.discover(f"{repository}@{platform}", cc.ARTIFACT_TYPE)
        if referrers:
            return referrers
    return []


def fetch_attached(client: RegistryClient, repository: str, digest: str) -> bytes | None:
    """The document attached to a digest as an OCI referrer, or `None` if nothing is."""
    referrers = discover_contract(client, repository, digest)
    if not referrers:
        return None
    if len(referrers) > 1:
        raise RefreshError(
            f"{repository}@{digest} has {len(referrers)} referrers of type {cc.ARTIFACT_TYPE}, "
            "expected one"
        )

    manifest = client.manifest(f"{repository}@{referrers[0]['digest']}")
    layers = manifest.get("layers") or []
    if len(layers) != 1:
        raise RefreshError(
            f"the contract attached to {repository}@{digest} has {len(layers)} layers, expected one"
        )

    claimed = str(layers[0]["digest"])
    payload = client.blob(f"{repository}@{claimed}")
    actual = hashlib.sha256(payload).hexdigest()
    if claimed != f"sha256:{actual}":
        raise RefreshError(
            f"the contract attached to {repository}@{digest} hashes to sha256:{actual}, but its "
            f"descriptor claims {claimed}"
        )
    return payload


def fetch_embedded(
    client: RegistryClient, repository: str, digest: str, path: str | None
) -> bytes | None:
    """The document from inside the image, at the path `dev.terrace.config.contract.path` names.

    Read by walking the layer blobs from the top down, because that is the order a filesystem
    resolves them: the last layer to write a path is the one the container sees. The first hit
    wins and the walk stops, so the usual case — the document `COPY`ed in a late, tiny layer —
    costs one small blob rather than the whole image.

    No `docker` and no local daemon: the images this validates are `FROM scratch` with no shell,
    and requiring a container runtime for a recipe that otherwise needs two static binaries would
    put this out of reach of the pull request that needs it most.
    """
    if not path:
        return None
    wanted = path.lstrip("/")

    for platform in client.platforms(repository, digest):
        manifest = client.manifest(f"{repository}@{platform}")
        for layer in reversed(manifest.get("layers") or []):
            found = _read_from_layer(client.blob(f"{repository}@{layer['digest']}"), wanted)
            if found is not None:
                return found
    return None


def _read_from_layer(blob: bytes, wanted: str) -> bytes | None:
    """One file out of one layer blob, or `None` if this layer does not hold it."""
    if blob[:2] == b"\x1f\x8b":
        try:
            blob = gzip.decompress(blob)
        except OSError:
            return None
    try:
        archive = tarfile.open(fileobj=io.BytesIO(blob))
    except tarfile.TarError:
        # A zstd or otherwise unreadable layer. Not an error: the file may be in another one, and
        # a document that is nowhere is reported by the caller rather than here.
        return None

    with archive:
        try:
            member = archive.getmember(wanted)
        except KeyError:
            return None
        if not member.isfile():
            return None
        handle = archive.extractfile(member)
        return handle.read() if handle else None


def write_vendored(path: Path, image: str, digest: str, payload: bytes, sha256: str) -> bool:
    """Write the wrapper, and report whether the file changed.

    `source.sha256` is over the document as published — the same value the registry addressed the
    blob by, minus the `sha256:` prefix. It is deliberately *not* recomputable from this file:
    the published bytes do not survive being re-serialised into the wrapper. That is a provenance
    record for a later networked run to check, not an offline integrity check, and the offline
    trust anchor is that this file was reviewed in the pull request that introduced it —
    which is the pull request that also carries the digest bump, by design.
    """
    contract = json.loads(payload.decode("utf-8"))

    wrapper = {
        "source": {
            "image": image,
            "digest": digest,
            "sha256": sha256,
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "contract": contract,
    }
    rendered = json.dumps(wrapper, indent=2, ensure_ascii=False) + "\n"

    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    if previous is not None and _same_contract(previous, rendered):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def _same_contract(previous: str, rendered: str) -> bool:
    """Whether two wrappers differ in anything but `source.fetched`.

    `fetched` moves on every run by construction. Rewriting the file for it alone would put a
    commit on every Documentation job — noise in exactly the place a real contract change is
    supposed to stand out.
    """
    try:
        old, new = json.loads(previous), json.loads(rendered)
    except json.JSONDecodeError:
        return False
    old.get("source", {}).pop("fetched", None)
    new.get("source", {}).pop("fetched", None)
    return old == new


def normalized_image(registry: str, repository: str) -> str:
    return f"{registry or 'docker.io'}/{repository}"


def reference_for(image: dict[str, Any], app_version: str | None) -> tuple[str, str, str | None]:
    """`(normalized repository, normalized tagged reference, inline digest)` for one image block.

    Mirrors `charts/common/templates/_images.tpl` for the tag, so what is fetched is what the
    chart deploys — but always in the fully qualified form. A chart writes `timschoenle/x` for
    Docker Hub because that is what Helm and the Docker CLI accept; oras reads a bare first
    segment as a registry hostname and tries to resolve `timschoenle` by DNS. cosign normalizes
    and oras does not, so passing the chart's spelling around would have the two disagree about
    which image was verified.
    """
    registry = str(image.get("registry") or "")
    repository = str(image.get("repository") or "")
    if not repository:
        raise RefreshError("image block has no `repository`")
    tag = str(image.get("tag") or app_version or "")

    normalized = normalized_image(registry, repository)
    reference = f"{normalized}:{tag}" if tag else normalized
    digest = tag.split("@", 1)[1] if "@" in tag else None
    return normalized, reference, digest


def dig(values: Any, path: str) -> Any:
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def refresh_chart(chart_dir: Path, client: RegistryClient, signer: str) -> list[str]:
    """Refresh every contract one chart declares. Returns the paths that changed."""
    declaration = yaml.safe_load((chart_dir / DECLARATION).read_text(encoding="utf-8")) or {}
    values = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8")) or {}
    meta = yaml.safe_load((chart_dir / "Chart.yaml").read_text(encoding="utf-8")) or {}
    app_version = meta.get("appVersion")

    changed: list[str] = []
    seen: set[str] = set()
    for document in declaration.get("documents") or []:
        for entry in document.get("images") or []:
            target = chart_dir / entry["contract"]
            if str(target) in seen:
                continue
            seen.add(str(target))

            image = dig(values, entry["values"])
            if not isinstance(image, dict):
                raise RefreshError(
                    f"{chart_dir / DECLARATION}: values path {entry['values']!r} resolves to no "
                    "image block"
                )

            normalized, reference, digest = reference_for(image, app_version)
            if digest is None:
                # Resolving a tag is a second network call and a second answer; a chart in this
                # repository pins by digest, so this is a fallback rather than the path.
                digest = client.resolve(reference)

            payload, sha256 = fetch_contract(client, normalized, digest, signer)
            if write_vendored(target, normalized, digest, payload, sha256):
                changed.append(str(target))
                print(f"==> updated {target} ({normalized}@{digest[:19]}...)")
            else:
                print(f"==> unchanged {target}")

    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chart", nargs="?", default="", help="one chart, or every chart")
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    args = parser.parse_args(argv)

    signer = os.environ.get("CONTRACT_SIGNER")
    if not signer:
        print(
            "error: CONTRACT_SIGNER is unset. It is the workflow identity a contract must be "
            "signed by; without it `cosign verify` would accept any signature at all.",
            file=sys.stderr,
        )
        return 1

    client = RegistryClient()
    try:
        client.require()
    except RefreshError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    charts = Path(args.charts)
    directories = [charts / args.chart] if args.chart else sorted(charts.iterdir())

    failed = False
    for chart_dir in directories:
        if not (chart_dir / DECLARATION).is_file():
            continue
        try:
            refresh_chart(chart_dir, client, signer)
        except (RefreshError, cc.ContractError) as error:
            # Every chart is attempted before the script exits, matching `just render`: one image
            # that cannot be reached must not hide the state of the rest.
            print(f"{chart_dir.name}: {error}", file=sys.stderr)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
