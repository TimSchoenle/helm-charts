{{/*
The TOML the one-shot bootstrap steps read.

`telemetry.service_name` is required of every binary, and the seed step takes the
administrator's username and email as ordinary configuration — only the password is a
credential, and that arrives as a mounted file like every other one.
*/}}
{{- define "tankovault.bootstrapConfigToml" -}}
{{- $ctx := . -}}
{{- $seed := $ctx.Values.bootstrap.seedAdmin -}}
{{- $derived := dict "telemetry" (dict "service_name" "bootstrap") -}}
{{- if $seed.enabled -}}
{{- $_ := set $derived "seed_admin_username" $seed.username -}}
{{- with $seed.email }}{{- $_ := set $derived "seed_admin_email" . }}{{- end -}}
{{- end -}}
{{- include "tankovault.tomlMerged" (dict "maps" (list $ctx.Values.config $derived)) | trim -}}
{{- end -}}

{{/*
The credential keys the bootstrap steps read.

`auth__password_pepper` is included because `seed-admin` hashes the initial password with it.
If it does not match what the api runs with, the account is created successfully and can then
never log in — a failure that looks like a wrong password rather than a misconfiguration.
*/}}
{{- define "tankovault.bootstrapSecretKeys" -}}
- database__url
- auth__password_pepper
- seed_admin_password
{{- end -}}

{{/*
The subset of those keys `seed-admin` alone reads.

Both are credentials of the account that step creates, and no other subcommand opens either:
the pepper is the hashing parameter and the password is the plaintext being hashed. Named here
rather than inlined below so that `tankovault.bootstrapSecretKeys` stays the one list of what
the image reads, and what follows is visibly a subtraction from it rather than a second copy
free to drift alongside it.
*/}}
{{- define "tankovault.seedAdminSecretKeys" -}}
- auth__password_pepper
- seed_admin_password
{{- end -}}

{{/*
The credential keys `bootstrap migrate` reads: applying a schema needs the database URL and
nothing else.

This exists because the migration is not always a pod of its own. As an initContainer it runs
inside a service pod, and mounting that pod's own `secrets` volume would hand it every
credential the service reads — on `api`, eight keys against the one this command opens. Same
pod, so not a boundary crossing; but a different binary with a different attack surface,
running at a different point in the pod's life, has no call for the token signing key, the MFA
sealing key, the SMTP password or an internal caller token.

That the pepper is not among them is not an inference: `control-plane`, `worker`, `notifier`
and `sync` never project it, and the migration has always run on their pods.

Args: ctx (root).
*/}}
{{- define "tankovault.migrateSecretKeys" -}}
{{- $seedOnly := include "tankovault.seedAdminSecretKeys" . | fromYamlArray -}}
{{- range $key := include "tankovault.bootstrapSecretKeys" . | fromYamlArray -}}
{{- if not (has $key $seedOnly) }}
- {{ $key }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The values tree the bootstrap workloads render against — `.Values.defaults` with the bootstrap
image, its own resources and no probes at all: these are one-shot commands with no listener,
and `common.container` would otherwise attach the service probes and fail them immediately.

Shared by the container and by the Job that carries it so that the two resolve
`readOnlyRootFilesystem` — and therefore the `/tmp` mount and the `tmp` volume that has to back
it — from exactly the same values. Deriving the volumes from anything else produces a pod whose
mounts name a volume it does not define, which the API server rejects outright.

Args: the root context.
*/}}
{{- define "tankovault.bootstrapValues" -}}
{{- $root := . -}}
{{- $bootstrap := $root.Values.bootstrap -}}
{{- $values := mergeOverwrite (deepCopy ($root.Values.defaults | default dict)) (dict
      "image" (merge (deepCopy $bootstrap.image) (deepCopy ($root.Values.image | default dict)))
      "imagePullSecrets" ($root.Values.imagePullSecrets | default list)
      "startupProbe" (dict "enabled" false)
      "livenessProbe" (dict "enabled" false)
      "readinessProbe" (dict "enabled" false)) -}}
{{- /*
  Forced rather than merged: `mergeOverwrite` leaves an empty list in place of a populated one,
  so a service's extra environment and volumes would otherwise leak into a one-shot command
  that has no use for them — and an extra volume the bootstrap container never mounts.
*/ -}}
{{- /*
  Replaced wholesale rather than merged, so `bootstrap.resources` is the whole answer for a
  one-shot command and `defaults.resources` cannot show through key by key.
*/ -}}
{{- $_ := set $values "resources" (deepCopy $bootstrap.resources) -}}
{{- $_ := set $values "extraEnv" list -}}
{{- $_ := set $values "extraVolumeMounts" list -}}
{{- $_ := set $values "extraVolumes" list -}}
{{- toYaml $values -}}
{{- end -}}

