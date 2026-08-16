#!/usr/bin/env bash
# Run the paperless-ngx chart's export and import scripts against the real image, end to end.
#
# Why this exists
# ---------------
# `helm unittest` proves the chart renders a backup CronJob and an import initContainer that call
# `document_exporter` and `document_importer` with the right flags, mounts and environment. It
# cannot prove the scripts inside them work, because what they have to work against is upstream's
# behaviour — and both defects this gate was written for lived entirely on that side:
#
#   * `document_importer` finishes by rebuilding the search index, and `wipe_index` iterates
#     `data/index` without creating it. Only the image's s6 `init-folders` step makes that
#     directory, and a container overriding `command` never runs it — so a fresh import copied the
#     whole archive into place and then died, after the copy and before the marker.
#
#   * `document_importer` copies files under
#     `if Path(document.source_path).is_file(): raise FileExistsError(...)`, with no flag to skip or
#     overwrite. An attempt killed part-way therefore made every later attempt fail on a file it had
#     already written, and the pod crash-looped on a traceback naming a document rather than a cause.
#
# A rendered manifest looks identical either way. So the scripts are executed, on the image
# `values.yaml` pins, against the PostgreSQL the chart would otherwise install, over archives this
# run produces. Both halves are the chart's own, so the round trip is covered rather than either
# end alone and neither can drift from the other without this failing.
#
# What is and is not covered
# --------------------------
# The scripts are extracted from the rendered pod specs, and so are the environment values the
# chart states literally — `RESTORE_SOURCE`, `BACKUP_TARGET` and the rest come from the manifest
# rather than from this file restating them. The values behind `secretKeyRef` and
# `configMapKeyRef` cannot be read from a manifest and are supplied here; that wiring is what
# `tests/backup_test.yaml` and `tests/restore_test.yaml` assert, and this script deliberately does
# not duplicate it.
#
# Usage: bash .github/scripts/e2e/paperless-ngx.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
chart="${repo_root}/charts/paperless-ngx"
fixture="${repo_root}/.github/testdata/e2e/paperless-ngx"

postgres_image="${POSTGRES_IMAGE:-postgres:18.6-alpine}"
network="paperless-e2e-$$"
postgres="paperless-e2e-postgres-$$"

db_name="paperless"
db_user="paperless"
db_password="fixture-db-password-not-a-real-one"
secret_key="fixture-secret-key-not-a-real-one"
passphrase="fixture-export-passphrase-not-a-real-one"
wrong_passphrase="fixture-wrong-passphrase-not-a-real-one"

# Asserted to survive an encrypted round trip, and to be absent from the archive that carried it.
# Kept in step with `seed.py`, which is where it originates.
mail_password="fixture-mail-password-8f3ac1e0-not-a-real-one"

# Git Bash rewrites POSIX-looking arguments into Windows paths before `docker` sees them, which
# turns every `-v host:container` into something neither side recognises. Suppressing that leaves
# the host half of each mount as a path the Windows daemon cannot resolve either, so the two are
# handled as one pair. Both are no-ops on a CI runner; they are here so the gate can be reproduced
# on the machine the chart is edited on, which is how it gets used before it is pushed.
docker() { MSYS_NO_PATHCONV=1 command docker "$@"; }
host_path() {
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) cygpath -m -- "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

workdir=""

# The containers write into the mounted volumes as uid 1000, inside directories they create
# themselves with their own umask. A runner that is not uid 1000 cannot unlink anything in those
# directories, however the tree above them was chmod'ed — unlinking needs write on the parent, and
# the parent is the container's. So state a container wrote is removed from a container, as root,
# over the same mount; that is also what emptying a PersistentVolume amounts to.
#
# Only paths beneath the mount point are ever passed: `/workdir` is the mount itself and cannot be
# unlinked from inside it.
scrub() {
  docker run --rm -u 0:0 -v "$(host_path "${workdir}")":/workdir \
    --entrypoint /bin/sh "${image}" -c 'rm -rf -- "$@"' scrub "$@" >/dev/null
}

