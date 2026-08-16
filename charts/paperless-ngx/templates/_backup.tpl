{{/*
Export and import, built on the two management commands upstream ships for them.

Why these are shaped so differently
-----------------------------------
`document_exporter` reads the archive and writes somewhere else, so it is a scheduled Job. It
takes the application's own media lock while it runs, which means it coexists with a live
instance rather than needing one stopped.

`document_importer` is the opposite in every respect: it writes the archive, refuses to run
against an installation that already holds documents, and must finish before the consumer or the
scheduler touches anything. No separately-scheduled pod can promise that ordering, so the import
is an `initContainer` on the application pod itself — which also gives it the volumes without a
second copy, works under `ReadWriteOnce`, and fails the pod instead of starting a half-imported
archive.

The image's entrypoint is not usable for either of them: it is `/init`, which brings up the whole
s6-overlay supervision tree — webserver, workers, scheduler. Neither are the `document_exporter`
and `document_importer` wrappers in `/usr/local/bin`, despite being exactly the commands wanted:
their shebang is `with-contenv`, which execs `s6-envdir /run/s6/container_environment`, and that
directory is written by the s6 init step a one-shot container never runs — so the wrapper exits
with `unable to envdir ...: No such file or directory` before it ever reaches Python.

Both commands therefore override `command` and call `manage.py` directly. That is all the wrappers
themselves do once their uid dispatch has taken the non-root branch, which is the only branch this
chart's `podSecurityContext` can reach.

Every environment variable and every mount is derived from the same helpers the Deployment uses.
That is the point of this file — a backup container whose configuration has drifted from the
application's does not fail, it quietly dumps the wrong database.
*/}}

{{/*
Name of the backup objects.
*/}}
{{- define "paperless-ngx.backup.name" -}}
{{- include "common.fullname.suffixed" (dict "ctx" . "suffix" "backup") -}}
{{- end -}}

{{/*
The values tree the backup pod renders against.

Mostly the application's, because that is what keeps the two from drifting: the same image, the
same security contexts, the same `extraEnv`, `extraVolumes` and `extraVolumeMounts` — an operator
who mounted a Secret to satisfy a `PAPERLESS_<NAME>_FILE` setting needs it here too, or the
exporter connects to a different database than the one it is meant to back up.

What is deliberately replaced:

  probes       all three off. These are one-shot commands with no listener, and `common.container`
               would otherwise attach the HTTP probes and fail every one of them.
  resources    the backup's own. The exporter copies files and serialises rows; it never runs
               OCR, so the application's OCR-sized limit is an order of magnitude too generous.
  placement    `affinity` carries the co-scheduling rule (see `paperless-ngx.backup.affinity`),
               and `podAntiAffinity` is cleared so `common.affinity` cannot fall through to the
               spread rule and quietly discard it. `topologySpreadConstraints` goes for the same
               reason: spreading a single one-shot pod is meaningless and can only conflict with
               a required affinity.

Args: the root context.
*/}}
{{- define "paperless-ngx.backup.values" -}}
{{- $root := . -}}
{{- $backup := $root.Values.backup -}}
{{- $values := mergeOverwrite (deepCopy $root.Values) (dict
      "nameOverride" (include "common.name" $root)
      "resourcesPreset" $backup.resourcesPreset
      "resources" (deepCopy $backup.resources)
      "startupProbe" (dict "enabled" false)
      "livenessProbe" (dict "enabled" false)
      "readinessProbe" (dict "enabled" false)
      "podAntiAffinity" ""
      "affinity" (include "paperless-ngx.backup.affinity" $root | fromYaml)
      "podLabels" (deepCopy $backup.podLabels)
      "podAnnotations" (deepCopy $backup.podAnnotations)) -}}
{{- /*
  Forced rather than merged: `mergeOverwrite` leaves a populated list in place when the override
  is empty, so the application's own scheduling constraints would survive an explicit empty one
  here — and `resources` has to be replaced wholesale for `resourcesPreset` to have any effect at
  all, since `common.resources` prefers an explicit block.
*/ -}}
{{- $_ := set $values "topologySpreadConstraints" list -}}
{{- if not $backup.resources -}}
{{- $_ := set $values "resources" dict -}}
{{- end -}}
{{- with $backup.nodeSelector }}{{- $_ := set $values "nodeSelector" (deepCopy .) -}}{{- end -}}
{{- with $backup.tolerations }}{{- $_ := set $values "tolerations" (deepCopy .) -}}{{- end -}}
{{- toYaml $values -}}
{{- end -}}

