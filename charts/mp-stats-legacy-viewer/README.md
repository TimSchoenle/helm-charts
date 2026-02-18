# mp-stats-legacy-viewer

![Version: 0.0.1](https://img.shields.io/badge/Version-0.0.1-informational?style=flat-square) ![AppVersion: v0.2.1](https://img.shields.io/badge/AppVersion-v0.2.1-informational?style=flat-square)

MP Stats Legacy Viewer

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
helm install [RELEASE_NAME] timschoenle/mp-stats-legacy-viewer \
  --namespace [NAMESPACE] \
  --create-namespace
```

## Upgrade Chart

```shell
helm upgrade [RELEASE_NAME] timschoenle/mp-stats-legacy-viewer \
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
| application.healthCheck | object | `{"liveness":{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":10,"successThreshold":1,"timeoutSeconds":5},"path":"/health","readiness":{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3},"startup":{"failureThreshold":12,"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}}` | Health check probe configuration. Application exposes health check on /health. |
| application.healthCheck.liveness | object | `{"failureThreshold":3,"initialDelaySeconds":1,"periodSeconds":10,"successThreshold":1,"timeoutSeconds":5}` | Liveness probe configuration. Detects if the container needs to be restarted. |
| application.healthCheck.liveness.failureThreshold | int | `3` | Minimum consecutive failures for the probe to be considered failed. |
| application.healthCheck.liveness.initialDelaySeconds | int | `1` | Number of seconds after the container has started before liveness probe is initiated. |
| application.healthCheck.liveness.periodSeconds | int | `10` | How often (in seconds) to perform the probe. |
| application.healthCheck.liveness.successThreshold | int | `1` | Minimum consecutive successes for the probe to be considered successful. |
| application.healthCheck.liveness.timeoutSeconds | int | `5` | Number of seconds after which the probe times out. |
| application.healthCheck.path | string | `"/health"` | Path for health check endpoint. |
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
| application.server | object | `{"host":"0.0.0.0","port":8080}` | HTTP server configuration. Defines where the application listens for incoming connections. |
| application.server.host | string | `"0.0.0.0"` | Host address to bind the HTTP server. Typically `0.0.0.0` to listen on all network interfaces. |
| application.server.port | int | `8080` | Port number the server listens on. |
| image.pullPolicy | string | `"IfNotPresent"` | Kubernetes image pull policy. Determines when the image should be pulled from the registry. |
| image.repository | string | `"timschoenle/mp-stats-legacy-viewer"` | Container image repository where the application image is stored. Usually points to Docker Hub or a private registry. Example: ghcr.io/your-org/s3-bucket-perma-link |
| image.tag | string | `"v0.2.1@sha256:3e72119d7276bc93beff0caadca09d801f39d176927d50958f0ce98b3c268f14"` | Container image tag to deploy. Pin to a version for predictable deployments rather than using "latest". |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Useful for configuring ingress controllers (e.g., cert-manager, rate limits). |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. Set to `true` to expose the service externally via Ingress. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Each host defines rules for routing external traffic. Example: ```yaml hosts:   - host: s3.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). Should match your cluster’s ingress controller configuration. |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: s3-cert     hosts:       - s3.example.com ``` |
| networkPolicy | object | `{"egress":{"customRules":[],"dns":{"enabled":true},"enabled":true,"http":{"enabled":false},"https":{"enabled":false},"sentry":{"enabled":true}},"enabled":false,"ingress":{"controller":{"enabled":true,"namespace":"traefik","selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring"}}}` | Network policy configuration |
| networkPolicy.egress | object | `{"customRules":[],"dns":{"enabled":true},"enabled":true,"http":{"enabled":false},"https":{"enabled":false},"sentry":{"enabled":true}}` | Egress configuration |
| networkPolicy.egress.customRules | list | `[]` | Custom egress rules |
| networkPolicy.egress.dns | object | `{"enabled":true}` | DNS configuration for egress |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to DNS |
| networkPolicy.egress.enabled | bool | `true` | Enable egress rules |
| networkPolicy.egress.http | object | `{"enabled":false}` | HTTP configuration for egress |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to HTTP (TCP/80) |
| networkPolicy.egress.https | object | `{"enabled":false}` | HTTPS configuration for egress |
| networkPolicy.egress.https.enabled | bool | `false` | Allow egress to HTTPS (TCP/443) |
| networkPolicy.egress.sentry | object | `{"enabled":true}` | Sentry configuration for egress |
| networkPolicy.egress.sentry.enabled | bool | `true` | Allow egress to Sentry (HTTPS) |
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
| podSecurityContext.fsGroup | int | `1000` | Group ID for file system access |
| podSecurityContext.runAsNonRoot | bool | `true` | Run pod as non-root user |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as |
| resources.limits | object | `{"memory":"100Mi"}` | Resource limits define the maximum resources the container can use. |
| resources.limits.memory | string | `"100Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"memory":"100Mi"}` | Resource requests define the guaranteed resources reserved for the container. |
| resources.requests.memory | string | `"100Mi"` | Minimum memory requested by the container. |
| securityContext.allowPrivilegeEscalation | bool | `false` | Allow privilege escalation |
| securityContext.capabilities.drop | list | `["ALL"]` | Linux capabilities to drop |
| securityContext.readOnlyRootFilesystem | bool | `false` | Mount root filesystem as read-only. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. Typically maps to `application.server.port`. |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |

## Source Code

* <https://github.com/TimSchoenle/mp-stats-legacy-viewer>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)

