# portfolio

![Version: 1.3.8](https://img.shields.io/badge/Version-1.3.8-informational?style=flat-square) ![AppVersion: v1.12.10](https://img.shields.io/badge/AppVersion-v1.12.10-informational?style=flat-square)

Personal portfolio built with Next.js.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- GitHub Personal Access Token

## Get Repository Info

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
```

## Install Chart

```shell
helm install [RELEASE_NAME] timschoenle/portfolio \
  --namespace [NAMESPACE] \
  --create-namespace \
  --set application.github.token="your-github-token"
```

## Upgrade Chart

```shell
helm upgrade [RELEASE_NAME] timschoenle/portfolio \
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
| affinity | object | `{}` | Affinity rules for pod assignment |
| application.github | object | `{"secretName":""}` | GitHub configuration for the Portfolio application. The GITHUB_TOKEN is required for the application to function correctly. |
| application.github.secretName | string | `""` | Name of an existing Kubernetes Secret containing the GitHub token. The secret must include a `GITHUB_TOKEN` field. If not provided, a secret will be created from the `token` field below. |
| application.healthCheck | object | `{"liveness":{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":10,"successThreshold":1,"timeoutSeconds":5},"path":"/api/health","readiness":{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3},"startup":{"failureThreshold":12,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}}` | Health check probe configuration. Next.js Portfolio application exposes health checks on /api/health. |
| application.healthCheck.liveness | object | `{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":10,"successThreshold":1,"timeoutSeconds":5}` | Liveness probe configuration. Detects if the container needs to be restarted. |
| application.healthCheck.liveness.failureThreshold | int | `3` | Minimum consecutive failures for the probe to be considered failed. |
| application.healthCheck.liveness.initialDelaySeconds | int | `1` | Number of seconds after the container has started before liveness probe is initiated. |
| application.healthCheck.liveness.periodSeconds | int | `10` | How often (in seconds) to perform the probe. |
| application.healthCheck.liveness.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful. |
| application.healthCheck.liveness.timeoutSeconds | int | `5` | Number of seconds after which the probe times out. |
| application.healthCheck.path | string | `"/api/health"` | Path for health check endpoint. |
| application.healthCheck.readiness | object | `{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Readiness probe configuration. Detects if the container is ready to serve traffic. |
| application.healthCheck.readiness.failureThreshold | int | `3` | Minimum consecutive failures for the probe to be considered failed. |
| application.healthCheck.readiness.initialDelaySeconds | int | `1` | Number of seconds after the container has started before readiness probe is initiated. |
| application.healthCheck.readiness.periodSeconds | int | `5` | How often (in seconds) to perform the probe. |
| application.healthCheck.readiness.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful. |
| application.healthCheck.readiness.timeoutSeconds | int | `3` | Number of seconds after which the probe times out. |
| application.healthCheck.startup | object | `{"failureThreshold":12,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Startup probe configuration. Protects slow starting containers from being killed by liveness probe. |
| application.healthCheck.startup.failureThreshold | int | `12` | Minimum consecutive failures for the probe to be considered failed. |
| application.healthCheck.startup.initialDelaySeconds | int | `1` | Number of seconds after the container has started before startup probe is initiated. |
| application.healthCheck.startup.periodSeconds | int | `5` | How often (in seconds) to perform the probe. |
| application.healthCheck.startup.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful. |
| application.healthCheck.startup.timeoutSeconds | int | `3` | Number of seconds after which the probe times out. |
| application.port | int | `3000` | Port number the Next.js application listens on. Next.js standalone server defaults to 3000. |
| image.pullPolicy | string | `"IfNotPresent"` | Kubernetes image pull policy. Determines when the image should be pulled from the registry. |
| image.repository | string | `"timschoenle/portfolio"` | Container image repository where the Portfolio application image is stored. Points to Docker Hub timschoenle/portfolio. |
| image.tag | string | `"v1.12.10@sha256:dedb7ea4f92e9ce5509ae50f35785bdc8c7b36a10b20f49bf8079adc445b02ec"` | Container image tag to deploy. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Useful for configuring ingress controllers (e.g., cert-manager, rate limits). Example: ```yaml annotations:   cert-manager.io/cluster-issuer: "letsencrypt-prod"   nginx.ingress.kubernetes.io/ssl-redirect: "true" ``` |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. Set to `true` to expose the service externally via Ingress. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Each host defines rules for routing external traffic. Example: ```yaml hosts:   - host: portfolio.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). Should match your cluster's ingress controller configuration. |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: portfolio-tls     hosts:       - portfolio.example.com ``` |
| nodeSelector | object | `{}` | Node selector for pod assignment |
| podAnnotations | object | `{}` | Additional annotations to add to the pod |
| podLabels | object | `{}` | Additional labels to add to the pod |
| podSecurityContext.fsGroup | int | `65532` | Group ID for file system access |
| podSecurityContext.fsGroupChangePolicy | string | `"OnRootMismatch"` | Change the fsGroup of the pod for Security Context Constraints. |
| podSecurityContext.runAsGroup | int | `65532` | Group ID for file system access |
| podSecurityContext.runAsNonRoot | bool | `true` | Run pod as non-root user |
| podSecurityContext.runAsUser | int | `65532` | User ID to run as. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name |
| resources.limits | object | `{"cpu":"500m","memory":"256Mi"}` | Resource limits define the maximum resources the container can use. Next.js applications typically require more memory than simple web servers. |
| resources.limits.cpu | string | `"500m"` | Maximum CPU allocation for the container. |
| resources.limits.memory | string | `"256Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"100m","memory":"128Mi"}` | Resource requests define the guaranteed resources reserved for the container. |
| resources.requests.cpu | string | `"100m"` | Minimum CPU requested by the container. |
| resources.requests.memory | string | `"128Mi"` | Minimum memory requested by the container. |
| securityContext.allowPrivilegeEscalation | bool | `false` | Allow privilege escalation |
| securityContext.capabilities.drop | list | `["ALL"]` | Linux capabilities to drop |
| securityContext.readOnlyRootFilesystem | bool | `false` | Mount root filesystem as read-only. Next.js ISR requires write access to update prerender cache. Set to true only if your app doesn't use ISR. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. This port is mapped to the application container port (3000). |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| tolerations | list | `[]` | Tolerations for pod assignment |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |

## GitHub Token Configuration

The Portfolio application requires a GitHub token to function correctly. You can provide this in two ways:

### Option 1: Create a Kubernetes Secret (Recommended)

Create a secret manually before installing the chart:

```bash
kubectl create secret generic portfolio-github \
  --namespace [NAMESPACE] \
  --from-literal=GITHUB_TOKEN='your-github-token'
```

Then reference it in your values:

```yaml
application:
  github:
    secretName: "portfolio-github"
```

### Option 2: Provide Token Inline

```yaml
application:
  github:
    token: "your-github-token"
```

> [!WARNING]
> This creates the secret automatically but the token will be visible in your values file.
> Use Option 1 for production deployments.

## Examples

### Minimal Configuration

```yaml
application:
  github:
    secretName: "portfolio-github"
```

### With Ingress and TLS

```yaml
application:
  github:
    secretName: "portfolio-github"

ingress:
  enabled: true
  ingressClassName: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
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

### Production Configuration with Resource Limits

```yaml
application:
  github:
    secretName: "portfolio-github"
  logLevel: info
  sentryDsn: "https://your-sentry-dsn@sentry.io/project"

resources:
  limits:
    cpu: 1000m
    memory: 512Mi
  requests:
    cpu: 200m
    memory: 256Mi

ingress:
  enabled: true
  ingressClassName: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/rate-limit: "100"
  hosts:
    - host: portfolio.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: portfolio-tls
      hosts:
        - portfolio.example.com

podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "3000"
  prometheus.io/path: "/api/metrics"
```

### High Availability Configuration

```yaml
application:
  github:
    secretName: "portfolio-github"

resources:
  limits:
    cpu: 1000m
    memory: 512Mi
  requests:
    cpu: 250m
    memory: 256Mi

topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: portfolio

affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: portfolio
          topologyKey: kubernetes.io/hostname
```

## Health Checks

The chart configures comprehensive health checks using the `/api/health` endpoint:

- **Startup Probe**: Protects slow starting containers (up to 60 seconds)
- **Liveness Probe**: Restarts unhealthy containers
- **Readiness Probe**: Controls traffic routing to ready pods

All probes are fully configurable via values:

```yaml
application:
  healthCheck:
    path: /api/health
    startup:
      initialDelaySeconds: 10
      periodSeconds: 5
      failureThreshold: 12
    liveness:
      initialDelaySeconds: 30
      periodSeconds: 10
      failureThreshold: 3
    readiness:
      initialDelaySeconds: 10
      periodSeconds: 5
      failureThreshold: 3
```

## Security

The chart follows security best practices:

- Runs as non-root user (UID 1000)
- Read-only root filesystem
- No privilege escalation
- Service account token not automounted by default
- Drop all capabilities

Writable volumes are provided only where necessary:
- `/tmp` - Temporary files
- `/app/.next/cache` - Next.js build cache

## Resource Recommendations

Based on the Next.js application characteristics:

| Environment | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-------------|-------------|-----------|----------------|--------------|
| Development | 100m        | 500m      | 128Mi          | 256Mi        |
| Production  | 200m        | 1000m     | 256Mi          | 512Mi        |
| High Traffic| 500m        | 2000m     | 512Mi          | 1Gi          |

## Troubleshooting

### Pod not starting

Check if the GitHub token is correctly configured:

```bash
kubectl get secret [SECRET_NAME] -n [NAMESPACE] -o jsonpath='{.data.GITHUB_TOKEN}' | base64 -d
```

### Health check failures

View pod logs to diagnose:

```bash
kubectl logs -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
```

Check health endpoint directly:

```bash
kubectl port-forward -n [NAMESPACE] svc/[SERVICE_NAME] 8080:80
curl http://localhost:8080/api/health
```

## Source Code

* <https://github.com/TimSchoenle/Portfolio>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
