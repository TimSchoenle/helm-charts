"""Consume office documents and e-mail through paperless-ngx, with the Tika and Gotenberg the chart
bundles, as the chart wires them.

Why this exists
---------------
Every other gate in this repository renders the chart with `tika.enabled` and inspects the
result, and the install job skips `ci/tika.yaml` because the two converter images are about
2.5 GiB. So nothing ever ran them. A Gotenberg or Tika release that renamed a route, a form field
or a command-line flag the chart passes, or that stopped producing what paperless-ngx asks for,
passed every check and broke office and e-mail consumption for whoever upgraded. Renovate opens
those bumps, including majors, and nothing reviewed them but a diff of the tag.

The compatibility that matters is not "Gotenberg starts". It is the contract between the
`gotenberg-client` and `tika-client` that the pinned paperless-ngx image carries and the servers
the chart pins beside it, and only paperless-ngx exercises that contract the way paperless-ngx
uses it. So this test does not call Tika or Gotenberg itself. It starts the whole rendered release
(see `docker_release.py`), uploads documents through paperless-ngx's REST API as a user would,
waits for its own consumer to finish, and asserts on what paperless-ngx stored.

What each fixture exercises, as of paperless-ngx 3.4
----------------------------------------------------
  .docx   Tika `/tika` (text) and `/meta` (metadata, including the creation date paperless-ngx
          adopts as the document date); Gotenberg `/forms/libreoffice/convert` with
          `updateIndexes` and `pdfa`
  .eml    Tika on the HTML part; Gotenberg `/forms/chromium/convert/html` twice (the mail
          template, and the HTML part with its inline image as a resource, under the chart's
          `--chromium-disable-javascript` and `--chromium-allow-list`), then
          `/forms/pdfengines/merge` with `pdfa`

Each is asserted on its output, not on a status code: text that came back through Tika, an
archive PDF whose text layer holds what LibreOffice or Chromium rendered, the PDF/A
identification paperless-ngx itself reads from that archive, a page count that proves the merge
ran, and a thumbnail. A field a new Gotenberg silently ignores fails here as surely as a route it
removed.

The release is rendered with the chart's default resources for Tika and Gotenberg rather than the
reduced ones in `ci/tika.yaml`, so a converter that outgrew its default memory limit fails here
as it would in production.

Usage: python3 .github/scripts/e2e/paperless_ngx_tika.py (needs docker and helm on PATH, and
`just deps` to have built the chart's dependencies).
"""

from __future__ import annotations

import base64
import io
import json
import signal
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docker_release import Manifests, Release, ReleaseError

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "charts" / "paperless-ngx"
RELEASE = "e2e"

ADMIN_USER = "e2e-admin"
ADMIN_PASSWORD = "e2e-admin-password-not-a-real-one"

# The version this test was written against. Pinned rather than left to the server's default so
# a paperless-ngx release that changes the response shape, or drops this version, fails with an
# error naming it rather than with a KeyError three calls later.
API_VERSION = "10"

# What paperless-ngx's archive metadata calls the PDF/A identification schema, and the
# conformance its Tika parser and mail parser both request for the chart's default
# `ocr.outputType` of `pdfa`.
PDFA_NAMESPACE = "http://www.aiim.org/pdfa/ns/id/"
PDFA_PART, PDFA_CONFORMANCE = "2", "B"

# Generous for a two-page document: a cold LibreOffice or Chromium takes a few seconds. Kept well
# inside the CI job's limit so a hung conversion fails with logs rather than as a job timeout.
CONSUME_TIMEOUT = 180

# Words rather than tokens: Tika, LibreOffice, Chromium and pdftotext each break text their own
# way, and a hyphenated or camel-cased marker is exactly what one of them would split.
DOCX_MARKER = "quokka lantern docx fixture"
DOCX_CREATED = datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)
MAIL_TEXT_MARKER = "pangolin harbour plain fixture"
MAIL_HTML_MARKER = "axolotl meadow markup fixture"
MAIL_SUBJECT = "Fixture mail for the Gotenberg contract"


# =================================================================================================
# Output.
# =================================================================================================


