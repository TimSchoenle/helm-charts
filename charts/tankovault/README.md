# tankovault

![Version: 4.0.0](https://img.shields.io/badge/Version-4.0.0-informational?style=flat-square) ![AppVersion: 3.9.0](https://img.shields.io/badge/AppVersion-3.9.0-informational?style=flat-square)

This chart deploys the full TankoVault manga aggregator stack — frontend, api, control-plane, worker, notifier, sync, challenge-solver and render — hardened to the restricted Pod Security Standard, with file-backed configuration that reloads in place instead of restarting pods, optional bundled PostgreSQL, Valkey, NATS JetStream and TRAWL, and optional Prometheus metrics, alerting rules and Grafana dashboards.

TankoVault is a multi-service manga/manhwa aggregator and tracker. It indexes series metadata
across provider sites and layers watchlists, read progress, notifications and AniList
synchronisation on top — it stores links and metadata only, and never downloads, caches or
serves chapter images.

This chart deploys the whole system: the `frontend` SPA server, the `api` edge, the
`control-plane` scheduler, the `worker` fetch tier, `notifier`, `sync`, `challenge-solver` and
the optional headless `render` tier, plus the one-shot `bootstrap` migration and seeding steps.

> [!IMPORTANT]
> **Upgrading an existing release?** Read
> [UPGRADING.md](https://github.com/TimSchoenle/helm-charts/blob/main/charts/tankovault/UPGRADING.md)
> first. Three versions need a manual step that nothing else will remind you about: 4.0.0 (the
> shared `internal.token` is gone, and the services **refuse to boot** on it), 3.1.0 (pgvector,
> and a one-off `REINDEX` on the bundled database) and 3.0.3 (a one-off StatefulSet recreate).

## Where to look

| If you are | Read |
|---|---|
| installing for the first time | [Quick start](#quick-start), then [Exposure](#exposure) |
| upgrading across 4.0.0 | [Inter-service authentication](#inter-service-authentication) |
| putting Cloudflare in front of it | [Running behind Cloudflare](#running-behind-cloudflare) |
| wiring up your own datastores | [The bundled datastores are evaluation-tier](#the-bundled-datastores-are-evaluation-tier) |
| wondering why a config change did not restart anything | [Configuration reloads instead of restarting](#configuration-reloads-instead-of-restarting) |
| supplying secrets yourself | [Generated credentials](#generated-credentials), [Configuration and secrets](#configuration-and-secrets) |
| deploying through Argo CD | [Argo CD and `bootstrap.migrate.ordering`](#argo-cd-and-bootstrapmigrateordering) |
| hooking up Prometheus or Grafana | [Observability](#observability) |
| publishing terms or a privacy policy | [Legal documents](#legal-documents) |
| turning on two-factor authentication | [Two-factor authentication](#two-factor-authentication) |
| tuning how fast the catalogue is crawled | [Crawl concurrency and the connection pool](#crawl-concurrency-and-the-connection-pool) |
| deciding what a reader is allowed to see | [Adult content is gated off](#adult-content-is-gated-off-and-this-chart-does-not-open-it) |

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A PostgreSQL database **with [pgvector](https://github.com/pgvector/pgvector) available** —
  either the bundled one (`postgresql.enabled=true`, which runs `pgvector/pgvector`) or your own
  (`externalDatabase.*`). See [Recommendations need pgvector](#recommendations-need-pgvector).
- A NATS server with JetStream enabled, if `worker` or `control-plane` are deployed. It must be
  TLS-enabled under the default `internal.identity=mtls`
- cert-manager, a CA issuer, and the CA published into this namespace as a ConfigMap (which is
  what trust-manager does), unless you set `internal.identity=token`. See
  [Inter-service authentication](#inter-service-authentication)
- The Prometheus Operator CRDs, if `metrics.serviceMonitor` or `metrics.prometheusRule` are enabled
- An ingress controller, if `ingress.enabled=true`
- The Gateway API CRDs and a `Gateway` to attach to, if `gateway.enabled=true`
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

Nothing stateful is created unless you ask for it, and the only credentials you must supply are
the ones issued by somebody else. The smallest working install is therefore:

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install tankovault timschoenle/tankovault \
  --set internal.identity=token \
  --set postgresql.enabled=true \
  --set nats.enabled=true \
  --set valkey.enabled=true \
  --set trawl.enabled=true \
  --set bootstrap.seedAdmin.enabled=true
```

That is an evaluation stack — see
[the bundled datastores](#the-bundled-datastores-are-evaluation-tier) before it carries anything
you care about. `services.sync` additionally needs an
[AniList OAuth application](https://anilist.co/settings/developer); set
`services.sync.enabled=false` if you do not want it.

`internal.identity=token` is what makes that line installable on a cluster with nothing else on
it. The chart's default is `mtls`, which needs cert-manager and a CA and is the mode to run in
production — [Inter-service authentication](#inter-service-authentication) is the whole of that
decision. The bundled NATS is the other reason it appears here: it serves plaintext, and `mtls`
requires TLS to the broker.

### Generated credentials

Every credential whose value is meaningful only inside the release is generated when you leave it
empty, and remembered afterwards: the chart reads the previous value back out of its own `Secret`,
so `helm upgrade` never rotates one out from under a running workload.

| Value | Generated when empty | Read it back with |
|---|---|---|
| `auth.jwtSecret` | always | `auth__jwt_secret` |
| `auth.passwordPepper` | first install only (see below) | `auth__password_pepper` |
| `auth.mfaEncryptionKey` | `services.api.enabled` | `auth__mfa_encryption_key` |
| `internal.tokens.api` | `identity=token`, api enabled | `internal__tokens__api` |
| `internal.tokens.worker` | `identity=token`, worker enabled | `internal__tokens__worker` |
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

> [!WARNING]
> **Back the `Secret` up.** Losing `auth__password_pepper` invalidates every stored password;
> losing `anilist__token_encryption_key` forces every account to re-link. That is true however
> those values were set, but generated values exist nowhere else.

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

Four changes do not behave like the rest:

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
  --from-literal=anilist__token_encryption_key="$(openssl rand -base64 32)" \
  --from-literal=internal__tokens__api="$(openssl rand -hex 32)" \
  --from-literal=internal__tokens__worker="$(openssl rand -hex 32)"
```

The last two are needed only under `internal.identity=token`, and they are the one pair of keys
whose name is *not* a configuration path: one token is read as `internal.caller.token` by the
caller that presents it and as `internal.peers.<caller>.token` by each of its callees, so the
Secret key is storage and the projected file name carries the configuration path.

## Inter-service authentication

Each service exposes privileged routes that only certain other services may call, and each callee
carries a compiled-in table of which caller may reach which route. `internal.identity` chooses
only how a caller **proves who it is** — authorisation is identical either way, so the two modes
differ in nothing else:

| Mode | A caller is | Needs |
|---|---|---|
| `mtls` (default) | the DNS SAN on its verified client certificate | cert-manager, a CA issuer, the CA as a ConfigMap, a TLS-enabled NATS |
| `token` | a per-caller secret in `X-Internal-Token` | nothing |

Exactly two services make privileged calls, and that is the whole graph:

| Service | Calls | Called by |
|---|---|---|
| `api` | control-plane, sync, worker | — (public edge) |
| `worker` | challenge-solver, render | api |
| `control-plane`, `sync` | — | api |
| `challenge-solver`, `render` | — | worker |
| `notifier`, `frontend` | — | — |

`worker` is the only service that is both a caller and a callee. `notifier` is neither — it
speaks only to NATS — and still carries the mTLS material, because under `mtls` the broker
connection presents the same client certificate. `frontend` reads none of this: it proxies
`/v1/*` to the API as an ordinary public request and holds no caller identity in either mode.

The NetworkPolicies are that same table: `render` and `challenge-solver` admit `worker` and
nothing else, which matters because both fetch caller-supplied URLs. The one edge in the policies
that is not in the table is `frontend` to `api` — the public request path, which carries no caller
identity by design.

### `mtls`

```yaml
internal:
  identity: mtls
  tls:
    issuerRef:
      name: internal-ca
      kind: ClusterIssuer
    trustBundle:
      name: tankovault-ca      # the ConfigMap a trust-manager Bundle writes into this namespace
```

The chart issues one `Certificate` per service — `notifier` included, `frontend` excluded — each
requesting `<release>-tankovault-<service>.<namespace>.svc` and usable as both a client and a
server certificate. Every callee is configured with the SAN of each caller it accepts, and no
credential for any of this exists in the release.

Three things are worth knowing before you turn it on:

- **The CA has to reach this namespace as a ConfigMap.** That is what trust-manager produces, and
  `internal.tls.trustBundle.name` is both the `Bundle`'s name and the ConfigMap's.
  `internal.tls.trustBundle.create` renders the Bundle for a cluster that has no cluster-wide one;
  it is **off by default** because a Bundle is cluster-scoped, so two releases creating the same
  name would be two objects overwriting each other.
- **Rotation is not a rollout.** Each service re-reads the files every 30 seconds and swaps the
  credential without dropping connections, so cert-manager's renewal is invisible to the fleet.
  No `checksum/` annotation names the TLS Secret, deliberately.
- **NATS must speak TLS.** Under `mtls` a service presents its client certificate to the broker
  too. The bundled `nats` serves plaintext only, so `nats.enabled=true` with `identity: mtls` is
  refused at render time rather than found at runtime.

The peer URLs follow the mode: `control_plane_url`, `sync_url`, `worker_url` and
`worker.challenge_solver_endpoint` become `https://` under `mtls`, because upstream refuses to
boot on a plaintext peer URL there — the connection would be accepted, offer no client
certificate and encrypt nothing, while the peer's own configuration still says it requires both.
Two things deliberately stay plaintext: the frontend's `api_upstream`, which is a reverse-proxied
public request rather than an internal call, and the `/health` and `/ready` probes, which are
mounted outside the authenticated stack and stay on a plain listener because a kubelet probe
presents no client certificate.

### `token`

```yaml
internal:
  identity: token
```

One secret per caller, generated and remembered across upgrades, and each callee receives only
the tokens of the callers it accepts. Holding `worker`'s token opens the routes `worker` may call
and nothing else; a compromised `challenge-solver` — which is only ever a callee — holds no
caller credential at all. Set `internal.tokens.api` / `internal.tokens.worker` explicitly only if
a value has to be known outside the release; each is length-checked at 32 characters in every
profile (`openssl rand -hex 32`).

### Migrating off `internal.token`

The shared token is **refused at boot** by app 4.0.0, in every profile, with no dual-accept
window. A release that carries it through does not degrade — every service stops. The chart fails
the render if `internal.token` is set, naming the replacement, so a values file carrying it cannot
reach a cluster.

The one case the chart cannot see is `existingSecret`: it never reads that Secret's contents, so a
stale `internal__token` in it surfaces as every pod failing to boot at once. Remove that key, and
under `identity: token` add `internal__tokens__api` and `internal__tokens__worker` in its place.

### Per-service NATS accounts

The account each service connects to is carried in its NATS URL, so per-service accounts — what
actually stops `notifier` publishing scan tasks — are delivered as one URL per service:

```yaml
externalNats:
  perServiceSecret: tankovault-nats-accounts   # keys: api, control-plane, worker, notifier
```

Each pod projects its own key as `nats__url`, replacing the derived URL entirely. The accounts
themselves are NATS server configuration: the services present what you give them here and
cannot assert what a broker does not enforce.

### Two-factor authentication

`auth.mfaEncryptionKey` seals every enrolled TOTP secret at rest. The chart generates one when
`services.api` is enabled and remembers it across upgrades, so a default install needs nothing —
but two properties are worth knowing before you touch it:

- **It must never change once anyone has enrolled.** A TOTP secret is symmetric, unlike a password
  hash: rotating or losing the key locks every enrolled account out of its second factor, and the
  repair is clearing `user_totp` and having them enrol again. It is deliberately not derived from
  `auth.jwtSecret`, which is routine to rotate. Back the Secret up alongside `auth.passwordPepper`.
- **Leaving it unset is not neutral.** Unset disables authenticator-app enrolment only — security
  keys and recovery codes still work — but a second factor is required by the *authorization path*
  for any account holding an administrative permission, not by a feature flag. The seeded
  administrator holds all of them, so an install without this key leaves it a hardware security key
  as its only route into the console.

The rest is runtime configuration and goes under `config` like anything else:

```yaml
config:
  auth:
    totp_issuer: TankoVault              # what an authenticator app files the entry under
    step_up_ttl_minutes: 5               # how long a "sudo" elevation lasts
    mfa_challenge_ttl_minutes: 5         # how long a half-finished sign-in may sit
```

Two feature flags govern the rest, both in the admin console. `accounts.mfa` is on by default and
gates enrolment — switching it off hides the option but leaves factors already enrolled working, so
a flag change cannot quietly downgrade an account that opted into protection. `accounts.mfa_required`
ships **off** and extends the requirement from administrators to everyone; turning it on confines
every account without a second factor to the enrolment surface, so pick the day for it.

### Crawl concurrency and the connection pool

A worker scans `worker.max_concurrent_providers` providers at once — 4 by default, and it runs at
most one task per provider, so that number is both its task concurrency and the count of distinct
providers it has in flight. Raising it is the first thing to reach for when the backlog is not
draining and the fetch panels show idle time:

```yaml
services:
  worker:
    config:
      worker:
        max_concurrent_providers: 8
      database:
        max_connections: 32
```

**Raise the pool with it.** Crawl politeness is unaffected — a provider's `rps` and `concurrency`
are enforced by a fetch stack cached per provider that every task for that provider shares — but
`database.max_connections` is not. It defaults to 16 *per replica*, and a scan that cannot acquire
a connection queues on `acquire` and then times out, which surfaces as database errors rather than
as the saturation it is. `0` is clamped to `1`: it would deadlock the consumer loop rather than
disable it, and stopping the crawl is what `providers.active` is for.

Read the result on the pipeline dashboard's *Providers in flight, and lanes with work* panel,
recorded as `namespace_job:scan_tasks_inflight:avg`. Pinned at the cap means the queue is the
constraint and there is more work than concurrency; below the cap while the backlog row is
non-empty means every remaining provider already has a task in flight, and neither a higher cap
nor more worker replicas will help — only more providers would.

The other direction matters on the bundled datastore. Pools are ceilings, not reservations, so
seven replicas at 16 have never actually held 112 connections against `pgvector/pgvector`'s
default `max_connections=100` — but a worker fleet that now really does run several scans at once
holds more of its pool than it used to. It is one more reason the bundled database is
[evaluation-tier](#the-bundled-datastores-are-evaluation-tier).

### Metadata authority and the tag guard

`metadata.priority` decides which source owns each field of a series, and `metadata.tags` decides
which scraped "genres" are refused before they become a facet chip, a recommender term, or an
alternative title:

```yaml
config:
  metadata:
    priority:
      description: [anilist, adapter]
      content_type: [anilist, adapter]
      status: [anilist, adapter]
      release_year: [anilist, adapter]
    tags:
      use_defaults: true       # the shipped refusals: `updating`, `status`, `manga`, ...
      blocklist: [bookmark]    # added to them, never replacing them
      adult_tags: []           # additions only; see below
```

Put both under the top-level `config`, **not** under `services.sync.config`. Two writers put
metadata on a series row — the worker's catalogue scan and sync's enrichment pass — and both read
these sections. A priority only `sync` had was no priority at all: every enriched description was
overwritten by the next scrape. `services.<name>.config` is still the right place for a knob one
service owns, such as `worker.max_concurrent_providers` above.

Only `anilist` and `adapter` are accepted in a priority list, and anything else is a boot failure
naming the key rather than a silently ignored entry, so a typo cannot read as deliberate
de-prioritisation.

### Automatic-merge guards

The duplicate sweep can merge two already-existing series without asking, which deletes a series
row and the id it carries. Four guards turn a pair that clears both the identity rule and the
score threshold into a review-queue row instead, and all four are on by default:

| Key under `config.matching` | Catches |
|---|---|
| `block_auto_merge_on_numeric_conflict` | `Overlord` against `Overlord 2` — nothing else in the scorer distinguishes a sequel from its predecessor |
| `block_auto_merge_on_author_conflict` | Both name authors and share none: a remake, a spin-off, an unrelated work with the same title |
| `block_auto_merge_on_year_conflict` | Release years three or more years apart |
| `block_auto_merge_on_type_conflict` | Both declare a medium and disagree (manga against manhwa) |

Switching one off does not switch its signal off — it still fires, is still scored, and is still
recorded on the decision journal; it just stops blocking the merge. `GET /v1/admin/merge-decisions`
carries `blocked_by` per row, so the way to size these is to run with them on and read the near
misses. The type guard is the one worth reconsidering first, on a deployment whose providers infer
the medium from the site they were scraped from rather than from the work.

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
when the hook finishes, because the release's workloads use them for as long as they run.

> [!NOTE]
> Being hooks, those three do not appear in `helm get manifest`, are not reverted by
> `helm rollback`, and **survive `helm uninstall`**. Delete the leftover `<release>` Secret,
> `<release>` ServiceAccount and `<release>-bootstrap-config` ConfigMap by hand if you want the
> namespace empty.

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

The default is unchanged, so `helm install` consumers and anyone happy with the hook are
unaffected. Only `bootstrap.migrate.mode: job` is affected; the seed steps stay `post-install`
because PostSync runs after Sync and their secrets already exist.

## Recommendations need pgvector

TankoVault 1.3.0 adds a recommender: a vector model built from the catalogue on the
control-plane's schedule and queried per reader on the `api`. Migration
`0027_recsys_signals` runs `CREATE EXTENSION vector`, so **from that release on the database must
have pgvector available**. The migration fails loudly rather than degrading, because a recommender
that silently returns nothing is worse than one that refuses to start.

- **`postgresql.enabled=true`**: the bundled image is `pgvector/pgvector:pg18` — the same
  PostgreSQL major as the `postgres:18-alpine` it replaced, with the extension preinstalled, same
  entrypoint, same environment contract, same uid, so the existing PVC is reused in place and no
  other value changes. **An existing release needs one manual step afterwards**; see
  [UPGRADING.md](https://github.com/TimSchoenle/helm-charts/blob/main/charts/tankovault/UPGRADING.md#310).
  A fresh install needs nothing.
- **`externalDatabase.*`**: install the extension package *before* upgrading — `apt install
  postgresql-18-pgvector`, or your managed provider's equivalent; RDS, Cloud SQL and Azure
  Flexible Server all ship it behind an allowlist setting. The migration only needs it to be
  installable, and issues the `CREATE EXTENSION` itself.

Rolling the migration back drops the recommender's tables and deliberately leaves the extension in
place, since dropping it would cascade into every column typed by it.

### Build cadence and cost

The model is built by the `control-plane`'s scheduler, on the elected leader only, and only while
the `catalogue.recommendations` feature flag is on. Both cadences are ordinary configuration, so
they go under `services.controlPlane.config` like anything else:

```yaml
services:
  controlPlane:
    config:
      scheduler:
        recsys_incremental_interval_secs: 900     # re-embeds what changed
        recsys_full_interval_secs: 604800         # re-solves the projection; 0 disables
        recsys_batch: 512                         # series per streamed batch
        recsys_incremental_max: 20000             # ceiling on one incremental pass
```

Three things about this are worth knowing before you tune it:

- **The full build is not about freshness.** The incremental pass covers changed series; the full
  one exists for vocabulary and idf drift, which is why weekly is enough.
- **A deployment that has never completed a full build has no projection basis, and every
  incremental build refuses to run** — deliberately, because projecting against a partially-solved
  basis yields vectors that are not comparable with the stored ones. Both timers fire once
  immediately when the leader starts, so a fresh install builds on the control-plane's first
  scheduler tick rather than waiting a week. The consequence to remember is the other direction:
  every restart of the leading replica starts a full rebuild.
- **`recsys_batch` is the only knob on the builder's peak memory in the streaming stages.** The
  covariance matrix that dominates the rest is bounded upstream at ~32 MB regardless of catalogue
  size, so the `medium` preset on `control-plane` is not the constraint — but a batch raised far
  above the default against a memory limit will be, and an OOM-killed builder leaves no log line
  and no `failed` metric, only a restart.

To run without the recommender, switch the `catalogue.recommendations` feature flag off in the
admin console. Setting both intervals to `0` stops the builds but leaves the surface on, which
serves empty shelves rather than hiding them.

## Adult content is gated off, and this chart does not open it

From TankoVault 3.0.0 a series flagged adult is excluded from every surface — Discover, search,
the recommender — unless **two** things are both true: the deployment has the
`catalogue.adult_content` feature flag on, and the reader has opted in and attested their age at
`PUT /v1/me/content-prefs`. Anonymous callers cannot satisfy the second and so never see gated
series at all.

The flag is runtime state in the database, set from the admin console, and it ships **off**. This
chart deliberately exposes no value for it: it is the one flag whose default-on failure mode is
showing adult material to an audience nobody chose, and turning it on should be a decision with a
name attached to it rather than a line in a values file. An upgrade therefore hides content a
release was previously serving, which is the intended direction — see
[UPGRADING.md](https://github.com/TimSchoenle/helm-charts/blob/main/charts/tankovault/UPGRADING.md#320).

What the chart *can* influence is the classifier that decides which series are flagged. Two
independent writers feed it: AniList's `isAdult`, which is authoritative wherever the enrichment
sweep found a match and may say either yes or no, and an ingest classifier over the provider's own
genre chips, which may only ever say **yes** and which nothing clears — a provider dropping a chip
and an adapter selector breaking are indistinguishable from the scan's side, and one of them
silently reopens a gate. The shipped terms mean explicit sexual content and nothing else; `ecchi`,
`yaoi`, `yuri`, `bl`, `mature`, `seinen`, `josei` and `doujinshi` are deliberately not among them.
Add your own providers' terms, and note there is no switch that drops the shipped ones:

```yaml
config:
  metadata:
    tags:
      adult_tags: [smut, hentai]
```

The classifier runs against the raw scrape, *before* `metadata.tags.blocklist` — so a term added
there to keep it out of the facet list cannot hide an adult signal from the gate as a side effect.

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

### Running behind Cloudflare

The frontend serves the SPA shell under a Content-Security-Policy it assembles itself, hashing
every inline script in the shell at startup. Cloudflare's bot products inject their own inline
`<script>` **at the edge**, long after that hash was taken — so `script-src` refuses it and the
detection silently never runs. No code change can reach that, which is why there are two flags:

```yaml
cloudflare:
  scriptNonce: true    # Bot Fight Mode, JavaScript Detections, the challenge platform
  turnstile: false     # a Turnstile widget embedded in a page this app serves
```

Both default off, and both belong off anywhere that is not actually behind Cloudflare — each
admits something the policy otherwise refuses, and an unused concession is only a weakness. Most
of what the bot products need is already covered without them: the `/cdn-cgi/challenge-platform/`
scripts and their beacons are same-origin, so `script-src 'self'` and `connect-src 'self'` reach
them. These two are what `'self'` never can.

`scriptNonce` sends a freshly minted `'nonce-…'` in `script-src` on every response; Cloudflare
reads it out of the response header and copies it onto what it injects, so nothing is stamped
into the shell and the shell's own inline scripts keep being admitted by hash. Decide it rather
than setting it by default. An injected script that can already run could read the header back
off a same-origin fetch and admit further inline script — it cannot forge a nonce ahead of time
(128 CSPRNG bits, minted per response), and it still reaches neither `'unsafe-eval'` nor an
off-origin host.

`turnstile` admits `https://challenges.cloudflare.com` in `script-src` **and** `frame-src`, which
is one flag because Turnstile loads `api.js` and then frames the widget from that same host;
admitting the script alone renders an empty box. `frame-ancestors 'none'` is untouched — this app
framing the widget is the opposite direction from this app being framed. A managed-challenge
interstitial needs neither flag: it is a Cloudflare-served document carrying its own policy.

> [!WARNING]
> **Turn Rocket Loader off for this hostname**, and do not put an edge cache in front of the app
> shell. Rocket Loader is a *speed* feature, not a bot product: no flag here makes it work, and it
> breaks the app outright by re-injecting the SPA's `<script type="module">`, whose reload misses
> the bundle, resolves to the app shell, and is rejected as `text/html`. Cloudflare's documented
> opt-out cannot be applied, because the tag is generated at build time — disable it zone-wide or
> with a Configuration Rule for this host. Caching the shell breaks the other half: the nonce is
> only safe while every reader gets their own, and a cached shell pins one across all of them for
> the lifetime of the entry, which is `'unsafe-inline'` with extra steps.

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

The recording rules, alerting rules and dashboards under `rules/` and `dashboards/` are this
chart's own, written against upstream's metric catalogue (`docs/OBSERVABILITY.md`) and against a
Kubernetes deployment of it: every runbook is a `kubectl` command built from labels the alert
itself carries, so it needs no knowledge of the release name.

Readiness is taken from the services' own `service_ready` and `service_dependency_up` gauges. No
blackbox prober is involved, and `TankoVaultDependencyDown` names the failing dependency —
postgres, nats or redis — in the alert rather than sending you to `curl /ready`.

The recommender gets its own rules for the same reason the scan pipeline does: both of its failure
modes are invisible everywhere else. A model that has stopped being built keeps serving, stale and
then empty, without a single error or a millisecond of added latency
(`TankoVaultRecsysBuildFailing`); and an empty shelf is a `200` returned faster than a full one
(`TankoVaultRecsysShelvesEmpty`). Neither moves the request-path metrics at all.

The scan pipeline gets a section of its own for a different reason: `scan_task_duration_seconds`
says a task took nine minutes and nothing about what for, which is the question an operator
actually has. **Why a scan is slow** splits that wall clock two ways — the share spent waiting for
*permission to send* (the concurrency gate, the token rate, the crawl delay and whatever adaptive
penalty a 429 has earned), and, for the remainder, which stage of the task it went into. The two
readings have opposite remedies, so each gets an alert. `TankoVaultScanPaceBound` means the
crawler is being exactly as polite as it was configured to be, and only `politeness.rps` or
`crawl_delay_ms` will change that; `TankoVaultScanIngestBound` means the time is in our own
catalogue write, and the database is where to look. Both are `severity: info` — neither is an
outage, and the value is the diagnosis rather than the page.

Two alerts that a chart should not reinvent are deliberately absent, because the Prometheus
Operator this chart already requires ships both: `KubeDeploymentReplicasMismatch` for replica
shortfalls (it reads kube-state-metrics, which knows the desired count; nothing this chart emits
does), and `PrometheusRuleFailures` for the case where these rule groups stop evaluating.

Every one of these objects needs its CRDs. When they are missing the chart refuses to render and
says which API is absent, rather than dropping the objects and leaving you with a release that
installed cleanly and is not monitored. Rendering offline — `helm template` reports the built-in
API surface but no CRDs — needs
`--api-versions monitoring.coreos.com/v1 --api-versions grafana.integreatly.org/v1beta1`.

### Queue depth needs the NATS exporter

NATS speaks its own monitoring protocol and publishes no Prometheus exposition, so the scan
pipeline's backlog — tasks waiting per provider lane, tasks being redelivered, whether the
notifier's fan-out queue is draining — is invisible without a translator. `metrics.natsExporter`
deploys one and relabels the opaque JetStream `consumer_name` into `provider` and `scan`, so
backlog and throughput can be read on the same axis.

It is off by default. While it is off those series simply do not exist: the panels are empty and
the alerts built on them never fire, rather than a missing exporter reading as a healthy queue.
The exporter scrapes NATS' **monitoring** listener on `:8222`, which is a different port and
protocol from the client URL, so an external NATS needs `metrics.natsExporter.url` set explicitly.

### Confining the rules to this release

A `PrometheusRule` is not scoped to the namespace it lives in. Left alone, `up{job="api"} == 0`
matches an `api` job anywhere Prometheus can see it, so a second TankoVault release — or anyone
else's service that happens to produce the same `job` label — makes both alert on each other's
outages.

`metrics.prometheusRule.scope` defaults to `namespace` and rewrites every expression to match only
series from this release's namespace. Set it to `none` when the Prometheus already sets
`enforcedNamespaceLabel` and would perform the same rewrite itself. The mechanism is a placeholder
substitution rather than PromQL parsing: every selector in `rules/*.yml` carries
`tankovault_scope=~".*"`, an always-true matcher on a label nothing emits, and the chart swaps it
for a real one — so the files are valid, loadable PromQL before and after, and a new rule that
forgets the token fails the render instead of quietly escaping the scope.

Two releases in the *same* namespace cannot be told apart by this or by `enforcedNamespaceLabel`.

### Getting the dashboards into a Grafana in another namespace

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

There are two dashboards: a service overview (liveness, readiness and its dependencies, the
request path, the database pool, edge policy) and a scan pipeline (scheduling, per-provider
throughput and backlog, outbound fetches and throttling, notifications, AniList, and the
recommendation model built from the catalogue the rest of it fills). Neither pins a datasource
UID — both expose a `Data source` picker and a `Namespace` picker, so one copy works for every
release in the cluster.

The rules have no such choice. `PrometheusRule` is already the Prometheus Operator's own CRD and
has no per-object cross-namespace grant — a Prometheus decides what it loads through
`ruleNamespaceSelector` and `ruleSelector`. `metrics.prometheusRule.labels` is the half of that a
chart can influence; on a kube-prometheus-stack cluster it usually has to carry
`release: kube-prometheus-stack` or the rules are created and never loaded.

## Legal documents

TankoVault 1.2.0 lets an operator publish terms, a privacy policy and an imprint, served
unauthenticated by the API at `/v1/legal` and `/v1/legal/{slug}`; the frontend builds its footer's
Legal column from that index. Upstream ships none of the texts, because every deployment is a
different operator under different law and an imprint is a statutory requirement in some
jurisdictions and meaningless in others. With no documents configured the index is empty and the
footer publishes no Legal column at all, rather than links that 404.

Each document under `legal.documents.<slug>` names its body exactly once:

| Key | Who owns the file | Use it when |
|---|---|---|
| `content` | This chart, via a ConfigMap mounted into the API pod | The default. Locale-keyed text written inline in your values. |
| `sources` | You, via `extraVolumes`/`extraVolumeMounts` | The texts live in a Secret, a PVC, or another chart. |
| `url` | Nobody — it is a link | The document is hosted elsewhere. |

The chart refuses a document that sets more than one of these, or none, naming the slug — the
service would otherwise refuse to boot on the same condition at container start.

Documents supplied through `content` are read on demand behind an mtime check, so correcting a
policy is a values change and the kubelet's ConfigMap refresh, never a restart. That ConfigMap is
deliberately excluded from the pod's `checksum/config` annotation for exactly that reason.

## Upgrading

Version-by-version migration notes live in
[UPGRADING.md](https://github.com/TimSchoenle/helm-charts/blob/main/charts/tankovault/UPGRADING.md).
Read it before upgrading across 3.0.3 or 3.1.0 — both need a manual step, and neither fails in a
way that points at it.

## Exposing it through Gateway API

`ingress` and `gateway` are independent switches, so a cluster moving from an Ingress controller
to a Gateway implementation can run both while it migrates. What gets published is the same and
for the same reason — the frontend serves the SPA and reverse-proxies `/v1/*` to the API, so one
origin covers the whole application:

```yaml
ingress:
  enabled: false

gateway:
  enabled: true
  parentRefs:
    - name: shared-gateway
      namespace: gateway-system
  host: tankovault.example.com
  tls:
    enabled: true
```

Only the route belongs to this chart; the `Gateway` — its listeners, its address, its certificates
— is the cluster operator's, and `parentRefs` is how the route asks to be attached to one.
`gateway.create` renders one for an install that has no cluster-wide Gateway, in which case a
route that names no parent attaches to it automatically.

> [!IMPORTANT]
> `gateway.tls.enabled` is what the derived external URL takes its scheme from, and it is
> meaningful even when the Gateway is somebody else's — it says this hostname is served over
> HTTPS, not that this chart terminates it. `anilist.redirect_uri`, `email.base_url` and
> `auth.webauthn_origin` are all derived from that URL, and the application's
> `auth.cookie_secure` defaults to true: an `http://` origin produces a login that appears to
> succeed and lands straight back on the sign-in page. The chart refuses that combination at
> render time rather than letting you find it in production.

The API can take its own hostname exactly as it can under Ingress, with the same warning attached
— a split origin means CORS, `auth.webauthn_origin` and cookie `SameSite` all have to line up:

```yaml
gateway:
  api:
    enabled: true
    host: api.tankovault.example.com
```

What used to live in controller-specific annotations is a typed field: `gateway.filters` for
header manipulation, rewrites, redirects and mirroring, `gateway.timeouts` for
`request`/`backendRequest`, and `gateway.httpsRedirect.enabled` in place of an `ssl-redirect`
annotation.

> [!NOTE]
> Leave `gateway.timeouts.request` unset. The SPA holds the `/v1/events` SSE stream open through
> this route, and a request timeout cuts a long-lived stream at the Gateway — the symptom is a UI
> that quietly stops updating rather than an error anybody sees. `backendRequest` is the one to
> set if you want a bound on ordinary requests.

The NetworkPolicies follow automatically: the frontend admits the Gateway's data plane when
`gateway.enabled` is set, the API when `gateway.api.enabled` is, and the peer is derived from
`gateway.parentRefs` — `gateway.networking.k8s.io/gateway-name`, the label Cilium, Envoy Gateway,
Istio and NGINX Gateway Fabric all put on the pods they provision. Restating the Gateway's
identity under `networkPolicy` would be a second place to edit on a rename, and a policy naming
the wrong Gateway looks correct and blocks everything. `networkPolicy.gateway` overrides either
half for an implementation that labels its pods differently.

## Network policies with Cilium

`networkPolicy.engine` picks the dialect the twelve per-service policies are written in —
`kubernetes` (default), `cilium`, or `both` for a CNI migration. Both are rendered from the same
derived topology, so the engine changes how the rules are written and never what they are.

The internet rules are what the switch buys. `worker` scrapes provider sites, `sync` talks to
AniList and `notifier` reaches SMTP and webhook endpoints — real egress needs that the portable
API can only express as `0.0.0.0/0` minus RFC1918 and the metadata endpoint. That permits every
public host that exists, held by precisely the tier you least want holding it:

```yaml
networkPolicy:
  enabled: true
  engine: cilium
  cilium:
    description: "outbound to the hosts each tier actually talks to"
    egress:
      toFQDNs:
        - matchName: graphql.anilist.co
        - matchPattern: "*.mangadex.org"
      fqdnPorts:
        - port: 443
          protocol: TCP
        - port: 587      # SMTP submission, if notifier sends mail directly
          protocol: TCP
      dnsMatchPatterns:
        - matchPattern: "*.anilist.co"
        - matchPattern: "*.mangadex.org"
```

Setting `toFQDNs` *replaces* the CIDR rule for every service that carried it, rather than adding
to it — emitting both would leave the broad rule in place and make the narrow one decorative. The
in-cluster half of the graph is untouched: service-to-service and datastore rules are the same
edges either way.

`dnsMatchPatterns` is not optional decoration. An FQDN rule is enforced against the addresses
Cilium's DNS proxy saw returned for that name, so the DNS rule has to be there to observe the
lookup; the chart fails the render if FQDN destinations are named with the DNS rule turned off.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| allowUnsafeExposure | bool | `false` | Permit `control-plane`, `sync`, `render` and `challenge-solver` to use a Service type other than `ClusterIP`. Off by default and validated, because those four expose privileged contracts: upstream publishes none of them even on a single host, and reaching them directly bypasses both the frontend proxy and the rate limiter. |
| anilist.clientId | string | `""` | AniList OAuth application client ID. Register at <https://anilist.co/settings/developer>. |
| anilist.clientSecret | string | `""` | AniList OAuth application client secret. |
| anilist.redirectUri | string | `""` | OAuth redirect URI. Left empty it is derived from the ingress as `<external URL>/account/anilist-callback`. It must point at the **frontend**, not the API: the API callback would need the SPA's in-memory bearer token, which a browser redirect cannot carry. |
| anilist.tokenEncryptionKey | string | `""` | Base64 of exactly 32 bytes, sealing every user's AniList token at rest. Left empty the chart generates one when `services.sync` is enabled and remembers it across upgrades, which is the recommended setting; set one explicitly (`openssl rand -base64 32`) only if it has to be known outside the release. Losing it forces every account to re-link; leaking it exposes every stored token. Rotating it does not re-seal tokens already stored. |
| auth.jwtSecret | string | `""` | Token signing secret (`auth.jwt_secret`). Left empty the chart generates one and remembers it across upgrades, which is the recommended setting; set one explicitly (minimum 32 characters, e.g. `openssl rand -hex 32`) only if it has to be known outside the release. The known upstream placeholder is refused at boot in every profile, and rotating the value signs every user out. |
| auth.mfaEncryptionKey | string | `""` | Base64 of exactly 32 bytes (`auth.mfa_encryption_key`), sealing every enrolled TOTP secret at rest. A TOTP secret is symmetric, unlike a password hash — whoever reads the column can mint that account's codes — so a database dump must not be enough on its own. Left empty the chart generates one when `services.api` is enabled and remembers it across upgrades, which is the recommended setting; set one explicitly (`openssl rand -base64 32`) only if it has to be known outside the release. **It must never change once anyone has enrolled**: rotating or losing it locks every enrolled account out of its second factor, and the only repair is clearing `user_totp` and having them enrol again. Deliberately not derived from `jwtSecret`, which is routine to rotate. Unset disables authenticator-app enrolment only — security keys and recovery codes still work — but the seeded administrator holds permissions that require a second factor, so unset leaves it only a hardware key. |
| auth.passwordPepper | string | `""` | Server-side pepper mixed into every argon2id hash, so a database leak alone cannot be brute-forced offline. Left empty the chart generates one **on a first install only** and remembers it across upgrades; a release that already exists without a pepper keeps running without one, because every password stored unpeppered would stop verifying the moment one appeared. For the same reason it must never change once set: rotating or losing it invalidates every stored password, so back the Secret up. The `seed-admin` step receives the identical value, or the administrator it creates could never log in. |
| bootstrap | object | `{"image":{"repository":"timschoenle/tankovault-bootstrap","tag":"v3.9.0@sha256:ef286227e13dda6a48966cba27421fe06249745f9a6da600ac6b55d09a2ab843"},"migrate":{"argoSyncWaveBase":0,"backoffLimit":3,"mode":"auto","ordering":"helmHook"},"resourcesPreset":"small","seedAdmin":{"email":"","enabled":false,"password":"","username":"admin"},"seedProviders":{"enabled":false}}` | Schema migration and first-install seeding, all from the `bootstrap` image. Nothing published carries a destructive command; resetting the schema is not available in any image. |
| bootstrap.image.repository | string | `"timschoenle/tankovault-bootstrap"` | Image repository. |
| bootstrap.image.tag | string | `"v3.9.0@sha256:ef286227e13dda6a48966cba27421fe06249745f9a6da600ac6b55d09a2ab843"` | Image tag, pinned by digest. |
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
| cloudflare | object | `{"scriptNonce":false,"turnstile":false}` | What the frontend's Content-Security-Policy concedes to Cloudflare, one flag per product. Both default off and both belong off on a deployment that is not behind Cloudflare: each admits something the policy otherwise refuses, and an unused concession is only a weakness. Nothing else Cloudflare's bot products need is opened here — the `/cdn-cgi/challenge-platform/` scripts and their beacons are same-origin, so `script-src 'self'` and `connect-src 'self'` already cover them. These are the only two things `'self'` can never reach. |
| cloudflare.scriptNonce | bool | `false` | Send a freshly minted `'nonce-…'` in `script-src` on every response. Set this when the zone runs Bot Fight Mode, JavaScript Detections or the challenge platform: each injects an inline `<script>` at the edge, *after* the frontend has hashed the shell, so `script-src` refuses it and the detection silently never runs. Cloudflare reads the nonce out of the response header and copies it onto what it injects, so nothing is stamped into the shell and the shell's own inline scripts keep being admitted by hash. Decide this rather than setting it by default: an injected script that can already run could read the header back off a same-origin fetch and admit further inline script. It cannot forge a nonce ahead of time (128 CSPRNG bits, minted per response) and it still reaches neither `'unsafe-eval'` nor an off-origin host — but that argument rests on the shell being served `Cache-Control: no-cache`, so **do not put an edge cache in front of the shell**. A cached shell pins one nonce across every reader for the lifetime of the entry, which is `'unsafe-inline'` with extra steps. |
| cloudflare.turnstile | bool | `false` | Admit `https://challenges.cloudflare.com` in `script-src` and `frame-src`, for a Turnstile widget embedded **in** a page the frontend serves. One flag reaches both directives because Turnstile loads `api.js` and then frames the widget from that same host; admitting the script without the frame renders an empty box. A managed-challenge interstitial needs nothing here — it is a Cloudflare-served document carrying its own policy. `frame-ancestors 'none'` is untouched: this app framing the widget is the opposite direction from this app being framed. |
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
| existingSecret | string | `""` | Name of an existing Secret holding the credentials, instead of having the chart render one from the values above. When set it takes precedence and no credential value is written into the Helm release. Its keys are the configuration paths with `__` for nesting and no dots — `auth__jwt_secret`, `auth__password_pepper`, `auth__mfa_encryption_key`, `anilist__client_id`, `anilist__client_secret`, `anilist__token_encryption_key`, `email__username`, `email__password`, `channels__discord_webhook_url`, `channels__webhook_url`, `database__url`, `redis__url`. Only the keys a given service reads are projected into it. Under `internal.identity=token` add `internal__tokens__api` and `internal__tokens__worker`: those two are keyed by caller rather than by configuration path, because one token is read as `internal.caller.token` by the caller and as `internal.peers.<caller>.token` by each of its callees, and the projection maps the key onto whichever path the reading pod needs. Under `mtls` no internal credential is needed at all. |
| externalDatabase | object | `{"existingSecret":"","url":"","urlKey":"database__url"}` | Point TankoVault at a PostgreSQL you already run. Used whenever `postgresql.enabled` is false, and the only supported production topology. |
| externalDatabase.existingSecret | string | `""` | Name of an existing Secret holding the connection URL. |
| externalDatabase.url | string | `""` | Connection URL, e.g. `postgres://user:password@host:5432/tankovault`. Rendered into the chart's Secret; prefer `existingSecret` so it never enters the Helm release. |
| externalDatabase.urlKey | string | `"database__url"` | Key within that Secret. |
| externalNats | object | `{"perServiceSecret":"","url":""}` | Point TankoVault at a NATS you already run. Used whenever `nats.enabled` is false. |
| externalNats.perServiceSecret | string | `""` | Name of an existing Secret holding one NATS URL per service, keyed by service slug (`api`, `control-plane`, `worker`, `notifier`). Each pod projects its own key as `nats__url`, which replaces the derived URL entirely. This is how per-service NATS **accounts** are delivered — the thing that stops `notifier` publishing scan tasks — since the credential belongs in the URL and so cannot live in the ConfigMap. Note that the accounts themselves are NATS server configuration: the services present what you give them here, and cannot assert what a broker does not enforce. |
| externalNats.url | string | `""` | Connection URL, e.g. `nats://host:4222`. Required by control-plane, worker and notifier; optional on the API, where its absence only degrades the live notification stream. Under `internal.identity=mtls` the services present their client certificate to the broker and require TLS on that connection, so it must be a TLS-enabled NATS. |
| externalRedis | object | `{"existingSecret":"","url":"","urlKey":"redis__url"}` | Point TankoVault at a Redis-compatible server you already run. Used whenever `valkey.enabled` is false. |
| externalRedis.existingSecret | string | `""` | Name of an existing Secret holding the connection URL. |
| externalRedis.url | string | `""` | Connection URL, e.g. `redis://host:6379`. Empty leaves Redis unconfigured, which is supported: the rate limiter and scheduler both degrade rather than fail. |
| externalRedis.urlKey | string | `"redis__url"` | Key within that Secret. |
| externalTrawl | object | `{"url":""}` | Point the challenge solver at a TRAWL you already run. |
| externalTrawl.url | string | `""` | Endpoint URL, e.g. `http://trawl:8191`. |
| fullnameOverride | string | `""` | Override the fully qualified release name. |
| gateway | object | `{"addresses":[],"allowedRoutes":{},"annotations":{},"api":{"annotations":{},"enabled":false,"filters":[],"host":"","timeouts":{}},"create":false,"enabled":false,"filters":[],"gatewayClassName":"","host":"","httpPort":80,"httpsPort":443,"httpsRedirect":{"enabled":false,"sectionName":"","statusCode":301},"infrastructure":{},"listeners":[],"parentRefs":[],"path":"/","timeouts":{},"tls":{"certificateRefs":[],"enabled":false,"mode":"Terminate","options":{}},"url":""}` | Gateway API exposure, the successor to `ingress` and an independent switch from it: a cluster migrating from an Ingress controller to a Gateway implementation runs both for a while.  What it publishes is the same thing `ingress` does and for the same reason — the frontend serves the SPA and reverse-proxies `/v1/*` to the API, so one origin covers the whole application. Only the mechanism differs: the `Gateway` (its listeners, its address, its certificates) belongs to the cluster operator, and this chart owns the `HTTPRoute` that attaches to it. |
| gateway.addresses | list | `[]` | Addresses requested for the created Gateway, e.g. a fixed `IPAddress`. |
| gateway.allowedRoutes | object | `{}` | Which routes may attach to the created Gateway's listeners. Defaults to `Same`. |
| gateway.annotations | object | `{}` | Annotations for the HTTPRoute. |
| gateway.api | object | `{"annotations":{},"enabled":false,"filters":[],"host":"","timeouts":{}}` | A second route publishing the API on its own hostname, for split-origin clients. Enabling it means the browser origin no longer matches the API origin, so CORS, `auth.webauthn_origin` and cookie `SameSite` all have to be set consistently or logins break in ways that only appear at runtime. |
| gateway.api.annotations | object | `{}` | Annotations for the API HTTPRoute. |
| gateway.api.enabled | bool | `false` | Create the API HTTPRoute. |
| gateway.api.filters | list | `[]` | Filters applied to the API route. |
| gateway.api.host | string | `""` | Hostname the API is served on. |
| gateway.api.timeouts | object | `{}` | Timeouts for the API route. |
| gateway.create | bool | `false` | Also create the Gateway itself, for an install with no cluster-wide one to attach to. A route that names no parent attaches to it automatically. |
| gateway.enabled | bool | `false` | Create an HTTPRoute for the frontend. Requires the `gateway.networking.k8s.io` CRDs; the render fails loudly rather than silently dropping the route when they are absent. |
| gateway.filters | list | `[]` | Filters applied to the frontend route: `RequestHeaderModifier`, `ResponseHeaderModifier`, `RequestRedirect`, `URLRewrite`, `RequestMirror`, `ExtensionRef`. This is where an Ingress controller's annotations end up, as typed fields. |
| gateway.gatewayClassName | string | `""` | GatewayClass that programs the created Gateway, e.g. `cilium`, `istio`, `envoy-gateway`. Required by `create`; a Gateway without one is never reconciled. |
| gateway.host | string | `""` | Hostname the application is served on. |
| gateway.httpPort | int | `80` | Port for the derived HTTP listener. |
| gateway.httpsPort | int | `443` | Port for the derived HTTPS listener. |
| gateway.httpsRedirect | object | `{"enabled":false,"sectionName":"","statusCode":301}` | A second route that redirects plaintext traffic to HTTPS. Under Ingress this was a controller-specific annotation; Gateway API expresses it as a typed `RequestRedirect` filter, which means it has to be a real object. |
| gateway.httpsRedirect.enabled | bool | `false` | Create the redirect route. |
| gateway.httpsRedirect.sectionName | string | `""` | Listener to bind the redirect to. Must be the plaintext one: attached to every listener the redirect would also apply to the HTTPS listener and loop forever. Defaults to `http`, the name of the listener `create` renders. |
| gateway.httpsRedirect.statusCode | int | `301` | Redirect status code. `301` or `302`. |
| gateway.infrastructure | object | `{}` | `infrastructure.labels` / `infrastructure.annotations` for the created Gateway, passed through to the load balancer the implementation provisions. Where Cilium's LB-IPAM annotations go. |
| gateway.listeners | list | `[]` | Listeners for the created Gateway, replacing the derived `http`/`https` pair entirely. |
| gateway.parentRefs | list | `[]` | Gateways the routes attach to. Each entry takes `name` and optionally `namespace`, `sectionName`, `port`, `group` and `kind`. Required unless `create` is set: a route with no parent is accepted by the API server and then never programmed by any listener. |
| gateway.path | string | `"/"` | Path prefix the route matches. |
| gateway.timeouts | object | `{}` | Timeouts for the frontend route: `request` and `backendRequest`, as Go durations.  Leave `request` unset or generous if the SPA holds the `/v1/events` SSE stream open through this route — a request timeout cuts a long-lived stream at the Gateway, and the symptom is a UI that quietly stops updating rather than an error anybody sees. |
| gateway.tls | object | `{"certificateRefs":[],"enabled":false,"mode":"Terminate","options":{}}` | TLS for the route's hostname. |
| gateway.tls.certificateRefs | list | `[]` | Secrets holding the certificate for the created Gateway's listener. Required by `Terminate`: unlike an Ingress there is no convention by which one is looked up from the hostname. Ignored without `create` — otherwise the certificate is the Gateway owner's. |
| gateway.tls.enabled | bool | `false` | The hostname is served over HTTPS. Two effects: it adds the HTTPS listener when `create` is set, and it is what the derived external URL takes its scheme from — so set it even when the Gateway is somebody else's and it is their listener terminating TLS. Note `auth.cookie_secure` defaults to true, so sessions are lost over plain HTTP on any host other than `localhost`. |
| gateway.tls.mode | string | `"Terminate"` | TLS mode for the created Gateway's listener. |
| gateway.tls.options | object | `{}` | Implementation-specific TLS options for the created Gateway's listener. |
| gateway.url | string | `""` | Override the derived external URL used for `anilist.redirect_uri`, `email.base_url` and `auth.webauthn_origin`. Takes precedence over `ingress.url`. Set this when TLS terminates on a proxy in front of the Gateway. |
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
| internal | object | `{"identity":"mtls","tls":{"caDir":"/etc/tankovault/tls-ca","certDir":"/etc/tankovault/tls","duration":"2160h","issuerRef":{"group":"cert-manager.io","kind":"ClusterIssuer","name":""},"privateKey":{"algorithm":"ECDSA","rotationPolicy":"Always","size":256},"renewBefore":"360h","trustBundle":{"create":false,"key":"ca.crt","name":"tankovault-ca","namespaceSelector":{},"sources":[]}},"token":"","tokens":{"api":"","worker":""}}` | How the services identify each other on their privileged internal routes. Authorisation is the same either way — every callee carries a compiled-in table of which callers may reach which routes — so this chooses only how a caller proves who it is, and the two modes are otherwise indistinguishable. |
| internal.identity | string | `"mtls"` | Identification mode. `mtls` identifies a caller by the DNS SAN on its verified client certificate, issued per service by cert-manager; it is the default and needs no credential in the release at all. `token` gives each caller its own secret, presented in `X-Internal-Token`, and is the mode for clusters with no CA or operators who would rather not run cert-manager. |
| internal.tls | object | `{"caDir":"/etc/tankovault/tls-ca","certDir":"/etc/tankovault/tls","duration":"2160h","issuerRef":{"group":"cert-manager.io","kind":"ClusterIssuer","name":""},"privateKey":{"algorithm":"ECDSA","rotationPolicy":"Always","size":256},"renewBefore":"360h","trustBundle":{"create":false,"key":"ca.crt","name":"tankovault-ca","namespaceSelector":{},"sources":[]}}` | Certificate material for `identity: mtls`. Ignored entirely under `token`. |
| internal.tls.caDir | string | `"/etc/tankovault/tls-ca"` | Directory the CA bundle is mounted at. Kept separate from `certDir` because the two come from different objects written by different controllers — a Secret and a ConfigMap. |
| internal.tls.certDir | string | `"/etc/tankovault/tls"` | Directory the certificate and its key are mounted at, as `tls.crt` and `tls.key`. |
| internal.tls.duration | string | `"2160h"` | Requested certificate lifetime. |
| internal.tls.issuerRef | object | `{"group":"cert-manager.io","kind":"ClusterIssuer","name":""}` | The cert-manager issuer that signs each service's certificate. It has to be one whose CA every service can also verify against, so a public ACME issuer is the wrong shape here — a CA `Issuer` or `ClusterIssuer`, or a Vault/Venafi issuer, is what this expects. |
| internal.tls.issuerRef.group | string | `"cert-manager.io"` | API group of the issuer resource. |
| internal.tls.issuerRef.kind | string | `"ClusterIssuer"` | Issuer kind. |
| internal.tls.issuerRef.name | string | `""` | Issuer name. Required under `mtls`; there is no default to fall back on. |
| internal.tls.privateKey | object | `{"algorithm":"ECDSA","rotationPolicy":"Always","size":256}` | Private key policy, passed to the Certificate verbatim. `rotationPolicy: Always` issues a fresh key on every renewal rather than re-signing the old one. |
| internal.tls.privateKey.algorithm | string | `"ECDSA"` | Key algorithm. |
| internal.tls.privateKey.rotationPolicy | string | `"Always"` | Whether a renewal also rotates the private key. |
| internal.tls.privateKey.size | int | `256` | Key size, in the units the algorithm uses. |
| internal.tls.renewBefore | string | `"360h"` | How far ahead of expiry cert-manager renews. Renewal needs no rollout: the services re-read the files every 30 seconds and swap the credential without dropping connections, which is why no `checksum/` annotation names this Secret. |
| internal.tls.trustBundle | object | `{"create":false,"key":"ca.crt","name":"tankovault-ca","namespaceSelector":{},"sources":[]}` | The trust-manager Bundle carrying the CA every service verifies its peers against. |
| internal.tls.trustBundle.create | bool | `false` | Render the `Bundle` as part of this release. Off by default because a Bundle is cluster-scoped: two releases creating the same name would be two objects fighting over one. Leave it off and point `name` at the ConfigMap your existing cluster-wide Bundle already writes here. |
| internal.tls.trustBundle.key | string | `"ca.crt"` | Key inside that ConfigMap holding the PEM bundle. |
| internal.tls.trustBundle.name | string | `"tankovault-ca"` | Bundle name, which is also the name of the ConfigMap it writes into this namespace and the ConfigMap every pod mounts. Required under `mtls`. |
| internal.tls.trustBundle.namespaceSelector | object | `{}` | Label selector for the namespaces the Bundle writes into, when `create` is set. Empty confines it to this release's namespace; a Bundle with no selector at all would write a ConfigMap into every namespace in the cluster. |
| internal.tls.trustBundle.sources | list | `[]` | trust-manager `spec.sources`, verbatim. Required when `create` is set, because nothing in `issuerRef` says where the CA certificate lives — a `ClusterIssuer` names a Secret in cert-manager's namespace, not in this one. For a CA issuer that is usually `[{secret: {name: internal-ca, key: ca.crt}}]`. |
| internal.token | string | `""` | **Removed in chart 4.0.0.** The single shared token that used to open every privileged route on every service. Upstream refuses it at boot in every profile with no dual-accept window, so this chart fails the render with the migration path rather than letting a release carry it into a fleet-wide crash loop. Use `internal.identity` with `internal.tls` or `internal.tokens`. |
| internal.tokens | object | `{"api":"","worker":""}` | Per-caller tokens for `identity: token`, keyed by caller name. Exactly two services make privileged calls — `api` and `worker` — so exactly two tokens exist, and each callee receives only the tokens of the callers it accepts: holding `worker`'s opens the routes `worker` may call and nothing else. Left empty the chart generates each one and remembers it across upgrades, which is the recommended setting; set one explicitly (minimum 32 characters, e.g. `openssl rand -hex 32`) only if it has to be known outside the release. Ignored under `mtls`, and the chart refuses to render rather than leave a value here meaning nothing. |
| kubeVersionOverride | string | `""` | Override the detected Kubernetes version used for API version selection. |
| legal | object | `{"dir":"/etc/tankovault/legal","documents":{}}` | Operator-published legal documents, served unauthenticated by the API at `/v1/legal` and `/v1/legal/{slug}`; the frontend's footer builds its Legal column from that index. Upstream ships none of these on purpose — every deployment is a different operator under different law, and an imprint is a statutory requirement in some jurisdictions and meaningless in others. With no documents the index is empty and the footer publishes no Legal column at all, rather than links that 404. |
| legal.dir | string | `"/etc/tankovault/legal"` | Directory relative `sources` paths resolve against, and where the chart mounts any document supplied through `content`. An absolute `sources` path ignores it. |
| legal.documents | object | `{}` | Published documents, keyed by the URL slug they are served under. Each document names its body exactly once, in one of three ways, and the chart refuses a document that names it twice or not at all:  - `content`: a locale-keyed map of the text itself. The chart writes it into a ConfigMap as   `<slug>.<locale>.md` and mounts it into the API pod. Edits are picked up without a restart —   the service re-reads behind an mtime check — so this is the option to reach for by default. - `sources`: a locale-keyed map of file paths you have arranged to mount yourself, through   `extraVolumes`/`extraVolumeMounts`. Use this when the texts belong to a Secret, a PVC or   another chart. - `url`: an absolute `http(s)` link to a document hosted elsewhere. Mounts nothing.  `title` is a locale-keyed display name, and `updated` a free-form date shown alongside it. Both are optional and independent of how the body is supplied.  <details><summary>Example</summary>  ```yaml legal:   documents:     terms:       updated: "2026-08-04"       title:         en: Terms of Service         de: Nutzungsbedingungen       content:         en: |           # Terms of Service           ...         de: |           # Nutzungsbedingungen           ...     imprint:       title:         de: Impressum       url: https://example.org/impressum ```  </details> |
| metrics | object | `{"dashboard":{"enabled":false,"grafanaOperator":{"allowCrossNamespaceImport":true,"enabled":false,"folder":"","instanceSelector":{"matchLabels":{"dashboards":"grafana"}},"resyncPeriod":"5m"},"label":"grafana_dashboard","labelValue":"1"},"enabled":true,"natsExporter":{"enabled":false,"image":{"repository":"natsio/prometheus-nats-exporter","tag":"0.20.1@sha256:4fbf6dacb84780a45a1c3af9b1080c69451a288d20902deae671b80717bb8f61"},"resourcesPreset":"nano","url":""},"port":9090,"prometheusRule":{"enabled":false,"labels":{},"scope":"namespace"},"serviceMonitor":{"enabled":false,"interval":"15s","labels":{},"scrapeTimeout":"10s"}}` | Prometheus integration. Every service serves a scrape on an isolated port, outside the request-facing listener and outside its own HTTP metrics middleware. |
| metrics.dashboard.enabled | bool | `false` | Create a ConfigMap holding the upstream Grafana overview dashboard. |
| metrics.dashboard.grafanaOperator.allowCrossNamespaceImport | bool | `true` | Let the CRs bind to Grafana instances outside this namespace. This is the entire point of the operator path: with `false` the operator only considers Grafana CRs in this release's own namespace, and a Grafana living elsewhere never imports the dashboard. |
| metrics.dashboard.grafanaOperator.enabled | bool | `false` | Also create one `GrafanaDashboard` per dashboard, for clusters running grafana-operator v5. Unlike the sidecar ConfigMap, whose discovery is a property of the *Grafana* release, a `GrafanaDashboard` declares its own reach, so a Grafana in another namespace can import it without cluster-wide sidecar configuration. The CRs reference the ConfigMap through `configMapRef` rather than inlining the JSON, so `dashboard.enabled` must stay true. Requires the `grafana.integreatly.org/v1beta1` CRDs; rendering fails loudly without them. |
| metrics.dashboard.grafanaOperator.folder | string | `""` | Folder to file the dashboards under. Empty leaves them at the Grafana root. |
| metrics.dashboard.grafanaOperator.instanceSelector | object | `{"matchLabels":{"dashboards":"grafana"}}` | Label selector for the Grafana instances to import into. Must select something: an empty selector matches no instance, which the chart refuses rather than rendering a CR that reconciles into nothing. |
| metrics.dashboard.grafanaOperator.resyncPeriod | string | `"5m"` | How often the operator re-reconciles the dashboard, undoing edits made in the Grafana UI. A Go duration. |
| metrics.dashboard.label | string | `"grafana_dashboard"` | Label a Grafana sidecar watches for dashboard ConfigMaps. The sidecar only picks the ConfigMap up if Grafana is configured to look in this namespace — its own by default. Either set `sidecar.dashboards.searchNamespace` to `ALL` on the Grafana release, or use `grafanaOperator` below, which carries the cross-namespace grant on the dashboard itself. |
| metrics.dashboard.labelValue | string | `"1"` | Value for that label. |
| metrics.enabled | bool | `true` | Expose the metrics port on each Service. |
| metrics.natsExporter.enabled | bool | `false` | Deploy `prometheus-nats-exporter` next to NATS. NATS speaks its own monitoring protocol and no Prometheus exposition, so without this the scan pipeline's queue depth — tasks waiting per provider lane, tasks being redelivered, whether the notifier backlog is draining — is invisible. Nothing the services emit substitutes: they can report tasks handed out, not tasks still sitting in the broker. The rules and dashboard panels that need it are simply empty while it is off, never falsely green. |
| metrics.natsExporter.image.repository | string | `"natsio/prometheus-nats-exporter"` | Image repository. |
| metrics.natsExporter.image.tag | string | `"0.20.1@sha256:4fbf6dacb84780a45a1c3af9b1080c69451a288d20902deae671b80717bb8f61"` | Image tag, pinned by digest. |
| metrics.natsExporter.resourcesPreset | string | `"nano"` | Resource t-shirt size. The exporter polls two JSON endpoints and re-serves them; it does no work proportional to traffic. |
| metrics.natsExporter.url | string | `""` | NATS monitoring endpoint to scrape. Defaults to the bundled NATS' `:8222`. This is not the client URL: `externalNats.url` points at 4222 and speaks the NATS protocol, while the exporter reads HTTP on the monitoring listener, so an external NATS needs this set explicitly — e.g. `http://nats.example.com:8222`. |
| metrics.port | int | `9090` | Port the Prometheus exposition is served on. |
| metrics.prometheusRule.enabled | bool | `false` | Create a PrometheusRule from the recording and alerting rules under `rules/`. |
| metrics.prometheusRule.labels | object | `{}` | Extra labels for the PrometheusRule. |
| metrics.prometheusRule.scope | string | `"namespace"` | Confine the rules to this release's namespace. A `PrometheusRule` is not scoped to the namespace it lives in, so an unscoped `up{job="api"} == 0` matches an `api` job anywhere Prometheus can see — a second TankoVault release, or anyone else's service that happens to produce the same `job` label, and the two then alert on each other. `namespace` rewrites every rule expression to match only series from this namespace; `none` installs them as written, which is correct when a Prometheus already sets `enforcedNamespaceLabel` and would do the same rewrite itself, and wrong otherwise. Two releases of this chart in *one* namespace cannot be told apart by either mechanism. |
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
| networkPolicy | object | `{"cilium":{"description":"","egress":{"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"extraEgress":[],"extraIngress":[],"ingress":{"fromEntities":[]}},"enabled":false,"engine":"kubernetes","extraEgress":[],"extraIngress":[],"gateway":{"namespaceSelector":{},"podSelector":{}},"ingressController":{"namespaceSelector":{},"podSelector":{}},"internetCidrs":["0.0.0.0/0"],"monitoring":{"namespaceSelector":{}}}` | NetworkPolicies. Default-deny per service, then exactly the peers each one needs. Written per service rather than through the `common` builder, which cannot express pod-to-pod rules between nine workloads. |
| networkPolicy.cilium | object | `{"description":"","egress":{"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]},"extraEgress":[],"extraIngress":[],"ingress":{"fromEntities":[]}}` | Cilium-only additions, used when `engine` is `cilium` or `both`. The whole topology above is rendered into the CiliumNetworkPolicies automatically; these are the rules the portable API has no way to express.  Note that `extraIngress` and `extraEgress` above are *not* carried over: those are verbatim `networking.k8s.io/v1` rule objects and are not valid CNP. The fields below are their counterparts. |
| networkPolicy.cilium.description | string | `""` | `spec.description`, which Cilium surfaces in `cilium policy get` and in Hubble flow verdicts. The one place to record why a rule exists where an operator debugging a drop will actually see it. |
| networkPolicy.cilium.egress | object | `{"dnsMatchPatterns":[],"entityPorts":[],"fqdnPorts":[],"httpRules":[],"toEntities":[],"toFQDNs":[]}` | Cilium-only egress rules. |
| networkPolicy.cilium.egress.dnsMatchPatterns | list | `[]` | What the DNS proxy may resolve, e.g. `- matchPattern: "*.example.com"`. Defaults to everything, which only permits the lookup — an answer is still only reachable if some rule allows the address. |
| networkPolicy.cilium.egress.entityPorts | list | `[]` | Restrict the `toEntities` rule to specific ports. Empty means all ports. |
| networkPolicy.cilium.egress.fqdnPorts | list | `[]` | Ports the `toFQDNs` rule allows. Defaults to TCP/443. SMTP submission needs 587 here if `notifier` sends mail directly. |
| networkPolicy.cilium.egress.httpRules | list | `[]` | L7 HTTP rules layered onto the `toFQDNs` rule, e.g. `- method: GET` / `path: "/v1/.*"`. Costs a proxy hop per connection. |
| networkPolicy.cilium.egress.toEntities | list | `[]` | Named destination sets added to every policy, e.g. `kube-apiserver`. |
| networkPolicy.cilium.egress.toFQDNs | list | `[]` | The hosts the internet-facing tiers may reach, by name: `- matchName: api.anilist.co` or `- matchPattern: "*.example.com"`. Set, this *replaces* the `internetCidrs` rule for every service that carries it — `worker`, `sync`, `notifier`, `render` and the bundled TRAWL. That is the point: `0.0.0.0/0` on 443 permits every public host that exists, and a scraping tier is precisely the one you do not want holding that.  Emitting both would leave the broad rule in place and make this one decorative, so it is a substitution rather than an addition. |
| networkPolicy.cilium.extraEgress | list | `[]` | Extra egress rules in CiliumNetworkPolicy form, appended to every service's policy. |
| networkPolicy.cilium.extraIngress | list | `[]` | Extra ingress rules in CiliumNetworkPolicy form, appended to every service's policy. |
| networkPolicy.cilium.ingress | object | `{"fromEntities":[]}` | Cilium-only ingress rules, added to every service's policy. |
| networkPolicy.cilium.ingress.fromEntities | list | `[]` | Named source sets, e.g. `cluster`, `host`, `remote-node`, `kube-apiserver`. |
| networkPolicy.enabled | bool | `false` | Create NetworkPolicies. |
| networkPolicy.engine | string | `"kubernetes"` | Which policy dialect to render. `kubernetes` emits the portable `networking.k8s.io/v1` objects; `cilium` emits `CiliumNetworkPolicy`, which can express FQDN destinations, named entities and L7 rules that the portable API cannot; `both` emits both, for the window in which a cluster is migrating between CNIs.  Both dialects are rendered from the same derived topology, so they cannot describe different graphs — the engine picks how the rules are written, never what they are. |
| networkPolicy.extraEgress | list | `[]` | Extra egress rules appended to every service's policy. |
| networkPolicy.extraIngress | list | `[]` | Extra ingress rules appended to every service's policy. |
| networkPolicy.gateway | object | `{"namespaceSelector":{},"podSelector":{}}` | Selectors matching the Gateway API data plane, allowed to reach the frontend (and the API when `gateway.api.enabled` is set). Left empty they are derived from `gateway.parentRefs`: the namespace of the first parent, and `gateway.networking.k8s.io/gateway-name` — the label Cilium, Envoy Gateway, Istio and NGINX Gateway Fabric all put on the pods they provision.  Restating the Gateway's identity here would be a second place to edit on a rename, and a policy naming the wrong Gateway looks correct and blocks all inbound traffic. |
| networkPolicy.gateway.namespaceSelector | object | `{}` | Namespace selector matching the Gateway's data plane. Empty derives it. |
| networkPolicy.gateway.podSelector | object | `{}` | Pod selector matching the Gateway's data plane. Empty derives it. |
| networkPolicy.ingressController.namespaceSelector | object | `{}` | Namespace selector matching the ingress controller, allowed to reach the frontend. |
| networkPolicy.ingressController.podSelector | object | `{}` | Pod selector matching the ingress controller. |
| networkPolicy.internetCidrs | list | `["0.0.0.0/0"]` | Egress CIDRs treated as "the internet". The worker scrapes provider sites, sync talks to AniList and the notifier reaches SMTP and webhook endpoints, so these tiers need it. RFC1918 ranges and the cloud metadata endpoint are excluded automatically. |
| networkPolicy.monitoring.namespaceSelector | object | `{}` | Namespace selector matching Prometheus, allowed to reach the metrics port. |
| postgresql | object | `{"auth":{"database":"tankovault","password":"","username":"tankovault"},"enabled":false,"image":{"repository":"pgvector/pgvector","tag":"pg18@sha256:691673308c99d2161ba298736f3147f1f22d79de2fb7ec93ae9b4afcab870b62"},"persistence":{"enabled":true,"existingClaim":"","size":"20Gi","storageClassName":""},"resourcesPreset":"large"}` | Bundled PostgreSQL. A single instance with a PVC, on the same image the upstream compose stack pins. Deliberately not an operator and not a third-party subchart, so `helm install` works on a bare cluster — but equally deliberately **not a production database**: one replica, no failover, no point-in-time recovery. Use `externalDatabase` for anything real — and give it [pgvector](https://github.com/pgvector/pgvector), which migration `0027` requires. |
| postgresql.auth.database | string | `"tankovault"` | Database name. |
| postgresql.auth.password | string | `""` | Database password. Generated and persisted across upgrades when left empty. |
| postgresql.auth.username | string | `"tankovault"` | Database role. |
| postgresql.enabled | bool | `false` | Deploy the bundled PostgreSQL. |
| postgresql.image.repository | string | `"pgvector/pgvector"` | Image repository. `pgvector/pgvector`, not stock `postgres`: TankoVault migration `0027_recsys_signals` runs `CREATE EXTENSION vector` and the recommender retrieves through an HNSW index, so the extension is a hard dependency of the deployment rather than an optional extra. This is the official PostgreSQL image with pgvector preinstalled — same major, same entrypoint, same environment contract, same `postgres` uid — so nothing else in this block changes and an existing PVC is reused in place. It is Debian-based rather than Alpine; `resourcesPreset` already accommodates that. |
| postgresql.image.tag | string | `"pg18@sha256:691673308c99d2161ba298736f3147f1f22d79de2fb7ec93ae9b4afcab870b62"` | Image tag, pinned by digest. Held to the same digest as upstream's compose stack and CI services, so the database this chart runs is the one the queries were type-checked against. |
| postgresql.persistence.enabled | bool | `true` | Persist the data directory on a PersistentVolumeClaim. Turning this off means the whole catalogue is lost when the pod is rescheduled. |
| postgresql.persistence.existingClaim | string | `""` | Use an existing claim instead of creating one. |
| postgresql.persistence.size | string | `"20Gi"` | Requested volume size. |
| postgresql.persistence.storageClassName | string | `""` | Storage class. Empty uses the cluster default. |
| postgresql.resourcesPreset | string | `"large"` | Resource t-shirt size. |
| profile | string | `"production"` | Deployment profile, passed as `TANKOVAULT_PROFILE`. This is a process-level key: it is read before the layered configuration exists, so it can only ever come from the environment. `production` is what turns on the fail-fast secret validation and turns off the API docs endpoint; anything other than exactly `production` leaves those safeties off. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account. |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account. One account is shared by every workload: nothing in TankoVault talks to the Kubernetes API, so per-service accounts would add objects without reducing any privilege. |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty). |
| services | object | `{"api":{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-api","tag":"v3.9.0@sha256:abfa00cd4d395fcd8fb3ce461f9013d19c8df02d405f27c7b11e70080fc50f10"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}},"challengeSolver":{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-challenge-solver","tag":"v3.9.0@sha256:a5170a70659efaf6a9b0aecef856aeca076c6484451605f961e35e15f0ff5ee5"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"controlPlane":{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-control-plane","tag":"v3.9.0@sha256:78f831ca4fb3609c7c4523e8d636166b8c2e27bcaca86dab75e2d62842dad9d0"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"frontend":{"autoscaling":{"enabled":false,"maxReplicas":6,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-frontend","tag":"v3.9.0@sha256:5ba4828e4a8ae2ce27f548bc1d9664609509d97d2d3cc09af14c9014a227376b"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"small","service":{"annotations":{},"type":"ClusterIP"}},"notifier":{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-notifier","tag":"v3.9.0@sha256:b87d317c58b9d813ade9ad02d7e019752a7d47bdf45994ed321902ee72b9e31d"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"render":{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":false,"homeDir":"/home/nonroot","image":{"repository":"timschoenle/tankovault-render","tag":"v3.9.0@sha256:94d586f02feb4e32401df8e9ed065e76405570863245d2d7c7f48338b7594880"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resources":{"limits":{"memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}},"service":{"annotations":{},"type":"ClusterIP"},"shmSize":"1Gi"},"sync":{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-sync","tag":"v3.9.0@sha256:22ad04c979b1dd04e5604e85a466a0b943601cf8beaae3ec4f612d851530ed9e"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}},"worker":{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-worker","tag":"v3.9.0@sha256:d19948768b24e6da513906995d0c729166cc983a2bfdddb53c02190d209e5105"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}}}` | Per-service settings. Each block is merged over `defaults`, so any key from `defaults` may be repeated here for one service only. |
| services.api | object | `{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-api","tag":"v3.9.0@sha256:abfa00cd4d395fcd8fb3ce461f9013d19c8df02d405f27c7b11e70080fc50f10"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}}` | The axum REST edge: authentication, read models, write endpoints, administration and the server-sent scan feed. |
| services.api.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.api.autoscaling.maxReplicas | int | `10` | Maximum replicas. |
| services.api.autoscaling.minReplicas | int | `2` | Minimum replicas. |
| services.api.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.api.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.api.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.api.enabled | bool | `true` | Deploy the API. |
| services.api.image.repository | string | `"timschoenle/tankovault-api"` | Image repository. |
| services.api.image.tag | string | `"v3.9.0@sha256:abfa00cd4d395fcd8fb3ce461f9013d19c8df02d405f27c7b11e70080fc50f10"` | Image tag, pinned by digest. |
| services.api.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.api.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.api.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.api.replicaCount | int | `2` | Replica count, ignored when autoscaling is enabled. |
| services.api.resourcesPreset | string | `"large"` | Resource t-shirt size. |
| services.api.service.annotations | object | `{}` | Extra Service annotations. |
| services.api.service.type | string | `"ClusterIP"` | Service type. |
| services.challengeSolver | object | `{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-challenge-solver","tag":"v3.9.0@sha256:a5170a70659efaf6a9b0aecef856aeca076c6484451605f961e35e15f0ff5ee5"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | Modular bot-management bypass tier. Detects Cloudflare, JavaScript and Turnstile interstitials and delegates to a solver backend, TRAWL by default. |
| services.challengeSolver.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.challengeSolver.autoscaling.maxReplicas | int | `4` | Maximum replicas. |
| services.challengeSolver.autoscaling.minReplicas | int | `1` | Minimum replicas. |
| services.challengeSolver.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.challengeSolver.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.challengeSolver.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.challengeSolver.enabled | bool | `true` | Deploy the challenge solver. |
| services.challengeSolver.image.repository | string | `"timschoenle/tankovault-challenge-solver"` | Image repository. |
| services.challengeSolver.image.tag | string | `"v3.9.0@sha256:a5170a70659efaf6a9b0aecef856aeca076c6484451605f961e35e15f0ff5ee5"` | Image tag, pinned by digest. |
| services.challengeSolver.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.challengeSolver.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.challengeSolver.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.challengeSolver.replicaCount | int | `1` | Replica count, ignored when autoscaling is enabled. |
| services.challengeSolver.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.challengeSolver.service.annotations | object | `{}` | Extra Service annotations. |
| services.challengeSolver.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract. |
| services.controlPlane | object | `{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-control-plane","tag":"v3.9.0@sha256:78f831ca4fb3609c7c4523e8d636166b8c2e27bcaca86dab75e2d62842dad9d0"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | The singleton scheduler: run planning, task distribution and provider health. Safe to run with more than one replica — it elects a leader through Redis, and falls open to sole-leader when Redis is absent. |
| services.controlPlane.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.controlPlane.enabled | bool | `true` | Deploy the control plane. |
| services.controlPlane.image.repository | string | `"timschoenle/tankovault-control-plane"` | Image repository. |
| services.controlPlane.image.tag | string | `"v3.9.0@sha256:78f831ca4fb3609c7c4523e8d636166b8c2e27bcaca86dab75e2d62842dad9d0"` | Image tag, pinned by digest. |
| services.controlPlane.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.controlPlane.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.controlPlane.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.controlPlane.replicaCount | int | `1` | Replica count. |
| services.controlPlane.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.controlPlane.service.annotations | object | `{}` | Extra Service annotations. |
| services.controlPlane.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract; the chart refuses anything but `ClusterIP` unless `allowUnsafeExposure` is set. |
| services.frontend | object | `{"autoscaling":{"enabled":false,"maxReplicas":6,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-frontend","tag":"v3.9.0@sha256:5ba4828e4a8ae2ce27f548bc1d9664609509d97d2d3cc09af14c9014a227376b"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"small","service":{"annotations":{},"type":"ClusterIP"}}` | The Dioxus WASM SPA and its axum server. It serves the client and reverse-proxies `/v1/*` to the API, so this single origin is all a browser needs — which is why it is the only service the ingress exposes. |
| services.frontend.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.frontend.autoscaling.maxReplicas | int | `6` | Maximum replicas. |
| services.frontend.autoscaling.minReplicas | int | `2` | Minimum replicas. |
| services.frontend.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. Null disables the CPU metric. |
| services.frontend.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. Null disables the memory metric. |
| services.frontend.config | object | `{}` | Service-specific configuration, merged over the global `config` tree for this service only. Rendered into this service's own TOML fragment. |
| services.frontend.enabled | bool | `true` | Deploy the frontend. |
| services.frontend.image.repository | string | `"timschoenle/tankovault-frontend"` | Image repository. |
| services.frontend.image.tag | string | `"v3.9.0@sha256:5ba4828e4a8ae2ce27f548bc1d9664609509d97d2d3cc09af14c9014a227376b"` | Image tag, pinned by digest. |
| services.frontend.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.frontend.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. Mutually exclusive with `minAvailable`. |
| services.frontend.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.frontend.replicaCount | int | `2` | Replica count, ignored when autoscaling is enabled. |
| services.frontend.resourcesPreset | string | `"small"` | Resource t-shirt size. |
| services.frontend.service.annotations | object | `{}` | Extra Service annotations. |
| services.frontend.service.type | string | `"ClusterIP"` | Service type. The frontend is the one service it is safe to publish directly. |
| services.notifier | object | `{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-notifier","tag":"v3.9.0@sha256:b87d317c58b9d813ade9ad02d7e019752a7d47bdf45994ed321902ee72b9e31d"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | Distributes new-chapter notifications to users over email, Discord and generic webhooks. |
| services.notifier.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.notifier.enabled | bool | `true` | Deploy the notifier. |
| services.notifier.image.repository | string | `"timschoenle/tankovault-notifier"` | Image repository. |
| services.notifier.image.tag | string | `"v3.9.0@sha256:b87d317c58b9d813ade9ad02d7e019752a7d47bdf45994ed321902ee72b9e31d"` | Image tag, pinned by digest. |
| services.notifier.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.notifier.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.notifier.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.notifier.replicaCount | int | `1` | Replica count. One by default; upstream runs this tier as a singleton. |
| services.notifier.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.notifier.service.annotations | object | `{}` | Extra Service annotations. |
| services.notifier.service.type | string | `"ClusterIP"` | Service type. |
| services.render | object | `{"autoscaling":{"enabled":false,"maxReplicas":4,"minReplicas":1,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":false,"homeDir":"/home/nonroot","image":{"repository":"timschoenle/tankovault-render","tag":"v3.9.0@sha256:94d586f02feb4e32401df8e9ed065e76405570863245d2d7c7f48338b7594880"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resources":{"limits":{"memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}},"service":{"annotations":{},"type":"ClusterIP"},"shmSize":"1Gi"}` | Optional headless-browser tier for JavaScript-rendered pages; doubles as a solver backend. This is the one service not built on `scratch`: it is a Debian base driving a real Chromium, so it needs writable scratch space and a shared-memory volume. |
| services.render.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.render.autoscaling.maxReplicas | int | `4` | Maximum replicas. |
| services.render.autoscaling.minReplicas | int | `1` | Minimum replicas. |
| services.render.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.render.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.render.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.render.enabled | bool | `false` | Deploy the render tier. |
| services.render.homeDir | string | `"/home/nonroot"` | Home directory of the image's nonroot user, mounted as a writable emptyDir. Chromium writes its profile and crashpad database here; when it is not writable the failure surfaces as a misleading `--database is required` error. |
| services.render.image.repository | string | `"timschoenle/tankovault-render"` | Image repository. |
| services.render.image.tag | string | `"v3.9.0@sha256:94d586f02feb4e32401df8e9ed065e76405570863245d2d7c7f48338b7594880"` | Image tag, pinned by digest. |
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
| services.sync | object | `{"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-sync","tag":"v3.9.0@sha256:22ad04c979b1dd04e5604e85a466a0b943601cf8beaae3ec4f612d851530ed9e"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":1,"resourcesPreset":"medium","service":{"annotations":{},"type":"ClusterIP"}}` | Bidirectional AniList integration and metadata enrichment. |
| services.sync.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.sync.enabled | bool | `true` | Deploy the sync service. Requires the `anilist` credentials. |
| services.sync.image.repository | string | `"timschoenle/tankovault-sync"` | Image repository. |
| services.sync.image.tag | string | `"v3.9.0@sha256:22ad04c979b1dd04e5604e85a466a0b943601cf8beaae3ec4f612d851530ed9e"` | Image tag, pinned by digest. |
| services.sync.podDisruptionBudget.enabled | bool | `false` | Create a PodDisruptionBudget. |
| services.sync.podDisruptionBudget.maxUnavailable | string | `nil` | Maximum unavailable pods. |
| services.sync.podDisruptionBudget.minAvailable | int | `1` | Minimum available pods during voluntary disruption. |
| services.sync.replicaCount | int | `1` | Replica count. One by default: the reconcile loop is interval-driven and has no leader election, so extra replicas duplicate outbound AniList calls. |
| services.sync.resourcesPreset | string | `"medium"` | Resource t-shirt size. |
| services.sync.service.annotations | object | `{}` | Extra Service annotations. |
| services.sync.service.type | string | `"ClusterIP"` | Service type. Publishing this service exposes a privileged contract. |
| services.worker | object | `{"autoscaling":{"enabled":false,"maxReplicas":10,"minReplicas":2,"targetCPUUtilizationPercentage":80,"targetMemoryUtilizationPercentage":null},"config":{},"enabled":true,"image":{"repository":"timschoenle/tankovault-worker","tag":"v3.9.0@sha256:d19948768b24e6da513906995d0c729166cc983a2bfdddb53c02190d209e5105"},"podDisruptionBudget":{"enabled":false,"maxUnavailable":null,"minAvailable":1},"replicaCount":2,"resourcesPreset":"large","service":{"annotations":{},"type":"ClusterIP"}}` | Fetches and parses provider data through the adapters and upserts chapter and metadata changes. Scales horizontally for free: replicas join one NATS JetStream consumer group. |
| services.worker.autoscaling.enabled | bool | `false` | Enable a HorizontalPodAutoscaler. |
| services.worker.autoscaling.maxReplicas | int | `10` | Maximum replicas. |
| services.worker.autoscaling.minReplicas | int | `2` | Minimum replicas. |
| services.worker.autoscaling.targetCPUUtilizationPercentage | int | `80` | Target average CPU utilisation, percent. |
| services.worker.autoscaling.targetMemoryUtilizationPercentage | string | `nil` | Target average memory utilisation, percent. |
| services.worker.config | object | `{}` | Service-specific configuration, merged over the global `config` tree. |
| services.worker.enabled | bool | `true` | Deploy the worker. |
| services.worker.image.repository | string | `"timschoenle/tankovault-worker"` | Image repository. |
| services.worker.image.tag | string | `"v3.9.0@sha256:d19948768b24e6da513906995d0c729166cc983a2bfdddb53c02190d209e5105"` | Image tag, pinned by digest. |
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
