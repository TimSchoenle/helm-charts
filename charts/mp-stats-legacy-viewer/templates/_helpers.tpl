{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
server reads.

`dist_dir` and `data_dir` are restated rather than inherited. The image ships a `/config.toml`
and points `MP_STATS_CONFIG` at it; pointing that variable at this chart's mount **replaces**
that file rather than layering over it, so every key it carried has to be carried here or the
server falls back to the workspace-relative defaults and refuses to start on a missing
`index.html`.

`telemetry.sentry` is written only while its switch is on: every key under it is inert
otherwise, so writing the block unconditionally would put fifteen settings that do nothing into
the document an operator reads to find out what the server is doing. Optional keys inside it are
omitted rather than written empty — an empty `telemetry.sentry.environment` is a *supplied*
value to the loader, and "reported with a blank environment tag" is not what an operator who
left it unset meant. The DSN never appears here at all: it is a credential and goes to the
Secret under `telemetry__sentry__dsn`.
*/}}
{{- define "mp-stats-legacy-viewer.derivedConfig" -}}
server:
  bind_addr: {{ printf "%s:%v" .Values.server.host (.Values.server.port | toString) | quote }}
  dist_dir: {{ .Values.server.distDir | quote }}
  data_dir: {{ .Values.server.dataDir | quote }}
  csp:
    enabled: {{ .Values.server.csp.enabled }}
    cloudflare:
      script_nonce: {{ .Values.server.csp.cloudflare.scriptNonce }}
      turnstile: {{ .Values.server.csp.cloudflare.turnstile }}
      web_analytics: {{ .Values.server.csp.cloudflare.webAnalytics }}
telemetry:
  log_filter: {{ .Values.telemetry.logFilter | quote }}
  json_logs: {{ .Values.telemetry.jsonLogs }}
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
    send_default_pii: {{ .Values.telemetry.sentry.sendDefaultPii }}
    http_transactions: {{ .Values.telemetry.sentry.httpTransactions }}
    span_attributes: {{ .Values.telemetry.sentry.spanAttributes }}
    shutdown_timeout_secs: {{ .Values.telemetry.sentry.shutdownTimeoutSecs }}
    debug: {{ .Values.telemetry.sentry.debug }}
  {{- end }}
{{- end -}}

{{/*
The configuration that actually reaches the server: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "mp-stats-legacy-viewer.effectiveConfig" -}}
{{- $derived := include "mp-stats-legacy-viewer.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "mp-stats-legacy-viewer.configToml" -}}
{{- $config := include "mp-stats-legacy-viewer.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}

{{/*
The credential this chart manages, keyed by the file name the loader reads it from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.

The Sentry DSN is the only one, and it appears only while the switch is on. Listed
unconditionally it would survive into `mp-stats-legacy-viewer.secretKeys` under an
`existingSecret` — which cannot be read from here, so every key the chart knows about is
projected — and a pod that reports to nothing would still carry a secrets volume and an
`MP_STATS_SECRETS_DIR` pointing into it.
*/}}
{{- define "mp-stats-legacy-viewer.secretData" -}}
{{- if .Values.telemetry.sentry.enabled }}
telemetry__sentry__dsn: {{ .Values.telemetry.sentry.dsn | quote }}
{{- end }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "mp-stats-legacy-viewer.secretKeys" -}}
{{- $data := include "mp-stats-legacy-viewer.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}

{{/*
Refuse the two Sentry combinations that are certainly not what was meant.

Upstream refuses to boot with the switch on and no DSN, so that is a CrashLoopBackOff rather
than a degraded feature, and a rejected `helm upgrade` beats one. An `existingSecret` is taken
as the answer, because the chart cannot see inside one and a DSN it cannot see is not the same
as a DSN that is missing.

The converse is the ordinary dead-value check: a DSN set while the switch is off is neither
projected into the pod nor written into the Secret, so it would sit in the release meaning
nothing — and a DSN is a credential to leave lying around.

Checked against the values rather than against the effective tree: unlike every other setting
here the DSN cannot arrive through `config` or `configExtraToml`, because it does not travel the
configuration document at all.
*/}}
{{- define "mp-stats-legacy-viewer.validateValues" -}}
{{- $messages := list -}}
{{- $sentry := .Values.telemetry.sentry -}}
{{- if and $sentry.enabled (not $sentry.dsn) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - telemetry.sentry.enabled is set but no DSN is available. The server refuses to boot rather than installing a client that reports nowhere, so this is a CrashLoopBackOff, not a degraded feature. Set `telemetry.sentry.dsn`, or put `telemetry__sentry__dsn` in the Secret named by `existingSecret`." -}}
{{- end -}}
{{- if and (not $sentry.enabled) $sentry.dsn -}}
{{- $messages = append $messages "  - telemetry.sentry.dsn is set while telemetry.sentry.enabled is false. No client is installed, so the DSN is neither projected into the pod nor written into the Secret and would sit in the release meaning nothing. Set `telemetry.sentry.enabled=true`, or clear the DSN." -}}
{{- end -}}
{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}
