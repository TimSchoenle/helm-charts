# portfolio

![Version: 2.0.0](https://img.shields.io/badge/Version-2.0.0-informational?style=flat-square) ![AppVersion: v2.0.0](https://img.shields.io/badge/AppVersion-v2.0.0-informational?style=flat-square)

Personal portfolio built with Rust (Yew frontend, Axum server).

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+

## Get Repository Info

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
```

## Install Chart

```shell
helm install [RELEASE_NAME] timschoenle/portfolio \
  --namespace [NAMESPACE] \
  --create-namespace
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
| application.healthCheck | object | `{"liveness":{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":10,"successThreshold":1,"timeoutSeconds":5},"path":"/api/health","readiness":{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3},"startup":{"failureThreshold":12,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}}` | Health check probe configuration. The Portfolio application exposes a health check on /api/health. |
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
| application.logLevel | string | `"info"` | Log verbosity passed to the application as `RUST_LOG`. Accepts standard tracing/env_logger directives (e.g. `info`, `debug`, `warn`). |
| application.port | int | `8080` | Port number the application listens on (exposed to the container as `PORT`). The Axum server binds to 0.0.0.0 on this port. |
| image.pullPolicy | string | `"IfNotPresent"` | Kubernetes image pull policy. Determines when the image should be pulled from the registry. |
| image.repository | string | `"timschoenle/portfolio"` | Container image repository where the Portfolio application image is stored. Points to Docker Hub timschoenle/portfolio. |
| image.tag | string | `"v2.0.0"` | Container image tag to deploy. The digest is pinned by Renovate once the v2.0.0 image is published. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Useful for configuring ingress controllers (e.g., cert-manager, rate limits). Example: ```yaml annotations:   cert-manager.io/cluster-issuer: "letsencrypt-prod"   nginx.ingress.kubernetes.io/ssl-redirect: "true" ``` |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. Set to `true` to expose the service externally via Ingress. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Each host defines rules for routing external traffic. Example: ```yaml hosts:   - host: portfolio.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). Should match your cluster's ingress controller configuration. |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: portfolio-tls     hosts:       - portfolio.example.com ``` |
| networkPolicy | object | `{"egress":{"customRules":[],"dns":{"enabled":true},"enabled":true,"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"ingress":{"controller":{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring"}}}` | Network policy configuration |
| networkPolicy.egress | object | `{"customRules":[],"dns":{"enabled":true},"enabled":true,"http":{"enabled":false},"https":{"enabled":true}}` | Egress configuration |
| networkPolicy.egress.customRules | list | `[]` | Custom egress rules |
| networkPolicy.egress.dns | object | `{"enabled":true}` | DNS configuration for egress |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to DNS |
| networkPolicy.egress.enabled | bool | `true` | Enable egress rules |
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
| nodeSelector | object | `{}` | Node selector for pod assignment |
| podAnnotations | object | `{}` | Additional annotations to add to the pod |
| podLabels | object | `{}` | Additional labels to add to the pod |
| podSecurityContext.fsGroup | int | `1001` | Group ID for file system access |
| podSecurityContext.fsGroupChangePolicy | string | `"OnRootMismatch"` | Change the fsGroup of the pod for Security Context Constraints. |
| podSecurityContext.runAsGroup | int | `1001` | Group ID for file system access |
| podSecurityContext.runAsNonRoot | bool | `true` | Run pod as non-root user |
| podSecurityContext.runAsUser | int | `1001` | User ID to run as. Matches the non-root user baked into the container image (USER 1001:1001). |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name |
| resources.limits | object | `{"cpu":"250m","memory":"128Mi"}` | Resource limits define the maximum resources the container can use. The Rust server has a small footprint; these limits leave generous headroom. |
| resources.limits.cpu | string | `"250m"` | Maximum CPU allocation for the container. |
| resources.limits.memory | string | `"128Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"25m","memory":"32Mi"}` | Resource requests define the guaranteed resources reserved for the container. |
| resources.requests.cpu | string | `"25m"` | Minimum CPU requested by the container. |
| resources.requests.memory | string | `"32Mi"` | Minimum memory requested by the container. |
| securityContext.allowPrivilegeEscalation | bool | `false` | Allow privilege escalation |
| securityContext.capabilities.drop | list | `["ALL"]` | Linux capabilities to drop |
| securityContext.readOnlyRootFilesystem | bool | `true` | Mount root filesystem as read-only. The application is a statically linked binary on a scratch image that serves pre-built assets and needs no writable root filesystem. A writable /tmp is still provided via an emptyDir volume. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. This port is mapped to the application container port (8080). |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| tolerations | list | `[]` | Tolerations for pod assignment |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |

> [!NOTE]
> The v2 application is a self-contained Rust binary that serves pre-built assets.
> GitHub data is fetched at build time, so no GitHub token is required at runtime.

## Examples

### Minimal Configuration

The chart runs with sensible defaults and requires no mandatory configuration:

```shell
helm install [RELEASE_NAME] timschoenle/portfolio \
  --namespace [NAMESPACE] --create-namespace
```

### With Ingress and TLS

```yaml
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
  logLevel: info

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
```

### High Availability Configuration

```yaml
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

- Runs as non-root user (UID 1001)
- Read-only root filesystem (enabled by default)
- No privilege escalation
- Service account token not automounted by default
- Drop all capabilities

Writable volumes are provided only where necessary:
- `/tmp` - Temporary files

## Resource Recommendations

Based on the Rust application characteristics:

| Environment | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-------------|-------------|-----------|----------------|--------------|
| Development | 25m         | 250m      | 32Mi           | 128Mi        |
| Production  | 50m         | 500m      | 64Mi           | 256Mi        |
| High Traffic| 100m        | 1000m     | 128Mi          | 512Mi        |

## Troubleshooting

### Pod not starting

Inspect the pod events and logs:

```bash
kubectl describe pod -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
kubectl logs -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
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
