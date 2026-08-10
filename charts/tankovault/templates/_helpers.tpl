{{/*
Static, chart-owned facts about each TankoVault service.

Returned as YAML for `fromYaml`, because a Go template can only ever return a string —
this is why nothing in this chart "returns a dict" directly.

  slug        the Kubernetes name segment AND the value of `telemetry.service_name`.
              These are deliberately the same string: `docs/OBSERVABILITY.md` makes the
              Prometheus `job` label the sole identifier of which service emitted a metric,
              so the workload name and the telemetry name diverging would make every alert
              point at the wrong thing.
  port        the service's own `bind_addr` port, from `docs/CONFIGURATION.md` §"Per-Service".
              Note `worker` is 8085, not 8084 — 8084 is `render`.
  secretKeys  the credential keys this service actually reads. Each pod projects only these,
              so a `worker` compromise never yields `auth__jwt_secret`.
  internal*   how the service takes part in inter-service authentication:
              `internalIdentity` whether it reads the `internal` config block at all — the
              `frontend` is a Node process that proxies to the API and does not;
              `internalCaller` its own caller name, set only on the services that make
              privileged calls, and `internalPeers` the caller names it accepts on its own
              privileged routes. The two halves are the whole call graph, and the callee's
              compiled-in route table is keyed by exactly these names.
  needs*      dependency wiring, used to derive config and NetworkPolicy egress.
  scalable    whether replicas > 1 is known-safe (`deploy/README.md` + docs/OPERATIONS.md).

`ingressFrom` is the same graph as `internalPeers` plus the one edge that is not an internal
call: the `frontend` reverse-proxies `/v1/*` to the `api` as an ordinary public request, with
no caller identity of any kind. Everything else that may open a connection may also
authenticate on it.
*/}}
{{- define "tankovault.serviceSpecs" -}}
api:
  slug: api
  port: 8080
  scalable: true
  ingressFrom: [frontend]
  egressServices: [controlPlane, sync, worker]
  egressInternet: true
  needsDatabase: true
  needsRedis: true
  needsNats: true
  internalIdentity: true
  internalCaller: api
  internalPeers: []
  secretKeys:
    - auth__jwt_secret
    - auth__password_pepper
    - auth__mfa_encryption_key
    - database__url
    - redis__url
    - email__username
    - email__password
frontend:
  slug: frontend
  port: 3000
  scalable: true
  ingressFrom: []
  egressServices: [api]
  egressInternet: false
  needsDatabase: false
  needsRedis: false
  needsNats: false
  internalIdentity: false
  internalCaller: ""
  internalPeers: []
  secretKeys: []
controlPlane:
  slug: control-plane
  port: 8081
  scalable: true
  ingressFrom: [api]
  egressServices: []
  egressInternet: false
  needsDatabase: true
  needsRedis: true
  needsNats: true
  internalIdentity: true
  internalCaller: ""
  internalPeers: [api]
  secretKeys:
    - database__url
    - redis__url
worker:
  slug: worker
  port: 8085
  scalable: true
  ingressFrom: [api]
  egressServices: [challengeSolver, render]
  egressInternet: true
  needsDatabase: true
  needsRedis: false
  needsNats: true
  internalIdentity: true
  internalCaller: worker
  internalPeers: [api]
  secretKeys:
    - database__url
notifier:
  slug: notifier
  port: 8082
  scalable: false
  ingressFrom: []
  egressServices: []
  egressInternet: true
  needsDatabase: true
  needsRedis: false
  needsNats: true
  internalIdentity: true
  internalCaller: ""
  internalPeers: []
  secretKeys:
    - database__url
    - email__username
    - email__password
    - channels__discord_webhook_url
    - channels__webhook_url
sync:
  slug: sync
  port: 8083
  scalable: false
  ingressFrom: [api]
  egressServices: []
  egressInternet: true
  needsDatabase: true
  needsRedis: false
  needsNats: false
  internalIdentity: true
  internalCaller: ""
  internalPeers: [api]
  secretKeys:
    - database__url
    - anilist__client_id
    - anilist__client_secret
    - anilist__token_encryption_key
