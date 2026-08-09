{{/*
Gateway API support: the HTTPRoute a chart publishes itself with, the optional HTTP→HTTPS
redirect route, and the optional Gateway itself.

Why this exists alongside `templates/ingress.yaml` rather than replacing it
---------------------------------------------------------------------------
Ingress and Gateway API are not two spellings of one object. Ingress folds "which load balancer",
"which hostnames", "which backend" and "everything the spec cannot express" into one resource,
with the last of those living in controller-specific annotations that no other controller reads.
Gateway API splits that in two: the cluster operator owns the `Gateway` (the listeners, the
address, the certificates) and the application owns the `HTTPRoute` that attaches to it. So the
chart's job here is smaller than the Ingress one — it publishes a route and names the Gateway it
wants to attach to — and the things that used to be annotations become typed fields: rewrites and
header manipulation are `filters`, timeouts are `timeouts`, TLS is the Gateway's listener.

The two are independent switches on purpose. A cluster migrating from an Ingress controller to a
Gateway implementation runs both for a while, and a chart that forced the choice would make that
window impossible.

Route-only by default
---------------------
`gateway.parentRefs` points at a Gateway somebody else owns, which is the split of responsibility
the API was designed around. `gateway.create` additionally renders one, for a self-contained
install that has no cluster-wide Gateway to attach to — and when it is set, a route that names no
parent attaches to it automatically, so the two halves cannot disagree.

What is deliberately *not* rendered: `ReferenceGrant`
-----------------------------------------------------
A cross-namespace reference (a `backendRef` or a `certificateRef` in another namespace) is only
permitted when a `ReferenceGrant` in the *target's* namespace allows it. That object belongs to
whoever owns the namespace being referenced. A chart that emitted its own grants would be
asserting permission it was never given — the grant would simply be the chart authorising itself,
which is the one thing the object exists to prevent. Cross-namespace references are supported
here; the grant that makes them work is the target namespace owner's to create.
*/}}

{{/*
The Gateway API group. One place, so the guard, the render and the error messages agree.
*/}}
{{- define "common.gateway.group" -}}
gateway.networking.k8s.io
{{- end -}}

{{/*
A message when the Gateway API CRDs are missing, empty when they are present. Returned rather
than raised so consuming charts can fold it into an aggregated report.

Refusing rather than skipping is the same call `common.prometheus.operatorErrors` makes, for the
same reason: `helm template` reports the built-in API surface but no CRDs, so a capability guard
that silently dropped the route would render clean in CI and leave a real install succeeding with
the application unreachable and nothing to point at.
*/}}
{{- define "common.gateway.crdErrors" -}}
{{- $api := printf "%s/v1" (include "common.gateway.group" .ctx) -}}
{{- $beta := printf "%s/v1beta1" (include "common.gateway.group" .ctx) -}}
{{- if not (or (include "common.capabilities.apiVersions.has" (dict "ctx" .ctx "api" $api)) (include "common.capabilities.apiVersions.has" (dict "ctx" .ctx "api" $beta))) -}}
{{- printf "%s is set, but the cluster registers no `%s` API. Install the Gateway API CRDs first (`kubectl apply -k github.com/kubernetes-sigs/gateway-api/config/crd/standard`), or pass `--api-versions %s` if you are rendering offline with `helm template`. Rendering regardless would produce manifests the API server rejects at apply time." (.feature | default "gateway.enabled") $api $api -}}
{{- end -}}
{{- end -}}

