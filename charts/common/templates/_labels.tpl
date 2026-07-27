{{/*
Selector labels.

These end up in immutable selector fields (Deployment.spec.selector,
Service.spec.selector, ...) and therefore must never change for a given release.
Nothing version-dependent belongs here.
*/}}
{{- define "common.selectorLabels" -}}
app.kubernetes.io/name: {{ include "common.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Version-carrying labels shared by object metadata and pod templates.
*/}}
{{- define "common.versionLabels" -}}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- with .Values.component }}
app.kubernetes.io/component: {{ . | quote }}
{{- end }}
{{- with .Values.partOf }}
app.kubernetes.io/part-of: {{ . | quote }}
{{- end }}
{{- end }}

{{/*
Full label set for object metadata.
*/}}
{{- define "common.labels" -}}
helm.sh/chart: {{ include "common.chart" . }}
{{ include "common.selectorLabels" . }}
{{- include "common.versionLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ include "common.tplvalues.render" (dict "value" . "context" $) }}
{{- end }}
{{- end }}

{{/*
Labels for the pod template.

Deliberately excludes `helm.sh/chart`: it embeds the chart version, so including it
would rewrite every pod label — and trigger a rollout — on a chart-only version bump.
*/}}
{{- define "common.podLabels" -}}
{{ include "common.selectorLabels" . }}
{{- include "common.versionLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ include "common.tplvalues.render" (dict "value" . "context" $) }}
{{- end }}
{{- with .Values.podLabels }}
{{ include "common.tplvalues.render" (dict "value" . "context" $) }}
{{- end }}
{{- end }}

{{/*
Annotations applied to every object this chart creates.
*/}}
{{- define "common.annotations" -}}
{{- with .Values.commonAnnotations }}
{{- include "common.tplvalues.render" (dict "value" . "context" $) }}
{{- end }}
{{- end }}
