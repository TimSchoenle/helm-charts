{{/*
Pod annotations, including config checksums that force a rollout when mounted
configuration changes.

Replaces the previous `common.podAnnotations`, which hardcoded
`include (print $.Template.BasePath "/configmap.yaml")` and therefore failed to render in
any chart that has no configmap.yaml. Template paths are now passed in explicitly, so a
path that does not resolve is a template error rather than silent breakage.

Arguments:
  ctx        (required) root context
  templates  list of template paths relative to the chart's templates/ directory
             (e.g. list "configmap.yaml" "secret.yaml")

Usage:
  {{- with (include "common.podAnnotations" (dict "ctx" $ "templates" (list "configmap.yaml"))) }}
  annotations:
    {{- . | nindent 8 }}
  {{- end }}
*/}}
{{- define "common.podAnnotations" -}}
{{- $ctx := .ctx -}}
{{- $annotations := dict -}}
{{- range .templates | default list -}}
{{- $key := printf "checksum/%s" (. | trimSuffix ".yaml" | replace "." "-") -}}
{{- $_ := set $annotations $key (include (print $ctx.Template.BasePath "/" .) $ctx | sha256sum) -}}
{{- end -}}
{{- $annotations = mustMergeOverwrite $annotations (deepCopy ($ctx.Values.podAnnotations | default dict)) -}}
{{- with $annotations -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $ctx) -}}
{{- end -}}
{{- end -}}