{{- define "paperless-ngx.backup.context" -}}
{{- $root := . -}}
{{- toYaml (dict
      "Values" (include "paperless-ngx.backup.values" $root | fromYaml)
      "Chart" $root.Chart
      "Release" $root.Release
      "Capabilities" $root.Capabilities
      "Template" $root.Template
      "Files" $root.Files) -}}
{{- end -}}

{{/*
Pod annotations for the backup pod: the application's, plus the ConfigMap and Secret checksums,
plus `backup.podAnnotations` last.

The checksums matter as much here as on the Deployment. They are what makes a change to the
application's configuration reach the exporter — without them a CronJob created months ago keeps
its original pod template, and the next backup runs against whatever database address it was
created with rather than the one the application moved to.

`common.podAnnotations` is called with the *root* context rather than the backup's, because it
renders the ConfigMap and Secret templates to hash them and those objects belong to the release,
not to this pod. Inheriting `podAnnotations` along the way is deliberate and matches how
`podLabels`, `nodeSelector` and `tolerations` behave: the backup's own values layer over the
application's rather than replacing them.

Args: the root context.
*/}}
{{- define "paperless-ngx.backup.podAnnotations" -}}
{{- $ctx := . -}}
{{- $annotations := include "common.podAnnotations" (dict "ctx" $ctx "templates" (list "configmap.yaml" "secret.yaml")) | fromYaml -}}
{{- $annotations = mustMergeOverwrite $annotations (deepCopy ($ctx.Values.backup.podAnnotations | default dict)) -}}
{{- with $annotations -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $ctx) -}}
{{- end -}}
{{- end -}}

{{/*
Where the backup pod is allowed to run.

`backup.coScheduleWithApp` pins it to the node the application pod is on, with a *required*
podAffinity. This is what makes the chart's default `ReadWriteOnce` claims usable: an RWO volume
can be mounted by any number of pods sharing a node and cannot be attached to two nodes at all,
so without this the Job either lands correctly by luck or sits Pending until its deadline — which
presents as a backup that has silently not run for weeks.

The topology key is `kubernetes.io/hostname` and nothing else. A zone-level term would satisfy
the affinity while still failing to attach the volume, which is worse than no rule at all.

An explicit `backup.affinity` replaces this entirely; the two together are rejected in
`validateValues` rather than silently resolved, because either answer would surprise somebody.
*/}}
{{- define "paperless-ngx.backup.affinity" -}}
{{- $ctx := . -}}
{{- if $ctx.Values.backup.affinity -}}
{{- include "common.tplvalues.render" (dict "value" $ctx.Values.backup.affinity "context" $ctx) -}}
{{- else if $ctx.Values.backup.coScheduleWithApp -}}
podAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchLabels:
          {{- include "paperless-ngx.selectorLabels" $ctx | nindent 10 }}
      topologyKey: kubernetes.io/hostname
{{- end -}}
{{- end -}}

{{/*
The two variables the image leaves to its s6 init step, which a container that overrides `command`
never runs.

`init-start` writes them into `/run/s6/container_environment` at container start, and the scripts
in `/usr/local/bin` read them back through their `with-contenv` shebang. Nothing sets them for a
one-shot command, so both arrive empty:

  PAPERLESS_SRC_DIR   where `manage.py` lives; the scripts below `cd` into it, exactly as the
                      image's own wrappers do. Empty happens to leave the process in the image's
                      WORKDIR, which is the same directory — so the command works by luck, and
                      stops working the day the WORKDIR moves.
  HOME                Python and Django write dotfiles into it. Left unset, kubelet defaults it to
                      `/root`, which uid 1000 cannot write.

Stated here rather than assumed, so the failure is impossible rather than unlikely.
*/}}
{{- define "paperless-ngx.oneShot.env" -}}
- name: PAPERLESS_SRC_DIR
  value: /usr/src/paperless/src