cleanup() {
  docker rm --force "${postgres}" >/dev/null 2>&1 || true
  docker network rm "${network}" >/dev/null 2>&1 || true
  if [ -n "${workdir}" ]; then
    scrub /workdir/media /workdir/data /workdir/export /workdir/tmp >/dev/null 2>&1 || true
    rm -rf -- "${workdir}"
  fi
  return 0
}
trap cleanup EXIT

# CI runners ship `python3`; a Git Bash shell on Windows commonly has only `python`, and often also
# a `python3` on PATH that is the Microsoft Store's install stub rather than an interpreter. So
# each candidate is executed rather than merely located. Matches `test-rules.sh`.
python_bin=""
for candidate in python3 python; do
  if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c "import yaml" >/dev/null 2>&1; then
    python_bin="${candidate}"
    break
  fi
done
if [ -z "${python_bin}" ]; then
  echo "no python interpreter with PyYAML found; install PyYAML" >&2
  exit 1
fi

failures=0
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
pass() { printf '   ok    %s\n' "$*"; }
fail() {
  printf '   \033[31mFAIL\033[0m  %s\n' "$*"
  failures=$((failures + 1))
}
check() {
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then pass "${description}"; else fail "${description}"; fi
}

# =================================================================================================
# What is being tested, taken from the chart rather than restated here.
# =================================================================================================

# The image is read from values.yaml so the test follows the pin. A `tag` of `<version>@<digest>`
# is the chart's own convention and has to be split: `repository:tag@digest` is not a reference
# Docker accepts, and the digest is the half worth keeping.
image="$(
  "${python_bin}" - "${chart}/values.yaml" <<'PY'
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as handle:
    values = yaml.safe_load(handle)

image = values["image"]
tag = str(image["tag"])
reference = f"{image['repository']}@{tag.split('@', 1)[1]}" if "@" in tag else f"{image['repository']}:{tag}"
print(reference)
PY
)"

# Extracts one container's script and its literal environment from a rendered manifest. Emits a
# shell fragment: `script` holding args[0], and `env_<NAME>` for every variable stated as a plain
# value. `valueFrom` entries are skipped — a manifest does not carry what they resolve to.
#
# Held in a variable rather than fed to the interpreter as a heredoc, because the manifest arrives
# on stdin and a heredoc would take that redirection for itself.
read -r -d '' extract_program <<'PY' || true
import shlex
import sys

import yaml

kind, container_path, container_name = sys.argv[1:4]

for document in yaml.safe_load_all(sys.stdin):
    if not document or document.get("kind") != kind:
        continue
    node = document
    for key in container_path.split("."):
        node = node[int(key)] if key.isdigit() else node[key]
    for container in node:
        if container["name"] != container_name:
            continue
        print(f"script={shlex.quote(container['args'][0])}")
        for variable in container.get("env", []):
            if "value" in variable:
                print(f"env_{variable['name']}={shlex.quote(str(variable['value']))}")
        sys.exit(0)

raise SystemExit(f"no container {container_name!r} in any {kind}")
PY

# Sets `script` and `env_<NAME>` from one rendered container. Previously-set `env_*` are cleared
# first, so a variable a later render stops emitting cannot be read as if it were still there.
render() {
  local kind="$1" container_path="$2" container_name="$3"
  shift 3
  local rendered variable
  for variable in $(compgen -v env_ || true); do unset "${variable}"; done
  rendered="$(
    helm template fixture "${chart}" "$@" |
      "${python_bin}" -c "${extract_program}" "${kind}" "${container_path}" "${container_name}"
  )"
  eval "${rendered}"
}

render_backup() {
  render CronJob spec.jobTemplate.spec.template.spec.containers export \
    --set paperless.secretKey="${secret_key}" \
    --set persistence.export.enabled=true \
    --set backup.enabled=true \
    --set backup.options.delete=false \
    "$@"
}

render_restore() {
  render Deployment spec.template.spec.initContainers import \
    --set paperless.secretKey="${secret_key}" \
    --set persistence.export.enabled=true \
    --set restore.enabled=true \
    --set restore.source.useExportVolume=true \
    "$@"
}

step "Extracting the scripts the chart renders"

