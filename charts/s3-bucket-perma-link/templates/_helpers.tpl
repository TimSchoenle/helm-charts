{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
service reads.

Optional keys are omitted rather than written empty: an empty
`telemetry.sentry.environment` is a *supplied* value to the loader, and "reported with a blank
environment tag" is not what an operator who left it unset meant.

`telemetry.sentry` is written only while its switch is on, for the same reason: every key under
it is inert otherwise, so writing the block unconditionally would put fifteen settings that do
nothing into the document an operator reads to find out what the server is doing. The DSN never
appears here at all — it is a credential, and it goes to the Secret under
`telemetry__sentry__dsn` alongside the S3 keys.
*/}}
{{- define "s3-bucket-perma-link.derivedConfig" -}}
server:
  host: {{ .Values.server.host | quote }}
  port: {{ .Values.server.port }}
s3:
  host: {{ .Values.s3.host | quote }}
  region: {{ .Values.s3.region | quote }}
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
    attach_stacktrace: {{ .Values.telemetry.sentry.attachStacktrace }}
    send_default_pii: {{ .Values.telemetry.sentry.sendDefaultPii }}
    http_transactions: {{ .Values.telemetry.sentry.httpTransactions }}
    span_attributes: {{ .Values.telemetry.sentry.spanAttributes }}
    shutdown_timeout_secs: {{ .Values.telemetry.sentry.shutdownTimeoutSecs }}
    debug: {{ .Values.telemetry.sentry.debug }}
  {{- end }}
{{- with .Values.bucket.entries }}
bucket:
  entries:
    {{- toYaml . | nindent 4 }}
{{- end }}
{{- end -}}

{{/*
The configuration that actually reaches the service: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "s3-bucket-perma-link.effectiveConfig" -}}
{{- $derived := include "s3-bucket-perma-link.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "s3-bucket-perma-link.configToml" -}}
{{- $config := include "s3-bucket-perma-link.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}

{{/*
Refuse a render that could only produce a server with nothing to serve, and one that would
resolve an entry to a bucket or an object the operator never named.

Checked against the *effective* tree rather than against `.Values.bucket`, so supplying entries
through `config` is as valid as supplying them through the first-class value. `configExtraToml`
is appended verbatim and never parsed, so a chart that has one steps out of the way rather than
rejecting a configuration it cannot see.
*/}}
{{- define "s3-bucket-perma-link.validateValues" -}}
{{- $messages := list -}}
{{- /*
Sentry, checked outside the `configExtraToml` guard below.

Both halves of "a reporter that reports nowhere is worse than none". Upstream refuses to boot
with the switch on and no DSN, so this is a CrashLoopBackOff rather than a degraded feature, and
a rejected `helm upgrade` beats one. The DSN cannot arrive through `configExtraToml` either — it
is a credential and travels the Secret — so unlike the bucket entries below there is nothing
here the escape hatch could be supplying, and stepping out of the way would only hide the fault.
An `existingSecret` is taken as the answer, because the chart cannot see inside one.

The converse is the ordinary dead-value check: a DSN set while the switch is off is neither
written into the Secret nor read by anything, so it would sit in the release meaning nothing —
and a DSN is a credential to leave lying around.
*/ -}}
{{- $sentry := .Values.telemetry.sentry -}}
{{- if and $sentry.enabled (not $sentry.dsn) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - telemetry.sentry.enabled is set but no DSN is available. The server refuses to boot rather than installing a client that reports nowhere, so this is a CrashLoopBackOff, not a degraded feature. Set `telemetry.sentry.dsn`, or put `telemetry__sentry__dsn` in the Secret named by `existingSecret`." -}}
{{- end -}}
{{- if and (not $sentry.enabled) $sentry.dsn -}}
{{- $messages = append $messages "  - telemetry.sentry.dsn is set while telemetry.sentry.enabled is false. No client is installed, so the DSN is neither projected into the pod nor written into the Secret and would sit in the release meaning nothing. Set `telemetry.sentry.enabled=true`, or clear the DSN." -}}
{{- end -}}
{{- if not .Values.configExtraToml -}}
{{- $config := include "s3-bucket-perma-link.effectiveConfig" . | fromYaml -}}
{{- $entries := $config | dig "bucket" "entries" dict -}}
{{- if not $entries -}}
{{- $messages = append $messages "  - bucket.entries is required but was not set: a server with no entry serves nothing" -}}
{{- end -}}
{{- range $path, $entry := $entries -}}
{{- if not (kindIs "map" $entry) -}}
{{- $messages = append $messages (printf "  - bucket.entries[%q] must be a mapping of `bucket` and `object`" $path) -}}
{{- else -}}
{{- if not (get $entry "bucket") -}}
{{- $messages = append $messages (printf "  - bucket.entries[%q].bucket is required but was not set" $path) -}}
{{- end -}}
{{- if not (get $entry "object") -}}
{{- $messages = append $messages (printf "  - bucket.entries[%q].object is required but was not set" $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}

{{/*
The credentials this chart manages, keyed by the file name the loader reads them from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.

The Sentry DSN appears only while the switch is on. Listed unconditionally it would survive into
`s3-bucket-perma-link.secretKeys` under an `existingSecret` — which cannot be read from here, so
every key the chart knows about is projected — and the pod would mount a file for a client it
never installs.
*/}}
{{- define "s3-bucket-perma-link.secretData" -}}
s3__access_key: {{ .Values.s3.accessKey | quote }}
s3__secret_key: {{ .Values.s3.secretKey | quote }}
{{- if .Values.telemetry.sentry.enabled }}
telemetry__sentry__dsn: {{ .Values.telemetry.sentry.dsn | quote }}
{{- end }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "s3-bucket-perma-link.secretKeys" -}}
{{- $data := include "s3-bucket-perma-link.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}

{{/*
The pod template annotations.

Deliberately without the `checksum/*` annotations the other charts in this repository use by
default: the service watches its configuration and secrets directories and rebuilds its bucket
clients and listener when the kubelet refreshes either mount, so rolling the Deployment on a
configuration change would throw that property away. `configMount.rolloutOnChange` restores the
conventional behaviour.
*/}}
{{- define "s3-bucket-perma-link.podAnnotations" -}}
{{- $templates := ternary (list "configmap.yaml" "secret.yaml") (list) .Values.configMount.rolloutOnChange -}}
{{- include "common.podAnnotations" (dict "ctx" . "templates" $templates) -}}
{{- end -}}
