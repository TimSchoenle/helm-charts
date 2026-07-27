{{/*
Render a single probe from a probe values block.

Unset fields are omitted rather than defaulted, so the Kubernetes defaults apply and the
rendered manifest stays free of noise. `successThreshold` is dropped for liveness and
startup probes: the API server rejects any value other than 1 there, so accepting one from
values would only produce an invalid manifest at apply time.

The probe handler may be `httpGet`, `tcpSocket`, `exec` or `grpc`. Handler-less probes
render nothing.

Usage:
  {{- with (include "common.probe" (dict "probe" .Values.livenessProbe "type" "liveness" "context" $)) }}
  livenessProbe:
    {{- . | nindent 12 }}
  {{- end }}
*/}}
{{- define "common.probe" -}}
{{- $probe := .probe | default dict -}}
{{- $type := .type | default "readiness" -}}
{{- $context := .context -}}
{{- if $probe.enabled -}}
{{- $handlers := list "httpGet" "tcpSocket" "exec" "grpc" -}}
{{- $handler := "" -}}
{{- range $handlers -}}
{{- if and (not $handler) (hasKey $probe .) (get $probe .) -}}
{{- $handler = . -}}
{{- end -}}
{{- end -}}
{{- if not $handler -}}
{{- fail (printf "%sProbe is enabled but defines no handler (one of: %s)" $type (join ", " $handlers)) -}}
{{- end -}}
{{ $handler }}:
{{- include "common.tplvalues.render" (dict "value" (get $probe $handler) "context" $context) | nindent 2 }}
{{- range $field := list "initialDelaySeconds" "periodSeconds" "timeoutSeconds" "failureThreshold" "terminationGracePeriodSeconds" }}
{{- if hasKey $probe $field }}
{{ $field }}: {{ get $probe $field }}
{{- end }}
{{- end }}
{{- if and (hasKey $probe "successThreshold") (eq $type "readiness") }}
successThreshold: {{ $probe.successThreshold }}
{{- end }}
{{- end -}}
{{- end -}}

{{/*
Render all three probes for a container, from `.Values.startupProbe`, `.Values.livenessProbe`
and `.Values.readinessProbe`.

Usage:
  {{- include "common.probes" . | nindent 10 }}
*/}}
{{- define "common.probes" -}}
{{- $rendered := list -}}
{{- range $type := list "startup" "liveness" "readiness" -}}
{{- $probe := index $.Values (printf "%sProbe" $type) | default dict -}}
{{- $body := include "common.probe" (dict "probe" $probe "type" $type "context" $) -}}
{{- if $body -}}
{{- $rendered = append $rendered (printf "%sProbe:\n%s" $type (indent 2 $body)) -}}
{{- end -}}
{{- end -}}
{{- join "\n" $rendered -}}
{{- end -}}
