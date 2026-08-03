# common

![Version: 1.0.3](https://img.shields.io/badge/Version-1.0.3-informational?style=flat-square) ![Type: library](https://img.shields.io/badge/Type-library-informational?style=flat-square)

Shared template partials for the TimSchoenle Helm charts

This is a **library chart**. It renders nothing on its own and is never released; the
application charts in this repository depend on it via `file://../common` and compose its
partials.

## Design

The library provides *partials*, not whole resources. Charts keep ownership of which
Kubernetes objects they create; the library owns everything those objects have in common —
naming, labels, image references, security contexts, probes, resources, scheduling and
network policy.

A minimal consumer looks like this:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "common.fullname" . }}
  namespace: {{ include "common.namespace" . }}
  labels:
    {{- include "common.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  revisionHistoryLimit: {{ .Values.revisionHistoryLimit }}
  selector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "common.podLabels" . | nindent 8 }}
    spec:
      {{- include "common.podSpec.common" . | nindent 6 }}
      containers:
        {{- include "common.container" (dict "ctx" $ "ports" (list (dict "name" "http" "containerPort" 8080 "protocol" "TCP"))) | nindent 8 }}
      {{- with (include "common.volumes" (dict "ctx" $)) }}
      volumes:
        {{- . | nindent 8 }}
      {{- end }}
