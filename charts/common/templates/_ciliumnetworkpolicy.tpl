{{/*
CiliumNetworkPolicy: the same rules `_networkpolicy.tpl` describes, in the dialect of a CNI that
can express more of them.

Why a second dialect at all
---------------------------
The portable NetworkPolicy API can only name destinations by IP. "This scraper may reach the
internet over HTTPS" therefore has to be written `0.0.0.0/0` on port 443 with the private ranges
and 169.254.0.0/16 carved out — a rule that, read honestly, permits a compromised container to
reach every public host on earth. What the operator meant was "it may reach api.anilist.co", and
there is no way to say that, because the policy is enforced on packets that carry an address and
not a name.

Cilium enforces at the identity level and proxies DNS, so it can:

  toFQDNs     the destinations by name, which is the rule that was actually intended
  toEntities  `world`, `cluster`, `host`, `remote-node`, `kube-apiserver` — named sets that
              stay correct when the cluster is renumbered, unlike a hand-maintained CIDR list
  L7          method and path, so "may POST to /v1/webhook" is a policy and not a code review
  DNS L7      which names may even be resolved — and the prerequisite for toFQDNs working

Everything the portable engine understands is translated, so `engine: cilium` is a drop-in for an
existing configuration and the Cilium-only knobs under `networkPolicy.cilium` are purely
additive. What is *not* carried over is `networkPolicy.{extraIngress,extraEgress}` and the
per-section `customRules`: those are verbatim `networking.k8s.io/v1` rule objects and are not
valid CNP. `networkPolicy.cilium.{extraIngress,extraEgress,ingress.customRules,egress.customRules}`
are their counterparts.

Two objects rather than one, and why `enableDefaultDeny` is stated explicitly
-----------------------------------------------------------------------------
Cilium turns on default-deny for a direction when a policy selecting the endpoint carries rules
for that direction. That makes the intentional default-deny case — a policy with an empty rule
list, which is how a chart says "nothing may reach this" — a silent no-op on older behaviour: an
empty `ingress: []` was not treated as "deny everything", it was treated as no rule at all.
`enableDefaultDeny` (Cilium 1.16+) is what states it rather than implying it.

Splitting into an ingress object and an egress object mirrors the portable pair, and makes the
flag safe to set: each object turns default-deny on for its own direction and explicitly *off*
for the other. A single object with `enableDefaultDeny: {ingress: true, egress: true}` and only
ingress rules would deny all egress from a workload whose egress policy the operator never asked
for — including DNS.
*/}}

