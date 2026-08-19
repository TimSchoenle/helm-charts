# cloudflare-access-webhook-redirect

![Version: 5.0.1](https://img.shields.io/badge/Version-5.0.1-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: v1.2.1](https://img.shields.io/badge/AppVersion-v1.2.1-informational?style=flat-square)

A Helm chart for deploying the Cloudflare Access Webhook Redirect service. This service acts as an authentication proxy that validates requests using Cloudflare Access Service Auth tokens before forwarding them to target backend services.

Use it in front of a webhook receiver or an internal API that has no authentication of its
own: only the paths and methods you declare are forwarded, and each one is validated against
Cloudflare Access before it reaches the backend.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A Cloudflare Access application with Service Auth credentials
- The backend service to forward to, reachable from the pod
- The Gateway API CRDs and a `Gateway` to attach to, if `gateway.enabled=true`
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/cloudflare-access-webhook-redirect \
  --namespace [NAMESPACE] --create-namespace \
  --values values.yaml
```

with `values.yaml`:

```yaml
webhook:
  targetBase: http://backend-service:8080
  paths:
    "/api/webhook/.*":
      - POST

existingSecret: cloudflare-access-secret
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/cloudflare-access-webhook-redirect -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`.

## Credentials

Create the Secret yourself and reference it by name. **The keys are the configuration paths the
service reads, not free-form names** — the proxy takes the key out of the file name, so
`cloudflare__client_id` is required and `client_id` is not read at all:

```shell
kubectl create secret generic cloudflare-access-secret   --namespace [NAMESPACE]   --from-literal=cloudflare__client_id='...'   --from-literal=cloudflare__client_secret='...'
```

```yaml
existingSecret: cloudflare-access-secret
```

`cloudflare.clientId` / `.clientSecret` are accepted as an alternative and make the chart render
the Secret itself. That puts the credentials into `values.yaml` and into the Helm release
object, where anyone who can run `helm get values` can read them — use it for a throwaway
cluster, not for anything real. `existingSecret` wins if both are set.

Either way the credentials arrive as files in a projected volume, which the proxy watches: a
rotated Secret is picked up in place, with no rollout and no window in which the pod is serving
on a credential you have already revoked.

## Configuration

Everything the service reads is rendered into one `config.toml`, mounted as a ConfigMap and
pointed at by `WEBHOOK_REDIRECT_CONFIG`. Nothing is passed as an environment variable, and that
is deliberate: the loader **fails the boot on a key supplied by both the environment and a
file** rather than resolving it by precedence, and a value that lives in a file is one the
kubelet can rotate under a running process.

The values above cover the whole documented surface. `config` takes the raw TOML tree for
anything they do not, merged over the derived one:

```yaml
config:
  server:
    host: 127.0.0.1
```

and `configExtraToml` is appended verbatim for what the renderer cannot express, notably arrays
of tables.

Because the proxy rebuilds its client, path patterns, credentials and listener in place when a
mount changes, this chart publishes **no `checksum/*` pod annotations by default** — a
configuration change reloads rather than rolls. Set `configMount.rolloutOnChange: true` to make
it behave like an ordinary image bump instead. `telemetry.*` is installed once per process and
needs a restart either way.

## Declaring what gets forwarded

`webhook.paths` is an allowlist, keyed by a path **regex**, valued by the methods permitted on
it. Nothing outside it is proxied, so a backend endpoint you did not list stays unreachable
through this service even though `targetBase` points at the same host.

Patterns are anchored: `/webhook/.*` matches `/webhook/github` but not `/api/webhook/github`.

```yaml
webhook:
  targetBase: http://backend-service:8080
  paths:
    "/api/webhook/.*":   # every method
      - ALL
    "/api/data/.*":      # reads and writes
      - GET
      - POST
    "/health":           # reads only
      - GET
```

Methods: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, or `ALL`.

Keep the list as narrow as the caller actually needs. `ALL` on a path that only ever receives
webhooks also exposes `DELETE` on it.

## Request flow

1. A client reaches the service, usually through an Ingress.
2. The path and method are checked against `webhook.paths`; anything not declared is rejected
   here.
3. The request is authenticated with the Cloudflare Access Service Auth credentials.
4. It is forwarded to `targetBase`, and the backend's response is returned unchanged.

## Publishing it

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  hosts:
    - host: webhook.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: webhook-tls
      hosts:
        - webhook.example.com
```

Terminate TLS at the Ingress. The service speaks plain HTTP on port 8080 and is not meant to be
published without one.

## Running more than one replica

The service is stateless, so `replicaCount`, `autoscaling` and `podDisruptionBudget` behave the
way they do for any other Deployment and are all off or at 1 by default:

```yaml
replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

podDisruptionBudget:
  enabled: true
  minAvailable: 2

topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: cloudflare-access-webhook-redirect
```

A PodDisruptionBudget with `minAvailable` equal to `replicaCount` blocks node drains
indefinitely — keep it at least one below.

## Network policy

`networkPolicy.enabled` renders a default-deny policy per direction, with cluster DNS and
outbound HTTPS allowed out of the box. The HTTPS rule covers Cloudflare, but its destination
CIDR carves out RFC1918 space — **so the backend, which is almost always an in-cluster address,
is not reachable until you add a rule for it**. Without one the pod starts and every forwarded
request times out.

```yaml
networkPolicy:
  enabled: true
  ingress:
    controller:
      namespace: ingress-nginx                    # defaults to Traefik
      selector:
        app.kubernetes.io/name: ingress-nginx
  egress:
    customRules:
      - to:
          - podSelector:
              matchLabels:
                app.kubernetes.io/name: backend-service
        ports:
          - protocol: TCP
            port: 8080
```

Every rule you add must carry its own `to:`. A rule that lists only `ports:` is not a
restriction — the NetworkPolicy API reads a missing `to` as *any destination*, which includes
the cloud instance metadata endpoint at `169.254.169.254`.

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

The same release also documents the `networkPolicy` knobs the `common` library always accepted
but this chart never listed: `networkPolicy.extraIngress`, `networkPolicy.extraEgress`,
`networkPolicy.ingress.monitoring.namespaceSelector`, and a `ports` list on the monitoring and
controller rules. They are additions — nothing that worked before behaves differently.

### 3.x to 4.0

Chart 4.0 tracks the service's 1.0 release, which replaced its environment-only configuration
with the layered, file-first loader every chart in this repository now uses. The values that
described that environment are gone; a `helm upgrade` with 3.x values fails schema validation
naming the offending key rather than starting a pod on the defaults.

| Before | After |
|---|---|
| `application.server.host` | `server.host` |
| `application.server.port` | `server.port` |
| `application.handler.targetBase` | `webhook.targetBase` |
| `application.handler.paths` | `webhook.paths` |
| `application.logLevel` | `telemetry.logLevel` |
| `application.sentryDsn` | `telemetry.sentryDsn` |
| `application.cloudflareAccess.clientId` | `cloudflare.clientId` |
| `application.cloudflareAccess.clientSecret` | `cloudflare.clientSecret` |
| `application.cloudflareAccess.secretName` | `existingSecret` |

Two changes need work beyond a rename:

**Path keys are regexes and are anchored.** `api/webhook` matched a prefix before; write
`"/api/webhook/.*"` for the same reach, and quote it — it is no longer a bare key.

**An existing Secret has to be re-keyed.** The proxy reads each credential out of the *file
name*, so the keys are now `cloudflare__client_id` and `cloudflare__client_secret`. A Secret
still holding `client_id` / `client_secret` mounts cleanly and supplies nothing, and the proxy
refuses to boot naming the missing credential.

```shell
kubectl create secret generic cloudflare-access-secret   --namespace [NAMESPACE]   --from-literal=cloudflare__client_id="$(kubectl get secret cloudflare-access-secret -n [NAMESPACE] -o jsonpath='{.data.client_id}' | base64 -d)"   --from-literal=cloudflare__client_secret="$(kubectl get secret cloudflare-access-secret -n [NAMESPACE] -o jsonpath='{.data.client_secret}' | base64 -d)"   --dry-run=client -o yaml | kubectl apply -f -
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
    - hooks.example.com
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
        - name: cloudflare-access-webhook-redirect-canary
          port: 80
          weight: 10
        - name: RELEASE-NAME-cloudflare-access-webhook-redirect
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
    - hooks.example.com
  tls:
    enabled: true
    certificateRefs:
      - name: cloudflare-access-webhook-redirect-tls
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
| affinity | object | `{}` | Pod affinity rules |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| autoscaling.enabled | bool | `false` | Enable Horizontal Pod Autoscaler (HPA) |
| autoscaling.maxReplicas | int | `5` | Maximum replicas |
| autoscaling.minReplicas | int | `1` | Minimum replicas |
| autoscaling.targetCPUUtilizationPercentage | int | `80` | Target CPU utilization (%) |
| autoscaling.targetMemoryUtilizationPercentage | int | `80` | Target memory utilization (%) |
| cloudflare.clientId | string | `""` | Cloudflare Access service token client ID (`cloudflare.client_id`). Rendered into the chart's Secret and mounted as a file, so a rotation is picked up without a restart. Required unless `existingSecret` supplies it. |
| cloudflare.clientSecret | string | `""` | Cloudflare Access service token client secret (`cloudflare.client_secret`). Required unless `existingSecret` supplies it. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Extra configuration, expressed as the TOML tree of [the service's README](https://github.com/TimSchoenle/cloudflare-access-webhook-redirect#-configuration) (`server.host`, `webhook.target_base`, ...). Merged over everything the chart derives from the values above, so it can both extend and override them. Rendered into the mounted ConfigMap — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount.configDir | string | `"/etc/cloudflare-access-webhook-redirect/config"` | Directory the rendered `config.toml` is mounted at, passed as `WEBHOOK_REDIRECT_CONFIG`. |
| configMount.rolloutOnChange | bool | `false` | Add `checksum/*` pod annotations so a configuration change rolls the Deployment. Off by default, and deliberately so: the proxy watches the directories its configuration came from and rebuilds its client, path patterns, credentials and listener in place when the kubelet updates the mounted ConfigMap or Secret, which is strictly better than a rollout. Turn this on only if you want configuration changes to behave like an ordinary image bump. `telemetry.*` is installed once per process and needs a restart either way. |
| configMount.secretsDir | string | `"/etc/cloudflare-access-webhook-redirect/secrets"` | Directory the credential files are mounted at, passed as `WEBHOOK_REDIRECT_SECRETS_DIR`. |
| existingSecret | string | `""` | Name of an existing Secret holding the Cloudflare Access credentials, which keeps them out of `values.yaml` and out of the Helm release object. **Its keys are the configuration paths, not free-form names**: `cloudflare__client_id` and `cloudflare__client_secret`, because the file name is what the loader parses. Set, the chart renders no Secret of its own and `cloudflare.clientId` / `cloudflare.clientSecret` are ignored. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts (e.g., /cache) |
| extraVolumes | list | `[]` | Additional volumes (e.g., cache, tmp) |
| fullnameOverride | string | `""` | Override the full release name |
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
| image.pullPolicy | string | `""` | Image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timmi6790/cloudflare-access-webhook-redirect"` | Container image repository (e.g. docker.io/user/image) |
| image.tag | string | `"v1.2.1@sha256:39a93296a96f65176a8c1f06e9ee8748d82d438ff2a285fcceac5a4382e564a2"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| ingress.annotations | object | `{}` | Additional ingress annotations Example:   cert-manager.io/cluster-issuer: letsencrypt-prod   nginx.ingress.kubernetes.io/rate-limit: "100" |
| ingress.enabled | bool | `false` | Enable ingress resource |
| ingress.hosts | list | `[]` | Host definitions for ingress Example:   - host: example.local     paths:       - path: /         pathType: Prefix |
| ingress.ingressClassName | string | `"nginx"` | Ingress class name (e.g. nginx) |
| ingress.tls | list | `[]` | TLS configuration for ingress Example:   - secretName: example-tls     hosts:       - example.local |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| livenessProbe.enabled | bool | `true` | Enable liveness probe |
| livenessProbe.failureThreshold | int | `3` | Failure threshold |
| livenessProbe.httpGet.path | string | `"/health"` | Health check path |
| livenessProbe.httpGet.port | string | `"http"` | Health check port |
| livenessProbe.initialDelaySeconds | int | `10` | Initial delay before probe starts |
| livenessProbe.periodSeconds | int | `10` | Probe frequency |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout |
| nameOverride | string | `""` | Override the chart name |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"cilium":{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}},"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration |
| networkPolicy.cilium | object | `{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}}` | Cilium-only additions, used when `engine` is `cilium` or `both`. Everything above is translated into the CiliumNetworkPolicy automatically; these are the rules the portable API has no way to express.  Note that `extraIngress`, `extraEgress` and the per-section `customRules` above are *not* carried over: those are verbatim `networking.k8s.io/v1` rule objects and are not valid CNP. The fields below are their counterparts. |
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
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to the cluster DNS service |
| networkPolicy.egress.dns.namespaceSelector | object | `{"kubernetes.io/metadata.name":"kube-system"}` | Namespace selector for the DNS service |
| networkPolicy.egress.dns.podSelector | object | `{"k8s-app":"kube-dns"}` | Pod selector for the DNS service |
| networkPolicy.egress.enabled | bool | `true` | Enable egress rules |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. Defaults exclude RFC1918 private space and link-local 169.254.0.0/16, which covers the cloud instance metadata endpoint. |
| networkPolicy.egress.http | object | `{"enabled":false}` | HTTP configuration for egress |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to TCP/80 on the destinations described by `cidr`/`except` |
| networkPolicy.egress.https | object | `{"enabled":true}` | HTTPS configuration for egress. Also covers Sentry, which replaced the former dedicated `sentry` flag: that flag emitted a rule byte-identical to this one. |
| networkPolicy.egress.https.enabled | bool | `true` | Allow egress to TCP/443 on the destinations described by `cidr`/`except` |
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
| nodeSelector | object | `{}` | Node selector labels for scheduling |
| podAnnotations | object | `{}` | Additional annotations for the Pod metadata |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. Ignored when `affinity` is set. |
| podDisruptionBudget.enabled | bool | `false` | Enable PodDisruptionBudget |
| podDisruptionBudget.maxUnavailable | int | `1` | Maximum unavailable pods |
| podDisruptionBudget.minAvailable | int | `1` | Minimum available pods |
| podLabels | object | `{}` | Additional labels for the Pod metadata |
| podSecurityContext | object | `{"fsGroup":10001,"runAsGroup":10001,"runAsUser":10001}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `10001` | Group ID for file system access |
| podSecurityContext.runAsGroup | int | `10001` | Primary group ID to run as |
| podSecurityContext.runAsUser | int | `10001` | User ID to run as |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name |
| readinessProbe.enabled | bool | `true` | Enable readiness probe |
| readinessProbe.failureThreshold | int | `3` | Failure threshold |
| readinessProbe.httpGet.path | string | `"/health"` | Health check path |
| readinessProbe.httpGet.port | string | `"http"` | Health check port |
| readinessProbe.initialDelaySeconds | int | `5` | Initial delay before probe starts |
| readinessProbe.periodSeconds | int | `5` | Probe frequency |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout |
| replicaCount | int | `1` | Number of replicas to deploy |
| resources.limits.cpu | string | `"100m"` | Maximum CPU usage (e.g. 100m = 0.1 core) |
| resources.limits.memory | string | `"50Mi"` | Maximum memory usage (e.g. 64Mi) |
| resources.requests.cpu | string | `"10m"` | Guaranteed CPU request |
| resources.requests.memory | string | `"35Mi"` | Guaranteed memory request |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. The preset mounts the root filesystem read-only; a writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| server.host | string | `"0.0.0.0"` | Bind address (`server.host`). The application's own default is `127.0.0.1`, which in a container answers nothing; `0.0.0.0` is what makes the Service reach it. |
| server.port | int | `8080` | Bind port (`server.port`). Also the container port, the Service target and what every probe and NetworkPolicy rule is written against. |
| service.annotations | object | `{}` | Additional service annotations |
| service.port | int | `80` | Service port |
| service.type | string | `"ClusterIP"` | Kubernetes service type |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| startupProbe.enabled | bool | `true` | Enable startup probe |
| startupProbe.failureThreshold | int | `30` | Failure threshold |
| startupProbe.httpGet.path | string | `"/health"` | Health check path |
| startupProbe.httpGet.port | string | `"http"` | Health check port |
| startupProbe.initialDelaySeconds | int | `2` | Initial delay before probe starts |
| startupProbe.periodSeconds | int | `5` | Probe frequency |
| startupProbe.successThreshold | int | `1` | Success threshold |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| telemetry.logLevel | string | `"info"` | Log level (`telemetry.log_level`). |
| telemetry.sentryDsn | string | `""` | Sentry DSN (`telemetry.sentry_dsn`). Empty disables Sentry entirely. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for taints |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |
| webhook.paths | object | `{}` | Path regex to the methods allowed on it (`webhook.paths`). Patterns are anchored, so `/webhook/.*` matches `/webhook/github` but not `/api/webhook/github`. Methods are `ALL`, `GET`, `POST`, `PUT`, `PATCH` or `DELETE`. Required — a proxy with no allowed path forwards nothing. Example:   "/webhook/.*":     - ALL   "/api/public/.*":     - GET     - POST |
| webhook.targetBase | string | `""` | The Cloudflare Access protected service every allowed path is joined onto (`webhook.target_base`). Required. |

## Source Code

* <https://github.com/TimSchoenle/cloudflare-access-webhook-redirect>
* <https://github.com/TimSchoenle/helm-charts>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle |  | <https://github.com/TimSchoenle> |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
