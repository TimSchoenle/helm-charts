# mp-stats-legacy-viewer

![Version: 2.1.0](https://img.shields.io/badge/Version-2.1.0-informational?style=flat-square) ![AppVersion: v0.16.0](https://img.shields.io/badge/AppVersion-v0.16.0-informational?style=flat-square)

MP Stats Legacy Viewer

A single stateless HTTP deployment: no database, no credentials and nothing to persist. It
installs and serves on its ClusterIP with no configuration at all, so the only values most
installs touch are `ingress.*` and `resources`.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- An ingress controller, if `ingress.enabled=true`

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

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Extra configuration, expressed as the TOML tree of [the application's configuration reference](https://github.com/TimSchoenle/mp-stats-legacy-viewer/blob/main/docs/CONFIGURATION.md) (`server.bind_addr`, `converter.*`, ...). Merged over everything the chart derives from the values above, so it can both extend and override them. Rendered into the mounted ConfigMap — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount.configDir | string | `"/etc/mp-stats/config"` | Directory the rendered `config.toml` is mounted at, passed as `MP_STATS_CONFIG`. Pointing this at the mount **replaces** the `/config.toml` the image ships, so everything the image described — the bind address, `dist_dir`, `data_dir` — is restated by the values above. |
| configMount.secretsDir | string | `"/etc/mp-stats/secrets"` | Directory credential files would be mounted at, passed as `MP_STATS_SECRETS_DIR`. Neither binary reads a secret today, so nothing is mounted and the variable is not set; the value is here for an operator adding one through `extraVolumes`. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timschoenle/mp-stats-legacy-viewer"` | The container image repository. |
| image.tag | string | `"v0.16.0@sha256:3bd63be239ea5a290c59bdc9527d0869e25b7a6adb6f6c3ab1b360e3a7ec74a4"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Useful for configuring ingress controllers (e.g., cert-manager, rate limits). |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. Set to `true` to expose the service externally via Ingress. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Each host defines rules for routing external traffic. Example: ```yaml hosts:   - host: s3.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). Should match your cluster’s ingress controller configuration. |
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
| networkPolicy | object | `{"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"ingress":{"controller":{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring"}}}` | Network policy configuration |
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
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring"}}` | Ingress configuration |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}}` | Ingress Controller configuration |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from Ingress Controller |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace where Ingress Controller is running (default: traefik) |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector for Ingress Controller (default: Traefik label) |
| networkPolicy.ingress.customRules | list | `[]` | Custom ingress rules |
| networkPolicy.ingress.enabled | bool | `true` | Enable ingress rules |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring"}` | Monitoring configuration for ingress |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from monitoring namespace |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where monitoring tools are running |
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
| resources.limits | object | `{"memory":"100Mi"}` | Resource limits define the maximum resources the container can use. |
| resources.limits.memory | string | `"100Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"25m","memory":"100Mi"}` | Resource requests define the guaranteed resources reserved for the container. |
| resources.requests.cpu | string | `"25m"` | Minimum CPU requested by the container. Serves rendered pages; modest but non-trivial CPU on request. Without a CPU request the pod is BestEffort and is the first thing evicted under node pressure. |
| resources.requests.memory | string | `"100Mi"` | Minimum memory requested by the container. |
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. A writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
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
| service.port | int | `80` | Port that the Kubernetes Service will expose. Typically maps to `application.server.port`. |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
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