{{/*
Every problem the chart can detect in the gateway configuration, as newline-separated messages;
empty when it is sound.
*/}}
{{- define "common.gateway.errors" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $messages := list -}}
{{- if $values.enabled -}}
{{- with (include "common.gateway.crdErrors" (dict "ctx" $ctx "feature" (.feature | default "gateway.enabled"))) -}}
{{- $messages = append $messages . -}}
{{- end -}}
{{- if and (not $values.parentRefs) (not $values.create) -}}
{{- $messages = append $messages "gateway.enabled is set but gateway.parentRefs is empty and gateway.create is false, so the route would name no Gateway to attach to. An HTTPRoute without a parent is accepted by the API server and then does nothing at all — no listener ever programs it, and the failure is invisible until somebody tries the hostname. Either name the Gateway your cluster operator runs, or set gateway.create to have this chart render one." -}}
{{- end -}}
{{- range $i, $ref := $values.parentRefs | default list -}}
{{- if not $ref.name -}}
{{- $messages = append $messages (printf "gateway.parentRefs[%d] has no `name`. A parent reference is resolved by name; there is no default." $i) -}}
{{- end -}}
{{- end -}}
{{- /*
An HTTPRoute with no hostnames matches every hostname its listener accepts. On a Gateway this
chart created that is harmless and sometimes the point — an install reached by address, with no
DNS name to state. On somebody else's Gateway it is this chart silently taking over traffic
meant for other applications, which is why the requirement is scoped to that case.
*/ -}}
{{- if and (not $values.create) (not (include "common.gateway.hostnames" (dict "ctx" $ctx "values" $values))) -}}
{{- $messages = append $messages "gateway.enabled is set but gateway.hostnames is empty, and the route attaches to a Gateway this chart does not own. A route with no hostnames matches every hostname its listener accepts, so on a shared Gateway this silently takes over traffic meant for other applications. Name the hostnames this chart serves, or set gateway.create if the Gateway is meant to be this chart's own." -}}
{{- end -}}
{{- if and $values.create (not $values.gatewayClassName) -}}
{{- $messages = append $messages "gateway.create is set but gateway.gatewayClassName is empty. The class is what selects the implementation that programs the Gateway (`cilium`, `istio`, `envoy-gateway`, ...); a Gateway without one is never reconciled." -}}
{{- end -}}
{{- if and $values.create (($values.tls | default dict).enabled) (not (($values.tls | default dict).certificateRefs)) (not $values.listeners) -}}
{{- $messages = append $messages "gateway.create and gateway.tls.enabled are set but gateway.tls.certificateRefs is empty. A `Terminate` listener needs a certificate to terminate with; unlike an Ingress there is no convention by which one is looked up from the hostname." -}}
{{- end -}}
{{- if and (($values.httpsRedirect | default dict).enabled) $values.create (not (($values.tls | default dict).enabled)) (not $values.listeners) -}}
{{- $messages = append $messages "gateway.httpsRedirect.enabled is set but nothing in this chart terminates TLS. The redirect route only makes sense when an HTTPS listener exists to redirect to — otherwise it sends every client to a port that refuses the connection. This is only checked for Gateways this chart creates; when you attach to somebody else's Gateway, its listeners are theirs to declare." -}}
{{- end -}}
{{- end -}}
{{- join "\n" $messages -}}
{{- end -}}

{{/*
Raise the errors above directly. For charts without an aggregated validator of their own.

Usage:
  {{- include "common.gateway.validate" (dict "ctx" $ "values" .Values.gateway) }}
*/}}
{{- define "common.gateway.validate" -}}
{{- $errors := include "common.gateway.errors" . -}}
{{- if $errors -}}
{{- fail (printf "\n\nGATEWAY CONFIGURATION INVALID for chart %q:\n\n  - %s\n" .ctx.Chart.Name (join "\n  - " (splitList "\n" $errors))) -}}
{{- end -}}
{{- end -}}

{{/*
The hostnames this chart serves, as a YAML list; empty when none are configured.

Templated, so `"{{ .Release.Name }}.example.com"` works the way it does for `ingress.hosts`.
*/}}
{{- define "common.gateway.hostnames" -}}
{{- $values := .values | default dict -}}
{{- with $values.hostnames -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $.ctx) -}}
{{- end -}}
{{- end -}}

