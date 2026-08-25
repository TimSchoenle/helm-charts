# portfolio

![Version: 5.1.7](https://img.shields.io/badge/Version-5.1.7-informational?style=flat-square) ![AppVersion: v2.9.0](https://img.shields.io/badge/AppVersion-v2.9.0-informational?style=flat-square)

Personal portfolio built with Rust (Yew frontend, Axum server).

A single self-contained Rust binary serving pre-built assets. There is no database, no cache
and no runtime credential — the GitHub data on the site is fetched at build time, so no token
is needed here. Installing it without any values produces a working release; the only thing
most installs add is an Ingress.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- An ingress controller, if `ingress.enabled=true`
- The Gateway API CRDs and a `Gateway` to attach to, if `gateway.enabled=true`
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/portfolio \
  --namespace [NAMESPACE] --create-namespace
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/portfolio -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`. Nothing outlives the release.

## Publishing it

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  hosts:
    - host: portfolio.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: portfolio-tls
      hosts:
        - portfolio.example.com
```

## Sizing

`resources` ships with real defaults — 25m/32Mi requested, 250m/128Mi limited — rather than the
empty block most charts use, because the server's footprint is known and small. Raise the
limits only if you are actually seeing throttling or OOM kills; a static-asset server that is
slow is usually waiting on the network, not on CPU.

For availability, `replicaCount` plus a `podAntiAffinity` (or the `topologySpreadConstraints`
value) is the whole story — the pods share nothing, so any number of them can run.

## Health checks

All three probes are on by default and hit `/api/health`. The startup probe is the one that
covers a slow boot, and it is why the liveness probe can be aggressive without risking a kill
loop during startup.

Each probe takes one handler (`httpGet`, `tcpSocket`, `exec` or `grpc`) and the usual timings.
Anything left unset is omitted from the manifest rather than written out, so Kubernetes' own
defaults apply:

```yaml
startupProbe:
  failureThreshold: 12   # 12 x periodSeconds before the container is considered failed
livenessProbe:
  periodSeconds: 10
```

> [!NOTE]
> `successThreshold` is accepted only on the readiness probe. Kubernetes rejects any value
> other than `1` on startup and liveness probes, so the chart omits it there.

## Configuration

The application reads its settings through a layered, file-first loader. Everything in the
`PORTFOLIO_` namespace — `assets.dist_dir`, `isr.*` — is rendered into one `config.toml`,
mounted as a ConfigMap and pointed at by `PORTFOLIO_CONFIG`. The loader **fails the boot on a
key supplied by both the environment and a file** rather than resolving it by precedence, so
keeping the chart's output in one place makes that collision impossible.

`PORT`, `IP` and `RUST_LOG` are the exception, and stay environment variables: they belong to
the Dioxus toolchain, which reads them itself. They are `server.port`, `server.host` and
`logLevel` in this chart's values, and cannot be supplied through `config`.

One more variable is set on purpose. The published image bakes
`PORTFOLIO_ISR__CACHE_DIR=/tmp/isr`, and the environment layer outranks the TOML one — so the
chart restates the *effective* cache directory as an environment variable alongside the file.
Without that, moving the cache through `isr.cacheDir` would write a value the image silently
overrode.

`config` takes the raw TOML tree for anything the first-class values do not cover, merged over
the derived one, and `configExtraToml` is appended verbatim for what the renderer cannot
express.

The server does not reload its configuration — only the loader half of the library is used — so
the chart keeps the conventional `checksum/config` pod annotation: a configuration change rolls
the Deployment, which is the only way it takes effect.

### What checks that the configuration is one the image accepts

Nothing about the paragraphs above is enforced by Helm. `values.schema.json` describes this
chart's *values*, not the application's settings, and a ConfigMap holding a `config.toml` full of
keys the server stopped reading is a valid ConfigMap to every other gate in this repository —
`serde` ignores an unknown key, so the pod starts, reports healthy, and runs on a compiled
default nobody chose.

`config-contract.yaml` closes that. It names the rendered document, the images that read it and
the containers that mount it; `contracts/server.json` vendors the contract the image publishes,
listing every key it actually reads with its type, its environment spelling and its secret-file
spelling. `just check-config` validates the rendered `config.toml` against it, classifies every
`PORTFOLIO_` variable the chart emits, and refuses a secret file whose name spells no key. A
renamed key fails the pull request that renames it, next to the diff that removed it.

The pod sets `enableServiceLinks: false` for the same reason. Kubernetes injects seven variables
per Service in the namespace named after the release, so a release called `portfolio` would land
them inside the `PORTFOLIO_` namespace the loader owns — where the environment layer outranks the
mounted file and one of them could supply a key `config.toml` had already set. The kubelet injects
them at admission, so no gate can see them; the switch is what stops them existing.

## Content-Security-Policy

The header the server sends is derived, not configured: each document's inline `<script>` tags
are hashed out of the response body itself, so the policy cannot drift from what is sent. The
`csp` values decide only what that policy has to make room for.

```yaml
csp:
  hashInlineScripts: true
  cloudflare:
    scriptNonce: true    # for the script Cloudflare's bot products inject at the edge
    turnstile: false     # admits challenges.cloudflare.com in script-src *and* frame-src
    webAnalytics: false  # admits the beacon and the endpoint it reports to
```

`scriptNonce` is on by default, because a script injected at the edge lands after the server
hashed what it rendered — no hash can cover it, and without the nonce `script-src` refuses it
and the bot detection silently never runs. The server discharges half of what that costs by
serving every document `Cache-Control: no-cache`. The other half is yours:

> [!IMPORTANT]
> No Cloudflare Cache Rule may cache the shell. A "Cache Everything" rule overrides the origin's
> `Cache-Control` and pins one nonce across every reader for the lifetime of the cache entry,
> which is exactly what the nonce exists to prevent. Nothing inside the pod can detect it.

**If a page renders blank**, the policy is refusing a script it should have admitted.
`hashInlineScripts: false` restores `'unsafe-inline'` — but only together with
`cloudflare.scriptNonce: false`, since a browser ignores `'unsafe-inline'` as soon as the policy
carries a nonce. The chart rejects that pair at render time rather than letting the pod
CrashLoopBackOff on its own boot check.

## Incremental static regeneration

ISR is on by default and caches into `/tmp/isr`, inside the writable `emptyDir` the chart
already mounts for the read-only root filesystem. The server creates the directory itself and
falls back to rendering every request fresh if it turns out not to be writable.

```yaml
isr:
  cacheDir: /tmp/isr   # empty disables ISR entirely
  ttlSecs: 0           # 0 is a permanent cache; positive opts into time-based revalidation
```

A permanent cache is right here: every page renders from compile-time data, so the only thing
that changes the output is a redeploy, and a redeploy starts from an empty cache. Set a
positive TTL only when a *persistent* cache volume is shared across deploys — which needs an
`extraVolumes` mount, since the default cache lives in an `emptyDir`.

## Upgrading

### 4.x to 5.0

`resourcesPreset` is gone. `resources` is the only sizing knob left, and it already ships with
the numbers this chart runs on — so a release that never set a preset needs no change, and a
`helm upgrade` with a values file that still carries one fails schema validation naming the key
rather than quietly ignoring it.

A preset was a word that meant something different in every chart, and reading the library was
the only way to find out what `medium` actually reserved. Substitute the block it stood for:

| Preset | requests | limits |
|---|---|---|
| `nano` | `cpu: 10m`, `memory: 32Mi` | `memory: 64Mi` |
| `micro` | `cpu: 25m`, `memory: 64Mi` | `memory: 128Mi` |
| `small` | `cpu: 50m`, `memory: 128Mi` | `memory: 256Mi` |
| `medium` | `cpu: 100m`, `memory: 256Mi` | `memory: 512Mi` |
| `large` | `cpu: 250m`, `memory: 512Mi` | `memory: 1Gi` |

No preset ever set a CPU limit, and this chart's defaults still do not: a CPU limit cannot
protect the node the way a memory limit does — it only throttles the workload that owns it once
it is hit. Set `resources.limits.cpu` if you want one.

### 3.x to 4.0

Chart 4.0 tracks application 2.3.0, which moved its settings into the layered `PORTFOLIO_`
configuration. The `application` block is gone; a `helm upgrade` with 3.x values fails schema
validation naming the offending key rather than starting a pod on the defaults.

| Before | After |
|---|---|
| `application.port` | `server.port` |
| `application.logLevel` | `logLevel` |

Both are still delivered as `PORT` and `RUST_LOG` — the Dioxus toolchain reads them from the
environment and they are not part of the layered configuration. What is new is everything
around them: `server.host`, `assets.distDir`, `isr.*`, and the `config` / `configExtraToml`
escape hatches, all rendered into a mounted `config.toml`.

## Security

The pod satisfies the [restricted Pod Security Standard][pss] as installed: non-root (UID
1001), read-only root filesystem, all capabilities dropped, no privilege escalation,
`seccompProfile: RuntimeDefault`, and no ServiceAccount token mounted. `/tmp` is the one
writable path, provided as an `emptyDir`.

Opt out with `podSecurityContextPreset: none` / `securityContextPreset: none`, or override
single fields under `podSecurityContext` / `securityContext` — your values are merged over the
preset rather than replacing it.

[pss]: https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted

## When it will not start

```bash
kubectl describe pod -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
kubectl logs -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
```

If the pod is running but the Ingress serves an error, check the app directly first — that
separates a broken Ingress from a broken pod:

```bash
kubectl port-forward -n [NAMESPACE] svc/[RELEASE_NAME]-portfolio 8080:80
curl http://localhost:8080/api/health
```

## Publishing it with Gateway API

`ingress` and `gateway` are independent switches, so a cluster moving from an Ingress controller
to a Gateway implementation can run both while it migrates. Only the route belongs to this chart:
the `Gateway` — its listeners, its address, its certificates — is the cluster operator's, and
`parentRefs` is how the route asks to be attached to one.

```yaml
ingress:
  enabled: false

gateway:
  enabled: true
  parentRefs:
    - name: shared-gateway
      namespace: gateway-system
  hostnames:
    - portfolio.example.com
```

That is the whole configuration. With no `rules`, the route gets one prefix match on `/` pointing
at this chart's Service, which is the Gateway API equivalent of a single-path Ingress.

What used to live in controller-specific annotations is a typed field:

| Ingress | Gateway API |
|---|---|
| `ingressClassName` | `gateway.parentRefs` — the Gateway, not the class |
| `hosts[].host` | `gateway.hostnames` |
| `hosts[].paths[]` | `gateway.rules[].matches`, or `gateway.path` for the single-path case |
| `tls[]` | the Gateway listener's `certificateRefs` — not this chart's, unless it creates one |
| `nginx.ingress.kubernetes.io/ssl-redirect` | `gateway.httpsRedirect.enabled` |
| `nginx.ingress.kubernetes.io/rewrite-target` | a `URLRewrite` filter in `gateway.filters` |
| `*/proxy-read-timeout` | `gateway.timeouts` |

Header manipulation, redirects, rewrites, mirroring and traffic splitting are all `filters` or
weighted `backendRefs`, which means they are schema-validated at apply time rather than being
strings a controller may or may not recognise:

```yaml
gateway:
  timeouts:
    request: 30s
  filters:
    - type: ResponseHeaderModifier
      responseHeaderModifier:
        set:
          - name: X-Content-Type-Options
            value: nosniff
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: portfolio-canary
          port: 80
          weight: 10
        - name: RELEASE-NAME-portfolio
          port: 80
          weight: 90
```

### Letting the chart own a Gateway

For an install with no cluster-wide Gateway to attach to, `gateway.create` renders one. It is off
by default because a Gateway usually provisions a load balancer, and one per application is rarely
what you want.

```yaml
gateway:
  enabled: true
  create: true
  gatewayClassName: cilium
  hostnames:
    - portfolio.example.com
  tls:
    enabled: true
    certificateRefs:
      - name: portfolio-tls
  httpsRedirect:
    enabled: true
  infrastructure:
    annotations:
      io.cilium/lb-ipam-ips: 203.0.113.10
```

A route that names no parent attaches to that Gateway automatically, so the Gateway's name is
never written twice. Listeners are `http` and — with `tls.enabled` — `https`, both accepting any
hostname and both restricted to routes from this namespace; `gateway.listeners` replaces them
outright when a listener needs its own hostname or certificate.

> [!NOTE]
> The chart never creates a `ReferenceGrant`. A grant lives in the namespace of the object being
> referenced, so a chart that emitted its own would simply be authorising itself — the one thing
> the object exists to prevent. Cross-namespace `backendRefs` and `certificateRefs` work, but the
> grant is the target namespace owner's to create.

If `gateway.enabled` is set on a cluster without the Gateway API CRDs, the render fails and says
so. It does not quietly skip the route: that would render clean in CI and leave a real install
succeeding with the application unreachable.

## Network policies, and what Cilium adds

`networkPolicy.engine` picks the dialect the same rules are written in:

```yaml
networkPolicy:
  enabled: true
  engine: cilium   # kubernetes (default) | cilium | both
```

Every value is translated either way, so switching is a one-line change rather than a re-authoring.
`both` emits both objects for a CNI migration — additive, not stricter, since policies selecting
one pod union their allowances.

The portable API can only name destinations by IP, so "may reach the internet over HTTPS" has to be
written `0.0.0.0/0` on 443 with the private ranges and the cloud metadata endpoint carved out — a
rule that, read honestly, permits a compromised container to reach every public host that exists.
Cilium can say the thing that was actually meant:

```yaml
networkPolicy:
  enabled: true
  engine: cilium
  egress:
    https:
      enabled: false     # drop the CIDR rule; the FQDN rule replaces it
  cilium:
    description: "outbound to the hosts this actually talks to"
    egress:
      toFQDNs:
        - matchName: api.example.com
        - matchPattern: "*.cdn.example.com"
      dnsMatchPatterns:
        - matchPattern: "*.example.com"
      toEntities:
        - kube-apiserver
      httpRules:
        - method: GET
          path: "/v1/.*"
```

`toFQDNs` is enforced against the addresses Cilium's DNS proxy saw returned for that name, so the
DNS rule has to stay on — the render fails rather than leaving an FQDN rule that silently matches
nothing. `enableDefaultDeny` is stated rather than implied, which is what makes the intentional
default-deny case (policies enabled, rule lists empty) actually deny.

When the chart is exposed through a Gateway, the policy admits the Gateway's data plane by
deriving the peer from `gateway.parentRefs` — `gateway.networking.k8s.io/gateway-name`, the label
Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision. Naming
the Gateway a second time under `networkPolicy` would be a second place to edit on a rename, and a
policy pointing at the wrong Gateway looks correct and blocks everything.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| assets | object | `{"distDir":"public"}` | Static asset serving, rendered under `assets` in the mounted `config.toml`. |
| assets.distDir | string | `"public"` | Directory holding the bundled assets (`assets.dist_dir`), relative to the working directory. Only the readiness probe consults it; the bundle itself is served by the Dioxus asset router, which resolves `public/` relative to the binary. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. The application never calls the Kubernetes API. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Extra configuration, expressed as the TOML tree of [the application's README](https://github.com/TimSchoenle/Portfolio#configuration) (`assets.dist_dir`, `isr.ttl_secs`, ...). Merged over everything the chart derives from the values above, so it can both extend and override them. Rendered into the mounted ConfigMap — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount | object | `{"configDir":"/etc/portfolio/config","secretsDir":"/etc/portfolio/secrets"}` | Where the rendered configuration and the credential files land in the container. |
| configMount.configDir | string | `"/etc/portfolio/config"` | Directory the rendered `config.toml` is mounted at, passed as `PORTFOLIO_CONFIG`. |
| configMount.secretsDir | string | `"/etc/portfolio/secrets"` | Directory credential files would be mounted at, passed as `PORTFOLIO_SECRETS_DIR`. The server reads no secret today — `github.token` belongs to the build-time repository builder — so nothing is mounted and the variable is not set; the value is here for an operator adding one through `extraVolumes`. |
| csp | object | `{"cloudflare":{"scriptNonce":true,"turnstile":false,"webAnalytics":false},"hashInlineScripts":true}` | The `Content-Security-Policy` the server sends on every document. The policy itself is not configurable — it is built from the response body, hashing each inline `<script>` the document actually carries, so it cannot drift from what is sent. These keys decide only what it has to make room for. |
| csp.cloudflare | object | `{"scriptNonce":true,"turnstile":false,"webAnalytics":false}` | Concessions to the Cloudflare products running in front of this origin. Each one widens the policy, so each is switched on only for a product that is actually in use. |
| csp.cloudflare.scriptNonce | bool | `true` | Reserve a per-response nonce in `script-src` (`csp.cloudflare.script_nonce`) for the inline `<script>` Cloudflare's bot products — Bot Fight Mode, JavaScript Detections, the challenge platform — inject at the edge, after the server has hashed what it rendered. No hash can cover it, and without the nonce the detection is refused and silently never runs. On by default, and it carries one obligation the chart cannot enforce: **no Cloudflare Cache Rule may cache the shell**, or a single nonce is pinned across every reader for the lifetime of the cache entry. |
| csp.cloudflare.turnstile | bool | `false` | Admit `https://challenges.cloudflare.com` in `script-src` *and* `frame-src` (`csp.cloudflare.turnstile`), for a Turnstile widget rendered in a page this server serves. Admitting only the first renders an empty box. |
| csp.cloudflare.webAnalytics | bool | `false` | Admit the Cloudflare Web Analytics beacon and the endpoint it reports to (`csp.cloudflare.web_analytics`). For the manually embedded snippet only — the automatic edge injection is an inline script, which is what `scriptNonce` covers instead. |
| csp.hashInlineScripts | bool | `true` | Hash the document's inline scripts (`csp.hash_inline_scripts`) instead of admitting all inline script with `'unsafe-inline'`. An escape hatch, not a preference: turning it off also requires `csp.cloudflare.scriptNonce: false`, because a browser ignores `'unsafe-inline'` as soon as the policy carries a nonce. The chart refuses the mismatched pair rather than letting the server fail its own boot. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| gateway | object | `{"addresses":[],"allowedRoutes":{},"annotations":{},"backendRefs":[],"create":false,"enabled":false,"filters":[],"gatewayClassName":"","hostnames":[],"httpPort":80,"httpsPort":443,"httpsRedirect":{"enabled":false,"port":null,"sectionName":"","statusCode":301},"infrastructure":{},"listeners":[],"parentRefs":[],"path":"/","rules":[],"timeouts":{},"tls":{"certificateRefs":[],"enabled":false,"mode":"Terminate","options":{}}}` | Gateway API configuration, consumed by `common.gateway.*`. The successor to `ingress`, and an independent switch from it: a cluster migrating between an Ingress controller and a Gateway implementation runs both for a while.  The division of labour is the API's, not this chart's. A `Gateway` — the listeners, the address, the certificates — belongs to the cluster operator; an application owns only the `HTTPRoute` that attaches to it. So the default here is route-only, and `create` is for installs that have no cluster-wide Gateway to attach to. |
| gateway.addresses | list | `[]` | Addresses requested for the created Gateway, e.g. a fixed `IPAddress`. |
| gateway.allowedRoutes | object | `{}` | Which routes may attach to the created Gateway's listeners. Defaults to `Same`: a Gateway this chart owns should not be attachable from another namespace unless that is asked for. |
| gateway.annotations | object | `{}` | Annotations for the HTTPRoute and the created Gateway. Values may contain Go templates. |
| gateway.backendRefs | list | `[]` | Backends for rules that name none. Defaults to this chart's own Service. Weights are honoured, so a traffic split needs no custom rule. |
| gateway.create | bool | `false` | Also create the Gateway itself. Leave off when the cluster already runs one — that is the normal case, and one Gateway per application usually means one load balancer per application. When on, a route that names no parent attaches to it automatically. |
| gateway.enabled | bool | `false` | Create the HTTPRoute. Requires the `gateway.networking.k8s.io` CRDs; `common.gateway.validate` fails the render loudly rather than silently dropping the route when they are absent. |
| gateway.filters | list | `[]` | Filters applied to the default rule: `RequestHeaderModifier`, `ResponseHeaderModifier`, `RequestRedirect`, `URLRewrite`, `RequestMirror`, `ExtensionRef`. This is where an Ingress controller's annotations end up, as typed fields. Ignored when `rules` is set. |
| gateway.gatewayClassName | string | `""` | GatewayClass that programs the created Gateway, e.g. `cilium`, `istio`, `envoy-gateway`, `nginx`. Required by `create`; a Gateway without one is never reconciled. Ignored otherwise. |
| gateway.hostnames | list | `[]` | Hostnames the route serves. Values may contain Go templates.  Required unless `create` is set. A route with no hostnames matches every name its listener accepts: on a Gateway this chart owns that is harmless, and sometimes the point — an install reached by address has no DNS name to state. On a shared Gateway it means taking over traffic meant for other applications, so the render refuses it. |
| gateway.httpPort | int | `80` | Port for the derived HTTP listener. |
| gateway.httpsPort | int | `443` | Port for the derived HTTPS listener. |
| gateway.httpsRedirect | object | `{"enabled":false,"port":null,"sectionName":"","statusCode":301}` | A second route that redirects plaintext traffic to HTTPS. Under Ingress this was a controller-specific annotation; Gateway API expresses it as a typed `RequestRedirect` filter, which means it has to be a real object. |
| gateway.httpsRedirect.enabled | bool | `false` | Create the redirect route. |
| gateway.httpsRedirect.port | string | `nil` | Port to redirect to. Left unset, the scheme implies it. |
| gateway.httpsRedirect.sectionName | string | `""` | Listener to bind the redirect to. Must be the plaintext one: attached to every listener, the redirect would also apply to the HTTPS listener and loop forever. Defaults to `http`, the name of the listener `create` renders. |
| gateway.httpsRedirect.statusCode | int | `301` | Redirect status code. `301` or `302`. |
| gateway.infrastructure | object | `{}` | `infrastructure.labels` / `infrastructure.annotations` for the created Gateway, passed through to the load balancer the implementation provisions. Where Cilium's LB-IPAM annotations go. |
| gateway.listeners | list | `[]` | Listeners for the created Gateway, replacing the derived ones entirely. Reach for this when a listener needs its own hostname or certificate. |
| gateway.parentRefs | list | `[]` | Gateways the route attaches to. Each entry takes `name` and optionally `namespace`, `sectionName` (a single listener), `port`, `group` and `kind`; the API's defaults are filled in. Values may contain Go templates.  A route that names no parent is accepted by the API server and then does nothing — no listener ever programs it — so this is required unless `create` is set. Example: ```yaml parentRefs:   - name: shared-gateway     namespace: gateway-system     sectionName: https ``` |
| gateway.path | string | `"/"` | Path prefix for the default rule. Ignored when `rules` is set. |
| gateway.rules | list | `[]` | Routing rules, in full `HTTPRouteRule` form (`matches`, `filters`, `backendRefs`, `timeouts`). An entry that omits `backendRefs` inherits this chart's Service, so a rule that only narrows the path does not have to restate where the traffic goes.  Left empty, the route gets one rule matching `path` as a prefix — the Gateway API equivalent of a single-path Ingress, and a complete configuration together with `hostnames`. |
| gateway.timeouts | object | `{}` | Timeouts for the default rule: `request` and `backendRequest`, as Go durations. Ignored when `rules` is set. |
| gateway.tls | object | `{"certificateRefs":[],"enabled":false,"mode":"Terminate","options":{}}` | TLS for the created Gateway's HTTPS listener. Ignored without `create`, and irrelevant when attaching to somebody else's Gateway — the certificate is theirs. |
| gateway.tls.certificateRefs | list | `[]` | Secrets holding the certificate. Required by `Terminate`: unlike an Ingress there is no convention by which one is looked up from the hostname. A ref naming another namespace additionally needs a `ReferenceGrant` there, which this chart deliberately does not create — a grant is the target namespace owner's to give. |
| gateway.tls.enabled | bool | `false` | Add an HTTPS listener. |
| gateway.tls.mode | string | `"Terminate"` | TLS mode. |
| gateway.tls.options | object | `{}` | Implementation-specific TLS options. |
| image | object | `{"pullPolicy":"","registry":"","repository":"timschoenle/portfolio","tag":"v2.9.0@sha256:a3380c804d62ea7dcadab149079414a94aeebad99d973eed3c384f912566466d"}` | Container image the pod runs, composed as `registry/repository:tag`. |
| image.pullPolicy | string | `""` | Kubernetes image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timschoenle/portfolio"` | Container image repository where the Portfolio application image is stored. |
| image.tag | string | `"v2.9.0@sha256:a3380c804d62ea7dcadab149079414a94aeebad99d973eed3c384f912566466d"` | Container image tag to deploy, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| ingress | object | `{"annotations":{},"enabled":false,"hosts":[],"ingressClassName":"nginx","tls":[]}` | The Ingress in front of the Service. Off by default; `gateway` is the Gateway API alternative and the two are independent switches. |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Example: ```yaml annotations:   cert-manager.io/cluster-issuer: "letsencrypt-prod"   nginx.ingress.kubernetes.io/ssl-redirect: "true" ``` |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Values may contain Go templates. Example: ```yaml hosts:   - host: portfolio.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: portfolio-tls     hosts:       - portfolio.example.com ``` |
| isr | object | `{"cacheDir":"/tmp/isr","ttlSecs":0}` | Incremental static regeneration: the server caches each rendered page and serves it again instead of re-rendering. |
| isr.cacheDir | string | `"/tmp/isr"` | Writable directory rendered HTML is cached into (`isr.cache_dir`). Empty disables ISR and renders every request fresh. Keep it under `/tmp`, which the chart already provides as a writable emptyDir under the read-only root filesystem, and outside the bundled `public/` tree so those content-hashed assets stay immutable. The server creates the directory itself and falls back to rendering fresh if it turns out not to be writable. |
| isr.ttlSecs | int | `0` | Revalidation interval in seconds (`isr.ttl_secs`). `0` means a permanent cache, which is right for this site: every page renders from compile-time data, so the only thing that changes the output is a redeploy — and that starts from an empty cache. Set a positive value only when a *persistent* cache volume is shared across deploys. |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| livenessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/api/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":10,"timeoutSeconds":5}` | Liveness probe. Restarts the container when it stops responding. |
| livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| livenessProbe.failureThreshold | int | `3` | Consecutive failures before the container is restarted. |
| livenessProbe.httpGet | object | `{"path":"/api/health","port":"http"}` | HTTP handler for the probe. |
| livenessProbe.httpGet.path | string | `"/api/health"` | Health check path. |
| livenessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| livenessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| livenessProbe.periodSeconds | int | `10` | Probe interval. |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| logLevel | string | `"info"` | Log verbosity, passed as `RUST_LOG`. Accepts standard tracing directives (`info`, `debug`, `web=debug,info`). Not part of the `PORTFOLIO_` namespace — see `server`. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"cilium":{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}},"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration.  Every generated egress rule is scoped by a `to:` selector. An egress rule that lists only ports is not a restriction: the NetworkPolicy API reads a missing `to` as "any destination", which would permit traffic to every in-cluster service and to the cloud instance metadata endpoint. |
| networkPolicy.cilium | object | `{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}}` | Cilium-only additions, used when `engine` is `cilium` or `both`. Everything above is translated into the CiliumNetworkPolicy automatically; these are the rules the portable API has no way to express.  `extraIngress`, `extraEgress` and the per-section `customRules` above are *not* carried over: those are verbatim `networking.k8s.io/v1` rule objects and are not valid CNP. The fields below are their counterparts. |
| networkPolicy.cilium.description | string | `""` | `spec.description`, which Cilium surfaces in `cilium policy get` and in Hubble flow verdicts. The one place to record why a rule exists where an operator debugging a drop will actually see it. |
| networkPolicy.cilium.egress | object | `{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]}` | Cilium-only egress rules. |
| networkPolicy.cilium.egress.customRules | list | `[]` | Additional egress rules in CiliumNetworkPolicy form, appended verbatim. |
| networkPolicy.cilium.egress.dnsMatchPatterns | list | `[]` | What the DNS proxy may resolve, e.g. `- matchPattern: "*.example.com"`. Defaults to everything, which only permits the lookup — an answer is still only reachable if some rule allows the address. |
| networkPolicy.cilium.egress.entityPorts | list | `[]` | Restrict the `toEntities` rule to specific ports. Empty means all ports. |
| networkPolicy.cilium.egress.fqdnPorts | list | `[]` | Ports the `toFQDNs` rule allows. Defaults to TCP/443. |
| networkPolicy.cilium.egress.httpRules | list | `[]` | L7 HTTP rules layered onto the `toFQDNs` rule, e.g. `- method: GET` / `path: "/v1/.*"`. Turns "may reach this host" into "may make these requests to this host". Costs a proxy hop per connection. |
| networkPolicy.cilium.egress.toEntities | list | `[]` | Named destination sets, e.g. `world` for everything outside the cluster, or `kube-apiserver`. Not a synonym for the `egress.cidr`/`except` translation: `world` does not carve out the cloud metadata endpoint the way those defaults do. |
| networkPolicy.cilium.egress.toFQDNs | list | `[]` | Destinations by name rather than by address, e.g. `- matchName: api.example.com` or `- matchPattern: "*.example.com"`. This is the rule the CIDR-based `egress.https` was always a poor approximation of: "may reach the internet on 443" permits every public host that exists, where this permits the ones the application actually talks to.  Enforced against the addresses Cilium's DNS proxy saw returned for the name, so `egress.dns.enabled` must stay on — the render fails if it is not. |
| networkPolicy.cilium.enableDefaultDeny | bool | `true` | State default-deny explicitly rather than relying on it being implied by the presence of rules. This is what makes the intentional default-deny case — a policy with an empty rule list — actually deny, instead of being treated as no policy at all. Cilium 1.16+. |
| networkPolicy.cilium.extraEgress | list | `[]` | Extra egress rules in CiliumNetworkPolicy form, appended regardless of `egress.enabled`. |
| networkPolicy.cilium.extraIngress | list | `[]` | Extra ingress rules in CiliumNetworkPolicy form, appended regardless of `ingress.enabled`. |
| networkPolicy.cilium.ingress | object | `{"customRules":[],"fromEntities":[]}` | Cilium-only ingress rules. |
| networkPolicy.cilium.ingress.customRules | list | `[]` | Additional ingress rules in CiliumNetworkPolicy form, appended verbatim. |
| networkPolicy.cilium.ingress.fromEntities | list | `[]` | Named source sets, e.g. `cluster`, `host`, `remote-node`, `world`, `kube-apiserver`. A named entity stays correct when the cluster is renumbered; a CIDR list does not. |
| networkPolicy.egress | object | `{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}}` | Egress configuration. |
| networkPolicy.egress.cidr | string | `"0.0.0.0/0"` | Destination CIDR for the HTTP/HTTPS rules. |
| networkPolicy.egress.customRules | list | `[]` | Additional egress rules, appended verbatim. Each must carry its own `to:` selector. |
| networkPolicy.egress.dns | object | `{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}}` | DNS resolution. |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to the cluster DNS service. |
| networkPolicy.egress.dns.namespaceSelector | object | `{"kubernetes.io/metadata.name":"kube-system"}` | Namespace selector for the DNS service. |
| networkPolicy.egress.dns.podSelector | object | `{"k8s-app":"kube-dns"}` | Pod selector for the DNS service. |
| networkPolicy.egress.enabled | bool | `true` | Add egress rules. Disabled means default-deny for outbound traffic. |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. Defaults exclude RFC1918 private space and link-local 169.254.0.0/16, which covers the cloud instance metadata endpoint. |
| networkPolicy.egress.http | object | `{"enabled":false}` | Outbound HTTP. |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to TCP/80 on the destinations described by `cidr`/`except`. |
| networkPolicy.egress.https | object | `{"enabled":true}` | Outbound HTTPS. |
| networkPolicy.egress.https.enabled | bool | `true` | Allow egress to TCP/443 on the destinations described by `cidr`/`except`. |
| networkPolicy.enabled | bool | `false` | Create the NetworkPolicies. Enabling this with no rules configured yields a default-deny policy, which is intentional. |
| networkPolicy.engine | string | `"kubernetes"` | Which policy dialect to render. `kubernetes` emits the portable `networking.k8s.io/v1` pair; `cilium` emits `CiliumNetworkPolicy`, which can express FQDN destinations, named entities and L7 rules that the portable API cannot; `both` emits both, for the window in which a cluster is migrating between CNIs.  The engine picks the dialect, not the rules: every value below is translated either way. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended regardless of `egress.enabled`. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended regardless of `ingress.enabled`. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress configuration. |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Ingress controller configuration. |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from the ingress controller. |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace where the ingress controller runs. |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector for the ingress controller. |
| networkPolicy.ingress.customRules | list | `[]` | Additional ingress rules, appended verbatim. |
| networkPolicy.ingress.enabled | bool | `true` | Add ingress rules. Disabled means default-deny for inbound traffic. |
| networkPolicy.ingress.gateway | object | `{"enabled":true,"namespace":"","ports":[],"selector":{}}` | Allow traffic from the Gateway API data plane. Only rendered when `gateway.enabled` is also set, so it costs nothing on a chart exposed through an Ingress.  Needs no configuration in the common case: the Gateway that must be admitted is by definition the one `gateway.parentRefs` names, so both fields below are derived from it. |
| networkPolicy.ingress.gateway.enabled | bool | `true` | Allow ingress from the Gateway's data plane. |
| networkPolicy.ingress.gateway.namespace | string | `""` | Namespace the data plane runs in. Empty derives it from `gateway.parentRefs`. |
| networkPolicy.ingress.gateway.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.gateway.selector | object | `{}` | Pod selector matching the data plane. Empty derives `gateway.networking.k8s.io/gateway-name: <parentRef>`, the label Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision for a Gateway. |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}` | Allow scraping from a monitoring namespace. |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from the monitoring namespace. |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where monitoring tools run, matched on `kubernetes.io/metadata.name`. |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Override the namespace selector entirely. |
| networkPolicy.ingress.monitoring.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1001,"runAsGroup":1001,"runAsUser":1001}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `1001` | Supplemental group ID applied to mounted volumes. |
| podSecurityContext.runAsGroup | int | `1001` | Primary group ID to run as. |
| podSecurityContext.runAsUser | int | `1001` | User ID to run as. Matches the non-root user baked into the image (USER 1001:1001). |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name. |
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/api/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Readiness probe. Removes the pod from the Service while it cannot serve traffic. |
| readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| readinessProbe.failureThreshold | int | `3` | Consecutive failures before the pod is removed from the Service. |
| readinessProbe.httpGet | object | `{"path":"/api/health","port":"http"}` | HTTP handler for the probe. |
| readinessProbe.httpGet.path | string | `"/api/health"` | Health check path. |
| readinessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| readinessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| readinessProbe.periodSeconds | int | `5` | Probe interval. |
| readinessProbe.successThreshold | int | `1` | Consecutive successes before the pod is considered ready. |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout. |
| replicaCount | int | `1` | Number of application replicas. |
| resources | object | `{"limits":{"cpu":"250m","memory":"128Mi"},"requests":{"cpu":"25m","memory":"32Mi"}}` | Resource requests and limits for the container. The Rust server has a small footprint; these leave generous headroom. |
| resources.limits | object | `{"cpu":"250m","memory":"128Mi"}` | Maximum resources the container may use. |
| resources.limits.cpu | string | `"250m"` | Maximum CPU allocation for the container. |
| resources.limits.memory | string | `"128Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"25m","memory":"32Mi"}` | Resources guaranteed to the container. |
| resources.requests.cpu | string | `"25m"` | Minimum CPU requested by the container. |
| resources.requests.memory | string | `"32Mi"` | Minimum memory requested by the container. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. The application is a statically linked binary serving pre-built assets and needs no writable root filesystem; a writable /tmp is provided automatically via an emptyDir. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation and a writable root filesystem. |
| server | object | `{"host":"0.0.0.0","port":8080}` | The listener, which is the one part of the configuration the `PORTFOLIO_` namespace does not own: `PORT`, `IP` and `RUST_LOG` belong to the Dioxus toolchain, which reads them from the environment itself. They are therefore still passed as environment variables, and cannot be supplied through `config`. |
| server.host | string | `"0.0.0.0"` | Bind address, passed as `IP`. |
| server.port | int | `8080` | Bind port, passed as `PORT`. Also the container port, the Service target and what every probe and NetworkPolicy rule is written against. |
| service | object | `{"annotations":{},"port":80,"type":"ClusterIP"}` | The Service in front of the pods, forwarding `port` to the container's listener. |
| service.annotations | object | `{}` | Annotations for the Service. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount | object | `{"annotations":{},"automountToken":false,"create":true,"name":""}` | ServiceAccount the pods run under. `create: false` with no `name` means the `default` one. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account. |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token for pods that default to it. |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account. |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty). |
| startupProbe | object | `{"enabled":true,"failureThreshold":12,"httpGet":{"path":"/api/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"timeoutSeconds":3}` | Startup probe. Protects a slow-starting container from the liveness probe. |
| startupProbe.enabled | bool | `true` | Enable the startup probe. |
| startupProbe.failureThreshold | int | `12` | Consecutive failures before the container is considered failed. |
| startupProbe.httpGet | object | `{"path":"/api/health","port":"http"}` | HTTP handler for the probe. The Portfolio application serves /api/health. |
| startupProbe.httpGet.path | string | `"/api/health"` | Health check path. |
| startupProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| startupProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| startupProbe.periodSeconds | int | `5` | Probe interval. |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout. |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |

## Source Code

* <https://github.com/TimSchoenle/Portfolio>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
