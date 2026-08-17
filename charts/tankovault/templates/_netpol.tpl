{{/*
The network policy topology, as data.

Why this exists
---------------
This chart has twelve workloads whose rules are pod-to-pod and asymmetric, and the interesting
part is exactly which service may talk to which. That has always been derived from
`tankovault.serviceSpecs` (`ingressFrom`, `egressServices`, `egressInternet`) rather than written
out as a dozen literal rules — but the derivation used to live inside the NetworkPolicy template,
which meant a second policy dialect could only be added by copying the whole walk.

Two copies of a policy graph is the failure this avoids. They would not diverge loudly; they
would diverge on one rule, on one upgrade, and the symptom would be a service that works under
one CNI and hangs under the other. So the walk happens once, here, and produces a description of
the policies that `networkpolicy.yaml` and `ciliumnetworkpolicy.yaml` each render in their own
dialect. Neither renderer knows anything about the topology.

Shape
-----
A YAML list, one entry per policy object:

  name        object name
  component   value for the `app.kubernetes.io/component` label
  selector    matchLabels selecting the workload's pods
  ingress     list of {peer, port}
  egress      list of {peer, port} — plus the two peers that are not pod selectors

A peer is exactly one of:

  selector    matchLabels for pods in this namespace
  namespace   matchLabels for a namespace, optionally with `selector` for pods inside it
  dns         the cluster DNS service (rendered as an L7 DNS rule under Cilium)
  internet    everything outside the cluster, minus the private ranges and the metadata endpoint
*/}}

{{/*
The carve-outs for one entry of `networkPolicy.internetCidrs`, chosen by address family.

Why the split is mandatory, not tidiness
----------------------------------------
`networking.k8s.io/v1` validates that every `except` lies inside its `ipBlock.cidr`, so a v4
range under `::/0` is rejected by the API server outright — one list covering both families is
not a thing that can be written. Both renderers call this so the carve-outs cannot differ
between the two dialects any more than the topology can.

What each family excludes, and why the two lists are not translations of each other
-----------------------------------------------------------------------------------
v4 is the familiar set: RFC1918 plus 169.254.0.0/16, which is where the cloud metadata endpoint
lives. A tier that fetches attacker-influenced URLs and can also reach 169.254.169.254 is one
SSRF away from the node's instance credentials.

v6 has to close the same door twice more, because IPv6 has two ways to name an IPv4 address:

  fc00::/7            unique-local — every IPv6 pod and service CIDR in practice, and the ULA
                      metadata address fd00:ec2::254 with it. The v6 counterpart of RFC1918.
  fe80::/10           link-local, which is where IPv6 neighbours and the node itself answer.
  ::ffff:0:0/96       IPv4-mapped. `::ffff:169.254.169.254` is the metadata endpoint written as
                      an IPv6 literal, and the v4 excepts above do not see it.
  64:ff9b::a00:0/104  the well-known NAT64 prefix's images of the four v4 ranges. On a cluster
  64:ff9b::ac10:0/108 with NAT64 those are literally routes to 10/8, 172.16/12, 192.168/16 and
  64:ff9b::c0a8:0/112 169.254/16. Only the images are excluded, so NAT64 still reaches public
  64:ff9b::a9fe:0/112 IPv4 — which on an IPv6-only cluster is the whole point of it.

Only the well-known prefix is covered: a network-specific NAT64 prefix is by definition local
knowledge, and belongs in an `internetCidrs` override rather than guessed at here.
*/}}
{{- define "tankovault.netpol.internetExcept" -}}
{{- if contains ":" . }}
- fc00::/7
- fe80::/10
- ::ffff:0:0/96
- 64:ff9b::a00:0/104
- 64:ff9b::ac10:0/108
- 64:ff9b::c0a8:0/112
- 64:ff9b::a9fe:0/112
{{- else }}
- 10.0.0.0/8
- 172.16.0.0/12
- 192.168.0.0/16
- 169.254.0.0/16
{{- end }}
{{- end -}}

