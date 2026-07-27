{{/*
Fail the render with every missing required value at once, rather than one `required`
error per `helm template` run.

Arguments:
  ctx       (required) root context
  required  list of dotted value paths that must resolve to a non-empty value

Usage (templates/_validation.tpl or the first line of a resource template):
  {{- include "common.validateValues" (dict "ctx" $ "required" (list "application.server.host" "image.repository")) }}
*/}}
{{- define "common.validateValues" -}}
{{- $ctx := .ctx -}}
{{- $messages := list -}}
{{- range .required | default list -}}
{{- $path := . -}}
{{- $value := $ctx.Values -}}
{{- range splitList "." $path -}}
{{- if kindIs "map" $value -}}
{{- $value = get $value . -}}
{{- else -}}
{{- $value = "" -}}
{{- end -}}
{{- end -}}
{{- if not $value -}}
{{- $messages = append $messages (printf "  - %s is required but was not set" $path) -}}
{{- end -}}
{{- end -}}
{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" $ctx.Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}