class Checks:
    """Collects verdicts so one run reports every failure, not only the first."""

    def __init__(self) -> None:
        self.failures = 0

    @staticmethod
    def step(title: str) -> None:
        print(f"\n\033[1m== {title}\033[0m", flush=True)

    def check(self, description: str, passed: bool, detail: str = "") -> bool:
        if passed:
            print(f"   ok    {description}", flush=True)
        else:
            self.failures += 1
            print(f"   \033[31mFAIL\033[0m  {description}", flush=True)
            if detail:
                print("         " + detail.replace("\n", "\n         "), flush=True)
        return passed


# =================================================================================================
# Fixtures, built here rather than committed: each is a few lines of readable XML or MIME, and a
# binary checked in beside the test could not be reviewed.
# =================================================================================================


def build_docx() -> bytes:
    """The smallest WordprocessingML package LibreOffice and Tika both accept, with a core
    properties part so Tika has a creation date to report."""
    created = DOCX_CREATED.strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = {
        # First, as libmagic expects it to be when it identifies an OOXML package.
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="word/document.xml" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            '<Relationship Id="rId2" Target="docProps/core.xml" Type="http://schemas.'
            'openxmlformats.org/package/2006/relationships/metadata/core-properties"/>'
            "</Relationships>"
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{DOCX_MARKER}</w:t></w:r></w:p></w:body>"
            "</w:document>"
        ),
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            "<cp:coreProperties "
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>Gotenberg contract fixture</dc:title>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
            "</cp:coreProperties>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def build_png() -> bytes:
    """A 2×2 opaque PNG, so the HTML part has an inline image Gotenberg receives as a resource."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    header = (2).to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0])
    pixels = b"".join(b"\x00" + b"\x20\x60\xa0" * 2 for _ in range(2))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def build_eml() -> bytes:
    """A multipart/alternative message with an inline image: the shape that sends paperless-ngx
    down every Gotenberg route its mail parser has, rather than the text-only shortcut."""
    image_id = make_msgid(domain="fixture.invalid")
    message = EmailMessage()
    message["From"] = "Fixture Sender <sender@fixture.invalid>"
    message["To"] = "Fixture Recipient <recipient@fixture.invalid>"
    message["Subject"] = MAIL_SUBJECT
    message["Date"] = format_datetime(datetime(2002, 3, 4, 5, 6, 7, tzinfo=UTC))
    message["Message-ID"] = make_msgid(domain="fixture.invalid")
    message.set_content(f"{MAIL_TEXT_MARKER}\n")
    message.add_alternative(
        "<!DOCTYPE html><html><body>"
        f"<h1>{MAIL_HTML_MARKER}</h1>"
        f'<p><img src="cid:{image_id[1:-1]}" alt="fixture image"></p>'
        "</body></html>",
        subtype="html",
    )
    message.get_payload()[1].add_related(
        build_png(), maintype="image", subtype="png", cid=image_id, filename="fixture.png"
    )
    return message.as_bytes()


# =================================================================================================
# The paperless-ngx REST API.
# =================================================================================================


class ApiError(RuntimeError):
    pass


@dataclass
class Paperless:
    base_url: str

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str | None = None) -> tuple[bytes, str]:  # fmt: skip
        credentials = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()
        request = urllib.request.Request(self.base_url + path, data=body, method=method)
        request.add_header("Authorization", f"Basic {credentials}")
        request.add_header("Accept", f"application/json; version={API_VERSION}")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            raise ApiError(f"{method} {path} returned {error.code}: {detail}") from error

    def json(self, path: str) -> Any:
        return json.loads(self._request("GET", path)[0])

    def raw(self, path: str) -> bytes:
        return self._request("GET", path)[0]

    def upload(self, filename: str, content: bytes, title: str) -> str:
        """POST a document for consumption and return the consume task's id."""
        boundary = uuid.uuid4().hex
        fields = [
            ('name="title"', title.encode()),
            (f'name="document"; filename="{filename}"', content),
        ]
        body = b"".join(
            f"--{boundary}\r\nContent-Disposition: form-data; {disposition}\r\n\r\n".encode()
            + value
            + b"\r\n"
            for disposition, value in fields
        ) + f"--{boundary}--\r\n".encode()
        payload, _ = self._request(
            "POST",
            "/api/documents/post_document/",
            body,
            f"multipart/form-data; boundary={boundary}",
        )
        return str(json.loads(payload))

    def wait_for_task(self, task_id: str, timeout: float = CONSUME_TIMEOUT) -> dict[str, Any]:
        """Poll until the consume task finishes; return it whatever its outcome."""
        deadline = time.monotonic() + timeout
        while True:
            tasks = self.json("/api/tasks/?page_size=100")["results"]
            task = next((t for t in tasks if t["task_id"] == task_id), None)
            if task is not None and task["status"] in ("success", "failure", "revoked"):
                return task
            if time.monotonic() > deadline:
                raise ApiError(f"task {task_id} did not finish within {timeout:.0f}s: {task}")
            time.sleep(2)