{{/*
A peer selecting one TankoVault service by its per-service name label.
*/}}
{{- define "tankovault.netpol.plan.servicePeer" -}}
selector:
  app.kubernetes.io/name: {{ include "tankovault.name" (dict "ctx" .ctx "service" .service) }}
  app.kubernetes.io/instance: {{ .ctx.Release.Name }}
{{- end -}}

{{/*
A peer selecting one bundled datastore. These carry the shared name label and are distinguished
by `app.kubernetes.io/component`.
*/}}
{{- define "tankovault.netpol.plan.componentPeer" -}}
selector:
  app.kubernetes.io/name: {{ include "common.name" .ctx }}
  app.kubernetes.io/instance: {{ .ctx.Release.Name }}
  app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Every policy object this chart would create, as YAML.

Usage:
  {{- $plan := include "tankovault.netpol.plan" . | fromYamlArray }}
*/}}
{{- define "tankovault.netpol.plan" -}}
{{- $ctx := . -}}
{{- $specs := include "tankovault.serviceSpecs" $ctx | fromYaml -}}
{{- $policies := list -}}

{{- /* Which services consume each bundled datastore, derived from the same `needs*` flags that
       drive their egress — so the two halves of every rule cannot drift apart. */ -}}
{{- $dbConsumers := list -}}
{{- $redisConsumers := list -}}
{{- $natsConsumers := list -}}
{{- range $service, $spec := $specs -}}
{{- if (index $ctx.Values.services $service).enabled -}}
{{- if $spec.needsDatabase }}{{ $dbConsumers = append $dbConsumers $service }}{{ end -}}
{{- if $spec.needsRedis }}{{ $redisConsumers = append $redisConsumers $service }}{{ end -}}
{{- if $spec.needsNats }}{{ $natsConsumers = append $natsConsumers $service }}{{ end -}}
{{- end -}}
{{- end -}}

{{- /*
The bundled TRAWL is the one datastore that is also a *client* of another one: it keeps its
solved-cookie jar in Redis. The rule is derived by comparing the URL it resolved against the
bundled Valkey's, so an explicit `trawl.redis.url` that happens to name the same instance is
covered too, and one pointing anywhere else is not silently granted in-cluster access.
*/ -}}
{{- $trawlUsesValkey := and $ctx.Values.trawl.enabled $ctx.Values.valkey.enabled
      (include "tankovault.trawlRedisUrl" $ctx)
      (eq (include "tankovault.trawlRedisUrl" $ctx) (include "tankovault.redisUrl" $ctx)) -}}

{{- /*
The exporter is the one component that scrapes a datastore rather than using it, so it appears on
both halves of a rule the others do not have: NATS gains an ingress on its *monitoring* port
(8222, not the client 4222 every service uses), and the exporter gains the matching egress. It is
granted only when it is pointed at the bundled NATS — an exporter aimed elsewhere is reaching
outside this release and gets no in-cluster grant, exactly as `trawl.redis.url` is handled above.
*/ -}}
{{- $natsExporterEnabled := and $ctx.Values.metrics.enabled $ctx.Values.metrics.natsExporter.enabled -}}
{{- $exporterUsesBundledNats := and $natsExporterEnabled $ctx.Values.nats.enabled (not $ctx.Values.metrics.natsExporter.url) -}}

{{- $monitoringPeer := dict "namespace" $ctx.Values.networkPolicy.monitoring.namespaceSelector -}}
{{- $controllerPeer := dict "namespace" $ctx.Values.networkPolicy.ingressController.namespaceSelector "selector" $ctx.Values.networkPolicy.ingressController.podSelector -}}
{{- $gatewayPeer := include "tankovault.netpol.plan.gatewayPeer" $ctx | fromYaml -}}