- name: HOME
  value: /usr/src/paperless
{{- end -}}

{{/*
The directories the same init step creates, for the same reason.

`init-folders` runs before anything else in the image and makes the working tree the application
assumes: the four volume roots, the scratch directory, and `data/index`. A container that
overrides `command` skips it, and every one of those is then a directory some code path expects to
exist rather than to create.

`data/index` is the one that actually bites. `document_importer` finishes by rebuilding the search
index, and `wipe_index` iterates the index directory without creating it — so a fresh import copies
the entire archive into place and then dies on `FileNotFoundError: .../data/index`. Because that is
after the copy and before the marker, the next start finds a populated media tree and a completed
import that was never recorded, which is the shape the retry logic exists to survive but should
never have had to.

The defaults and the environment variables that override them are the image's own, expanded here
by the same `${VAR:-default}` form its script uses, so the two cannot disagree about where anything
lives. Emitted as one list for both one-shot containers rather than trimmed per container: a
`mkdir -p` too many costs nothing, and two hand-tuned lists would drift.
*/}}
{{- define "paperless-ngx.oneShot.dirs" -}}
data_dir="${PAPERLESS_DATA_DIR:-/usr/src/paperless/data}"
media_dir="${PAPERLESS_MEDIA_ROOT:-/usr/src/paperless/media}"
mkdir -p -- \
  /usr/src/paperless/export \
  "${data_dir}" \
  "${data_dir}/index" \
  "${media_dir}" \
  "${media_dir}/documents/originals" \
  "${media_dir}/documents/thumbnails" \
  "${PAPERLESS_CONSUMPTION_DIR:-/usr/src/paperless/consume}" \
  "${PAPERLESS_SCRATCH_DIR:-/tmp/paperless}"
{{- end -}}

{{/*
Environment for the backup container.

The application's own, plus the export parameters. Those are passed as variables rather than
baked into the command line for two different reasons: the passphrase must not appear in the pod
spec, where `kubectl describe pod` and the Helm release object would both carry it, and the
archive name is a `strftime` string that has to be expanded by the container at run time — a name
rendered by Helm would be frozen at the last `helm upgrade` and every run would overwrite one
file.

The prefix is `BACKUP_`, not `PAPERLESS_`: the application reads its entire configuration from
`PAPERLESS_*`, and `PAPERLESS_EXPORT_DIR` is already a real setting.
*/}}
{{- define "paperless-ngx.backup.env" -}}
{{- $ctx := . -}}
{{- $backup := $ctx.Values.backup -}}
{{- include "paperless-ngx.env" $ctx }}
{{ include "paperless-ngx.oneShot.env" $ctx }}
- name: BACKUP_TARGET
  value: {{ $backup.target | quote }}
{{- if eq $backup.mode "zip" }}
- name: BACKUP_ZIP_NAME_TEMPLATE
  value: {{ $backup.zip.nameTemplate | quote }}
- name: BACKUP_RETENTION
  value: {{ $backup.zip.retentionCount | quote }}
{{- end }}
{{- if include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $backup.passphrase) }}
- name: BACKUP_PASSPHRASE
  valueFrom:
    secretKeyRef:
      name: {{ include "common.secretName" $ctx }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "export-passphrase" "override" $backup.existingSecretKey) }}
{{- end }}
{{- end -}}

