{{/*
The volume backing /app/data, where the bot records which offers it has already announced.

Resolves to an existing claim, the claim this chart creates, or — when persistence is
disabled — an emptyDir, in which case every restart re-announces all current offers.
*/}}
{{- define "netcup-offer-bot.dataVolume" -}}
name: data
{{- if not .Values.persistence.data.enabled }}
emptyDir: {}
{{- else }}
persistentVolumeClaim:
  claimName: {{ .Values.persistence.data.existingClaim | default (include "common.fullname" .) }}
{{- end }}
{{- end -}}

{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the bot
reads.

`metrics` is emitted only when the exporter is on: written unconditionally it would bind a
listener no PodMonitor scrapes and no Service exposes. Optional keys are omitted rather than
written empty — an empty `telemetry.sentry_dsn` is a *supplied* value to the loader, and
"Sentry configured with a blank DSN" is not what an operator who left it unset meant.
*/}}
{{- define "netcup-offer-bot.derivedConfig" -}}
feed:
  check_interval_secs: {{ .Values.feed.checkIntervalSecs }}
telemetry:
  log_level: {{ .Values.telemetry.logLevel | quote }}
  {{- with .Values.telemetry.sentryDsn }}
  sentry_dsn: {{ . | quote }}
  {{- end }}
{{- if .Values.metrics.enabled }}
metrics:
  ip: {{ .Values.metrics.ip | quote }}
  port: {{ .Values.metrics.port }}
{{- end }}
{{- end -}}

{{/*
The configuration that actually reaches the bot: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "netcup-offer-bot.effectiveConfig" -}}
{{- $derived := include "netcup-offer-bot.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "netcup-offer-bot.configToml" -}}
{{- $config := include "netcup-offer-bot.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}

{{/*
The credential this chart manages, keyed by the file name the loader reads it from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.
*/}}
{{- define "netcup-offer-bot.secretData" -}}
discord__webhook_url: {{ .Values.discord.webhookUrl | quote }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "netcup-offer-bot.secretKeys" -}}
{{- $data := include "netcup-offer-bot.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}

{{/*
Refuse a render that could only produce a bot with nowhere to post.

Checked against the projected key list rather than against `.Values.discord.webhookUrl`, so an
`existingSecret` counts — the chart cannot see inside one, and a webhook it cannot see is not
the same as a webhook that is missing.
*/}}
{{- define "netcup-offer-bot.validateValues" -}}
{{- if not (include "netcup-offer-bot.secretKeys" .) -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n  - discord.webhookUrl is required unless existingSecret supplies `discord__webhook_url`\n" .Chart.Name) -}}
{{- end -}}
{{- end -}}
