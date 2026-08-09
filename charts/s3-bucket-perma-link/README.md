# s3-bucket-perma-link

![Version: 3.0.0](https://img.shields.io/badge/Version-3.0.0-informational?style=flat-square) ![AppVersion: v1.0.0](https://img.shields.io/badge/AppVersion-v1.0.0-informational?style=flat-square)

This chart deploys a simple web server that provides permanent links to specific S3 bucket resources. It allows you to define static URL paths that always point to specific files in your S3 buckets.

The point is a URL that never changes while the object behind it does: `/latest-report` keeps
working when the file it maps to is replaced, and the bucket layout stays private. The service
holds the credentials and fetches the object itself, so nothing is signed into a link and no
bucket has to be made public.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- An S3-compatible bucket and a credential pair that can read it
- An ingress controller, if the links are meant to work outside the cluster

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
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. |
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
