# tankovault

![Version: 2.0.3](https://img.shields.io/badge/Version-2.0.3-informational?style=flat-square) ![AppVersion: 1.2.0](https://img.shields.io/badge/AppVersion-1.2.0-informational?style=flat-square)

This chart deploys the full TankoVault manga aggregator stack — frontend, api, control-plane, worker, notifier, sync, challenge-solver and render — hardened to the restricted Pod Security Standard, with file-backed configuration that reloads in place instead of restarting pods, optional bundled PostgreSQL, Valkey, NATS JetStream and TRAWL, and optional Prometheus metrics, alerting rules and Grafana dashboards.

TankoVault is a multi-service manga/manhwa aggregator and tracker. It indexes series metadata
across provider sites and layers watchlists, read progress, notifications and AniList
synchronisation on top — it stores links and metadata only, and never downloads, caches or
serves chapter images.

This chart deploys the whole system: the `frontend` SPA server, the `api` edge, the
`control-plane` scheduler, the `worker` fetch tier, `notifier`, `sync`, `challenge-solver` and
the optional headless `render` tier, plus the one-shot `bootstrap` migration and seeding steps.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A PostgreSQL database — either the bundled one (`postgresql.enabled=true`) or your own
  (`externalDatabase.*`)
- A NATS server with JetStream enabled, if `worker` or `control-plane` are deployed
- The Prometheus Operator CRDs, if `metrics.serviceMonitor` or `metrics.prometheusRule` are enabled
- An ingress controller, if `ingress.enabled=true`

## Get Repository Info

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
```

## Install

Nothing stateful is created unless you ask for it, and the only credentials you must supply are
the ones issued by somebody else. The smallest working install is therefore:

```shell
helm install tankovault timschoenle/tankovault \
  --set postgresql.enabled=true \
  --set nats.enabled=true \
  --set valkey.enabled=true \
  --set trawl.enabled=true \
  --set bootstrap.seedAdmin.enabled=true
```

`services.sync` additionally needs an [AniList OAuth application](https://anilist.co/settings/developer);
set `services.sync.enabled=false` if you do not want it.

### Generated credentials

Every credential whose value is meaningful only inside the release is generated when you leave it
empty, and remembered afterwards: the chart reads the previous value back out of its own `Secret`,
so `helm upgrade` never rotates one out from under a running workload.

| Value | Generated when empty | Read it back with |
|---|---|---|
| `auth.jwtSecret` | always | `auth__jwt_secret` |
| `auth.passwordPepper` | first install only (see below) | `auth__password_pepper` |
| `internal.token` | `profile=production` | `internal__token` |
| `anilist.tokenEncryptionKey` | `services.sync.enabled` | `anilist__token_encryption_key` |
| `bootstrap.seedAdmin.password` | `bootstrap.seedAdmin.enabled` | `seed_admin_password` |
| `postgresql.auth.password` | `postgresql.enabled` | `postgresql__password` |

```shell
kubectl get secret tankovault -o jsonpath='{.data.seed_admin_password}' | base64 -d
```

Set a value explicitly only when it has to be known outside the release. Values issued by a third
party — the AniList application credentials, SMTP and webhook endpoints — are never generated,
because they cannot be invented. Generation also requires the chart to own the `Secret`: with
`existingSecret` set it fills in nothing.

`auth.passwordPepper` is the one exception to "generated when empty": it is generated only when
this release has no `Secret` yet. Introducing a pepper into a release that has been storing
unpeppered password hashes would make every one of them unverifiable, so an upgrade never adds
one — a release running without a pepper keeps running without it until you set the value
yourself, at which point every existing password is invalidated deliberately rather than by
surprise.

**Back the `Secret` up.** Losing `auth__password_pepper` invalidates every stored password;
losing `anilist__token_encryption_key` forces every account to re-link. That is true however
those values were set, but generated values exist nowhere else.

## Configuration reloads instead of restarting

This is the one place where the chart deliberately departs from the convention used by the other
charts in this repository, and it is worth understanding before you change anything.

Every TankoVault service watches the directories its configuration came from. When the kubelet
refreshes a mounted `ConfigMap` or `Secret`, the service re-reads the whole configuration and
rebuilds its runtime in place. So this chart:

- delivers **all** configuration as files, never as environment variables. Only the four
  process-level keys that are read before the layered configuration exists
  (`TANKOVAULT_PROFILE`, `TANKOVAULT_CONFIG`, `TANKOVAULT_SECRETS_DIR`, `RUST_LOG`) are passed
  through the environment;
- mounts them as whole directories, never with `subPath`, because a `subPath` mount is resolved
  once at container start and never updated;
- emits **no `checksum/config` pod annotations**, so `helm upgrade` with a changed `config` value
  does not roll the Deployments.

A configuration change therefore takes effect within a kubelet sync period (roughly a minute),
with the pods' restart count unchanged. Verify it that way, too:

```shell
kubectl get pods -l app.kubernetes.io/part-of=tankovault \
  -o custom-columns=NAME:.metadata.name,RESTARTS:.status.containerStatuses[0].restartCount
```

Three exceptions are worth knowing:

| Change | Effect |
|---|---|
| `telemetry.*`, `metrics.*` | Installed process-globally; needs a real restart |
| `auth.jwtSecret` | Reloads, and signs every user out |
| `auth.passwordPepper` | Reloads, and invalidates **every stored password** |
| `anilist.tokenEncryptionKey` | Reloads, but does not re-seal tokens already stored |

Set `configReload.rolloutOnChange=true` if you would rather configuration changes behave like an
ordinary image bump.

## Configuration and secrets

`config` is the TankoVault TOML tree exactly as `docs/CONFIGURATION.md` documents it, so a key
written `TANKOVAULT_RATE_LIMIT__AUTH__PER_MINUTE` upstream is written here as:

```yaml
config:
  rate_limit:
    auth:
      per_minute: 10
```

`services.<name>.config` overrides it for one service. The chart fills in the topology itself —
each service's `bind_addr`, `telemetry.service_name`, the peer URLs, and the datastore
endpoints — and derives `anilist.redirect_uri`, `email.base_url` and `auth.webauthn_origin` from
the ingress, because all three are bound to the browser-visible origin and produce runtime-only
failures when they disagree with it.

Credentials go in a `Secret` and are mounted as one file per key, named for the configuration
path with `__` for nesting (`auth__jwt_secret`, `anilist__token_encryption_key`). **Each pod
receives only the keys its own service reads**, so the `worker` — the tier that parses untrusted
provider HTML — never has the token-signing key on its filesystem.

To keep credentials out of the release entirely, pre-create the Secret and set `existingSecret`.
The chart then generates nothing, so this Secret must carry every key the enabled services need:

```shell
kubectl create secret generic tankovault-credentials \
  --from-literal=auth__jwt_secret="$(openssl rand -hex 32)" \
  --from-literal=internal__token="$(openssl rand -hex 32)" \
  --from-literal=anilist__token_encryption_key="$(openssl rand -base64 32)"
```

## Migrations

Schema migration is a discrete step, never something a service does at startup.
`bootstrap.migrate.mode` defaults to `auto`, which picks:

- **`job`** when the database is external — a `pre-install,pre-upgrade` hook, so the new schema
  is in place before any new code sees it;
- **`initContainer`** when `postgresql.enabled=true` — a pre-install hook would run before the
  bundled StatefulSet exists and could only ever time out. Running it in every pod is safe and
  is sanctioned upstream; sqlx takes a Postgres advisory lock for the duration.

The migration Job runs before Helm creates any of the release's own objects, so the three things
it needs — the ServiceAccount, the credential Secret and its ConfigMap — are Helm hooks
themselves, created at weight `-10` ahead of the Job at `-5`. They are deliberately not deleted
when the hook finishes, because the release's workloads use them for as long as they run. The
consequence to know about: being hooks, these three do not appear in `helm get manifest`, are not
reverted by `helm rollback`, and **survive `helm uninstall`** — delete the leftover
`<release>` Secret, `<release>` ServiceAccount and `<release>-bootstrap-config` ConfigMap by hand
if you want the namespace empty.

### Argo CD and `bootstrap.migrate.ordering`

Argo CD compiles a `pre-install,pre-upgrade` hook into its PreSync phase, and PreSync runs to
completion before **any** Sync-phase resource is applied. Sync waves only order within a phase, so
no weight can place a PreSync hook after something in Sync.

That is fatal in one specific setup: `database__url` supplied from an ExternalSecret. On a first
install the migration Job mounts a Secret that does not exist yet, the pod sits in
`ContainerCreating` on `FailedMount`, the Job never completes, PreSync never finishes, Sync never
starts — and the ExternalSecret that would have created the Secret is therefore never applied. The
deadlock does not resolve on its own, and nothing inside PreSync can break it: marking the source
optional or adding a wait-for-secret initContainer only changes which step hangs.

Set `bootstrap.migrate.ordering=argoSyncWave` for that case. The Job drops its hook annotations
entirely and becomes an ordinary tracked resource at `bootstrap.migrate.argoSyncWaveBase` (default
`0`), with the workloads one wave above it. Argo holds a wave until the previous one is healthy and
a Job is healthy only once it is Complete, so the ordering `pre-upgrade` used to guarantee is
recovered inside the Sync phase, where the ExternalSecret reconciles alongside it.

Two things follow from the Job no longer being a hook:

- Its name carries a short digest of the pod spec (`<release>-tankovault-migrate-1a2b3c4d`),
  because `spec.template` is immutable and re-applying a Job under a stable name would either be
  rejected or silently do nothing. A changed image or spec is a new Job that runs; an unchanged one
  resolves to the Job already sitting there Complete. Superseded names are left for Argo to prune.
- Anything the migration depends on — the ExternalSecret itself, a database provisioned by an
  operator — must be given a sync wave **strictly below** `argoSyncWaveBase`.

The default is unchanged, so `helm install` consumers and anyone happy with the hook are unaffected.
Only `bootstrap.migrate.mode: job` is affected; the seed steps stay `post-install` because PostSync
runs after Sync and their secrets already exist.

## Exposure

Only the frontend is published. It serves the SPA and reverse-proxies `/v1/*` to the API, so one
origin covers the whole application and the SPA's API calls, its SSE stream, its session cookies
and its passkey origin all resolve without a cross-origin hop.

`control-plane`, `sync`, `render` and `challenge-solver` expose privileged contracts and upstream
publishes none of them even on a single host. Giving any of the four a Service type other than
`ClusterIP` fails rendering unless you also set `allowUnsafeExposure=true`.

`ingress.api.enabled` adds a second Ingress for split-origin clients. If you use it, set
`config.security.cors.allowed_origins` and `config.auth.webauthn_origin` to match, or logins will
fail in ways that only show up in a browser.

## The bundled datastores are evaluation-tier

`postgresql`, `valkey`, `nats` and `trawl` exist so that `helm install` produces a working
stack on a bare cluster. Each is a single instance with no replication and no failover, and the
bundled PostgreSQL has no point-in-time recovery. Before this carries anything you care about,
move to `externalDatabase`, `externalRedis` and `externalNats`.

`trawl` is the exception to "evaluation-tier": it is the solver back-end upstream ships and there
is no managed alternative to graduate to. Size it with `trawl.resources` rather than a t-shirt
preset — each entry in `trawl.browserPoolSize` is a full Firefox, and the largest preset this
repository offers caps memory below what one of them needs. It keeps its per-domain solved-session
cache in the Redis the services already use; `trawl.redis.enabled=false` turns that off, at the
cost of re-solving from a cold browser on every request.

They are embedded rather than pulled in as subcharts for concrete reasons, recorded in
`Chart.yaml`: the official Valkey chart defines its own `common.image`, which collides with this
repository's `common` library because Helm's template namespace is global across dependencies;
and the NATS chart ships no `values.schema.json`, so `helm schema` derives a closed one from its
own values and makes the very `merge` patches unsettable that its pods need in order to pass this
repository's policy scan.

## Observability

Every service serves a Prometheus scrape on an isolated port. `metrics.serviceMonitor.enabled`
creates **one ServiceMonitor per service**, because metrics carry no service label and the `job`
label is the sole identifier of which service emitted a series.

`jobLabel` is pointed at `app.kubernetes.io/component` deliberately: without it the Prometheus
Operator sets `job` to the Service name, while the vendored rules match on the bare slug
(`up{job=~"api|frontend"}`). The rules would load without error and simply never fire.

The recording rules, alerting rules and Grafana dashboard under `rules/` and `dashboards/` are
vendored verbatim from the upstream repository's `deploy/observability/`. Some alert annotations
still describe `docker compose` remediation steps, because that is what upstream ships.

### Getting the dashboard into a Grafana in another namespace

Grafana has no Kubernetes-native dashboard type, so there are two ways to deliver one and they
differ in exactly the way that matters here.

`metrics.dashboard.enabled` writes a labelled ConfigMap for a Grafana sidecar to discover. The
sidecar decides which namespaces it watches, not this chart: unless the Grafana release sets
`sidecar.dashboards.searchNamespace` to `ALL` or to a list including this namespace — and the
default is Grafana's own — the ConfigMap is created and nothing ever reads it.

`metrics.dashboard.grafanaOperator.enabled` additionally creates a `GrafanaDashboard` per file for
clusters running [grafana-operator](https://github.com/grafana/grafana-operator) v5. That resource
carries `allowCrossNamespaceImport`, so the dashboard declares its own reach and a Grafana
elsewhere can import it without any cluster-wide sidecar configuration. Point `instanceSelector`
at the labels on your `Grafana` custom resource. The resources reference the ConfigMap through
`configMapRef` rather than inlining the JSON, so the ConfigMap stays enabled and the dashboard is
stored once.

The rules have no such choice. `PrometheusRule` is already the Prometheus Operator's own CRD and
has no per-object cross-namespace grant — a Prometheus decides what it loads through
`ruleNamespaceSelector` and `ruleSelector`. `metrics.prometheusRule.labels` is the half of that a
chart can influence; on a kube-prometheus-stack cluster it usually has to carry
`release: kube-prometheus-stack` or the rules are created and never loaded.

Every one of these objects needs its CRDs. When they are missing the chart refuses to render and
says which API is absent, rather than dropping the objects and leaving you with a release that
installed cleanly and is not monitored. Rendering offline — `helm template` reports the built-in
API surface but no CRDs — needs
`--api-versions monitoring.coreos.com/v1 --api-versions grafana.integreatly.org/v1beta1`.

## Upgrading to 2.0.0

TankoVault 1.0.0 replaced FlareSolverr with [TRAWL](https://github.com/germondai/trawl) as its
solver back-end, and this chart follows. The rename is mechanical:

| 1.x | 2.0.0 |
|---|---|
| `flaresolverr.enabled` | `trawl.enabled` |
| `flaresolverr.image.*` | `trawl.image.*` (now `ghcr.io/germondai/trawl`) |
| `flaresolverr.runAsUser` | `trawl.runAsUser` |
| `flaresolverr.shmSize` | `trawl.shmSize` |
| `flaresolverr.resourcesPreset` | `trawl.resources` (no preset fits a browser tier) |
| `externalFlaresolverr.url` | `externalTrawl.url` |
| — | `trawl.browserPoolSize`, `trawl.redis.*` (new) |

There is no compatibility shim: the old keys are rejected by `values.schema.json`, so an upgrade
that missed one fails at render rather than quietly dropping the solver.

Order matters in one direction only. TRAWL serves FlareSolverr's `/v1` API as well as its own, so
a TankoVault 0.4.x release keeps working once the container behind `externalTrawl.url` is swapped;
the reverse is not true. The chart emits `solver.trawl_endpoint`, which only TankoVault 1.0.0 and
later read — upgrade the application first, or in the same step.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| allowUnsafeExposure | bool | `false` | Permit `control-plane`, `sync`, `render` and `challenge-solver` to use a Service type other than `ClusterIP`. Off by default and validated, because those four expose privileged contracts: upstream publishes none of them even on a single host, and reaching them directly bypasses both the frontend proxy and the rate limiter. |
| anilist.clientId | string | `""` | AniList OAuth application client ID. Register at <https://anilist.co/settings/developer>. |
| anilist.clientSecret | string | `""` | AniList OAuth application client secret. |
| anilist.redirectUri | string | `""` | OAuth redirect URI. Left empty it is derived from the ingress as `<external URL>/account/anilist-callback`. It must point at the **frontend**, not the API: the API callback would need the SPA's in-memory bearer token, which a browser redirect cannot carry. |
| anilist.tokenEncryptionKey | string | `""` | Base64 of exactly 32 bytes, sealing every user's AniList token at rest. Left empty the chart generates one when `services.sync` is enabled and remembers it across upgrades, which is the recommended setting; set one explicitly (`openssl rand -base64 32`) only if it has to be known outside the release. Losing it forces every account to re-link; leaking it exposes every stored token. Rotating it does not re-seal tokens already stored. |
| auth.jwtSecret | string | `""` | Token signing secret (`auth.jwt_secret`). Left empty the chart generates one and remembers it across upgrades, which is the recommended setting; set one explicitly (minimum 32 characters, e.g. `openssl rand -hex 32`) only if it has to be known outside the release. The known upstream placeholder is refused at boot in every profile, and rotating the value signs every user out. |
| auth.passwordPepper | string | `""` | Server-side pepper mixed into every argon2id hash, so a database leak alone cannot be brute-forced offline. Left empty the chart generates one **on a first install only** and remembers it across upgrades; a release that already exists without a pepper keeps running without one, because every password stored unpeppered would stop verifying the moment one appeared. For the same reason it must never change once set: rotating or losing it invalidates every stored password, so back the Secret up. The `seed-admin` step receives the identical value, or the administrator it creates could never log in. |
| bootstrap | object | `{"image":{"repository":"timschoenle/tankovault-bootstrap","tag":"v1.2.0@sha256:e0c837144778bf7d7c5ea7b46c37b53ed3a6fdcff6b2a7b1d297bb30277ea309"},"migrate":{"argoSyncWaveBase":0,"backoffLimit":3,"mode":"auto","ordering":"helmHook"},"resourcesPreset":"small","seedAdmin":{"email":"","enabled":false,"password":"","username":"admin"},"seedProviders":{"enabled":false}}` | Schema migration and first-install seeding, all from the `bootstrap` image. Nothing published carries a destructive command; resetting the schema is not available in any image. |
| bootstrap.image.repository | string | `"timschoenle/tankovault-bootstrap"` | Image repository. |
| bootstrap.image.tag | string | `"v1.2.0@sha256:e0c837144778bf7d7c5ea7b46c37b53ed3a6fdcff6b2a7b1d297bb30277ea309"` | Image tag, pinned by digest. |
| bootstrap.migrate.argoSyncWaveBase | int | `0` | Sync wave the migration Job takes under `ordering: argoSyncWave`; the workloads take this plus one, which is what reproduces the `pre-upgrade` guarantee — Argo CD holds a wave until the previous one is healthy, and a Job is healthy only once it is Complete. Anything the migration itself depends on — the ExternalSecret carrying `database__url`, a database some operator provisions — must be given a wave strictly below this one. |
| bootstrap.migrate.backoffLimit | int | `3` | Retries before the migration Job is considered failed. |
| bootstrap.migrate.mode | string | `"auto"` | How `bootstrap migrate` runs. `job` is a `pre-install,pre-upgrade` Helm hook, correct when the database already exists. `initContainer` runs it in every service pod, which is what the bundled PostgreSQL needs — a pre-install hook would run before the StatefulSet exists and could only ever fail. Concurrent runs are safe: sqlx takes a Postgres advisory lock. `auto` picks `initContainer` when `postgresql.enabled`, otherwise `job`. |
| bootstrap.migrate.ordering | string | `"helmHook"` | How the migration Job is ordered against the workloads. Ignored unless `mode` resolves to `job`, and irrelevant under plain `helm install`, which has no phases to order across. `helmHook` is the `pre-install,pre-upgrade` hook, which Argo CD compiles to the PreSync phase — and PreSync runs before **every** Sync-phase resource, with sync waves ordering only within a phase. So when `database__url` arrives from an ExternalSecret, a first install deadlocks and never recovers: the Job's projected secrets volume names a Secret that does not exist yet, the pod sits in ContainerCreating on FailedMount, the Job never completes, PreSync never finishes, Sync never starts, and the ExternalSecret that would have created that Secret is therefore never applied. Nothing inside PreSync can break the cycle. `argoSyncWave` drops the hook annotations entirely and orders the Job by sync wave inside the Sync phase instead, where the ExternalSecret reconciles alongside it. |
| bootstrap.resourcesPreset | string | `"small"` | Resource t-shirt size for the bootstrap containers. |
| bootstrap.seedAdmin.email | string | `""` | Administrator email address. |
| bootstrap.seedAdmin.enabled | bool | `false` | Create the first administrator on install. Create-only: re-running changes nothing. This is the only account privilege is ever minted for, since registration confers none. |
| bootstrap.seedAdmin.password | string | `""` | Initial administrator password. Left empty the chart generates one and remembers it across upgrades; NOTES.txt prints the command that reads it back out of the Secret. It is created with the same `auth.passwordPepper` the API runs with, because a mismatch produces an account that can never log in. |
| bootstrap.seedAdmin.username | string | `"admin"` | Administrator username. |
| bootstrap.seedProviders.enabled | bool | `false` | Install the built-in provider presets. Each can be disabled or retargeted from the admin console afterwards. |
| channels | object | `{"discordWebhookUrl":"","emailTo":[],"webhookUrl":""}` | Notification fan-out targets read by the `notifier` service. |
| channels.discordWebhookUrl | string | `""` | Discord webhook URL. Empty disables the channel. |
| channels.emailTo | list | `[]` | Static recipient addresses for new-chapter notifications. Empty disables the channel. |
| channels.webhookUrl | string | `""` | Generic outbound webhook URL. Empty disables the channel. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| config | object | `{}` | Global TankoVault configuration, expressed exactly as the TOML tree documented in `docs/CONFIGURATION.md` (`database.max_connections`, `security.cors.allowed_origins`, ...). It is rendered to a ConfigMap and mounted as a file, never passed as environment variables, so **changing a value here reloads the running services in place instead of restarting them**. Two carve-outs from upstream: `telemetry.*` and `metrics.*` are installed process-globally and still need a restart to take effect. |
| configExtraToml | string | `""` | Verbatim TOML appended after the rendered `config` tree. The escape hatch for anything this chart's TOML renderer cannot express, notably arrays of tables. |
| configReload.configDir | string | `"/etc/tankovault/config"` | Directory the configuration fragments are mounted at, passed as `TANKOVAULT_CONFIG`. |
| configReload.rolloutOnChange | bool | `false` | Add `checksum/config` pod annotations so a configuration change rolls the Deployments. Off by default, and deliberately so: every TankoVault service watches the directories its configuration came from and rebuilds its runtime when the kubelet updates the mounted ConfigMap or Secret, which is strictly better than a rollout. Turn this on only if you want config changes to behave like an ordinary image bump. |
| configReload.secretsDir | string | `"/etc/tankovault/secrets"` | Directory the credential files are mounted at, passed as `TANKOVAULT_SECRETS_DIR`. |
| defaults | object | `{"affinity":{},"automountServiceAccountToken":false,"extraEnv":[],"extraVolumeMounts":[],"extraVolumes":[],"livenessProbe":{"enabled":true,"failureThreshold":5,"httpGet":{"path":"/health","port":"http"},"periodSeconds":30,"timeoutSeconds":5},"nodeSelector":{},"podAnnotations":{},"podAntiAffinity":"soft","podLabels":{},"podSecurityContext":{"fsGroup":1001,"runAsGroup":1001,"runAsUser":1001},"podSecurityContextPreset":"restricted","priorityClassName":"","readinessProbe":{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/ready","port":"http"},"periodSeconds":10,"timeoutSeconds":3},"resources":{},"resourcesPreset":"medium","revisionHistoryLimit":3,"securityContext":{},"securityContextPreset":"restricted","startupProbe":{"enabled":true,"failureThreshold":30,"httpGet":{"path":"/health","port":"http"},"periodSeconds":5,"timeoutSeconds":3},"strategy":{},"terminationGracePeriodSeconds":30,"tolerations":[],"topologySpreadConstraints":[]}` | Settings shared by every TankoVault workload. Anything here can be overridden per service under `services.<name>`, which is merged over this block. |
| defaults.affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity` when set. |
| defaults.automountServiceAccountToken | bool | `false` | Whether to automount the service account token. Nothing in TankoVault talks to the Kubernetes API. |
| defaults.extraEnv | list | `[]` | Extra environment variables. Use sparingly: this chart delivers configuration as files precisely so it can be reloaded, and a `TANKOVAULT_*` key supplied both here and as a mounted secret file **fails the boot** rather than resolving by precedence. |
| defaults.extraVolumeMounts | list | `[]` | Extra container volume mounts. |
| defaults.extraVolumes | list | `[]` | Extra pod volumes. |
| defaults.livenessProbe | object | `{"enabled":true,"failureThreshold":5,"httpGet":{"path":"/health","port":"http"},"periodSeconds":30,"timeoutSeconds":5}` | Liveness probe. Uses `/health`, never `/ready`: `/ready` reports on Postgres and NATS, so wiring liveness to it would turn a database blip into a fleet-wide restart storm. |
| defaults.livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| defaults.livenessProbe.failureThreshold | int | `5` | Consecutive failures before the container is restarted. |
| defaults.livenessProbe.httpGet | object | `{"path":"/health","port":"http"}` | HTTP handler for the probe. |
| defaults.livenessProbe.httpGet.path | string | `"/health"` | Liveness path. |
| defaults.livenessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| defaults.livenessProbe.periodSeconds | int | `30` | Probe interval. |
| defaults.livenessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| defaults.nodeSelector | object | `{}` | Node selector for pod assignment. |
| defaults.podAnnotations | object | `{}` | Extra pod annotations. |
| defaults.podAntiAffinity | string | `"soft"` | Pod anti-affinity across nodes. `soft` prefers spreading replicas, `hard` requires it. |
| defaults.podLabels | object | `{}` | Extra pod labels. |
| defaults.podSecurityContext | object | `{"fsGroup":1001,"runAsGroup":1001,"runAsUser":1001}` | Pod security context, merged over the preset. The identity fields match the numeric nonroot user baked into every published TankoVault image (`USER 1001:1001`). |
| defaults.podSecurityContext.fsGroup | int | `1001` | Group ID applied to mounted volumes. |
| defaults.podSecurityContext.runAsGroup | int | `1001` | Primary group ID to run as. |
| defaults.podSecurityContext.runAsUser | int | `1001` | User ID to run as. |
| defaults.podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. |
| defaults.priorityClassName | string | `""` | Priority class for the pods. |
| defaults.readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"/ready","port":"http"},"periodSeconds":10,"timeoutSeconds":3}` | Readiness probe. Uses `/ready`, which probes every registered dependency concurrently and returns per-dependency detail. |
| defaults.readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| defaults.readinessProbe.failureThreshold | int | `3` | Consecutive failures before the pod leaves the Service. |
| defaults.readinessProbe.httpGet | object | `{"path":"/ready","port":"http"}` | HTTP handler for the probe. |
| defaults.readinessProbe.httpGet.path | string | `"/ready"` | Readiness path. |
| defaults.readinessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| defaults.readinessProbe.periodSeconds | int | `10` | Probe interval. |
| defaults.readinessProbe.timeoutSeconds | int | `3` | Probe timeout. Upstream bounds each dependency check at 2s, so 3s leaves headroom without letting a wedged probe hang. |
| defaults.resources | object | `{}` | Explicit resources. Wins over `resourcesPreset` when set. |
| defaults.resourcesPreset | string | `"medium"` | Default resource t-shirt size, overridden per service below. |
| defaults.revisionHistoryLimit | int | `3` | Number of old ReplicaSets to retain. |
| defaults.securityContext | object | `{}` | Container security context, merged over the preset. |
| defaults.securityContextPreset | string | `"restricted"` | Container security context baseline. Safe for every service: eight of the nine images are bare `scratch` layers holding only the binary, the musl loader and a CA bundle, so there is no writable path to depend on. |
| defaults.startupProbe | object | `{"enabled":true,"failureThreshold":30,"httpGet":{"path":"/health","port":"http"},"periodSeconds":5,"timeoutSeconds":3}` | Startup probe. Hits `/health`, which deliberately checks nothing external. |
| defaults.startupProbe.enabled | bool | `true` | Enable the startup probe. |
| defaults.startupProbe.failureThreshold | int | `30` | Consecutive failures before the container is considered failed. |
| defaults.startupProbe.httpGet | object | `{"path":"/health","port":"http"}` | HTTP handler for the probe. |
| defaults.startupProbe.httpGet.path | string | `"/health"` | Liveness path. |
| defaults.startupProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| defaults.startupProbe.periodSeconds | int | `5` | Probe interval. |
| defaults.startupProbe.timeoutSeconds | int | `3` | Probe timeout. |
| defaults.strategy | object | `{}` | Deployment update strategy. |
| defaults.terminationGracePeriodSeconds | int | `30` | Grace period for shutdown. Every service cancels its work loop on SIGTERM and stops between units of work. |
| defaults.tolerations | list | `[]` | Tolerations for pod assignment. |
| defaults.topologySpreadConstraints | list | `[]` | Topology spread constraints. |
| email | object | `{"baseUrl":"","from":"","host":"","password":"","port":587,"security":"starttls","username":""}` | Transactional email relay, shared by `api` (password reset, verification) and `notifier`. The channel is enabled only when a relay and `from` are both present; a partial configuration falls back to a no-op mailer that logs and drops. |
| email.baseUrl | string | `""` | Public base URL used to build links in outgoing mail. Left empty it is derived from the ingress. Leaving it pointed at localhost produces unusable links in real emails. |
| email.from | string | `""` | `From` header, e.g. `TankoVault <no-reply@example.com>`. Required for any mail to be sent. |
| email.host | string | `""` | SMTP host. Empty disables outbound email entirely. |
| email.password | string | `""` | SMTP password. |
| email.port | int | `587` | SMTP port. |
| email.security | string | `"starttls"` | Transport security for the relay connection. |
| email.username | string | `""` | SMTP username. |
| existingSecret | string | `""` | Name of an existing Secret holding the credentials, instead of having the chart render one from the values above. When set it takes precedence and no credential value is written into the Helm release. Its keys are the configuration paths with `__` for nesting and no dots — `auth__jwt_secret`, `auth__password_pepper`, `internal__token`, `anilist__client_id`, `anilist__client_secret`, `anilist__token_encryption_key`, `email__username`, `email__password`, `channels__discord_webhook_url`, `channels__webhook_url`, `database__url`, `redis__url`. Only the keys a given service reads are projected into it. |
| externalDatabase | object | `{"existingSecret":"","url":"","urlKey":"database__url"}` | Point TankoVault at a PostgreSQL you already run. Used whenever `postgresql.enabled` is false, and the only supported production topology. |
| externalDatabase.existingSecret | string | `""` | Name of an existing Secret holding the connection URL. |
| externalDatabase.url | string | `""` | Connection URL, e.g. `postgres://user:password@host:5432/tankovault`. Rendered into the chart's Secret; prefer `existingSecret` so it never enters the Helm release. |
| externalDatabase.urlKey | string | `"database__url"` | Key within that Secret. |
| externalNats | object | `{"url":""}` | Point TankoVault at a NATS you already run. Used whenever `nats.enabled` is false. |
| externalNats.url | string | `""` | Connection URL, e.g. `nats://host:4222`. Required by control-plane, worker and notifier; optional on the API, where its absence only degrades the live notification stream. |
| externalRedis | object | `{"existingSecret":"","url":"","urlKey":"redis__url"}` | Point TankoVault at a Redis-compatible server you already run. Used whenever `valkey.enabled` is false. |
| externalRedis.existingSecret | string | `""` | Name of an existing Secret holding the connection URL. |
| externalRedis.url | string | `""` | Connection URL, e.g. `redis://host:6379`. Empty leaves Redis unconfigured, which is supported: the rate limiter and scheduler both degrade rather than fail. |
| externalRedis.urlKey | string | `"redis__url"` | Key within that Secret. |
| externalTrawl | object | `{"url":""}` | Point the challenge solver at a TRAWL you already run. |
| externalTrawl.url | string | `""` | Endpoint URL, e.g. `http://trawl:8191`. |
| fullnameOverride | string | `""` | Override the fully qualified release name. |
| image.pullPolicy | string | `""` | Image pull policy. Empty derives it from the tag: pinned digests and versions get `IfNotPresent`, `latest` gets `Always`. |
| image.registry | string | `""` | Registry host shared by every TankoVault image. Empty means Docker Hub. |
| imagePullSecrets | list | `[]` | Image pull secrets for private registries. |
| ingress | object | `{"annotations":{},"api":{"annotations":{},"enabled":false,"host":""},"className":"","enabled":false,"host":"","path":"/","pathType":"Prefix","tls":{"enabled":false,"secretName":""},"url":""}` | Ingress. Only the frontend is exposed by default; it serves the SPA and proxies `/v1/*` to the API, so one origin covers the whole application and same-origin cookies, SSE and passkey origins all line up without extra configuration. |
| ingress.annotations | object | `{}` | Extra Ingress annotations. |
| ingress.api | object | `{"annotations":{},"enabled":false,"host":""}` | A second Ingress publishing the API on its own hostname, for split-origin clients. Enabling it means the browser origin no longer matches the API origin, so CORS, `auth.webauthn_origin` and cookie `SameSite` all have to be set consistently or logins break in ways that only appear at runtime. |
| ingress.api.annotations | object | `{}` | Extra Ingress annotations. |
| ingress.api.enabled | bool | `false` | Create the API Ingress. |
| ingress.api.host | string | `""` | Hostname the API is served on. |
| ingress.className | string | `""` | IngressClass name. |
| ingress.enabled | bool | `false` | Create an Ingress for the frontend. |
| ingress.host | string | `""` | Hostname the application is served on. |
| ingress.path | string | `"/"` | Path prefix. |
| ingress.pathType | string | `"Prefix"` | Path type. |
| ingress.tls.enabled | bool | `false` | Terminate TLS. Note `auth.cookie_secure` defaults to true, so sessions are lost over plain HTTP on any host other than `localhost`. |
| ingress.tls.secretName | string | `""` | Name of the TLS Secret. Defaults to `<fullname>-tls` when empty. |
| ingress.url | string | `""` | Override the derived external URL used for `anilist.redirect_uri`, `email.base_url` and `auth.webauthn_origin`. Set this when TLS terminates on a proxy in front of the ingress. |
| internal.token | string | `""` | Shared service-to-service token (`internal.token`), identical across api, control-plane, worker, sync, render and challenge-solver. Without it those services accept privileged calls from anything that can reach the port, so the `production` profile refuses to boot without one. Left empty in that profile the chart generates a token and remembers it across upgrades, which is the recommended setting; set one explicitly (minimum 32 characters, e.g. `openssl rand -hex 32`) only if it has to be known outside the release. |
| kubeVersionOverride | string | `""` | Override the detected Kubernetes version used for API version selection. |
| metrics | object | `{"dashboard":{"enabled":false,"grafanaOperator":{"allowCrossNamespaceImport":true,"enabled":false,"folder":"","instanceSelector":{"matchLabels":{"dashboards":"grafana"}},"resyncPeriod":"5m"},"label":"grafana_dashboard","labelValue":"1"},"enabled":true,"port":9090,"prometheusRule":{"enabled":false,"labels":{}},"serviceMonitor":{"enabled":false,"interval":"15s","labels":{},"scrapeTimeout":"10s"}}` | Prometheus integration. Every service serves a scrape on an isolated port, outside the request-facing listener and outside its own HTTP metrics middleware. |
| metrics.dashboard.enabled | bool | `false` | Create a ConfigMap holding the upstream Grafana overview dashboard. |
| metrics.dashboard.grafanaOperator.allowCrossNamespaceImport | bool | `true` | Let the CRs bind to Grafana instances outside this namespace. This is the entire point of the operator path: with `false` the operator only considers Grafana CRs in this release's own namespace, and a Grafana living elsewhere never imports the dashboard. |
| metrics.dashboard.grafanaOperator.enabled | bool | `false` | Also create one `GrafanaDashboard` per dashboard, for clusters running grafana-operator v5. Unlike the sidecar ConfigMap, whose discovery is a property of the *Grafana* release, a `GrafanaDashboard` declares its own reach, so a Grafana in another namespace can import it without cluster-wide sidecar configuration. The CRs reference the ConfigMap through `configMapRef` rather than inlining the JSON, so `dashboard.enabled` must stay true. Requires the `grafana.integreatly.org/v1beta1` CRDs; rendering fails loudly without them. |
| metrics.dashboard.grafanaOperator.folder | string | `""` | Folder to file the dashboards under. Empty leaves them at the Grafana root. |
| metrics.dashboard.grafanaOperator.instanceSelector | object | `{"matchLabels":{"dashboards":"grafana"}}` | Label selector for the Grafana instances to import into. Must select something: an empty selector matches no instance, which the chart refuses rather than rendering a CR that reconciles into nothing. |
| metrics.dashboard.grafanaOperator.resyncPeriod | string | `"5m"` | How often the operator re-reconciles the dashboard, undoing edits made in the Grafana UI. A Go duration. |
| metrics.dashboard.label | string | `"grafana_dashboard"` | Label a Grafana sidecar watches for dashboard ConfigMaps. The sidecar only picks the ConfigMap up if Grafana is configured to look in this namespace — its own by default. Either set `sidecar.dashboards.searchNamespace` to `ALL` on the Grafana release, or use `grafanaOperator` below, which carries the cross-namespace grant on the dashboard itself. |
| metrics.dashboard.labelValue | string | `"1"` | Value for that label. |
| metrics.enabled | bool | `true` | Expose the metrics port on each Service. |
| metrics.port | int | `9090` | Port the Prometheus exposition is served on. |
| metrics.prometheusRule.enabled | bool | `false` | Create a PrometheusRule from the recording and alerting rules vendored from upstream. |
| metrics.prometheusRule.labels | object | `{}` | Extra labels for the PrometheusRule. |
| metrics.serviceMonitor.enabled | bool | `false` | Create one ServiceMonitor per service. One per service, not one for the release: the `job` label is the sole identifier of which service emitted a metric, and collapsing them would silently break every per-service rule. |
| metrics.serviceMonitor.interval | string | `"15s"` | Scrape interval. |
| metrics.serviceMonitor.labels | object | `{}` | Extra labels, e.g. the `release` label a Prometheus Operator selector requires. |
| metrics.serviceMonitor.scrapeTimeout | string | `"10s"` | Scrape timeout. |
| nameOverride | string | `""` | Override the chart name used in resource names. |
| namespaceOverride | string | `""` | Override the namespace objects are created in. |
| nats | object | `{"enabled":false,"image":{"repository":"nats","tag":"2-alpine@sha256:f2123f533c2b0cada0a5c5ec434fb2b8cfe1cf220215ef9d7517e1372917ad66"},"persistence":{"enabled":true,"existingClaim":"","size":"10Gi","storageClassName":""},"resourcesPreset":"medium"}` | Bundled NATS JetStream, the task queue and event bus the control plane, worker and notifier are built on: a single instance with a file store on a PVC. Embedded rather than taken from the NATS authors' chart because that chart ships no `values.schema.json`, so `helm schema` derives a closed one from its own values.yaml and makes the `container.merge` and `podTemplate.merge` patches unsettable — and those patches are the only way to give its pods the security context and resource requests this repository's policy scan requires. Evaluation-tier like the other bundled datastores: no clustering, so no replication and no failover. Use `externalNats` for anything real. |
| nats.enabled | bool | `false` | Deploy the bundled NATS. |
| nats.image.repository | string | `"nats"` | Image repository. |
| nats.image.tag | string | `"2-alpine@sha256:f2123f533c2b0cada0a5c5ec434fb2b8cfe1cf220215ef9d7517e1372917ad66"` | Image tag, pinned by digest. |
| nats.persistence.enabled | bool | `true` | Persist the JetStream file store. Without it every queued scan task and every undelivered event is lost when the pod is rescheduled. |
| nats.persistence.existingClaim | string | `""` | Use an existing claim instead of creating one. |
| nats.persistence.size | string | `"10Gi"` | Requested volume size. |
| nats.persistence.storageClassName | string | `""` | Storage class. Empty uses the cluster default. |
| nats.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| networkPolicy | object | `{"enabled":false,"extraEgress":[],"extraIngress":[],"ingressController":{"namespaceSelector":{},"podSelector":{}},"internetCidrs":["0.0.0.0/0"],"monitoring":{"namespaceSelector":{}}}` | NetworkPolicies. Default-deny per service, then exactly the peers each one needs. Written per service rather than through the `common` builder, which cannot express pod-to-pod rules between nine workloads. |
| networkPolicy.enabled | bool | `false` | Create NetworkPolicies. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended to every service's policy. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended to every service's policy. |
| networkPolicy.ingressController.namespaceSelector | object | `{}` | Namespace selector matching the ingress controller, allowed to reach the frontend. |
| networkPolicy.ingressController.podSelector | object | `{}` | Pod selector matching the ingress controller. |
| networkPolicy.internetCidrs | list | `["0.0.0.0/0"]` | Egress CIDRs treated as "the internet". The worker scrapes provider sites, sync talks to AniList and the notifier reaches SMTP and webhook endpoints, so these tiers need it. RFC1918 ranges and the cloud metadata endpoint are excluded automatically. |
| networkPolicy.monitoring.namespaceSelector | object | `{}` | Namespace selector matching Prometheus, allowed to reach the metrics port. |
| postgresql | object | `{"auth":{"database":"tankovault","password":"","username":"tankovault"},"enabled":false,"image":{"repository":"postgres","tag":"18-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"},"persistence":{"enabled":true,"existingClaim":"","size":"20Gi","storageClassName":""},"resourcesPreset":"large"}` | Bundled PostgreSQL. A single instance with a PVC, on the same image the upstream compose stack pins. Deliberately not an operator and not a third-party subchart, so `helm install` works on a bare cluster — but equally deliberately **not a production database**: one replica, no failover, no point-in-time recovery. Use `externalDatabase` for anything real. |
| postgresql.auth.database | string | `"tankovault"` | Database name. |
| postgresql.auth.password | string | `""` | Database password. Generated and persisted across upgrades when left empty. |
| postgresql.auth.username | string | `"tankovault"` | Database role. |
| postgresql.enabled | bool | `false` | Deploy the bundled PostgreSQL. |
| postgresql.image.repository | string | `"postgres"` | Image repository. |
| postgresql.image.tag | string | `"18-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"` | Image tag, pinned by digest. |
| postgresql.persistence.enabled | bool | `true` | Persist the data directory on a PersistentVolumeClaim. Turning this off means the whole catalogue is lost when the pod is rescheduled. |
| postgresql.persistence.existingClaim | string | `""` | Use an existing claim instead of creating one. |
| postgresql.persistence.size | string | `"20Gi"` | Requested volume size. |
| postgresql.persistence.storageClassName | string | `""` | Storage class. Empty uses the cluster default. |
| postgresql.resourcesPreset | string | `"large"` | Resource t-shirt size. |
| profile | string | `"production"` | Deployment profile, passed as `TANKOVAULT_PROFILE`. This is a process-level key: it is read before the layered configuration exists, so it can only ever come from the environment. `production` is what turns on the fail-fast secret validation and turns off the API docs endpoint; anything other than exactly `production` leaves those safeties off. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account. |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account. One account is shared by every workload: nothing in TankoVault talks to the Kubernetes API, so per-service accounts would add objects without reducing any privilege. |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty). |
| services | object | `{"api":{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-api","tag":"v1.2.0@sha256:22ff0b7a7a97f2cebacf052400036425fea397336b41eb11bb78b361ebde3b65"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}},"challengeSolver":{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-challenge-solver","tag":"v1.2.0@sha256:b64d1d2ea0f6be43f1fa69eccc06f92fdde37284a05ccf9c5b9e8e6438408ba8"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"controlPlane":{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-control-plane","tag":"v1.2.0@sha256:4d5a9ca31ab6cbe37f895ac08d7d90730080bd7ed1b01fc0195213461991a9d2"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"frontend":{"autoscaling":{"enabled":false,"maxReplicas":6,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-frontend","tag":"v1.2.0@sha256:4736e450dc3f0a2b89d8399ed3390427c3c01739cdcb568c19b70954581637fd"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"small","service":{"annotations":{},"type":"ClusterIP"}},"notifier":{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-notifier","tag":"v1.2.0@sha256:b2fe977eaa242aecfaa081de5c4c85bf484ef9781da2184db74ba6de19dd22c3"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"render":{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":false,"homeDir":"/home/nonroot","image":{"repository":"timschoenle/tankovault-render","tag":"v1.2.0@sha256:76b2bbb011c858c2a7e9e9d016d0a2cc87436b6b2f2c4c52967ba23b75daec8d"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resources":{"limits":{"memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}},"service":{"annotations":{},"type":"ClusterIP"},"shmSize":"1Gi"},"sync":{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-sync","tag":"v1.2.0@sha256:836d8deed69fa7e9b5902f0147b35c85a1285957332ec69bf4d0869051e85da1"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"worker":{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-worker","tag":"v1.2.0@sha256:b849f8fa17e88ac2239b9a905589e4b07a9430c9d243d10738982edec087c562"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}}}` | Per-service settings. Each block is merged over `defaults`, so any key from `defaults` may be repeated here for one service only. |
| services.api | object | `{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-api","tag":"v1.2.0@sha256:22ff0b7a7a97f2cebacf052400036425fea397336b41eb11bb78b361ebde3b65"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}}` | The axum REST edge: authentication, read models, write endpoints, administration and the server-sent scan feed. |
| services.api.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.api.autoscaling.maxReplicas | int | `10` | Maximum replicas. |
| services.api.autoscaling.minReplicas | int | `2` | Minimum replicas. |
| services.api.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.api.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.api.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.api.enabled | bool | `true` | Deploy the API. |
| services.api.image.repository | string | `"timschoenle/tankovault-api"` | Image repository. |
| services.api.image.tag | string | `"v1.2.0@sha256:22ff0b7a7a97f2cebacf052400036425fea397336b41eb11bb78b361ebde3b65"` | Image tag, pinned by digest. |
| services.api.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.api.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.api.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.api.replicaCount | int | `2` | Replica count, ignored when autoscaling is enabled. |
| services.api.resourcesPreset | string | `"large"` | Resource t-shirt size. |
| services.api.service.annotations | object | `{}` | Extra Service annotations. |
| services.api.service.type | string | `"ClusterIP"` | Service type. |
| services.challengeSolver | object | `{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-challenge-solver","tag":"v1.2.0@sha256:b64d1d2ea0f6be43f1fa69eccc06f92fdde37284a05ccf9c5b9e8e6438408ba8"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | Modular bot-management bypass tier. Detects Cloudflare, JavaScript and Turnstile interstitials and delegates to a solver backend, TRAWL by default. |
| services.challengeSolver.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.challengeSolver.autoscaling.maxReplicas | int | `4` | Maximum replicas. |
| services.challengeSolver.autoscaling.minReplicas | int | `1` | Minimum replicas. |
| services.challengeSolver.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.challengeSolver.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.challengeSolver.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.challengeSolver.enabled | bool | `true` | Deploy the challenge solver. |
| services.challengeSolver.image.repository | string | `"timschoenle/tankovault-challenge-solver"` | Image repository. |
| services.challengeSolver.image.tag | string | `"v1.2.0@sha256:b64d1d2ea0f6be43f1fa69eccc06f92fdde37284a05ccf9c5b9e8e6438408ba8"` | Image tag, pinned by digest. |
| services.challengeSolver.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.challengeSolver.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.challengeSolver.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.challengeSolver.replicaCount | int | `1` | Replica count, ignored when autoscaling is enabled. |
| services.challengeSolver.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.challengeSolver.service.annotations | object | `{}` | Extra Service annotations. |
| services.challengeSolver.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract. |
| services.controlPlane | object | `{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-control-plane","tag":"v1.2.0@sha256:4d5a9ca31ab6cbe37f895ac08d7d90730080bd7ed1b01fc0195213461991a9d2"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | The singleton scheduler: run planning, task distribution and provider health. Safe to run with more than one replica — it elects a leader through Redis, and falls open to sole-leader when Redis is absent. |
| services.controlPlane.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.controlPlane.enabled | bool | `true` | Deploy the control plane. |
| services.controlPlane.image.repository | string | `"timschoenle/tankovault-control-plane"` | Image repository. |
| services.controlPlane.image.tag | string | `"v1.2.0@sha256:4d5a9ca31ab6cbe37f895ac08d7d90730080bd7ed1b01fc0195213461991a9d2"` | Image tag, pinned by digest. |
| services.controlPlane.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.controlPlane.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.controlPlane.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.controlPlane.replicaCount | int | `1` | Replica count. |
| services.controlPlane.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.controlPlane.service.annotations | object | `{}` | Extra Service annotations. |
| services.controlPlane.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract; the chart refuses anything but `ClusterIP` unless `allowUnsafeExposure` is set. |
| services.frontend | object | `{"autoscaling":{"enabled":false,"maxReplicas":6,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-frontend","tag":"v1.2.0@sha256:4736e450dc3f0a2b89d8399ed3390427c3c01739cdcb568c19b70954581637fd"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"small","service":{"annotations":{},"type":"ClusterIP"}}` | The Dioxus WASM SPA and its axum server. It serves the client and reverse-proxies `/v1/*` to the API, so this single origin is all a browser needs — which is why it is the only service the ingress exposes. |
| services.frontend.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.frontend.autoscaling.maxReplicas | int | `6` | Maximum replicas. |
| services.frontend.autoscaling.minReplicas | int | `2` | Minimum replicas. |
| services.frontend.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. Null disables the CPU metric. |
| services.frontend.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. Null disables the memory metric. |
| services.frontend.config | object | `{}` | Service-specific configuration, merged over the global `config` tree for this service only. Rendered into this service's own TOML fragment. |
| services.frontend.enabled | bool | `true` | Deploy the frontend. |
| services.frontend.image.repository | string | `"timschoenle/tankovault-frontend"` | Image repository. |
| services.frontend.image.tag | string | `"v1.2.0@sha256:4736e450dc3f0a2b89d8399ed3390427c3c01739cdcb568c19b70954581637fd"` | Image tag, pinned by digest. |
| services.frontend.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.frontend.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. Mutually exclusive with `minAvailable`. |
| services.frontend.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.frontend.replicaCount | int | `2` | Replica count, ignored when autoscaling is enabled. |
| services.frontend.resourcesPreset | string | `"small"` | Resource t-shirt size. |
| services.frontend.service.annotations | object | `{}` | Extra Service annotations. |
| services.frontend.service.type | string | `"ClusterIP"` | Service type. The frontend is the one service it is safe to publish directly. |
| services.notifier | object | `{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-notifier","tag":"v1.2.0@sha256:b2fe977eaa242aecfaa081de5c4c85bf484ef9781da2184db74ba6de19dd22c3"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | Distributes new-chapter notifications to users over email, Discord and generic webhooks. |
| services.notifier.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.notifier.enabled | bool | `true` | Deploy the notifier. |
| services.notifier.image.repository | string | `"timschoenle/tankovault-notifier"` | Image repository. |
| services.notifier.image.tag | string | `"v1.2.0@sha256:b2fe977eaa242aecfaa081de5c4c85bf484ef9781da2184db74ba6de19dd22c3"` | Image tag, pinned by digest. |
| services.notifier.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.notifier.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.notifier.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.notifier.replicaCount | int | `1` | Replica count. One by default; upstream runs this tier as a singleton. |
| services.notifier.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.notifier.service.annotations | object | `{}` | Extra Service annotations. |
| services.notifier.service.type | string | `"ClusterIP"` | Service type. |
| services.render | object | `{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":false,"homeDir":"/home/nonroot","image":{"repository":"timschoenle/tankovault-render","tag":"v1.2.0@sha256:76b2bbb011c858c2a7e9e9d016d0a2cc87436b6b2f2c4c52967ba23b75daec8d"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resources":{"limits":{"memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}},"service":{"annotations":{},"type":"ClusterIP"},"shmSize":"1Gi"}` | Optional headless-browser tier for JavaScript-rendered pages; doubles as a solver backend. This is the one service not built on `scratch`: it is a Debian base driving a real Chromium, so it needs writable scratch space and a shared-memory volume. |
| services.render.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.render.autoscaling.maxReplicas | int | `4` | Maximum replicas. |
| services.render.autoscaling.minReplicas | int | `1` | Minimum replicas. |
| services.render.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.render.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.render.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.render.enabled | bool | `false` | Deploy the render tier. |
| services.render.homeDir | string | `"/home/nonroot"` | Home directory of the image's nonroot user, mounted as a writable emptyDir. Chromium writes its profile and crashpad database here; when it is not writable the failure surfaces as a misleading `--database is required` error. |
| services.render.image.repository | string | `"timschoenle/tankovault-render"` | Image repository. |
| services.render.image.tag | string | `"v1.2.0@sha256:76b2bbb011c858c2a7e9e9d016d0a2cc87436b6b2f2c4c52967ba23b75daec8d"` | Image tag, pinned by digest. |
| services.render.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.render.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.render.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.render.replicaCount | int | `1` | Replica count, ignored when autoscaling is enabled. |
| services.render.resources | object | `{"limits":{"memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}}` | Explicit resources. Chromium needs more headroom than the largest preset offers. |
| services.render.resources.limits | object | `{"memory":"2Gi"}` | Resource limits. |
| services.render.resources.limits.memory | string | `"2Gi"` | Memory limit. |
| services.render.resources.requests | object | `{"cpu":"250m","memory":"512Mi"}` | Resource requests. |
| services.render.resources.requests.cpu | string | `"250m"` | CPU request. |
| services.render.resources.requests.memory | string | `"512Mi"` | Memory request. |
| services.render.service.annotations | object | `{}` | Extra Service annotations. |
| services.render.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract. |
| services.render.shmSize | string | `"1Gi"` | Size of the `/dev/shm` in-memory volume. Chromium crashes with cryptic renderer failures on the 64Mi Kubernetes default. |
| services.sync | object | `{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-sync","tag":"v1.2.0@sha256:836d8deed69fa7e9b5902f0147b35c85a1285957332ec69bf4d0869051e85da1"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | Bidirectional AniList integration and metadata enrichment. |
| services.sync.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.sync.enabled | bool | `true` | Deploy the sync service. Requires the `anilist` credentials. |
| services.sync.image.repository | string | `"timschoenle/tankovault-sync"` | Image repository. |
| services.sync.image.tag | string | `"v1.2.0@sha256:836d8deed69fa7e9b5902f0147b35c85a1285957332ec69bf4d0869051e85da1"` | Image tag, pinned by digest. |
| services.sync.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.sync.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.sync.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.sync.replicaCount | int | `1` | Replica count. One by default: the reconcile loop is interval-driven and has no leader election, so extra replicas duplicate outbound AniList calls. |
| services.sync.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.sync.service.annotations | object | `{}` | Extra Service annotations. |
| services.sync.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract. |
| services.worker | object | `{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-worker","tag":"v1.2.0@sha256:b849f8fa17e88ac2239b9a905589e4b07a9430c9d243d10738982edec087c562"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}}` | Fetches and parses provider data through the adapters and upserts chapter and metadata changes. Scales horizontally for free: replicas join one NATS JetStream consumer group. |
| services.worker.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.worker.autoscaling.maxReplicas | int | `10` | Maximum replicas. |
| services.worker.autoscaling.minReplicas | int | `2` | Minimum replicas. |
| services.worker.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.worker.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.worker.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.worker.enabled | bool | `true` | Deploy the worker. |
| services.worker.image.repository | string | `"timschoenle/tankovault-worker"` | Image repository. |
| services.worker.image.tag | string | `"v1.2.0@sha256:b849f8fa17e88ac2239b9a905589e4b07a9430c9d243d10738982edec087c562"` | Image tag, pinned by digest. |
| services.worker.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.worker.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.worker.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.worker.replicaCount | int | `2` | Replica count, ignored when autoscaling is enabled. |
| services.worker.resourcesPreset | string | `"large"` | Resource t-shirt size. |
| services.worker.service.annotations | object | `{}` | Extra Service annotations. |
| services.worker.service.type | string | `"ClusterIP"` | Service type. |
| trawl | object | `{"browserPoolSize":1,"enabled":false,"image":{"repository":"ghcr.io/germondai/trawl","tag":"1.3.1@sha256:1276e2937346190380310e15b3c4cbbf7757827c2ed3056459ad999b10cb90c9"},"redis":{"enabled":true,"url":""},"resources":{"limits":{"memory":"2Gi"},"requests":{"cpu":"500m","memory":"1Gi"}},"runAsUser":1000,"shmSize":"1Gi"}` | Bundled [TRAWL](https://github.com/germondai/trawl), the default backend for the challenge solver. |
| trawl.browserPoolSize | int | `1` | Number of warm browser instances to keep. Each is a full Firefox, so raising this raises `resources` with it. The image's own default is 3, which fits none of the sizes below. |
| trawl.enabled | bool | `false` | Deploy the bundled TRAWL. |
| trawl.image.repository | string | `"ghcr.io/germondai/trawl"` | Image repository. |
| trawl.image.tag | string | `"1.3.1@sha256:1276e2937346190380310e15b3c4cbbf7757827c2ed3056459ad999b10cb90c9"` | Image tag, pinned by digest. Never `latest`: it silently changes the bot-management behaviour the adapters are tuned against. |
| trawl.redis.enabled | bool | `true` | Give TRAWL a Redis for its per-domain solved-session cache. Without one every request re-solves from a cold browser — slower, not broken. |
| trawl.redis.url | string | `""` | Connection URL. Defaults to the same Redis the services use (`valkey` or `externalRedis.url`). Sharing one instance is safe: TankoVault namespaces its keys under `tankovault:` and TRAWL uses `session:<domain>`. |
| trawl.resources | object | `{"limits":{"memory":"2Gi"},"requests":{"cpu":"500m","memory":"1Gi"}}` | Resource requests and limits. Set explicitly rather than by t-shirt size because the largest preset caps memory at 1Gi, and a browser tier exceeds that before it has solved anything. Scale with `browserPoolSize`. |
| trawl.runAsUser | int | `1000` | Numeric UID to run as. The upstream image declares no `USER`, so it would run as root and the restricted Pod Security Standard would refuse the pod. Everything the image ships is world-readable, so any non-root UID works. |
| trawl.shmSize | string | `"1Gi"` | Size of the `/dev/shm` in-memory volume. Firefox needs far more shared memory than the 64Mi Kubernetes provides by default, and fails in confusing ways without it. |
| valkey | object | `{"enabled":false,"image":{"repository":"valkey/valkey","tag":"9.1.1-alpine@sha256:ee91f7a174ac4d6a6b0685b3a60e321f0a9dbbb691f9b0e285be2ba1d1be8328"},"resourcesPreset":"medium"}` | Bundled Valkey, backing the API rate limiter and the control-plane's leader election. Embedded rather than taken as a subchart: the official Valkey chart defines its own `common.image`, and Helm's template namespace is global across dependencies, so it and the `common` library overwrite each other. TankoVault degrades gracefully without Valkey — the rate limiter falls back to per-replica in-memory counters and the scheduler to sole-leader. |
| valkey.enabled | bool | `false` | Deploy the bundled Valkey. |
| valkey.image.repository | string | `"valkey/valkey"` | Image repository. |
| valkey.image.tag | string | `"9.1.1-alpine@sha256:ee91f7a174ac4d6a6b0685b3a60e321f0a9dbbb691f9b0e285be2ba1d1be8328"` | Image tag, pinned by digest. |
| valkey.resourcesPreset | string | `"medium"` | Resource t-shirt size. |

## Source Code

* <https://github.com/TimSchoenle/TankoVault>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
