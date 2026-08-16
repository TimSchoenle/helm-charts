{{/*
The network policy topology, as data.

Why this exists
---------------
The library's `common.networkPolicy` builds one ingress and one egress policy for a
single-workload chart, keyed off a `networkPolicy.egress.{dns,http,https}` value shape. This
chart has up to five workloads whose rules are pod-to-pod and asymmetric — the application
reaches the broker, the database and the two converters, and none of them reaches anything but
DNS. That is a graph, and the interesting part of it is which component may talk to which.

Deriving it once, here, is what keeps the two dialects honest. Two copies of a policy graph do
not diverge loudly; they diverge on one rule, on one upgrade, and the symptom is a deployment
that works under one CNI and hangs under the other. So the walk happens once and produces a
description that `networkpolicy.yaml` and `ciliumnetworkpolicy.yaml` each render in their own
dialect. Neither renderer knows anything about the topology.

Shape
-----
A YAML list, one entry per policy object:

  name        object name
  component   value for the `app.kubernetes.io/component` label
  selector    matchLabels selecting the workload's pods
  ingress     list of rules
  egress      list of rules

A rule is a peer plus `ports`, a list of `{port, protocol}`. The peer is exactly one of:

  selector    matchLabels for pods in this namespace
  namespace   matchLabels for a namespace, optionally with `selector` for pods inside it
  dns         the cluster DNS service (rendered as an L7 DNS rule under Cilium)
  internet    everything outside the cluster, minus RFC1918 and the metadata endpoint
*/}}

{{/*
A peer selecting one of this release's own workloads.

Arguments:
  ctx        (required) root context
  component  (required) `server`, `valkey`, `postgresql`, `gotenberg` or `tika`
*/}}
{{- define "paperless-ngx.netpol.componentPeer" -}}
selector:
  app.kubernetes.io/name: {{ include "common.name" .ctx }}
  app.kubernetes.io/instance: {{ .ctx.Release.Name }}
  app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
The Gateway API data plane as a plan peer.

Derived from `gateway.parentRefs` rather than configured: the Gateway a policy must admit is by
definition the one this chart's route attaches to, so restating it under `networkPolicy` would
only be a second place to edit on a rename — and a policy naming the wrong Gateway looks correct
and blocks all inbound traffic. The two override fields exist for an implementation that labels
the pods it provisions differently.
*/}}
{{- define "paperless-ngx.netpol.gatewayPeer" -}}
{{- $override := .Values.networkPolicy.ingress.gateway -}}
{{- $namespace := $override.namespaceSelector | default dict -}}
{{- if not $namespace -}}
{{- $namespace = dict "kubernetes.io/metadata.name" (include "common.gateway.namespace" (dict "ctx" . "values" .Values.gateway)) -}}
{{- end -}}
{{- $selector := $override.podSelector | default dict -}}
{{- if not $selector -}}
{{- with (include "common.gateway.name" (dict "ctx" . "values" .Values.gateway)) -}}
{{- $selector = dict "gateway.networking.k8s.io/gateway-name" . -}}
{{- end -}}
{{- end -}}
namespace:
  {{- toYaml $namespace | nindent 2 }}
{{- with $selector }}
selector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Every policy object this chart would create, as YAML.

Usage:
  {{- $plan := include "paperless-ngx.netpol.plan" . | fromYamlArray }}
*/}}
{{- define "paperless-ngx.netpol.plan" -}}
{{- $ctx := . -}}
{{- $policy := $ctx.Values.networkPolicy -}}
{{- $httpPorts := list (dict "port" 8000 "protocol" "TCP") -}}
{{- $serverPeer := include "paperless-ngx.netpol.componentPeer" (dict "ctx" $ctx "component" "server") | fromYaml -}}
{{- $policies := list -}}

{{- /* Inbound to the application: whatever publishes it, and nothing else. */ -}}
{{- $ingress := list -}}
{{- $controller := $policy.ingress.controller -}}
{{- if and $ctx.Values.ingress.enabled $controller.enabled -}}
{{- $ingress = append $ingress (dict
      "namespace" (dict "kubernetes.io/metadata.name" $controller.namespace)
      "selector" $controller.podSelector
      "ports" $httpPorts) -}}
{{- end -}}
{{- if and $ctx.Values.gateway.enabled $policy.ingress.gateway.enabled -}}
{{- $gatewayPeer := include "paperless-ngx.netpol.gatewayPeer" $ctx | fromYaml -}}
{{- $ingress = append $ingress (merge (dict "ports" $httpPorts) $gatewayPeer) -}}
{{- end -}}

{{- /* Outbound from the application. */ -}}
{{- $egress := list (dict "dns" true) -}}
{{- $external := list -}}

{{- if include "paperless-ngx.componentEnabled" (dict "ctx" $ctx "component" "valkey") -}}
{{- $egress = append $egress (merge
      (dict "ports" (list (dict "port" 6379 "protocol" "TCP")))
      (include "paperless-ngx.netpol.componentPeer" (dict "ctx" $ctx "component" "valkey") | fromYaml)) -}}
{{- else -}}
{{- $external = append $external (dict "port" (int $ctx.Values.redis.port) "protocol" "TCP") -}}
{{- end -}}