{{/*
The exporter's flags, one per line, ready to be appended to a bash array.

`--no-progress-bar` is unconditional: the progress bar writes carriage returns to a log nobody is
watching interactively, and upstream provides this flag precisely for scripted use.
*/}}
{{- define "paperless-ngx.backup.flags" -}}
{{- $backup := .Values.backup -}}
{{- $options := $backup.options -}}
--no-progress-bar
--batch-size {{ $options.batchSize }}
{{- if eq $backup.mode "dataOnly" }}
--data-only
{{- end }}
{{- if and $options.delete (ne $backup.mode "zip") }}
--delete
{{- end }}
{{- if $options.compareChecksums }}
--compare-checksums
{{- end }}
{{- if $options.compareJson }}
--compare-json
{{- end }}
{{- if $options.useFilenameFormat }}
--use-filename-format
{{- end }}
{{- if $options.splitManifest }}
--split-manifest
{{- end }}
{{- if $options.noArchive }}
--no-archive
{{- end }}
{{- if $options.noThumbnail }}
--no-thumbnail
{{- end }}
{{- if $options.useFolderPrefix }}
--use-folder-prefix
{{- end }}
{{- end -}}

{{/*
The export script.

Written as bash rather than a bare `args` list because three things have to happen at run time
and cannot be expressed in a pod spec: the archive name has to be expanded from a `strftime`
template by `date`, the passphrase has to be read from the environment instead of appearing in
argv, and old archives have to be pruned after — and only after — the exporter has succeeded.

`set -euo pipefail` is what makes the last part safe. A pruning pass that runs after a failed
export would delete yesterday's good archive on the strength of today's broken one.
*/}}
{{- define "paperless-ngx.backup.script" -}}
{{- $ctx := . -}}
{{- $backup := $ctx.Values.backup -}}
set -euo pipefail

{{ include "paperless-ngx.oneShot.dirs" $ctx }}
mkdir -p -- "${BACKUP_TARGET}"

args=(
{{- range $flag := (include "paperless-ngx.backup.flags" $ctx | trim | splitList "\n") }}
  {{ $flag }}
{{- end }}
)

{{- if include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $backup.passphrase) }}

args+=(--passphrase "${BACKUP_PASSPHRASE}")
{{- end }}

{{- if eq $backup.mode "zip" }}

# Expanded here and not by Helm: a name rendered at template time is fixed at the moment of the
# last upgrade, and every nightly run would write over the same file.
zip_name="$(date -u +"${BACKUP_ZIP_NAME_TEMPLATE}")"
args+=(--zip --zip-name "${zip_name}")

echo "exporting to ${BACKUP_TARGET}/${zip_name}.zip"
{{- else }}

echo "exporting to ${BACKUP_TARGET}"
{{- end }}

# `manage.py` and not `/usr/local/bin/document_exporter`: that wrapper's `with-contenv` shebang
# needs an `/run/s6/container_environment` only the image's init step creates, and this container
# does not run it. The `cd` is the wrapper's, kept in a subshell so the retention pass below still
# resolves `BACKUP_TARGET` against the directory it was created in.
(cd -- "${PAPERLESS_SRC_DIR}" && python3 manage.py document_exporter "${BACKUP_TARGET}" "${args[@]}")

{{- if eq $backup.mode "zip" }}

# Retention runs only because the export above succeeded — `set -e` would have left before here
# otherwise. Pruning on a failed run would trade a good archive for a broken one.
cd -- "${BACKUP_TARGET}"
mapfile -t archives < <(ls -1t -- *.zip 2>/dev/null || true)
if [ "${#archives[@]}" -gt "${BACKUP_RETENTION}" ]; then
  for stale in "${archives[@]:${BACKUP_RETENTION}}"; do
    echo "pruning ${stale}"
    rm -f -- "${stale}"
  done
fi
echo "kept ${BACKUP_RETENTION} of ${#archives[@]} archive(s)"
{{- end }}

echo "export complete"
{{- end -}}

{{/*
Volumes and mounts for the backup pod.

Narrower than the application's on purpose. `consume` is absent — the exporter never reads the
drop box, and every additional claim is another constraint the pod has to satisfy to be scheduled
at all. `tmp` is its own volume rather than the application's `persistence.scratchSizeLimit`,
because a `zip` export assembles the entire export tree there before compressing it and the
application's 2 GiB default would evict the pod partway through.
*/}}
{{- define "paperless-ngx.backup.volumes" -}}
{{- $ctx := . -}}
{{- range $volume := list "media" "data" "export" }}
{{ include "paperless-ngx.dataVolume" (dict "ctx" $ctx "volume" $volume) }}
{{- end }}
- name: tmp
{{- with $ctx.Values.backup.scratch.existingClaim }}
  persistentVolumeClaim:
    claimName: {{ . }}
{{- else }}
{{- with $ctx.Values.backup.scratch.sizeLimit }}
  emptyDir:
    sizeLimit: {{ . }}
{{- else }}
  emptyDir: {}
{{- end }}
{{- end }}
{{- end -}}

