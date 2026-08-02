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
A container running one `bootstrap` subcommand.

Used both as the migration initContainer on every service pod and as the body of the seed
Jobs, so the two can never drift apart. Probes are explicitly disabled: these are one-shot
commands with no listener, and `common.container` would otherwise attach the service probes
from `defaults` and fail the container immediately.

Args: ctx (root), command (bootstrap subcommand), name (container name).
*/}}
{{- define "tankovault.bootstrapContainer" -}}
{{- $root := .ctx -}}
{{- $bootstrap := $root.Values.bootstrap -}}
{{- $values := mergeOverwrite (deepCopy ($root.Values.defaults | default dict)) (dict
      "image" (merge (deepCopy $bootstrap.image) (deepCopy ($root.Values.image | default dict)))
      "imagePullSecrets" ($root.Values.imagePullSecrets | default list)
      "resourcesPreset" $bootstrap.resourcesPreset
      "resources" dict
      "extraEnv" list
      "extraVolumeMounts" list
      "startupProbe" (dict "enabled" false)
      "livenessProbe" (dict "enabled" false)
      "readinessProbe" (dict "enabled" false)) -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
{{- include "common.container" (dict
      "ctx" $ctx
      "name" .name
      "args" (list .command)
      "env" (include "tankovault.env" (dict "ctx" $root) | fromYamlArray)
      "volumeMounts" (include "tankovault.bootstrapVolumeMounts" $root | fromYamlArray)
    ) -}}
{{- end -}}

{{- define "tankovault.bootstrapVolumeMounts" -}}
- name: config
  mountPath: {{ .Values.configReload.configDir | quote }}
  readOnly: true
- name: secrets
  mountPath: {{ .Values.configReload.secretsDir | quote }}
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
A one-shot bootstrap Job.

Args: ctx (root), command, name (resource name suffix), hook, weight.
*/}}
{{- define "tankovault.bootstrapJob" -}}
{{- $root := .ctx -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "common.fullname.suffixed" (dict "ctx" $root "suffix" .name) }}
  namespace: {{ include "common.namespace" $root }}
  labels:
    {{- include "common.labels" $root | nindent 4 }}
    app.kubernetes.io/component: bootstrap
  annotations:
    {{- with (include "common.annotations" $root) }}
    {{- . | nindent 4 }}
    {{- end }}
    "helm.sh/hook": {{ .hook }}
    "helm.sh/hook-weight": {{ .weight | quote }}
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: {{ $root.Values.bootstrap.migrate.backoffLimit }}
  template:
    metadata:
      labels:
        {{- include "common.podLabels" $root | nindent 8 }}
        app.kubernetes.io/component: bootstrap
    spec:
      restartPolicy: Never
      serviceAccountName: {{ include "tankovault.serviceAccountName" $root }}
      automountServiceAccountToken: false
      {{- with (include "common.podSecurityContext" $root) }}
      securityContext:
        {{- . | nindent 8 }}
      {{- end }}
      {{- include "common.imagePullSecrets" $root | nindent 6 }}
      containers:
        {{- include "tankovault.bootstrapContainer" (dict "ctx" $root "command" .command "name" .name) | nindent 8 }}
      volumes:
        {{- include "tankovault.bootstrapVolumes" $root | nindent 8 }}
{{- end -}}
