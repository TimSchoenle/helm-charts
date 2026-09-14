#!/usr/bin/env python3
"""Cache the pinned `terrace-contract` release under a repo-local directory.

`.github/actions/install-terrace-contract` is CI's copy of this: download the release, verify it,
unpack it to `/usr/local/bin`. That path needs `sudo` and writes outside the checkout, which is
fine for a disposable runner and is exactly why every contributor who cannot or would rather not
do that has been stuck reading `justfile`'s comment about `TERRACE_CONTRACT_BIN` and either
installing the release by hand or building the Rust toolchain themselves. This is the third way
in: same release, same verification, written under `contract_cache` in `justfile` instead — a
directory that belongs to the checkout, is gitignored, and `resolve_contract` already knows to
look in.

Verification mirrors the CI action exactly, in the same order, because the reasoning does not
change with where the bytes end up: the signature over `SHA256SUMS` proves the release came from
`terrace-config`'s own release workflow, and the checksum proves the archive is the file that
signature was made over. `cosign` is what verifies that signature, and it is itself downloaded
unverified here, for the same reason the CI action's is: verifying the verifier needs a second
root of trust this repository does not have, so what is pinned is *which* release of cosign, not a
proof it arrived unmodified. `--cosign-version` is `justfile`'s `cosign_version`, resolved on PATH
first exactly as `RegistryClient` in `refresh-contracts.py` resolves it, so a machine that already
has cosign never fetches a second copy.

Idempotent and offline-first: an existing cache for the requested version is left alone unless
`--force` says otherwise, so re-running this after a version bump is the only time it touches the
network again.

Usage:
    install-contract-cli.py --version 0.2.2 --cosign-version v3.1.3 \\
        --cache-dir .cache/terrace-contract
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

TERRACE_CONFIG_REPO = "TimSchoenle/terrace-config"
TERRACE_CONFIG_RELEASES = f"https://github.com/{TERRACE_CONFIG_REPO}/releases/download"
COSIGN_RELEASES = "https://github.com/sigstore/cosign/releases/download"

# The workflow identity `SHA256SUMS.sig` must be signed by. Paired with `OIDC_ISSUER` below, this
# is what `.github/actions/install-terrace-contract/action.yaml` verifies against; kept identical
# so a release this script accepts is exactly one CI would accept too.
CERT_IDENTITY = r"^https://github\.com/TimSchoenle/terrace-config/\.github/workflows/release-cli\.yml@"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"


class InstallError(Exception):
    """A release that could not be fetched, or could not be proven authentic."""


# --------------------------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------------------------


def _terrace_contract_asset(version: str) -> tuple[str, str]:
    """(archive filename, binary filename) for the running machine.

    Measured against the actual release rather than assumed: `terrace-config` publishes four
    targets — both linux-musl architectures, `aarch64-apple-darwin`, and
    `x86_64-pc-windows-msvc` — and no `x86_64` macOS build, which is why that combination raises
    rather than silently picking the nearest one.
    """
    system = platform.system()
    machine = platform.machine().lower()
    arch64 = machine in ("aarch64", "arm64")
    amd64 = machine in ("x86_64", "amd64")

    if system == "Linux" and (amd64 or arch64):
        triple = "x86_64-unknown-linux-musl" if amd64 else "aarch64-unknown-linux-musl"
        return f"terrace-contract-{version}-{triple}.tar.gz", "terrace-contract"
    if system == "Darwin" and arch64:
        return f"terrace-contract-{version}-aarch64-apple-darwin.tar.gz", "terrace-contract"
    if system == "Windows" and amd64:
        return f"terrace-contract-{version}-x86_64-pc-windows-msvc.zip", "terrace-contract.exe"

    raise InstallError(
        f"terrace-contract publishes no release for {system}/{machine}. Build it from source and "
        f"set TERRACE_CONTRACT_BIN instead:\n"
        f"  cargo build --release --manifest-path <terrace-config>/cli/Cargo.toml"
    )


def _cosign_asset() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    arch64 = machine in ("aarch64", "arm64")
    amd64 = machine in ("x86_64", "amd64")

    if system == "Linux" and amd64:
        return "cosign-linux-amd64"
    if system == "Linux" and arch64:
        return "cosign-linux-arm64"
    if system == "Darwin" and amd64:
        return "cosign-darwin-amd64"
    if system == "Darwin" and arch64:
        return "cosign-darwin-arm64"
    if system == "Windows" and amd64:
        return "cosign-windows-amd64.exe"

    raise InstallError(f"cosign publishes no release for {system}/{machine}")


# --------------------------------------------------------------------------------------------
# Fetching and verifying
# --------------------------------------------------------------------------------------------


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise InstallError(f"{url} -> HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise InstallError(f"{url} -> {exc.reason}") from exc


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _ensure_cosign(cache_dir: Path, version: str) -> Path:
    """A cosign binary to verify with — on PATH, named by `COSIGN_BIN`, or cached here."""
    found = os.environ.get("COSIGN_BIN") or shutil.which("cosign")
    if found:
        return Path(found)

    asset = _cosign_asset()
    target = cache_dir / "cosign" / version / asset
    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_download(f"{COSIGN_RELEASES}/{version}/{asset}"))
    _make_executable(target)
    return target


def _verify_signature(cosign: Path, work: Path) -> None:
    result = subprocess.run(
        [
            str(cosign),
            "verify-blob",
            str(work / "SHA256SUMS"),
            "--signature",
            str(work / "SHA256SUMS.sig"),
            "--certificate",
            str(work / "SHA256SUMS.pem"),
            "--certificate-identity-regexp",
            CERT_IDENTITY,
            "--certificate-oidc-issuer",
            OIDC_ISSUER,
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise InstallError(f"cosign could not verify SHA256SUMS: {detail}")


def _verify_checksum(work: Path, asset: str) -> None:
    sums = (work / "SHA256SUMS").read_text(encoding="utf-8")
    wanted = next(
        (
            parts[0]
            for line in sums.splitlines()
            if (parts := line.split()) and len(parts) == 2 and parts[1].lstrip("*") == asset
        ),
        None,
    )
    if wanted is None:
        raise InstallError(f"SHA256SUMS names no entry for {asset}")

    actual = hashlib.sha256((work / asset).read_bytes()).hexdigest()
    if actual != wanted:
        raise InstallError(f"checksum mismatch for {asset}: expected {wanted}, got {actual}")


def _extract_binary(archive: Path, binary_name: str, dest: Path) -> None:
    """Pull just the named binary out of `archive`, wherever inside it lives."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            member = next((n for n in zf.namelist() if Path(n).name == binary_name), None)
            if member is None:
                raise InstallError(f"{archive.name} contains no {binary_name}")
            dest.write_bytes(zf.read(member))
    else:
        with tarfile.open(archive) as tf:
            member = next((m for m in tf.getmembers() if Path(m.name).name == binary_name), None)
            if member is None:
                raise InstallError(f"{archive.name} contains no {binary_name}")
            extracted = tf.extractfile(member)
            if extracted is None:
                raise InstallError(f"{archive.name} entry {member.name} is not a regular file")
            dest.write_bytes(extracted.read())
    _make_executable(dest)


