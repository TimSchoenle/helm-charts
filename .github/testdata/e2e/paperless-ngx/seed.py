# The instance the paperless-ngx end-to-end test exports from, and then has to see come back.
#
# A committed archive would be the obvious fixture and is the wrong one. `document_importer` reads
# a manifest written by `document_exporter` of the same release, and both are upstream's — a
# fixture frozen in this repository would keep passing after the format it encodes stopped being
# the format paperless-ngx writes, which is precisely the day the test needed to fail. So the
# archive is built by the chart's own export script from this file, on the image `values.yaml`
# pins, every run.
#
# Documents are created through the ORM rather than by consuming files, because consumption means
# OCR: minutes of Tesseract per document, a parser stack to stand up, and content that depends on
# the image's OCR quality. Nothing here is testing OCR. What has to be true is that the manifest
# carries several documents with files, thumbnails and relations, so the importer's copy loop runs
# more than once and has something to raise `FileExistsError` on when a partial import is retried.
#
# Nothing here is decoration:
#
#   user, correspondent,  make the archive exercise `bulk_create(update_conflicts=True)` across
#   type, tag            several models and foreign keys — the half of the import that *is*
#                        idempotent, and the half the retry logic depends on.
#
#   mail account         carries a `password`, which is one of the two fields `document_exporter`
#                        encrypts under `--passphrase`. Without it a passphrase would encrypt
#                        nothing, and an export asserted to hide its secrets would be hiding an
#                        empty set.
#
# Run as `manage.py shell < seed.py`; it prints `seeded=<n>` for the caller to check.
import hashlib
from datetime import datetime
from datetime import timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from PIL import Image

from documents.models import Correspondent
from documents.models import Document
from documents.models import DocumentType
from documents.models import Tag
from paperless_mail.models import MailAccount

DOCUMENT_COUNT = 3

# Asserted to appear in an unencrypted export and to be absent from an encrypted one, so it has to
# be a string that cannot occur by chance in a manifest.
MAIL_PASSWORD = "fixture-mail-password-8f3ac1e0-not-a-real-one"

settings.ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
settings.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

User.objects.create_superuser(
    "fixture-admin",
    "fixture-admin@example.invalid",
    "fixture-password-not-a-real-one",
)
correspondent = Correspondent.objects.create(name="Fixture Correspondent")
document_type = DocumentType.objects.create(name="Fixture Type")
tag = Tag.objects.create(name="fixture")

MailAccount.objects.create(
    name="Fixture Mail Account",
    imap_server="imap.example.invalid",
    imap_port=993,
    username="fixture-mail-user",
    password=MAIL_PASSWORD,
)

for index in range(1, DOCUMENT_COUNT + 1):
    # Padded so the files are large enough for a truncated copy to be a distinguishable state
    # rather than an atomic one, and byte-identical content is asserted after the import.
    body = f"%PDF-1.4 paperless-ngx chart fixture document {index}\n".encode() + b"0" * 4096
    document = Document.objects.create(
        title=f"Fixture Document {index}",
        mime_type="application/pdf",
        checksum=hashlib.md5(body).hexdigest(),
        created=datetime(2026, 1, index, tzinfo=timezone.utc),
        added=datetime(2026, 1, index, tzinfo=timezone.utc),
        original_filename=f"fixture-{index}.pdf",
        correspondent=correspondent,
        document_type=document_type,
        content=f"fixture content {index}",
    )
    document.tags.add(tag)
    Path(document.source_path).write_bytes(body)
    # Written with Pillow, which the image already carries, rather than as embedded bytes: the
    # exporter and the importer both handle these as real images.
    Image.new("RGB", (8, 8), "white").save(Path(document.thumbnail_path), "WEBP")

print(f"seeded={Document.objects.count()}")