{{/*
The resolved parent references, as a YAML list.

Each entry is normalised to the full `{group, kind, name, namespace, sectionName, port}` shape
with the API's own defaults filled in, so consumers — including the NetworkPolicy peer derivation
in `_networkpolicy.tpl` — never have to re-implement "what does an omitted `group` mean". Optional
fields are omitted rather than emitted empty: `sectionName: ""` is not the same as no
`sectionName`, it is a reference to a listener named the empty string.

When `gateway.create` is set and no parent is named, the route attaches to the Gateway this chart
renders. That is what makes a self-contained install a two-line configuration.
*/}}
{{- define "common.gateway.parentRefs" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $refs := $values.parentRefs | default list -}}
{{- if and (not $refs) $values.create -}}
{{- $refs = list (dict "name" (include "common.fullname" $ctx)) -}}
{{- end -}}
{{- $out := list -}}
{{- range $refs -}}
{{- $ref := dict "group" (.group | default (include "common.gateway.group" $ctx)) "kind" (.kind | default "Gateway") "name" (tpl (.name | toString) $ctx) -}}
{{- with .namespace -}}
{{- $_ := set $ref "namespace" (tpl (. | toString) $ctx) -}}
{{- end -}}
{{- with .sectionName -}}
{{- $_ := set $ref "sectionName" (tpl (. | toString) $ctx) -}}
{{- end -}}
{{- if .port -}}
{{- $_ := set $ref "port" (int .port) -}}
{{- end -}}
{{- $out = append $out $ref -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/*
Name of the Gateway this chart's routes attach to: the first parent reference, or the Gateway
this chart creates. Empty when neither is configured.

This is what lets a NetworkPolicy allow the Gateway's data plane without the operator restating
the Gateway's identity in a second place — see `networkPolicy.ingress.gateway` in
`_networkpolicy.tpl`.
*/}}
{{- define "common.gateway.name" -}}
{{- $refs := include "common.gateway.parentRefs" . | fromYamlArray -}}
{{- with (first $refs) -}}
{{- .name -}}
{{- end -}}
{{- end -}}

{{/*
Namespace of the Gateway this chart's routes attach to. Falls back to the release namespace,
which is what an omitted `parentRefs[].namespace` means to the API.
*/}}
{{- define "common.gateway.namespace" -}}
{{- $refs := include "common.gateway.parentRefs" . | fromYamlArray -}}
{{- $ns := "" -}}
{{- with (first $refs) -}}
{{- $ns = .namespace | default "" -}}
{{- end -}}
{{- $ns | default (include "common.namespace" .ctx) -}}
{{- end -}}

{{/*
The default backend for a rule that names none: this chart's own Service.

Arguments:
  ctx      (required) root context
  values   (required) the `gateway` value tree
  service  Service name (default: the chart's fullname)
  port     Service port (default: `.Values.service.port`)
*/}}
{{- define "common.gateway.backendRefs" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- if $values.backendRefs -}}
{{- include "common.tplvalues.render" (dict "value" $values.backendRefs "context" $ctx) -}}
{{- else -}}
{{- $port := .port | default (($ctx.Values.service | default dict).port) -}}
{{- if not $port -}}
{{- fail (printf "\n\nGATEWAY CONFIGURATION INVALID for chart %q:\n\n  - the route has no backend. This chart exposes no `service.port` to fall back on, so `gateway.backendRefs` has to name the Service and port to route to explicitly.\n" $ctx.Chart.Name) -}}
{{- end -}}
{{- toYaml (list (dict "name" (.service | default (include "common.fullname" $ctx)) "port" (int $port))) -}}
{{- end -}}
{{- end -}}

