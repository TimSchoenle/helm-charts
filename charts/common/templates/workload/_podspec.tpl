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
          {{- include "common.affinity.selectorLabels" . | nindent 10 }}
      topologyKey: kubernetes.io/hostname
      {{- with (include "common.affinity.matchLabelKeys" .) }}
      {{- . | nindent 6 }}
      {{- end }}
  {{- else }}
  preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        labelSelector:
          matchLabels:
            {{- include "common.affinity.selectorLabels" . | nindent 12 }}
        topologyKey: kubernetes.io/hostname
        {{- with (include "common.affinity.matchLabelKeys" .) }}
        {{- . | nindent 8 }}
        {{- end }}
  {{- end }}
{{- end -}}
{{- end -}}

{{/*
The pods the `podAntiAffinity` shorthand keeps apart.

`common.selectorLabels` is name-and-instance only, which is the right scope for a chart that runs
one workload and the wrong one for a chart that runs several under one name and tells them apart
by `app.kubernetes.io/component`. There, `hard` on any one of them means "never share a node with
*any* pod of this release": a second replica stays Pending for want of a node free of its own
datastores, and a release whose bundled components inherit the setting needs one node per pod.
`.Values.podAntiAffinityLabels` narrows the term to the labels that actually identify the
workload. It is set by the chart that builds the render context, not by an operator, and it has
to name labels the pod template carries or the term matches nothing at all.

A key repeating a selector label would be a duplicate YAML key that silently wins, so it is refused.
*/}}
{{- define "common.affinity.selectorLabels" -}}
{{- include "common.selectorLabels" . }}
{{- $extra := .Values.podAntiAffinityLabels | default dict }}
{{- range $key := keys $extra | sortAlpha }}
{{- if has $key (list "app.kubernetes.io/name" "app.kubernetes.io/instance") }}
{{- fail (printf "podAntiAffinityLabels may not set the selector label %q" $key) }}
{{- end }}
{{ $key }}: {{ index $extra $key | toString | quote }}
{{- end }}
{{- end -}}

{{/*
Scope the anti-affinity term to the incoming pod's own revision.

Without it a rolling update fights itself: the old ReplicaSet's pods match the term, so under
`hard` the surge pod has no node to go to once every node already holds an old replica, and the
rollout never completes; under `soft` the new pods are merely pushed off the nodes they are about
to free. `pod-template-hash` is stamped on every pod by the Deployment controller, so matching on
its value confines the term to pods of the same revision. A StatefulSet or bare pod carries no such
label, and a key missing from the incoming pod is ignored, so the term is unchanged for them.

`matchLabelKeys` is GA in Kubernetes 1.33. Older API servers that do not know the field, or have
its feature gate off, drop it on admission, and the term reverts to the pre-1.33 behaviour without
any warning. The field is therefore emitted only where it is guaranteed to take effect, and a
cluster below 1.33 needs a strategy with `maxSurge: 0` to roll a `hard`-spread workload whose
replica count equals its node count.
*/}}
{{- define "common.affinity.matchLabelKeys" -}}
{{- if semverCompare ">=1.33-0" (include "common.capabilities.kubeVersion" .) -}}
matchLabelKeys:
  - pod-template-hash
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
