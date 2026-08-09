{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
service reads.

Optional keys are omitted rather than written empty: an empty `telemetry.sentry_dsn` is a
*supplied* value to the loader, and "Sentry configured with a blank DSN" is not what an
operator who left it unset meant.
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
  {{- with .Values.telemetry.sentryDsn }}
  sentry_dsn: {{ . | quote }}
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
{{- if not .Values.configExtraToml -}}
{{- $config := include "s3-bucket-perma-link.effectiveConfig" . | fromYaml -}}
{{- $entries := $config | dig "bucket" "entries" dict -}}
{{- $messages := list -}}
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
{{- define "s3-bucket-perma-link.secretData" -}}
s3__access_key: {{ .Values.s3.accessKey | quote }}
s3__secret_key: {{ .Values.s3.secretKey | quote }}
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
