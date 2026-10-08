{{/*
Render a value that may itself contain Go template syntax.

Lets consumers put templates inside values, e.g.:

  ingress:
    hosts:
      - host: "{{ .Release.Name }}.example.com"

Usage:
  {{ include "common.tplvalues.render" (dict "value" .Values.ingress.hosts "context" $) }}
*/}}
{{- define "common.tplvalues.render" -}}
{{- $value := .value -}}
{{- $context := .context -}}
{{- if typeIs "string" $value -}}
{{- tpl $value $context -}}
{{- else -}}
{{- tpl ($value | toYaml) $context -}}
{{- end -}}
{{- end -}}

{{/*
Merge a list of dicts, later entries winning, without mutating the inputs.

Usage:
  {{ include "common.tplvalues.merge" (dict "values" (list .Values.a .Values.b) "context" $) }}
*/}}
{{- define "common.tplvalues.merge" -}}
{{- $dst := dict -}}
{{- range .values -}}
{{- $dst = mustMergeOverwrite $dst (deepCopy (default dict .)) -}}
{{- end -}}
{{- /* Return nothing rather than "{}" when everything merged away, so callers can
       guard the whole block with `with` and not emit an empty `annotations: {}`. */ -}}
{{- with $dst -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $.context) -}}
{{- end -}}
{{- end -}}
