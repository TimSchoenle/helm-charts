# Upgrading the tankovault chart

Migration notes, newest first. Only the versions listed here need anything beyond
`helm upgrade`; everything in between is an image or dependency bump.

Two of them require a manual step that nothing will remind you about:

| Version | Applies to | Step |
|---|---|---|
| [3.1.0](#310) | existing releases with `postgresql.enabled=true` | `REINDEX DATABASE` once, after the upgrade |
| [3.1.0](#310) | existing releases with `externalDatabase.*` | install pgvector **before** upgrading |
| [3.0.3](#303) | existing releases with a bundled datastore | delete the StatefulSet with `--cascade=orphan` once |

The values contract is enforced by `values.schema.json`, so a key a new major removed or
renamed fails the render with the offending path named, rather than being silently ignored.

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
