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

The prepended mount names the volume `tmp`, and `common.volumes` provisions that volume only
when no caller volume already has the name. The two partials are called separately and neither
sees the other's list, so the name is what ties them together, and a caller volume named `tmp`
would be the one mounted at /tmp. That is sound when the caller means it as the /tmp backing,
which is why a caller volume of that name is not refused outright. A caller mount of `tmp`
somewhere else shows it is not meant that way: mounting it at /tmp as well would share one
volume between two paths without anybody asking for it. That combination is refused.

Arguments:
  ctx           (required) root context
  volumeMounts  list of chart-specific mounts (optional)
*/}}
{{- define "common.volumeMounts" -}}
{{- $ctx := .ctx -}}
{{- $mounts := concat (.volumeMounts | default list) ($ctx.Values.extraVolumeMounts | default list) -}}
{{- $paths := list -}}
{{- $elsewhere := list -}}
{{- range $mounts -}}
{{- $paths = append $paths .mountPath -}}
{{- if and (eq (toString .name) "tmp") (ne (toString .mountPath) "/tmp") -}}
{{- $elsewhere = append $elsewhere (toString .mountPath) -}}
{{- end -}}
{{- end -}}
{{- if and (include "common.readOnlyRootFilesystem" $ctx) (not (has "/tmp" $paths)) -}}
{{- with $elsewhere -}}
{{- fail (printf "\n\nVOLUME CONFIGURATION INVALID for chart %q:\n\n  - the volume `tmp` is mounted at %s, but nothing is mounted at /tmp. The read-only root filesystem needs a writable /tmp, which this chart provides as a volume named `tmp`; with a volume of that name already supplied, it would mount that one at /tmp as well. Rename the volume, or mount a volume at /tmp yourself.\n" $ctx.Chart.Name (join ", " .)) -}}
{{- end -}}
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
