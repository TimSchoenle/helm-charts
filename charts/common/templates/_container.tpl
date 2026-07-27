{{/*
The application container, rendered as a YAML list item.

Everything shared across charts (image reference, pull policy, security context, probes,
resources, the writable /tmp mount that a read-only root filesystem requires) comes from
values; everything chart-specific is passed in.

Arguments:
  ctx           (required) root context
  name          container name                          (default: .Chart.Name)
  command       list                                    (optional)
  args          list                                    (optional)
  ports         list of container ports                 (optional)
  env           list of env vars                        (optional)
  envFrom       list of envFrom sources                 (optional)
  volumeMounts  list of additional volume mounts        (optional)
  image         image dict                              (default: .Values.image)

Usage:
  containers:
    {{- include "common.container" (dict "ctx" $ "ports" (list (dict "name" "http" "containerPort" 8080 "protocol" "TCP"))) | nindent 8 }}
*/}}
{{- define "common.container" -}}
{{- $ctx := .ctx -}}
- name: {{ .name | default $ctx.Chart.Name }}
  image: {{ include "common.image" (dict "ctx" $ctx "image" .image) | quote }}
  imagePullPolicy: {{ include "common.imagePullPolicy" (dict "ctx" $ctx "image" .image) }}
  {{- with (include "common.containerSecurityContext" $ctx) }}
  securityContext:
    {{- . | nindent 4 }}
  {{- end }}
  {{- with .command }}
  command:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  {{- with .args }}
  args:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  {{- with .ports }}
  ports:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  {{- with .envFrom }}
  envFrom:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  {{- $env := concat (.env | default list) ($ctx.Values.extraEnv | default list) }}
  {{- with $env }}
  env:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  {{- with (include "common.probes" $ctx) }}
  {{- . | nindent 2 }}
  {{- end }}
  {{- with (include "common.resources" $ctx) }}
  resources:
    {{- . | nindent 4 }}
  {{- end }}
  {{- with (include "common.volumeMounts" (dict "ctx" $ctx "volumeMounts" .volumeMounts)) }}
  volumeMounts:
    {{- . | nindent 4 }}
  {{- end }}
{{- end -}}

{{/*
Volume mounts for the application container.

Prepends the writable /tmp mount whenever the container runs with a read-only root
filesystem and the caller has not already mounted something there, then appends
caller-provided mounts and `.Values.extraVolumeMounts`.

Arguments:
  ctx           (required) root context
  volumeMounts  list of chart-specific mounts (optional)
*/}}
{{- define "common.volumeMounts" -}}
{{- $ctx := .ctx -}}
{{- $mounts := concat (.volumeMounts | default list) ($ctx.Values.extraVolumeMounts | default list) -}}
{{- $paths := list -}}
{{- range $mounts -}}
{{- $paths = append $paths .mountPath -}}
{{- end -}}
{{- if and (include "common.readOnlyRootFilesystem" $ctx) (not (has "/tmp" $paths)) -}}
{{- $mounts = prepend $mounts (dict "name" "tmp" "mountPath" "/tmp") -}}
{{- end -}}
{{- with $mounts -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $ctx) -}}
{{- end -}}
{{- end -}}

{{/*
Pod volumes.

Mirrors `common.volumeMounts`: provisions the `tmp` emptyDir that backs the /tmp mount when
the root filesystem is read-only, then appends caller-provided volumes and
`.Values.extraVolumes`.

Arguments:
  ctx      (required) root context
  volumes  list of chart-specific volumes (optional)
*/}}
{{- define "common.volumes" -}}
{{- $ctx := .ctx -}}
{{- $volumes := concat (.volumes | default list) ($ctx.Values.extraVolumes | default list) -}}
{{- $names := list -}}
{{- range $volumes -}}
{{- $names = append $names .name -}}
{{- end -}}
{{- if and (include "common.readOnlyRootFilesystem" $ctx) (not (has "tmp" $names)) -}}
{{- $volumes = prepend $volumes (dict "name" "tmp" "emptyDir" (dict)) -}}
{{- end -}}
{{- with $volumes -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $ctx) -}}
{{- end -}}
{{- end -}}
