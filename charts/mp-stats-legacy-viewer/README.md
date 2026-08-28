# mp-stats-legacy-viewer




![Version: 3.3.2](https://img.shields.io/badge/Version-3.3.2-informational?style=flat-square) ![AppVersion: v0.19.0](https://img.shields.io/badge/AppVersion-v0.19.0-informational?style=flat-square) 

MP Stats Legacy Viewer

A single stateless HTTP deployment: no database, no credentials and nothing to persist. It
installs and serves on its ClusterIP with no configuration at all, so the only values most
installs touch are `ingress.*` and `resources`.

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

helm install [RELEASE_NAME] timschoenle/mp-stats-legacy-viewer \
  --namespace [NAMESPACE] --create-namespace
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/mp-stats-legacy-viewer -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`. Nothing outlives the release.

## Publishing it

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: stats.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: stats-tls
      hosts:
        - stats.example.com
```

## Configuration

The application reads its settings through a layered, file-first loader. This chart renders
them into one `config.toml`, mounts it as a ConfigMap and points `MP_STATS_CONFIG` at it.
Nothing is passed as an environment variable: the loader **fails the boot on a key supplied by
both the environment and a file** rather than resolving it by precedence.

**Pointing `MP_STATS_CONFIG` at the mount replaces the `/config.toml` the image ships** rather
than layering over it, so this chart restates every key that file carried — the bind address,
`server.distDir` and `server.dataDir`. The defaults match the image's own layout; change them
only for an image that puts the frontend bundle or the converted data somewhere else. A
`dist_dir` without an `index.html` is a boot failure, not a 404.

`config` takes the raw TOML tree for anything the first-class values do not cover, merged over
the derived one:

```yaml
config:
  converter:
    cache:
      enabled: false
```

and `configExtraToml` is appended verbatim for what the renderer cannot express.

The server does not reload its configuration, so the chart keeps the conventional
`checksum/configmap` pod annotation: a configuration change rolls the Deployment, which is the
only way it takes effect.

## Logging and error reporting

`telemetry.logFilter` is a `RUST_LOG`-style filter — `info,mp_stats_server=debug` — and
`telemetry.jsonLogs` switches the output from human-readable lines to one JSON object per
record, which is what a cluster shipping logs to a collector wants. A `RUST_LOG` set in the
container outranks the filter: it is the one place in this configuration where a bare
environment variable wins over the layered loader, which makes `extraEnv` the override to reach
for while a pod is misbehaving.

`telemetry.sentry` reports errors and panics to Sentry, and can record a transaction per
request. It is off, and inert while it is off: no client, no panic hook, no `tracing` layer and
no HTTP middleware is installed, so nothing leaves the process and nothing about a rendered
release changes.

```yaml
telemetry:
  sentry:
    enabled: true
    dsn: https://<key>@<host>/<project>
```

The DSN embeds a write key for the project's event stream, so the chart treats it as the
credential it is: it goes to a Secret under `telemetry__sentry__dsn`, never into the ConfigMap.
It is the **only** credential this server reads, so switching Sentry on is what gives the pod a
secrets volume and an `MP_STATS_SECRETS_DIR` at all — expect the pod spec to change shape, not
just the ConfigMap. Point `existingSecret` at a Secret you created yourself to keep the DSN out
of `values.yaml` and out of the Helm release object:

```shell
kubectl create secret generic mp-stats-sentry   --namespace [NAMESPACE]   --from-literal=telemetry__sentry__dsn='https://<key>@<host>/<project>'
```

```yaml
existingSecret: mp-stats-sentry
```

**The key name is the configuration path the server reads, not a free-form name.** The DSN
arrives as a file in a projected volume and the server takes the key out of the file *name*, so
`telemetry__sentry__dsn` is required; a Secret spelled any other way mounts cleanly and supplies
nothing. Switching the feature on with no DSN — neither inline nor in an `existingSecret` — is
refused at render time, because the server refuses to boot rather than installing a client that
reports nowhere, and a rejected `helm upgrade` beats a CrashLoopBackOff.

**Check the egress.** The chart never reads the DSN, so it cannot name the ingest host in the
network policies. The defaults do cover it — `networkPolicy.egress.https` permits TCP/443 to
`0.0.0.0/0` minus private space — so an out-of-the-box release reaches Sentry without further
work. A release that narrowed `networkPolicy.egress.cidr`, turned `egress.https` off, or
replaced the CIDR rule with `networkPolicy.cilium.egress.toFQDNs` has to name the endpoint
itself. Getting that wrong is silent: an SDK that cannot reach its endpoint queues events and
then discards them, so the project simply stays empty, which reads as "no errors".

`tracesSampleRate` is `0` — this server starts no traces of its own, which is the right figure
for a static file server, where a trace per asset request is volume without a question behind
it. It still continues a trace that arrives already sampled, so one reader action stays readable
across whatever sits in front of this server.

`sendDefaultPii` stays off. On, every event carries the client IP, the whole request header set
including `Cookie`, and the resolved user, to a third party — and the same flag is what stops
the HTTP middleware redacting sensitive headers.

`environment`, `release` and `serverName` are empty by default and the chart writes none of
them: the server derives the first two — `production` for the release binaries every published
image is built as, and `mp-stats-legacy-viewer@v<version>` spelled the way the image tag spells
it — and reports no host tag. A blank value is a *supplied* value to the loader, not an absent
one, so set them only to say something the server cannot derive.

`captureLevel` and `breadcrumbLevel` both sit *under* `logFilter`: a record the filter drops
never becomes an issue or a breadcrumb, whatever they say.

## Content-Security-Policy

The header is derived, not configured: at startup the server reads the `index.html` in
`server.distDir` and hashes every inline `<script>` in it, so the policy cannot drift from the
frontend build. An unreadable or unscannable shell fails the boot before the listener binds.

What the values decide is which Cloudflare products this deployment runs. All are off, because
each one widens the policy:

```yaml
server:
  csp:
    enabled: true
    cloudflare:
      scriptNonce: false   # for the script Cloudflare's bot products inject at the edge
      turnstile: false     # admits challenges.cloudflare.com in script-src and frame-src
      webAnalytics: false  # admits the beacon and the endpoint it reports to
```

Turn `scriptNonce` on when Bot Fight Mode, JavaScript Detections or the challenge platform sit
in front of this origin: they inject an inline script at the edge, after the shell was hashed,
so no hash can cover it and `script-src` refuses it — bot management looks enabled and does
nothing. The nonce also makes every document `Cache-Control: no-cache`, which comes with one
condition the chart cannot enforce:

> [!IMPORTANT]
> No Cloudflare Cache Rule may cache the shell. A "Cache Everything" rule overrides the origin's
> `Cache-Control`, satisfying the obligation at the origin and violating it at the edge — one
> nonce shared by every reader of that cache entry.

`enabled: false` drops the header entirely, and is only right when something in front of this
server already sets one.

## Upgrading

### 3.2 to 3.3

Chart 3.3 tracks the viewer's 0.19.0 release, which adds optional Sentry error reporting and
tracing and gives the log settings first-class keys. Everything is additive and off, so an
existing release needs no change.

Two things are worth knowing before switching Sentry on:

- **The pod gains a secrets volume.** The DSN is the only credential this server reads, so
  today's pods carry no secrets volume and no `MP_STATS_SECRETS_DIR`. Setting
  `telemetry.sentry.enabled` gives them both, and an `existingSecret` that should carry the DSN
  needs the key `telemetry__sentry__dsn`.
- **`telemetry.logFilter` restates a key the mounted file has to carry.** Pointing
  `MP_STATS_CONFIG` at this chart's mount replaces the image's own `/config.toml`, so the filter
  is now written explicitly rather than inherited. The default, `info`, is what the image
  carried; a release that had set a filter through `config.telemetry.log_filter` keeps working —
  `config` still merges over the derived tree.

### 2.x to 3.0

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

The same release also documents the `networkPolicy` knobs the `common` library always accepted
but this chart never listed: `networkPolicy.extraIngress`, `networkPolicy.extraEgress`,
`networkPolicy.ingress.monitoring.namespaceSelector`, and a `ports` list on the monitoring and
controller rules. They are additions — nothing that worked before behaves differently.


### 1.x to 2.0

Chart 2.0 tracks application 0.15.0, which replaced its environment-only configuration with the
layered, file-first loader every chart in this repository now uses. The `application` block is
gone; a `helm upgrade` with 1.x values fails schema validation naming the offending key rather
than starting a pod on the defaults.

| Before | After |
|---|---|
| `application.server.host` | `server.host` |
| `application.server.port` | `server.port` |

Both now feed `server.bind_addr` in the rendered file rather than a command-line flag. Two
values are new and have no 1.x equivalent — `server.distDir` and `server.dataDir` — because the
chart now supplies the paths the image's own `/config.toml` used to. Leave them alone unless
you run a modified image.

## Health checks

All three probes are enabled by default and point at the application's own endpoints —
`/health/startup`, `/health/live` and `/health/ready`. The startup probe is what tolerates a
slow first boot; raise `startupProbe.failureThreshold` rather than the liveness probe's
`initialDelaySeconds` if the pod is being killed while still starting.

`successThreshold` is accepted only on the readiness probe. Kubernetes rejects any other value
on the startup and liveness probes, so the chart omits it there.

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
    - mp-stats.example.com
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
        - name: mp-stats-legacy-viewer-canary
          port: 80
          weight: 10
        - name: RELEASE-NAME-mp-stats-legacy-viewer
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
    - mp-stats.example.com
  tls:
    enabled: true
    certificateRefs:
      - name: mp-stats-legacy-viewer-tls
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
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Extra configuration, expressed as the TOML tree of [the application's configuration reference](https://github.com/TimSchoenle/mp-stats-legacy-viewer/blob/main/docs/CONFIGURATION.md) (`server.bind_addr`, `converter.*`, ...). Merged over everything the chart derives from the values above, so it can both extend and override them. Rendered into the mounted ConfigMap — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount | object | `{"configDir":"/etc/mp-stats/config","secretsDir":"/etc/mp-stats/secrets"}` | Where the rendered configuration and the credential files land in the container. |
| configMount.configDir | string | `"/etc/mp-stats/config"` | Directory the rendered `config.toml` is mounted at, passed as `MP_STATS_CONFIG`. Pointing this at the mount **replaces** the `/config.toml` the image ships, so everything the image described — the bind address, `dist_dir`, `data_dir` — is restated by the values above. |
| configMount.secretsDir | string | `"/etc/mp-stats/secrets"` | Directory credential files are mounted at, passed as `MP_STATS_SECRETS_DIR`. The Sentry DSN is the only credential this chart handles, so the volume and the variable both appear only while `telemetry.sentry.enabled` is set — a release that does not report to Sentry carries neither. |
| existingSecret | string | `""` | Name of an existing Secret holding the Sentry DSN, which keeps it out of `values.yaml` and out of the Helm release object. **Its key is the configuration path, not a free-form name**: `telemetry__sentry__dsn`, because the file name is what the loader parses. Set, the chart renders no Secret of its own and `telemetry.sentry.dsn` is ignored. It is read only while `telemetry.sentry.enabled` is set — nothing else this server reads is a credential. |
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
| image | object | `{"pullPolicy":"","registry":"","repository":"timschoenle/mp-stats-legacy-viewer","tag":"v0.19.0@sha256:115f48976c6091f381668f50c8f8410b26532926c618afbb567bfbd16009adec"}` | Container image the pod runs, composed as `registry/repository:tag`. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timschoenle/mp-stats-legacy-viewer"` | The container image repository. |
| image.tag | string | `"v0.19.0@sha256:115f48976c6091f381668f50c8f8410b26532926c618afbb567bfbd16009adec"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| ingress | object | `{"annotations":{},"enabled":false,"hosts":[],"ingressClassName":"nginx","tls":[]}` | The Ingress in front of the Service. Off by default; `gateway` is the Gateway API alternative and the two are independent switches. |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Useful for configuring ingress controllers (e.g., cert-manager, rate limits). |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. Set to `true` to expose the service externally via Ingress. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Each host defines rules for routing external traffic. Example: ```yaml hosts:   - host: s3.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). Should match your cluster's ingress controller configuration. |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: s3-cert     hosts:       - s3.example.com ``` |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| livenessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/health/live","port":"http"},"initialDelaySeconds":1,"periodSeconds":10,"timeoutSeconds":5}` | Liveness probe. Restarts the container when it stops responding. |
| livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| livenessProbe.failureThreshold | int | `3` | Consecutive failures before the probe is considered failed. |
| livenessProbe.httpGet | object | `{"path":"/health/live","port":"http"}` | HTTP handler for the probe. |
| livenessProbe.httpGet.path | string | `"/health/live"` | Health check path. |
| livenessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| livenessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| livenessProbe.periodSeconds | int | `10` | Probe interval. |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"cilium":{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}},"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration |
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
| networkPolicy.egress | object | `{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}}` | Egress configuration |
| networkPolicy.egress.cidr | string | `"0.0.0.0/0"` | Destination CIDR for the HTTP/HTTPS rules |
| networkPolicy.egress.customRules | list | `[]` | Custom egress rules |
| networkPolicy.egress.dns | object | `{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}}` | DNS configuration for egress |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to DNS |
| networkPolicy.egress.dns.namespaceSelector | object | `{"kubernetes.io/metadata.name":"kube-system"}` | Namespace selector for the DNS service |
| networkPolicy.egress.dns.podSelector | object | `{"k8s-app":"kube-dns"}` | Pod selector for the DNS service |
| networkPolicy.egress.enabled | bool | `true` | Enable egress rules |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. Defaults exclude RFC1918 private space and link-local 169.254.0.0/16, which covers the cloud instance metadata endpoint. |
| networkPolicy.egress.http | object | `{"enabled":false}` | HTTP configuration for egress |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to HTTP (TCP/80) |
| networkPolicy.egress.https | object | `{"enabled":true}` | HTTPS configuration for egress |
| networkPolicy.egress.https.enabled | bool | `true` | Allow egress to HTTPS (TCP/443) |
| networkPolicy.enabled | bool | `false` | Enable network policies |
| networkPolicy.engine | string | `"kubernetes"` | Which policy dialect to render. `kubernetes` emits the portable `networking.k8s.io/v1` pair; `cilium` emits `CiliumNetworkPolicy`, which can express FQDN destinations, named entities and L7 rules that the portable API cannot; `both` emits both, for the window in which a cluster is migrating between CNIs.  The engine picks the dialect, not the rules: every value below is translated either way. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended regardless of `egress.enabled`. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended regardless of `ingress.enabled`. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress configuration |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Ingress Controller configuration |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from Ingress Controller |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace where Ingress Controller is running (default: traefik) |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector for Ingress Controller (default: Traefik label) |
| networkPolicy.ingress.customRules | list | `[]` | Custom ingress rules |
| networkPolicy.ingress.enabled | bool | `true` | Enable ingress rules |
| networkPolicy.ingress.gateway | object | `{"enabled":true,"namespace":"","ports":[],"selector":{}}` | Allow traffic from the Gateway API data plane. Only rendered when `gateway.enabled` is also set, so it costs nothing on a chart exposed through an Ingress.  Needs no configuration in the common case: the Gateway that must be admitted is by definition the one `gateway.parentRefs` names, so both fields below are derived from it. |
| networkPolicy.ingress.gateway.enabled | bool | `true` | Allow ingress from the Gateway's data plane. |
| networkPolicy.ingress.gateway.namespace | string | `""` | Namespace the data plane runs in. Empty derives it from `gateway.parentRefs`. |
| networkPolicy.ingress.gateway.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.gateway.selector | object | `{}` | Pod selector matching the data plane. Empty derives `gateway.networking.k8s.io/gateway-name: <parentRef>`, the label Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision for a Gateway. |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}` | Monitoring configuration for ingress |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from monitoring namespace |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where monitoring tools are running |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Namespace selector matching the monitoring namespace, replacing `namespace` when set. For a Prometheus labelled rather than named, or one of several namespaces that scrape. |
| networkPolicy.ingress.monitoring.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. Ignored when `affinity` is set. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1000,"runAsGroup":1000,"runAsUser":1000}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `1000` | Group ID for file system access |
| podSecurityContext.runAsGroup | int | `1000` | Primary group ID to run as |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name. |
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/health/ready","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Readiness probe. Removes the pod from the Service while it cannot serve traffic. |
| readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| readinessProbe.failureThreshold | int | `3` | Consecutive failures before the probe is considered failed. |
| readinessProbe.httpGet | object | `{"path":"/health/ready","port":"http"}` | HTTP handler for the probe. |
| readinessProbe.httpGet.path | string | `"/health/ready"` | Health check path. |
| readinessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| readinessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| readinessProbe.periodSeconds | int | `5` | Probe interval. |
| readinessProbe.successThreshold | int | `1` | Consecutive successes before the pod is considered ready. |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout. |
| replicaCount | int | `1` | Number of application replicas. |
| resources | object | `{"limits":{"memory":"100Mi"},"requests":{"cpu":"25m","memory":"100Mi"}}` | Requests and limits for the container, passed through to the pod spec unchanged. |
| resources.limits | object | `{"memory":"100Mi"}` | Resource limits define the maximum resources the container can use. |
| resources.limits.memory | string | `"100Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"25m","memory":"100Mi"}` | Resource requests define the guaranteed resources reserved for the container. |
| resources.requests.cpu | string | `"25m"` | Minimum CPU requested by the container. Serves rendered pages; modest but non-trivial CPU on request. Without a CPU request the pod is BestEffort and is the first thing evicted under node pressure. |
| resources.requests.memory | string | `"100Mi"` | Minimum memory requested by the container. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. A writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| server | object | `{"csp":{"cloudflare":{"scriptNonce":false,"turnstile":false,"webAnalytics":false},"enabled":true},"dataDir":"/dist/data","distDir":"/dist","host":"0.0.0.0","port":8080}` | The listener, and the two directories on disk it serves from. Rendered under `server` in the mounted `config.toml`, never into the environment. |
| server.csp | object | `{"cloudflare":{"scriptNonce":false,"turnstile":false,"webAnalytics":false},"enabled":true}` | The `Content-Security-Policy` the server attaches to every document. The policy itself is not configurable — it is derived at startup from the `index.html` in `distDir`, hashing every inline `<script>` in it, so it cannot drift from the frontend build. An unreadable shell fails the boot before the listener binds. |
| server.csp.cloudflare | object | `{"scriptNonce":false,"turnstile":false,"webAnalytics":false}` | Concessions to the Cloudflare products running in front of this deployment. All off: each one widens the policy, and only for a product that is actually switched on. |
| server.csp.cloudflare.scriptNonce | bool | `false` | Reserve a per-response nonce in `script-src` and serve documents `Cache-Control: no-cache` (`server.csp.cloudflare.script_nonce`). Needed by the products that inject an inline script at the edge — Bot Fight Mode, JavaScript Detections, the challenge platform — which no hash can cover, because they run after the shell was hashed. It carries one obligation the chart cannot enforce: **no Cloudflare Cache Rule may cache the shell**, or a single nonce is shared by every reader of that cache entry. |
| server.csp.cloudflare.turnstile | bool | `false` | Admit `https://challenges.cloudflare.com` in `script-src` and `frame-src` (`server.csp.cloudflare.turnstile`), for a Turnstile widget rendered in a page this server serves. |
| server.csp.cloudflare.webAnalytics | bool | `false` | Admit the Web Analytics beacon and the endpoint it reports to (`server.csp.cloudflare.web_analytics`). For the manually embedded snippet only — the automatic edge injection needs `scriptNonce` instead. |
| server.csp.enabled | bool | `true` | Attach the header at all (`server.csp.enabled`). Turn it off only when something in front of this server already sets one. |
| server.dataDir | string | `"/dist/data"` | The converter's output, served under `/data` (`server.data_dir`). The default is where the image puts it. |
| server.distDir | string | `"/dist"` | Built frontend served as the SPA (`server.dist_dir`). The server refuses to start unless it holds an `index.html`, which is both the entry point and the fallback for unknown routes. The default is where the image puts it — change it only for an image that differs. |
| server.host | string | `"0.0.0.0"` | Host half of the bind address (`server.bind_addr`). `0.0.0.0` is what makes the Service reach the listener. |
| server.port | int | `8080` | Port half of the bind address (`server.bind_addr`). Also the container port, the Service target and what every probe and NetworkPolicy rule is written against. |
| service | object | `{"port":80,"type":"ClusterIP"}` | The Service in front of the pods, publishing `port` and forwarding to `server.port`. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. Typically maps to `application.server.port`. |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount | object | `{"annotations":{},"automountToken":false,"create":true,"name":""}` | ServiceAccount the pods run under. `create: false` with no `name` means the `default` one. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| startupProbe | object | `{"enabled":true,"failureThreshold":12,"httpGet":{"path":"/health/startup","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"timeoutSeconds":3}` | Startup probe. Protects a slow-starting container from the liveness probe. |
| startupProbe.enabled | bool | `true` | Enable the startup probe. |
| startupProbe.failureThreshold | int | `12` | Consecutive failures before the probe is considered failed. |
| startupProbe.httpGet | object | `{"path":"/health/startup","port":"http"}` | HTTP handler for the probe. |
| startupProbe.httpGet.path | string | `"/health/startup"` | Health check path. |
| startupProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| startupProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| startupProbe.periodSeconds | int | `5` | Probe interval. |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout. |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| telemetry | object | `{"jsonLogs":false,"logFilter":"info","sentry":{"attachStacktraces":true,"breadcrumbLevel":"info","captureLevel":"error","debug":false,"dsn":"","enabled":false,"environment":"","httpTransactions":true,"maxBreadcrumbs":100,"release":"","sampleRate":1,"sendDefaultPii":false,"serverName":"","shutdownTimeoutSecs":2,"spanAttributes":false,"tracesSampleRate":0}}` | Logging and error reporting. The log settings are rendered under `telemetry` in the mounted `config.toml`; the Sentry DSN is a credential and goes to the Secret instead. The whole block is read once as the process boots rather than re-read from the mount, so a change to it takes effect on the next rollout. |
| telemetry.jsonLogs | bool | `false` | Emit one JSON object per record instead of human-readable lines (`telemetry.json_logs`). The image's default suits `docker run` on a terminal; a cluster shipping logs to a collector wants this on. |
| telemetry.logFilter | string | `"info"` | `RUST_LOG`-style filter deciding which records are emitted at all (`telemetry.log_filter`), for example `info,mp_stats_server=debug`. `RUST_LOG` set in the container outranks this — the one place in this configuration where a bare environment variable wins over the layered loader — so `extraEnv` is the override to reach for while a pod is misbehaving. It governs what reaches Sentry too: a record this filter drops never becomes an issue or a breadcrumb, whatever `sentry.captureLevel` says. |
| telemetry.sentry | object | `{"attachStacktraces":true,"breadcrumbLevel":"info","captureLevel":"error","debug":false,"dsn":"","enabled":false,"environment":"","httpTransactions":true,"maxBreadcrumbs":100,"release":"","sampleRate":1,"sendDefaultPii":false,"serverName":"","shutdownTimeoutSecs":2,"spanAttributes":false,"tracesSampleRate":0}` | Sentry error reporting and request tracing. **Off**, and wholly inert while it is: no client, no panic hook, no `tracing` layer and no HTTP middleware is installed, and nothing leaves the process.  Two things the chart deliberately does not do for you. It never reads the DSN, so it cannot name the ingest host in the NetworkPolicies. The defaults do happen to cover it — `networkPolicy.egress.https` permits TCP/443 to `0.0.0.0/0` minus private space — but a deployment that narrowed `networkPolicy.egress.cidr`, turned `egress.https` off, or replaced the CIDR rule with `networkPolicy.cilium.egress.toFQDNs` has to name the endpoint itself, and getting that wrong is silent: an SDK that cannot reach its endpoint queues events and then discards them, so the project stays empty, which reads as "no errors". And it configures nothing inside Sentry — the project, its quota and its server-side data-scrubbing rules are yours. |
| telemetry.sentry.attachStacktraces | bool | `true` | Attach a stack trace to events that carry none of their own (`telemetry.sentry.attach_stacktraces`). |
| telemetry.sentry.breadcrumbLevel | string | `"info"` | Least severe `tracing` level kept as a breadcrumb, the trail attached to the next issue (`telemetry.sentry.breadcrumb_level`). Records at or above `captureLevel` become issues instead, so this only ever describes the band below it. Quote `"off"`. |
| telemetry.sentry.captureLevel | string | `"error"` | Least severe `tracing` level reported as a Sentry issue (`telemetry.sentry.capture_level`). Quote `"off"` — unquoted, YAML reads it as the boolean `false`. This and `breadcrumbLevel` both sit *under* `logFilter`, so a record that filter drops never reaches Sentry either. |
| telemetry.sentry.debug | bool | `false` | Print the SDK's own diagnostics to stderr (`telemetry.sentry.debug`). For proving a DSN works, not for running. |
| telemetry.sentry.dsn | string | `""` | Ingest URL, `https://<key>@<host>/<project>`. A credential — the embedded key is a bearer token for the project's ingest endpoint — so it is delivered as a file in `MP_STATS_SECRETS_DIR` under `telemetry__sentry__dsn` and never written into the ConfigMap. It is the only credential this chart handles, and switching Sentry on is what gives the pod a secrets volume at all. Leave it empty and put that key in the Secret named by `existingSecret` to keep it out of `helm get values` entirely. |
| telemetry.sentry.enabled | bool | `false` | Initialise the Sentry client (`telemetry.sentry.enabled`). Off, every other key here is inert. On, the server refuses to boot without a DSN rather than starting with a reporter that reports nowhere, so this and `dsn` are set together. |
| telemetry.sentry.environment | string | `""` | Environment tag on every event (`telemetry.sentry.environment`). Empty lets the server derive it from the build: `production` for a release binary, which every published image is. Set it for anything in between, such as a staging cluster running that same binary. |
| telemetry.sentry.httpTransactions | bool | `true` | Record one transaction per request, named by the matched route rather than by the URI (`telemetry.sentry.http_transactions`). Whether a started transaction is kept is `tracesSampleRate`'s decision; this is the switch for a deployment that wants error reporting and no performance data at all. |
| telemetry.sentry.maxBreadcrumbs | int | `100` | How many breadcrumbs one event carries (`telemetry.sentry.max_breadcrumbs`). |
| telemetry.sentry.release | string | `""` | Release tag on every event (`telemetry.sentry.release`). Empty uses `mp-stats-legacy-viewer@v<version>`, spelled as the image tag spells it, which is what makes a regression attributable to a deploy — set this only to match a release you create in Sentry under a name of your own. |
| telemetry.sentry.sampleRate | float | `1` | Fraction of captured events actually sent (`telemetry.sentry.sample_rate`). A blunt volume cap — it drops whole issues, not repetitions of one — so leave it at `1` unless a quota forces otherwise. A value outside the range fails the boot. |
| telemetry.sentry.sendDefaultPii | bool | `false` | Send personally identifying data with every event: the client IP, the whole request header set — `Cookie` included — and the resolved user (`telemetry.sentry.send_default_pii`). **Off, and worth leaving off** — a reader's IP address is not what makes a crash report actionable, and Sentry is a third party for the purposes of whatever data policy this deployment publishes. It is two controls rather than one, besides: off is also what keeps the HTTP middleware redacting sensitive headers. |
| telemetry.sentry.serverName | string | `""` | Host tag on every event (`telemetry.sentry.server_name`). Empty reports none, which is the right answer here: the value would be one replica's pod name, gone by the time anyone reads the issue. |
| telemetry.sentry.shutdownTimeoutSecs | int | `2` | How long process exit waits for queued events to drain (`telemetry.sentry.shutdown_timeout_secs`). Paid on every pod shutdown, so it is time added to every rollout; keep it well inside `terminationGracePeriodSeconds`. |
| telemetry.sentry.spanAttributes | bool | `false` | Copy `tracing` span fields onto the Sentry span as attributes (`telemetry.sentry.span_attributes`). Off: the span fields here carry request paths, and a transaction is retained for longer than a log line. |
| telemetry.sentry.tracesSampleRate | float | `0` | Fraction of traces this server **starts** that are recorded (`telemetry.sentry.traces_sample_rate`). `0` starts none, which is the right figure for a static file server — a trace per asset request is volume without a question behind it — and it is what makes the feature free to switch on for error reporting alone. It does not take the server out of a trace that reaches it already sampled: an inbound `sentry-trace` header is continued whatever this says. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |

## Source Code

* <https://github.com/TimSchoenle/mp-stats-legacy-viewer>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |


----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