# --------------------------------------------------------------------------------------------
# The install
# --------------------------------------------------------------------------------------------


def install(version: str, cosign_version: str, cache_dir: Path, force: bool) -> Path:
    asset, binary_name = _terrace_contract_asset(version)
    target = cache_dir / version / binary_name

    if target.exists() and not force:
        print(f"==> terrace-contract {version} already cached at {target}")
        return target

    base = f"{TERRACE_CONFIG_RELEASES}/terrace-contract-v{version}"

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name in (asset, "SHA256SUMS", "SHA256SUMS.sig", "SHA256SUMS.pem"):
            try:
                (work / name).write_bytes(_download(f"{base}/{name}"))
            except InstallError as exc:
                raise InstallError(
                    f"terrace-contract v{version} publishes no {name} ({exc}). If the version was "
                    f"just bumped in justfile, the release may not be cut yet; if you are testing "
                    f"a toolchain change, build it and set TERRACE_CONTRACT_BIN instead."
                ) from exc

        cosign = _ensure_cosign(cache_dir, cosign_version)
        _verify_signature(cosign, work)
        _verify_checksum(work, asset)

        target.parent.mkdir(parents=True, exist_ok=True)
        _extract_binary(work / asset, binary_name, target)

    print(f"==> cached terrace-contract {version} at {target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="terrace_contract_version from justfile")
    parser.add_argument("--cosign-version", required=True, help="cosign_version from justfile")
    parser.add_argument(
        "--cache-dir", required=True, type=Path, help="contract_cache from justfile"
    )
    parser.add_argument("--force", action="store_true", help="re-download even if already cached")
    args = parser.parse_args()

    try:
        target = install(args.version, args.cosign_version, args.cache_dir, args.force)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    subprocess.run([str(target), "--version"], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