{{- define "paperless-ngx.backup.volumeMounts" -}}
- name: media
  mountPath: /usr/src/paperless/media
- name: data
  mountPath: /usr/src/paperless/data
- name: export
  mountPath: /usr/src/paperless/export
- name: tmp
  mountPath: /tmp
{{- end -}}

{{/*
The container running the export.

`bash` and not the wrapper's own shebang: the script above needs arrays and `mapfile`.

Args: ctx (root), scoped (the backup render context).
*/}}
{{- define "paperless-ngx.backup.container" -}}
{{- $root := .ctx -}}
{{- include "common.container" (dict
      "ctx" .scoped
      "name" "export"
      "command" (list "/usr/bin/bash" "-c")
      "args" (list (include "paperless-ngx.backup.script" $root))
      "env" (include "paperless-ngx.backup.env" $root | fromYamlArray)
      "envFrom" (list (dict "configMapRef" (dict "name" (include "common.configMapName" $root))))
      "volumeMounts" (include "paperless-ngx.backup.volumeMounts" $root | fromYamlArray)
    ) -}}
{{- end -}}

{{/*
The operator's upload container, with the export volume attached.

Rendered verbatim, because the chart has no business knowing whether the export is going to object
storage, a restic repository or another cluster. Its own credentials come from its own Secret,
referenced in its own `env`.

Ordering comes from the pod, not from a script: with an uploader configured the exporter becomes
an `initContainer`, so the upload cannot start until the export has completed successfully, and a
failed export never reaches it.

Three things are supplied, and only where the container did not state them itself:

  volumeMounts     the export volume, appended. Without it there is nothing to upload, and
                   requiring the operator to name a volume this chart owns would be a detail to
                   get wrong for no benefit.
  resources        the backup's own sizing. A container with no limits is not a stylistic
                   difference — it is a pod that can consume a node, and this chart claims a
                   baseline that would otherwise hold for every container it authors and none of
                   the ones it is handed.
  securityContext  the same restricted baseline. Both are defaults rather than overrides: a
                   container that states either keeps what it states.
*/}}
{{- define "paperless-ngx.backup.uploadContainer" -}}
{{- $root := .ctx -}}
{{- $upload := $root.Values.backup.upload -}}
{{- $container := deepCopy $upload.container -}}
{{- $_ := set $container "volumeMounts" (append ($container.volumeMounts | default list) (dict "name" "export" "mountPath" $upload.mountPath)) -}}
{{- if not $container.resources -}}
{{- with (include "common.resources" .scoped | fromYaml) -}}
{{- $_ := set $container "resources" . -}}
{{- end -}}
{{- end -}}
{{- if not $container.securityContext -}}
{{- with (include "common.containerSecurityContext" .scoped | fromYaml) -}}
{{- $_ := set $container "securityContext" . -}}
{{- end -}}
{{- end -}}
{{- include "common.tplvalues.render" (dict "value" (list $container) "context" $root) -}}
{{- end -}}

{{/*
Environment for the import container: the application's own, plus the import parameters.
*/}}
{{- define "paperless-ngx.restore.env" -}}
{{- $ctx := . -}}
{{- $restore := $ctx.Values.restore -}}
{{- include "paperless-ngx.env" $ctx }}
{{ include "paperless-ngx.oneShot.env" $ctx }}
- name: RESTORE_SOURCE
  value: {{ include "paperless-ngx.restore.sourcePath" $ctx | quote }}
- name: RESTORE_MARKER
  value: {{ printf "/usr/src/paperless/data/%s" $restore.markerFile | quote }}
