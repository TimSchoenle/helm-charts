# netcup-offer-bot

![Version: 6.1.0](https://img.shields.io/badge/Version-6.1.0-informational?style=flat-square) ![AppVersion: v3.0.0](https://img.shields.io/badge/AppVersion-v3.0.0-informational?style=flat-square)

This chart deploys the Netcup Offer Bot, which monitors https://www.netcup-sonderangebote.de/ RSS feed and sends notifications to Discord webhooks when new offers are available.

One replica, one webhook, one small volume. The bot polls the feed on a timer and posts new
offers; everything else is defaults.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A Discord webhook URL
- A Sentry DSN, if `telemetry.sentry.enabled` is set
- The Prometheus Operator CRDs, if `metrics.podMonitor` is enabled
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/netcup-offer-bot \
  --namespace [NAMESPACE] --create-namespace \
  --set discord.webhookUrl="https://discord.com/api/webhooks/..."
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/netcup-offer-bot -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`.

## Keeping the credentials out of the release

`discord.webhookUrl` and `telemetry.sentry.dsn` are written into a Secret, but they still pass
through `values.yaml` and stay readable in the Helm release object afterwards. Point
`existingSecret` at a Secret you created yourself to avoid both:

```shell
kubectl create secret generic netcup-webhook   --namespace [NAMESPACE]   --from-literal=discord__webhook_url='https://discord.com/api/webhooks/...'   --from-literal=telemetry__sentry__dsn='https://<key>@<host>/<project>'
```

```yaml
existingSecret: netcup-webhook
```

**The key names are the configuration paths the bot reads, not free-form names.** Each
credential arrives as a file in a projected volume and the bot takes the key out of the file
*name*, so `discord__webhook_url` and `telemetry__sentry__dsn` are the required spellings; a
Secret spelled any other way mounts cleanly, supplies nothing, and the bot refuses to boot
naming the missing credential.

`discord.webhookUrl` and `telemetry.sentry.dsn` are then ignored. Only the keys the release
actually uses need to be present: the DSN is projected only while `telemetry.sentry.enabled` is
set, so a Secret for a release that does not report to Sentry carries the webhook alone.

## Configuration

Everything the bot reads is rendered into one `config.toml`, mounted as a ConfigMap and pointed
at by `NETCUP_OFFER_BOT_CONFIG`; the credentials — the Discord webhook, and the Sentry DSN when
that is switched on — are mounted separately as files under `NETCUP_OFFER_BOT_SECRETS_DIR`.
Nothing is passed as an environment variable, and that is deliberate on two counts: the loader
**fails the boot on a key supplied by both the environment and a file** rather than resolving it
by precedence, and an environment variable is visible in `kubectl describe pod`, in
`/proc/<pid>/environ` and in the environment of every child process — which for a webhook URL is
exactly the exposure worth removing.

The values above cover the whole documented surface. `config` takes the raw TOML tree for
anything they do not, merged over the derived one, and `configExtraToml` is appended verbatim
for what the renderer cannot express.

The bot does not reload its configuration, so the chart keeps the conventional `checksum/*` pod
annotations: a configuration change rolls the Deployment, which is the only way it takes effect.

## State and restarts

The bot records which offers it has already announced on a `ReadWriteOnce` volume, 10Mi by
default. Two things follow:

- **Turning persistence off means re-announcing every current offer after each restart**, since
  the replacement pod starts from an empty `emptyDir`.
- **`ReadWriteOnce` forces the `Recreate` update strategy.** A rolling update would wedge: the
  replacement pod cannot attach a volume the outgoing pod still holds. Expect a short gap on
  every upgrade — harmless here, since a missed poll is picked up by the next one.

`helm uninstall` deletes the claim along with the release. Set
`persistence.data.annotations."helm\.sh/resource-policy"=keep` if you want the seen-offer state
to survive a reinstall.

## Metrics

`metrics.enabled` exposes a Prometheus endpoint on `metrics.port` (9184). There is no Service,
so the chart renders a `PodMonitor` rather than a ServiceMonitor:

```yaml
metrics:
  enabled: true
  podMonitor:
    enabled: true
    labels:
      release: kube-prometheus-stack # whatever your Prometheus selects rules and monitors on
```

Without a matching label the PodMonitor is created and never read.

`metrics.ip` defaults to `0.0.0.0` rather than to the bot's own `127.0.0.1`, which answers
nothing from outside the container — a PodMonitor pointed at that would scrape a refused
connection.

## Error reporting and tracing

`telemetry.sentry` reports errors and panics to Sentry, and can record traces of the poll loop.
It is off, and inert while it is off: no client, no panic hook and no layer is installed, so
nothing leaves the process and nothing about a rendered release changes.

```yaml
telemetry:
  sentry:
    enabled: true
    dsn: https://<key>@<host>/<project>
    tracesSampleRate: 0.1
```

The DSN embeds a write key for the project's event stream, so the chart treats it as the
credential it is: it goes to the Secret under `telemetry__sentry__dsn`, never into the
ConfigMap. Switching the feature on with no DSN — neither here nor in an `existingSecret` — is
refused at render time, because the bot refuses to boot rather than installing a client that
reports nowhere, and a rejected `helm upgrade` beats a CrashLoopBackOff.

**Check the egress.** The chart never reads the DSN, so it cannot name the ingest host in the
network policies. The defaults do cover it — `networkPolicy.egress.https` permits TCP/443 to
`0.0.0.0/0` minus private space — so an out-of-the-box release reaches Sentry without further
work. A release that narrowed `networkPolicy.egress.cidr`, turned `egress.https` off, or
replaced the CIDR rule with `networkPolicy.cilium.egress.toFQDNs` has to name the endpoint
itself. Getting that wrong is silent: an SDK that cannot reach its endpoint queues events and
then discards them, so the project simply stays empty, which reads as "no errors".

```yaml
networkPolicy:
  enabled: true
  extraEgress:
    - to:
        - ipBlock:
            cidr: 34.120.195.249/32 # your ingest endpoint
      ports:
        - port: 443
          protocol: TCP
```

Under the Cilium engine the same destination is better expressed by name, via
`networkPolicy.cilium.egress.toFQDNs`.

`environment`, `release` and `serverName` are empty by default and the chart writes none of
them: the bot sends `production` (or `development` from a debug build) and the version the
binary was built from, and the second is what makes a regression attributable to a deploy. A
blank value is a *supplied* value to the loader, not an absent one, so set them only to say
something the bot cannot derive.

`telemetry.sentry` is installed once as the process boots, so a change to it is a restart rather
than a reload — which for this chart is the ordinary behaviour anyway, since it keeps the
`checksum/*` pod annotations.

## Upgrading

### 5.x to 6.0

Chart 6.0 tracks the bot's 3.0 release, which replaced the single `telemetry.sentry_dsn` key
with a full `telemetry.sentry` block — twelve settings and the DSN — and made Sentry an optional
compile-time feature. `telemetry.sentryDsn` is gone; a `helm upgrade` with 5.x values fails
schema validation naming the key rather than silently dropping error reporting.

| Before | After |
|---|---|
| `telemetry.sentryDsn: https://...` | `telemetry.sentry.enabled: true` **and** `telemetry.sentry.dsn: https://...` |
| (unset) | (unset — `telemetry.sentry.enabled` defaults to `false`) |

Two behaviours changed along with the spelling:

- **The DSN is a credential now.** 5.x rendered it into `config.toml`, where anything that can
  read the namespace could print it. It goes to the Secret instead, under
  `telemetry__sentry__dsn` — so an `existingSecret` that should carry it needs that key added,
  and the pod projects it only while `telemetry.sentry.enabled` is set.
- **The switch is separate from the DSN.** Setting one without the other is refused at render
  time in both directions: on with no DSN is a boot failure upstream, and a DSN with the switch
  off is a credential in the release that nothing reads.

Everything else under the block is new and optional; the defaults reproduce 5.x behaviour apart
from `tracesSampleRate`, which is `0.0` here against the `0.2` the old build hard-coded. Set it
explicitly if you were relying on those traces.

### 4.x to 5.0

`resourcesPreset` is gone. `resources` is the only sizing knob left, and it already ships with
the numbers this chart runs on — so a release that never set a preset needs no change, and a
`helm upgrade` with a values file that still carries one fails schema validation naming the key
rather than quietly ignoring it.

A preset was a word that meant something different in every chart, and reading the library was
the only way to find out what `medium` actually reserved. Substitute the block it stood for:

| Preset | requests | limits |
|---|---|---|
| `nano` | `cpu: 10m`, `memory: 32Mi` | `memory: 64Mi` |
| `micro` | `cpu: 25m`, `memory: 64Mi` | `memory: 128Mi` |
| `small` | `cpu: 50m`, `memory: 128Mi` | `memory: 256Mi` |
| `medium` | `cpu: 100m`, `memory: 256Mi` | `memory: 512Mi` |
| `large` | `cpu: 250m`, `memory: 512Mi` | `memory: 1Gi` |

No preset ever set a CPU limit, and this chart's defaults still do not: a CPU limit cannot
protect the node the way a memory limit does — it only throttles the workload that owns it once
it is hit. Set `resources.limits.cpu` if you want one.

The same release also documents the `networkPolicy` knobs the `common` library always accepted
but this chart never listed: `networkPolicy.extraIngress`, `networkPolicy.extraEgress`,
`networkPolicy.ingress.monitoring.namespaceSelector`, and a `ports` list on the monitoring and
controller rules. They are additions — nothing that worked before behaves differently.

### 3.x to 4.0

Chart 4.0 tracks the bot's 2.0 release, which replaced its environment-only configuration with
the layered, file-first loader every chart in this repository now uses. The `env` block that
described that environment is gone; a `helm upgrade` with 3.x values fails schema validation
naming the offending key rather than starting a pod on the defaults.

| Before | After |
|---|---|
| `env.webHook` | `discord.webhookUrl` |
| `env.checkInterval` | `feed.checkIntervalSecs` |
| `env.logLevel` | `telemetry.logLevel` (now `TRACE`/`DEBUG`/`INFO`/`WARN`/`ERROR`) |
| `env.sentryDns` | `telemetry.sentryDsn` |

**An existing Secret has to be re-keyed** from `webHook` to `discord__webhook_url`:

```shell
kubectl create secret generic netcup-webhook   --namespace [NAMESPACE]   --from-literal=discord__webhook_url="$(kubectl get secret netcup-webhook -n [NAMESPACE] -o jsonpath='{.data.webHook}' | base64 -d)"   --dry-run=client -o yaml | kubectl apply -f -
```

One fix rides along: `metrics.ip` is now a real value and defaults to `0.0.0.0`. The 3.x chart
read a `metrics.ip` that its `values.yaml` never declared, so the exporter kept the bot's
`127.0.0.1` default and the PodMonitor scraped a refused connection.

## Network policies, and what Cilium adds

`networkPolicy.engine` picks the dialect the same rules are written in:

```yaml
networkPolicy:
  enabled: true
  engine: cilium   # kubernetes (default) | cilium | both
```

Every value is translated either way, so switching is a one-line change rather than a re-authoring.
`both` emits both objects for a CNI migration — additive, not stricter, since policies selecting
one pod union their allowances.

The portable API can only name destinations by IP, so "may reach the internet over HTTPS" has to be
written `0.0.0.0/0` on 443 with the private ranges and the cloud metadata endpoint carved out — a
rule that, read honestly, permits a compromised container to reach every public host that exists.
Cilium can say the thing that was actually meant:

```yaml
networkPolicy:
  enabled: true
  engine: cilium
  egress:
    https:
      enabled: false     # drop the CIDR rule; the FQDN rule replaces it
  cilium:
    description: "outbound to the hosts this actually talks to"
    egress:
      toFQDNs:
        - matchName: api.example.com
        - matchPattern: "*.cdn.example.com"
      dnsMatchPatterns:
        - matchPattern: "*.example.com"
      toEntities:
        - kube-apiserver
      httpRules:
        - method: GET
          path: "/v1/.*"
```

`toFQDNs` is enforced against the addresses Cilium's DNS proxy saw returned for that name, so the
DNS rule has to stay on — the render fails rather than leaving an FQDN rule that silently matches
nothing. `enableDefaultDeny` is stated rather than implied, which is what makes the intentional
default-deny case (policies enabled, rule lists empty) actually deny.

When the chart is exposed through a Gateway, the policy admits the Gateway's data plane by
deriving the peer from `gateway.parentRefs` — `gateway.networking.k8s.io/gateway-name`, the label
Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision. Naming
the Gateway a second time under `networkPolicy` would be a second place to edit on a rename, and a
policy pointing at the wrong Gateway looks correct and blocks everything.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Extra configuration, expressed as the TOML tree of [the bot's README](https://github.com/TimSchoenle/netcup-offer-bot#configuration) (`feed.check_interval_secs`, `metrics.port`, ...). Merged over everything the chart derives from the values above, so it can both extend and override them. Rendered into the mounted ConfigMap — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount | object | `{"configDir":"/etc/netcup-offer-bot/config","secretsDir":"/etc/netcup-offer-bot/secrets"}` | Where the rendered configuration and the credential file land in the container. |
| configMount.configDir | string | `"/etc/netcup-offer-bot/config"` | Directory the rendered `config.toml` is mounted at, passed as `NETCUP_OFFER_BOT_CONFIG`. |
| configMount.secretsDir | string | `"/etc/netcup-offer-bot/secrets"` | Directory the credential file is mounted at, passed as `NETCUP_OFFER_BOT_SECRETS_DIR`. |
| discord | object | `{"webhookUrl":""}` | Where the offers are posted. The webhook is a bearer credential, so it is rendered into the Secret and the render fails when neither it nor `existingSecret` supplies one. |
| discord.webhookUrl | string | `""` | Discord webhook the offers are posted to (`discord.webhook_url`). Rendered into the chart's Secret and mounted as a file rather than passed as an environment variable, so it never appears in `kubectl describe pod` or in the environment of a child process. Required unless `existingSecret` supplies it. |
| existingSecret | string | `""` | Name of an existing Secret holding the bot's credentials, which keeps them out of `values.yaml` and out of the Helm release object. **Its keys are configuration paths, not free-form names**: `discord__webhook_url` and, once `telemetry.sentry.enabled` is set, `telemetry__sentry__dsn` — because the file name is what the loader parses. Set, the chart renders no Secret of its own and `discord.webhookUrl` / `telemetry.sentry.dsn` are ignored. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the application container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| feed | object | `{"checkIntervalSecs":180}` | The RSS poll loop. Only the interval is configurable; which feed the bot watches is not a configuration key. |
| feed.checkIntervalSecs | int | `180` | Seconds between two RSS feed checks (`feed.check_interval_secs`). |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| image | object | `{"pullPolicy":"","registry":"","repository":"timmi6790/netcup-offer-bot","tag":"v3.0.0@sha256:973e861a1e546066d87520994643b0d7208c0dfafd583c2055b094caec9d1648"}` | Container image the pod runs, composed as `registry/repository:tag`. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timmi6790/netcup-offer-bot"` | The container image repository. |
| image.tag | string | `"v3.0.0@sha256:973e861a1e546066d87520994643b0d7208c0dfafd583c2055b094caec9d1648"` | The container image tag. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| metrics | object | `{"enabled":false,"ip":"0.0.0.0","podMonitor":{"enabled":true,"interval":"1m","labels":{},"scrapeTimeout":"30s"},"port":9184}` | The Prometheus exporter and the PodMonitor that scrapes it. The bot binds no metrics listener at all until `enabled` is set. |
| metrics.enabled | bool | `false` | Enable Prometheus metrics endpoint. |
| metrics.ip | string | `"0.0.0.0"` | Address the Prometheus exporter binds (`metrics.ip`). The bot's own default is `127.0.0.1`, which answers nothing from outside the container — a PodMonitor pointed at it scrapes a refused connection. |
| metrics.podMonitor | object | `{"enabled":true,"interval":"1m","labels":{},"scrapeTimeout":"30s"}` | PodMonitor configuration for Prometheus Operator integration. Renamed from `serviceMonitor`: the chart has always rendered a PodMonitor, and there is no Service to monitor. |
| metrics.podMonitor.enabled | bool | `true` | Create the PodMonitor. Requires the Prometheus Operator CRDs. |
| metrics.podMonitor.interval | string | `"1m"` | Metrics scrape interval (e.g., 1m, 30s). |
| metrics.podMonitor.labels | object | `{}` | Extra labels for the PodMonitor, e.g. the `release` label a Prometheus Operator instance selects on. |
| metrics.podMonitor.scrapeTimeout | string | `"30s"` | Timeout for metrics scraping (e.g., 30s). |
| metrics.port | int | `9184` | Port the Prometheus exporter listens on (`metrics.port`). |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"cilium":{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}},"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration |
| networkPolicy.cilium | object | `{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}}` | Cilium-only additions, used when `engine` is `cilium` or `both`. Everything above is translated into the CiliumNetworkPolicy automatically; these are the rules the portable API has no way to express.  `extraIngress`, `extraEgress` and the per-section `customRules` above are *not* carried over: those are verbatim `networking.k8s.io/v1` rule objects and are not valid CNP. The fields below are their counterparts. |
| networkPolicy.cilium.description | string | `""` | `spec.description`, which Cilium surfaces in `cilium policy get` and in Hubble flow verdicts. The one place to record why a rule exists where an operator debugging a drop will actually see it. |
| networkPolicy.cilium.egress | object | `{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]}` | Cilium-only egress rules. |
| networkPolicy.cilium.egress.customRules | list | `[]` | Additional egress rules in CiliumNetworkPolicy form, appended verbatim. |
| networkPolicy.cilium.egress.dnsMatchPatterns | list | `[]` | What the DNS proxy may resolve, e.g. `- matchPattern: "*.example.com"`. Defaults to everything, which only permits the lookup — an answer is still only reachable if some rule allows the address. |
| networkPolicy.cilium.egress.entityPorts | list | `[]` | Restrict the `toEntities` rule to specific ports. Empty means all ports. |
| networkPolicy.cilium.egress.fqdnPorts | list | `[]` | Ports the `toFQDNs` rule allows. Defaults to TCP/443. |
| networkPolicy.cilium.egress.httpRules | list | `[]` | L7 HTTP rules layered onto the `toFQDNs` rule, e.g. `- method: GET` / `path: "/v1/.*"`. Turns "may reach this host" into "may make these requests to this host". Costs a proxy hop per connection. |
| networkPolicy.cilium.egress.toEntities | list | `[]` | Named destination sets, e.g. `world` for everything outside the cluster, or `kube-apiserver`. Not a synonym for the `egress.cidr`/`except` translation: `world` does not carve out the cloud metadata endpoint the way those defaults do. |
| networkPolicy.cilium.egress.toFQDNs | list | `[]` | Destinations by name rather than by address, e.g. `- matchName: api.example.com` or `- matchPattern: "*.example.com"`. This is the rule the CIDR-based `egress.https` was always a poor approximation of: "may reach the internet on 443" permits every public host that exists, where this permits the ones the application actually talks to.  Enforced against the addresses Cilium's DNS proxy saw returned for the name, so `egress.dns.enabled` must stay on — the render fails if it is not. |
| networkPolicy.cilium.enableDefaultDeny | bool | `true` | State default-deny explicitly rather than relying on it being implied by the presence of rules. This is what makes the intentional default-deny case — a policy with an empty rule list — actually deny, instead of being treated as no policy at all. Cilium 1.16+. |
| networkPolicy.cilium.extraEgress | list | `[]` | Extra egress rules in CiliumNetworkPolicy form, appended regardless of `egress.enabled`. |
| networkPolicy.cilium.extraIngress | list | `[]` | Extra ingress rules in CiliumNetworkPolicy form, appended regardless of `ingress.enabled`. |
| networkPolicy.cilium.ingress | object | `{"customRules":[],"fromEntities":[]}` | Cilium-only ingress rules. |
| networkPolicy.cilium.ingress.customRules | list | `[]` | Additional ingress rules in CiliumNetworkPolicy form, appended verbatim. |
| networkPolicy.cilium.ingress.fromEntities | list | `[]` | Named source sets, e.g. `cluster`, `host`, `remote-node`, `world`, `kube-apiserver`. A named entity stays correct when the cluster is renumbered; a CIDR list does not. |
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
| networkPolicy.engine | string | `"kubernetes"` | Which policy dialect to render. `kubernetes` emits the portable `networking.k8s.io/v1` pair; `cilium` emits `CiliumNetworkPolicy`, which can express FQDN destinations, named entities and L7 rules that the portable API cannot; `both` emits both, for the window in which a cluster is migrating between CNIs.  The engine picks the dialect, not the rules: every value below is translated either way. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended regardless of `egress.enabled`. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended regardless of `ingress.enabled`. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress configuration |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Ingress Controller configuration |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from Ingress Controller |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace where Ingress Controller is running (default: traefik) |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector for Ingress Controller (default: Traefik label) |
| networkPolicy.ingress.customRules | list | `[]` | Custom ingress rules |
| networkPolicy.ingress.enabled | bool | `true` | Enable ingress rules |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}` | Monitoring configuration for ingress |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from monitoring namespace |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where monitoring tools are running |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Namespace selector matching the monitoring namespace, replacing `namespace` when set. For a Prometheus labelled rather than named, or one of several namespaces that scrape. |
| networkPolicy.ingress.monitoring.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| persistence | object | `{"data":{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"10Mi","storageClassName":""}}` | The volumes the pod mounts. One today, holding the seen-offer state. |
| persistence.data | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"10Mi","storageClassName":""}` | Configuration for persistent data storage. The bot keeps its seen-offer state on disk; disabling persistence means it re-announces every offer after a restart. |
| persistence.data.accessMode | string | `"ReadWriteOnce"` | The access mode for the persistent volume. `ReadWriteOnce` forces the Deployment to the `Recreate` update strategy: a rolling update would wedge, because the replacement pod cannot attach a volume the outgoing pod still holds. |
| persistence.data.annotations | object | `{}` | Annotations for the PersistentVolumeClaim, e.g. `helm.sh/resource-policy: keep`. |
| persistence.data.enabled | bool | `true` | Create a PersistentVolumeClaim. When disabled, an emptyDir is used instead. |
| persistence.data.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.data.size | string | `"10Mi"` | The storage size requested for the volume. |
| persistence.data.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. Ignored when `affinity` is set. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1000,"runAsGroup":1000,"runAsUser":1000}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `1000` | Group ID for file system access |
| podSecurityContext.runAsGroup | int | `1000` | Primary group ID to run as |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name |
| replicaCount | int | `1` | Number of replicas. The bot holds a ReadWriteOnce volume, so more than one replica will not schedule. |
| resources | object | `{"limits":{"memory":"20Mi"},"requests":{"cpu":"10m","memory":"15Mi"}}` | Requests and limits for the container, passed through to the pod spec unchanged. |
| resources.limits | object | `{"memory":"20Mi"}` | Resource limits for the container. |
| resources.limits.memory | string | `"20Mi"` | Maximum allowed memory usage. |
| resources.requests | object | `{"cpu":"10m","memory":"15Mi"}` | Resource requests for the container. |
| resources.requests.cpu | string | `"10m"` | Minimum CPU requested by the container. The bot is idle between RSS polls; this only has to keep it out of BestEffort. Without a CPU request the pod is BestEffort and is the first thing evicted under node pressure. |
| resources.requests.memory | string | `"15Mi"` | Minimum guaranteed memory allocation. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| serviceAccount | object | `{"annotations":{},"automountToken":false,"create":true,"name":""}` | ServiceAccount the pods run under. `create: false` with no `name` means the `default` one. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| telemetry | object | `{"logLevel":"INFO","sentry":{"attachStacktraces":true,"breadcrumbLevel":"info","captureLevel":"error","debug":false,"dsn":"","enabled":false,"environment":"","maxBreadcrumbs":100,"release":"","sampleRate":1,"serverName":"","shutdownTimeoutSecs":2,"tracesSampleRate":0}}` | Logging and error reporting. `log_level` is rendered under `telemetry` in `config.toml`; the Sentry DSN is a credential and goes to the Secret instead. Both are read once as the process boots rather than re-read from the mount, so a change here needs a restart — `configMount.rolloutOnChange` is what turns one into a rollout. |
| telemetry.logLevel | string | `"INFO"` | Log level (`telemetry.log_level`). |
| telemetry.sentry | object | `{"attachStacktraces":true,"breadcrumbLevel":"info","captureLevel":"error","debug":false,"dsn":"","enabled":false,"environment":"","maxBreadcrumbs":100,"release":"","sampleRate":1,"serverName":"","shutdownTimeoutSecs":2,"tracesSampleRate":0}` | Sentry error reporting and tracing. **Off**, and wholly inert while it is: no client, no panic hook and no layer is installed, and nothing leaves the process.  Two things the chart deliberately does not do for you. It never reads the DSN, so it cannot name the ingest host in the NetworkPolicies. The defaults do happen to cover it — `networkPolicy.egress.https` permits TCP/443 to `0.0.0.0/0` minus private space — but a deployment that narrowed `networkPolicy.egress.cidr`, turned `egress.https` off, or replaced the CIDR rule with `networkPolicy.cilium.egress.toFQDNs` has to name the endpoint itself, and getting that wrong is silent: an SDK that cannot reach its endpoint queues events and then discards them, so the project stays empty, which reads as "no errors". And it configures nothing inside Sentry — the project, its quota and its server-side data-scrubbing rules are yours. |
| telemetry.sentry.attachStacktraces | bool | `true` | Attach a stack trace to events that carry none of their own (`telemetry.sentry.attach_stacktraces`). |
| telemetry.sentry.breadcrumbLevel | string | `"info"` | Least severe `tracing` level kept as a breadcrumb, the trail attached to the next issue (`telemetry.sentry.breadcrumb_level`). Records at or above `captureLevel` become issues instead. Independent of `logLevel` on purpose: the trail keeps being collected however quiet stdout is. Quote `"off"`. |
| telemetry.sentry.captureLevel | string | `"error"` | Least severe `tracing` level reported as a Sentry issue (`telemetry.sentry.capture_level`). Quote `"off"` — unquoted, YAML reads it as the boolean `false`. This one sits *under* the subscriber's own filter, so a record `logLevel` drops never reaches Sentry either. |
| telemetry.sentry.debug | bool | `false` | Print the SDK's own diagnostics to stderr (`telemetry.sentry.debug`). For proving a DSN works, not for running. |
| telemetry.sentry.dsn | string | `""` | Ingest URL, `https://<key>@<host>/<project>`. A credential — the embedded key is a bearer token for the project's ingest endpoint — so it is delivered as a file in `NETCUP_OFFER_BOT_SECRETS_DIR` under `telemetry__sentry__dsn` and never written into the ConfigMap. Leave it empty and put that key in the Secret named by `existingSecret` to keep it out of `helm get values` entirely. |
| telemetry.sentry.enabled | bool | `false` | Initialise the Sentry client (`telemetry.sentry.enabled`). Off, every other key here is inert. On, the bot refuses to boot without a DSN rather than starting with a reporter that reports nowhere, so this and `dsn` are set together. |
| telemetry.sentry.environment | string | `""` | Environment tag on every event (`telemetry.sentry.environment`). Empty lets the bot send `production`, or `development` from a debug build. |
| telemetry.sentry.maxBreadcrumbs | int | `100` | How many breadcrumbs one event carries (`telemetry.sentry.max_breadcrumbs`). |
| telemetry.sentry.release | string | `""` | Release tag on every event (`telemetry.sentry.release`). Empty uses the version the binary was built from, which is what makes a regression attributable to a deploy and what the image's debug files are indexed under — set this only to match a release you create in Sentry under a name of your own. |
| telemetry.sentry.sampleRate | float | `1` | Fraction of captured events actually sent (`telemetry.sentry.sample_rate`). A blunt volume cap — it drops whole issues, not repetitions of one — so leave it at `1` unless a quota forces otherwise. |
| telemetry.sentry.serverName | string | `""` | Host tag on every event (`telemetry.sentry.server_name`). Empty reports none, which is the right answer here: the value would be one pod's generated name, not anything an operator chose. |
| telemetry.sentry.shutdownTimeoutSecs | int | `2` | How long process exit waits for queued events to drain (`telemetry.sentry.shutdown_timeout_secs`). It is the only flush this process performs, and it is spent on every pod shutdown, so keep it well inside `terminationGracePeriodSeconds`. |
| telemetry.sentry.tracesSampleRate | float | `0` | Fraction of traces that are recorded (`telemetry.sentry.traces_sample_rate`). `0` records none, and nothing hands this process a trace to continue, so at `0` no spans are built at all — which is what makes the feature free to switch on for error reporting alone. `0.05`-`0.2` is an ordinary production figure. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability |

## Source Code

* <https://github.com/TimSchoenle/netcup-offer-bot>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