{{- /* One policy per enabled service. */ -}}
{{- range $service, $spec := $specs -}}
{{- if (index $ctx.Values.services $service).enabled -}}
{{- $ingress := list -}}
{{- $egress := list -}}

{{- range $peer := $spec.ingressFrom -}}
{{- if (index $ctx.Values.services $peer).enabled -}}
{{- $ingress = append $ingress (merge (dict "port" $spec.port) (include "tankovault.netpol.plan.servicePeer" (dict "ctx" $ctx "service" $peer) | fromYaml)) -}}
{{- end -}}
{{- end -}}

{{- if and $ctx.Values.ingress.enabled (eq $service "frontend") -}}
{{- $ingress = append $ingress (merge (dict "port" $spec.port) (deepCopy $controllerPeer)) -}}
{{- end -}}
{{- if and $ctx.Values.ingress.api.enabled (eq $service "api") -}}
{{- $ingress = append $ingress (merge (dict "port" $spec.port) (deepCopy $controllerPeer)) -}}
{{- end -}}
{{- if and $ctx.Values.gateway.enabled (eq $service "frontend") -}}
{{- $ingress = append $ingress (merge (dict "port" $spec.port) (deepCopy $gatewayPeer)) -}}
{{- end -}}
{{- if and $ctx.Values.gateway.api.enabled (eq $service "api") -}}
{{- $ingress = append $ingress (merge (dict "port" $spec.port) (deepCopy $gatewayPeer)) -}}
{{- end -}}
{{- if $ctx.Values.metrics.enabled -}}
{{- $ingress = append $ingress (merge (dict "port" $ctx.Values.metrics.port) (deepCopy $monitoringPeer)) -}}
{{- end -}}