challengeSolver:
  slug: challenge-solver
  port: 8090
  scalable: true
  ingressFrom: [worker]
  egressServices: []
  egressInternet: false
  needsDatabase: false
  needsRedis: false
  needsNats: false
  internalIdentity: true
  internalCaller: ""
  internalPeers: [worker]
  secretKeys: []
render:
  slug: render
  port: 8084
  scalable: true
  ingressFrom: [worker]
  egressServices: []
  egressInternet: true
  needsDatabase: false
  needsRedis: false
  needsNats: false
  internalIdentity: true
  internalCaller: ""
  internalPeers: [worker]
  secretKeys: []
{{- end -}}

{{/*
The spec for one service, or a hard failure naming the bad key.

Usage: {{- $spec := include "tankovault.spec" "api" | fromYaml }}
*/}}
{{- define "tankovault.spec" -}}
{{- $specs := include "tankovault.serviceSpecs" . | fromYaml -}}
{{- if not (hasKey $specs .) -}}
{{- fail (printf "tankovault: unknown service %q (known: %s)" . (keys $specs | sortAlpha | join ", ")) -}}
{{- end -}}
{{- index $specs . | toYaml -}}
{{- end -}}

{{/*
The `app.kubernetes.io/name` of one service, e.g. `tankovault-control-plane`.

Every workload needs its own selector labels; the library's `common.selectorLabels` derives
them from `common.name`, so a nine-workload chart has to give each service its own
`nameOverride` or all nine Deployments would fight over the same pods.
*/}}
{{- define "tankovault.name" -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- printf "%s-%s" (include "common.name" .ctx) $spec.slug | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The resource name of one service, e.g. `RELEASE-NAME-tankovault-control-plane`.
*/}}
{{- define "tankovault.fullname" -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" $spec.slug) -}}
{{- end -}}

{{/*
The in-cluster base URL of one service, e.g. `http://RELEASE-NAME-tankovault-api:8080`.

Always plaintext, and used for exactly one edge: the frontend's `api_upstream`. That hop is an
ordinary public request being reverse-proxied, not an internal call — the frontend holds no
caller identity in either mode — so it is the one peer URL that does not follow
`internal.identity`. Everything that does goes through `tankovault.internalUrl`.
*/}}
{{- define "tankovault.url" -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- printf "http://%s:%v" (include "tankovault.fullname" .) $spec.port -}}
{{- end -}}

{{/*
The in-cluster base URL of one service as a *peer* — the scheme follows `internal.identity`.

Under `mtls` the callee requires a verified client certificate, and a plaintext upstream is the
one failure mode that looks like success from the client's side: the connection is accepted, the
request is sent, no certificate is offered and nothing is encrypted, while the peer's own config
still says it requires both. Upstream refuses to boot on an `http://` peer URL in that mode for
that reason, so the scheme is derived here rather than left to the operator to restate.

Args: ctx (root), service.
*/}}
{{- define "tankovault.internalUrl" -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- $scheme := ternary "https" "http" (eq .ctx.Values.internal.identity "mtls") -}}
{{- printf "%s://%s:%v" $scheme (include "tankovault.fullname" .) $spec.port -}}
{{- end -}}

{{/*
The DNS name that identifies one service to its peers, e.g.
`RELEASE-NAME-tankovault-api.default.svc`.

One name per service, stable across upgrades and independent of pod identity, because it is both
what the certificate carries and what every callee is configured to expect. The verifier checks
the configured name against *every* DNS SAN on the presented certificate rather than only the
first, so a certificate may carry more names than this one and their order does not matter — but
the two sides must agree on this one exactly, which is why one helper answers for both.

`internal.tls.sans.<service>` overrides it for an external PKI that issues names of its own. Under
`source: certManager` the override is also requested on the Certificate, so the two halves cannot
drift; under `existingSecrets` it declares what the operator's certificate already carries.

Args: ctx (root), service.
*/}}
{{- define "tankovault.internalSan" -}}
{{- $override := index (.ctx.Values.internal.tls.sans | default dict) .service -}}
{{- if $override -}}
{{- $override -}}
{{- else -}}
{{- printf "%s.%s.svc" (include "tankovault.fullname" .) (include "common.namespace" .ctx) -}}
{{- end -}}
{{- end -}}

{{/*
The name of the cert-manager Certificate for one service, and of the Secret it writes.

Args: ctx (root), service.
*/}}
{{- define "tankovault.internalCertName" -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" (printf "%s-internal-tls" $spec.slug)) -}}
{{- end -}}

{{/*
The Secret one service mounts its keypair from: the Certificate this chart creates, or the one
the operator supplies.

The single point where the two certificate sources differ, so everything downstream — the volume,
the config paths, and the CA when it is read from the same Secret — is written once.

Args: ctx (root), service.
*/}}
{{- define "tankovault.internalTlsSecret" -}}
{{- $tls := .ctx.Values.internal.tls -}}
{{- if eq $tls.source "existingSecrets" -}}
{{- index ($tls.existingSecrets | default dict) .service -}}
{{- else -}}
{{- include "tankovault.internalCertName" . -}}
{{- end -}}
{{- end -}}

{{/*
The enabled callers, as a YAML map of caller name to service key.

A caller name is the identity a service presents (`internal.caller.name`) and the key a callee's
peer entry is written under (`internal.peers.<caller>`), so the two can never be spelled
differently. Only enabled services appear: a peer entry for a service that was never deployed
would be a credential nothing can present and, under `token`, a generated secret nothing reads.

Today this is `api` and `worker`, and both happen to equal their service key. The indirection is
kept because the peer name is upstream's contract — a compiled-in route table is keyed by it —
while the service key is this chart's, and nothing guarantees they stay identical.

Usage: {{- $callers := include "tankovault.internalCallers" $ | fromYaml }}
*/}}
{{- define "tankovault.internalCallers" -}}
{{- $ctx := . -}}
{{- $specs := include "tankovault.serviceSpecs" $ctx | fromYaml -}}
{{- $callers := dict -}}
{{- range $service, $spec := $specs -}}
{{- if and $spec.internalCaller (index $ctx.Values.services $service).enabled -}}
{{- $_ := set $callers $spec.internalCaller $service -}}
{{- end -}}
{{- end -}}
{{- toYaml $callers -}}
{{- end -}}

{{/*
The peers one service accepts, narrowed to the callers that are actually deployed.

Returned as a YAML map of caller name to service key, in the same shape as
`tankovault.internalCallers`, so a consumer can resolve each peer's SAN without a second lookup.

Args: ctx (root), service.
*/}}
{{- define "tankovault.internalPeers" -}}
{{- $ctx := .ctx -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- $callers := include "tankovault.internalCallers" $ctx | fromYaml -}}
{{- $peers := dict -}}
{{- range $peer := $spec.internalPeers | default list -}}
{{- if hasKey $callers $peer -}}
{{- $_ := set $peers $peer (index $callers $peer) -}}
{{- end -}}
{{- end -}}
{{- toYaml $peers -}}
{{- end -}}

{{/*
Whether a service is enabled. Emits "true" or "".
*/}}
{{- define "tankovault.enabled" -}}
{{- $svc := index .ctx.Values.services .service | default dict -}}
{{- if $svc.enabled -}}true{{- end -}}
{{- end -}}

{{/*
The per-service values tree, as YAML.

This is the heart of the chart. `charts/common` provides fragments only, and every one of
them (`common.container`, `common.probes`, `common.resources`, `common.podSpec.common`, ...)
reads the ROOT `.Values`. Rather than fork nine copies of those helpers, each builder
rebuilds a *scoped render context* whose `.Values` is this tree, and then calls the library
completely unchanged:

  {{- $values := include "tankovault.serviceValues" (dict "ctx" $ "service" "api") | fromYaml }}
  {{- $ctx := dict "Values" $values "Chart" $.Chart "Release" $.Release
                   "Capabilities" $.Capabilities "Template" $.Template "Files" $.Files }}

`$.Files` and `$.Capabilities` are passed by reference on purpose — `deepCopy` on the root
context would flatten them into plain maps and break `.Files.Get` and `.Capabilities.APIVersions`.

Precedence, lowest to highest: chart-global keys, `.Values.defaults`, `.Values.services.<key>`.
`nameOverride`/`fullnameOverride`/`component` are then forced, so a user cannot accidentally
collapse two services onto one name.
*/}}
{{- define "tankovault.serviceValues" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
{{- $global := dict
      "image" (deepCopy ($ctx.Values.image | default dict))
      "imagePullSecrets" ($ctx.Values.imagePullSecrets | default list)
      "commonLabels" (deepCopy ($ctx.Values.commonLabels | default dict))
      "commonAnnotations" (deepCopy ($ctx.Values.commonAnnotations | default dict))
      "serviceAccount" (deepCopy ($ctx.Values.serviceAccount | default dict))
      "kubeVersionOverride" ($ctx.Values.kubeVersionOverride | default "")
      "namespaceOverride" ($ctx.Values.namespaceOverride | default "")
      "partOf" (include "common.name" $ctx)
-}}
{{- $defaults := deepCopy ($ctx.Values.defaults | default dict) -}}
{{- $overrides := deepCopy ((index $ctx.Values.services $service) | default dict) -}}
{{- $values := mergeOverwrite $global $defaults $overrides -}}
{{- $spec := include "tankovault.spec" $service | fromYaml -}}
{{- $_ := set $values "nameOverride" (include "tankovault.name" (dict "ctx" $ctx "service" $service)) -}}
{{- $_ := set $values "fullnameOverride" (include "tankovault.fullname" (dict "ctx" $ctx "service" $service)) -}}
{{- $_ := set $values "component" $spec.slug -}}
{{- /*
  Pin the ServiceAccount name. `common.podSpec.common` resolves it through
  `common.serviceAccountName`, which falls back to `common.fullname` — and inside a scoped
  context that is the *service* name, so every pod would name a ServiceAccount that does not
  exist. One account is shared by the whole release, so resolve it against the root context.
*/ -}}
{{- $_ := set $values "serviceAccount" (dict
      "create" $ctx.Values.serviceAccount.create
      "name" (include "tankovault.serviceAccountName" $ctx)) -}}
{{- toYaml $values -}}
{{- end -}}

{{/*
The ServiceAccount name, resolved once at chart level rather than per service: nothing in
TankoVault talks to the Kubernetes API, so one non-mounting account for the whole release is
both sufficient and the smaller blast radius.
*/}}
{{- define "tankovault.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "common.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
The externally reachable base URL of the frontend.

`anilist.redirect_uri`, `email.base_url` and `auth.webauthn_origin` all have to agree with the
origin a browser actually uses, or OAuth round-trips, emailed links and passkeys break in ways
that only show up at runtime. Derive them from whichever mechanism publishes the chart rather
than asking the operator to restate the same hostname four times.

Both mechanisms are checked, because both may be enabled: `ingress` and `gateway` are independent
switches so that a cluster can migrate between an Ingress controller and a Gateway implementation
without an outage. When both are on the Gateway wins, which is the direction a migration runs —
turning `gateway` on is the act of moving to it, and the derived origin should follow immediately
rather than only once the Ingress is finally deleted.

`gateway.tls.enabled` is what supplies the scheme, and it is meaningful even when the Gateway is
somebody else's: it says this hostname is served over HTTPS, not that this chart terminates it.
*/}}
{{- define "tankovault.externalUrl" -}}
{{- if .Values.gateway.url -}}
{{- .Values.gateway.url | trimSuffix "/" -}}
{{- else if .Values.ingress.url -}}
{{- .Values.ingress.url | trimSuffix "/" -}}
{{- else if and .Values.gateway.enabled .Values.gateway.host -}}
{{- $scheme := ternary "https" "http" .Values.gateway.tls.enabled -}}
{{- printf "%s://%s" $scheme .Values.gateway.host -}}
{{- else if and .Values.ingress.enabled .Values.ingress.host -}}
{{- $scheme := ternary "https" "http" .Values.ingress.tls.enabled -}}
{{- printf "%s://%s" $scheme .Values.ingress.host -}}
{{- end -}}
{{- end -}}