# `zip.nameTemplate` is normally a strftime string expanded by the container at run time, so a
# nightly CronJob does not write over one file. Pinned for the archives an import phase later has
# to name; the default is exercised as itself in the retention phase.
render_backup --set backup.mode=zip --set backup.zip.nameTemplate=fixture-export
backup_zip_script="${script}"
backup_target="${env_BACKUP_TARGET}"

render_backup --set backup.mode=zip --set backup.zip.retentionCount=2
backup_retention_script="${script}"
backup_retention_template="${env_BACKUP_ZIP_NAME_TEMPLATE}"
backup_retention_count="${env_BACKUP_RETENTION}"

render_backup --set backup.mode=zip --set backup.zip.nameTemplate=encrypted-export \
  --set backup.passphrase="${passphrase}"
backup_encrypted_script="${script}"

render_backup --set backup.mode=incremental
backup_incremental_script="${script}"

render_restore --set restore.source.subPath=fixture-export.zip
restore_script="${script}"
restore_source="${env_RESTORE_SOURCE}"
restore_marker="${env_RESTORE_MARKER}"
restore_attempt_marker="${env_RESTORE_ATTEMPT_MARKER}"
src_dir="${env_PAPERLESS_SRC_DIR}"

render_restore --set restore.source.subPath=encrypted-export.zip \
  --set restore.passphrase="${passphrase}"
restore_encrypted_script="${script}"
restore_encrypted_source="${env_RESTORE_SOURCE}"

# No subPath: the export volume's own root, which is what an `incremental` export writes into.
render_restore
restore_directory_script="${script}"
restore_directory_source="${env_RESTORE_SOURCE}"

for required in backup_zip_script backup_target backup_retention_script backup_retention_template \
  backup_retention_count backup_encrypted_script backup_incremental_script restore_script \
  restore_source restore_marker restore_attempt_marker src_dir restore_encrypted_script \
  restore_directory_script; do
  if [ -z "${!required}" ]; then
    echo "the chart no longer renders ${required}; the scripts moved and this test has to follow" >&2
    exit 1
  fi
done
pass "four export scripts, three import scripts, and the paths they use, read from the manifests"

# =================================================================================================
# The instance the scripts run against.
# =================================================================================================

workdir="$(mktemp -d)"
stash="${workdir}/stash"
mkdir -p "${workdir}/media" "${workdir}/data" "${workdir}/export" "${workdir}/tmp" "${stash}"
# The image runs as uid 1000 under this chart's podSecurityContext, and the volumes are its own.
chmod -R 0777 "${workdir}"

step "Starting PostgreSQL ${postgres_image}"
docker network create "${network}" >/dev/null
docker run --detach --name "${postgres}" --network "${network}" \
  -e POSTGRES_DB="${db_name}" -e POSTGRES_USER="${db_user}" -e POSTGRES_PASSWORD="${db_password}" \
  "${postgres_image}" >/dev/null
for _ in $(seq 60); do
  if docker exec "${postgres}" pg_isready -U "${db_user}" -d "${db_name}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "${postgres}" pg_isready -U "${db_user}" -d "${db_name}" >/dev/null
pass "database ready"