- name: RESTORE_ATTEMPT_MARKER
  value: {{ printf "/usr/src/paperless/data/%s.attempt" $restore.markerFile | quote }}
{{- if include "paperless-ngx.restore.usesUrl" $ctx }}
- name: RESTORE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "common.secretName" $ctx }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "restore-url" "override" $restore.source.urlExistingSecretKey) }}
{{- with $restore.source.checksum }}
- name: RESTORE_CHECKSUM
  value: {{ . | quote }}
{{- end }}
{{- end }}
{{- if include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $restore.passphrase) }}
- name: RESTORE_PASSPHRASE
  valueFrom:
    secretKeyRef:
      name: {{ include "common.secretName" $ctx }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "import-passphrase" "override" $restore.existingSecretKey) }}
{{- end }}
{{- end -}}

{{/*
Absolute path of the export to import — a directory holding `manifest.json`, or a `.zip` written
by `backup.mode: zip`, both of which `document_importer` accepts.

For a URL source this is where the download lands: a fixed name rather than anything derived from
the URL, because a presigned URL's last path segment is followed by a query string and is not a
filename.

It goes in a subdirectory of the export volume rather than at its root, and that placement is
load-bearing. `backup.mode: zip` retains the newest `backup.zip.retentionCount` files matching
`*.zip` in the export directory — a downloaded archive sitting beside them would be counted as one
of the retained backups and could push a real one out.
*/}}
{{- define "paperless-ngx.restore.sourcePath" -}}
{{- $restore := .Values.restore -}}
{{- if include "paperless-ngx.restore.usesUrl" . -}}
/usr/src/paperless/export/.restore/archive.zip
{{- else -}}
{{- $root := ternary "/usr/src/paperless/export" "/restore" $restore.source.useExportVolume -}}
{{- with $restore.source.subPath -}}
{{- printf "%s/%s" $root (trimPrefix "/" .) -}}
{{- else -}}
{{- $root -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Whether the export is fetched from a URL rather than read from a volume. The URL may come from
this chart's Secret or from an operator's, so this cannot simply test `restore.source.url`.
*/}}
{{- define "paperless-ngx.restore.usesUrl" -}}
{{- if and .Values.restore.enabled (include "paperless-ngx.hasCredential" (dict "ctx" . "value" .Values.restore.source.url)) -}}
{{- if not (or .Values.restore.source.existingClaim .Values.restore.source.useExportVolume) -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The import script.

The marker file is the whole of the run-exactly-once mechanism, and it lives on the data volume
rather than in the release: an initContainer runs on every pod start, and `restore.enabled` left
set — which is the normal state of affairs, since values are not usually edited back out after a
restore — would otherwise re-run the import on every restart and fail the pod against its own
imported data.

It is written last, only after `document_importer` returns successfully. An import interrupted
half-way leaves no marker, so the next start retries it; that is the right behaviour, because the
alternative is an instance that comes up serving a partial archive and says nothing.

Making that retry actually succeed takes a second marker, because `document_importer` is only
half restartable. Its database load is an upsert — `bulk_create(update_conflicts=True)` — so
re-running it over rows a failed attempt already inserted is a no-op. Its file copy is not: it
opens with `if Path(document.source_path).is_file(): raise FileExistsError(...)` and upstream
offers no flag to skip or overwrite. So an attempt killed part-way through the copy — OOM, a
drained node, a deadline — leaves files behind that make every later attempt die on the first one
of them. Without intervention that is a permanent crash loop, and the traceback names a document
rather than the cause.

The `.attempt` marker turns that into a retry. It is written *before* the import and removed only
on success, so finding it on a later start proves an earlier attempt did not finish, and the only
thing that can have written into the media document tree is that attempt — which makes clearing
the tree and importing again safe rather than merely convenient.

That proof is why the first attempt refuses to start against a tree that already holds files. It
is not a second opinion on the `restore.enabled` contract; it is what establishes the invariant
the reset relies on. Existing *rows* are deliberately not part of it: this chart provisions
`paperless.admin.user` on first start, and documents recorded without their files are exactly the
inconsistency an import repairs.
*/}}
{{- define "paperless-ngx.restore.script" -}}
{{- $ctx := . -}}
{{- $restore := $ctx.Values.restore -}}
set -euo pipefail

