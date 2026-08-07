# portfolio

![Version: 3.0.10](https://img.shields.io/badge/Version-3.0.10-informational?style=flat-square) ![AppVersion: v2.2.3](https://img.shields.io/badge/AppVersion-v2.2.3-informational?style=flat-square)

Personal portfolio built with Rust (Yew frontend, Axum server).

A single self-contained Rust binary serving pre-built assets. There is no database, no cache
and no runtime credential — the GitHub data on the site is fetched at build time, so no token
is needed here. Installing it without any values produces a working release; the only thing
most installs add is an Ingress.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- An ingress controller, if `ingress.enabled=true`

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/portfolio \
  --namespace [NAMESPACE] --create-namespace
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/portfolio -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`. Nothing outlives the release.

## Publishing it

```yaml
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
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

## Sizing

`resources` ships with real defaults — 25m/32Mi requested, 250m/128Mi limited — rather than the
empty block most charts use, because the server's footprint is known and small. Raise the
limits only if you are actually seeing throttling or OOM kills; a static-asset server that is
slow is usually waiting on the network, not on CPU.

`resourcesPreset` offers named t-shirt sizes instead, and is ignored whenever `resources` is
set. Set one or the other, not both.

For availability, `replicaCount` plus a `podAntiAffinity` (or the `topologySpreadConstraints`
value) is the whole story — the pods share nothing, so any number of them can run.

## Health checks

All three probes are on by default and hit `/api/health`. The startup probe is the one that
covers a slow boot, and it is why the liveness probe can be aggressive without risking a kill
loop during startup.

Each probe takes one handler (`httpGet`, `tcpSocket`, `exec` or `grpc`) and the usual timings.
Anything left unset is omitted from the manifest rather than written out, so Kubernetes' own
defaults apply:

```yaml
startupProbe:
  failureThreshold: 12   # 12 x periodSeconds before the container is considered failed
livenessProbe:
  periodSeconds: 10
```

> [!NOTE]
> `successThreshold` is accepted only on the readiness probe. Kubernetes rejects any value
> other than `1` on startup and liveness probes, so the chart omits it there.

## Security

The pod satisfies the [restricted Pod Security Standard][pss] as installed: non-root (UID
1001), read-only root filesystem, all capabilities dropped, no privilege escalation,
`seccompProfile: RuntimeDefault`, and no ServiceAccount token mounted. `/tmp` is the one
writable path, provided as an `emptyDir`.

Opt out with `podSecurityContextPreset: none` / `securityContextPreset: none`, or override
single fields under `podSecurityContext` / `securityContext` — your values are merged over the
preset rather than replacing it.

[pss]: https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted

## When it will not start

```bash
kubectl describe pod -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
kubectl logs -n [NAMESPACE] -l app.kubernetes.io/name=portfolio
```

If the pod is running but the Ingress serves an error, check the app directly first — that
separates a broken Ingress from a broken pod:

```bash
kubectl port-forward -n [NAMESPACE] svc/[RELEASE_NAME]-portfolio 8080:80
curl http://localhost:8080/api/health
```

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| application.logLevel | string | `"info"` | Log verbosity passed to the application as `RUST_LOG`. Accepts standard tracing/env_logger directives (e.g. `info`, `debug`, `warn`). |
| application.port | int | `8080` | Port number the application listens on (exposed to the container as `PORT`). The Axum server binds to 0.0.0.0 on this port. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. The application never calls the Kubernetes API. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| image.pullPolicy | string | `""` | Kubernetes image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timschoenle/portfolio"` | Container image repository where the Portfolio application image is stored. |
| image.tag | string | `"v2.2.3@sha256:b3cbdcad9b50ba79049cfafb0f219a294106647c6cb3928057f0efbd278d40dc"` | Container image tag to deploy, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| ingress.annotations | object | `{}` | Custom annotations for the Ingress resource. Example: ```yaml annotations:   cert-manager.io/cluster-issuer: "letsencrypt-prod"   nginx.ingress.kubernetes.io/ssl-redirect: "true" ``` |
| ingress.enabled | bool | `false` | Enable or disable Kubernetes Ingress resource creation. |
| ingress.hosts | list | `[]` | List of host configurations for the Ingress. Values may contain Go templates. Example: ```yaml hosts:   - host: portfolio.example.com     paths:       - path: /         pathType: Prefix ``` |
| ingress.ingressClassName | string | `"nginx"` | Ingress class to use (e.g., "nginx", "traefik"). |
| ingress.tls | list | `[]` | TLS configuration for securing ingress connections. Example: ```yaml tls:   - secretName: portfolio-tls     hosts:       - portfolio.example.com ``` |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| livenessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/api/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":10,"timeoutSeconds":5}` | Liveness probe. Restarts the container when it stops responding. |
| livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| livenessProbe.failureThreshold | int | `3` | Consecutive failures before the container is restarted. |
| livenessProbe.httpGet | object | `{"path":"/api/health","port":"http"}` | HTTP handler for the probe. |
| livenessProbe.httpGet.path | string | `"/api/health"` | Health check path. |
| livenessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| livenessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| livenessProbe.periodSeconds | int | `10` | Probe interval. |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration.  Every generated egress rule is scoped by a `to:` selector. An egress rule that lists only ports is not a restriction: the NetworkPolicy API reads a missing `to` as "any destination", which would permit traffic to every in-cluster service and to the cloud instance metadata endpoint. |
| networkPolicy.egress | object | `{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}}` | Egress configuration. |
| networkPolicy.egress.cidr | string | `"0.0.0.0/0"` | Destination CIDR for the HTTP/HTTPS rules. |
| networkPolicy.egress.customRules | list | `[]` | Additional egress rules, appended verbatim. Each must carry its own `to:` selector. |
| networkPolicy.egress.dns | object | `{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}}` | DNS resolution. |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to the cluster DNS service. |
| networkPolicy.egress.dns.namespaceSelector | object | `{"kubernetes.io/metadata.name":"kube-system"}` | Namespace selector for the DNS service. |
| networkPolicy.egress.dns.podSelector | object | `{"k8s-app":"kube-dns"}` | Pod selector for the DNS service. |
| networkPolicy.egress.enabled | bool | `true` | Add egress rules. Disabled means default-deny for outbound traffic. |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. Defaults exclude RFC1918 private space and link-local 169.254.0.0/16, which covers the cloud instance metadata endpoint. |
| networkPolicy.egress.http | object | `{"enabled":false}` | Outbound HTTP. |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to TCP/80 on the destinations described by `cidr`/`except`. |
| networkPolicy.egress.https | object | `{"enabled":true}` | Outbound HTTPS. |
| networkPolicy.egress.https.enabled | bool | `true` | Allow egress to TCP/443 on the destinations described by `cidr`/`except`. |
| networkPolicy.enabled | bool | `false` | Create the NetworkPolicies. Enabling this with no rules configured yields a default-deny policy, which is intentional. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended regardless of `egress.enabled`. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended regardless of `ingress.enabled`. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress configuration. |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Ingress controller configuration. |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from the ingress controller. |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace where the ingress controller runs. |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector for the ingress controller. |
| networkPolicy.ingress.customRules | list | `[]` | Additional ingress rules, appended verbatim. |
| networkPolicy.ingress.enabled | bool | `true` | Add ingress rules. Disabled means default-deny for inbound traffic. |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}` | Allow scraping from a monitoring namespace. |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from the monitoring namespace. |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where monitoring tools run, matched on `kubernetes.io/metadata.name`. |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Override the namespace selector entirely. |
| networkPolicy.ingress.monitoring.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1001,"runAsGroup":1001,"runAsUser":1001}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `1001` | Supplemental group ID applied to mounted volumes. |
| podSecurityContext.runAsGroup | int | `1001` | Primary group ID to run as. |
| podSecurityContext.runAsUser | int | `1001` | User ID to run as. Matches the non-root user baked into the image (USER 1001:1001). |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name. |
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/api/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Readiness probe. Removes the pod from the Service while it cannot serve traffic. |
| readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| readinessProbe.failureThreshold | int | `3` | Consecutive failures before the pod is removed from the Service. |
| readinessProbe.httpGet | object | `{"path":"/api/health","port":"http"}` | HTTP handler for the probe. |
| readinessProbe.httpGet.path | string | `"/api/health"` | Health check path. |
| readinessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| readinessProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| readinessProbe.periodSeconds | int | `5` | Probe interval. |
| readinessProbe.successThreshold | int | `1` | Consecutive successes before the pod is considered ready. |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout. |
| replicaCount | int | `1` | Number of application replicas. |
| resources | object | `{"limits":{"cpu":"250m","memory":"128Mi"},"requests":{"cpu":"25m","memory":"32Mi"}}` | Explicit resource requests and limits. Wins over `resourcesPreset`. The Rust server has a small footprint; these leave generous headroom. |
| resources.limits | object | `{"cpu":"250m","memory":"128Mi"}` | Maximum resources the container may use. |
| resources.limits.cpu | string | `"250m"` | Maximum CPU allocation for the container. |
| resources.limits.memory | string | `"128Mi"` | Maximum memory allocation for the container. |
| resources.requests | object | `{"cpu":"25m","memory":"32Mi"}` | Resources guaranteed to the container. |
| resources.requests.cpu | string | `"25m"` | Minimum CPU requested by the container. |
| resources.requests.memory | string | `"32Mi"` | Minimum memory requested by the container. |
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. The application is a statically linked binary serving pre-built assets and needs no writable root filesystem; a writable /tmp is provided automatically via an emptyDir. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation and a writable root filesystem. |
| service.annotations | object | `{}` | Annotations for the Service. |
| service.port | int | `80` | Port that the Kubernetes Service will expose. |
| service.type | string | `"ClusterIP"` | Kubernetes Service type that exposes the application. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account. |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token for pods that default to it. |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account. |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty). |
| startupProbe | object | `{"enabled":true,"failureThreshold":12,"httpGet":{"path":"/api/health","port":"http"},"initialDelaySeconds":1,"periodSeconds":5,"timeoutSeconds":3}` | Startup probe. Protects a slow-starting container from the liveness probe. |
| startupProbe.enabled | bool | `true` | Enable the startup probe. |
| startupProbe.failureThreshold | int | `12` | Consecutive failures before the container is considered failed. |
| startupProbe.httpGet | object | `{"path":"/api/health","port":"http"}` | HTTP handler for the probe. The Portfolio application serves /api/health. |
| startupProbe.httpGet.path | string | `"/api/health"` | Health check path. |
| startupProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| startupProbe.initialDelaySeconds | int | `1` | Delay before the first probe. |
| startupProbe.periodSeconds | int | `5` | Probe interval. |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout. |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |

## Source Code

* <https://github.com/TimSchoenle/Portfolio>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