# A one-shot container over the same volumes, with the environment the chart supplies through its
# Secret and ConfigMap. `--entrypoint` because the image's own is `/init`, which brings up the
# whole s6 supervision tree — the same reason the chart overrides `command`.
#
# Every phase is its own `docker run`, never a `docker exec` into a long-lived container: that is
# what an initContainer and a CronJob pod are, and a retry that only passed because state survived
# in a running process would not be a retry.
#
# `PAPERLESS_CACHE_BACKEND` is the one setting here the chart does not supply: the chart installs
# Valkey and points the cache at it, and a broker is irrelevant to a command that neither queues
# work nor serves a request. Standing one up would be testing Valkey.
paperless() {
  docker run --rm --network "${network}" -u 1000:1000 \
    -e PAPERLESS_SRC_DIR=/usr/src/paperless/src \
    -e HOME=/usr/src/paperless \
    -e PAPERLESS_SECRET_KEY="${secret_key}" \
    -e PAPERLESS_DBHOST="${postgres}" \
    -e PAPERLESS_DBNAME="${db_name}" \
    -e PAPERLESS_DBUSER="${db_user}" \
    -e PAPERLESS_DBPASS="${db_password}" \
    -e PAPERLESS_CACHE_BACKEND=django.core.cache.backends.locmem.LocMemCache \
    -e BACKUP_TARGET="${backup_target}" \
    -e BACKUP_ZIP_NAME_TEMPLATE="${2:-fixture-export}" \
    -e BACKUP_RETENTION="${3:-7}" \
    -e BACKUP_PASSPHRASE="${passphrase}" \
    -e RESTORE_SOURCE="${4:-${restore_source}}" \
    -e RESTORE_MARKER="${restore_marker}" \
    -e RESTORE_ATTEMPT_MARKER="${restore_attempt_marker}" \
    -e RESTORE_PASSPHRASE="${5:-${passphrase}}" \
    -v "$(host_path "${workdir}")/media:/usr/src/paperless/media" \
    -v "$(host_path "${workdir}")/data:/usr/src/paperless/data" \
    -v "$(host_path "${workdir}")/export:/usr/src/paperless/export" \
    -v "$(host_path "${workdir}")/tmp:/tmp" \
    -v "$(host_path "${fixture}"):/fixture:ro" \
    --entrypoint /usr/bin/bash "${image}" -c "$1"
}

# Everything the application would have on disk, gone — which is what a fresh release is, and what
# the importer requires. The database is dropped rather than emptied so the import has to create
# the schema through the script's own `migrate` step, as it would on a new release.
reset_installation() {
  scrub /workdir/media/documents /workdir/data
  mkdir -p "${workdir}/data"
  chmod 0777 "${workdir}/data"
  docker exec -e PGPASSWORD="${db_password}" "${postgres}" \
    psql -U "${db_user}" -d postgres -q -c "DROP DATABASE IF EXISTS ${db_name} WITH (FORCE)" \
    -c "CREATE DATABASE ${db_name} OWNER ${db_user}" >/dev/null
}

clear_export_volume() {
  scrub /workdir/export
  mkdir -p "${workdir}/export"
  chmod 0777 "${workdir}/export"
}

query() {
  docker exec -e PGPASSWORD="${db_password}" "${postgres}" \
    psql -U "${db_user}" -d "${db_name}" -tAc "$1" 2>/dev/null | tr -d '[:space:]'
}
document_count() { query "SELECT count(*) FROM documents_document"; }
mail_account_password() { query "SELECT password FROM paperless_mail_mailaccount LIMIT 1"; }

originals() { find "${workdir}/media/documents/originals" -type f 2>/dev/null | sort; }
original_count() { originals | wc -l | tr -d '[:space:]'; }
originals_digest() { originals | xargs -r md5sum | md5sum; }
archives() { find "${workdir}/export" -maxdepth 1 -name '*.zip' -type f -printf '%f\n' 2>/dev/null | sort; }

seed_installation() {
  reset_installation
  paperless "
set -euo pipefail
cd '${src_dir}'
python3 manage.py migrate --no-input >/dev/null
python3 manage.py shell < /fixture/seed.py
" | grep -q "^seeded=3$"
}

# =================================================================================================
# Export — the half that runs nightly and is only ever read from once something has gone wrong.
# =================================================================================================

step "Seeding an instance and exporting it with the chart's backup script"
seed_installation || {
  echo "the fixture did not seed three documents" >&2
  exit 1
}
seeded_originals="$(original_count)"
clear_export_volume
paperless "${backup_zip_script}" >/dev/null
check "the export script wrote an archive" test -s "${workdir}/export/fixture-export.zip"
cp -- "${workdir}/export/fixture-export.zip" "${stash}/"

# A zip that opens and holds a manifest, not merely a file of non-zero size: a truncated or
# half-written archive passes `test -s` and fails only months later, during a restore.
if "${python_bin}" - "${stash}/fixture-export.zip" <<'PY'; then
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    if archive.testzip() is not None:
        raise SystemExit("corrupt member in archive")
    names = archive.namelist()

