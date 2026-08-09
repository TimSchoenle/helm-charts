{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
service reads.

Optional keys are omitted rather than written empty: an empty `telemetry.sentry_dsn` is a
*supplied* value to the loader, and "Sentry configured with a blank DSN" is not what an
operator who left it unset meant.
*/}}
{{- define "cloudflare-access-webhook-redirect.derivedConfig" -}}
server:
  host: {{ .Values.server.host | quote }}
  port: {{ .Values.server.port }}
telemetry:
  log_level: {{ .Values.telemetry.logLevel | quote }}
  {{- with .Values.telemetry.sentryDsn }}
  sentry_dsn: {{ . | quote }}
  {{- end }}
{{- if or .Values.webhook.targetBase .Values.webhook.paths }}
webhook:
  {{- with .Values.webhook.targetBase }}
  target_base: {{ . | quote }}
  {{- end }}
  {{- with .Values.webhook.paths }}
  paths:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
The configuration that actually reaches the service: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "cloudflare-access-webhook-redirect.effectiveConfig" -}}
{{- $derived := include "cloudflare-access-webhook-redirect.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "cloudflare-access-webhook-redirect.configToml" -}}
{{- $config := include "cloudflare-access-webhook-redirect.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}

{{/*
Refuse a render that could only produce a proxy which forwards nothing.

Checked against the *effective* tree rather than against `.Values.webhook`, so supplying either
key through `config` is as valid as supplying it through the first-class value. `configExtraToml`
is appended verbatim and never parsed, so a chart that has one steps out of the way rather than
rejecting a configuration it cannot see.
*/}}
{{- define "cloudflare-access-webhook-redirect.validateValues" -}}
{{- if not .Values.configExtraToml -}}
{{- $config := include "cloudflare-access-webhook-redirect.effectiveConfig" . | fromYaml -}}
{{- $messages := list -}}
{{- if not ($config | dig "webhook" "target_base" "") -}}
{{- $messages = append $messages "  - webhook.targetBase is required but was not set" -}}
{{- end -}}
{{- if not ($config | dig "webhook" "paths" dict) -}}
{{- $messages = append $messages "  - webhook.paths is required but was not set: a proxy with no allowed path forwards nothing" -}}
{{- end -}}
{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The credentials this chart manages, keyed by the file name the loader reads them from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.
*/}}
{{- define "cloudflare-access-webhook-redirect.secretData" -}}
cloudflare__client_id: {{ .Values.cloudflare.clientId | quote }}
cloudflare__client_secret: {{ .Values.cloudflare.clientSecret | quote }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "cloudflare-access-webhook-redirect.secretKeys" -}}
{{- $data := include "cloudflare-access-webhook-redirect.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}

{{/*
The pod template annotations.

Deliberately without the `checksum/*` annotations the other charts in this repository use by
default: the proxy watches its configuration and secrets directories and rebuilds itself when
the kubelet refreshes either mount, so rolling the Deployment on a configuration change would
throw that property away. `configMount.rolloutOnChange` restores the conventional behaviour.
*/}}
{{- define "cloudflare-access-webhook-redirect.podAnnotations" -}}
{{- $templates := ternary (list "configmap.yaml" "secret.yaml") (list) .Values.configMount.rolloutOnChange -}}
{{- include "common.podAnnotations" (dict "ctx" . "templates" $templates) -}}
{{- end -}}