{{/*
A Kubernetes namespace label selector, as Cilium endpoint-selector labels.

Cilium does not have a separate namespace selector: a namespace is matched through labels on the
endpoint itself. The namespace *name* is exposed as `io.kubernetes.pod.namespace`, and every
other label on the Namespace object is mirrored onto its endpoints with an
`io.cilium.k8s.namespace.labels.` prefix. Getting this wrong produces a selector that matches
nothing and a policy that silently denies, so the mapping lives in one place.
*/}}
{{- define "common.cilium.namespaceLabels" -}}
{{- $out := dict -}}
{{- range $key, $value := . -}}
{{- if eq $key "kubernetes.io/metadata.name" -}}
{{- $_ := set $out "io.kubernetes.pod.namespace" $value -}}
{{- else -}}
{{- $_ := set $out (printf "io.cilium.k8s.namespace.labels.%s" $key) $value -}}
{{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/*
A `NetworkPolicyPort` list as a CNP `toPorts[].ports` list.

The two APIs disagree on the type of a port number: `networking.k8s.io/v1` takes an integer (or
a named port), CNP takes a string. An unquoted integer here is rejected by the CRD schema, so the
conversion is not cosmetic.
*/}}
{{- define "common.cilium.ports" -}}
{{- $out := list -}}
{{- range . -}}
{{- $port := dict "port" (.port | toString) -}}
{{- $_ := set $port "protocol" (.protocol | default "TCP") -}}
{{- $out = append $out $port -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/*
Shared object metadata for both policy objects.
*/}}
{{- define "common.cilium.metadata" -}}
name: {{ include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" .suffix) }}
namespace: {{ include "common.namespace" .ctx }}
labels:
  {{- include "common.labels" .ctx | nindent 2 }}
{{- with (include "common.annotations" .ctx) }}
annotations:
  {{- . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Both CiliumNetworkPolicies for a chart. Reached through `common.networkPolicy`, which selects
the engine; not normally included directly.
*/}}
{{- define "common.ciliumNetworkPolicy" -}}
{{- include "common.ciliumNetworkPolicy.ingress" . }}
---
{{ include "common.ciliumNetworkPolicy.egress" . }}
{{- end -}}

{{/*
Whether default-deny is stated explicitly. Defaults to true.

Read through `hasKey` rather than `| default true`, which cannot express this: `default` returns
its fallback for any empty value, and `false` is empty, so the pipeline form silently ignores the
one setting an operator would bother to write.
*/}}
{{- define "common.cilium.enableDefaultDeny" -}}
{{- $cilium := .Values.networkPolicy.cilium | default dict -}}
{{- if hasKey $cilium "enableDefaultDeny" -}}
{{- $cilium.enableDefaultDeny | toString -}}
{{- else -}}
true
{{- end -}}
{{- end -}}

{{/*
Ingress CiliumNetworkPolicy.
*/}}
{{- define "common.ciliumNetworkPolicy.ingress" -}}
{{- $ingress := .Values.networkPolicy.ingress | default dict -}}
{{- $cilium := .Values.networkPolicy.cilium | default dict -}}
{{- $ciliumIngress := $cilium.ingress | default dict -}}
apiVersion: {{ include "common.capabilities.cilium.apiVersion" . }}
kind: CiliumNetworkPolicy
metadata:
  {{- include "common.cilium.metadata" (dict "ctx" . "suffix" "ingress") | nindent 2 }}
spec:
  {{- with $cilium.description }}
  description: {{ . | quote }}
  {{- end }}
  endpointSelector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  enableDefaultDeny:
    ingress: {{ include "common.cilium.enableDefaultDeny" . }}
    egress: false
  ingress:
    {{- if $ingress.enabled }}
    {{- $monitoring := $ingress.monitoring | default dict }}
    {{- if $monitoring.enabled }}
    - fromEndpoints:
        - matchLabels:
            {{- include "common.cilium.namespaceLabels" ($monitoring.namespaceSelector | default (dict "kubernetes.io/metadata.name" $monitoring.namespace)) | nindent 12 }}
      {{- with $monitoring.ports }}
      toPorts:
        - ports:
            {{- include "common.cilium.ports" . | nindent 12 }}
      {{- end }}
    {{- end }}
    {{- $controller := $ingress.controller | default dict }}
    {{- if $controller.enabled }}
    - fromEndpoints:
        - matchLabels:
            {{- include "common.cilium.namespaceLabels" (dict "kubernetes.io/metadata.name" $controller.namespace) | nindent 12 }}
            {{- toYaml $controller.selector | nindent 12 }}
      {{- with $controller.ports }}
      toPorts:
        - ports:
            {{- include "common.cilium.ports" . | nindent 12 }}
      {{- end }}
    {{- end }}
    {{- $gateway := $ingress.gateway | default dict }}
    {{- if and $gateway.enabled (.Values.gateway | default dict).enabled }}
    {{- $gatewayValues := .Values.gateway | default dict }}
    {{- $namespace := $gateway.namespace | default (include "common.gateway.namespace" (dict "ctx" . "values" $gatewayValues)) }}
    {{- $selector := $gateway.selector | default dict }}
    {{- if not $selector }}
    {{- with (include "common.gateway.name" (dict "ctx" . "values" $gatewayValues)) }}
    {{- $selector = dict "gateway.networking.k8s.io/gateway-name" . }}
    {{- end }}
    {{- end }}
    - fromEndpoints:
        - matchLabels:
            {{- include "common.cilium.namespaceLabels" (dict "kubernetes.io/metadata.name" $namespace) | nindent 12 }}
            {{- with $selector }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
      {{- with $gateway.ports }}
      toPorts:
        - ports:
            {{- include "common.cilium.ports" . | nindent 12 }}
      {{- end }}
    {{- end }}
    {{- end }}
    {{- with $ciliumIngress.fromEntities }}
    - fromEntities:
        {{- toYaml . | nindent 8 }}
    {{- end }}
    {{- with $ciliumIngress.customRules }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
    {{- with $cilium.extraIngress }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
{{- end -}}

{{/*
Egress CiliumNetworkPolicy.

The DNS rule carries `rules.dns` and not only a port. Allowing UDP/53 to kube-dns lets a pod
resolve, but it does not let Cilium *observe* what was resolved — and `toFQDNs` is enforced by
matching the IPs the proxy saw come back from a query it was allowed to inspect. Without the L7
DNS rule every `toFQDNs` entry below silently matches nothing, so the two are emitted together
and `egress.dns.enabled: false` alongside a non-empty `toFQDNs` is reported as a configuration
error rather than left to fail at runtime.
*/}}
{{- define "common.ciliumNetworkPolicy.egress" -}}
{{- $egress := .Values.networkPolicy.egress | default dict -}}
{{- $cilium := .Values.networkPolicy.cilium | default dict -}}
{{- $ciliumEgress := $cilium.egress | default dict -}}
{{- $dns := $egress.dns | default dict -}}
{{- if and $ciliumEgress.toFQDNs (not $dns.enabled) -}}
{{- fail (printf "\n\nNETWORK POLICY CONFIGURATION INVALID for chart %q:\n\n  - networkPolicy.cilium.egress.toFQDNs names destinations but networkPolicy.egress.dns.enabled is false. Cilium enforces an FQDN rule against the addresses its DNS proxy saw returned for that name, so with no DNS rule to observe the lookup the FQDN rule matches nothing and every one of those destinations is denied. Enable the DNS rule or drop the FQDN destinations.\n" .Chart.Name) -}}
{{- end -}}
apiVersion: {{ include "common.capabilities.cilium.apiVersion" . }}
kind: CiliumNetworkPolicy
metadata:
  {{- include "common.cilium.metadata" (dict "ctx" . "suffix" "egress") | nindent 2 }}
spec:
  {{- with $cilium.description }}
  description: {{ . | quote }}
  {{- end }}
  endpointSelector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  enableDefaultDeny:
    ingress: false
    egress: {{ include "common.cilium.enableDefaultDeny" . }}
  egress:
    {{- if $egress.enabled }}
    {{- if $dns.enabled }}
    - toEndpoints:
        - matchLabels:
            {{- include "common.cilium.namespaceLabels" ($dns.namespaceSelector | default (dict "kubernetes.io/metadata.name" "kube-system")) | nindent 12 }}
            {{- toYaml ($dns.podSelector | default (dict "k8s-app" "kube-dns")) | nindent 12 }}
      toPorts:
        - ports:
            - port: "53"
              protocol: UDP
            - port: "53"
              protocol: TCP
          rules:
            dns:
              {{- toYaml ($ciliumEgress.dnsMatchPatterns | default (list (dict "matchPattern" "*"))) | nindent 14 }}
    {{- end }}
    {{- /*
      The CIDR translation of the portable `http`/`https` rules. `toCIDRSet` is the direct
      equivalent of `ipBlock`, `except` and all — kept rather than silently upgraded to
      `toEntities: [world]`, because the two are not the same rule: `world` is everything outside
      the cluster, while the CIDR carve-outs also exclude the cloud metadata endpoint. An
      operator who wants the simpler form asks for it under `cilium.egress.toEntities`.
    */}}
    {{- $cidrSet := list (dict "cidr" ($egress.cidr | default "0.0.0.0/0") "except" ($egress.except | default list)) }}
    {{- if (($egress.http | default dict).enabled) }}
    - toCIDRSet:
        {{- toYaml $cidrSet | nindent 8 }}
      toPorts:
        - ports:
            - port: "80"
              protocol: TCP
    {{- end }}
    {{- if (($egress.https | default dict).enabled) }}
    - toCIDRSet:
        {{- toYaml $cidrSet | nindent 8 }}
      toPorts:
        - ports:
            - port: "443"
              protocol: TCP
    {{- end }}
    {{- with $ciliumEgress.toFQDNs }}
    - toFQDNs:
        {{- toYaml . | nindent 8 }}
      toPorts:
        - ports:
            {{- include "common.cilium.ports" ($ciliumEgress.fqdnPorts | default (list (dict "port" 443 "protocol" "TCP"))) | nindent 12 }}
          {{- with $ciliumEgress.httpRules }}
          rules:
            http:
              {{- toYaml . | nindent 14 }}
          {{- end }}
    {{- end }}
    {{- with $ciliumEgress.toEntities }}
    - toEntities:
        {{- toYaml . | nindent 8 }}
      {{- with $ciliumEgress.entityPorts }}
      toPorts:
        - ports:
            {{- include "common.cilium.ports" . | nindent 12 }}
      {{- end }}
    {{- end }}
    {{- with $ciliumEgress.customRules }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
    {{- end }}
    {{- with $cilium.extraEgress }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $) | nindent 4 }}
    {{- end }}
{{- end -}}