if "manifest.json" not in names:
    raise SystemExit(f"no manifest.json in archive: {names[:10]}")
if not any(name.endswith(".pdf") for name in names):
    raise SystemExit("archive carries a manifest but no documents")
PY
  pass "the archive is a valid zip carrying a manifest and its documents"
else
  fail "the archive is not a usable export"
fi

step "Naming the archive from the template, expanded at run time"
clear_export_volume
paperless "${backup_retention_script}" "${backup_retention_template}" "${backup_retention_count}" >/dev/null
check "exactly one archive was written" test "$(archives | wc -l | tr -d '[:space:]')" = "1"
# The default `nameTemplate` is a strftime string. Helm cannot expand it — a name rendered at
# template time would be frozen at the last upgrade and every run would overwrite one file — so the
# container has to. The shape is asserted rather than a predicted name: `%S` means a name computed
# here is already stale by the time the container writes one, and matching the template's own
# structure is the stronger claim anyway, since a literal `%Y` cannot satisfy it.
if "${python_bin}" - "${backup_retention_template}" "$(archives | head -n 1)" <<'PY'; then
import re
import sys

template, produced = sys.argv[1:3]
widths = {"Y": 4, "m": 2, "d": 2, "H": 2, "M": 2, "S": 2, "y": 2, "j": 3}

pattern, index = "", 0
while index < len(template):
    character = template[index]
    if character == "%" and index + 1 < len(template):
        specifier = template[index + 1]
        pattern += r"\d{%d}" % widths[specifier] if specifier in widths else r".+"
        index += 2
        continue
    pattern += re.escape(character)
    index += 1

if not re.fullmatch(pattern + r"\.zip", produced):
    raise SystemExit(f"{produced!r} is not {template!r} expanded (expected {pattern}.zip)")
PY
  pass "wrote $(archives | head -n 1), expanded by the container rather than by Helm"
else
  fail "the archive name is not the template expanded: $(archives | tr '\n' ' ')"
fi

step "Retaining backup.zip.retentionCount archives and pruning the rest"
# Cleared first: the archive the phase above wrote is minutes newer than any stale one, so leaving
# it would make which archives survive depend on how long that phase took.
clear_export_volume
# Stale archives are placed rather than produced: `nameTemplate` resolves to the second, so real
# runs would need a minute between them to be distinguishable, and the pruning pass reads
# modification time and name — both of which these carry honestly.
for stale in 1 2 3; do
  printf 'stale archive %s' "${stale}" >"${workdir}/export/paperless-export-2020-01-0${stale}T000000Z.zip"
  touch -d "2020-01-0${stale}T00:00:00Z" "${workdir}/export/paperless-export-2020-01-0${stale}T000000Z.zip"
done
check "three stale archives are present before the run" test "$(archives | wc -l | tr -d '[:space:]')" = "3"
paperless "${backup_retention_script}" "${backup_retention_template}" "${backup_retention_count}" >/dev/null
check "only backup.zip.retentionCount archives remain" \
  test "$(archives | wc -l | tr -d '[:space:]')" = "${backup_retention_count}"
check "the two oldest were pruned" test ! -e "${workdir}/export/paperless-export-2020-01-01T000000Z.zip" \
  -a ! -e "${workdir}/export/paperless-export-2020-01-02T000000Z.zip"
check "the newest stale archive was kept" test -e "${workdir}/export/paperless-export-2020-01-03T000000Z.zip"
# Retention that kept only stale archives and discarded the run that just succeeded would satisfy
# a count alone, which is the failure worth naming separately.
check "the archive just written was kept, not pruned in favour of older ones" \
  test "$(archives | grep -cv '^paperless-export-2020-')" = "1"

step "Pruning nothing when the export itself failed"
# `set -euo pipefail` is what makes retention safe: a pruning pass that ran after a failed export
# would trade a good archive for a broken one. The database is dropped to fail the exporter for a
# reason it cannot recover from, which is the shape of the failures that matter here.
before="$(archives)"
docker exec -e PGPASSWORD="${db_password}" "${postgres}" \
  psql -U "${db_user}" -d postgres -q -c "DROP DATABASE IF EXISTS ${db_name} WITH (FORCE)" >/dev/null
