# Upgrading the tankovault chart

Migration notes, newest first. Only the versions listed here need anything beyond
`helm upgrade`; everything in between is an image or dependency bump.

Some of them require a manual step that nothing will remind you about:

| Version | Applies to | Step |
|---|---|---|
| [4.0.0](#400) | **every release** | replace `internal.token`; the services refuse to boot on it |
| [4.0.0](#400) | releases using `existingSecret` | remove `internal__token`, add the per-caller keys |
| [3.2.0](#320) | releases that set `metadata.priority` under `services.sync.config` | move it to the top-level `config` |
| [3.2.0](#320) | releases using `existingSecret` | add `auth__mfa_encryption_key` to it |
| [3.1.0](#310) | existing releases with `postgresql.enabled=true` | `REINDEX DATABASE` once, after the upgrade |
| [3.1.0](#310) | existing releases with `externalDatabase.*` | install pgvector **before** upgrading |
| [3.0.3](#303) | existing releases with a bundled datastore | delete the StatefulSet with `--cascade=orphan` once |

The values contract is enforced by `values.schema.json`, so a key a new major removed or
renamed fails the render with the offending path named, rather than being silently ignored.

## 4.0.0

**The shared internal token is gone, and it does not fail gracefully.**

`TANKOVAULT_INTERNAL__TOKEN` — one value, byte-identical on every service — is **refused at boot**
by app 4.0.0, in every profile, with an error naming its replacements. There is no dual-accept
window. A release upgraded to this app version while still carrying the shared token does not
weaken, degrade or fall back: every service that receives it stops.

That single value used to open every privileged route on every service, so any one compromised
pod yielded the credential for all of them. Each caller now has its own, and each callee carries a
compiled-in table of which callers may reach which routes. Exactly two services make privileged
calls:

| Service | Calls | Called by |
|---|---|---|
| `api` | control-plane, sync, worker | — (public edge) |
| `worker` | challenge-solver, render | api |
| `control-plane`, `sync` | — | api |
| `challenge-solver`, `render` | — | worker |
| `notifier`, `frontend` | — | — |

`worker` is the only service that is both.

### What to change

The chart fails the render if `internal.token` is set, with the migration path in the message —
so a values file carrying it cannot reach a cluster. Pick a mode:

```yaml
# The default. Needs cert-manager, and a CA issuer whose root every service can verify against.
internal:
  identity: mtls
  tls:
    issuerRef:
      name: internal-ca
      kind: ClusterIssuer
    trustBundle:
      name: tankovault-ca    # the ConfigMap your trust-manager Bundle writes into this namespace
```

```yaml
# No CA required. The chart generates one token per caller and remembers both across upgrades.
internal:
  identity: token
```

Nothing else moves. Both modes authorise identically — only identification differs — so no route,
no permission and no `config` key changes with the mode.

> [!WARNING]
> **`existingSecret` releases get no render-time warning.** The chart never reads that Secret's
> contents, so it cannot see a stale `internal__token` in it and the break surfaces as every pod
> failing to boot at once. Remove that key. Under `identity: token` add `internal__tokens__api`
> and `internal__tokens__worker` in its place — those two are keyed by caller rather than by
> configuration path, because one token is read as `internal.caller.token` by the caller and as
> `internal.peers.<caller>.token` by each of its callees, and the projection maps each key onto
> the path the reading pod expects. Under `mtls` no internal credential is needed at all.

### Under `mtls`, the upstream URLs become `https://`

The chart derives them, so there is nothing to set — but it is worth knowing why the render
changes. `control_plane_url`, `sync_url`, `worker_url` and `worker.challenge_solver_endpoint`
follow `internal.identity`, because upstream refuses to boot on a plaintext peer URL in that mode:
the connection would be accepted, offer no client certificate and encrypt nothing, while the
peer's own configuration still says it requires both.

The frontend's `api_upstream` stays `http://`. That hop is a reverse-proxied public request rather
than an internal call, and the frontend holds no certificate in either mode.

The probes stay plaintext too. `/health` and `/ready` are mounted outside the authenticated stack
and stay on a plain listener under `mtls`, because a kubelet probe presents no client certificate.

### Prerequisites `mtls` adds

- **cert-manager**, and an issuer named by `internal.tls.issuerRef`. The chart refuses to render
  without the `cert-manager.io/v1` API rather than producing Certificates the API server rejects.
- **A CA in this namespace as a ConfigMap**, which is what trust-manager produces.
  `internal.tls.trustBundle.create` renders the `Bundle` if your cluster has no cluster-wide one;
  it is off by default because a Bundle is cluster-scoped and two releases would fight over the
  name.
- **A TLS-enabled NATS.** Under `mtls` each service presents the same client certificate to the
  broker and requires TLS on that connection. The bundled `nats` serves plaintext only, so
  `nats.enabled=true` with `identity: mtls` is refused at render time. Use `externalNats.url`, or
  stay on `identity: token` for the bundled evaluation stack.

`identity: token` needs none of these, which is why it exists.

### NetworkPolicies are tightened to the same graph

Two ingress edges are removed, because nothing ever used them: `worker` no longer admits
`control-plane`, and `render` no longer admits `challenge-solver`. Neither had a matching egress
rule on the other side, so no traffic is affected — and `render` and `challenge-solver` both fetch
caller-supplied URLs, which makes "exactly one caller" worth being exact about. The
frontend-to-`api` edge stays: it is the public request path, not an internal call.

### Per-service NATS accounts

`externalNats.perServiceSecret` names a Secret holding one NATS URL per service slug, projected
into each pod as `nats__url` in place of the derived one. This is how a broker is told that
`notifier` may not publish scan tasks — the accounts themselves are NATS server configuration, and
the services can only present what you give them here.

## 3.5.0

**"Why is this scan slow" becomes a metric.** Nothing to do, and no values changed: the chart's
rules and the scan-pipeline dashboard grow a section that reads three metrics app 3.7.0 already
emits — `scan_stage_duration_seconds`, `scan_task_pace_wait_seconds` and
`provider_pace_wait_seconds`. A release already on appVersion 3.7.0 starts populating the new
panels on the next scrape; one on anything older sees them empty, exactly as the backlog row is
empty without the NATS exporter.

Five recording rules and two alerts come with it. Both alerts are `severity: info`, because
neither is an outage — they are the two halves of a diagnosis that `scan_task_duration_seconds`
alone could not make:

| Alert | Says | Remedy |
|---|---|---|
| `TankoVaultScanPaceBound` | over 80% of a provider's task time is spent waiting for permission to send | raise `politeness.rps` or lower `crawl_delay_ms` for that provider, or accept the duration — no code change will help |
| `TankoVaultScanIngestBound` | over half of a provider's stage time is `series_ingest`, and the crawl budget is not the reason | the database, not the provider; a newly added provider's first full scan is the benign case |

If you route `severity: info` to a channel anyone reads, expect `TankoVaultScanPaceBound` to fire
on any provider you have deliberately configured to be crawled slowly. That is the alert being
correct rather than noisy, but it is a standing condition, not an event — silence it per provider
rather than raising the threshold, which would only move the line.

## 3.4.0

**Gateway API exposure and a Cilium policy engine.** Nothing to do: both are opt-in, both default
off, and a release that changes neither renders byte-identically to 3.3.6 apart from the chart
version label.

### `gateway.*`

`ingress` and `gateway` are independent switches, so there is no migration window to schedule —
turn `gateway` on while the Ingress is still serving, cut DNS over, then turn `ingress` off. What
this chart owns is the `HTTPRoute`; the `Gateway` belongs to whoever runs the cluster, and
`gateway.parentRefs` names it.

| Ingress | Gateway API |
|---|---|
| `ingress.className` | `gateway.parentRefs` — the Gateway, not the class |
| `ingress.host` | `gateway.host` |
| `ingress.path` / `ingress.pathType` | `gateway.path`, or `gateway.filters` for anything richer |
| `ingress.tls.enabled` | `gateway.tls.enabled` |
| `ingress.tls.secretName` | the Gateway listener's `certificateRefs` — theirs, unless `gateway.create` |
| `ingress.url` | `gateway.url` (takes precedence over `ingress.url`) |
| `ingress.api.*` | `gateway.api.*` |
| an `ssl-redirect` annotation | `gateway.httpsRedirect.enabled` |
| a `proxy-read-timeout` annotation | `gateway.timeouts` |

> [!IMPORTANT]
> `gateway.tls.enabled` is what the derived external URL takes its scheme from — the same job
> `ingress.tls.enabled` does — and it applies even when the Gateway is somebody else's, because it
> states that the hostname is served over HTTPS, not that this chart terminates it. Leaving it off
> while the application's `auth.cookie_secure` is true yields an `http://` origin, a session
> cookie that is never sent back, and a login that lands straight back on the sign-in page. The
> chart now refuses that combination at render time.

The NetworkPolicies follow on their own: the frontend admits the Gateway's data plane when
`gateway.enabled` is set, the API when `gateway.api.enabled` is, and the peer is derived from
`gateway.parentRefs`.

### `networkPolicy.engine`

`kubernetes` (the default, and what every existing release already renders), `cilium`, or `both`
for a CNI migration. The twelve per-service policies are now rendered from one derived topology
that both dialects read, so they cannot describe different graphs — and the portable output is
unchanged from 3.3.6.

The reason to switch is the internet rules. `worker`, `sync`, `notifier`, `render` and the bundled
TRAWL need outbound access, and `networking.k8s.io/v1` can only say that as `0.0.0.0/0` minus
RFC1918 and the metadata endpoint. `networkPolicy.cilium.egress.toFQDNs` replaces it with the
hosts those tiers actually talk to. Note it *replaces* rather than adds: emitting both would leave
the broad rule in place.

## 3.3.0

**`appVersion` moves to 3.3.0**, and with it the nine image digests.

Nothing to do. No value is renamed or removed, no default changes, and a release that is not
behind Cloudflare renders byte-identically to 3.2.0 on the new images. Upstream 3.3.0 also adds
catalogue bulk-delete and purge to the admin console, and accepts a security key at the
confirm-it-is-you prompt; neither needs anything from this chart.

### `cloudflare.scriptNonce` and `cloudflare.turnstile`

The frontend hashes every inline script in the SPA shell at startup and serves the result as its
`script-src`. Cloudflare's bot products — Bot Fight Mode, JavaScript Detections, the challenge
platform — inject their own inline `<script>` *at the edge*, after that hash was taken, so the
policy refuses it and the detection silently never runs. Nothing upstream can fix that from
inside the process, so TankoVault added two opt-ins and this chart exposes them as a top-level
`cloudflare` block:

```yaml
cloudflare:
  scriptNonce: true
  turnstile: false
```

They render into the frontend's own TOML as `[frontend.cloudflare]` and reach no other service —
the frontend is the only tier that assembles a Content-Security-Policy. Setting either while
`services.frontend.enabled=false` now fails the render rather than accepting a flag nothing
serves.

Both default off, which is the state every existing release is already in. Turn them on only if
you really are behind Cloudflare, and read
[Running behind Cloudflare](README.md#running-behind-cloudflare) first — in particular that
Rocket Loader must be off for the hostname (it breaks the SPA outright, with or without these
flags) and that the app shell must not sit behind an edge cache while `scriptNonce` is on.

## 3.2.0

**`appVersion` moves to 3.2.0**, and with it the nine image digests.

Chart versions 3.1.1 through 3.1.10 carried TankoVault from 1.3.0 to 3.1.0 — fifteen releases and
two majors — by repinning those digests and nothing else. That is correct for a chart whose
`config` is a free-form TOML tree: every key those releases added was already settable, and no
default changed under anyone. It also means none of it was written down, and three of the changes
alter what a running deployment does. **No value is renamed or removed here**, and the schema
migrations run themselves; what follows is the part no migration can tell you.

### Adult content is now gated, and it was gated by chart 3.1.9

TankoVault 3.0.0 excludes any series flagged adult from Discover, from search and from the
recommender unless the `catalogue.adult_content` feature flag is on *and* the reader has opted in
with an age attestation. The flag ships **off**, so a release that was serving such series stopped
when its images moved to `v3.0.0` — which chart 3.1.9 did, without saying so. If your Discover
counts fell around that upgrade, this is why.

Nothing needs undoing, and the chart still exposes no value for the flag on purpose. See
[Adult content is gated off](README.md#adult-content-is-gated-off-and-this-chart-does-not-open-it)
for how the classifier decides, and for `metadata.tags.adult_tags` if your providers use terms the
shipped set does not cover.

### `metadata.priority` is read by the worker now, not only by sync

Two writers put metadata on a series row — the worker's catalogue scan and sync's enrichment pass
— and as of TankoVault 1.3.3 both read `metadata.priority`. Before that only sync did, which made
the priority ineffective in practice: the scan runs far more often, so every enriched description
was overwritten by the next scrape and `content_type`/`status` reverted to the `unknown` the
adapters hardcode.

**If you set `metadata.priority` under `services.sync.config`, move it to the top-level `config`**
so the worker gets it too. Nothing fails if you do not — the worker falls back to the shipped
default of AniList before the adapters, which is also what the key usually says — but the override
you wrote will not be the one that wins. The section also gained `content_type`, `status` and
`release_year` lists, and a sibling `metadata.tags` guard over which scraped "genres" are allowed
to become tags at all; both are documented under
[Metadata authority and the tag guard](README.md#metadata-authority-and-the-tag-guard).

### Worker crawl concurrency is visible

TankoVault 3.1.0 added `worker.max_concurrent_providers`, defaulting to 4, and a
`scan_tasks_inflight` gauge to go with it.

- **One recording rule is added**, `namespace_job:scan_tasks_inflight:avg`, to the existing
  `tankovault-scan-recording` group. `metrics.prometheusRule` still produces 21 groups. No alert:
  neither a saturated worker nor an idle one is a fault, and the cap is a configuration value
  Prometheus cannot see, so any threshold would be a guess.
- **The scan-pipeline dashboard's *Active provider lanes* panel is renamed** to *Providers in
  flight, and lanes with work* and plots the new series beside the 30-minute lane count. No new
  ConfigMap key and no new `GrafanaDashboard`.
- **Raise `database.max_connections` if you raise the cap.** It is 16 per replica and the pool is
  not sized from the concurrency; a scan that cannot acquire a connection times out and reads like
  a database fault. See
  [Crawl concurrency and the connection pool](README.md#crawl-concurrency-and-the-connection-pool).

### Two-factor authentication arrives with app 3.2.0

TankoVault gained MFA — authenticator apps, security keys, recovery codes and step-up prompts —
in [288ace0](https://github.com/TimSchoenle/TankoVault/commit/288ace009fef068d7188d263406d0c95a52cfceb),
released as app 3.2.0 and pinned here. **This is the change in this version most likely to
surprise an operator**, because it can lock an administrator out of the console on first sign-in
rather than at upgrade time.

- **`auth.mfaEncryptionKey` is new**, and generated when `services.api` is enabled, so a default
  install needs nothing. It is projected into `api` only, and **must never change once anyone has
  enrolled** — a TOTP secret is symmetric, so rotating the key locks every enrolled account out of
  its second factor. Back it up alongside `auth.passwordPepper`.
- **If you use `existingSecret`, add `auth__mfa_encryption_key` to it** — base64 of exactly 32
  bytes, `openssl rand -base64 32`. The chart generates nothing when `existingSecret` is set, and a
  missing key disables authenticator-app enrolment rather than failing, which is the harder failure
  to notice.
- **A second factor is required for administrators by the authorization path, not a feature flag.**
  Any account holding an administrative permission — which the seeded administrator does, all of
  them — must enrol before it can administer anything. There is nothing to switch off, and this is
  why the chart generates the key rather than leaving it to be discovered: without it, a hardware
  security key is that account's only route into the console.
- **`accounts.mfa_required` ships off** and is the flag that extends the requirement to every
  account. Turning it on confines everyone without a second factor to the enrolment surface.
- `auth.totp_issuer`, `auth.step_up_ttl_minutes` and `auth.mfa_challenge_ttl_minutes` are ordinary
  `config` keys with working defaults; see
  [Two-factor authentication](README.md#two-factor-authentication).

Migration `0043` renames `user_passkeys` to `user_webauthn_credentials` and gives each row a
`purpose`, so a passkey and a security key cannot be the same authenticator — existing passkeys are
backfilled as first-factor credentials and keep working. It runs with the rest of this upgrade;
nothing is required of you.

### The rest

- **The installer's first account now holds `system.superuser`**, a grant that answers every
  permission check including capabilities added by later releases. `bootstrap.seedAdmin` claims it
  only while that account is the only one in the database; migration `0042` grants it retroactively
  to the earliest account holding `users.permissions`, so an existing deployment's owner already
  has it and a deployment with no such holder gets none. It cannot be granted from the permission
  editor, and a permission edit that omits it leaves it alone rather than revoking it. The seed
  Job's log says which of the two happened.
- **Notification preferences gate delivery** as of TankoVault 2.0.0, and readers who never touched
  them get the shipped defaults — which do *not* notify on series they marked `dropped` or
  `completed`. Every other kind and status stays on. Chapters of one series also coalesce into a
  single unread row instead of one row each; existing history is left ungrouped rather than
  rewritten.
- **Four automatic-merge guards are settable** under `config.matching`, all on by default, so the
  duplicate sweep queues a suspicious pair for review instead of merging it. This is only a
  documentation change here — the guards have been on since TankoVault 1.4.x. See
  [Automatic-merge guards](README.md#automatic-merge-guards).

## 3.1.0

This version moves to TankoVault 1.3.0, which adds the recommender. Two things need reading
before you upgrade an existing release, one for each kind of database.

**On an external database**, install pgvector first — see
[Recommendations need pgvector](README.md#recommendations-need-pgvector). Migration `0027` will
not run without it, and there is nothing a chart can do to detect that ahead of time.

### `postgresql.enabled=true`: REINDEX once, after the upgrade

**This is a required step for any existing release running the bundled database, and nothing
will tell you if you skip it.** Fresh installs are unaffected.

Getting pgvector meant moving the bundled image from Alpine to Debian, and those two sort text
differently under the same `en_US.utf8` locale name — musl compares by byte, glibc compares
linguistically:

| | `ORDER BY t` |
|---|---|
| `postgres:18-alpine` (musl) | `A, B, _z, a, a-b, ab, b` |
| `pgvector/pgvector:pg18` (glibc) | `a, A, a-b, ab, b, B, _z` |

Every btree index on a `text` or `varchar` column was built under the first ordering and is not
valid under the second. The consequence is silent, and it is wrong answers rather than errors:
an index scan can skip rows that are really there, and a unique index can stop rejecting
duplicates. Verified with `amcheck` against a data directory carried across the two images —
`bt_index_check` reports `item order invariant violated` immediately after the swap.

PostgreSQL issues **no warning**. It warns on a collation change only when the database records
a collation version to compare against, and a cluster initialised by the Alpine image records
none (`pg_database.datcollversion` is null).

The whole fix is one command, run once, after the new image is serving:

```shell
kubectl -n <namespace> exec -it <release>-tankovault-postgresql-0 -- \
  psql -U tankovault -d tankovault -c 'REINDEX DATABASE tankovault;'
```

`REINDEX DATABASE` takes locks on each index as it rebuilds it, so run it in a window where a
pause is acceptable, or use `REINDEX DATABASE CONCURRENTLY` to trade speed for staying online.
The chart prints this instruction in its upgrade notes too, and deliberately does not run it
for you: on a large catalogue it is a long operation with real locking behaviour, and that is
an operator's decision to schedule.

### The rest of 3.1.0

- **`appVersion` is 1.3.0** and all nine service images are repinned to their `v1.3.0` digests.
  The upgrade order is the one the chart already enforces — the migration runs ahead of the
  rollout — and the migration is the step that needs pgvector.
- **The bundled PostgreSQL image changed** from `postgres:18-alpine` to `pgvector/pgvector:pg18`,
  as above. If you pinned `postgresql.image.*` yourself, move it to an image that has pgvector.
- **On a capacity-constrained cluster, set `defaults.strategy.rollingUpdate.maxSurge: 0` for the
  upgrade.** Every chart-version bump rewrites the `helm.sh/chart` and `app.kubernetes.io/version`
  pod labels, so an upgrade rolls every workload — the bundled datastores included, which means
  the database pod is deleted and recreated. The default rollout asks for its replacement pods
  before retiring the old ones, and if the surge takes the CPU the database just released,
  nothing recovers: no service passes a readiness probe without the database, so no old pod is
  ever retired and the capacity never comes back. `maxSurge: 0` retires before it replaces. This
  is not new in 3.1.0 — it applies to any version bump with the bundled datastores on tight
  nodes — but it is worth knowing before an upgrade that also has a REINDEX after it.
- **Two alerts are added**, `TankoVaultRecsysBuildFailing` and `TankoVaultRecsysShelvesEmpty`, with
  seven recording rules behind them under `tankovault-recsys-recording`. `metrics.prometheusRule`
  therefore produces 21 groups where it produced 19.
- **The scan-pipeline dashboard gains a `Recommendations` row.** No new ConfigMap key and no new
  `GrafanaDashboard`; the existing `tankovault-scan-pipeline.json` grew five panels.
- **Nothing is switched on by this chart.** The recommender is gated on the
  `catalogue.recommendations` feature flag in the admin console, and its build cadence has
  working defaults — see [Build cadence and cost](README.md#build-cadence-and-cost). With the
  flag off nothing builds and nothing is served, so the new rules evaluate against no data and
  stay dark rather than firing.

## 3.0.3

**Read this before upgrading any release that runs a bundled datastore with persistence.** The
fix in this version is complete for new installs and requires one manual step for existing ones.

Until 3.0.3 the chart stamped the full label set — including `helm.sh/chart` and
`app.kubernetes.io/version` — onto the `volumeClaimTemplates` of the bundled NATS and
PostgreSQL StatefulSets. That block is part of the StatefulSet **spec**, which Kubernetes
refuses to update for any field other than `replicas`, `ordinals`, `template`,
`updateStrategy`, `revisionHistoryLimit`, `persistentVolumeClaimRetentionPolicy` and
`minReadySeconds`. Both of those labels move on every release, so any version bump of this
chart made the next in-place upgrade fail:

```
StatefulSet.apps "tankovault-nats" is invalid: spec: Forbidden: updates to statefulset spec
for fields other than 'replicas', 'ordinals', 'template', 'updateStrategy',
'revisionHistoryLimit', 'persistentVolumeClaimRetentionPolicy' and 'minReadySeconds' are
forbidden
```

The claim templates now carry only labels that are stable for the lifetime of a release
(`name`, `instance`, `managed-by`, `component`), so this cannot recur.

**It does not repair an existing install.** The StatefulSet already running still carries the
old labels, and the corrected render still differs from them — so the upgrade is rejected on
exactly the same field until the object is recreated once:

```shell
kubectl delete statefulset <release>-nats -n <namespace> --cascade=orphan
```

Repeat for `<release>-postgresql` if the bundled database is in use. If you are unsure of the
names:

```shell
kubectl get statefulset -n <namespace> -l app.kubernetes.io/instance=<release>
```

Then upgrade as usual. This is safe, and it is worth knowing exactly why rather than having to
reason it out under a failing sync:

- **The data survives.** The chart sets no `persistentVolumeClaimRetentionPolicy`, so it
  defaults to `Retain`/`Retain` and the PVCs carry no owner reference to the StatefulSet. The
  JetStream and PostgreSQL volumes are untouched however the StatefulSet is deleted.
- **The service stays up.** `--cascade=orphan` deletes only the StatefulSet object; the pod
  keeps running throughout.
- **No second pod appears.** The recreated StatefulSet's selector is `name`/`instance`/
  `component` only and is unchanged between versions, so it adopts the orphaned pod rather
  than starting a rival. It then rolls it once to pick up the new image — which the upgrade
  was going to do anyway.

Argo CD users: perform the delete manually, then sync. Do not add the StatefulSet to a
`Replace=true` sync policy — that would delete the pod along with it.

### `nameOverride` on the bundled datastores

Also fixed here, and unrelated to the above. The bundled datastores rendered their selector and
their pod template against two different contexts, so under `nameOverride` the two disagreed on
`app.kubernetes.io/name` and the API server rejected the workload outright with "`selector` does
not match template `labels`". `nameOverride` broke the bundled NATS, PostgreSQL, Valkey, TRAWL
and NATS exporter rather than renaming them. If you were affected you will not have a running
release to upgrade — install normally.

## 3.0.2

Three corrections to the rules 3.0.0 introduced, all found by evaluating them against synthetic
series rather than only checking that they parse.

- **`TankoVaultLatencyCritical` could never fire.** It compared p99 against `> 10`, and
  `histogram_quantile` cannot return a value above the highest finite bucket — a service whose
  every request takes a minute reports exactly `10`. The threshold is now `>= 10`.
- **`namespace_consumer:events_queue_depth:current` is renamed to
  `namespace_consumer_name:events_queue_depth:current`**, because it groups by `consumer_name` and
  a recorded series' level prefix is meant to be its label set. If you referenced the old name in a
  query of your own, update it; nothing inside the chart still does.
- **`TankoVaultWorkerTargetsAbsent`'s runbook** no longer interpolates `{{ $labels.namespace }}`.
  `absent()` builds its series from equality matchers only, so under
  `metrics.prometheusRule.scope=none` that label does not exist and the runbook rendered a
  `kubectl -n  ...` command with a hole in it.

## 3.0.0

The observability surface was rebuilt for Kubernetes. Nothing outside `metrics.*` changed, and a
release that does not enable metrics is unaffected.

**The alerting and recording rules are new.** The previous set was carried over from a
docker-compose stack and large parts of it could never fire in a cluster: readiness came from a
blackbox exporter this chart does not deploy, the replica count aggregated every scrape job in the
cluster, the JetStream queue-depth series had no producer, and the runbooks gave
`docker compose logs` commands.

- **Recorded series are renamed.** Every rule now aggregates by `namespace` as well as `job`, and
  the names say so: `job:http_requests:rate5m` is now `namespace_job:http_requests:rate5m`, and so
  on for all of them. Without namespace in the aggregation the recorded series lose the label and
  nothing downstream can be scoped. Saved Grafana queries and any downstream rules of your own
  need updating.
- **Alerts removed:** `TankoVaultReadinessProberDown` (no prober any more),
  `TankoVaultWorkerFleetIncomplete` (hardcoded compose's `replicas: 2`; use
  `KubeDeploymentReplicasMismatch`), `TankoVaultPrometheusRuleEvaluationFailing` (use
  `PrometheusRuleFailures`).
- **Alerts added:** `TankoVaultDependencyDown`, `TankoVaultVersionSkew`,
  `TankoVaultAuthFailureSurge`, `TankoVaultDatabasePoolSaturated`,
  `TankoVaultProviderFetchFailing`, `TankoVaultProviderThrottlingHeavily`,
  `TankoVaultScanTasksFailing`, `TankoVaultNoSchedulerLeader`, `TankoVaultSplitBrainScheduler`,
  `TankoVaultChaptersAllRejected`, `TankoVaultNotificationDeliveryFailing`,
  `TankoVaultNotificationEventsFailing`, `TankoVaultSseEventsUndeliverable`,
  `TankoVaultAniListRateLimited`.
- **`metrics.prometheusRule.scope` is new and defaults to `namespace`**, which rewrites every
  expression to match only this release's series. Set it to `none` to keep the previous
  cluster-wide behaviour.
- **The dashboards no longer pin a datasource UID.** The old one referenced
  `tankovault-prometheus`, a name provisioned by upstream's compose stack and absent from every
  cluster; both dashboards now expose a `Data source` picker and a `Namespace` picker. A second
  dashboard, `tankovault-scan-pipeline`, is added, so `metrics.dashboard.enabled` now produces two
  ConfigMap keys and, on the operator path, two `GrafanaDashboard` resources.
- **`metrics.natsExporter` is new**, off by default. The queue-depth alerts and the pipeline
  dashboard's backlog row need it.

## 2.0.0

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
