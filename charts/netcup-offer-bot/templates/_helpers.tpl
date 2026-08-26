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
listener no PodMonitor scrapes and no Service exposes. `telemetry.sentry` follows the same rule
for a stronger reason — every key under it is inert while the switch is off, so writing the
block unconditionally would put twelve settings that do nothing into the document an operator
reads to find out what the bot is doing.

Optional keys inside the block are omitted rather than written empty: an empty
`telemetry.sentry.environment` is a *supplied* value to the loader, and "reported with a blank
environment tag" is not what an operator who left it unset meant. The DSN never appears here at
all — it is a credential, and it goes to the Secret under `telemetry__sentry__dsn`.
*/}}
{{- define "netcup-offer-bot.derivedConfig" -}}
feed:
  check_interval_secs: {{ .Values.feed.checkIntervalSecs }}
telemetry:
  log_level: {{ .Values.telemetry.logLevel | quote }}
  {{- if .Values.telemetry.sentry.enabled }}
  sentry:
    enabled: true
    {{- with .Values.telemetry.sentry.environment }}
    environment: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.release }}
    release: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.serverName }}
    server_name: {{ . | quote }}
    {{- end }}
    sample_rate: {{ .Values.telemetry.sentry.sampleRate }}
    traces_sample_rate: {{ .Values.telemetry.sentry.tracesSampleRate }}
    capture_level: {{ .Values.telemetry.sentry.captureLevel | quote }}
    breadcrumb_level: {{ .Values.telemetry.sentry.breadcrumbLevel | quote }}
    max_breadcrumbs: {{ .Values.telemetry.sentry.maxBreadcrumbs }}
    attach_stacktraces: {{ .Values.telemetry.sentry.attachStacktraces }}
    shutdown_timeout_secs: {{ .Values.telemetry.sentry.shutdownTimeoutSecs }}
    debug: {{ .Values.telemetry.sentry.debug }}
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
The credentials this chart manages, keyed by the file name the loader reads them from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.

The Sentry DSN appears only while the switch is on. Listed unconditionally it would survive into
`netcup-offer-bot.secretKeys` under an `existingSecret` — which cannot be read from here, so
every key the chart knows about is projected — and the pod would mount a file for a client it
never installs.
*/}}
{{- define "netcup-offer-bot.secretData" -}}
discord__webhook_url: {{ .Values.discord.webhookUrl | quote }}
{{- if .Values.telemetry.sentry.enabled }}
telemetry__sentry__dsn: {{ .Values.telemetry.sentry.dsn | quote }}
{{- end }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "netcup-offer-bot.secretKeys" -}}
{{- $data := include "netcup-offer-bot.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}

{{/*
Refuse a render that could only produce a bot with nowhere to post, and the two Sentry
combinations that are certainly not what was meant.

The webhook is checked against the projected key list rather than against
`.Values.discord.webhookUrl`, so an `existingSecret` counts — the chart cannot see inside one,
and a webhook it cannot see is not the same as a webhook that is missing. The DSN is checked the
same way and for the same reason: upstream refuses to boot when the switch is on and no DSN
resolves, so the case this chart can see offline is caught before the rollout rather than after
it.

The converse is the ordinary dead-value check: a DSN set while the switch is off is neither
projected into the pod nor written into the Secret, so it would sit in the release meaning
nothing — and a DSN is a credential to leave lying around.
*/}}
{{- define "netcup-offer-bot.validateValues" -}}
{{- $messages := list -}}
{{- if not (has "discord__webhook_url" (include "netcup-offer-bot.secretKeys" . | fromYamlArray)) -}}
{{- $messages = append $messages "  - discord.webhookUrl is required unless existingSecret supplies `discord__webhook_url`" -}}
{{- end -}}
{{- $sentry := .Values.telemetry.sentry -}}
{{- if and $sentry.enabled (not $sentry.dsn) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - telemetry.sentry.enabled is set but no DSN is available. The bot refuses to boot rather than installing a client that reports nowhere, so this is a CrashLoopBackOff, not a degraded feature. Set `telemetry.sentry.dsn`, or put `telemetry__sentry__dsn` in the Secret named by `existingSecret`." -}}
{{- end -}}
{{- if and (not $sentry.enabled) $sentry.dsn -}}
{{- $messages = append $messages "  - telemetry.sentry.dsn is set while telemetry.sentry.enabled is false. No client is installed, so the DSN is neither projected into the pod nor written into the Secret and would sit in the release meaning nothing. Set `telemetry.sentry.enabled=true`, or clear the DSN." -}}
{{- end -}}
{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}
