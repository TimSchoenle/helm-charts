{{/*
Fail fast, and report every problem at once.

These are the misconfigurations that either cannot be expressed in the JSON schema (they are
relationships between values, not shapes) or that the schema would only catch after a partial
rollout. Each message names the value to change, because the alternative is an operator
reading a Rust panic or, worse, a stack that starts cleanly and misbehaves later — a wrong
pepper produces an administrator account that can never log in, and a `memory` rate-limit
backend across replicas silently multiplies every limit by the replica count.
*/}}
{{- define "tankovault.validateValues" -}}
{{- $ctx := . -}}
{{- $errors := list -}}
{{- $managed := not $ctx.Values.existingSecret -}}

{{- /*
Chart-managed credentials. A credential that means nothing outside this release is generated when
left empty rather than demanded (see `tankovault.rememberedSecret` and its callers), so the only
thing left to check is a value the operator did supply. With an existingSecret the chart neither
sees nor generates any of them, and the values here are unused, so it says nothing about them.
*/ -}}
{{- if $managed -}}
{{- if and $ctx.Values.auth.jwtSecret (lt (len $ctx.Values.auth.jwtSecret) 32) -}}
{{- $errors = append $errors (printf "auth.jwtSecret must be at least 32 characters (got %d). Shorter values are refused at boot." (len $ctx.Values.auth.jwtSecret)) -}}
{{- end -}}
{{- range $caller, $token := ($ctx.Values.internal.tokens | default dict) -}}
{{- if and $token (lt (len $token) 32) -}}
{{- $errors = append $errors (printf "internal.tokens.%s must be at least 32 characters (got %d). Upstream length-checks it in every profile; `openssl rand -hex 32`." $caller (len $token)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /*
Inter-service authentication.

`internal.token` is gone rather than deprecated, and this is the message an operator who still
sets it gets. The shared token is refused *at boot* upstream, with no dual-accept window, so a
release that carried the value through would not degrade — every service would fail to start at
once. Failing the render instead moves that from a fleet-wide CrashLoopBackOff to a message
naming the two values that replace it.
*/ -}}
{{- if $ctx.Values.internal.token -}}
{{- $errors = append $errors "internal.token no longer exists. Inter-service authentication is per caller: each of `api` and `worker` carries its own credential, and every callee names the callers it accepts. Set `internal.identity=mtls` (the default) and point `internal.tls.issuerRef` at a cert-manager issuer, or set `internal.identity=token` and leave `internal.tokens` empty for the chart to generate one per caller. Upstream refuses the shared token at boot in every profile — there is no dual-accept window — so a release still carrying it does not degrade, it stops. See UPGRADING.md#400." -}}
{{- end -}}

{{- $internal := $ctx.Values.internal -}}
{{- if eq $internal.identity "mtls" -}}
{{- $tls := $internal.tls -}}
{{- if not $tls.issuerRef.name -}}
{{- $errors = append $errors "internal.identity=mtls needs internal.tls.issuerRef.name: it names the cert-manager Issuer or ClusterIssuer that signs each service's client certificate, and there is no default to fall back on. Use `internal.identity=token` on a cluster with no CA." -}}
{{- end -}}
{{- if not (include "common.capabilities.apiVersions.has" (dict "ctx" $ctx "api" "cert-manager.io/v1")) -}}
{{- $errors = append $errors "internal.identity=mtls is set, but the cluster registers no `cert-manager.io/v1` API. Install cert-manager first, or pass `--api-versions cert-manager.io/v1` if you are rendering offline with `helm template`. Rendering regardless would produce Certificates the API server rejects, and pods waiting forever on a Secret nothing writes. `internal.identity=token` needs no CA at all." -}}
{{- end -}}
{{- if not $tls.trustBundle.name -}}
{{- $errors = append $errors "internal.identity=mtls needs internal.tls.trustBundle.name. It is both the trust-manager Bundle's name and the name of the ConfigMap it writes, which every pod mounts as its CA — without it a service can present a certificate but verify nobody's." -}}
{{- end -}}
{{- if $tls.trustBundle.create -}}
{{- if not $tls.trustBundle.sources -}}
{{- $errors = append $errors "internal.tls.trustBundle.create is set but internal.tls.trustBundle.sources is empty, so the Bundle would distribute an empty CA and every peer verification would fail. Name the Secret or ConfigMap holding your CA certificate — for a cert-manager CA ClusterIssuer that is the Secret its `spec.ca.secretName` points at, in cert-manager's namespace." -}}
{{- end -}}
{{- if not (include "common.capabilities.apiVersions.has" (dict "ctx" $ctx "api" "trust.cert-manager.io/v1alpha1")) -}}
{{- $errors = append $errors "internal.tls.trustBundle.create is set, but the cluster registers no `trust.cert-manager.io/v1alpha1` API. Install trust-manager, pass `--api-versions trust.cert-manager.io/v1alpha1` when rendering offline, or set `create=false` and point `internal.tls.trustBundle.name` at a ConfigMap that already carries the CA." -}}
{{- end -}}
{{- end -}}
{{- $setTokens := list -}}
{{- range $caller, $token := ($internal.tokens | default dict) -}}
{{- if $token -}}{{- $setTokens = append $setTokens (printf "internal.tokens.%s" $caller) -}}{{- end -}}
{{- end -}}
{{- if $setTokens -}}
{{- $errors = append $errors (printf "%s %s set while internal.identity=mtls, where callers are identified by their certificate's DNS SAN and no token is read by anything. The chart neither writes nor projects these values, so they would sit in the release meaning nothing. Clear them, or set internal.identity=token." (join " and " $setTokens) (ternary "are" "is" (gt (len $setTokens) 1))) -}}
{{- end -}}
{{- if $ctx.Values.nats.enabled -}}
{{- $errors = append $errors "internal.identity=mtls and nats.enabled=true cannot be combined. Under mtls a service presents its client certificate to the broker as well and requires TLS on that connection, and the bundled NATS serves plaintext only — every service that uses it would fail to connect. Point `externalNats.url` at a TLS-enabled broker, or use `internal.identity=token` for the bundled evaluation stack." -}}
{{- end -}}
{{- end -}}

{{- /* Exactly one database source. */ -}}
{{- $dbSources := list -}}
{{- if $ctx.Values.postgresql.enabled -}}{{- $dbSources = append $dbSources "postgresql.enabled" -}}{{- end -}}
{{- if $ctx.Values.externalDatabase.url -}}{{- $dbSources = append $dbSources "externalDatabase.url" -}}{{- end -}}
{{- if $ctx.Values.externalDatabase.existingSecret -}}{{- $dbSources = append $dbSources "externalDatabase.existingSecret" -}}{{- end -}}
{{- if gt (len $dbSources) 1 -}}
{{- $errors = append $errors (printf "exactly one database source may be set, but %s are all configured." (join ", " $dbSources)) -}}
{{- else if and (eq (len $dbSources) 0) $managed -}}
{{- $errors = append $errors "no database configured. Set `postgresql.enabled=true` for the bundled single-instance PostgreSQL, or point `externalDatabase.url` / `externalDatabase.existingSecret` at your own." -}}
{{- end -}}

{{- /*
The AniList application credentials are issued by a third party and cannot be invented here.
The token-encryption key can — it is purely local — so an unset one is generated rather than
rejected; see `tankovault.anilistTokenEncryptionKey`.
*/ -}}
{{- if and $ctx.Values.services.sync.enabled $managed -}}
{{- $anilist := $ctx.Values.anilist -}}
{{- $missing := list -}}
{{- if not $anilist.clientId -}}{{- $missing = append $missing "anilist.clientId" -}}{{- end -}}
{{- if not $anilist.clientSecret -}}{{- $missing = append $missing "anilist.clientSecret" -}}{{- end -}}
{{- if $missing -}}
{{- $errors = append $errors (printf "services.sync.enabled requires the AniList application credentials; missing: %s. Register an application at https://anilist.co/settings/developer, or set services.sync.enabled=false." (join ", " $missing)) -}}
{{- end -}}
{{- end -}}

{{- /* The redirect URI has to resolve to something, and it must be the frontend's. */ -}}
{{- if and $ctx.Values.services.sync.enabled (not $ctx.Values.anilist.redirectUri) (not (include "tankovault.externalUrl" $ctx)) -}}
{{- $errors = append $errors "services.sync.enabled needs a callback URL: either enable the ingress (so it can be derived) or set `anilist.redirectUri`. It must point at the frontend's /account/anilist-callback, not at the API." -}}
{{- end -}}

{{- /*
An unset seed password is generated and printed by NOTES.txt; with an existingSecret the chart
cannot generate one, so the Secret must already carry `seed_admin_password`.
*/ -}}

{{- /* The challenge solver is useless without a backend to delegate to. */ -}}
{{- if and $ctx.Values.services.challengeSolver.enabled (not (include "tankovault.trawlUrl" $ctx)) -}}
{{- $errors = append $errors "services.challengeSolver.enabled requires a solver backend: set `trawl.enabled=true` or point `externalTrawl.url` at one." -}}
{{- end -}}

{{- /* Publishing a privileged service is a security defect, not a preference. */ -}}
{{- if not $ctx.Values.allowUnsafeExposure -}}
{{- range $service := list "controlPlane" "sync" "render" "challengeSolver" -}}
{{- $values := index $ctx.Values.services $service -}}
{{- if and $values.enabled $values.service (ne $values.service.type "ClusterIP") -}}
{{- $errors = append $errors (printf "services.%s.service.type is %q. control-plane, sync, render and challenge-solver expose privileged contracts and upstream publishes none of them; reaching them directly bypasses the frontend proxy and the rate limiter. Set it back to ClusterIP, or set allowUnsafeExposure=true if you genuinely mean it." $service $values.service.type) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /*
A worker with nothing to talk to will start and then do nothing useful. `perServiceSecret` counts
as a source: it delivers one URL per service out of a Secret this chart never reads, which is the
whole point of it, so there is nothing here to check beyond its presence.
*/ -}}
{{- $nats := or (include "tankovault.natsUrl" $ctx) $ctx.Values.externalNats.perServiceSecret -}}
{{- if and $ctx.Values.services.worker.enabled (not $nats) -}}
{{- $errors = append $errors "services.worker.enabled requires NATS JetStream: set `nats.enabled=true`, point `externalNats.url` at one, or supply per-service URLs through `externalNats.perServiceSecret`. The worker consumes its scan tasks from a JetStream work queue." -}}
{{- end -}}
{{- if and $ctx.Values.services.controlPlane.enabled (not $nats) -}}
{{- $errors = append $errors "services.controlPlane.enabled requires NATS JetStream: set `nats.enabled=true`, point `externalNats.url` at one, or supply per-service URLs through `externalNats.perServiceSecret`." -}}
{{- end -}}

{{- /*
Legal documents. The service refuses to boot on a document that names its body twice or not at
all, so these are caught here where the message can name the slug and the keys rather than at
container start. The API is the only reader; publishing documents without it is a no-op that
would otherwise look like a working configuration.
*/ -}}
{{- range $slug, $doc := $ctx.Values.legal.documents -}}
{{- $doc = $doc | default dict -}}
{{- $ways := list -}}
{{- if $doc.content -}}{{- $ways = append $ways "content" -}}{{- end -}}
{{- if $doc.sources -}}{{- $ways = append $ways "sources" -}}{{- end -}}
{{- if $doc.url -}}{{- $ways = append $ways "url" -}}{{- end -}}
{{- if gt (len $ways) 1 -}}
{{- $errors = append $errors (printf "legal.documents.%s sets %s, but a document carries its body exactly once. `content` and `sources` are two ways to point at a file and `url` means the document lives elsewhere entirely; the service refuses to boot on a document that sets more than one." $slug (join " and " $ways)) -}}
{{- else if eq (len $ways) 0 -}}
{{- $errors = append $errors (printf "legal.documents.%s publishes no body. Set `content` (the text itself, mounted by this chart), `sources` (paths you mount yourself), or `url` (a document hosted elsewhere)." $slug) -}}
{{- end -}}
{{- if and $doc.url (not (regexMatch "^https?://" $doc.url)) -}}
{{- $errors = append $errors (printf "legal.documents.%s.url is %q. Only absolute http(s) URLs are accepted." $slug $doc.url) -}}
{{- end -}}
{{- end -}}
{{- if and $ctx.Values.legal.documents (not $ctx.Values.services.api.enabled) -}}
{{- $errors = append $errors "legal.documents are configured but services.api.enabled=false. The API is the only service that reads them and the only one that serves them, so nothing would publish these documents. Enable the api, or remove the documents." -}}
{{- end -}}

{{- /*
The Cloudflare CSP concessions. Only the frontend assembles a Content-Security-Policy, so with
that service disabled these flags reach no reader at all — and an operator who set them believes
their edge-injected scripts are admitted when nothing is serving the header.
*/ -}}
{{- if not $ctx.Values.services.frontend.enabled -}}
{{- $flags := list -}}
{{- if $ctx.Values.cloudflare.scriptNonce -}}{{- $flags = append $flags "cloudflare.scriptNonce" -}}{{- end -}}
{{- if $ctx.Values.cloudflare.turnstile -}}{{- $flags = append $flags "cloudflare.turnstile" -}}{{- end -}}
{{- if $flags -}}
{{- $plural := gt (len $flags) 1 -}}
{{- $errors = append $errors (printf "%s %s set while services.frontend.enabled=false. The frontend is the only service that assembles a Content-Security-Policy, so nothing would apply what you set here. Enable the frontend, or turn %s off." (join " and " $flags) (ternary "are" "is" $plural) (ternary "them" "it" $plural)) -}}
{{- end -}}
{{- end -}}

{{- /*
Grafana dashboards. The rules are the library's, because the value contract and the CRD it
depends on are — messages are collected rather than raised there so they land in this one report
alongside everything else. Prefixed here, since the library cannot know which key path the
consuming chart exposed them under.
*/ -}}
{{- $dashboard := $ctx.Values.metrics.dashboard -}}
{{- if and $dashboard.enabled (not $ctx.Values.metrics.enabled) -}}
{{- $errors = append $errors "metrics.dashboard.enabled has no effect while metrics.enabled=false: nothing scrapes the services, so the dashboard would render against an empty datasource. Enable metrics, or turn the dashboard off." -}}
{{- end -}}
{{- $dashboardErrors := include "common.grafana.dashboard.errors" (dict "ctx" $ctx "values" $dashboard) -}}
{{- if $dashboardErrors -}}
{{- range splitList "\n" $dashboardErrors -}}
{{- $errors = append $errors (printf "under `metrics.dashboard`, %s" .) -}}
{{- end -}}
{{- end -}}

{{- /*
Prometheus Operator objects. Same contract as the dashboards: a missing CRD is refused here
rather than silently dropped, so an operator who forgot to install the Prometheus Operator finds
out now instead of discovering an unmonitored release weeks later.
*/ -}}
{{- if and $ctx.Values.metrics.enabled $ctx.Values.metrics.serviceMonitor.enabled -}}
{{- with (include "common.prometheus.operatorErrors" (dict "ctx" $ctx "feature" "metrics.serviceMonitor.enabled")) -}}
{{- $errors = append $errors . -}}
{{- end -}}
{{- end -}}
{{- if $ctx.Values.metrics.enabled -}}
{{- $ruleErrors := include "common.prometheus.rules.errors" (dict
      "ctx" $ctx
      "values" $ctx.Values.metrics.prometheusRule
      "feature" "metrics.prometheusRule.enabled"
      "scopePlaceholder" (include "tankovault.rules.scopePlaceholder" $ctx)
      "scopeMatcher" (include "tankovault.rules.scopeMatcher" $ctx)) -}}
{{- if $ruleErrors -}}
{{- range splitList "\n" $ruleErrors -}}
{{- $errors = append $errors . -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and $ctx.Values.metrics.prometheusRule.enabled (not $ctx.Values.metrics.enabled) -}}
{{- $errors = append $errors "metrics.prometheusRule.enabled has no effect while metrics.enabled=false: no service exposes a scrape port, so every rule would evaluate against no data and the `up`-based alerts would fire immediately. Enable metrics, or turn the rules off." -}}
{{- end -}}
{{- if and $ctx.Values.metrics.serviceMonitor.enabled (not $ctx.Values.metrics.enabled) -}}
{{- $errors = append $errors "metrics.serviceMonitor.enabled has no effect while metrics.enabled=false: the Services carry no metrics port for the ServiceMonitor to select. Enable metrics, or turn the ServiceMonitors off." -}}
{{- end -}}

{{- /*
The NATS exporter. It reads NATS' *monitoring* listener, which is a different port and a
different protocol from the client URL the services use, so an external NATS that publishes only
4222 leaves it with nothing to scrape — a state that shows up as a pod retrying a connection
forever rather than as an error, which is why it is refused here.
*/ -}}
{{- if $ctx.Values.metrics.natsExporter.enabled -}}
{{- if not $ctx.Values.metrics.enabled -}}
{{- $errors = append $errors "metrics.natsExporter.enabled has no effect while metrics.enabled=false: nothing would scrape the exporter. Enable metrics, or turn the exporter off." -}}
{{- end -}}
{{- if not (include "tankovault.natsMonitoringUrl" $ctx) -}}
{{- $errors = append $errors "metrics.natsExporter.enabled needs a NATS monitoring endpoint, and none could be resolved. The exporter reads NATS' HTTP monitoring listener (`-m 8222`), not the client port, so `externalNats.url` does not supply one: set `metrics.natsExporter.url` to something like `http://nats.example.com:8222`, or enable the bundled NATS." -}}
{{- end -}}
{{- end -}}

{{- /*
Gateway API exposure. Checked here rather than in `templates/httproute.yaml` for the reason every
other check is here: one message naming everything wrong beats one `helm template` run per
mistake. The CRD guard is the library's, so this chart and the four that use
`common.gateway.routes` refuse an absent Gateway API in exactly the same words.

`gateway.*` and `ingress.*` are independent switches and both may be on — that is the migration
window, not a mistake — so nothing here objects to the pair.
*/ -}}
{{- $gateway := $ctx.Values.gateway -}}
{{- if or $gateway.enabled $gateway.api.enabled -}}
{{- with (include "common.gateway.crdErrors" (dict "ctx" $ctx "feature" "gateway.enabled")) -}}
{{- $errors = append $errors . -}}
{{- end -}}
{{- if and (not $gateway.parentRefs) (not $gateway.create) -}}
{{- $errors = append $errors "gateway is enabled but gateway.parentRefs is empty and gateway.create is false, so the routes would name no Gateway to attach to. An HTTPRoute without a parent is accepted by the API server and then does nothing at all — no listener ever programs it, and the failure is invisible until somebody tries the hostname. Either name the Gateway your cluster operator runs, or set gateway.create to have this chart render one." -}}
{{- end -}}
{{- range $i, $ref := $gateway.parentRefs | default list -}}
{{- if not $ref.name -}}
{{- $errors = append $errors (printf "gateway.parentRefs[%d] has no `name`. A parent reference is resolved by name; there is no default." $i) -}}
{{- end -}}
{{- end -}}
{{- if and $gateway.enabled (not $gateway.host) -}}
{{- $errors = append $errors "gateway.enabled is set but gateway.host is empty. An HTTPRoute with no hostnames matches every hostname its listener accepts, which on a shared Gateway means this release silently takes over traffic meant for other applications." -}}
{{- end -}}
{{- if and $gateway.api.enabled (not $gateway.api.host) -}}
{{- $errors = append $errors "gateway.api.enabled is set but gateway.api.host is empty. The API route needs its own hostname — that is the entire point of publishing it separately." -}}
{{- end -}}
{{- if and $gateway.api.enabled $gateway.enabled (eq $gateway.api.host $gateway.host) -}}
{{- $errors = append $errors "gateway.api.host and gateway.host name the same hostname. Two routes claiming one hostname on one Gateway is resolved by Gateway API's own precedence rules, not by intent, and the loser is silently ignored. The frontend already proxies /v1/* — publish the API separately only when it needs an origin of its own." -}}
{{- end -}}
{{- if and $gateway.create (not $gateway.gatewayClassName) -}}
{{- $errors = append $errors "gateway.create is set but gateway.gatewayClassName is empty. The class is what selects the implementation that programs the Gateway (`cilium`, `istio`, `envoy-gateway`, ...); a Gateway without one is never reconciled." -}}
{{- end -}}
{{- if and $gateway.create $gateway.tls.enabled (not $gateway.tls.certificateRefs) (not $gateway.listeners) -}}
{{- $errors = append $errors "gateway.create and gateway.tls.enabled are set but gateway.tls.certificateRefs is empty. A `Terminate` listener needs a certificate to terminate with; unlike an Ingress there is no convention by which one is looked up from the hostname." -}}
{{- end -}}
{{- if and $gateway.httpsRedirect.enabled $gateway.create (not $gateway.tls.enabled) (not $gateway.listeners) -}}
{{- $errors = append $errors "gateway.httpsRedirect.enabled is set but the Gateway this chart creates terminates no TLS, so the redirect would send every client to a port that refuses the connection." -}}
{{- end -}}
{{- /*
The scheme of the derived external URL comes from `gateway.tls.enabled`. The application defaults
`auth.cookie_secure` to true, and a cookie marked Secure is never sent back over plain HTTP — so
an http:// origin produces a login that appears to succeed and lands straight back on the sign-in
page, with nothing in any log to say why. `localhost` is the exception browsers make, and the one
case where a plaintext origin is genuinely fine.

Read out of the free-form `config` tree because that is the only place this setting can be
changed from: it has no first-class value.
*/ -}}
{{- $cookieSecure := dig "auth" "cookie_secure" true ($ctx.Values.config | default dict) -}}
{{- if and $gateway.enabled $gateway.host (not $gateway.tls.enabled) (not $gateway.url) $cookieSecure (ne $gateway.host "localhost") -}}
{{- $errors = append $errors "gateway.enabled is set without gateway.tls.enabled and without gateway.url, so the derived origin is http:// — and the application's auth.cookie_secure defaults to true, meaning the session cookie is never sent back and every login lands straight back on the sign-in page. Set gateway.tls.enabled when the Gateway terminates TLS for this hostname (it does not have to be a Gateway this chart creates), set gateway.url if TLS terminates on a proxy in front of it, or set config.auth.cookie_secure=false if you really are serving plain HTTP." -}}
{{- end -}}
{{- end -}}

{{- if $errors -}}
{{- fail (printf "\n\nTankoVault chart configuration is invalid:\n\n  - %s\n" (join "\n  - " $errors)) -}}
{{- end -}}
{{- end -}}
