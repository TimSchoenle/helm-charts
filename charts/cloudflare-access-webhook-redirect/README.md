# cloudflare-access-webhook-redirect

![Version: 2.0.10](https://img.shields.io/badge/Version-2.0.10-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: v0.3.11](https://img.shields.io/badge/AppVersion-v0.3.11-informational?style=flat-square)

A Helm chart for deploying the Cloudflare Access Webhook Redirect service. This service acts as an authentication proxy that validates requests using Cloudflare Access Service Auth tokens before forwarding them to target backend services.

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
| autoscaling.enabled | bool | `false` | Enable Horizontal Pod Autoscaler (HPA) |
| autoscaling.maxReplicas | int | `5` | Maximum replicas |
| autoscaling.minReplicas | int | `1` | Minimum replicas |
| autoscaling.targetCPUUtilizationPercentage | int | `80` | Target CPU utilization (%) |
| autoscaling.targetMemoryUtilizationPercentage | int | `80` | Target memory utilization (%) |
| fullnameOverride | string | `""` | Override the full release name |
| image.pullPolicy | string | `"IfNotPresent"` | Image pull policy |
| image.repository | string | `"timmi6790/cloudflare-access-webhook-redirect"` | Container image repository (e.g. docker.io/user/image) |
| image.tag | string | `"v0.3.11@sha256:d3df58915085a59e6fa4a503f092d1d18df4d0c481a0db0013183456f8b0aa9a"` | Container image tag (version) |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| ingress.annotations | object | `{}` | Additional ingress annotations Example:   cert-manager.io/cluster-issuer: letsencrypt-prod   nginx.ingress.kubernetes.io/rate-limit: "100" |
| ingress.enabled | bool | `false` | Enable ingress resource |
| ingress.hosts | list | `[]` | Host definitions for ingress Example:   - host: example.local     paths:       - path: /         pathType: Prefix |
| ingress.ingressClassName | string | `"nginx"` | Ingress class name (e.g. nginx) |
| ingress.tls | list | `[]` | TLS configuration for ingress Example:   - secretName: example-tls     hosts:       - example.local |
| livenessProbe.enabled | bool | `true` | Enable liveness probe |
| livenessProbe.failureThreshold | int | `3` | Failure threshold |
| livenessProbe.httpGet.path | string | `"/health"` | Health check path |
| livenessProbe.httpGet.port | string | `"http"` | Health check port |
| livenessProbe.initialDelaySeconds | int | `10` | Initial delay before probe starts |
| livenessProbe.periodSeconds | int | `10` | Probe frequency |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout |
| nameOverride | string | `""` | Override the chart name |
| networkPolicy.egress | list | `[]` | Egress rules |
| networkPolicy.enabled | bool | `false` | Enable Kubernetes NetworkPolicy |
| networkPolicy.ingress | list | `[]` | Ingress rules |
| networkPolicy.policyTypes | list | `["Ingress","Egress"]` | Policy types (Ingress/Egress) |
| nodeSelector | object | `{}` | Node selector labels for scheduling |
| podAnnotations | object | `{}` | Additional annotations for the Pod metadata |
| podDisruptionBudget.enabled | bool | `false` | Enable PodDisruptionBudget |
| podDisruptionBudget.maxUnavailable | int | `1` | Maximum unavailable pods |
| podDisruptionBudget.minAvailable | int | `1` | Minimum available pods |
| podLabels | object | `{}` | Additional labels for the Pod metadata |
| podSecurityContext.fsGroup | int | `10001` | Group ID for file system access |
| podSecurityContext.runAsNonRoot | bool | `true` | Run pod as non-root user |
| podSecurityContext.runAsUser | int | `10001` | User ID to run as |
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
| securityContext.allowPrivilegeEscalation | bool | `false` | Allow privilege escalation |
| securityContext.capabilities.drop | list | `["ALL"]` | Linux capabilities to drop |
| securityContext.readOnlyRootFilesystem | bool | `false` | Mount root filesystem as read-only |
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
| tolerations | list | `[]` | Tolerations for taints |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |
| volumeMounts | list | `[]` | Additional volume mounts (e.g., /cache) |
| volumes | list | `[]` | Additional volumes (e.g., cache, tmp) |

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