{{- $egress = append $egress (dict "dns" true) -}}
{{- range $peer := $spec.egressServices -}}
{{- if (index $ctx.Values.services $peer).enabled -}}
{{- $peerSpec := include "tankovault.spec" $peer | fromYaml -}}
{{- $egress = append $egress (merge (dict "port" $peerSpec.port) (include "tankovault.netpol.plan.servicePeer" (dict "ctx" $ctx "service" $peer) | fromYaml)) -}}
{{- end -}}
{{- end -}}
{{- if and $spec.needsDatabase $ctx.Values.postgresql.enabled -}}
{{- $egress = append $egress (merge (dict "port" 5432) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" "postgresql") | fromYaml)) -}}
{{- end -}}
{{- if and $spec.needsRedis $ctx.Values.valkey.enabled -}}
{{- $egress = append $egress (merge (dict "port" 6379) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" "valkey") | fromYaml)) -}}
{{- end -}}
{{- if and $spec.needsNats $ctx.Values.nats.enabled -}}
{{- $egress = append $egress (merge (dict "port" 4222) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" "nats") | fromYaml)) -}}
{{- end -}}
{{- if and (eq $service "challengeSolver") $ctx.Values.trawl.enabled -}}
{{- $egress = append $egress (merge (dict "port" 8191) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" "trawl") | fromYaml)) -}}
{{- end -}}
{{- if $spec.egressInternet -}}
{{- $egress = append $egress (dict "internet" true) -}}
{{- end -}}

{{- $policies = append $policies (dict
      "name" (include "tankovault.fullname" (dict "ctx" $ctx "service" $service))
      "component" $spec.slug
      "selector" (dict
        "app.kubernetes.io/name" (include "tankovault.name" (dict "ctx" $ctx "service" $service))
        "app.kubernetes.io/instance" $ctx.Release.Name)
      "ingress" $ingress
      "egress" $egress) -}}
{{- end -}}
{{- end -}}

{{- /* One policy per enabled bundled datastore. */ -}}
{{- range $ds := list
      (dict "component" "postgresql" "enabled" $ctx.Values.postgresql.enabled "port" 5432 "consumers" $dbConsumers "bootstrap" true "internet" false)
      (dict "component" "valkey" "enabled" $ctx.Values.valkey.enabled "port" 6379 "consumers" $redisConsumers "bootstrap" false "internet" false "componentConsumers" (ternary (list "trawl") list $trawlUsesValkey))
      (dict "component" "nats" "enabled" $ctx.Values.nats.enabled "port" 4222 "consumers" $natsConsumers "bootstrap" false "internet" false "extraIngress" (ternary (list (dict "component" "nats-exporter" "port" 8222)) list $exporterUsesBundledNats))
      (dict "component" "nats-exporter" "enabled" $natsExporterEnabled "port" 7777 "consumers" list "bootstrap" false "internet" false "scraped" true "componentEgress" (ternary (list (dict "component" "nats" "port" 8222)) list $exporterUsesBundledNats))
      (dict "component" "trawl" "enabled" $ctx.Values.trawl.enabled "port" 8191 "consumers" (list "challengeSolver") "bootstrap" false "internet" true "componentEgress" (ternary (list (dict "component" "valkey" "port" 6379)) list $trawlUsesValkey)) -}}
{{- if $ds.enabled -}}
{{- $ingress := list -}}
{{- $egress := list -}}

{{- range $consumer := $ds.consumers -}}
{{- if (index $ctx.Values.services $consumer).enabled -}}
{{- $ingress = append $ingress (merge (dict "port" $ds.port) (include "tankovault.netpol.plan.servicePeer" (dict "ctx" $ctx "service" $consumer) | fromYaml)) -}}
{{- end -}}
{{- end -}}
{{- if $ds.bootstrap -}}
{{- $ingress = append $ingress (merge (dict "port" $ds.port) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" "bootstrap") | fromYaml)) -}}
{{- end -}}
{{- range $consumer := $ds.componentConsumers -}}
{{- $ingress = append $ingress (merge (dict "port" $ds.port) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" $consumer) | fromYaml)) -}}
{{- end -}}
{{- range $peer := $ds.extraIngress -}}
{{- $ingress = append $ingress (merge (dict "port" $peer.port) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" $peer.component) | fromYaml)) -}}
{{- end -}}
{{- if $ds.scraped -}}
{{- $ingress = append $ingress (merge (dict "port" $ds.port) (deepCopy $monitoringPeer)) -}}
{{- end -}}

{{- $egress = append $egress (dict "dns" true) -}}
{{- range $peer := $ds.componentEgress -}}
{{- $egress = append $egress (merge (dict "port" $peer.port) (include "tankovault.netpol.plan.componentPeer" (dict "ctx" $ctx "component" $peer.component) | fromYaml)) -}}
{{- end -}}
{{- if $ds.internet -}}
{{- $egress = append $egress (dict "internet" true) -}}
{{- end -}}

{{- $policies = append $policies (dict
      "name" (include "common.fullname.suffixed" (dict "ctx" $ctx "suffix" $ds.component))
      "component" $ds.component
      "selector" (dict
        "app.kubernetes.io/name" (include "common.name" $ctx)
        "app.kubernetes.io/instance" $ctx.Release.Name
        "app.kubernetes.io/component" $ds.component)
      "ingress" $ingress
      "egress" $egress) -}}
{{- end -}}
{{- end -}}

{{- toYaml $policies -}}
{{- end -}}

{{/*
The Gateway API data plane as a plan peer.

Derived from `gateway.parentRefs` rather than configured: the Gateway a policy must admit is by
definition the one this chart's own routes attach to, so restating it under `networkPolicy` would
only be a second place to edit on a rename — and a policy naming the wrong Gateway looks correct
and blocks all inbound traffic. `networkPolicy.gateway` overrides either half, for an
implementation that labels the pods it provisions differently.
*/}}
{{- define "tankovault.netpol.plan.gatewayPeer" -}}
{{- $override := .Values.networkPolicy.gateway | default dict -}}
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
