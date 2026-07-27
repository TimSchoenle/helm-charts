{{/*
The parts of a pod spec that are identical across every chart in this repository.

Deliberately excludes `containers` and `volumes` so charts stay in control of what they
actually run — see `common.container` and `common.volumes` for those.

Usage:
  spec:
    template:
      spec:
        {{- include "common.podSpec.common" . | nindent 8 }}
        containers:
          {{- include "common.container" (dict "ctx" $) | nindent 10 }}
*/}}
{{- define "common.podSpec.common" -}}
serviceAccountName: {{ include "common.serviceAccountName" . }}
{{- /*
  Set on the pod, not only on the ServiceAccount: `automountServiceAccountToken` on a
  ServiceAccount is ignored the moment a pod names a different one, and the pod-level
  setting is what actually keeps the API token out of the container filesystem.
*/}}
automountServiceAccountToken: {{ .Values.automountServiceAccountToken | default false }}
{{- with (include "common.podSecurityContext" .) }}
securityContext:
  {{- . | nindent 2 }}
{{- end }}
{{- include "common.imagePullSecrets" . }}
{{- with .Values.priorityClassName }}
priorityClassName: {{ . }}
{{- end }}
{{- with .Values.terminationGracePeriodSeconds }}
terminationGracePeriodSeconds: {{ . }}
{{- end }}
{{- with .Values.hostAliases }}
hostAliases:
  {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 2 }}
{{- end }}
{{- with .Values.dnsPolicy }}
dnsPolicy: {{ . }}
{{- end }}
{{- with .Values.dnsConfig }}
dnsConfig:
  {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 2 }}
{{- end }}
{{- with (include "common.affinity" .) }}
affinity:
  {{- . | nindent 2 }}
{{- end }}
{{- with .Values.topologySpreadConstraints }}
topologySpreadConstraints:
  {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Affinity rules.

An explicit `.Values.affinity` wins. Otherwise `.Values.podAntiAffinity` ("soft" or "hard")
expands to the usual spread-across-nodes rule.
*/}}
{{- define "common.affinity" -}}
{{- if .Values.affinity -}}
{{- include "common.tplvalues.render" (dict "value" .Values.affinity "context" $) -}}
{{- else if .Values.podAntiAffinity -}}
{{- if not (has .Values.podAntiAffinity (list "soft" "hard")) -}}
{{- fail (printf "podAntiAffinity must be one of: soft, hard (got %q)" .Values.podAntiAffinity) -}}
{{- end -}}
podAntiAffinity:
  {{- if eq .Values.podAntiAffinity "hard" }}
  requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchLabels:
          {{- include "common.selectorLabels" . | nindent 10 }}
      topologyKey: kubernetes.io/hostname
  {{- else }}
  preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        labelSelector:
          matchLabels:
            {{- include "common.selectorLabels" . | nindent 12 }}
        topologyKey: kubernetes.io/hostname
  {{- end }}
{{- end -}}
{{- end -}}

{{/*
Deployment update strategy.

Falls back to `Recreate` whenever the workload attaches a ReadWriteOnce volume: a rolling
update would otherwise wedge, because the replacement pod cannot attach a volume the
outgoing pod still holds.

Arguments:
  ctx  (required) root context
  rwo  true when the pod mounts a ReadWriteOnce PVC (optional)
*/}}
{{- define "common.updateStrategy" -}}
{{- $ctx := .ctx -}}
{{- if $ctx.Values.strategy -}}
{{- include "common.tplvalues.render" (dict "value" $ctx.Values.strategy "context" $ctx) -}}
{{- else if .rwo -}}
type: Recreate
{{- end -}}
{{- end -}}