{{/*
The route's rules, as a YAML list.

`gateway.rules` is the full expression of the API — `matches`, `filters`, `backendRefs`,
`timeouts` — and each entry that omits `backendRefs` inherits this chart's Service, so a rule that
only narrows the path does not have to restate where the traffic goes. With no rules at all the
result is the Ingress equivalent of `path: / (Prefix)`, which is what almost every deployment
wants and is why `gateway.enabled` plus `gateway.hostnames` is a complete configuration.
*/}}
{{- define "common.gateway.rules" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $backendRefs := include "common.gateway.backendRefs" . | fromYamlArray -}}
{{- $rules := list -}}
{{- if $values.rules -}}
{{- range (include "common.tplvalues.render" (dict "value" $values.rules "context" $ctx) | fromYamlArray) -}}
{{- $rule := deepCopy . -}}
{{- if not $rule.backendRefs -}}
{{- $_ := set $rule "backendRefs" $backendRefs -}}
{{- end -}}
{{- $rules = append $rules $rule -}}
{{- end -}}
{{- else -}}
{{- $rule := dict
      "matches" (list (dict "path" (dict "type" "PathPrefix" "value" ($values.path | default "/"))))
      "backendRefs" $backendRefs -}}
{{- with $values.filters -}}
{{- $_ := set $rule "filters" (include "common.tplvalues.render" (dict "value" . "context" $ctx) | fromYamlArray) -}}
{{- end -}}
{{- with $values.timeouts -}}
{{- $_ := set $rule "timeouts" . -}}
{{- end -}}
{{- $rules = append $rules $rule -}}
{{- end -}}
{{- toYaml $rules -}}
{{- end -}}

