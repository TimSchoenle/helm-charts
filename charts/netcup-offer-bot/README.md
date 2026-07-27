# netcup-offer-bot

![Version: 3.0.0](https://img.shields.io/badge/Version-3.0.0-informational?style=flat-square) ![AppVersion: v1.5.21](https://img.shields.io/badge/AppVersion-v1.5.21-informational?style=flat-square)

This chart deploys the Netcup Offer Bot, which monitors https://www.netcup-sonderangebote.de/ RSS feed and sends notifications to Discord webhooks when new offers are available.

> [!WARNING]
> This chart's latest major release changes the values contract. See
> [UPGRADING.md](https://github.com/TimSchoenle/helm-charts/blob/main/UPGRADING.md) before
> upgrading from an earlier major version.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A Discord webhook URL

## Get Repository Info

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
```

## Install Chart

```shell
helm install [RELEASE_NAME] timschoenle/netcup-offer-bot \
  --namespace [NAMESPACE] \
  --create-namespace \
  --set env.webHook="YOUR_DISCORD_WEBHOOK_URL"
```

## Upgrade Chart

```shell
helm upgrade [RELEASE_NAME] timschoenle/netcup-offer-bot \
  --namespace [NAMESPACE]
```

## Uninstall Chart

```shell
helm uninstall [RELEASE_NAME] --namespace [NAMESPACE]
```

## Configuration

The following table lists the configurable parameters of the chart and their default values.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| env.checkInterval | int | `180` | Interval in seconds between offer checks. |
| env.logLevel | string | `"info"` | Log level for the application. |
| env.sentryDns | string | `""` | Sentry DSN for error tracking. Leave empty to disable. |
| env.webHook | string | `""` | Webhook URL to send updates or notifications. Required unless `existingSecret` is set. |
| existingSecret | string | `""` | Name of an existing Secret holding the `webHook` key. When set, the chart does not create a Secret and `env.webHook` is ignored — which keeps the webhook URL out of `values.yaml` and out of the Helm release object. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| image.digest | string | `"sha256:29ca3ed4dfa9b3c6b03089fa3079b73cfac8eab3e2f25d736c4e730e5119919a"` | Image digest (`sha256:...`). Combined with `tag` rather than replacing it: the digest pins the pull, while `tag` stays on as the readable version marker. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timmi6790/netcup-offer-bot"` | The container image repository. |
| image.tag | string | `"v1.5.21"` | The container image tag. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| metrics.enabled | bool | `false` | Enable Prometheus metrics endpoint. |
| metrics.podMonitor | object | `{"enabled":true,"interval":"1m","labels":{},"scrapeTimeout":"30s"}` | PodMonitor configuration for Prometheus Operator integration. Renamed from `serviceMonitor`: the chart has always rendered a PodMonitor, and there is no Service to monitor. |
| metrics.podMonitor.enabled | bool | `true` | Create the PodMonitor. Requires the Prometheus Operator CRDs. |
| metrics.podMonitor.interval | string | `"1m"` | Metrics scrape interval (e.g., 1m, 30s). |
| metrics.podMonitor.labels | object | `{}` | Extra labels for the PodMonitor, e.g. the `release` label a Prometheus Operator instance selects on. |
| metrics.podMonitor.scrapeTimeout | string | `"30s"` | Timeout for metrics scraping (e.g., 30s). |
| metrics.port | int | `9184` | Port to expose metrics on. |
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
| persistence.data | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"10Mi","storageClassName":""}` | Configuration for persistent data storage. The bot keeps its seen-offer state on disk; disabling persistence means it re-announces every offer after a restart. |
| persistence.data.accessMode | string | `"ReadWriteOnce"` | The access mode for the persistent volume. `ReadWriteOnce` forces the Deployment to the `Recreate` update strategy: a rolling update would wedge, because the replacement pod cannot attach a volume the outgoing pod still holds. |
| persistence.data.annotations | object | `{}` | Annotations for the PersistentVolumeClaim, e.g. `helm.sh/resource-policy: keep`. |
| persistence.data.enabled | bool | `true` | Create a PersistentVolumeClaim. When disabled, an emptyDir is used instead. |
| persistence.data.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.data.size | string | `"10Mi"` | The storage size requested for the volume. |
| persistence.data.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. Ignored when `affinity` is set. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1000,"runAsGroup":1000,"runAsUser":1000}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `1000` | Group ID for file system access |
| podSecurityContext.runAsGroup | int | `1000` | Primary group ID to run as |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name |
| replicaCount | int | `1` | Number of replicas. The bot holds a ReadWriteOnce volume, so more than one replica will not schedule. |
| resources.limits | object | `{"memory":"20Mi"}` | Resource limits for the container. |
| resources.limits.memory | string | `"20Mi"` | Maximum allowed memory usage. |
| resources.requests | object | `{"cpu":"10m","memory":"15Mi"}` | Resource requests for the container. |
| resources.requests.cpu | string | `"10m"` | Minimum CPU requested by the container. The bot is idle between RSS polls; this only has to keep it out of BestEffort. Without a CPU request the pod is BestEffort and is the first thing evicted under node pressure. |
| resources.requests.memory | string | `"15Mi"` | Minimum guaranteed memory allocation. |
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |

## Examples

### Minimal Configuration

```yaml
env:
  webHook: "https://discord.com/api/webhooks/..."
  checkInterval: 180
```

### Production Setup with Metrics

```yaml
env:
  webHook: "https://discord.com/api/webhooks/..."
  sentryDns: "https://your-sentry-dsn@sentry.io/project"
  checkInterval: 300
  logLevel: info

metrics:
  enabled: true
  port: 9184
  podMonitor:
    enabled: true
    interval: 1m
    scrapeTimeout: 30s

resources:
  limits:
    memory: 20Mi
  requests:
    memory: 15Mi

persistence:
  data:
    size: 50Mi
```

### With Custom Resource Limits

```yaml
env:
  webHook: "https://discord.com/api/webhooks/..."
  checkInterval: 120
  logLevel: debug

resources:
  limits:
    memory: 30Mi
  requests:
    memory: 20Mi

persistence:
  data:
    accessMode: ReadWriteOnce
    size: 100Mi
```

## Persistence

The bot uses a persistent volume to store its state and track which offers have already been processed. This ensures that notifications aren't duplicated when the pod restarts.

The default storage size is 10Mi, which should be sufficient for most use cases.

## Source Code

* <https://github.com/TimSchoenle/netcup-offer-bot>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)

