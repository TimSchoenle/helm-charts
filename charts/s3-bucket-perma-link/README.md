# s3-bucket-perma-link

![Version: 2.0.9](https://img.shields.io/badge/Version-2.0.9-informational?style=flat-square) ![AppVersion: v0.3.24](https://img.shields.io/badge/AppVersion-v0.3.24-informational?style=flat-square)

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
kubectl create secret generic s3-credentials \
  --namespace [NAMESPACE] \
  --from-literal=access_key='...' \
  --from-literal=secret_key='...'
```

The key names matter: the chart reads `access_key` and `secret_key`, and a Secret with any
other spelling produces a pod that starts and then fails every request.

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/s3-bucket-perma-link \
  --namespace [NAMESPACE] --create-namespace \
  --values values.yaml
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/s3-bucket-perma-link -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`.

## Mapping paths to objects

`application.handler.entries` is the whole configuration: one key per URL path, whose value is
a list of `"bucket,key"` pairs.

```yaml
application:
  handler:
    entries:
      latest-report:
        - "company-reports,2024/q4-report.pdf"
      user-guide:
        - "documentation,guides/user-guide.pdf"

  s3:
    secretName: s3-credentials
    host: s3.eu-central-1.amazonaws.com
    region: eu-central-1
```

`GET /latest-report` then serves `2024/q4-report.pdf` out of `company-reports`. Paths are
declared, not derived — a request for anything not listed here is not proxied, so the bucket
cannot be walked through this service.

Changing an entry is a values change and a rollout; the URL is unaffected.

## Non-AWS endpoints

`application.s3.host` takes any S3-compatible API endpoint, including a port. `region` is still
required — most implementations only use it to build the request signature, but the signature
is rejected if it disagrees with what the server expects.

```yaml
application:
  s3:
    secretName: s3-credentials
    host: minio.example.com:9000
    region: us-east-1
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
| application.handler.entries | object | `{}` | Handler configuration defining static routes and their S3 mappings. Each key represents a URL path, and the value is a list of "bucket,file" pairs. Example: ```yaml entries:   myfile: ["bucket1,file.txt"]   mydir: ["bucket2,dir/index.html"] ``` |
| application.logLevel | string | `"info"` | Log level for application output. |
| application.s3 | object | `{"host":"s3.amazon.com","region":"eu-central-1","secretName":""}` | Configuration for connecting to an S3-compatible service. |
| application.s3.host | string | `"s3.amazon.com"` | S3-compatible API endpoint. Example: "https://s3.amazonaws.com" or "https://minio.yourdomain.com" |
| application.s3.region | string | `"eu-central-1"` | AWS region or S3 region identifier. Used for authenticating with region-specific endpoints. |
| application.s3.secretName | string | `""` | Name of an existing Kubernetes Secret containing S3 credentials. The secret must include `access_key` and `secret_key` fields. |
| application.sentryDsn | string | `""` | Sentry DSN for error tracking and reporting. Leave empty to disable Sentry integration. |
| application.server | object | `{"host":"0.0.0.0","port":8080}` | HTTP server configuration. Defines where the application listens for incoming connections. |
| application.server.host | string | `"0.0.0.0"` | Host address to bind the HTTP server. Typically `0.0.0.0` to listen on all network interfaces. |
| application.server.port | int | `8080` | Port number the server listens on. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timmi6790/s3-bucket-perma-link"` | The container image repository. |
| image.tag | string | `"v0.3.24@sha256:f3e960670fccf8e46d268ca091fcc12950b469f82de9d08d939eb19a53fb88bf"` | The container image tag, pinned by digest (`vX.Y.Z@sha256:...`). The digest pins the pull, while the tag stays on as the readable version marker. Defaults to the chart's `appVersion` when empty. |
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
| securityContext | object | `{}` | Container security context, merged over the preset. A writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
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