# =================================================================================================
# Assertions shared by both fixtures.
# =================================================================================================


def normalise(text: str) -> str:
    """Fold compatibility characters and collapse whitespace, so a marker matches however a
    renderer laid it out: Chromium writes "fi" into its text layer as the ligature U+FB01."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def pdf_text(release: Release, workload: str, pdf: bytes) -> str:
    """The text layer of a PDF, read by the pdftotext the paperless-ngx image ships."""
    pod = release.pod(workload)
    result = subprocess.run(
        ["docker", "exec", "-i", pod.owner.docker_name, "pdftotext", "-", "-"],
        input=pdf,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(f"pdftotext failed: {result.stderr.decode(errors='replace')}")
    return normalise(result.stdout.decode("utf-8", "replace"))


def pdf_image_count(release: Release, workload: str, pdf: bytes) -> int:
    """How many images a PDF embeds, as listed by the pdfimages the paperless-ngx image ships.

    pdfimages cannot read standard input, so the PDF is written to the container's own /tmp
    first; the file name is fixed, and this runs one call at a time.
    """
    pod = release.pod(workload)
    result = subprocess.run(
        [
            "docker", "exec", "-i", pod.owner.docker_name, "sh", "-c",
            "cat > /tmp/e2e-archive.pdf && pdfimages -list /tmp/e2e-archive.pdf"
            " && rm -f /tmp/e2e-archive.pdf",
        ],
        input=pdf,
        capture_output=True,
        check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise ReleaseError(f"pdfimages failed: {result.stderr.decode(errors='replace')}")
    # Two header lines, then one line per image.
    return max(0, len(result.stdout.decode("utf-8", "replace").strip().splitlines()) - 2)


def metadata_value(entries: list[dict[str, Any]], namespace: str, key: str) -> str | None:
    for entry in entries or []:
        if entry.get("namespace") == namespace and entry.get("key") == key:
            return str(entry.get("value"))
    return None


def consume(checks: Checks, api: Paperless, release: Release, filename: str, content: bytes,
            title: str) -> tuple[dict[str, Any], str, bytes] | None:  # fmt: skip
    """Upload one fixture and run the checks every converted document has to pass.

    Returns the stored document, its archive's text and the archive itself, or None when
    consumption failed and nothing further can be asserted.
    """
    task = api.wait_for_task(api.upload(filename, content, title))
    if not checks.check(
        "paperless-ngx consumed it",
        task["status"] == "success" and bool(task.get("related_document_ids")),
        json.dumps(task.get("result_data"), indent=2),
    ):
        return None

    document_id = task["related_document_ids"][0]
    document = api.json(f"/api/documents/{document_id}/")
    metadata = api.json(f"/api/documents/{document_id}/metadata/")

    checks.check(
        "an archive version was stored", bool(metadata.get("has_archive_version")), str(metadata)
    )
    archive = api.raw(f"/api/documents/{document_id}/download/")
    checks.check("the archive is a PDF", archive.startswith(b"%PDF-"), repr(archive[:16]))

    part = metadata_value(metadata.get("archive_metadata"), PDFA_NAMESPACE, "part")
    conformance = metadata_value(metadata.get("archive_metadata"), PDFA_NAMESPACE, "conformance")
    checks.check(
        f"the archive is PDF/A-{PDFA_PART}{PDFA_CONFORMANCE.lower()}, as the `pdfa` option asked",
        (part, conformance) == (PDFA_PART, PDFA_CONFORMANCE),
        f"pdfaid:part={part!r} pdfaid:conformance={conformance!r}",
    )

    thumbnail = api.raw(f"/api/documents/{document_id}/thumb/")
    checks.check(
        "a thumbnail was rendered from the archive",
        len(thumbnail) > 100 and thumbnail[:4] in (b"RIFF", b"\x89PNG"),
        f"{len(thumbnail)} bytes starting {thumbnail[:12]!r}",
    )

    text = pdf_text(release, RELEASE + "-paperless-ngx", archive)
    return document, text, archive


# =================================================================================================
# The run.
# =================================================================================================


def render() -> Manifests:
    secret_key = uuid.uuid4().hex
    output = subprocess.run(
        [
            "helm", "template", RELEASE, str(CHART),
            "--set", f"paperless.secretKey={secret_key}",
            "--set", f"paperless.admin.user={ADMIN_USER}",
            "--set", f"paperless.admin.password={ADMIN_PASSWORD}",
            "--set", "paperless.admin.email=admin@fixture.invalid",
            "--set", "tika.enabled=true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    if output.returncode != 0:
        raise ReleaseError(f"helm template failed:\n{output.stderr}")
    return Manifests.from_yaml(output.stdout)


def main() -> int:
    # A cancelled CI job or a Ctrl-C delivers a signal, not an exception; turning it into one is
    # what lets the release's `with` block remove every container, volume and network it made.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    checks = Checks()
    workload = RELEASE + "-paperless-ngx"

    Checks.step("Rendering the chart with Tika and Gotenberg enabled")
    manifests = render()
    release = Release(manifests)
    config = manifests.config_map(workload)
    checks.check(
        "the ConfigMap enables the Tika parser",
        config.get("PAPERLESS_TIKA_ENABLED") == "true",
        str({k: v for k, v in config.items() if "TIKA" in k}),
    )
    for component in ("tika", "gotenberg"):
        if not checks.check(
            f"the release runs a bundled {component}",
            any(pod.workload == f"{workload}-{component}" for pod in release.pods),
        ):
            return 1

    Checks.step("Pulling the images the chart pins")
    release.pull()

    Checks.step("Starting the release as rendered")
    with release:
        try:
            release.wait_ready()
            checks.check("every container passed its readiness probe", True)
            api = Paperless(release.url(workload, "http"))

            Checks.step("Consuming a .docx: Tika text and metadata, Gotenberg LibreOffice")
            result = consume(
                checks, api, release, "fixture.docx", build_docx(), "Fixture office document"
            )
            if result is not None:
                document, text, _ = result
                checks.check(
                    "Tika extracted the document's text",
                    DOCX_MARKER in normalise(document["content"]),
                    document["content"][:300],
                )
                checks.check(
                    "Tika's metadata supplied the document date",
                    str(document.get("created", "")).startswith(DOCX_CREATED.date().isoformat()),
                    f"created={document.get('created')!r}",
                )
                checks.check(
                    "LibreOffice rendered the text into the archive",
                    DOCX_MARKER in text,
                    text[:300],
                )

            Checks.step("Consuming an .eml: Tika HTML, Gotenberg Chromium and merge")
            result = consume(
                checks, api, release, "fixture.eml", build_eml(), "Fixture e-mail"
            )
            if result is not None:
                document, text, archive = result
                content = normalise(document["content"])
                checks.check(
                    "the plain-text part reached the content", MAIL_TEXT_MARKER in content,
                    content[:300],
                )  # fmt: skip
                checks.check(
                    "Tika extracted the HTML part's text", MAIL_HTML_MARKER in content,
                    content[:300],
                )  # fmt: skip
                checks.check(
                    "Chromium rendered the mail template into the archive",
                    MAIL_SUBJECT in text and MAIL_TEXT_MARKER in text,
                    text[:300],
                )
                checks.check(
                    "Chromium rendered the HTML part into the archive",
                    MAIL_HTML_MARKER in text,
                    text[:300],
                )
                checks.check(
                    "the two renders were merged into one archive",
                    (document.get("page_count") or 0) >= 2,
                    f"page_count={document.get('page_count')!r}",
                )
                images = pdf_image_count(release, workload, archive)
                checks.check(
                    "the HTML part's inline image reached the archive",
                    images >= 1,
                    f"pdfimages listed {images} image(s)",
                )
        except (ReleaseError, ApiError) as error:
            checks.check("the run completed", False, str(error))

        if checks.failures:
            print("\n".join(release.all_logs()), file=sys.stderr)

    print()
    if checks.failures:
        print(f"\033[31m{checks.failures} check(s) failed\033[0m")
        return 1
    print("\033[32mall checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
