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
Labels for metadata that cannot be updated after creation — currently
`volumeClaimTemplates`, whose entries are part of the StatefulSet *spec* and are therefore
immutable down to their labels.

Excludes `helm.sh/chart` and every version label: an otherwise no-op chart bump would
render a diff the API server rejects outright with "updates to statefulset spec for fields
other than ... are forbidden", breaking in-place upgrades of every persistent datastore the
chart ships. Same reasoning as `common.podLabels`, one step further — there a version label
costs a rollout, here it costs the upgrade.

`.Values.commonLabels` is deliberately excluded as well. It is operator-settable, so folding
it in would re-open this trap the first time someone adds a label to a running release.

Anything added here must be stable for the entire lifetime of a release. If it can change,
it does not belong in an immutable field.
*/}}
{{- define "common.immutableLabels" -}}
{{ include "common.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Annotations applied to every object this chart creates.
*/}}
{{- define "common.annotations" -}}
{{- with .Values.commonAnnotations }}
{{- include "common.tplvalues.render" (dict "value" . "context" $) }}
{{- end }}
{{- end }}