if [ -e "${RESTORE_MARKER}" ]; then
  echo "import already completed on $(cat -- "${RESTORE_MARKER}" 2>/dev/null || echo 'an earlier start'); nothing to do"
  exit 0
fi

{{- if include "paperless-ngx.restore.usesUrl" $ctx }}

if [ -z "${RESTORE_URL:-}" ]; then
  echo "restore.source.url resolved to nothing. With existingSecret set the URL is read from its {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "restore-url" "override" $restore.source.urlExistingSecretKey) }} key; check that the key exists." >&2
  exit 1
fi

# A subdirectory of the export volume, not its root: `backup.mode: zip` retains the newest
# archives matching *.zip there, and a downloaded one beside them would be counted as a backup.
mkdir -p -- "$(dirname -- "${RESTORE_SOURCE}")"

# Downloaded under a temporary name and moved into place only once curl has succeeded, so an
# interrupted transfer is never mistaken for a finished archive by the retry that follows it.
# `--fail` is what makes that true for HTTP errors as well: without it curl writes the 403 body
# to the file and exits 0, and the import fails much later on a zip that is really an XML error.
if [ -e "${RESTORE_SOURCE}" ]; then
  echo "reusing the archive already downloaded to ${RESTORE_SOURCE}"
else
  echo "downloading the export"
  curl --fail --location --show-error --silent \
       --retry 3 --retry-delay 5 --retry-connrefused \
       --output "${RESTORE_SOURCE}.part" -- "${RESTORE_URL}"
  mv -- "${RESTORE_SOURCE}.part" "${RESTORE_SOURCE}"
fi

{{- if $restore.source.checksum }}

# Checked on every start, not only after a fresh download: the point is to reject a truncated or
# substituted archive before it is half-imported, and a reused file deserves the same scrutiny.
echo "verifying the archive"
echo "${RESTORE_CHECKSUM}  ${RESTORE_SOURCE}" | sha256sum --check --strict -
{{- else }}

echo "WARNING: restore.source.checksum is empty, so the downloaded archive is imported unverified"
{{- end }}
{{- else }}

if [ ! -e "${RESTORE_SOURCE}" ]; then
  echo "no export at ${RESTORE_SOURCE} — check restore.source.subPath against what the claim actually holds" >&2
  exit 1
fi
{{- end }}

cd -- "${PAPERLESS_SRC_DIR}"

{{ include "paperless-ngx.oneShot.dirs" $ctx }}

# Asked of Django rather than assumed. The importer copies into ORIGINALS_DIR, ARCHIVE_DIR,
# THUMBNAIL_DIR and SHARE_LINK_BUNDLE_DIR, which are the four subdirectories of MEDIA_ROOT's
# `documents/` and nothing else lives there — but MEDIA_ROOT moves with PAPERLESS_MEDIA_ROOT, and
# a path this script guessed is not one it may delete.
documents_dir="$(python3 manage.py shell -c 'from django.conf import settings; print("documents_dir=" + str(settings.ORIGINALS_DIR.parent))' | sed -n 's/^documents_dir=//p')"

