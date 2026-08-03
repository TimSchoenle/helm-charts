# cloudflare-access-webhook-redirect

![Version: 3.0.3](https://img.shields.io/badge/Version-3.0.3-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: v0.3.21](https://img.shields.io/badge/AppVersion-v0.3.21-informational?style=flat-square)

A Helm chart for deploying the Cloudflare Access Webhook Redirect service. This service acts as an authentication proxy that validates requests using Cloudflare Access Service Auth tokens before forwarding them to target backend services.

> [!WARNING]
> This chart's latest major release changes the values contract. See
> [UPGRADING.md](https://github.com/TimSchoenle/helm-charts/blob/main/UPGRADING.md) before
> upgrading from an earlier major version.

## Use Case

This is particularly useful for protecting webhook endpoints or APIs that don't have built-in authentication, by leveraging Cloudflare Access for secure authentication and authorization.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A Cloudflare Access application with Service Auth credentials
- Target backend service to proxy requests to

## Get Repository Info

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
```

## Install Chart

```shell
helm install [RELEASE_NAME] timschoenle/cloudflare-access-webhook-redirect \
  --namespace [NAMESPACE] \
  --create-namespace \
  --set application.handler.targetBase="http://backend:8080" \
  --set application.cloudflareAccess.clientId="your-client-id" \
  --set application.cloudflareAccess.clientSecret="your-client-secret"
```

## Upgrade Chart

```shell
helm upgrade [RELEASE_NAME] timschoenle/cloudflare-access-webhook-redirect \
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
| affinity | object | `{}` | Pod affinity rules |
| application.cloudflareAccess.secretName | string | `""` | Existing secret name containing Cloudflare Access credentials Must contain client_id and client_secret keys |
| application.handler.paths | object | `{}` | Path configurations with allowed HTTP methods Example:   api/webhook:     - ALL   test:     - GET     - POST |
| application.handler.targetBase | string | `""` | Base URL for redirect targets |
| application.logLevel | string | `"info"` | Application log level |
| application.sentryDsn | string | `""` | Sentry DSN for error tracking (empty disables) |
| application.server.host | string | `"0.0.0.0"` | Server bind address |
| application.server.port | int | `8080` | HTTP server port |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| autoscaling.enabled | bool | `false` | Enable Horizontal Pod Autoscaler (HPA) |
| autoscaling.maxReplicas | int | `5` | Maximum replicas |
| autoscaling.minReplicas | int | `1` | Minimum replicas |
| autoscaling.targetCPUUtilizationPercentage | int | `80` | Target CPU utilization (%) |
| autoscaling.targetMemoryUtilizationPercentage | int | `80` | Target memory utilization (%) |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts (e.g., /cache) |
| extraVolumes | list | `[]` | Additional volumes (e.g., cache, tmp) |
| fullnameOverride | string | `""` | Override the full release name |
| image.pullPolicy | string | `""` | Image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timmi6790/cloudflare-access-webhook-redirect"` | Container image repository (e.g. docker.io/user/image) |
| image.tag | string | `"v0.3.21@sha256:e5607cedf22b71b8835146aaa1c3e274102888d3d85890224ace3bf088aa646a"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
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
| networkPolicy | object | `{"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"ingress":{"controller":{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring"}}}` | Network policy configuration |
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
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. The preset mounts the root filesystem read-only; a writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
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
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for taints |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |

## Cloudflare Access Configuration

### Option 1: Using Existing Secret

Create a Kubernetes secret with your Cloudflare Access Service Auth credentials:

```bash
kubectl create secret generic cloudflare-access-secret \
  --namespace [NAMESPACE] \
  --from-literal=client_id='your-client-id' \
  --from-literal=client_secret='your-client-secret'
```

Then reference it in your values:

```yaml
application:
  cloudflareAccess:
    secretName: "cloudflare-access-secret"
```

### Option 2: Inline Credentials (Not Recommended for Production)

```yaml
application:
  cloudflareAccess:
    clientId: "your-client-id"
    clientSecret: "your-client-secret"
