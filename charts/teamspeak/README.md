# teamspeak

![Version: 1.0.1](https://img.shields.io/badge/Version-1.0.1-informational?style=flat-square) ![AppVersion: 3.13.8](https://img.shields.io/badge/AppVersion-3.13.8-informational?style=flat-square)

This chart deploys a TeamSpeak 3 server hardened to the restricted Pod Security Standard, with optional persistence, an optional Prometheus metrics exporter sidecar, Grafana dashboards and Prometheus alerting rules.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- Acceptance of the [TeamSpeak end user license agreement](https://teamspeak.com/en/features/licensing/)
  (`license.accept=true`) — the chart refuses to render without it
- A `LoadBalancer` implementation, or another way to publish a UDP port, if clients outside the
  cluster are meant to connect
- The Prometheus Operator CRDs, if `metrics.podMonitor` or `metrics.prometheusRule` are enabled

## Get Repository Info

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
```

## Install Chart

```shell
helm install [RELEASE_NAME] timschoenle/teamspeak \
  --namespace [NAMESPACE] \
  --create-namespace \
  --set license.accept=true
```

## Upgrade Chart

```shell
helm upgrade [RELEASE_NAME] timschoenle/teamspeak \
  --namespace [NAMESPACE]
```

## Uninstall Chart

```shell
helm uninstall [RELEASE_NAME] --namespace [NAMESPACE]
```

> [!WARNING]
> The PersistentVolumeClaim is owned by the release, so `helm uninstall` deletes it — and with
> it the server's identity, database, channels and permissions. Set
> `persistence.data.annotations."helm\.sh/resource-policy"=keep` before you need it.

## First start

The ServerAdmin privilege key is printed exactly once, into the log of the very first start.
Nobody can administer the server without it:

```shell
kubectl logs -n [NAMESPACE] -l app.kubernetes.io/instance=[RELEASE_NAME] -c teamspeak | grep -i token
```

The same log carries the generated ServerQuery password, unless one was supplied through
`serverQuery.adminPassword` or `existingSecret`.

## Exposing the server

TeamSpeak clients dial the voice port they were handed; there is no protocol-level redirect.
**The port the outside world connects to has to be the port the server listens on.** That has
three consequences:

- `LoadBalancer` is the only Service type that works unmodified, which is why it is the
  default. The chart publishes `server.voicePort` (UDP) and `server.fileTransferPort` (TCP)
  on it, and the node ports behind it stay auto-allocated because nothing dials them.
- `NodePort` only works if the cluster's `--service-node-port-range` is widened to include
  9987, and the node port is pinned to it with `service.voice.nodePort`.
- `ClusterIP` reaches nobody outside the cluster; use it only when something else in the
  cluster fronts the server.
- The Service is mixed-protocol (UDP + TCP). `LoadBalancer` with mixed protocols needs
  Kubernetes 1.26+, or 1.20+ with the `MixedProtocolLBService` feature gate, and a load
  balancer implementation that supports it. Where it is not supported, disable
  `service.fileTransfer` and publish it separately.

`service.externalTrafficPolicy` defaults to `Local`, which preserves the client's source
address. The server's ban list and flood protection operate on that address; with `Cluster`
every client appears to come from a node and one abusive client gets everyone banned.

## Persistence

`/var/ts3server` holds the SQLite database, the server keypair, uploaded avatars and icons,
and the logs. Persistence is on by default because without it every restart produces a
*different* server: new unique ID, new admin token, no channels, and every client's
permissions gone.

The claim is `ReadWriteOnce`, which forces the Deployment to the `Recreate` update strategy —
a rolling update would deadlock, because the replacement pod cannot attach a volume the
outgoing pod still holds.

`replicaCount` is capped at 1 on purpose. Two replicas against one database corrupt it; two
against separate volumes are two unrelated servers behind one Service.

## Security

The pod satisfies the restricted Pod Security Standard out of the box:

- runs as the unprivileged `ts3server` account (UID/GID 9987) the official image provides
- read-only root filesystem, all Linux capabilities dropped, no privilege escalation,
  `seccompProfile: RuntimeDefault`
- no ServiceAccount token mounted

The image's entrypoint writes `ts3server.ini` and `ts3db.ini` into `/var/run/ts3server` on
every start. That path is outside the data volume, so the chart mounts a small `emptyDir`
there; without it a read-only root filesystem kills the container before the server binary
ever runs.

Credentials never reach the ConfigMap. `serverQuery.adminPassword` and `database.password`
are placed in a Secret and referenced with `secretKeyRef`; `existingSecret` keeps them out of
`values.yaml` and out of the Helm release object entirely.

The raw ServerQuery protocol sends its password in the clear, so it is not published on the
Service and not opened by the NetworkPolicy. The metrics exporter does not need it to be:
it connects over the pod loopback interface, which never leaves the pod.

## Metrics, dashboards and alerts

`metrics.enabled` adds a [ts3exporter](https://github.com/hikhvar/ts3exporter) sidecar. It
logs into ServerQuery as `serveradmin` over `127.0.0.1`, an address the server's default
`query_ip_allowlist.txt` exempts from both the IP allowlist and the flood limiter. A known
ServerQuery password is therefore required — the chart fails the render rather than shipping
a sidecar that cannot authenticate.

| Toggle | What it creates |
| --- | --- |
| `metrics.enabled` | the exporter sidecar, on port 9189 |
| `metrics.podMonitor.enabled` | a `PodMonitor` (the metrics port is deliberately absent from the Service) |
| `metrics.prometheusRule.enabled` | five alerting rules, each individually switchable |
| `metrics.dashboards.enabled` | a labelled ConfigMap the Grafana sidecar loads |

`metrics.channelMetrics` is off by default: each scrape then costs `(2 + channels) *
virtualservers` ServerQuery commands, which gets slow quickly.

The exporter exits if it cannot log in at startup, so on a cold start it restarts a couple of
times while the server finishes booting. That is expected and self-correcting; a sidecar that
is *still* restarting after a minute or two means the password is wrong.

```yaml
license:
  accept: true

serverQuery:
  adminPassword: "" # set this, or point existingSecret at a Secret that holds it

metrics:
  enabled: true
  podMonitor:
    enabled: true
    labels:
      release: kube-prometheus-stack
  prometheusRule:
    enabled: true
    labels:
      release: kube-prometheus-stack
  dashboards:
    enabled: true
    annotations:
      grafana_folder: TeamSpeak
```

## Network policies

`networkPolicy.enabled` renders one policy per direction. Both are default-deny with the
generated rules added on top, and every egress rule carries a `to:` selector — a rule that
lists only `ports:` is not a restriction, because the NetworkPolicy API reads a missing `to`
as "all destinations".

Two outbound rules are specific to TeamSpeak:

- **TCP 2008** to `accounting.teamspeak.com` (`egress.accounting`, on by default). The server
  checks its licence there on startup and periodically afterwards, and takes the virtual
  server offline when it cannot reach it. This is not optional.
- **UDP 2010** to `weblist.teamspeak.com` (`egress.weblist`, off by default). Only needed if
  the server advertises itself on the public server list.

The internet-facing rules exclude RFC1918 space and `169.254.0.0/16`, which covers the cloud
instance metadata endpoint.

## External database

The bundled SQLite database is the right answer for a single server. `database.plugin` can be
switched to `ts3db_mariadb` or `ts3db_postgresql` for setups that need a shared instance; the
chart then requires `database.host` and a password source, and derives the schema directory
(`create_mariadb` / `create_postgresql`) automatically.

```yaml
license:
  accept: true

database:
  plugin: ts3db_mariadb
  host: mariadb.data.svc.cluster.local
  port: 3306
  name: teamspeak
  user: teamspeak
  password: "" # or existingSecret

server:
  machineId: primary # required once several servers share one database
```

## Configuration

The following table lists the configurable parameters of the chart and their default values.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| database.clientKeepDays | int | `30` | Days of client history retained in the database. |
| database.connections | int | `10` | Size of the database connection pool. |
| database.existingSecretPasswordKey | string | `"database-password"` | Key inside `existingSecret` that holds the database password. |
| database.host | string | `""` | Database host. Required unless the plugin is `ts3db_sqlite3`. |
| database.name | string | `"teamspeak"` | Database name. |
| database.password | string | `""` | Database password. Ignored when `existingSecret` is set. |
| database.plugin | string | `"ts3db_sqlite3"` | Database backend. `ts3db_sqlite3` keeps everything in the data volume and is the right answer for a single server; the external backends exist for multi-instance setups. |
| database.port | int | `3306` | Database port. |
| database.user | string | `"teamspeak"` | Database user. |
| database.waitUntilReady | int | `30` | Seconds to wait for an external database to accept connections before giving up. |
| existingSecret | string | `""` | Name of an existing Secret holding the ServerQuery admin password (and, for external databases, the database password). When set, the chart creates no Secret of its own and `serverQuery.adminPassword` / `database.password` are ignored — which keeps both out of `values.yaml` and out of the Helm release object. |
| extraEnv | list | `[]` | Additional environment variables for the server container. |
| extraServerConfig | object | `{}` | Additional `TS3SERVER_*` environment variables written to the ConfigMap verbatim, for settings this chart does not model explicitly (see the image's entrypoint for the full list). Values are rendered through the template engine, so `{{ .Release.Name }}` works. Never put credentials here — the ConfigMap is not a Secret. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the server container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"teamspeak"` | The container image repository. Defaults to the official TeamSpeak image, which is published and signed by TeamSpeak Systems GmbH. |
| image.tag | string | `"3.13.8@sha256:15acbc64c92f57ef1fd8dd203791fa7f70a14707e60ee494132f26b5ca265c6b"` | The container image tag. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| license.accept | bool | `false` | Accept the TeamSpeak end user license agreement (<https://teamspeak.com/en/features/licensing/>). The server refuses to start without it, so the chart fails the render rather than shipping a pod that crash-loops. |
| license.existingSecret | string | `""` | Name of an existing Secret holding a non-free `licensekey.dat`. When set, the file is mounted read-only and `licensepath` is pointed at it. Leave empty to run on the free 32-slot license. |
| license.existingSecretKey | string | `"licensekey.dat"` | Key inside `license.existingSecret` that holds the license file. |
| livenessProbe | object | `{"enabled":true,"failureThreshold":5,"periodSeconds":30,"tcpSocket":{"port":"filetransfer"},"timeoutSeconds":5}` | Liveness probe. Restarts the container when it stops accepting connections. |
| livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| livenessProbe.failureThreshold | int | `5` | Consecutive failures before the container is restarted. Deliberately tolerant: a restart drops every connected client, so it should never be the answer to one slow probe. |
| livenessProbe.periodSeconds | int | `30` | Probe interval. |
| livenessProbe.tcpSocket | object | `{"port":"filetransfer"}` | TCP handler for the probe. |
| livenessProbe.tcpSocket.port | string | `"filetransfer"` | Named container port to probe. |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| metrics.channelMetrics | bool | `false` | Collect per-channel metrics. Each scrape then costs `(2 + channels) * virtualservers` ServerQuery commands, so leave this off on servers with many channels. |
| metrics.dashboards | object | `{"annotations":{},"enabled":false,"label":"grafana_dashboard","labelValue":"1","namespace":""}` | Grafana dashboards, shipped as ConfigMaps for the Grafana sidecar dashboard loader. |
| metrics.dashboards.annotations | object | `{}` | Extra annotations, e.g. `grafana_folder` to place the dashboard in a folder. |
| metrics.dashboards.enabled | bool | `false` | Create the dashboard ConfigMap. |
| metrics.dashboards.label | string | `"grafana_dashboard"` | Label the Grafana sidecar selects dashboards on. |
| metrics.dashboards.labelValue | string | `"1"` | Value for that label. |
| metrics.dashboards.namespace | string | `""` | Namespace for the ConfigMap. Empty uses the release namespace. It has to be a namespace the Grafana sidecar watches. |
| metrics.enabled | bool | `false` | Run the Prometheus exporter sidecar. It talks ServerQuery over the pod loopback interface, which the server allowlists by default, so no query port has to leave the pod. Requires a known ServerQuery admin password (`serverQuery.adminPassword` or `existingSecret`). |
| metrics.ignoreFloodLimits | bool | `true` | Skip the exporter's client-side flood limiter. Correct for the sidecar: the server exempts loopback connections from flood control anyway, and the limiter would otherwise stretch a scrape past its timeout. |
| metrics.image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| metrics.image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| metrics.image.repository | string | `"ricardbejarano/ts3exporter"` | Exporter image repository. The default is a `FROM scratch`, non-root build of hikhvar/ts3exporter. |
| metrics.image.tag | string | `"0.0.7@sha256:3f3e2fceb82365320446728474502b1dd26de1123e6eb9ffcc0626003c743d0e"` | Exporter image tag, pinned by digest. |
| metrics.podMonitor | object | `{"enabled":true,"interval":"1m","labels":{},"metricRelabelings":[],"relabelings":[],"scrapeTimeout":"30s"}` | PodMonitor for Prometheus Operator integration. |
| metrics.podMonitor.enabled | bool | `true` | Create the PodMonitor. Requires the Prometheus Operator CRDs. |
| metrics.podMonitor.interval | string | `"1m"` | Metrics scrape interval. |
| metrics.podMonitor.labels | object | `{}` | Extra labels for the PodMonitor, e.g. the `release` label a Prometheus Operator instance selects on. |
| metrics.podMonitor.metricRelabelings | list | `[]` | Relabeling rules applied to scraped samples. |
| metrics.podMonitor.relabelings | list | `[]` | Relabeling rules applied to discovered targets. |
| metrics.podMonitor.scrapeTimeout | string | `"30s"` | Timeout for metrics scraping. Keep it comfortably above the ServerQuery round trip; channel metrics in particular are slow. |
| metrics.port | int | `9189` | Port the exporter serves `/metrics` on. Never published on the Service — scraping goes through the PodMonitor. |
| metrics.prometheusRule | object | `{"additionalRules":[],"enabled":false,"labels":{},"namespace":"","rules":{"exporterDown":{"enabled":true,"for":"10m","severity":"critical"},"queryCommandFailures":{"enabled":true,"for":"15m","severity":"warning"},"serverOffline":{"enabled":true,"for":"5m","severity":"critical"},"serverRestarted":{"enabled":true,"for":"0m","severity":"info","thresholdSeconds":300},"slotsNearlyFull":{"enabled":true,"for":"15m","severity":"warning","threshold":0.9}}}` | PrometheusRule carrying the alerting rules below. |
| metrics.prometheusRule.additionalRules | list | `[]` | Additional rules appended to the generated group, in Prometheus rule syntax. |
| metrics.prometheusRule.enabled | bool | `false` | Create the PrometheusRule. Requires the Prometheus Operator CRDs. |
| metrics.prometheusRule.labels | object | `{}` | Extra labels for the PrometheusRule, e.g. the `release` label a Prometheus Operator instance selects on. |
| metrics.prometheusRule.namespace | string | `""` | Namespace for the PrometheusRule. Empty uses the release namespace. |
| metrics.prometheusRule.rules | object | `{"exporterDown":{"enabled":true,"for":"10m","severity":"critical"},"queryCommandFailures":{"enabled":true,"for":"15m","severity":"warning"},"serverOffline":{"enabled":true,"for":"5m","severity":"critical"},"serverRestarted":{"enabled":true,"for":"0m","severity":"info","thresholdSeconds":300},"slotsNearlyFull":{"enabled":true,"for":"15m","severity":"warning","threshold":0.9}}` | Built-in alerting rules. Each one can be switched off individually and carries its own threshold, severity and `for` duration. |
| metrics.prometheusRule.rules.exporterDown | object | `{"enabled":true,"for":"10m","severity":"critical"}` | Fires when Prometheus cannot scrape the exporter at all — the pod is gone, the sidecar crashed, or the network policy is wrong. |
| metrics.prometheusRule.rules.exporterDown.enabled | bool | `true` | Enable the rule. |
| metrics.prometheusRule.rules.exporterDown.for | string | `"10m"` | How long the condition must hold before the alert fires. |
| metrics.prometheusRule.rules.exporterDown.severity | string | `"critical"` | Alert severity label. |
| metrics.prometheusRule.rules.queryCommandFailures | object | `{"enabled":true,"for":"15m","severity":"warning"}` | Fires when ServerQuery commands start failing, which normally means the exporter's credentials went stale or it is being flood-limited — the metrics are lying by then. |
| metrics.prometheusRule.rules.queryCommandFailures.enabled | bool | `true` | Enable the rule. |
| metrics.prometheusRule.rules.queryCommandFailures.for | string | `"15m"` | How long the condition must hold before the alert fires. |
| metrics.prometheusRule.rules.queryCommandFailures.severity | string | `"warning"` | Alert severity label. |
| metrics.prometheusRule.rules.serverOffline | object | `{"enabled":true,"for":"5m","severity":"critical"}` | Fires when the exporter is reachable but the virtual server itself reports offline. |
| metrics.prometheusRule.rules.serverOffline.enabled | bool | `true` | Enable the rule. |
| metrics.prometheusRule.rules.serverOffline.for | string | `"5m"` | How long the condition must hold before the alert fires. |
| metrics.prometheusRule.rules.serverOffline.severity | string | `"critical"` | Alert severity label. |
| metrics.prometheusRule.rules.serverRestarted | object | `{"enabled":true,"for":"0m","severity":"info","thresholdSeconds":300}` | Fires when the server restarted recently. On a server whose state lives on an emptyDir this also means the identity and all permissions were just lost. |
| metrics.prometheusRule.rules.serverRestarted.enabled | bool | `true` | Enable the rule. |
| metrics.prometheusRule.rules.serverRestarted.for | string | `"0m"` | How long the condition must hold before the alert fires. |
| metrics.prometheusRule.rules.serverRestarted.severity | string | `"info"` | Alert severity label. |
| metrics.prometheusRule.rules.serverRestarted.thresholdSeconds | int | `300` | Uptime below this many seconds counts as a restart. |
| metrics.prometheusRule.rules.slotsNearlyFull | object | `{"enabled":true,"for":"15m","severity":"warning","threshold":0.9}` | Fires when the virtual server is running out of slots, so it can be resized before clients start getting turned away. |
| metrics.prometheusRule.rules.slotsNearlyFull.enabled | bool | `true` | Enable the rule. |
| metrics.prometheusRule.rules.slotsNearlyFull.for | string | `"15m"` | How long the condition must hold before the alert fires. |
| metrics.prometheusRule.rules.slotsNearlyFull.severity | string | `"warning"` | Alert severity label. |
| metrics.prometheusRule.rules.slotsNearlyFull.threshold | float | `0.9` | Fraction of the slot limit that counts as nearly full. |
| metrics.resources | object | `{"limits":{"memory":"64Mi"},"requests":{"cpu":"10m","memory":"24Mi"}}` | Resource requests and limits for the exporter sidecar. |
| metrics.resources.limits | object | `{"memory":"64Mi"}` | Resource limits for the exporter. |
| metrics.resources.limits.memory | string | `"64Mi"` | Maximum allowed memory usage. |
| metrics.resources.requests | object | `{"cpu":"10m","memory":"24Mi"}` | Resource requests for the exporter. |
| metrics.resources.requests.cpu | string | `"10m"` | Minimum CPU requested. Without a CPU request the pod drops to BestEffort and is the first thing evicted under node pressure. |
| metrics.resources.requests.memory | string | `"24Mi"` | Minimum guaranteed memory allocation. |
| metrics.user | string | `"serveradmin"` | ServerQuery account the exporter logs in as. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"egress":{"accounting":{"enabled":true,"port":2008},"cidr":"0.0.0.0/0","customRules":[],"database":{"enabled":true,"namespaceSelector":{},"podSelector":{}},"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"https":{"enabled":false},"weblist":{"enabled":false,"port":2010}},"enabled":false,"extraEgress":[],"extraIngress":[],"ingress":{"clients":{"cidrs":["0.0.0.0/0"],"enabled":true},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{}},"query":{"cidrs":[],"enabled":false}}}` | Network policy configuration. |
| networkPolicy.egress | object | `{"accounting":{"enabled":true,"port":2008},"cidr":"0.0.0.0/0","customRules":[],"database":{"enabled":true,"namespaceSelector":{},"podSelector":{}},"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"https":{"enabled":false},"weblist":{"enabled":false,"port":2010}}` | Egress configuration. |
| networkPolicy.egress.accounting | object | `{"enabled":true,"port":2008}` | TeamSpeak licensing/accounting service on TCP 2008. The server contacts it on startup and periodically afterwards; blocking it takes the virtual server offline. |
| networkPolicy.egress.accounting.enabled | bool | `true` | Allow egress to the accounting service. |
| networkPolicy.egress.accounting.port | int | `2008` | Accounting port. |
| networkPolicy.egress.cidr | string | `"0.0.0.0/0"` | Destination CIDR for the internet-facing egress rules. |
| networkPolicy.egress.customRules | list | `[]` | Custom egress rules. |
| networkPolicy.egress.database | object | `{"enabled":true,"namespaceSelector":{},"podSelector":{}}` | Egress to an external database. Only relevant when `database.plugin` is not SQLite. |
| networkPolicy.egress.database.enabled | bool | `true` | Allow egress to the database. Ignored while the SQLite plugin is in use. |
| networkPolicy.egress.database.namespaceSelector | object | `{}` | Namespace selector for the database. Empty means the release namespace. |
| networkPolicy.egress.database.podSelector | object | `{}` | Pod selector for the database. |
| networkPolicy.egress.dns | object | `{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}}` | DNS configuration for egress. |
| networkPolicy.egress.dns.enabled | bool | `true` | Allow egress to DNS. |
| networkPolicy.egress.dns.namespaceSelector | object | `{"kubernetes.io/metadata.name":"kube-system"}` | Namespace selector for the DNS service. |
| networkPolicy.egress.dns.podSelector | object | `{"k8s-app":"kube-dns"}` | Pod selector for the DNS service. |
| networkPolicy.egress.enabled | bool | `true` | Enable egress rules. With this off the policy is a default-deny. |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. Defaults exclude RFC1918 private space and link-local 169.254.0.0/16, which covers the cloud instance metadata endpoint. |
| networkPolicy.egress.https | object | `{"enabled":false}` | Outbound HTTPS. Not needed by the server itself; enable it only if something you added to the pod requires it. |
| networkPolicy.egress.https.enabled | bool | `false` | Allow egress to HTTPS (TCP/443). |
| networkPolicy.egress.weblist | object | `{"enabled":false,"port":2010}` | TeamSpeak public server list on UDP 2010. Only needed when the server advertises itself on the global list. |
| networkPolicy.egress.weblist.enabled | bool | `false` | Allow egress to the server list. |
| networkPolicy.egress.weblist.port | int | `2010` | Weblist port. |
| networkPolicy.enabled | bool | `false` | Enable network policies. |
| networkPolicy.extraEgress | list | `[]` | Egress rules appended verbatim, whether or not `networkPolicy.egress.enabled` is set. |
| networkPolicy.extraIngress | list | `[]` | Ingress rules appended verbatim, whether or not `networkPolicy.ingress.enabled` is set. |
| networkPolicy.ingress | object | `{"clients":{"cidrs":["0.0.0.0/0"],"enabled":true},"customRules":[],"enabled":true,"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{}},"query":{"cidrs":[],"enabled":false}}` | Ingress configuration. |
| networkPolicy.ingress.clients | object | `{"cidrs":["0.0.0.0/0"],"enabled":true}` | Client access to the voice and file transfer ports. |
| networkPolicy.ingress.clients.cidrs | list | `["0.0.0.0/0"]` | CIDRs clients may connect from. The default is the whole internet, which is what a public voice server needs; narrow it for a private deployment. |
| networkPolicy.ingress.clients.enabled | bool | `true` | Allow clients to reach the voice and file transfer ports. |
| networkPolicy.ingress.customRules | list | `[]` | Custom ingress rules. |
| networkPolicy.ingress.enabled | bool | `true` | Enable ingress rules. With this off the policy is a default-deny and nobody reaches the server at all. |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{}}` | Prometheus access to the exporter sidecar. |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from the monitoring namespace to the metrics port. |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where the Prometheus server runs. |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Namespace selector for the monitoring namespace. Defaults to matching `networkPolicy.ingress.monitoring.namespace` by name. |
| networkPolicy.ingress.query | object | `{"cidrs":[],"enabled":false}` | ServerQuery access. Off by default — the raw protocol is plaintext and the metrics sidecar reaches it over loopback, which no NetworkPolicy governs. |
| networkPolicy.ingress.query.cidrs | list | `[]` | CIDRs allowed to reach ServerQuery. Leave this as narrow as you possibly can. |
| networkPolicy.ingress.query.enabled | bool | `false` | Allow the ServerQuery ports to be reached from outside the pod. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| persistence.data | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"3Gi","storageClassName":""}` | Storage for `/var/ts3server`: the SQLite database, the server keypair, uploaded files and the logs. Without it every restart produces a brand new server — new unique ID, new admin token, no channels, and every client's permissions gone. |
| persistence.data.accessMode | string | `"ReadWriteOnce"` | The access mode for the persistent volume. `ReadWriteOnce` forces the Deployment to the `Recreate` update strategy: a rolling update would wedge, because the replacement pod cannot attach a volume the outgoing pod still holds. |
| persistence.data.annotations | object | `{}` | Annotations for the PersistentVolumeClaim. `helm.sh/resource-policy: keep` is worth setting here — it stops `helm uninstall` from deleting the server identity along with the release. |
| persistence.data.enabled | bool | `true` | Create a PersistentVolumeClaim. When disabled, an emptyDir is used instead and all server state is lost on restart. |
| persistence.data.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.data.size | string | `"3Gi"` | The storage size requested for the volume. |
| persistence.data.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. Ignored when `affinity` is set. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":9987,"runAsGroup":9987,"runAsUser":9987}` | Pod security context, merged over the preset. The identity fields match the `ts3server` account baked into the official image; changing them without also changing the image leaves the data volume unwritable. |
| podSecurityContext.fsGroup | int | `9987` | Group ID applied to the mounted volumes, so the non-root server can write its database and logs. |
| podSecurityContext.runAsGroup | int | `9987` | Primary group ID to run as. |
| podSecurityContext.runAsUser | int | `9987` | User ID to run as. |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name. |
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"periodSeconds":10,"tcpSocket":{"port":"filetransfer"},"timeoutSeconds":3}` | Readiness probe. Removes the pod from the Service while it cannot serve clients. |
| readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| readinessProbe.failureThreshold | int | `3` | Consecutive failures before the pod is taken out of the Service. |
| readinessProbe.periodSeconds | int | `10` | Probe interval. |
| readinessProbe.tcpSocket | object | `{"port":"filetransfer"}` | TCP handler for the probe. |
| readinessProbe.tcpSocket.port | string | `"filetransfer"` | Named container port to probe. |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout. |
| replicaCount | int | `1` | Number of replicas. A TeamSpeak server is a singleton: two instances on one database corrupt it, and two on separate volumes are two different servers. Only `0` (stopped) and `1` are meaningful. |
| resources.limits | object | `{"memory":"512Mi"}` | Resource limits for the server container. |
| resources.limits.memory | string | `"512Mi"` | Maximum allowed memory usage. No CPU limit is set on purpose: throttling a real-time voice mixer produces audible stutter, and a CPU limit protects nobody but the node's scheduler accounting. |
| resources.requests | object | `{"cpu":"100m","memory":"128Mi"}` | Resource requests for the server container. |
| resources.requests.cpu | string | `"100m"` | Minimum CPU requested. Voice mixing is latency-sensitive, so this reserves enough to keep the server off a fully contended core. |
| resources.requests.memory | string | `"128Mi"` | Minimum guaranteed memory allocation. |
| resourcesPreset | string | `""` | Named resource sizing. Ignored when `resources` is set. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{}` | Container security context, merged over the preset. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| server.fileTransferPort | int | `30033` | TCP port used for avatar, icon and channel file transfers. |
| server.hintsEnabled | bool | `true` | Send the client-side usage hints ("Did you know...") to connecting clients. |
| server.logAppend | bool | `false` | Append to the existing log file instead of starting a new one per launch. |
| server.logQueryCommands | bool | `false` | Log every ServerQuery command. Useful for auditing, noisy in normal operation. |
| server.machineId | string | `""` | Stable machine identifier written to the instance database. Set this when several servers share one database; leave empty otherwise. |
| server.queryPort | int | `10011` | TCP port of the plaintext (raw) ServerQuery interface. Always opened inside the pod so the metrics exporter and the probes can reach it; exposing it outside the pod is a separate decision made by `service.query` and the NetworkPolicy. |
| server.querySshPort | int | `10022` | TCP port of the SSH ServerQuery interface. Only opened when `serverQuery.protocols` includes `ssh`. |
| server.voicePort | int | `9987` | UDP port the voice server listens on. TeamSpeak clients dial this port directly, so whatever the outside world connects to must resolve to exactly this number — see the chart README on exposing the server. |
| serverQuery.adminPassword | string | `""` | Password for the `serveradmin` ServerQuery account. Leave empty to let the server generate one on first start and print it to the log. Required — here or via `existingSecret` — when `metrics.enabled` is true, because the exporter authenticates with it. |
| serverQuery.existingSecretPasswordKey | string | `"serveradmin-password"` | Key inside `existingSecret` that holds the ServerQuery admin password. |
| serverQuery.protocols | string | `"raw"` | ServerQuery protocols the server offers. `raw` is plaintext and must never be exposed beyond the pod; `ssh` is encrypted. The metrics exporter speaks `raw` over the pod loopback interface, so `raw` has to stay enabled while `metrics.enabled` is true. |
| serverQuery.skipBruteForceCheck | bool | `false` | Skip the ServerQuery brute-force protection. Leave disabled; it exists to slow down password guessing against the admin account. |
| serverQuery.timeout | int | `300` | Seconds an idle ServerQuery connection is kept open. |
| service.annotations | object | `{}` | Annotations for the Service, e.g. cloud load balancer configuration. |
| service.externalIPs | list | `[]` | External IPs the Service should also answer on. |
| service.externalTrafficPolicy | string | `"Local"` | External traffic policy. `Local` preserves the client source IP, which is what the server's ban and flood protection operates on — with `Cluster` every client appears to come from a node address and one abusive client can get everyone banned. |
| service.fileTransfer | object | `{"enabled":true,"nodePort":0}` | File transfer port (TCP). |
| service.fileTransfer.enabled | bool | `true` | Publish the file transfer port on the Service. Disabling it leaves voice working but breaks avatars, icons and channel file browsing. |
| service.fileTransfer.nodePort | int | `0` | Node port to pin. `0` lets Kubernetes allocate one. Under `NodePort`, pin it to `server.fileTransferPort`; the server hands that number to clients. |
| service.ipFamilyPolicy | string | `""` | IP family policy for the Service. |
| service.loadBalancerClass | string | `""` | LoadBalancer implementation to use, for clusters running more than one. |
| service.loadBalancerIP | string | `""` | Static IP to request for a LoadBalancer service. |
| service.loadBalancerSourceRanges | list | `[]` | CIDRs allowed to reach the LoadBalancer. Empty means everywhere. |
| service.query | object | `{"enabled":false,"nodePort":0}` | Plaintext (raw) ServerQuery port (TCP). |
| service.query.enabled | bool | `false` | Publish the raw ServerQuery port on the Service. Off by default: the protocol sends the admin password in the clear, and the in-pod metrics exporter does not need it. |
| service.query.nodePort | int | `0` | Node port to pin. `0` lets Kubernetes allocate one. |
| service.querySsh | object | `{"enabled":false,"nodePort":0}` | SSH ServerQuery port (TCP). Requires `ssh` in `serverQuery.protocols`. |
| service.querySsh.enabled | bool | `false` | Publish the SSH ServerQuery port on the Service. |
| service.querySsh.nodePort | int | `0` | Node port to pin. `0` lets Kubernetes allocate one. |
| service.type | string | `"LoadBalancer"` | Service type. `LoadBalancer` is the default and the only type that works unmodified: it answers on `server.voicePort` itself, which is the port clients dial. `ClusterIP` reaches nobody outside the cluster. `NodePort` only works when the node port range is widened to cover the voice port and `service.voice.nodePort` is pinned to it, because clients cannot be told to use a different one. |
| service.voice | object | `{"enabled":true,"nodePort":0}` | Voice port (UDP). This is the port TeamSpeak clients connect to. |
| service.voice.enabled | bool | `true` | Publish the voice port on the Service. |
| service.voice.nodePort | int | `0` | Node port to pin. `0` lets Kubernetes allocate one, which is correct for the default `LoadBalancer` type: the load balancer answers on `server.voicePort` and the node port behind it is an internal detail no client dials. Under `NodePort` the client dials the node port itself, so it has to be pinned to `server.voicePort` — which in turn needs `--service-node-port-range` widened to cover 9987. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account. |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token. |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account. |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty). |
| startupProbe | object | `{"enabled":true,"failureThreshold":36,"initialDelaySeconds":5,"periodSeconds":5,"tcpSocket":{"port":"filetransfer"},"timeoutSeconds":3}` | Startup probe. Gives the server room to create its database and keypair on first boot without the liveness probe killing it half way through. |
| startupProbe.enabled | bool | `true` | Enable the startup probe. |
| startupProbe.failureThreshold | int | `36` | Consecutive failures before the container is considered failed. 36 x 5s covers a three-minute first boot on slow storage. |
| startupProbe.initialDelaySeconds | int | `5` | Delay before the first probe. |
| startupProbe.periodSeconds | int | `5` | Probe interval. |
| startupProbe.tcpSocket | object | `{"port":"filetransfer"}` | TCP handler for the probe. The file transfer port is the one listener that is up regardless of which ServerQuery protocols are enabled. |
| startupProbe.tcpSocket.port | string | `"filetransfer"` | Named container port to probe. |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout. |
| strategy | object | `{}` | Deployment update strategy. Empty falls back to `Recreate` whenever the data volume is ReadWriteOnce, because a rolling update cannot hand that volume over. |
| terminationGracePeriodSeconds | int | `60` | Grace period for pod shutdown. The server flushes its database and disconnects clients on SIGTERM; cutting that short risks a corrupt SQLite file. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |

## Examples

### Public server on a LoadBalancer

```yaml
license:
  accept: true

serverQuery:
  adminPassword: change-me

service:
  type: LoadBalancer
  loadBalancerIP: 203.0.113.10

persistence:
  data:
    size: 5Gi
    annotations:
      helm.sh/resource-policy: keep

networkPolicy:
  enabled: true
  egress:
    weblist:
      enabled: true
```

### Licensed server

`licensepath` names a directory, and the file inside it has to be called `licensekey.dat`, so
the chart mounts the Secret key under that name:

```shell
kubectl create secret generic ts3-license \
  --namespace [NAMESPACE] \
  --from-file=licensekey.dat=./licensekey.dat
```

```yaml
license:
  accept: true
  existingSecret: ts3-license
```

### Settings the chart does not model

Anything the image's entrypoint understands can be passed through verbatim. Values are
rendered through the template engine, so release-scoped values work:

```yaml
extraServerConfig:
  TS3SERVER_DB_CLIENTKEEPDAYS: "90"
  TS3SERVER_MACHINE_ID: "{{ .Release.Name }}"
```

Never put credentials there — `extraServerConfig` lands in the ConfigMap, not the Secret.

## Source Code

* <https://github.com/TeamSpeak-Systems/teamspeak-linux-docker-images>
* <https://github.com/hikhvar/ts3exporter>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