{{- if ne $ctx.Values.database.engine "sqlite" -}}
{{- if include "paperless-ngx.componentEnabled" (dict "ctx" $ctx "component" "postgresql") -}}
{{- $egress = append $egress (merge
      (dict "ports" (list (dict "port" 5432 "protocol" "TCP")))
      (include "paperless-ngx.netpol.componentPeer" (dict "ctx" $ctx "component" "postgresql") | fromYaml)) -}}
{{- else -}}
{{- $external = append $external (dict "port" (int (include "paperless-ngx.databasePort" $ctx)) "protocol" "TCP") -}}
{{- end -}}
{{- end -}}

{{- /*
  A datastore outside this release can only be addressed as "some pods somewhere", because the
  chart knows its DNS name and not its labels. One rule for both of them, so the permission is
  as narrow as the portable API allows rather than one broad rule per datastore.
*/ -}}
{{- if and $external $policy.egress.external.enabled -}}
{{- $peer := dict "ports" $external -}}
{{- $namespaceSelector := $policy.egress.external.namespaceSelector | default dict -}}
{{- if not $namespaceSelector -}}
{{- $namespaceSelector = dict "kubernetes.io/metadata.name" (include "common.namespace" $ctx) -}}
{{- end -}}
{{- $_ := set $peer "namespace" $namespaceSelector -}}
{{- with $policy.egress.external.podSelector -}}
{{- $_ := set $peer "selector" . -}}
{{- end -}}
{{- $egress = append $egress $peer -}}
{{- end -}}

{{- range $component := list "gotenberg" "tika" -}}
{{- if include "paperless-ngx.componentEnabled" (dict "ctx" $ctx "component" $component) -}}
{{- $port := ternary 3000 9998 (eq $component "gotenberg") -}}
{{- $egress = append $egress (merge
      (dict "ports" (list (dict "port" $port "protocol" "TCP")))
      (include "paperless-ngx.netpol.componentPeer" (dict "ctx" $ctx "component" $component) | fromYaml)) -}}
{{- end -}}
{{- end -}}

{{- $internetPorts := list -}}
{{- if $policy.egress.https -}}
{{- $internetPorts = append $internetPorts (dict "port" 443 "protocol" "TCP") -}}
{{- end -}}
{{- if $policy.egress.smtp -}}
{{- $internetPorts = concat $internetPorts (list (dict "port" 587 "protocol" "TCP") (dict "port" 465 "protocol" "TCP")) -}}
{{- end -}}
{{- if $internetPorts -}}
{{- $egress = append $egress (dict "internet" true "ports" $internetPorts) -}}
{{- end -}}

{{- $policies = append $policies (dict
      "name" (include "common.fullname" $ctx)
      "component" "server"
      "selector" $serverPeer.selector
      "ingress" $ingress
      "egress" $egress) -}}

{{- /*
  The backup pod. It carries its own component label, so nothing above selects it — and a
  default-deny namespace with no policy naming it produces a Job that reaches neither DNS nor the
  database and hangs until `activeDeadlineSeconds`, an hour later, with no error that points at
  the network.

  Its egress is the application's minus the two document converters: `document_exporter` reads
  the database and the media volume and never renders anything. No ingress at all — it listens on
  nothing, and the empty rule list is what makes that explicit rather than accidental.

  The internet rules are reused deliberately. `backup.upload` runs in this same pod, and an
  uploader shipping the export to object storage is exactly what `networkPolicy.egress.https` is
  for; without it an otherwise correct upload container times out.
*/ -}}
{{- if $ctx.Values.backup.enabled -}}
{{- $backupEgress := list -}}
{{- range $rule := $egress -}}
{{- $converter := false -}}
{{- range $port := ($rule.ports | default list) -}}
{{- if or (eq (int $port.port) 3000) (eq (int $port.port) 9998) -}}
{{- $converter = true -}}
{{- end -}}
{{- end -}}
{{- if not $converter -}}
{{- $backupEgress = append $backupEgress (deepCopy $rule) -}}
{{- end -}}
{{- end -}}
{{- $policies = append $policies (dict
      "name" (include "paperless-ngx.backup.name" $ctx)
      "component" "backup"
      "selector" (include "paperless-ngx.netpol.componentPeer" (dict "ctx" $ctx "component" "backup") | fromYaml).selector
      "ingress" list
      "egress" $backupEgress) -}}
{{- end -}}

{{- /* One policy per bundled component: reachable by the application, and outbound to DNS. */ -}}
{{- range $component := list "postgresql" "valkey" "gotenberg" "tika" -}}
{{- if include "paperless-ngx.componentEnabled" (dict "ctx" $ctx "component" $component) -}}
{{- $port := 5432 -}}
{{- if eq $component "valkey" }}{{- $port = 6379 -}}{{- end -}}
{{- if eq $component "gotenberg" }}{{- $port = 3000 -}}{{- end -}}
{{- if eq $component "tika" }}{{- $port = 9998 -}}{{- end -}}
{{- $peer := include "paperless-ngx.netpol.componentPeer" (dict "ctx" $ctx "component" $component) | fromYaml -}}
{{- $policies = append $policies (dict
      "name" (include "paperless-ngx.componentName" (dict "ctx" $ctx "component" $component))
      "component" $component
      "selector" $peer.selector
      "ingress" (list (merge (dict "ports" (list (dict "port" $port "protocol" "TCP"))) (deepCopy $serverPeer)))
      "egress" (list (dict "dns" true))) -}}
{{- end -}}
{{- end -}}

{{- toYaml $policies -}}
{{- end -}}
