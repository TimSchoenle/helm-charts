{{/*
NetworkPolicy helpers.

The central rule these enforce: **every egress rule carries a `to:` selector**. A rule that
lists only `ports:` is not a restriction — the Kubernetes NetworkPolicy API treats a missing
`to` as "all destinations", so a policy that looks like "the pod may talk HTTPS" actually
reads "the pod may talk to anything, anywhere, on 443", including in-cluster services and
the cloud metadata endpoint at 169.254.169.254.
*/}}

{{/*
Every policy object for a chart, in whichever dialect `networkPolicy.engine` selects.

`networkPolicy.enabled` decides whether the policies exist at all; the nested
`ingress.enabled` / `egress.enabled` flags decide whether they carry rules. Creating a
policy with its rule list empty is the default-deny case and is supported on purpose.

`engine` picks the dialect, not the rules — both engines are driven by the same value tree, so
switching is a one-line change and not a re-authoring:

  kubernetes  the portable `networking.k8s.io/v1` pair. The default.
  cilium      `CiliumNetworkPolicy`, which can express what the portable API cannot: FQDN
              destinations, named entities, L7, and default-deny stated rather than implied.
  both        emit both, for the window in which a cluster is migrating between CNIs.

`both` is safe because NetworkPolicies are additive — two policies selecting one pod union
their allowances, they do not intersect. Cilium honours vanilla NetworkPolicy objects too, so
on a Cilium cluster `both` is strictly more permissive than `cilium` alone; it is a migration
setting, not a hardening one.

Usage (templates/networkpolicy.yaml):
  {{ include "common.networkPolicy" . }}
*/}}
{{- define "common.networkPolicy" -}}
{{- if .Values.networkPolicy.enabled -}}
{{- $engine := .Values.networkPolicy.engine | default "kubernetes" -}}
{{- if has $engine (list "kubernetes" "both") -}}
{{- include "common.networkPolicy.ingress" . }}
---
{{ include "common.networkPolicy.egress" . }}
{{- end -}}
{{- if has $engine (list "cilium" "both") -}}
{{- /* Only `both` needs a separator here; on its own the Cilium pair is the first document. */ -}}
{{- if eq $engine "both" }}
---
{{ end }}
{{- include "common.ciliumNetworkPolicy" . }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The peer that fronts this chart from outside the cluster, as a NetworkPolicy `from` selector.

Two mechanisms can be in front of a workload and they are found in completely different ways.
An Ingress controller is a deployment an operator names (`networkPolicy.ingress.controller`);
a Gateway API data plane is provisioned by the implementation in response to a Gateway object,
and is labelled `gateway.networking.k8s.io/gateway-name: <gateway>` — a convention Cilium,
Envoy Gateway, Istio and NGINX Gateway Fabric all follow.

That label is why the gateway peer needs no configuration at all in the common case: the
Gateway the policy must admit is by definition the one the chart's own route attaches to, so
both the namespace and the selector are derived from `gateway.parentRefs`. Restating the
Gateway's identity under `networkPolicy` would be a second place to edit on a rename, and the
failure mode of getting it wrong is a policy that looks correct and blocks all inbound traffic.

Arguments:
  ctx     (required) root context
  values  the `networkPolicy.ingress.gateway` value tree
*/}}
{{- define "common.networkPolicy.gatewayPeer" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $gateway := $ctx.Values.gateway | default dict -}}
{{- $namespace := $values.namespace | default (include "common.gateway.namespace" (dict "ctx" $ctx "values" $gateway)) -}}
{{- $selector := $values.selector | default dict -}}
{{- if not $selector -}}
{{- with (include "common.gateway.name" (dict "ctx" $ctx "values" $gateway)) -}}
{{- $selector = dict "gateway.networking.k8s.io/gateway-name" . -}}
{{- end -}}
{{- end -}}
- namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: {{ $namespace }}
  {{- with $selector }}
  podSelector:
    matchLabels:
      {{- toYaml . | nindent 6 }}
  {{- end }}
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
    {{- $gateway := $ingress.gateway | default dict }}
    {{- if and $gateway.enabled (.Values.gateway | default dict).enabled }}
    - from:
        {{- include "common.networkPolicy.gatewayPeer" (dict "ctx" . "values" $gateway) | nindent 8 }}
      {{- with $gateway.ports }}
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