{{/*
A container running one `bootstrap` subcommand.

Used both as the migration initContainer on the service pods that need the schema and as the
body of the seed Jobs, so the two can never drift apart.

The environment always names the secrets directory: `tankovault.bootstrapVolumeMounts` mounts
a volume there unconditionally, and both pods this container runs in provide one — the
bootstrap Job through `tankovault.bootstrapVolumes`, and a service pod through
`tankovault.migrateVolumes`.

`secretVolume` names which of them it is, because in a service pod it must not be the volume
the service itself mounts: `secrets` there carries every credential that service reads, and
the migration reads `database__url`. Defaults to `secrets`, the name a bootstrap Job's own pod
gives it.

Args: ctx (root), command (bootstrap subcommand), name (container name),
      secretVolume (optional, the pod volume holding this command's credentials).
*/}}
{{- define "tankovault.bootstrapContainer" -}}
{{- $root := .ctx -}}
{{- $values := include "tankovault.bootstrapValues" $root | fromYaml -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
{{- include "common.container" (dict
      "ctx" $ctx
      "name" .name
      "args" (list .command)
      "env" (include "tankovault.env" (dict "ctx" $root "secrets" true) | fromYamlArray)
      "volumeMounts" (include "tankovault.bootstrapVolumeMounts" (dict
            "ctx" $root
            "secretVolume" (.secretVolume | default "secrets")
          ) | fromYamlArray)
    ) -}}
{{- end -}}

{{/*
Args: ctx (root), secretVolume (the pod volume this command's credentials are projected into).
*/}}
{{- define "tankovault.bootstrapVolumeMounts" -}}
{{- $ctx := .ctx -}}
- name: config
  mountPath: {{ $ctx.Values.configReload.configDir | quote }}
  readOnly: true
- name: {{ .secretVolume }}
  mountPath: {{ $ctx.Values.configReload.secretsDir | quote }}
  readOnly: true
{{- end -}}

{{- define "tankovault.bootstrapVolumes" -}}
{{- $ctx := . -}}
- name: config
  configMap:
    name: {{ include "common.fullname" $ctx }}-bootstrap-config
- name: secrets
  projected:
    defaultMode: 0400
    sources:
      {{- include "tankovault.secretSources" (dict
            "ctx" $ctx
            "keys" (include "tankovault.bootstrapSecretKeys" $ctx | fromYamlArray)
          ) | nindent 6 }}
{{- end -}}

{{/*
The extra volume a service pod carries for the migration initContainer.

A second projection rather than a narrower view of the pod's `secrets` volume: a projected
volume is mounted whole, so projecting the keys twice is the only way to give one container in
a pod fewer of them than another. Built by `tankovault.secretSources` like every other
projection in this chart, so the file the migration reads is byte for byte the one the service
reads — same Secret, same path, same 0400, and the same `externalDatabase.existingSecret`
redirection.

Args: ctx (root).
*/}}
{{- define "tankovault.migrateVolumes" -}}
{{- $ctx := . -}}
- name: migrate-secrets
  projected:
    defaultMode: 0400
    sources:
      {{- include "tankovault.secretSources" (dict
            "ctx" $ctx
            "keys" (include "tankovault.migrateSecretKeys" $ctx | fromYamlArray)
          ) | nindent 6 }}
{{- end -}}

{{/*
The pod template of a bootstrap Job.

Split out from the Job itself so it can be rendered once and used twice — emitted, and hashed
into the Job's name under `ordering: argoSyncWave`. Hashing is only sound because nothing on
this path is non-deterministic: `tankovault.bootstrapValues` and `tankovault.secretSources`
resolve names and keys, never values, so `tankovault.rememberedSecret` and its `randAlphaNum`
fallback are never reached and two renders of the same values agree byte for byte.

Emitted at column zero and re-indented by the caller, so the nesting lives in one place.

Args: ctx (root), command, name (container name).
*/}}
{{- define "tankovault.bootstrapPodTemplate" -}}
{{- $root := .ctx -}}
{{- $values := include "tankovault.bootstrapValues" $root | fromYaml -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
metadata:
  labels:
    {{- include "common.podLabels" $root | nindent 4 }}
    app.kubernetes.io/component: bootstrap
spec:
  restartPolicy: Never
  # See `_workload.tpl` for why. The migration Job reads the same
  # configuration the services do, so it needs the same guarantee.
  enableServiceLinks: false
  serviceAccountName: {{ include "tankovault.serviceAccountName" $root }}
  automountServiceAccountToken: false
  {{- /*
    Rendered against `$ctx`, not `$root`: the identity fields — `runAsUser`, `runAsGroup`
    and above all `fsGroup` — live under `.Values.defaults`, which only the scoped context
    flattens to the top level. Read from `$root` the helper sees no `podSecurityContext`
    at all and emits the bare preset, leaving the pod without an `fsGroup`. The projected
    secrets volume is then owned by root at mode 0400 while the container runs as the
    image's UID 1001, and every bootstrap command dies on the first credential it opens
    with `Permission denied (os error 13)`.
  */}}
  {{- with (include "common.podSecurityContext" $ctx) }}
  securityContext:
    {{- . | nindent 4 }}
  {{- end }}
  {{- include "common.imagePullSecrets" $ctx | nindent 2 }}
  containers:
    {{- include "tankovault.bootstrapContainer" (dict "ctx" $root "command" .command "name" .name) | nindent 4 }}
  volumes:
    {{- include "common.volumes" (dict
          "ctx" $ctx
          "volumes" (include "tankovault.bootstrapVolumes" $root | fromYamlArray)
        ) | nindent 4 }}
{{- end -}}

{{/*
A one-shot bootstrap Job.

`ordering` selects how the Job is sequenced and defaults to `helmHook`, the shape every seed
step keeps: the Helm hook annotations, byte for byte what this chart has always emitted.

`argoSyncWave` is for the migration under Argo CD, and is a different kind of object entirely.
Dropping the hook annotations makes the Job a tracked resource that is re-applied on every
sync — but `spec.template` and `spec.selector` are immutable, so a re-apply under a stable name
either fails with `field is immutable` or is accepted as a no-op that silently never re-runs
the migration. The name therefore carries a digest of the pod template: a new image or spec is
a new object that runs, an unchanged one resolves to the Job already sitting there Complete,
which Argo reads as healthy. All three `helm.sh/hook*` annotations have to go together — Argo
classifies a resource as a hook on the presence of `helm.sh/hook` alone, so a stray
`hook-weight` or `hook-delete-policy` left behind would put the Job back in PreSync and restore
the deadlock this mode exists to avoid. Deliberately no `ttlSecondsAfterFinished`: deleting a
completed Job whose name is stable is precisely what makes the next sync run it again.

Args: ctx (root), command, name (resource name suffix), hook, weight,
      ordering (optional, `helmHook` or `argoSyncWave`).
*/}}
{{- define "tankovault.bootstrapJob" -}}
{{- $root := .ctx -}}
{{- $ordering := .ordering | default "helmHook" -}}
{{- $podTemplate := include "tankovault.bootstrapPodTemplate" (dict "ctx" $root "command" .command "name" .name) -}}
{{- $name := include "common.fullname.suffixed" (dict "ctx" $root "suffix" .name) -}}
{{- if eq $ordering "argoSyncWave" -}}
{{- $name = include "common.fullname.hashed" (dict "ctx" $root "suffix" .name "content" $podTemplate) -}}
{{- end -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ $name }}
  namespace: {{ include "common.namespace" $root }}
  labels:
    {{- include "common.labels" $root | nindent 4 }}
    app.kubernetes.io/component: bootstrap
  annotations:
    {{- with (include "common.annotations" $root) }}
    {{- . | nindent 4 }}
    {{- end }}
    {{- if eq $ordering "argoSyncWave" }}
    "argocd.argoproj.io/sync-wave": {{ $root.Values.bootstrap.migrate.argoSyncWaveBase | quote }}
    {{- else }}
    "helm.sh/hook": {{ .hook }}
    "helm.sh/hook-weight": {{ .weight | quote }}
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
    {{- end }}
spec:
  backoffLimit: {{ $root.Values.bootstrap.migrate.backoffLimit }}
  template:
    {{- $podTemplate | nindent 4 }}
{{- end -}}
