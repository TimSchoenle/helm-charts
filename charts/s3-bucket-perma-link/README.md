# s3-bucket-perma-link

![Version: 4.0.0](https://img.shields.io/badge/Version-4.0.0-informational?style=flat-square) ![AppVersion: v1.0.0](https://img.shields.io/badge/AppVersion-v1.0.0-informational?style=flat-square)

This chart deploys a simple web server that provides permanent links to specific S3 bucket resources. It allows you to define static URL paths that always point to specific files in your S3 buckets.

The point is a URL that never changes while the object behind it does: `/latest-report` keeps
working when the file it maps to is replaced, and the bucket layout stays private. The service
holds the credentials and fetches the object itself, so nothing is signed into a link and no
bucket has to be made public.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- An S3-compatible bucket and a credential pair that can read it
- An ingress controller, or the Gateway API CRDs and a `Gateway`, if the links are meant to
  work outside the cluster
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

Create the credential Secret first — the chart never takes the keys as values:

```shell
kubectl create secret generic s3-credentials   --namespace [NAMESPACE]   --from-literal=s3__access_key='...'   --from-literal=s3__secret_key='...'
```

**The key names are the configuration paths the service reads, not free-form names.** The
credential arrives as a file and the service takes the key out of the file *name*, so
`s3__access_key` is required; a Secret spelled any other way mounts cleanly, supplies nothing,
and the service refuses to boot naming the missing credential.

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/s3-bucket-perma-link   --namespace [NAMESPACE] --create-namespace   --values values.yaml
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/s3-bucket-perma-link -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`.

## Mapping paths to objects

`bucket.entries` is the whole configuration: one entry per URL path, naming the bucket and the
object key it resolves to.

```yaml
bucket:
  entries:
    latest-report:
      bucket: company-reports
      object: 2024/q4-report.pdf
    "guides/user-guide":
      bucket: documentation
      object: guides/user-guide.pdf

s3:
  host: s3.eu-central-1.amazonaws.com
  region: eu-central-1

existingSecret: s3-credentials
```

`GET /latest-report` then serves `2024/q4-report.pdf` out of `company-reports`. Paths are
declared, not derived — a request for anything not listed here is not proxied, so the bucket
cannot be walked through this service.

Changing an entry is a values change and no rollout: the service watches its configuration
directory and rebuilds its bucket clients in place. The URL is unaffected either way.

## Configuration

Everything the service reads is rendered into one `config.toml`, mounted as a ConfigMap and
pointed at by `S3_PERMA_LINK_CONFIG`. Nothing is passed as an environment variable, and that is
deliberate: the loader **fails the boot on a key supplied by both the environment and a file**
rather than resolving it by precedence, and a value that lives in a file is one the kubelet can
rotate under a running process — which is what lets a rotated S3 credential take effect without
a restart.

The values above cover the whole documented surface. `config` takes the raw TOML tree for
anything they do not, merged over the derived one, and `configExtraToml` is appended verbatim
for what the renderer cannot express, notably arrays of tables.

Because the service rebuilds itself when a mount changes, this chart publishes **no
`checksum/*` pod annotations by default** — a configuration change reloads rather than rolls.
Set `configMount.rolloutOnChange: true` to make it behave like an ordinary image bump instead.
`telemetry.*` is installed once per process and needs a restart either way.

`s3.accessKey` / `s3.secretKey` are accepted as an alternative to `existingSecret` and make the
chart render the Secret itself. That puts the credentials into `values.yaml` and into the Helm
release object, where anyone who can run `helm get values` can read them — use it for a
throwaway cluster, not for anything real. `existingSecret` wins if both are set.

## Non-AWS endpoints

`s3.host` takes any S3-compatible API endpoint, including a port. `region` is still required —
most implementations only use it to build the request signature, but the signature is rejected
if it disagrees with what the server expects.

```yaml
s3:
  host: minio.example.com:9000
  region: us-east-1

existingSecret: s3-credentials
```

## Upgrading

### 3.x to 4.0

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


### 2.x to 3.0

Chart 3.0 tracks the service's 1.0 release, which replaced its environment-only configuration
with the layered, file-first loader every chart in this repository now uses. The values that
described that environment are gone; a `helm upgrade` with 2.x values fails schema validation
naming the offending key rather than starting a pod on the defaults.

| Before | After |
|---|---|
| `application.server.host` | `server.host` |
| `application.server.port` | `server.port` |
| `application.s3.host` | `s3.host` |
| `application.s3.region` | `s3.region` |
| `application.s3.accessKey` | `s3.accessKey` |
| `application.s3.secretKey` | `s3.secretKey` |
| `application.s3.secretName` | `existingSecret` |
| `application.handler.entries` | `bucket.entries` |
| `application.logLevel` | `telemetry.logLevel` |
| `application.sentryDsn` | `telemetry.sentryDsn` |

Two changes need work beyond a rename:

**Entries are mappings, not `"bucket,object"` strings.**

```yaml
# before
application:
  handler:
    entries:
      latest-report:
        - "company-reports,2024/q4-report.pdf"

# after
bucket:
  entries:
    latest-report:
      bucket: company-reports
      object: 2024/q4-report.pdf
```

**An existing Secret has to be re-keyed** to `s3__access_key` and `s3__secret_key`:

```shell
kubectl create secret generic s3-credentials   --namespace [NAMESPACE]   --from-literal=s3__access_key="$(kubectl get secret s3-credentials -n [NAMESPACE] -o jsonpath='{.data.access_key}' | base64 -d)"   --from-literal=s3__secret_key="$(kubectl get secret s3-credentials -n [NAMESPACE] -o jsonpath='{.data.secret_key}' | base64 -d)"   --dry-run=client -o yaml | kubectl apply -f -
```

## Publishing it

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  hosts:
    - host: files.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: files-tls
      hosts:
        - files.example.com
```

Anyone who can reach the Ingress can fetch every mapped object — the service has no
authentication of its own. Put it behind one, or keep it internal, if the objects are not meant
to be public.

With `networkPolicy.enabled`, outbound HTTPS is allowed by default — enough for AWS and any
other endpoint on a public address. An in-cluster or otherwise private endpoint (a MinIO in the
next namespace, say) falls inside the RFC1918 range that `networkPolicy.egress.except` carves
out, and needs a rule of its own:

```yaml
networkPolicy:
  enabled: true
  egress:
    customRules:
      - to:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: minio
        ports:
          - protocol: TCP
            port: 9000
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
    - links.example.com
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
        - name: s3-bucket-perma-link-canary
          port: 80
          weight: 10
        - name: RELEASE-NAME-s3-bucket-perma-link
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
    - links.example.com
  tls:
    enabled: true
    certificateRefs:
      - name: s3-bucket-perma-link-tls
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
| bucket.entries | object | `{}` | One entry per permanent link (`bucket.entries`), keyed by the request path it is served at and valued by the bucket and object key it resolves to. Required — a server with no entry serves nothing. Example: ```yaml entries:   "docs/handbook":     bucket: media     object: handbook.pdf   changelog:     bucket: media     object: releases/CHANGELOG.md ``` |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Extra configuration, expressed as the TOML tree of [the service's README](https://github.com/TimSchoenle/s3-bucket-perma-link#configuration) (`server.host`, `bucket.entries`, ...). Merged over everything the chart derives from the values above, so it can both extend and override them. Rendered into the mounted ConfigMap — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount.configDir | string | `"/etc/s3-bucket-perma-link/config"` | Directory the rendered `config.toml` is mounted at, passed as `S3_PERMA_LINK_CONFIG`. |
| configMount.rolloutOnChange | bool | `false` | Add `checksum/*` pod annotations so a configuration change rolls the Deployment. Off by default, and deliberately so: the service watches the directories its configuration came from and rebuilds its bucket clients and listener in place when the kubelet updates the mounted ConfigMap or Secret, which is strictly better than a rollout. Turn this on only if you want configuration changes to behave like an ordinary image bump. `telemetry.*` is installed once per process and needs a restart either way. |
| configMount.secretsDir | string | `"/etc/s3-bucket-perma-link/secrets"` | Directory the credential files are mounted at, passed as `S3_PERMA_LINK_SECRETS_DIR`. |
| existingSecret | string | `""` | Name of an existing Secret holding the S3 credentials, which keeps them out of `values.yaml` and out of the Helm release object. **Its keys are the configuration paths, not free-form names**: `s3__access_key` and `s3__secret_key`, because the file name is what the loader parses. Set, the chart renders no Secret of its own and `s3.accessKey` / `s3.secretKey` are ignored. |
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
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timmi6790/s3-bucket-perma-link"` | The container image repository. |
| image.tag | string | `"v1.0.0@sha256:eb402090337f7123a489e0a5f386d6e5e89f587e6d48d1df403b8d3c827bbdbb"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Useful for configuring ingress controllers (e.g., cert-manager, rate limits). |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. Set to `true` to expose the service externally via Ingress. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Each host defines rules for routing external traffic. Example: ```yaml hosts:   - host: s3.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). Should match your cluster’s ingress controller configuration. |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: s3-cert     hosts:       - s3.example.com ``` |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| livenessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":10,"timeoutSeconds":5}` | Liveness probe. Restarts the container when it stops responding. |
| livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| livenessProbe.failureThreshold | int | `3` | Consecutive failures before the probe is considered failed. |
| livenessProbe.httpGet | object | `{"path":"/health","port":"http"}` | HTTP handler for the probe. |
| livenessProbe.httpGet.path | string | `"/health"` | Health check path. |
| livenessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| livenessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| livenessProbe.periodSeconds | int | `10` | Probe interval. |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
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
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Readiness probe. Removes the pod from the Service while it cannot serve traffic. |
| readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| readinessProbe.failureThreshold | int | `3` | Consecutive failures before the probe is considered failed. |
| readinessProbe.httpGet | object | `{"path":"/health","port":"http"}` | HTTP handler for the probe. |
| readinessProbe.httpGet.path | string | `"/health"` | Health check path. |
| readinessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| readinessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| readinessProbe.periodSeconds | int | `5` | Probe interval. |
| readinessProbe.successThreshold | int | `1` | Consecutive successes before the pod is considered ready. |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout. |
| replicaCount | int | `1` | Number of application replicas. |
| resources.limits | object | `{"memory":"20Mi"}` | Resource limits define the maximum resources the container can use. |
| resources.limits.memory | string | `"20Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"10m","memory":"15Mi"}` | Resource requests define the guaranteed resources reserved for the container. |
| resources.requests.cpu | string | `"10m"` | Minimum CPU requested by the container. The service proxies small objects and is mostly IO-bound. Without a CPU request the pod is BestEffort and is the first thing evicted under node pressure. |
| resources.requests.memory | string | `"15Mi"` | Minimum memory requested by the container. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| s3.accessKey | string | `""` | S3 access key (`s3.access_key`). Rendered into the chart's Secret and mounted as a file, so a rotation is picked up without a restart. Required unless `existingSecret` supplies it. |
| s3.host | string | `"s3.amazon.com"` | S3-compatible API endpoint (`s3.host`), e.g. `s3.eu-central-1.amazonaws.com` or `minio.example.com`. |
| s3.region | string | `"eu-central-1"` | Region identifier used when signing requests (`s3.region`). |
| s3.secretKey | string | `""` | S3 secret key (`s3.secret_key`). Required unless `existingSecret` supplies it. |
| securityContext | object | `{}` | Container security context, merged over the preset. A writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| server.host | string | `"0.0.0.0"` | Bind address (`server.host`). `0.0.0.0` is what makes the Service reach the listener. |
| server.port | int | `8080` | Bind port (`server.port`). Also the container port, the Service target and what every probe and NetworkPolicy rule is written against. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. Typically maps to `application.server.port`. |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| startupProbe | object | `{"enabled":true,"failureThreshold":12,"httpGet":{"path":"/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"timeoutSeconds":3}` | Startup probe. Protects a slow-starting container from the liveness probe. |
| startupProbe.enabled | bool | `true` | Enable the startup probe. |
| startupProbe.failureThreshold | int | `12` | Consecutive failures before the probe is considered failed. |
| startupProbe.httpGet | object | `{"path":"/health","port":"http"}` | HTTP handler for the probe. |
| startupProbe.httpGet.path | string | `"/health"` | Health check path. |
| startupProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| startupProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| startupProbe.periodSeconds | int | `5` | Probe interval. |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout. |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| telemetry.logLevel | string | `"info"` | Log level (`telemetry.log_level`). |
| telemetry.sentryDsn | string | `""` | Sentry DSN (`telemetry.sentry_dsn`). Empty disables Sentry entirely. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |

## Source Code

* <https://github.com/timschoenle/s3-bucket-perma-link>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |


----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
