# common

![Version: 1.5.0](https://img.shields.io/badge/Version-1.5.0-informational?style=flat-square) ![Type: library](https://img.shields.io/badge/Type-library-informational?style=flat-square)

Shared template partials for the TimSchoenle Helm charts

This is a **library chart**. It renders nothing on its own and is never published; the
application charts in this repository depend on it via `file://../common` and compose its
partials. Nothing here is installable — if you are looking for something to deploy, see the
[chart index](https://github.com/TimSchoenle/helm-charts#charts).

## Design

The library provides *partials*, not whole resources. Charts keep ownership of which
Kubernetes objects they create; the library owns everything those objects have in common —
naming, labels, image references, security contexts, probes, resources, scheduling and
network policy.

That split is the whole design. A partial that emitted a complete Deployment would have to
grow a value for every field any consumer might need, and the consumers would stop being
readable. Composing named fragments instead keeps each chart's templates a recognisable
Kubernetes manifest.

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

### Observability

| Partial | Purpose |
|---|---|
| `common.grafana.dashboard.configMap` | Dashboard JSON in a labelled ConfigMap, for a Grafana sidecar |
| `common.grafana.dashboard.customResources` | One `GrafanaDashboard` per file, for grafana-operator v5 |
| `common.grafana.dashboard.configMapName` | The ConfigMap name both of the above resolve through |
| `common.grafana.dashboard.errors` / `.validate` | Misconfiguration as messages, or raised |
| `common.prometheus.rules.prometheusRule` | Every rule group across the chart's rule files, as one `PrometheusRule` |
| `common.prometheus.rules.groups` | Just the groups, parsed and re-emitted |
| `common.prometheus.rules.errors` / `.validate` | Misconfiguration as messages, or raised |
| `common.prometheus.operatorErrors` | Whether the Prometheus Operator CRDs are missing, as a message |

Grafana ships no Kubernetes-native dashboard type, and the two carriers that exist are not
equivalent: a sidecar ConfigMap is discovered according to the *Grafana* release's
`sidecar.dashboards.searchNamespace`, which the chart owning the dashboard cannot influence,
while a `GrafanaDashboard` carries `allowCrossNamespaceImport` and so declares its own reach.
Both render from the same ConfigMap — the custom resources use `configMapRef` — so the JSON is
stored once. Rules have only the one carrier: `ruleNamespaceSelector`/`ruleSelector` on the
Prometheus custom resource decide what loads them, and `labels` is the half of that a chart
controls.

> [!IMPORTANT]
> Dashboard JSON and rule files are read as file data and never passed through the template
> engine. Grafana legends use `{{ }}` and alert annotations use
> `{{ $labels.job }}`, both of which Go would either fail on or silently resolve to an
> empty string.

> [!IMPORTANT]
> When the required CRDs are absent these partials fail the render instead of skipping. A
> capability guard that skipped would install cleanly and leave the release unmonitored, which is
> the failure worth preventing. Offline renders declare the APIs with
> `--api-versions monitoring.coreos.com/v1 --api-versions grafana.integreatly.org/v1beta1`.

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
| config | object | `{}` | Application configuration, expressed as the TOML tree the service documents. Rendered by `common.toml` into the ConfigMap the pod mounts, never passed as environment variables: the loader every application shares refuses a key supplied by both the environment and a file, and a value that lives in a file is one the kubelet can rotate under a running process. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered `config` tree. The escape hatch for anything `common.toml` cannot express, notably arrays of tables. |
| configMount | object | `{"configDir":"","secretsDir":""}` | Where the rendered configuration and the credential files land in the container. Consumed by `common.fileConfig.*`, which also passes both directories to the application as `<PREFIX>_CONFIG` and `<PREFIX>_SECRETS_DIR`. |
| configMount.configDir | string | `""` | Directory the rendered `config.toml` is mounted at. |
| configMount.secretsDir | string | `""` | Directory the credential files are mounted at, one file per configuration key. |
| dnsConfig | object | `{}` | Pod DNS configuration. |
| dnsPolicy | string | `""` | Pod DNS policy. |
| existingConfigMap | string | `""` | Name of an existing ConfigMap holding `config.toml`, replacing the one this chart renders. |
| existingSecret | string | `""` | Name of an existing Secret holding the credential files, replacing the one this chart renders. Its keys must be the configuration paths the service reads, with `__` for nesting and no dots — that is the file name the loader parses. |
| extraEnv | list | `[]` | Additional environment variables appended to the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| gateway | object | `{"addresses":[],"allowedRoutes":{},"annotations":{},"backendRefs":[],"create":false,"enabled":false,"filters":[],"gatewayClassName":"","hostnames":[],"httpPort":80,"httpsPort":443,"httpsRedirect":{"enabled":false,"port":null,"sectionName":"","statusCode":301},"infrastructure":{},"listeners":[],"parentRefs":[],"path":"/","rules":[],"timeouts":{},"tls":{"certificateRefs":[],"enabled":false,"mode":"Terminate","options":{}}}` | Gateway API configuration, consumed by `common.gateway.*`. The successor to `ingress`, and an independent switch from it: a cluster migrating between an Ingress controller and a Gateway implementation runs both for a while.  The division of labour is the API's, not this chart's. A `Gateway` — the listeners, the address, the certificates — belongs to the cluster operator; an application owns only the `HTTPRoute` that attaches to it. So the default here is route-only, and `create` is for installs that have no cluster-wide Gateway to attach to. |
| gateway.addresses | list | `[]` | Addresses requested for the created Gateway, e.g. a fixed `IPAddress`. |
| gateway.allowedRoutes | object | `{}` | Which routes may attach to the created Gateway's listeners. Defaults to `Same`: a Gateway this chart owns should not be attachable from another namespace unless that is asked for. |
| gateway.annotations | object | `{}` | Annotations for the HTTPRoute and the created Gateway. Values may contain Go templates. |
| gateway.backendRefs | list | `[]` | Backends for rules that name none. Defaults to this chart's own Service. Weights are honoured, so a traffic split needs no custom rule. |
| gateway.create | bool | `false` | Also create the Gateway itself. Leave off when the cluster already runs one — that is the normal case, and one Gateway per application usually means one load balancer per application. When on, a route that names no parent attaches to it automatically. |
| gateway.enabled | bool | `false` | Create the HTTPRoute. Requires the `gateway.networking.k8s.io` CRDs; `common.gateway.validate` fails the render loudly rather than silently dropping the route when they are absent. |
| gateway.filters | list | `[]` | Filters applied to the default rule: `RequestHeaderModifier`, `ResponseHeaderModifier`, `RequestRedirect`, `URLRewrite`, `RequestMirror`, `ExtensionRef`. This is where an Ingress controller's annotations end up, as typed fields. Ignored when `rules` is set. |
| gateway.gatewayClassName | string | `""` | GatewayClass that programs the created Gateway, e.g. `cilium`, `istio`, `envoy-gateway`, `nginx`. Required by `create`; a Gateway without one is never reconciled. Ignored otherwise. |
| gateway.hostnames | list | `[]` | Hostnames the route serves. Values may contain Go templates.  Required unless `create` is set. A route with no hostnames matches every name its listener accepts: on a Gateway this chart owns that is harmless, and sometimes the point — an install reached by address has no DNS name to state. On a shared Gateway it means taking over traffic meant for other applications, so the render refuses it. |
| gateway.httpPort | int | `80` | Port for the derived HTTP listener. |
| gateway.httpsPort | int | `443` | Port for the derived HTTPS listener. |
| gateway.httpsRedirect | object | `{"enabled":false,"port":null,"sectionName":"","statusCode":301}` | A second route that redirects plaintext traffic to HTTPS. Under Ingress this was a controller-specific annotation; Gateway API expresses it as a typed `RequestRedirect` filter, which means it has to be a real object. |
| gateway.httpsRedirect.enabled | bool | `false` | Create the redirect route. |
| gateway.httpsRedirect.port | string | `nil` | Port to redirect to. Left unset, the scheme implies it. |
| gateway.httpsRedirect.sectionName | string | `""` | Listener to bind the redirect to. Must be the plaintext one: attached to every listener, the redirect would also apply to the HTTPS listener and loop forever. Defaults to `http`, the name of the listener `create` renders. |
| gateway.httpsRedirect.statusCode | int | `301` | Redirect status code. `301` or `302`. |
| gateway.infrastructure | object | `{}` | `infrastructure.labels` / `infrastructure.annotations` for the created Gateway, passed through to the load balancer the implementation provisions. Where Cilium's LB-IPAM annotations go. |
| gateway.listeners | list | `[]` | Listeners for the created Gateway, replacing the derived ones entirely. Reach for this when a listener needs its own hostname or certificate. |
| gateway.parentRefs | list | `[]` | Gateways the route attaches to. Each entry takes `name` and optionally `namespace`, `sectionName` (a single listener), `port`, `group` and `kind`; the API's defaults are filled in. Values may contain Go templates.  A route that names no parent is accepted by the API server and then does nothing — no listener ever programs it — so this is required unless `create` is set. Example: ```yaml parentRefs:   - name: shared-gateway     namespace: gateway-system     sectionName: https ``` |
| gateway.path | string | `"/"` | Path prefix for the default rule. Ignored when `rules` is set. |
| gateway.rules | list | `[]` | Routing rules, in full `HTTPRouteRule` form (`matches`, `filters`, `backendRefs`, `timeouts`). An entry that omits `backendRefs` inherits this chart's Service, so a rule that only narrows the path does not have to restate where the traffic goes.  Left empty, the route gets one rule matching `path` as a prefix — the Gateway API equivalent of a single-path Ingress, and a complete configuration together with `hostnames`. |
| gateway.timeouts | object | `{}` | Timeouts for the default rule: `request` and `backendRequest`, as Go durations. Ignored when `rules` is set. |
| gateway.tls | object | `{"certificateRefs":[],"enabled":false,"mode":"Terminate","options":{}}` | TLS for the created Gateway's HTTPS listener. Ignored without `create`, and irrelevant when attaching to somebody else's Gateway — the certificate is theirs. |
| gateway.tls.certificateRefs | list | `[]` | Secrets holding the certificate. Required by `Terminate`: unlike an Ingress there is no convention by which one is looked up from the hostname. A ref naming another namespace additionally needs a `ReferenceGrant` there, which this chart deliberately does not create — a grant is the target namespace owner's to give. |
| gateway.tls.enabled | bool | `false` | Add an HTTPS listener. |
| gateway.tls.mode | string | `"Terminate"` | TLS mode. |
| gateway.tls.options | object | `{}` | Implementation-specific TLS options. |
| grafanaDashboard | object | `{"enabled":false,"grafanaOperator":{"allowCrossNamespaceImport":true,"enabled":false,"folder":"","instanceSelector":{"matchLabels":{"dashboards":"grafana"}},"resyncPeriod":"5m"},"label":"grafana_dashboard","labelValue":"1"}` | Grafana dashboard delivery, consumed by `common.grafana.dashboard.*`. A consuming chart exposes this shape wherever it likes — `metrics.dashboard` is the convention — and passes it in as the `values` argument. Grafana has no Kubernetes-native dashboard type, so the partials render both mechanisms: a labelled ConfigMap for the sidecar, and, optionally, one `GrafanaDashboard` per file for grafana-operator v5. |
| grafanaDashboard.enabled | bool | `false` | Create the ConfigMap holding the dashboard JSON. Required by the operator path too, which references it rather than duplicating the JSON into the custom resources. |
| grafanaDashboard.grafanaOperator.allowCrossNamespaceImport | bool | `true` | Let the resources bind to Grafana instances outside the release namespace. With `false` the operator only considers Grafana custom resources in the same namespace. |
| grafanaDashboard.grafanaOperator.enabled | bool | `false` | Also create one `GrafanaDashboard` per dashboard file. This is the only delivery path a chart can make cross-namespace on its own terms. Requires the `grafana.integreatly.org/v1beta1` CRDs; `common.grafana.dashboard.errors` fails the render loudly rather than silently dropping the objects when they are absent. |
| grafanaDashboard.grafanaOperator.folder | string | `""` | Folder to file the dashboards under. Empty leaves them at the Grafana root. |
| grafanaDashboard.grafanaOperator.instanceSelector | object | `{"matchLabels":{"dashboards":"grafana"}}` | Label selector for the Grafana instances to import into. Must select something: unlike a Kubernetes label selector, an empty `instanceSelector` matches no instance at all. |
| grafanaDashboard.grafanaOperator.resyncPeriod | string | `"5m"` | How often the operator re-reconciles the dashboard, undoing edits made in the Grafana UI. A Go duration. |
| grafanaDashboard.label | string | `"grafana_dashboard"` | Label a Grafana sidecar watches for dashboard ConfigMaps. Discovery is a property of the *Grafana* release: the sidecar only sees this ConfigMap if `sidecar.dashboards.searchNamespace` covers the namespace it lands in, which a chart cannot influence from its own side. |
| grafanaDashboard.labelValue | string | `"1"` | Value for that label. |
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
| networkPolicy | object | `{"cilium":{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}},"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration.  Every generated egress rule is scoped by a `to:` selector. An egress rule that lists only ports is not a restriction — the NetworkPolicy API reads a missing `to` as "any destination", so such a policy permits traffic to every in-cluster service and to the cloud metadata endpoint. |
| networkPolicy.cilium | object | `{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}}` | Cilium-only additions, used when `engine` is `cilium` or `both`. Everything above is translated into the CiliumNetworkPolicy automatically; these are the rules the portable API has no way to express.  Note that `extraIngress`, `extraEgress` and the per-section `customRules` above are *not* carried over: those are verbatim `networking.k8s.io/v1` rule objects and are not valid CNP. The fields below are their counterparts. |
| networkPolicy.cilium.description | string | `""` | `spec.description`, which Cilium surfaces in `cilium policy get` and in Hubble flow verdicts. The one place to record *why* a rule exists where an operator debugging a drop will actually see it. |
| networkPolicy.cilium.egress | object | `{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]}` | Cilium-only egress rules. |
| networkPolicy.cilium.egress.customRules | list | `[]` | Additional egress rules in CiliumNetworkPolicy form, appended verbatim. |
| networkPolicy.cilium.egress.dnsMatchPatterns | list | `[]` | What the DNS proxy may resolve, e.g. `- matchPattern: "*.example.com"`. Defaults to everything, which only permits the lookup — an answer is still only reachable if some rule allows the address. |
| networkPolicy.cilium.egress.entityPorts | list | `[]` | Restrict the `toEntities` rule to specific ports. Empty means all ports. |
| networkPolicy.cilium.egress.fqdnPorts | list | `[]` | Ports the `toFQDNs` rule allows. Defaults to TCP/443. |
| networkPolicy.cilium.egress.httpRules | list | `[]` | L7 HTTP rules layered onto the `toFQDNs` rule, e.g. `- method: GET` / `path: "/v1/.*"`. Turns "may reach this host" into "may make these requests to this host". Costs a proxy hop per connection. |
| networkPolicy.cilium.egress.toEntities | list | `[]` | Named destination sets, e.g. `world` for everything outside the cluster, or `kube-apiserver`. Not a synonym for the `egress.cidr`/`except` translation: `world` does not carve out the cloud metadata endpoint the way those defaults do. |
| networkPolicy.cilium.egress.toFQDNs | list | `[]` | Destinations by name rather than by address, e.g. `- matchName: api.example.com` or `- matchPattern: "*.example.com"`. This is the rule the CIDR-based `egress.https` was always a poor approximation of: "may reach the internet on 443" permits every public host that exists, where this permits the ones the application actually talks to.  Enforced against the addresses Cilium's DNS proxy saw returned for the name, so `egress.dns.enabled` must stay on — the render fails if it is not. |
| networkPolicy.cilium.enableDefaultDeny | bool | `true` | State default-deny explicitly rather than relying on it being implied by the presence of rules. This is what makes the intentional default-deny case — a policy with an empty rule list — actually deny, instead of being treated as no policy at all. Requires Cilium 1.16+. |
| networkPolicy.cilium.extraEgress | list | `[]` | Extra egress rules in CiliumNetworkPolicy form, appended regardless of `egress.enabled`. |
| networkPolicy.cilium.extraIngress | list | `[]` | Extra ingress rules in CiliumNetworkPolicy form, appended regardless of `ingress.enabled`. |
| networkPolicy.cilium.ingress | object | `{"customRules":[],"fromEntities":[]}` | Cilium-only ingress rules. |
| networkPolicy.cilium.ingress.customRules | list | `[]` | Additional ingress rules in CiliumNetworkPolicy form, appended verbatim. |
| networkPolicy.cilium.ingress.fromEntities | list | `[]` | Named source sets, e.g. `cluster`, `host`, `remote-node`, `world`, `kube-apiserver`. A named entity stays correct when the cluster is renumbered; a CIDR list does not. |
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
| networkPolicy.engine | string | `"kubernetes"` | Which policy dialect to render. `kubernetes` emits the portable `networking.k8s.io/v1` pair; `cilium` emits `CiliumNetworkPolicy`, which can express FQDN destinations, named entities and L7 rules that the portable API cannot; `both` emits both, for the window in which a cluster is migrating between CNIs.  The engine picks the dialect, not the rules — every value below is translated either way, so switching is a one-line change. `both` is additive rather than stricter: policies selecting one pod union their allowances, so it is a migration setting and not a hardening one. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended regardless of `egress.enabled`. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended regardless of `ingress.enabled`. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress rules. |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Allow traffic from the ingress controller. |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from the ingress controller. |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace the ingress controller runs in. |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector matching the ingress controller. |
| networkPolicy.ingress.customRules | list | `[]` | Additional ingress rules, appended verbatim. |
| networkPolicy.ingress.enabled | bool | `true` | Add ingress rules. Disabled means default-deny for inbound traffic. |
| networkPolicy.ingress.gateway | object | `{"enabled":true,"namespace":"","ports":[],"selector":{}}` | Allow traffic from the Gateway API data plane. Only rendered when `gateway.enabled` is also set, so it costs nothing on a chart exposed through an Ingress.  Needs no configuration in the common case: the Gateway that must be admitted is by definition the one `gateway.parentRefs` names, so both fields below are derived from it. Restating the Gateway's identity here would be a second place to edit on a rename, and a policy that names the wrong Gateway looks correct and blocks all inbound traffic. |
| networkPolicy.ingress.gateway.enabled | bool | `true` | Allow ingress from the Gateway's data plane. |
| networkPolicy.ingress.gateway.namespace | string | `""` | Namespace the data plane runs in. Empty derives it from `gateway.parentRefs`. |
| networkPolicy.ingress.gateway.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.gateway.selector | object | `{}` | Pod selector matching the data plane. Empty derives `gateway.networking.k8s.io/gateway-name: <parentRef>`, the label Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision for a Gateway. |
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
| prometheusRule | object | `{"enabled":false,"labels":{},"scope":{"matcher":"","placeholder":""}}` | Prometheus recording and alerting rules, consumed by `common.prometheus.rules.*`. Unlike the dashboards there is only one carrier — `PrometheusRule` is already the operator's own CRD — and no per-object cross-namespace grant: which namespaces a Prometheus loads rules from is decided by `ruleNamespaceSelector` and `ruleSelector` on the Prometheus custom resource. |
| prometheusRule.enabled | bool | `false` | Create a PrometheusRule from the chart's rule files. Requires the `monitoring.coreos.com/v1` CRDs; `common.prometheus.rules.errors` fails the render loudly rather than silently dropping the object when they are absent. |
| prometheusRule.labels | object | `{}` | Extra labels, templated. This is the only half of rule discovery a chart controls: a cluster whose Prometheus selects `release: kube-prometheus-stack` needs that label here, or the rules are created and never loaded. |
| prometheusRule.scope | object | `{"matcher":"","placeholder":""}` | Confine the rules to a subset of the cluster's series. A `PrometheusRule` is not scoped to the namespace it lives in, so `up{job="api"} == 0` matches somebody else's `api` job in another namespace and two installs of one chart alert on each other. Rather than parse PromQL in a Go template, the rule files mark every selector with an always-true placeholder matcher and the library swaps it for a real one — see the note in `_prometheus.tpl`. Consuming charts normally derive `matcher` rather than exposing it raw. |
| prometheusRule.scope.matcher | string | `""` | What to swap it for, e.g. `namespace="prod"`. Empty disables the substitution; the placeholder is left in place, which is correct, because it is already a no-op matcher. |
| prometheusRule.scope.placeholder | string | `""` | The literal placeholder written into the rule files, e.g. `myapp_scope=~".*"`. Empty disables the substitution and the files install exactly as vendored. |
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