if paperless "${backup_retention_script}" "${backup_retention_template}" 1 >/dev/null 2>&1; then
  fail "the export reported success with no database"
else
  pass "the export failed, which is what fails the CronJob"
fi
check "retention did not run, so every archive survived" test "$(archives)" = "${before}"
docker exec -e PGPASSWORD="${db_password}" "${postgres}" \
  psql -U "${db_user}" -d postgres -q -c "CREATE DATABASE ${db_name} OWNER ${db_user}" >/dev/null

step "Keeping the passphrase out of the archive it protects"
seed_installation >/dev/null
clear_export_volume
paperless "${backup_encrypted_script}" encrypted-export >/dev/null
check "the encrypted export wrote an archive" test -s "${workdir}/export/encrypted-export.zip"
cp -- "${workdir}/export/encrypted-export.zip" "${stash}/"
# The unencrypted archive from the first phase is the control: without it, an assertion that a
# secret is absent could be passing because the exporter never wrote it in the first place.
if "${python_bin}" - "${stash}/fixture-export.zip" "${mail_password}" <<'PY'; then
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    blob = b"".join(archive.read(name) for name in archive.namelist() if name.endswith(".json"))

if sys.argv[2].encode() not in blob:
    raise SystemExit("the control archive does not carry the secret, so absence proves nothing")
PY
  pass "an export without a passphrase carries the mail password in clear"
else
  fail "the unencrypted control archive does not carry the secret; the check below proves nothing"
fi
if "${python_bin}" - "${stash}/encrypted-export.zip" "${mail_password}" <<'PY'; then
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    blob = b"".join(archive.read(name) for name in archive.namelist())

if sys.argv[2].encode() in blob:
    raise SystemExit("the secret survived --passphrase in clear")
PY
  pass "an export with a passphrase does not"
else
  fail "backup.passphrase did not encrypt the field it exists to encrypt"
fi

step "Exporting in incremental mode, into a directory rather than an archive"
clear_export_volume
paperless "${backup_incremental_script}" >/dev/null
check "a manifest was written to the export volume root" test -s "${workdir}/export/manifest.json"
check "no archive was written in this mode" test "$(archives | wc -l | tr -d '[:space:]')" = "0"
cp -r -- "${workdir}/export" "${stash}/incremental"

# =================================================================================================
# Import — the half the export exists for.
# =================================================================================================

restore_archive() {
  clear_export_volume
  cp -- "${stash}/$1" "${workdir}/export/"
}

step "Importing a zip export into an empty installation"
reset_installation
restore_archive fixture-export.zip
if paperless "${restore_script}"; then
  pass "the import script succeeded"
else
  fail "the import script failed against an empty installation"
fi
check "every document came back" test "$(document_count)" = "3"
check "every original file came back" test "$(original_count)" = "${seeded_originals}"
check "the completion marker was written" test -f "${workdir}/data/$(basename "${restore_marker}")"
check "the attempt marker was cleared" test ! -e "${workdir}/data/$(basename "${restore_attempt_marker}")"

step "Restarting the pod after a completed import"
before="$(originals_digest)"
if paperless "${restore_script}" | grep -q "import already completed"; then
  pass "the import is skipped on the next start"
else
  fail "the import did not skip itself after completing"
fi
check "nothing on disk changed" test "$(originals_digest)" = "${before}"

# Simulated rather than raced: an attempt is arranged in exactly the state a killed one leaves
# behind — rows loaded, some files copied, the attempt marker present, no completion marker — and
# the script is run again. Actually killing a container mid-copy would test the same code path with
# a stopwatch attached.
step "Retrying an import that died part-way through the copy"
rm -f -- "${workdir}/data/$(basename "${restore_marker}")"
printf '2026-01-01T00:00:00Z\n' >"${workdir}/data/$(basename "${restore_attempt_marker}")"
chmod 0666 "${workdir}/data/$(basename "${restore_attempt_marker}")"
# Removed through `scrub` for the reason it exists: the file is the container's, in a directory the
# container made, so the host cannot unlink it.
newest_original="$(originals | tail -n 1)"
[ -n "${newest_original}" ] || {
  echo "the seeded installation has no originals to take away" >&2
  exit 1
}
scrub "/workdir/${newest_original#"${workdir}/"}"
partial="$(original_count)"
check "the target is in a partial state" test "${partial}" -gt 0 -a "${partial}" -lt "${seeded_originals}"
if paperless "${restore_script}"; then
  pass "the retry succeeded where document_importer alone would have raised FileExistsError"
