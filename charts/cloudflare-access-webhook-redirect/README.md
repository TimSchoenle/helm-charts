# cloudflare-access-webhook-redirect

![Version: 3.0.10](https://img.shields.io/badge/Version-3.0.10-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: v1.0.0](https://img.shields.io/badge/AppVersion-v1.0.0-informational?style=flat-square)

A Helm chart for deploying the Cloudflare Access Webhook Redirect service. This service acts as an authentication proxy that validates requests using Cloudflare Access Service Auth tokens before forwarding them to target backend services.

Use it in front of a webhook receiver or an internal API that has no authentication of its
own: only the paths and methods you declare are forwarded, and each one is validated against
Cloudflare Access before it reaches the backend.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A Cloudflare Access application with Service Auth credentials
- The backend service to forward to, reachable from the pod

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/cloudflare-access-webhook-redirect \
  --namespace [NAMESPACE] --create-namespace \
  --values values.yaml
```

with `values.yaml`:

```yaml
application:
  handler:
    targetBase: http://backend-service:8080
    paths:
      api/webhook:
        - POST

  cloudflareAccess:
    secretName: cloudflare-access-secret
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/cloudflare-access-webhook-redirect -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`.

## Credentials

Create the Secret yourself and reference it by name. The keys have to be called `client_id`
and `client_secret`:

```shell
kubectl create secret generic cloudflare-access-secret \
  --namespace [NAMESPACE] \
  --from-literal=client_id='...' \
  --from-literal=client_secret='...'
```

```yaml
application:
  cloudflareAccess:
    secretName: cloudflare-access-secret
```

`application.cloudflareAccess.clientId` / `.clientSecret` are accepted as an alternative and
make the chart render the Secret itself. That puts the credentials into `values.yaml` and into
the Helm release object, where anyone who can run `helm get values` can read them — use it for
a throwaway cluster, not for anything real. `secretName` wins if both are set.

## Declaring what gets forwarded

`application.handler.paths` is an allowlist, keyed by path, valued by the methods permitted on
it. Nothing outside it is proxied, so a backend endpoint you did not list stays unreachable
through this service even though `targetBase` points at the same host.

```yaml
application:
  handler:
    targetBase: http://backend-service:8080
    paths:
      api/webhook:      # every method
        - ALL
      api/data:         # reads and writes
        - GET
        - POST
      health:           # reads only
        - GET
```

Methods: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`, or `ALL`.

Keep the list as narrow as the caller actually needs. `ALL` on a path that only ever receives
webhooks also exposes `DELETE` on it.

## Request flow

1. A client reaches the service, usually through an Ingress.
2. The path and method are checked against `application.handler.paths`; anything not declared
   is rejected here.
3. The request is authenticated with the Cloudflare Access Service Auth credentials.
4. It is forwarded to `targetBase`, and the backend's response is returned unchanged.

## Publishing it

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
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

Terminate TLS at the Ingress. The service speaks plain HTTP on port 8080 and is not meant to be
published without one.

## Running more than one replica

The service is stateless, so `replicaCount`, `autoscaling` and `podDisruptionBudget` behave the
way they do for any other Deployment and are all off or at 1 by default:

```yaml
replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

podDisruptionBudget:
  enabled: true
  minAvailable: 2

topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: cloudflare-access-webhook-redirect
```

A PodDisruptionBudget with `minAvailable` equal to `replicaCount` blocks node drains
indefinitely — keep it at least one below.

## Network policy

`networkPolicy.enabled` renders a default-deny policy per direction, with cluster DNS and
outbound HTTPS allowed out of the box. The HTTPS rule covers Cloudflare, but its destination
CIDR carves out RFC1918 space — **so the backend, which is almost always an in-cluster address,
is not reachable until you add a rule for it**. Without one the pod starts and every forwarded
request times out.

```yaml
networkPolicy:
  enabled: true
  ingress:
    controller:
      namespace: ingress-nginx                    # defaults to Traefik
      selector:
        app.kubernetes.io/name: ingress-nginx
  egress:
    customRules:
      - to:
          - podSelector:
              matchLabels:
                app.kubernetes.io/name: backend-service
        ports:
          - protocol: TCP
            port: 8080
```

Every rule you add must carry its own `to:`. A rule that lists only `ports:` is not a
restriction — the NetworkPolicy API reads a missing `to` as *any destination*, which includes
the cloud instance metadata endpoint at `169.254.169.254`.

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
| image.tag | string | `"v1.0.0@sha256:90a8c511781fa563bca7b78149975ab34dc5a6736b469b4afc7cdd8c2b4e7afd"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
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

## Source Code

* <https://github.com/TimSchoenle/cloudflare-access-webhook-redirect>
* <https://github.com/TimSchoenle/helm-charts>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle |  | <https://github.com/TimSchoenle> |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