{{/*
The HTTPRoute.

Arguments:
  ctx      (required) root context
  values   (required) the `gateway` value tree
  name     object name (default: the chart's fullname)
  labels   extra labels merged into metadata (a dict)
  service  Service name for the default backend
  port     Service port for the default backend
*/}}
{{- define "common.gateway.httpRoute" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
apiVersion: {{ include "common.capabilities.gateway.apiVersion" $ctx }}
kind: HTTPRoute
metadata:
  name: {{ .name | default (include "common.fullname" $ctx) }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
    {{- with .labels }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  {{- with (include "common.tplvalues.merge" (dict "values" (list $values.annotations $ctx.Values.commonAnnotations) "context" $ctx)) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  parentRefs:
    {{- include "common.gateway.parentRefs" (dict "ctx" $ctx "values" $values) | nindent 4 }}
  {{- with (include "common.gateway.hostnames" (dict "ctx" $ctx "values" $values)) }}
  hostnames:
    {{- . | nindent 4 }}
  {{- end }}
  rules:
    {{- include "common.gateway.rules" . | nindent 4 }}
{{- end -}}

{{/*
The HTTP→HTTPS redirect route.

This has no annotation equivalent to inherit. Under Ingress, redirecting to HTTPS was
`nginx.ingress.kubernetes.io/ssl-redirect` or `traefik.ingress.kubernetes.io/router.middlewares`
— a different string per controller, none of them portable. Gateway API expresses it as a typed
`RequestRedirect` filter, which means it has to be a real object: a second route bound to the
plaintext listener whose only rule is "go away and come back over TLS".

Binding to a specific listener is the point. Without `sectionName` the route attaches to every
listener the Gateway has, including the HTTPS one, and an HTTPS listener that redirects to HTTPS
is an infinite loop.
*/}}
{{- define "common.gateway.httpsRedirect" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $redirect := $values.httpsRedirect | default dict -}}
{{- $parents := include "common.gateway.parentRefs" (dict "ctx" $ctx "values" $values) | fromYamlArray -}}
{{- $section := $redirect.sectionName | default "http" -}}
{{- $bound := list -}}
{{- range $parents -}}
{{- $ref := deepCopy . -}}
{{- $_ := set $ref "sectionName" $section -}}
{{- $bound = append $bound $ref -}}
{{- end -}}
apiVersion: {{ include "common.capabilities.gateway.apiVersion" $ctx }}
kind: HTTPRoute
metadata:
  name: {{ include "common.fullname.suffixed" (dict "ctx" $ctx "suffix" "https-redirect") }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- with (include "common.tplvalues.merge" (dict "values" (list $values.annotations $ctx.Values.commonAnnotations) "context" $ctx)) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  parentRefs:
    {{- toYaml $bound | nindent 4 }}
  {{- with (include "common.gateway.hostnames" (dict "ctx" $ctx "values" $values)) }}
  hostnames:
    {{- . | nindent 4 }}
  {{- end }}
  rules:
    - filters:
        - type: RequestRedirect
          requestRedirect:
            scheme: https
            statusCode: {{ $redirect.statusCode | default 301 }}
            {{- with $redirect.port }}
            port: {{ int . }}
            {{- end }}
{{- end -}}

{{/*
The Gateway, for installs with no cluster-wide one to attach to.

Listeners are derived rather than enumerated per hostname, and deliberately carry no `hostname`
of their own: an omitted listener hostname accepts every name, and the *route* is what narrows it
down. Pinning hostnames onto listeners as well would mean two places to edit for one rename, and
a Gateway whose listener no longer matches its route fails by dropping traffic silently. Set
`gateway.listeners` to take that over entirely when a listener really does need its own hostname
or per-listener certificate.

`allowedRoutes` defaults to `Same`. A Gateway this chart owns should not be attachable from
another namespace by default — that is a cross-tenant hole, and the whole point of the field.
*/}}
{{- define "common.gateway.gateway" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $tls := $values.tls | default dict -}}
{{- $allowed := $values.allowedRoutes | default (dict "namespaces" (dict "from" "Same")) -}}
apiVersion: {{ include "common.capabilities.gateway.apiVersion" $ctx }}
kind: Gateway
metadata:
  name: {{ include "common.fullname" $ctx }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- with (include "common.tplvalues.merge" (dict "values" (list $values.annotations $ctx.Values.commonAnnotations) "context" $ctx)) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  gatewayClassName: {{ $values.gatewayClassName | quote }}
  {{- with $values.addresses }}
  addresses:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  {{- with $values.infrastructure }}
  infrastructure:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
  listeners:
    {{- if $values.listeners }}
    {{- include "common.tplvalues.render" (dict "value" $values.listeners "context" $ctx) | nindent 4 }}
    {{- else }}
    - name: http
      protocol: HTTP
      port: {{ $values.httpPort | default 80 | int }}
      allowedRoutes:
        {{- toYaml $allowed | nindent 8 }}
    {{- if $tls.enabled }}
    - name: https
      protocol: HTTPS
      port: {{ $values.httpsPort | default 443 | int }}
      tls:
        mode: {{ $tls.mode | default "Terminate" }}
        {{- with $tls.certificateRefs }}
        certificateRefs:
          {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 10 }}
        {{- end }}
        {{- with $tls.options }}
        options:
          {{- toYaml . | nindent 10 }}
        {{- end }}
      allowedRoutes:
        {{- toYaml $allowed | nindent 8 }}
    {{- end }}
    {{- end }}
{{- end -}}

{{/*
Everything the Gateway API side of a chart needs, in one include.

Usage (templates/httproute.yaml):
  {{ include "common.gateway.routes" . }}

Charts whose routing is not "one hostname, one Service" — `tankovault` publishes a frontend and
an API on separate hostnames and backends — call the partials above directly instead.
*/}}
{{- define "common.gateway.routes" -}}
{{- $values := .Values.gateway | default dict -}}
{{- include "common.gateway.validate" (dict "ctx" . "values" $values) -}}
{{- if $values.enabled -}}
{{- if $values.create }}
{{ include "common.gateway.gateway" (dict "ctx" . "values" $values) }}
---
{{ end }}
{{- include "common.gateway.httpRoute" (dict "ctx" . "values" $values) }}
{{- if ($values.httpsRedirect | default dict).enabled }}
---
{{ include "common.gateway.httpsRedirect" (dict "ctx" . "values" $values) }}
{{- end }}
{{- end -}}
{{- end -}}