else
  fail "the retry failed; a killed import is still unrecoverable"
fi
check "every document is present again" test "$(document_count)" = "3"
check "every original file is present again" test "$(original_count)" = "${seeded_originals}"
check "the completion marker was written" test -f "${workdir}/data/$(basename "${restore_marker}")"
check "the attempt marker was cleared" test ! -e "${workdir}/data/$(basename "${restore_attempt_marker}")"

# The reset is only sound while upstream's copy still refuses to overwrite. If this ever passes,
# `document_importer` became idempotent and clearing the tree is no longer worth its risk.
step "Confirming document_importer still refuses to overwrite what it has already copied"
# Captured rather than piped into `grep`: the command is expected to fail, and under `pipefail`
# its exit status would be the pipeline's however well the match went.
output="$(paperless "
set -euo pipefail
cd '${src_dir}'
python3 manage.py document_importer '${restore_source}' --no-progress-bar
" 2>&1 || true)"
if printf '%s' "${output}" | grep -q "FileExistsError"; then
  pass "still raises FileExistsError, so the reset is still required"
else
  fail "document_importer no longer raises FileExistsError over existing files — re-examine whether the chart still needs to clear the media tree before a retry"
  printf '%s\n' "${output}" | tail -n 20
fi

step "Refusing a first import into an installation that already holds documents"
rm -f -- "${workdir}/data/$(basename "${restore_marker}")" \
  "${workdir}/data/$(basename "${restore_attempt_marker}")"
before="$(originals_digest)"
status=0
output="$(paperless "${restore_script}" 2>&1)" || status=$?
check "the container failed, which is what fails the pod" test "${status}" -ne 0
if printf '%s' "${output}" | grep -q "already holds documents"; then
  pass "refused, and said why"
else
  fail "a first import into a populated installation was not refused"
fi
check "nothing was deleted" test "$(originals_digest)" = "${before}"
check "no attempt marker was armed" test ! -e "${workdir}/data/$(basename "${restore_attempt_marker}")"

step "Importing an incremental export, read from the volume root"
reset_installation
clear_export_volume
cp -r -- "${stash}/incremental/." "${workdir}/export/"
if paperless "${restore_directory_script}" "" "" "${restore_directory_source}"; then
  pass "a directory export imports as readily as an archive"
else
  fail "the import script failed against an incremental export"
fi
check "every document came back" test "$(document_count)" = "3"
check "every original file came back" test "$(original_count)" = "${seeded_originals}"

step "Round-tripping an encrypted export"
reset_installation
restore_archive encrypted-export.zip
if paperless "${restore_encrypted_script}" "" "" "${restore_encrypted_source}"; then
  pass "the import script succeeded with the matching passphrase"
else
  fail "an encrypted export could not be imported with the passphrase that produced it"
fi
check "every document came back" test "$(document_count)" = "3"
check "the encrypted field came back in clear" test "$(mail_account_password)" = "${mail_password}"

step "Failing an encrypted import given the wrong passphrase"
reset_installation
restore_archive encrypted-export.zip
status=0
paperless "${restore_encrypted_script}" "" "" "${restore_encrypted_source}" "${wrong_passphrase}" \
  >/dev/null 2>&1 || status=$?
check "the container failed rather than importing the ciphertext as plaintext" test "${status}" -ne 0
check "no completion marker was written" test ! -e "${workdir}/data/$(basename "${restore_marker}")"

printf '\n'
if [ "${failures}" -gt 0 ]; then
  printf '\033[31m%s check(s) failed\033[0m\n' "${failures}"
  exit 1
fi
printf '\033[32mall checks passed\033[0m\n'