```

## Partials

### Naming and metadata

| Partial | Purpose |
|---|---|
| `common.name` | Chart name, honouring `nameOverride` |
| `common.fullname` | Release-qualified resource name, honouring `fullnameOverride` |
| `common.fullname.suffixed` | `common.fullname` plus a suffix, truncated to stay within 63 characters |
| `common.chart` | `name-version`, for the `helm.sh/chart` label |
| `common.namespace` | Release namespace, honouring `namespaceOverride` |
| `common.labels` | Full label set for object metadata |
| `common.podLabels` | Pod-template labels — deliberately excludes `helm.sh/chart` |
| `common.selectorLabels` | The stable subset used in immutable selector fields |
| `common.annotations` | `commonAnnotations`, rendered as templates |

### Workload

| Partial | Purpose |
|---|---|
| `common.podSpec.common` | Everything a pod spec shares across charts, except `containers` and `volumes` |
| `common.container` | The application container, as a YAML list item |
| `common.volumes` / `common.volumeMounts` | Chart volumes plus the `/tmp` scratch volume a read-only root filesystem requires |
| `common.probes` / `common.probe` | Startup, liveness and readiness probes |
| `common.resources` | Explicit `resources`, else a named `resourcesPreset` |
| `common.podSecurityContext` / `common.containerSecurityContext` | Restricted Pod Security Standard baseline, merged with chart values |
| `common.image` / `common.imagePullPolicy` / `common.imagePullSecrets` | Image reference as `registry/repository:tag@digest` |
| `common.affinity` | Explicit `affinity`, else the `podAntiAffinity` shorthand |
| `common.updateStrategy` | Deployment strategy, falling back to `Recreate` for ReadWriteOnce volumes |
| `common.podAnnotations` | Pod annotations plus config checksums that force a rollout |
| `common.configChecksum` | Digest of one template's `data`/`stringData`/`binaryData` |

### Network policy

| Partial | Purpose |
|---|---|
| `common.networkPolicy` | Both policies, gated on `networkPolicy.enabled` |
| `common.networkPolicy.ingress` / `.egress` | The individual policies |

> [!IMPORTANT]
> Every generated egress rule carries a `to:` selector. An egress rule that lists only
> `ports` is not a restriction — the NetworkPolicy API reads a missing `to` as *any
> destination*, so such a policy permits traffic to every in-cluster service and to the
> cloud instance metadata endpoint at `169.254.169.254`. Rules added through
> `networkPolicy.egress.customRules` must supply their own `to:`.

### Capabilities and utilities

| Partial | Purpose |
|---|---|
| `common.capabilities.kubeVersion` | Cluster version with vendor suffixes stripped, honouring `kubeVersionOverride` |
| `common.capabilities.{ingress,hpa,pdb,networkPolicy}.apiVersion` | Version-appropriate API groups |
| `common.capabilities.apiVersions.has` | Whether an API is registered on the target cluster |
| `common.tplvalues.render` | Render a value that itself contains Go templates |
| `common.tplvalues.merge` | Merge several maps without mutating them |
| `common.serviceAccountName`, `common.secretName`, `common.configMapName` | Resolve to an `existing*` override, else the chart's own object |
| `common.validateValues` | Report every missing required value at once |

## Development

The library cannot be exercised directly, since it renders nothing. A test-only consumer
lives at `.github/testdata/common-fixture` — outside `charts/`, so chart-testing and
chart-releaser never see it — and instantiates every partial:

```shell
helm dependency update .github/testdata/common-fixture
helm unittest .github/testdata/common-fixture
```

Bumping this chart's `version` requires bumping the pinned dependency version in every
consuming `Chart.yaml`.

## Values

The values below are never applied directly. They document the contract the partials expect
and act as the reference shape for consuming charts.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object the chart creates. Values may contain Go templates. |
| commonLabels | object | `{}` | Labels added to every object the chart creates. Values may contain Go templates. |
| component | string | `""` | Value for the `app.kubernetes.io/component` label. |
| dnsConfig | object | `{}` | Pod DNS configuration. |
| dnsPolicy | string | `""` | Pod DNS policy. |
| extraEnv | list | `[]` | Additional environment variables appended to the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| hostAliases | list | `[]` | Host aliases injected into the pod's /etc/hosts. |
| image.pullPolicy | string | `""` | Image pull policy. Left empty, resolves to `Always` for the `latest` tag and `IfNotPresent` for anything pinned. |
| image.registry | string | `""` | Registry host. Left empty, the repository is used as-is (Docker Hub). |
| image.repository | string | `""` | Image repository. Required. |
| image.tag | string | `""` | Image tag. Defaults to the chart's `appVersion`. May pin a digest inline (`v1.2.3@sha256:...`): the digest pins the pull, while the tag stays on as the readable version marker. |
| imagePullSecrets | list | `[]` | Pull secrets for private registries. Accepts `- name: regcred` or the shorthand `- regcred`. |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. Leave empty to detect from the cluster. |
| livenessProbe | object | `{"enabled":false}` | Liveness probe. See `startupProbe` for the accepted shape. |
| livenessProbe.enabled | bool | `false` | Enable the liveness probe. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration.  Every generated egress rule is scoped by a `to:` selector. An egress rule that lists only ports is not a restriction — the NetworkPolicy API reads a missing `to` as "any destination", so such a policy permits traffic to every in-cluster service and to the cloud metadata endpoint. |
| networkPolicy.egress | object | `{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}}` | Egress rules. |
| networkPolicy.egress.cidr | string | `"0.0.0.0/0"` | Destination CIDR for the HTTP/HTTPS rules. |
| networkPolicy.egress.customRules | list | `[]` | Additional egress rules, appended verbatim. Each must carry its own `to:` selector. |
| networkPolicy.egress.dns | object | `{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}}` | Allow DNS resolution. |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to the cluster DNS service. |
| networkPolicy.egress.dns.namespaceSelector | object | `{"kubernetes.io/metadata.name":"kube-system"}` | Namespace selector for the DNS service. |
| networkPolicy.egress.dns.podSelector | object | `{"k8s-app":"kube-dns"}` | Pod selector for the DNS service. |
| networkPolicy.egress.enabled | bool | `true` | Add egress rules. Disabled means default-deny for outbound traffic. |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. Defaults exclude RFC1918 private space and link-local 169.254.0.0/16, which covers the cloud instance metadata endpoint. |
| networkPolicy.egress.http | object | `{"enabled":false}` | Allow outbound HTTP. |
| networkPolicy.egress.http.enabled | bool | `false` | Allow egress to TCP/80 on the destinations described by `cidr`/`except`. |
| networkPolicy.egress.https | object | `{"enabled":true}` | Allow outbound HTTPS. |
| networkPolicy.egress.https.enabled | bool | `true` | Allow egress to TCP/443 on the destinations described by `cidr`/`except`. |
| networkPolicy.enabled | bool | `false` | Create the NetworkPolicies. When enabled with no rules configured, the result is a default-deny policy, which is intentional. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended regardless of `egress.enabled`. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended regardless of `ingress.enabled`. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress rules. |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Allow traffic from the ingress controller. |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from the ingress controller. |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace the ingress controller runs in. |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector matching the ingress controller. |
| networkPolicy.ingress.customRules | list | `[]` | Additional ingress rules, appended verbatim. |
| networkPolicy.ingress.enabled | bool | `true` | Add ingress rules. Disabled means default-deny for inbound traffic. |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}` | Allow scraping from a monitoring namespace. |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from the monitoring namespace. |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Monitoring namespace, matched on `kubernetes.io/metadata.name`. |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Override the namespace selector entirely. |
| networkPolicy.ingress.monitoring.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| partOf | string | `""` | Value for the `app.kubernetes.io/part-of` label. |
| podAnnotations | object | `{}` | Annotations added to the pod template. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. |
| podLabels | object | `{}` | Labels added to the pod template. |
| podSecurityContext | object | `{}` | Pod security context, merged over the preset. |
| podSecurityContextPreset | string | `"restricted"` | Baseline for the pod security context. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`); `none` applies nothing. Identity fields (`runAsUser`, `runAsGroup`, `fsGroup`) are always left to the chart, since they must match the UID baked into the image. |
| priorityClassName | string | `""` | PriorityClass for the pod. |
| readinessProbe | object | `{"enabled":false}` | Readiness probe. See `startupProbe` for the accepted shape. |
| readinessProbe.enabled | bool | `false` | Enable the readiness probe. |
| resources | object | `{}` | Explicit resource requests and limits. Wins over `resourcesPreset`. |
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. Presets set a memory limit and CPU/memory requests but no CPU limit: a CPU limit does not protect the node the way a memory limit does, it only throttles this workload. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets to retain for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. |
| securityContextPreset | string | `"restricted"` | Baseline for the container security context. `restricted` drops all capabilities and forbids privilege escalation, a writable root filesystem and running as root. |
| serviceAccount.annotations | object | `{}` | Annotations for the ServiceAccount. |
| serviceAccount.automountToken | bool | `false` | Mount the API token into pods that default to this ServiceAccount. |
| serviceAccount.create | bool | `true` | Create a dedicated ServiceAccount. |
| serviceAccount.name | string | `""` | Name of the ServiceAccount. Generated when empty. |
| startupProbe | object | `{"enabled":false}` | Startup probe. Set `enabled` and exactly one handler (`httpGet`, `tcpSocket`, `exec` or `grpc`); unset timing fields fall back to the Kubernetes defaults. |
| startupProbe.enabled | bool | `false` | Enable the startup probe. |
| strategy | object | `{}` | Deployment update strategy. Defaults to the Kubernetes default, except for workloads with a ReadWriteOnce volume, which fall back to `Recreate` to avoid a wedged rollout. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Topology spread constraints. |

## Source Code

* <https://github.com/TimSchoenle/helm-charts>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle |  | <https://github.com/TimSchoenle> |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
