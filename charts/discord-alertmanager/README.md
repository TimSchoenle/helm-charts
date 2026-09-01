# discord-alertmanager

![Version: 0.1.0](https://img.shields.io/badge/Version-0.1.0-informational?style=flat-square) ![AppVersion: v0.2.0](https://img.shields.io/badge/AppVersion-v0.2.0-informational?style=flat-square)

This chart deploys discord-alertmanager, a Discord operator surface for Prometheus Alertmanager. It receives the version-4 webhook envelope, renders each alert as a live status card in a Discord channel, and lets an operator acknowledge, ignore, silence or investigate it without leaving the client — with file-backed configuration that reloads in place instead of restarting pods, SQLite or PostgreSQL storage, optional Prometheus metrics and alerting rules, and an optional AlertmanagerConfig that registers the receiver with the Prometheus Operator instead of leaving it to be wired by hand.

Alertmanager posts an alert to a webhook. This service turns it into a live status card in a
Discord channel and lets an operator acknowledge it, mute it, silence it or open its graph without
leaving the client. A silence goes to Alertmanager and stops every receiver, the pager included.
An ignore is bot-local and stops only Discord, which is what "stop pinging #ops at 3am but keep
paging" actually asks for.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A Discord bot token, and a channel or forum for it to post in
- At least one reachable Alertmanager endpoint — the bot refuses to start without one
- The Prometheus Operator CRDs, if `metrics.serviceMonitor`, `metrics.prometheusRule` or
  `alertmanagerConfig` is enabled
- The Gateway API CRDs and a `Gateway` to attach to, if `gateway.enabled=true`
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/discord-alertmanager \
  --namespace [NAMESPACE] --create-namespace \
  --values values.yaml
```

with `values.yaml`:

```yaml
existingSecret: discord-alertmanager

alertmanager:
  endpoints:
    - http://alertmanager-operated.monitoring.svc:9093

routes:
  - name: platform
    guild_id: 123456789012345678
    matchers: 'severity=~"warning|critical"'
    target:
      kind: channel
      id: 234567890123456789
```

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/discord-alertmanager -n [NAMESPACE]`,
remove with `helm uninstall [RELEASE_NAME] -n [NAMESPACE]`.

Two settings have no working default and the chart refuses to render without them. Both are
constructed in `main` before the listener binds, so a release missing either is a
CrashLoopBackOff rather than a degraded feature:

| Setting | Why |
|---|---|
| `discord.token` (or `existingSecret`) | the gateway client is built at boot |
| `alertmanager.endpoints` | `AlertmanagerClient::new` refuses an empty peer list |

## Credentials

Create the Secret yourself and reference it by name. **The keys are the configuration paths the
service reads, not free-form names** — the loader takes each credential out of the file name, so
`discord__token` is required and `token` is not read at all:

```shell
kubectl create secret generic discord-alertmanager \
  --namespace [NAMESPACE] \
  --from-literal=discord__token='...' \
  --from-literal=ingest__webhook_token="$(openssl rand -hex 32)"
```

```yaml
existingSecret: discord-alertmanager
```

The five keys this chart knows how to consume:

| Key | Setting | When |
|---|---|---|
| `discord__token` | `discord.token` | always |
| `ingest__webhook_token` | `ingest.webhookToken` | to authenticate the webhook |
| `alertmanager__bearer_token` | `alertmanager.bearerToken` | Alertmanager behind bearer auth |
| `alertmanager__basic_password` | `alertmanager.basicPassword` | Alertmanager behind basic auth |
| `storage__postgres__url` | `storage.postgres.url` | `storage.backend: postgres` |

The matching chart values are accepted as an alternative and make the chart render the Secret
itself. That puts the credentials into `values.yaml` and into the Helm release object, where
anyone who can run `helm get values` can read them — use it for a throwaway cluster, not for
anything real. `existingSecret` wins if both are set.

`just config-secrets discord-alertmanager` prints the same list from the vendored
contract rather than from this table, which is the copy that cannot go stale.

## Configuration

Everything the service reads is rendered into one `config.toml`, mounted as a ConfigMap and
pointed at by `DAM_CONFIG`. Nothing is passed as an environment variable, and that is deliberate:
the loader **fails the boot on a key supplied by both the environment and a file** rather than
resolving it by precedence, and a value that lives in a file is one the kubelet can rotate under
a running process.

The values cover the whole documented surface — 58 settings, one chart value each. `config` takes
the raw TOML tree for anything they do not, merged over the derived one:

```yaml
config:
  engine:
    dispatchers: 8
```

and `configExtraToml` is appended verbatim for what the renderer cannot express.

```shell
just explain discord-alertmanager
```

prints the mapping: every key the image reads, which chart value feeds it, and how.

## Routes

A route is "which alerts go to which Discord channel, and who gets mentioned". Routes declared in
`routes` are the file's and cannot be edited or deleted from Discord, which is what makes a
deployment reproducible from its manifests. `/route add` writes the other kind, which lives only
in the database.

```yaml
routes:
  - name: platform-critical
    guild_id: 123456789012345678
    matchers: 'severity="critical", namespace=~"prod-.*"'
    min_severity: critical
    target:
      kind: forum
      id: 234567890123456789
    group_strategy: alert
    mentions:
      roles: [345678901234567890]
      min_severity: critical
    escalation:
      after_secs: 900
      users: [456789012345678901]
    priority: 10
    continue_to_next: true
    enabled: true
```

`matchers` uses Alertmanager's own operators — `=`, `!=`, `=~`, `!~` — and a regex is fully
anchored. A route removed from this list is disabled rather than deleted, so the cards it created
keep their history.

> [!NOTE]
> `routes` is an array of tables. It is rendered through the chart's TOML writer like everything
> else, but a route needing a shape the writer cannot express belongs in `configExtraToml`, which
> is appended verbatim.

## Wiring Alertmanager to it

Two ways, and the second is the one this chart exists to make easy.

### By hand

```yaml
receivers:
  - name: discord-alertmanager
    webhook_configs:
      - url: http://discord-alertmanager.monitoring.svc:80/webhook
        send_resolved: true
```

### As an AlertmanagerConfig

`alertmanagerConfig.enabled` ships the receiver and its route as a Prometheus Operator object, the
same way `metrics.prometheusRule` ships the alerts. The URL is derived from this release's own
Service and `ingest.webhookPath`, and the bearer token is a reference to the Secret the chart
already renders — so neither can drift from what the listener actually serves.

```yaml
ingest:
  webhookToken: ""          # or existingSecret carrying ingest__webhook_token

alertmanagerConfig:
  enabled: true
  labels:
    alertmanagerConfig: platform   # what alertmanagerConfigSelector matches
  route:
    groupBy: [alertname, namespace]
    repeatInterval: 4h
    continue: true
```

**`continue: true` is the default and is the point.** It makes this bot an additional surface
rather than a replacement: the alert gets a Discord card *and* still reaches the pager. Setting it
to `false` makes Discord the only receiver for everything the route matches.

#### The namespace matcher, which is the thing that catches people out

The Prometheus Operator injects a `namespace = <the object's namespace>` matcher into every route
it grafts from an `AlertmanagerConfig`. By default, therefore, **this route only ever matches
alerts whose `namespace` label equals the namespace the object lives in.** The object is accepted,
the receiver exists, and for a cluster-wide receiver nothing arrives.

There are three answers, and which one is right depends on what the release is for:

| Want | Do |
|---|---|
| A bot watching its own namespace | nothing; the default is correct |
| A cluster-wide bot | `spec.alertmanagerConfigMatcherStrategy.type: None` on the Alertmanager |
| A cluster-wide bot, one config | `spec.alertmanagerConfiguration.name: <this object>`, and set `alertmanagerConfig.namespace` to Alertmanager's namespace |

The last form is documented by the operator as one where it "will not enforce a `namespace` label
for routes and inhibition rules". It also replaces the Alertmanager's whole configuration rather
than adding to it, so it is a decision about the monitoring stack and not only about this chart.

Both of the first two are fields on the Alertmanager custom resource, which this chart cannot
reach — it can only create the object and say so. `NOTES.txt` repeats this after every install
that enables it, and `DiscordAlertmanagerNeverReceivedAWebhook` is in the shipped rule set for
exactly the case where it was missed.

> [!NOTE]
> Alertmanager has had a native `discordConfigs` receiver since 0.25, and it is not this. That one
> posts a message. This one posts a card an operator can acknowledge, ignore, silence and open a
> graph from. Both can be routed to at once.

#### When Alertmanager is outside the cluster

Set `alertmanagerConfig.url` to the address it can actually reach, and publish the listener with
`ingress` or `gateway`. Set `ingest.webhookToken` before you do — the webhook path accepts any
well-formed envelope without one.

## Storage

`storage.backend` is a configuration key, not a build flag, and one binary answers both.

**`sqlite` (default)** keeps one file on one volume. The container runs with a read-only root
filesystem, so the file has to live under `persistence.data.mountPath`; `storage.sqlite.path`
defaults to a path inside it and the render is refused if it is moved outside. A
`ReadWriteOnce` claim also forces the Deployment to `Recreate`, because a rolling update would
deadlock on the volume.

**`postgres`** is what a release above one replica needs. The outbox lease that lets several
dispatchers work in parallel is `FOR UPDATE SKIP LOCKED` there and `BEGIN IMMEDIATE` on SQLite,
and only the first is safe across processes — so the chart refuses `replicaCount > 1` and
`autoscaling.enabled` while the backend is SQLite, rather than corrupting a database to find out.
A PostgreSQL release renders no PersistentVolumeClaim at all.

```yaml
storage:
  backend: postgres
  postgres:
    url: ""      # existingSecret, under storage__postgres__url

replicaCount: 3

podDisruptionBudget:
  enabled: true
  minAvailable: 2
```

A PodDisruptionBudget with `minAvailable` equal to `replicaCount` blocks node drains
indefinitely — keep it at least one below.

Turning persistence off puts SQLite on an `emptyDir`, which loses every card, thread link,
acknowledgement and ignore on each restart and re-posts alerts that are still firing. It is for
test installs.

## Observability

The listener serves `/metrics` beside `/webhook` — there is no second port. `metrics.serviceMonitor`
scrapes it and `metrics.prometheusRule` installs the alerting rules under `rules/`. The image's
own `observability.metricsEnabled` decides whether the path answers at all, and the chart refuses
a ServiceMonitor pointed at a listener that would serve 404.

```yaml
observability:
  metricsEnabled: true
  adminChannelId: 123456789012345678   # deadman and route-health notices

metrics:
  serviceMonitor:
    enabled: true
    labels:
      release: kube-prometheus-stack
  prometheusRule:
    enabled: true
    labels:
      release: kube-prometheus-stack
```

Nine alerts ship, covering the bot being unscrapeable, webhooks being refused, the outbox not
draining, Discord rate-limiting, and the reconciler having quietly become the delivery mechanism.
`disabledAlerts`, `severityOverrides`, `forOverrides` and `thresholds` tune them, and every one of
those is validated against the rules actually shipped — a name that matches nothing is refused
rather than leaving the values file claiming an alert is off while it pages.

**They are deliberately not routed through this bot.** A bot that has stopped delivering cannot
deliver the card that says so. Pair `DiscordAlertmanagerDown` with a receiver that does not depend
on it, and pair the bot's own deadman — `observability.adminChannelId`, which fires when no
webhook has arrived inside its window *and* Alertmanager is unreachable — with a `Watchdog` alert
in Prometheus, so that silence on one side is always noise on the other.

## Network policy

The interesting half here is ingress, and it is not the usual one. Two different things reach this
pod on the same port: Alertmanager posting webhooks, and Prometheus scraping `/metrics`. The
`monitoring` rule is therefore half the access control rather than an observability nicety.

The egress `https` rule carves RFC1918 and link-local out of its CIDR, so **an in-cluster
Alertmanager is not reachable until you add a rule for it** — without one the pod starts, the
reconciler never polls, and `/readyz` stays red.

```yaml
networkPolicy:
  enabled: true
  ingress:
    monitoring:
      enabled: true
      namespace: monitoring
  egress:
    customRules:
      - ports:
          - port: 9093
            protocol: TCP
        to:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: monitoring
            podSelector:
              matchLabels:
                app.kubernetes.io/name: alertmanager
```

Every rule you add must carry its own `to:`. A rule that lists only `ports:` is not a restriction —
the NetworkPolicy API reads a missing `to` as *any destination*, which includes the cloud instance
metadata endpoint at `169.254.169.254`.

`networkPolicy.engine: cilium` writes the same rules as a `CiliumNetworkPolicy`, where the bot's
egress can be stated as what it actually is — Discord's API and gateway, and one Alertmanager —
instead of as "the whole public internet on 443":

```yaml
networkPolicy:
  enabled: true
  engine: cilium
  egress:
    https:
      enabled: false
  cilium:
    egress:
      toFQDNs:
        - matchName: discord.com
        - matchPattern: "*.discord.gg"
      dnsMatchPatterns:
        - matchPattern: "*.discord.com"
        - matchPattern: "*.discord.gg"
```

## Probes

| Probe | Path | Why |
|---|---|---|
| startup, liveness | `/healthz` | asks nothing of the database or Alertmanager — wiring liveness to a dependency turns an outage into a restart loop that cannot fix it |
| readiness | `/readyz` | 503 while the store is unreachable or the Alertmanager poll has gone stale |

A failing readiness probe takes the pod out of the Service, which makes Alertmanager's delivery
fail and be retried rather than accepted and dropped. That is the intended behaviour, and it is
also why a pod that cannot reach Alertmanager never becomes ready — check the egress policy first.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| alertmanager | object | `{"basicPassword":"","basicUsername":null,"bearerToken":"","caBundle":null,"connectTimeoutSecs":2,"endpoints":[],"retry":{"initialBackoffMs":200,"maxBackoffSecs":10,"maxElapsedSecs":45},"timeoutSecs":10}` | Where Alertmanager is, how to authenticate to it, and how hard to retry. At least one endpoint is required: the client is built during startup and refuses an empty peer list, so a release without one exits rather than running without silences. |
| alertmanager.basicPassword | string | `""` | Password for basic authentication. Supply it through the secrets directory or `_FILE` (`alertmanager.basic_password`). Delivered as the secrets-directory file `alertmanager__basic_password`. |
| alertmanager.basicUsername | string | `nil` | Username for basic authentication. Ignored when `bearer_token` is set (`alertmanager.basic_username`). |
| alertmanager.bearerToken | string | `""` | Bearer token sent to Alertmanager. Supply it through the secrets directory or `_FILE` (`alertmanager.bearer_token`). Delivered as the secrets-directory file `alertmanager__bearer_token`. |
| alertmanager.caBundle | string | `nil` | PEM bundle of certificate authorities to trust in addition to the system roots (`alertmanager.ca_bundle`). |
| alertmanager.connectTimeoutSecs | int | `2` | Seconds to wait for a connection before trying the next endpoint (`alertmanager.connect_timeout_secs`). |
| alertmanager.endpoints | list | `[]` | Base URLs of the Alertmanager peers, tried in order (`alertmanager.endpoints`). |
| alertmanager.retry | object | `{"initialBackoffMs":200,"maxBackoffSecs":10,"maxElapsedSecs":45}` | Bounded backoff for a request that failed. Giving up is the point of the bound — an unbounded retry is how a client finishes off the Alertmanager it is waiting for. |
| alertmanager.retry.initialBackoffMs | int | `200` | Milliseconds to wait before the first retry (`alertmanager.retry.initial_backoff_ms`). |
| alertmanager.retry.maxBackoffSecs | int | `10` | Ceiling on a single wait, in seconds (`alertmanager.retry.max_backoff_secs`). |
| alertmanager.retry.maxElapsedSecs | int | `45` | Seconds to keep retrying one request before giving up on it (`alertmanager.retry.max_elapsed_secs`). |
| alertmanager.timeoutSecs | int | `10` | Seconds to wait for a whole request before giving up (`alertmanager.timeout_secs`). |
| alertmanagerConfig | object | `{"auth":{"enabled":true,"secretKey":"ingest__webhook_token","secretName":""},"enabled":false,"extraReceivers":[],"inhibitRules":[],"labels":{},"maxAlerts":0,"muteTimeIntervals":[],"namespace":"","receiverName":"discord-alertmanager","route":{"continue":true,"enabled":true,"groupBy":["alertname","namespace"],"groupInterval":"5m","groupWait":"30s","matchers":[],"repeatInterval":"4h","routes":[]},"sendResolved":true,"timeout":"","url":""}` | Register this bot as an Alertmanager receiver, declaratively, instead of hand-editing the Alertmanager configuration.  This is the sending half of the same trick `metrics.prometheusRule` plays on the alerting half: a `PrometheusRule` is how a chart ships the alerts it wants evaluated, and an `AlertmanagerConfig` (`monitoring.coreos.com/v1alpha1`) is how it ships the receiver and route that carry them somewhere. Both are Prometheus Operator CRDs, both are picked up by a selector on a custom resource this chart cannot reach, and both are therefore *offered* rather than guaranteed — the object is created and the operator decides whether to load it.  ## What it renders  One receiver holding one `webhookConfig` pointed at this release's Service, and one route feeding it. The webhook URL is derived — `http://<fullname>.<namespace>.svc:<service.port>` plus `ingest.webhookPath` — so a changed path or port cannot leave the receiver pointing at a 404. `ingest.webhookToken`, when set, is sent as `httpConfig.authorization` referencing the Secret this chart already renders, so the token is configured in exactly one place.  ## The one thing to check before enabling it  **The Prometheus Operator enforces a `namespace` matcher on every route it grafts from an `AlertmanagerConfig`.** By default (`alertmanagerConfigMatcherStrategy.type: OnNamespace` on the Alertmanager custom resource) the route below only ever matches alerts whose `namespace` label equals the namespace this object lives in. For a bot meant to receive alerts from the whole cluster that is almost never what is wanted, and it fails silently: the object is accepted, the receiver exists, and nothing arrives.  There are exactly two ways out, and both are changes to the Alertmanager custom resource, which belongs to whoever installed the monitoring stack and not to this chart:    1. `spec.alertmanagerConfigMatcherStrategy.type: None` — the operator stops injecting the      matcher for every AlertmanagerConfig it loads, this one included.   2. `spec.alertmanagerConfiguration.name: <this object>` — names one AlertmanagerConfig, in the      Alertmanager's own namespace, as the whole configuration. The operator documents that it      will not enforce a `namespace` label for routes and inhibition rules in that case. Set      `namespace` below to the Alertmanager's namespace to land the object there, and note that      this form replaces the Alertmanager's configuration rather than adding to it.  Leaving the default in place is a legitimate third option: a release deployed beside the workloads it watches, receiving that namespace's alerts only, needs nothing changed.  ## Why a webhook and not `discordConfigs`  Alertmanager has had a native Discord receiver since 0.25, and it is not this. That one posts a message. This one posts a card an operator can acknowledge, ignore, silence and open a graph from, and it needs the alert's full label set and its own database to do it. Both can be routed to at once — see `route.continue` below, which defaults to `true` for exactly that reason. |
| alertmanagerConfig.auth | object | `{"enabled":true,"secretKey":"ingest__webhook_token","secretName":""}` | How Alertmanager authenticates to the listener. |
| alertmanagerConfig.auth.enabled | bool | `true` | Send `ingest.webhookToken` as a bearer credential. Refused when no token is available: a listener with a token configured rejects an unauthenticated post with 401, and a receiver silently failing every delivery is worse than a render that stops. |
| alertmanagerConfig.auth.secretKey | string | `"ingest__webhook_token"` | Key within that Secret. The chart's own Secret names its keys after configuration paths, which is why this is `ingest__webhook_token` and not something readable. |
| alertmanagerConfig.auth.secretName | string | `""` | Secret holding the token, resolved by the operator in this object's namespace. Empty uses the Secret this chart renders — or `existingSecret`, when that is set. |
| alertmanagerConfig.enabled | bool | `false` | Create the AlertmanagerConfig. Requires the Prometheus Operator CRDs; the render fails rather than skipping the object. |
| alertmanagerConfig.extraReceivers | list | `[]` | Extra receivers, appended verbatim to the one this chart derives. |
| alertmanagerConfig.inhibitRules | list | `[]` | `spec.inhibitRules`, appended verbatim. Subject to the same enforced namespace matcher as the route. |
| alertmanagerConfig.labels | object | `{}` | Extra labels — what an Alertmanager's `alertmanagerConfigSelector` matches on. An object carrying none of the labels that selector names is created and never loaded, which is the commonest way this ends up doing nothing.  Example: `{alertmanagerConfig: platform}` |
| alertmanagerConfig.maxAlerts | int | `0` | Truncate a batch to this many alerts, 0 for no limit. `ingest.bodyLimitBytes` is the listener's own ceiling and rejects the whole request when it is crossed; this is the knob on the sending side that keeps a storm from reaching it. |
| alertmanagerConfig.muteTimeIntervals | list | `[]` | `spec.muteTimeIntervals`, appended verbatim. |
| alertmanagerConfig.namespace | string | `""` | Namespace for the object. Empty uses the release namespace.  A namespace other than the release's is the `spec.alertmanagerConfiguration` case above. The Prometheus Operator resolves the token's Secret reference in *this* namespace, so an object landing anywhere but beside the Secret this chart renders must be told where the token is: set `auth.secretName` to a Secret in that namespace, or turn `auth.enabled` off. |
| alertmanagerConfig.receiverName | string | `"discord-alertmanager"` | Name of the receiver, as `route.receiver` and any custom sub-route must spell it. The operator namespaces it internally, so this only has to be unique within this object. |
| alertmanagerConfig.route | object | `{"continue":true,"enabled":true,"groupBy":["alertname","namespace"],"groupInterval":"5m","groupWait":"30s","matchers":[],"repeatInterval":"4h","routes":[]}` | The route that feeds the receiver. |
| alertmanagerConfig.route.continue | bool | `true` | Keep evaluating sibling routes after this one matches.  `true` is what makes this bot an additional surface rather than a replacement: the alert gets a Discord card *and* still reaches the pager. `false` makes Discord the only receiver for everything this route matches, which is a decision to make deliberately. |
| alertmanagerConfig.route.enabled | bool | `true` | Render `spec.route`. Off, the receiver is declared and nothing is routed to it, which is what a deployment wants when its routing tree is written somewhere else and only needs the receiver to exist. |
| alertmanagerConfig.route.groupBy | list | `["alertname","namespace"]` | Alertmanager's `group_by`. This decides what one webhook batch contains, and so what a bot route with `groupStrategy: group` renders onto one card. |
| alertmanagerConfig.route.groupInterval | string | `"5m"` | Wait before sending an update for a group that has changed. |
| alertmanagerConfig.route.groupWait | string | `"30s"` | Wait before sending the first notification for a new group. Empty leaves Alertmanager's default. |
| alertmanagerConfig.route.matchers | list | `[]` | Matchers deciding which alerts take this route, each `{name, value, matchType}` with `matchType` one of `=`, `!=`, `=~`, `!~`. Empty matches everything the enforced namespace matcher lets through.  Example: `[{name: severity, value: info, matchType: "!="}]` |
| alertmanagerConfig.route.repeatInterval | string | `"4h"` | Wait before re-sending a notification nothing has changed about.  A card is edited in place for its whole lifetime, so a repeat is not what keeps it current — the reconciler is. Long is therefore the right default here, and short is how a channel gets muted. |
| alertmanagerConfig.route.routes | list | `[]` | Child routes, appended verbatim. |
| alertmanagerConfig.sendResolved | bool | `true` | Send the resolved notification as well as the firing one. Off, a card never leaves the firing state until the reconciler's next poll notices, so leaving this on is what makes the push path complete. |
| alertmanagerConfig.timeout | string | `""` | How long Alertmanager waits for a response, e.g. `10s`. Empty leaves Alertmanager's default. `ingest.requestTimeoutSecs` is the same bound seen from the receiving end; a value here below that one turns a slow request into a retry rather than a wait. |
| alertmanagerConfig.url | string | `""` | URL Alertmanager posts to. Empty derives the in-cluster Service address from `service.port` and `ingest.webhookPath`, which is what keeps the two from drifting apart. Set it only when Alertmanager reaches this release by some other route — through an Ingress, or from outside the cluster entirely. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| autoscaling | object | `{"enabled":false,"maxReplicas":5,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":80}` | Horizontal Pod Autoscaler over the Deployment. While it is enabled the Deployment renders no `replicas`, so `replicaCount` is ignored and a `helm upgrade` leaves the current scale alone. |
| autoscaling.enabled | bool | `false` | Enable Horizontal Pod Autoscaler (HPA) |
| autoscaling.maxReplicas | int | `5` | Maximum replicas |
| autoscaling.minReplicas | int | `1` | Minimum replicas |
| autoscaling.targetCPUUtilizationPercentage | int | `80` | Target CPU utilization (%) |
| autoscaling.targetMemoryUtilizationPercentage | int | `80` | Target memory utilization (%) |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | — never into the environment, which the loader refuses to combine with a file. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered configuration. The escape hatch for anything the chart's TOML renderer cannot express, notably arrays of tables. |
| configMount | object | `{"configDir":"/etc/discord-alertmanager/config","secretsDir":"/etc/discord-alertmanager/secrets"}` | Where the rendered configuration and the credential files are mounted. Neither is ever mounted with `subPath`: a subPath mount is resolved once at container start and never receives kubelet updates, which would turn every configuration change back into "restart the pod to pick it up". |
| configMount.configDir | string | `"/etc/discord-alertmanager/config"` | Directory the rendered `config.toml` is mounted at, passed as `DAM_CONFIG`. |
| configMount.secretsDir | string | `"/etc/discord-alertmanager/secrets"` | Directory the credential files are mounted at, passed as `DAM_SECRETS_DIR`. |
| discord | object | `{"capabilities":{"admin":[],"operate":[],"silence":[],"view":["@everyone"]},"captureReplyText":false,"devGuildId":null,"token":""}` | Gateway credentials, the scope slash commands are registered into, and the map from Discord role to what that role may do. |
| discord.capabilities | object | `{"admin":[],"operate":[],"silence":[],"view":["@everyone"]}` | Which roles may do what, each level a list of role ids or `@everyone`.  The levels are separate grants rather than a ladder: a role listed under `silence` is not thereby granted `view`. `silence` is the one worth reading twice — it writes to Alertmanager and stops every receiver, the pager included, where `operate` only adds a bot-local ignore that suppresses Discord. |
| discord.capabilities.admin | list | `[]` | Manage routes and read the effective configuration (`discord.capabilities.admin`). |
| discord.capabilities.operate | list | `[]` | Acknowledge, assign, and add or remove bot-local ignores (`discord.capabilities.operate`). |
| discord.capabilities.silence | list | `[]` | Create, extend and expire Alertmanager silences, which affects every receiver (`discord.capabilities.silence`). |
| discord.capabilities.view | list | `["@everyone"]` | Read alerts, silences and routes. Grants no mutation of any kind (`discord.capabilities.view`). |
| discord.captureReplyText | bool | `false` | Capture the text of thread replies, which needs the privileged `MESSAGE_CONTENT` intent (`discord.capture_reply_text`). |
| discord.devGuildId | string | `nil` | Guild to register slash commands into. Registration is global when unset (`discord.dev_guild_id`). |
| discord.token | string | `""` | Bot token. Supply it through `DAM_DISCORD__TOKEN_FILE` or the secrets directory (`discord.token`). Delivered as the secrets-directory file `discord__token`. |
| engine | object | `{"deadmanWindowSecs":1800,"dispatchers":4,"escalationIntervalSecs":15,"outboxBatchSize":16,"outboxLeaseSecs":30,"persistEvents":true,"pruneIntervalSecs":3600,"reconcileIntervalSecs":60,"regroupWindowSecs":1800,"retention":{"auditDays":365,"eventsDays":30,"resolvedDays":30},"silenceSyncIntervalSecs":30,"storm":{"forumThreshold":20,"threshold":50,"windowSecs":60}}` | Pipeline cadences, retention horizons and storm thresholds. |
| engine.deadmanWindowSecs | int | `1800` | Seconds of webhook silence that, combined with an unreachable Alertmanager, trips the deadman (`engine.deadman_window_secs`). |
| engine.dispatchers | int | `4` | Outbox dispatcher workers (`engine.dispatchers`). |
| engine.escalationIntervalSecs | int | `15` | Seconds between escalation timer sweeps (`engine.escalation_interval_secs`). |
| engine.outboxBatchSize | int | `16` | Outbox rows one worker claims per pass (`engine.outbox_batch_size`). |
| engine.outboxLeaseSecs | int | `30` | Seconds a claimed outbox row stays claimed before a janitor may reclaim it (`engine.outbox_lease_secs`). |
| engine.persistEvents | bool | `true` | Record a row in `alert_events` for every state transition (`engine.persist_events`). |
| engine.pruneIntervalSecs | int | `3600` | Seconds between retention sweeps (`engine.prune_interval_secs`). |
| engine.reconcileIntervalSecs | int | `60` | Seconds between reconciler polls of the Alertmanager alert set (`engine.reconcile_interval_secs`). |
| engine.regroupWindowSecs | int | `1800` | Seconds within which a re-fire reuses the existing card and thread (`engine.regroup_window_secs`). |
| engine.retention | object | `{"auditDays":365,"eventsDays":30,"resolvedDays":30}` | How long each kind of history survives the retention sweep. `events_days` is the expensive one: a row per state transition per alert. |
| engine.retention.auditDays | int | `365` | Days of `audit_log` (`engine.retention.audit_days`). |
| engine.retention.eventsDays | int | `30` | Days of `alert_events` history. This is the expensive table (`engine.retention.events_days`). |
| engine.retention.resolvedDays | int | `30` | Days a resolved alert and its notification are kept (`engine.retention.resolved_days`). |
| engine.silenceSyncIntervalSecs | int | `30` | Seconds between silence syncs (`engine.silence_sync_interval_secs`). |
| engine.storm | object | `{"forumThreshold":20,"threshold":50,"windowSecs":60}` | When a route stops posting one card per alert and rolls one card per window instead.  Discord's per-channel limits are strict enough that an unthrottled storm produces rate-limit responses rather than notifications, so a worse card in a readable channel beats a better one nobody receives. |
| engine.storm.forumThreshold | int | `20` | Threshold for forum routes, which is lower (`engine.storm.forum_threshold`). |
| engine.storm.threshold | int | `50` | Alerts on one route inside the window that trigger digest mode (`engine.storm.threshold`). |
| engine.storm.windowSecs | int | `60` | Length of the window, in seconds (`engine.storm.window_secs`). |
| existingSecret | string | `""` | chart renders no Secret of its own and the credential values above are ignored. |
| extraEnv | list | `[]` | Additional environment variables for the application container. |
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
| image | object | `{"pullPolicy":"","registry":"","repository":"timschoenle/discord-alertmanager","tag":"v0.2.0@sha256:48c216821cdea24441c87d20ee156eea8ecad4e0cbc5e9afd3fe7b371e3c031d"}` | Container image the pod runs, composed as `registry/repository:tag`. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| image.repository | string | `"timschoenle/discord-alertmanager"` | The container image repository. |
| image.tag | string | `"v0.2.0@sha256:48c216821cdea24441c87d20ee156eea8ecad4e0cbc5e9afd3fe7b371e3c031d"` | The container image tag. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries |
| ingest | object | `{"bind":"0.0.0.0:9099","bodyLimitBytes":1048576,"maxConcurrentRequests":64,"requestTimeoutSecs":10,"shutdownDrainSecs":10,"webhookPath":"/webhook","webhookToken":""}` | The listener Alertmanager posts to. `/healthz`, `/readyz` and `/metrics` are served on the same address, so `bind` decides the container port for all four and the chart takes it from here rather than from a value of its own. |
| ingest.bind | string | `"0.0.0.0:9099"` | Address and port to listen on (`ingest.bind`). |
| ingest.bodyLimitBytes | int | `1048576` | Largest accepted request body, in bytes (`ingest.body_limit_bytes`). |
| ingest.maxConcurrentRequests | int | `64` | Requests handled at once. Further requests queue rather than being rejected (`ingest.max_concurrent_requests`). |
| ingest.requestTimeoutSecs | int | `10` | Seconds a request may take before the listener abandons it (`ingest.request_timeout_secs`). |
| ingest.shutdownDrainSecs | int | `10` | Seconds to let in-flight requests finish during shutdown (`ingest.shutdown_drain_secs`). |
| ingest.webhookPath | string | `"/webhook"` | Path Alertmanager posts the version-4 envelope to (`ingest.webhook_path`). |
| ingest.webhookToken | string | `""` | Bearer token every webhook request has to carry (`ingest.webhook_token`). Delivered as the secrets-directory file `ingest__webhook_token`. |
| ingress | object | `{"annotations":{},"enabled":false,"hosts":[],"ingressClassName":"nginx","tls":[]}` | The Ingress in front of the Service. An independent switch from `gateway`, so a cluster migrating from an Ingress controller to a Gateway implementation can run both. |
| ingress.annotations | object | `{}` | Additional ingress annotations Example:   cert-manager.io/cluster-issuer: letsencrypt-prod   nginx.ingress.kubernetes.io/rate-limit: "100" |
| ingress.enabled | bool | `false` | Enable ingress resource |
| ingress.hosts | list | `[]` | Host definitions for ingress Example:   - host: example.local     paths:       - path: /         pathType: Prefix |
| ingress.ingressClassName | string | `"nginx"` | Ingress class name (e.g. nginx) |
| ingress.tls | list | `[]` | TLS configuration for ingress Example:   - secretName: example-tls     hosts:       - example.local |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| links | object | `{"allowedHosts":[],"buttons":[],"grafanaBase":null,"prometheusBase":null,"windowLeadSecs":900,"windowTrailSecs":300}` | Templates for the link buttons on a card, and the host allowlist they are checked against. |
| links.allowedHosts | list | `[]` | Hosts a rendered button may point at (`links.allowed_hosts`). |
| links.buttons | list | `[]` | The buttons themselves, rendered in order (`links.buttons`). |
| links.grafanaBase | string | `nil` | Grafana base URL, available to templates as `links.grafana_base` (`links.grafana_base`). |
| links.prometheusBase | string | `nil` | Prometheus base URL, available to templates as `links.prometheus_base` (`links.prometheus_base`). |
| links.windowLeadSecs | int | `900` | Seconds of graph shown before the alert started (`links.window_lead_secs`). |
| links.windowTrailSecs | int | `300` | Seconds of graph shown after the alert ended, or after now while it is firing (`links.window_trail_secs`). |
| livenessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/healthz","port":"http"},"initialDelaySeconds":10,"periodSeconds":10,"timeoutSeconds":5}` | Liveness probe, whose failure restarts the container. `successThreshold` is dropped from the rendered probe, because the API server accepts nothing but 1 there. |
| livenessProbe.enabled | bool | `true` | Enable liveness probe |
| livenessProbe.failureThreshold | int | `3` | Failure threshold |
| livenessProbe.httpGet | object | `{"path":"/healthz","port":"http"}` | The probe handler, in the same four forms `startupProbe.httpGet` accepts. |
| livenessProbe.httpGet.path | string | `"/healthz"` | Path the probe requests. `/healthz` deliberately, not `/readyz`: a liveness probe that fails on an unreachable Alertmanager restarts a process that was working fine. |
| livenessProbe.httpGet.port | string | `"http"` | Health check port |
| livenessProbe.initialDelaySeconds | int | `10` | Initial delay before probe starts |
| livenessProbe.periodSeconds | int | `10` | Probe frequency |
| livenessProbe.timeoutSeconds | int | `5` | Probe timeout |
| metrics | object | `{"prometheusRule":{"additionalRuleGroups":[],"additionalRuleLabels":{},"disabledAlerts":[],"disabledGroups":[],"enabled":false,"forOverrides":{},"labels":{},"namespace":"","scope":"namespace","severityOverrides":{},"thresholds":{}},"serviceMonitor":{"enabled":false,"interval":"30s","labels":{},"metricRelabelings":[],"relabelings":[],"scrapeTimeout":"10s"}}` | The Prometheus Operator objects: the ServiceMonitor that scrapes this pod, and the PrometheusRule that alerts on it.  Neither switch turns the exposition on. That is `observability.metricsEnabled`, which is the image's own key and is what decides whether `/metrics` answers at all — a ServiceMonitor pointed at a listener serving 404 scrapes nothing and reports no error, so the render is refused rather than allowed to produce one.  The endpoint is the *ingest* listener, not a second port: `/metrics` is served beside `/webhook`, `/healthz` and `/readyz` on `ingest.bind`. There is nothing to expose separately and no second container port to open. |
| metrics.prometheusRule | object | `{"additionalRuleGroups":[],"additionalRuleLabels":{},"disabledAlerts":[],"disabledGroups":[],"enabled":false,"forOverrides":{},"labels":{},"namespace":"","scope":"namespace","severityOverrides":{},"thresholds":{}}` | The alerting rules under `rules/`, installed as a PrometheusRule.  Worth stating plainly because this chart is the one where it reads oddly: these are alerts *about the bot*, evaluated by Prometheus and delivered by Alertmanager through whatever receivers it already has. They are deliberately not routed through the bot itself. A bot that has stopped delivering cannot deliver the card that says so, which is the entire failure `DiscordAlertmanagerDown` exists to report. |
| metrics.prometheusRule.additionalRuleGroups | list | `[]` | Extra rule groups, appended verbatim. |
| metrics.prometheusRule.additionalRuleLabels | object | `{}` | Labels merged into every alert this chart ships, for Alertmanager routing. This is how a staging release keeps itself out of the production on-call rotation. |
| metrics.prometheusRule.disabledAlerts | list | `[]` | Alerts to drop, by name. Refused when the name is not one this chart ships — a typo here is the one failure the whole feature must not produce, because it leaves the values file saying an alert is off while it still pages. |
| metrics.prometheusRule.disabledGroups | list | `[]` | Rule groups to drop entirely, by name. Refused when the name is not one this chart ships. |
| metrics.prometheusRule.enabled | bool | `false` | Create a PrometheusRule from the alerting rules under `rules/`. |
| metrics.prometheusRule.forOverrides | object | `{}` | Per-alert `for` duration, replacing the shipped one. |
| metrics.prometheusRule.labels | object | `{}` | Extra labels for the PrometheusRule — what a Prometheus `ruleSelector` matches on. |
| metrics.prometheusRule.namespace | string | `""` | Namespace for the PrometheusRule. Empty uses the release namespace. A Prometheus loads rules from whichever namespaces its `ruleNamespaceSelector` names, which this chart cannot reach; landing the object in one of them is the move that is left. The rules stay scoped to this release either way — `scope` is derived from the release, not from where the object lands. |
| metrics.prometheusRule.scope | string | `"namespace"` | Confine the rules to this release's namespace. A `PrometheusRule` is not scoped to the namespace it lives in, so an unscoped `up{job="discord-alertmanager"} == 0` matches a second release of this chart anywhere Prometheus can see, and the two then alert on each other. `namespace` rewrites every rule expression to match only series from this namespace; `none` installs them as written, which is correct when a Prometheus already sets `enforcedNamespaceLabel` and would do the same rewrite itself, and wrong otherwise. |
| metrics.prometheusRule.severityOverrides | object | `{}` | Per-alert `severity` label, most specific and so beating `additionalRuleLabels`. |
| metrics.prometheusRule.thresholds | object | `{}` | Per-alert threshold values, for the alerts that declare a tunable in `rules/tunables.yaml`. An alert with no declared tunable is refused rather than silently left untuned. |
| metrics.serviceMonitor | object | `{"enabled":false,"interval":"30s","labels":{},"metricRelabelings":[],"relabelings":[],"scrapeTimeout":"10s"}` | Prometheus Operator scrape configuration. |
| metrics.serviceMonitor.enabled | bool | `false` | Create a ServiceMonitor. Requires the Prometheus Operator CRDs; the render fails rather than skipping the object, because a release that quietly drops its ServiceMonitor installs cleanly and is never scraped. |
| metrics.serviceMonitor.interval | string | `"30s"` | Scrape interval. |
| metrics.serviceMonitor.labels | object | `{}` | Extra labels, e.g. the `release` label a Prometheus Operator `serviceMonitorSelector` requires. |
| metrics.serviceMonitor.metricRelabelings | list | `[]` | `metricRelabelings` applied to every sample the scrape returns. |
| metrics.serviceMonitor.relabelings | list | `[]` | `relabelings` applied to the scrape target before the scrape. |
| metrics.serviceMonitor.scrapeTimeout | string | `"10s"` | Scrape timeout. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"cilium":{"description":"","egress":{"customRules":[],"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"enableDefaultDeny":true,"extraEgress":[],"extraIngress":[],"ingress":{"customRules":[],"fromEntities":[]}},"egress":{"cidr":"0.0.0.0/0","customRules":[],"dns":{"enabled":true,"namespaceSelector":{"kubernetes.io/metadata.name":"kube-system"},"podSelector":{"k8s-app":"kube-dns"}},"enabled":true,"except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"http":{"enabled":false},"https":{"enabled":true}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"ingress":{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}}` | Network policy configuration |
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
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}},"customRules":[],"enabled":true,"gateway":{"enabled":true,"namespace":"","ports":[],"selector":{}},"monitoring":{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}}` | Ingress configuration |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"traefik","ports":[],"selector":{"app.kubernetes.io/name":"traefik"}}` | Ingress Controller configuration |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from Ingress Controller |
| networkPolicy.ingress.controller.namespace | string | `"traefik"` | Namespace where Ingress Controller is running (default: traefik) |
| networkPolicy.ingress.controller.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.controller.selector | object | `{"app.kubernetes.io/name":"traefik"}` | Pod selector for Ingress Controller (default: Traefik label) |
| networkPolicy.ingress.customRules | list | `[]` | Custom ingress rules |
| networkPolicy.ingress.enabled | bool | `true` | Enable ingress rules |
| networkPolicy.ingress.gateway | object | `{"enabled":true,"namespace":"","ports":[],"selector":{}}` | Allow traffic from the Gateway API data plane. Only rendered when `gateway.enabled` is also set, so it costs nothing on a chart exposed through an Ingress.  Needs no configuration in the common case: the Gateway that must be admitted is by definition the one `gateway.parentRefs` names, so both fields below are derived from it. |
| networkPolicy.ingress.gateway.enabled | bool | `true` | Allow ingress from the Gateway's data plane. |
| networkPolicy.ingress.gateway.namespace | string | `""` | Namespace the data plane runs in. Empty derives it from `gateway.parentRefs`. |
| networkPolicy.ingress.gateway.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| networkPolicy.ingress.gateway.selector | object | `{}` | Pod selector matching the data plane. Empty derives `gateway.networking.k8s.io/gateway-name: <parentRef>`, the label Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision for a Gateway. |
| networkPolicy.ingress.monitoring | object | `{"enabled":true,"namespace":"monitoring","namespaceSelector":{},"ports":[]}` | Monitoring configuration for ingress |
| networkPolicy.ingress.monitoring.enabled | bool | `true` | Allow ingress from monitoring namespace |
| networkPolicy.ingress.monitoring.namespace | string | `"monitoring"` | Namespace where monitoring tools are running |
| networkPolicy.ingress.monitoring.namespaceSelector | object | `{}` | Namespace selector matching the monitoring namespace, replacing `namespace` when set. For a Prometheus labelled rather than named, or one of several namespaces that scrape. |
| networkPolicy.ingress.monitoring.ports | list | `[]` | Restrict the rule to specific ports. Empty means all ports. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| observability | object | `{"adminChannelId":null,"metricsEnabled":true}` | What the bot exposes about itself, and who hears when it goes quiet. The log format and filter are not here — both are read from the environment before the configuration exists, and supplying either through a file is an error rather than a silent no-op. |
| observability.adminChannelId | string | `nil` | Channel the deadman and route-health notices post to (`observability.admin_channel_id`). |
| observability.metricsEnabled | bool | `true` | Serve Prometheus metrics at `/metrics` on the ingest listener (`observability.metrics_enabled`). |
| persistence | object | `{"data":{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","mountPath":"/data","size":"1Gi","storageClassName":""}}` | Where the SQLite database file lives.  Only read while `storage.backend` is `sqlite`. A PostgreSQL deployment keeps its state in the database and needs no volume at all, so the claim is not rendered for one — an empty 1Gi PVC left behind by a backend switch is a bill nobody chose to pay.  The container runs with a read-only root filesystem, so there is nowhere else the file could go: the image's own default `discord-alertmanager.db` is relative to the `/app` working directory and would fail to open. `storage.sqlite.path` is therefore defaulted to a path under `mountPath` rather than to the image's default, and the render is refused if the two disagree. |
| persistence.data | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","mountPath":"/data","size":"1Gi","storageClassName":""}` | The volume the SQLite database lives on. |
| persistence.data.accessMode | string | `"ReadWriteOnce"` | The access mode for the persistent volume. `ReadWriteOnce` forces the Deployment to the `Recreate` update strategy: a rolling update would wedge, because the replacement pod cannot attach a volume the outgoing pod still holds. |
| persistence.data.annotations | object | `{}` | Annotations for the PersistentVolumeClaim, e.g. `helm.sh/resource-policy: keep`. |
| persistence.data.enabled | bool | `true` | Create a PersistentVolumeClaim. Disabled, an `emptyDir` is used instead — which loses every alert card, thread link, ignore and acknowledgement the bot has recorded on each restart, and re-posts alerts that are still firing. Acceptable for a `helm test` run and for nothing else. |
| persistence.data.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.data.mountPath | string | `"/data"` | Directory the volume is mounted at. `storage.sqlite.path` must name a file inside it. |
| persistence.data.size | string | `"1Gi"` | The storage size requested for the volume. The database holds one row per alert, per notification and per state transition, bounded by `engine.retention`; a gigabyte is far more than a single Alertmanager produces inside the default 30-day horizon. |
| persistence.data.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. `soft` prefers, `hard` requires. Ignored when `affinity` is set. |
| podDisruptionBudget | object | `{"enabled":false,"maxUnavailable":1,"minAvailable":1}` | PodDisruptionBudget for the pods. `minAvailable` and `maxUnavailable` both default to 1 and the API server refuses a budget carrying both, so set one of them to `null`. |
| podDisruptionBudget.enabled | bool | `false` | Enable PodDisruptionBudget |
| podDisruptionBudget.maxUnavailable | int | `1` | Maximum unavailable pods |
| podDisruptionBudget.minAvailable | int | `1` | Minimum available pods |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1000,"runAsGroup":1000,"runAsUser":1000}` | Pod security context, merged over the preset. |
| podSecurityContext.fsGroup | int | `1000` | Group ID for file system access |
| podSecurityContext.runAsGroup | int | `1000` | Primary group ID to run as |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name. |
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/readyz","port":"http"},"initialDelaySeconds":5,"periodSeconds":5,"timeoutSeconds":3}` | Readiness probe. While it fails the pod leaves the Service endpoints and keeps running. |
| readinessProbe.enabled | bool | `true` | Enable readiness probe |
| readinessProbe.failureThreshold | int | `3` | Failure threshold |
| readinessProbe.httpGet | object | `{"path":"/readyz","port":"http"}` | The probe handler, in the same four forms `startupProbe.httpGet` accepts. |
| readinessProbe.httpGet.path | string | `"/readyz"` | Path the probe requests. `/readyz` answers 503 while the database is unreachable or the Alertmanager poll has gone stale, which takes the pod out of the Service and makes Alertmanager retry the delivery rather than lose it. |
| readinessProbe.httpGet.port | string | `"http"` | Health check port |
| readinessProbe.initialDelaySeconds | int | `5` | Initial delay before probe starts |
| readinessProbe.periodSeconds | int | `5` | Probe frequency |
| readinessProbe.timeoutSeconds | int | `3` | Probe timeout |
| render | object | `{"debounceSecs":3,"descriptionBudget":1500,"keyLabels":["namespace","instance","job"],"showFingerprint":true,"threadArchiveAfterMinutes":1440}` | How an alert card is laid out and how often it may be edited. |
| render.debounceSecs | int | `3` | Seconds to coalesce edits to one card before sending them (`render.debounce_secs`). |
| render.descriptionBudget | int | `1500` | Characters of annotation text a card may carry before it is truncated (`render.description_budget`). |
| render.keyLabels | list | `["namespace","instance","job"]` | Labels promoted to their own inline field on the card, in order (`render.key_labels`). |
| render.showFingerprint | bool | `true` | Show a short fingerprint in the card footer (`render.show_fingerprint`). |
| render.threadArchiveAfterMinutes | int | `1440` | Minutes of inactivity after which an alert thread archives (`render.thread_archive_after_minutes`). |
| replicaCount | int | `1` | Number of application replicas. |
| resources | object | `{"limits":{"cpu":"500m","memory":"256Mi"},"requests":{"cpu":"25m","memory":"96Mi"}}` | What the container is given.  Sized for a bot holding one Discord gateway websocket, a connection pool and a handful of background loops, which is a few tens of megabytes at rest. The headroom above that is for the two things that are bursty rather than steady: a storm, where the renderer holds a batch of alerts and their labels at once, and a reconciler pass over a large alert set. A release watching an Alertmanager with thousands of active alerts wants more; `dam_outbox_depth` and the container's working set are what say how much. |
| resources.limits | object | `{"cpu":"500m","memory":"256Mi"}` | Ceiling for the container. Past the memory limit the kubelet OOM-kills it; past the CPU limit it is throttled instead. |
| resources.limits.cpu | string | `"500m"` | Maximum CPU usage (e.g. 100m = 0.1 core) |
| resources.limits.memory | string | `"256Mi"` | Maximum memory usage (e.g. 64Mi) |
| resources.requests | object | `{"cpu":"25m","memory":"96Mi"}` | What the scheduler reserves. Without a CPU request the pod is BestEffort and is the first thing evicted under node pressure. |
| resources.requests.cpu | string | `"25m"` | Guaranteed CPU request |
| resources.requests.memory | string | `"96Mi"` | Guaranteed memory request |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| routes | list | `[]` | Routes declared in the file, which cannot be edited or deleted from Discord (`routes`). |
| securityContext | object | `{}` | Container security context, merged over the preset. A writable /tmp is provided automatically via an emptyDir volume. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| service | object | `{"annotations":{},"port":80,"type":"ClusterIP"}` | The Service in front of the ingest listener. This is the address Alertmanager posts to, and on an in-cluster Alertmanager it is the only exposure the chart needs — `ingress` and `gateway` below exist for the case where Alertmanager is somewhere else. |
| service.annotations | object | `{}` | Additional service annotations |
| service.port | int | `80` | Service port. The container port is not configured here: it is the port half of `ingest.bind`, so the listener and the Service can never be told two different numbers. |
| service.type | string | `"ClusterIP"` | Kubernetes service type |
| serviceAccount | object | `{"annotations":{},"automountToken":false,"create":true,"name":""}` | ServiceAccount the pods run under. `create: false` with no `name` means the `default` one. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty) |
| startupProbe | object | `{"enabled":true,"failureThreshold":30,"httpGet":{"path":"/healthz","port":"http"},"initialDelaySeconds":2,"periodSeconds":5,"successThreshold":1,"timeoutSeconds":3}` | Startup probe, which holds the other two off until it passes. `failureThreshold` times `periodSeconds` is the budget before the kubelet restarts the container: 150 seconds here. |
| startupProbe.enabled | bool | `true` | Enable startup probe |
| startupProbe.failureThreshold | int | `30` | Failure threshold |
| startupProbe.httpGet | object | `{"path":"/healthz","port":"http"}` | The probe handler. `tcpSocket`, `exec` and `grpc` are accepted in its place; an enabled probe with no handler at all fails the render. |
| startupProbe.httpGet.path | string | `"/healthz"` | Path the probe requests. `/healthz` asks nothing of the database or of Alertmanager, by design: wiring a startup probe to a dependency is how an outage becomes a restart loop that cannot fix it. |
| startupProbe.httpGet.port | string | `"http"` | Health check port |
| startupProbe.initialDelaySeconds | int | `2` | Initial delay before probe starts |
| startupProbe.periodSeconds | int | `5` | Probe frequency |
| startupProbe.successThreshold | int | `1` | Success threshold |
| startupProbe.timeoutSeconds | int | `3` | Probe timeout |
| storage | object | `{"backend":"sqlite","postgres":{"acquireTimeoutSecs":5,"maxConnections":16,"migrateOnStart":true,"url":""},"sqlite":{"acquireTimeoutSecs":5,"maxConnections":4,"migrateOnStart":true,"path":"/data/discord-alertmanager.db"}}` | Which database backend the bot connects to, and how to reach it. Keys belonging to the backend that is not selected are ignored rather than refused. |
| storage.backend | string | `"sqlite"` | Which backend the bot connects to (`storage.backend`). |
| storage.postgres | object | `{"acquireTimeoutSecs":5,"maxConnections":16,"migrateOnStart":true,"url":""}` | The PostgreSQL backend, and the only shape this chart will run above one replica in: the outbox lease that makes several dispatchers safe is `FOR UPDATE SKIP LOCKED` here and `BEGIN IMMEDIATE` on SQLite, and only the first is safe across processes. |
| storage.postgres.acquireTimeoutSecs | int | `5` | Seconds to wait for a connection from the pool before failing the operation (`storage.postgres.acquire_timeout_secs`). |
| storage.postgres.maxConnections | int | `16` | Maximum pooled connections (`storage.postgres.max_connections`). |
| storage.postgres.migrateOnStart | bool | `true` | Run pending migrations during startup (`storage.postgres.migrate_on_start`). |
| storage.postgres.url | string | `""` | Connection URL. Supply it through `DAM_STORAGE__POSTGRES__URL_FILE` or the secrets directory, since it carries the password (`storage.postgres.url`). Delivered as the secrets-directory file `storage__postgres__url`. |
| storage.sqlite | object | `{"acquireTimeoutSecs":5,"maxConnections":4,"migrateOnStart":true,"path":"/data/discord-alertmanager.db"}` | The SQLite backend, which is the default. One file on one volume, one writer, and one replica. |
| storage.sqlite.acquireTimeoutSecs | int | `5` | Seconds to wait for a connection from the pool before failing the operation (`storage.sqlite.acquire_timeout_secs`). |
| storage.sqlite.maxConnections | int | `4` | Size of the read pool. The writer is always one connection (`storage.sqlite.max_connections`). |
| storage.sqlite.migrateOnStart | bool | `true` | Run pending migrations during startup (`storage.sqlite.migrate_on_start`). |
| storage.sqlite.path | string | `"/data/discord-alertmanager.db"` | Path to the database file, created on first start if it does not exist (`storage.sqlite.path`).  Not the image's own default, which is the relative `discord-alertmanager.db` and resolves inside the read-only `/app` working directory — the one place in this container the file cannot be created. It has to be an absolute path under `persistence.data.mountPath`, and the render is refused when it is not, because the alternative is a pod that starts, fails to open its database and crash-loops with an errno. |
| strategy | object | `{}` | Deployment update strategy. Empty uses the Kubernetes default rolling update. |
| terminationGracePeriodSeconds | int | `30` | Grace period for pod shutdown. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |

## Source Code

* <https://github.com/TimSchoenle/discord-alertmanager>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
