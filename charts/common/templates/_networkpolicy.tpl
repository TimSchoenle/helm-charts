{{/*
NetworkPolicy helpers.

The central rule these enforce: **every egress rule carries a `to:` selector**. A rule that
lists only `ports:` is not a restriction — the Kubernetes NetworkPolicy API treats a missing
`to` as "all destinations", so a policy that looks like "the pod may talk HTTPS" actually
reads "the pod may talk to anything, anywhere, on 443", including in-cluster services and
the cloud metadata endpoint at 169.254.169.254.
*/}}

{{/*
Both NetworkPolicies for a chart.

`networkPolicy.enabled` decides whether the policies exist at all; the nested
`ingress.enabled` / `egress.enabled` flags decide whether they carry rules. Creating a
policy with its rule list empty is the default-deny case and is supported on purpose.

Usage (templates/networkpolicy.yaml):
  {{ include "common.networkPolicy" . }}
*/}}
{{- define "common.networkPolicy" -}}
{{- if .Values.networkPolicy.enabled -}}
{{- include "common.networkPolicy.ingress" . }}
---
{{ include "common.networkPolicy.egress" . }}
{{- end -}}
{{- end -}}

{{/*
Default destination for the DNS egress rule: kube-dns / CoreDNS in kube-system.
Both selectors are overridable for clusters that label things differently.
*/}}
{{- define "common.networkPolicy.dnsPeers" -}}
{{- $dns := .Values.networkPolicy.egress.dns | default dict -}}
- namespaceSelector:
    matchLabels:
      {{- toYaml ($dns.namespaceSelector | default (dict "kubernetes.io/metadata.name" "kube-system")) | nindent 6 }}
  podSelector:
    matchLabels:
      {{- toYaml ($dns.podSelector | default (dict "k8s-app" "kube-dns")) | nindent 6 }}
{{- end -}}

{{/*
Default destination for internet egress: everything except private and link-local space.

169.254.0.0/16 is excluded because 169.254.169.254 is the cloud instance metadata endpoint —
reachable from any pod that is allowed "the internet" and the usual first stop for
credential theft after a container compromise.
*/}}
{{- define "common.networkPolicy.internetPeers" -}}
{{- $egress := .Values.networkPolicy.egress | default dict -}}
- ipBlock:
    cidr: {{ $egress.cidr | default "0.0.0.0/0" | quote }}
    except:
      {{- toYaml ($egress.except | default (list "10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16" "169.254.0.0/16")) | nindent 6 }}
{{- end -}}

{{/*
Ingress NetworkPolicy.

Rendering this with no rules enabled is meaningful and intentional: a policy that selects
the pod and declares `policyTypes: [Ingress]` with an empty rule list is a default-deny.
*/}}
{{- define "common.networkPolicy.ingress" -}}
{{- $ingress := .Values.networkPolicy.ingress | default dict -}}
apiVersion: {{ include "common.capabilities.networkPolicy.apiVersion" . }}
kind: NetworkPolicy
metadata:
  name: {{ include "common.fullname.suffixed" (dict "ctx" . "suffix" "ingress") }}
  namespace: {{ include "common.namespace" . }}
  labels:
    {{- include "common.labels" . | nindent 4 }}
  {{- with (include "common.annotations" .) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  podSelector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Ingress
  ingress:
    {{- if $ingress.enabled }}
    {{- $monitoring := $ingress.monitoring | default dict }}
    {{- if $monitoring.enabled }}
    - from:
        - namespaceSelector:
            matchLabels:
              {{- toYaml ($monitoring.namespaceSelector | default (dict "kubernetes.io/metadata.name" $monitoring.namespace)) | nindent 14 }}
      {{- with $monitoring.ports }}
      ports:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    {{- end }}
    {{- $controller := $ingress.controller | default dict }}
    {{- if $controller.enabled }}
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ $controller.namespace }}
          podSelector:
            matchLabels:
              {{- toYaml $controller.selector | nindent 14 }}
      {{- with $controller.ports }}
      ports:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    {{- end }}
    {{- with $ingress.customRules }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
    {{- end }}
    {{- with .Values.networkPolicy.extraIngress }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
{{- end -}}

{{/*
Egress NetworkPolicy. Every generated rule is scoped by a `to:` selector.
*/}}
{{- define "common.networkPolicy.egress" -}}
{{- $egress := .Values.networkPolicy.egress | default dict -}}
apiVersion: {{ include "common.capabilities.networkPolicy.apiVersion" . }}
kind: NetworkPolicy
metadata:
  name: {{ include "common.fullname.suffixed" (dict "ctx" . "suffix" "egress") }}
  namespace: {{ include "common.namespace" . }}
  labels:
    {{- include "common.labels" . | nindent 4 }}
  {{- with (include "common.annotations" .) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  podSelector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Egress
  egress:
    {{- if $egress.enabled }}
    {{- if (($egress.dns | default dict).enabled) }}
    - to:
        {{- include "common.networkPolicy.dnsPeers" . | nindent 8 }}
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    {{- end }}
    {{- if (($egress.http | default dict).enabled) }}
    - to:
        {{- include "common.networkPolicy.internetPeers" . | nindent 8 }}
      ports:
        - port: 80
          protocol: TCP
    {{- end }}
    {{- if (($egress.https | default dict).enabled) }}
    - to:
        {{- include "common.networkPolicy.internetPeers" . | nindent 8 }}
      ports:
        - port: 443
          protocol: TCP
    {{- end }}
    {{- with $egress.customRules }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
    {{- end }}
    {{- with .Values.networkPolicy.extraEgress }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
{{- end -}}