# The shape is checked as well as the value: `rm` reached through an empty or unexpected variable
# is the one failure in this script that cannot be undone by running it again.
case "${documents_dir}" in
  /*/documents) ;;
  *)
    echo "could not resolve the media document directory from Django (got '${documents_dir}'); refusing to go further" >&2
    exit 1
    ;;
esac
mkdir -p -- "${documents_dir}"

if [ -e "${RESTORE_ATTEMPT_MARKER}" ]; then
  # Only reachable when a previous attempt wrote this marker and never reached the success path,
  # so everything below ${documents_dir} was copied there by that attempt. document_importer
  # refuses to overwrite a file it has already copied, so a clean tree is the only state it can
  # be retried from.
  echo "an import started on $(cat -- "${RESTORE_ATTEMPT_MARKER}" 2>/dev/null || echo 'an earlier start') did not finish"
  echo "clearing the partial copy under ${documents_dir} and importing again"
  find -- "${documents_dir}" -mindepth 1 -delete
else
  if [ -n "$(find -- "${documents_dir}" -mindepth 1 -type f -print -quit)" ]; then
    echo "${documents_dir} already holds documents, and this release has not started an import before — so these belong to an existing installation, not to a failed attempt." >&2
    echo "document_importer cannot merge into one: it raises FileExistsError on the first document whose file is already there, part-way through the copy. Import onto a fresh release, or clear the media and data volumes first." >&2
    exit 1
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ > "${RESTORE_ATTEMPT_MARKER}"
fi

{{- if $restore.runMigrations }}

# document_importer inserts rows; it does not create the schema. Against the empty database it
# requires, it would otherwise fail on the first table.
echo "applying database migrations"
python3 manage.py migrate --no-input
{{- end }}

args=(
  --no-progress-bar
  --batch-size {{ $restore.batchSize }}
)
{{- if include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $restore.passphrase) }}

args+=(--passphrase "${RESTORE_PASSPHRASE}")
{{- end }}

echo "importing from ${RESTORE_SOURCE}"
# `manage.py` and not `/usr/local/bin/document_importer`: that wrapper's `with-contenv` shebang
# needs an `/run/s6/container_environment` only the image's init step creates, and this container
# does not run it. The wrapper's own `cd` has already happened above.
python3 manage.py document_importer "${RESTORE_SOURCE}" "${args[@]}"

date -u +%Y-%m-%dT%H:%M:%SZ > "${RESTORE_MARKER}"
rm -f -- "${RESTORE_ATTEMPT_MARKER}"
echo "import complete; API tokens are not part of an export and have to be re-issued"
{{- end -}}

{{/*
Mounts for the import container: the application's, plus the source claim when the export is not
being read from this release's own export volume.
*/}}
{{- define "paperless-ngx.restore.volumeMounts" -}}
{{- $ctx := . -}}
{{- include "paperless-ngx.volumeMounts" $ctx }}
{{- if include "paperless-ngx.restore.usesSourceClaim" $ctx }}
- name: restore-source
  mountPath: /restore
  readOnly: true
{{- end }}
{{- end -}}

{{/*
The import container, as the application pod's `initContainer`.

It renders against the backup's scoped context for the same reason the exporter does — the
application's HTTP probes must not be attached to a one-shot command — with the import's own
resource sizing substituted, because deserialising a large manifest is a memory cost the export
does not have.

Args: ctx (root).
*/}}
{{- define "paperless-ngx.restore.container" -}}
{{- $root := .ctx -}}
{{- $values := include "paperless-ngx.backup.values" $root | fromYaml -}}
{{- $_ := set $values "resourcesPreset" $root.Values.restore.resourcesPreset -}}
{{- $_ := set $values "resources" (deepCopy $root.Values.restore.resources) -}}
{{- /*
  Cleared so the initContainer inherits the application pod's own placement, which is not
  negotiable: it *is* the application pod. The backup's co-scheduling affinity would be nonsense
  here, and `common.container` reads none of it — but leaving it in the values would invite the
  next reader to assume it does.
*/ -}}
{{- $_ := set $values "affinity" dict -}}
{{- $scoped := dict
      "Values" $values
      "Chart" $root.Chart
      "Release" $root.Release
      "Capabilities" $root.Capabilities
      "Template" $root.Template
      "Files" $root.Files -}}
{{- include "common.container" (dict
      "ctx" $scoped
      "name" "import"
      "command" (list "/usr/bin/bash" "-c")
      "args" (list (include "paperless-ngx.restore.script" $root))
      "env" (include "paperless-ngx.restore.env" $root | fromYamlArray)
      "envFrom" (list (dict "configMapRef" (dict "name" (include "common.configMapName" $root))))
      "volumeMounts" (include "paperless-ngx.restore.volumeMounts" $root | fromYamlArray)
    ) -}}
{{- end -}}
