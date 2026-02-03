# netcup-offer-bot

![Version: 2.0.6](https://img.shields.io/badge/Version-2.0.6-informational?style=flat-square) ![AppVersion: v1.6.0](https://img.shields.io/badge/AppVersion-v1.6.0-informational?style=flat-square)

This chart deploys the Netcup Offer Bot, which monitors https://www.netcup-sonderangebote.de/ RSS feed and sends notifications to Discord webhooks when new offers are available.

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
| env.checkInterval | int | `180` | Interval in seconds between offer checks. |
| env.logLevel | string | `"info"` | Log level for the application. |
| env.sentryDns | string | `""` | Sentry DSN for error tracking. Leave empty to disable. |
| env.webHook | string | `""` | Webhook URL to send updates or notifications. |
| image.pullPolicy | string | `"IfNotPresent"` | The image pull policy. |
| image.repository | string | `"timmi6790/netcup-offer-bot"` | The container image repository. |
| image.tag | string | `"v1.5.1@sha256:b8002f21875b5b6fe323a9684139e0cb4f856d89edb3d3c319130a573e8de97c"` | The container image tag. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| metrics.enabled | bool | `false` | Enable Prometheus metrics endpoint. |
| metrics.port | int | `9184` | Port to expose metrics on. |
| metrics.serviceMonitor | object | `{"interval":"1m","namespace":"monitoring","scrapeTimeout":"30s"}` | ServiceMonitor configuration for Prometheus Operator integration. |
| metrics.serviceMonitor.interval | string | `"1m"` | Metrics scrape interval (e.g., 1m, 30s). |
| metrics.serviceMonitor.namespace | string | `"monitoring"` | Namespace where monitoring tools are running |
| metrics.serviceMonitor.scrapeTimeout | string | `"30s"` | Timeout for metrics scraping (e.g., 30s). |
| networkPolicy | object | `{"egress":{"customRules":[],"dns":{"enabled":true},"enabled":true,"http":{"enabled":false},"https":{"enabled":true},"sentry":{"enabled":false}},"enabled":false,"ingress":{"controller":{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring"}}}` | Network policy configuration |
| networkPolicy.egress | object | `{"customRules":[],"dns":{"enabled":true},"enabled":true,"http":{"enabled":false},"https":{"enabled":true},"sentry":{"enabled":false}}` | Egress configuration |
| networkPolicy.egress.customRules | list | `[]` | Custom egress rules |
| networkPolicy.egress.dns | object | `{"enabled":true}` | DNS configuration for egress |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to DNS |
| networkPolicy.egress.enabled | bool | `true` | Enable egress rules |
| networkPolicy.egress.http | object | `{"enabled":false}` | HTTP configuration for egress |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to HTTP (TCP/80) |
| networkPolicy.egress.https | object | `{"enabled":true}` | HTTPS configuration for egress |
| networkPolicy.egress.https.enabled | bool | `true` | Allow egress to HTTPS (TCP/443) |
| networkPolicy.egress.sentry | object | `{"enabled":false}` | Sentry configuration for egress |
| networkPolicy.egress.sentry.enabled | bool | `false` | Allow egress to Sentry (HTTPS) |
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
| persistence.data | object | `{"accessMode":"ReadWriteOnce","size":"10Mi"}` | Configuration for persistent data storage. |
| persistence.data.accessMode | string | `"ReadWriteOnce"` | The access mode for the persistent volume. |
| persistence.data.size | string | `"10Mi"` | The storage size requested for the volume. |
| podSecurityContext.fsGroup | int | `1000` | Group ID for file system access |
| podSecurityContext.runAsNonRoot | bool | `true` | Run pod as non-root user |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name |
| resources.limits | object | `{"memory":"20Mi"}` | Resource limits for the container. |
| resources.limits.memory | string | `"20Mi"` | Maximum allowed memory usage. |
| resources.requests | object | `{"memory":"15Mi"}` | Resource requests for the container. |
| resources.requests.memory | string | `"15Mi"` | Minimum guaranteed memory allocation. |
| securityContext.allowPrivilegeEscalation | bool | `false` | Allow privilege escalation |
| securityContext.capabilities.drop | list | `["ALL"]` | Linux capabilities to drop |
| securityContext.readOnlyRootFilesystem | bool | `true` | Mount root filesystem as read-only |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
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
  serviceMonitor:
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