```

## Path Configuration

Define which paths should be proxied and which HTTP methods are allowed:

```yaml
application:
  handler:
    paths:
      # Allow all HTTP methods for webhook endpoint
      api/webhook:
        - ALL

      # Allow specific methods
      api/data:
        - GET
        - POST

      # Multiple endpoints
      health:
        - GET
      metrics:
        - GET
```

Available HTTP methods: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`, or `ALL` for all methods.

## Examples

### Minimal Configuration

```yaml
application:
  handler:
    targetBase: "http://backend-service:8080"
    paths:
      api/webhook:
        - POST

  cloudflareAccess:
    clientId: "your-client-id-here"
    clientSecret: "your-client-secret-here"
```

### With Ingress and TLS

```yaml
application:
  handler:
    targetBase: "http://backend-service:8080"
    paths:
      api/webhook:
        - POST
      api/status:
        - GET

  cloudflareAccess:
    secretName: "cloudflare-access-secret"

ingress:
  enabled: true
  ingressClassName: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
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

### High Availability Setup

```yaml
replicaCount: 3

application:
  handler:
    targetBase: "http://backend-service:8080"
    paths:
      api/webhook:
        - POST
      api/events:
        - POST
        - GET

  cloudflareAccess:
    secretName: "cloudflare-access-secret"

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80

topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: cloudflare-access-webhook-redirect

podDisruptionBudget:
  enabled: true
  minAvailable: 2

resources:
  limits:
    cpu: 200m
    memory: 30Mi
  requests:
    cpu: 50m
    memory: 20Mi
```

### With Network Policy

```yaml
application:
  handler:
    targetBase: "http://backend-service:8080"
    paths:
      api/webhook:
        - ALL

  cloudflareAccess:
    secretName: "cloudflare-access-secret"

networkPolicy:
  enabled: true
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              name: ingress-nginx
      ports:
        - protocol: TCP
          port: 8080
  egress:
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: TCP
          port: 8080
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: TCP
          port: 443
```

### Production Setup with Monitoring

```yaml
replicaCount: 2

application:
  logLevel: info
  sentryDsn: "https://your-sentry-dsn@sentry.io/project"

  handler:
    targetBase: "http://backend-service:8080"
    paths:
      api/webhook:
        - POST
      api/data:
        - GET
        - POST

  cloudflareAccess:
    secretName: "cloudflare-access-secret"

resources:
  limits:
    cpu: 100m
    memory: 15Mi
  requests:
    cpu: 10m
    memory: 10Mi

startupProbe:
  enabled: true
  initialDelaySeconds: 0
  periodSeconds: 5
  failureThreshold: 30

livenessProbe:
  enabled: true
  initialDelaySeconds: 10
  periodSeconds: 10

readinessProbe:
  enabled: true
  initialDelaySeconds: 5
  periodSeconds: 5

podDisruptionBudget:
  enabled: true
  minAvailable: 1
```

## How It Works

1. Client sends a request to the service (e.g., via Ingress)
2. The service validates the request against the configured paths and HTTP methods
3. It uses Cloudflare Access Service Auth credentials to authenticate the request
4. If authenticated, the request is proxied to the target backend service
5. The response from the backend is returned to the client

This provides a zero-trust authentication layer for services that don't have built-in authentication.

## Security Considerations

- **Always use secrets** for Cloudflare Access credentials in production
- **Enable NetworkPolicy** to restrict ingress/egress traffic
- **Use TLS/HTTPS** via Ingress with proper certificates
- **Monitor logs** and consider enabling Sentry for error tracking
- **Set resource limits** to prevent resource exhaustion
- **Use PodDisruptionBudget** for high-availability deployments

## Source Code

* <https://github.com/TimSchoenle/cloudflare-access-webhook-redirect>
* <https://github.com/TimSchoenle/helm-charts>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle |  | <https://github.com/TimSchoenle> |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)

