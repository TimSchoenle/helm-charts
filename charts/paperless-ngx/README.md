# paperless-ngx

![Version: 1.1.1](https://img.shields.io/badge/Version-1.1.1-informational?style=flat-square) ![AppVersion: 3.0.5](https://img.shields.io/badge/AppVersion-3.0.5-informational?style=flat-square)

This chart deploys paperless-ngx — a document management system that scans, indexes and archives your paper documents — hardened to the restricted Pod Security Standard, with per-directory persistence, scheduled document_exporter backups and document_importer restores, optional bundled Valkey, PostgreSQL, Gotenberg and Tika, Ingress and Gateway API publishing, Grafana dashboards and Prometheus alerting rules.

Four decisions shape every install, and all four are covered below: the archive lives on volumes
that must outlive the release ([Storage](#storage)), the application cannot process a single
document without a broker ([Datastores](#datastores)), Django rejects requests whose hostname it
was not told about ([Publishing](#publishing)), and office documents need two more services
([Office and e-mail documents](#office-and-e-mail-documents)).

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- A StorageClass, unless every directory is deliberately run on an `emptyDir`
- A Django secret key — the chart refuses to render without one, and there is no safe default
- The Prometheus Operator CRDs, if `metrics.prometheusRule` is enabled
- The Gateway API CRDs, if `gateway` is enabled
- Cilium 1.16+, if `networkPolicy.engine` is `cilium` or `both`

## Quick start

```shell
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update

helm install [RELEASE_NAME] timschoenle/paperless-ngx \
  --namespace [NAMESPACE] --create-namespace \
  --set paperless.secretKey="$(openssl rand -base64 48)" \
  --set paperless.admin.user=admin \
  --set paperless.admin.password="$(openssl rand -base64 24)" \
  --set paperless.admin.email=admin@example.com
```

That gives you a working archive: paperless-ngx on SQLite, a bundled Valkey as the broker, and
four claims. Nothing is published yet — reach it with `kubectl port-forward` until you have
decided how it should be exposed.

Upgrade with `helm upgrade [RELEASE_NAME] timschoenle/paperless-ngx -n [NAMESPACE]`.

> [!WARNING]
> `helm uninstall` deletes the PersistentVolumeClaims along with the release, and with them every
> document in the archive. Set
> `persistence.media.annotations."helm\.sh/resource-policy"=keep` **before** you need it.

## The secret key

`paperless.secretKey` signs session cookies, password reset links and share links. Upstream ships
a published constant as its fallback, so an instance running on that fallback will hand a forged
session to anybody who knows it. The chart therefore fails the render rather than starting:

```shell
helm install ... --set paperless.secretKey="$(openssl rand -base64 48)"
```

For anything beyond a first try, put it in a Secret instead and keep it out of `values.yaml` and
out of the Helm release object entirely:

```shell
kubectl create secret generic paperless-credentials \
  --namespace [NAMESPACE] \
  --from-literal=secret-key="$(openssl rand -base64 48)" \
  --from-literal=admin-password="$(openssl rand -base64 24)" \
  --from-literal=database-password="$(openssl rand -base64 24)"
```

```yaml
existingSecret: paperless-credentials
```

`existingSecret` replaces the chart's own Secret entirely. The key names it looks for are the
`existingSecretKey` field beside each credential, so a Secret you already have can keep its own
naming.

Changing the secret key later invalidates every session and every outstanding share link. It does
not touch stored documents.

## Storage

Four directories, four claims, because they have genuinely different lifetimes:

| Value | Mount | What it holds | Losing it |
| --- | --- | --- | --- |
| `persistence.media` | `/usr/src/paperless/media` | originals, archived PDF/As, thumbnails | loses the archive |
| `persistence.data` | `/usr/src/paperless/data` | search index, classification model, scheduler state, and the SQLite database | rebuildable, by hand |
| `persistence.consume` | `/usr/src/paperless/consume` | the drop box | loses what was queued |
| `persistence.export` | `/usr/src/paperless/export` | `document_exporter` output | scratch space |

`media` is the one to back up. `data` can be rebuilt from it with `document_index reindex` —
unless the database is SQLite, in which case it *is* the archive's metadata and the chart refuses
to put it on an `emptyDir`.

`export` is backed by an `emptyDir` unless you enable a claim for it. That is not only a
convenience: the directory is one of the image's declared volumes, so without a mount of its own
it is read-only, and an export fails at the moment somebody needs a backup. `backup.enabled`
requires a real claim here and the chart refuses to render without one — see
[Backup and restore](#backup-and-restore).

Claims default to `ReadWriteOnce`, which forces the Deployment to the `Recreate` update strategy —
a rolling update deadlocks, because the replacement pod cannot attach a volume the outgoing pod
still holds. Every upgrade is therefore a short outage.

### Feeding it documents

Upload through the web UI, or drop files into the consumption directory. A `ReadWriteMany` claim
shared with a scanner is the usual arrangement for the latter:

```yaml
persistence:
  consume:
    existingClaim: scanner-dropbox

paperless:
  consumer:
    # Network storage delivers no filesystem events, so the watcher has to poll for them.
    pollingInterval: 30
```

## Datastores

**The broker is not optional.** paperless-ngx hands every document to a task worker through
Redis; without one it starts, serves the web UI and consumes nothing — a failure with no error
message anywhere. The chart bundles a Valkey for exactly that reason and refuses to render if
neither a bundled nor an external broker is configured.

| | Bundled | External |
| --- | --- | --- |
| Broker | `valkey.enabled` (default **on**) | `redis.host` / `redis.url` |
| Database | `postgresql.enabled` | `database.host` |

Both bundled datastores are evaluation tier and documented as such: one replica, no failover, no
backups, no connection pooler. They exist so `helm install` produces a working stack on a bare
cluster. A production archive points at a managed instance or an operator-run cluster.

### SQLite or PostgreSQL

SQLite is the default and is upstream's supported configuration for a single user. It lives in the
data volume and needs no second workload. PostgreSQL is upstream's recommendation once several
people use the archive at once:

```yaml
database:
  engine: postgresql
  password: "" # or existingSecret

postgresql:
  enabled: true
  persistence:
    size: 8Gi
```

Switching an existing archive is an export and re-import (`document_exporter` then
`document_importer`), not a values change — the chart cannot migrate the data for you, and
pointing a populated release at an empty database shows an empty archive rather than an error.
That is what [Backup and restore](#backup-and-restore) automates.

### Driver options

`database.sslMode` and `database.poolSize` are rendered into a single `PAPERLESS_DB_OPTIONS`
string, which is upstream's consolidated replacement for the per-option environment variables it
deprecated (`PAPERLESS_DBSSLMODE`, `PAPERLESS_DB_POOLSIZE`, the SSL certificate paths, the
connection timeout) and removes in 3.2. Emitting the old ones still works and logs a deprecation
warning per variable at every start; the risk is the release that drops them, where a `sslmode`
nothing reads is a plaintext connection with nothing said about it.

The option *names* are engine-specific and the chart translates them: `sslmode` for PostgreSQL,
`ssl_mode` for MariaDB. Pooling is a PostgreSQL driver feature, so `database.poolSize` on another
engine is rejected rather than silently dropped.

Anything the chart does not model goes in `database.options` verbatim, including nested keys:

```yaml
database:
  engine: postgresql
  sslMode: verify-full
  options:
    sslrootcert: /certs/ca.pem     # mount it yourself with extraVolumes
    connect_timeout: 10
    pool.min_size: 2
```

An entry there wins over the modelled shorthand when the keys collide.

### A broker with a password

The bundled Valkey runs without one by default and is protected by the NetworkPolicy instead.
Setting `redis.password` switches it to `--requirepass`, passed through the environment rather
than as an argument so it stays out of `kubectl get pod -o yaml` and out of the node's process
table. The password is injected into the broker URL by the kubelet, which is why it never appears
in the ConfigMap either.

Percent-encode a password containing `@`, `/` or `:` — the value becomes part of a URL.

## Publishing

Django validates the `Host` header and the origin of every unsafe request against `PAPERLESS_URL`.
Get it wrong and the login page loads perfectly and then rejects the login with a CSRF error,
which is a genuinely confusing failure. The chart derives it from whatever publishes the
application, so in the common case there is nothing to set:

```yaml
ingress:
  enabled: true
  host: paperless.example.com
  className: nginx
  tls:
    enabled: true
  annotations:
    # Uploading a scan is one large POST. The controller's default limit is well below what a
    # document weighs, and the upload fails at the proxy with an error paperless never sees.
    nginx.ingress.kubernetes.io/proxy-body-size: 100m
```

Set `paperless.url` explicitly when something the chart cannot see is in front — a second proxy,
a CDN, a split-horizon DNS name.

### Gateway API

`gateway` is the same job under the newer API, where the cluster operator owns the `Gateway` and
this chart owns the `HTTPRoute`:

```yaml
gateway:
  enabled: true
  hostnames:
    - paperless.example.com
  parentRefs:
    - name: shared
      namespace: gateways
  timeouts:
    # Worth raising for the same reason as the body size limit above.
    request: 300s
```

`gateway.create` additionally renders a Gateway, for an install with no cluster-wide one to attach
to. Both switches are independent of `ingress`, so a cluster migrating between the two can run
both for a while.

### Under a sub-path

Serving at `https://apps.example.com/paperless` takes two settings, because Django prefixes the
URLs it generates with the script name but not the static ones:

```yaml
paperless:
  rootPath: /paperless

ingress:
  enabled: true
  host: apps.example.com
  path: /paperless
```

The chart sets `PAPERLESS_STATIC_URL` to match, and the three probe paths follow `rootPath` as
well, so they keep pointing at the application rather than at a 404.

## Office and e-mail documents

PDFs and images need nothing extra. `.docx`, `.odt`, `.xlsx` and `.eml` are rejected at
consumption unless Apache Tika (text extraction) and Gotenberg (rendering to PDF) are reachable:

```yaml
tika:
  enabled: true
  # Both bundled by default; set an endpoint instead to use servers you already run.
  server:
    enabled: true
  gotenberg:
    enabled: true
```

Gotenberg is run with JavaScript disabled and an allow list confined to the request's own
temporary directory. An e-mail is untrusted input, and a headless browser that executes it and
follows its references is a server-side request forgery primitive with a document library
attached: tracking pixels resolve, and so does anything on a private address.

Both images are large — roughly 2.5 GiB together, because Gotenberg carries a LibreOffice and a
Chromium. Point `tika.server.endpoint` and `tika.gotenberg.endpoint` at shared instances if
several applications need them.

## Security

Every workload in the release satisfies the restricted Pod Security Standard out of the box:

- runs as the unprivileged account its image was built for — `paperless` (1000), `postgres` (999),
  `valkey` (1000), `gotenberg` (1001), `tika` (35002)
- all Linux capabilities dropped, no privilege escalation,
  `seccompProfile: RuntimeDefault`
- no ServiceAccount token mounted

The one exception is the root filesystem, which stays writable for the server container. The image
supervises its four processes with s6-overlay, whose `preinit` refuses to start unless `/run` is
writable and either owned by the UID it runs as or world-writable — mode `1777`, which is how the
image itself ships it. Kubernetes cannot reproduce either condition: an `emptyDir` is always
created owned by uid 0, `fsGroup` moves only its group and caps the mode at `2775`, `emptyDir` has
no `defaultMode` to raise it, and the capabilities that would let the container fix it by hand are
exactly the ones the restricted preset drops. Mounting anything at `/run` therefore replaces a
directory s6-overlay accepts with one it rejects, and a read-only root filesystem leaves it nothing
else to write to. So `/run` is left as the image ships it, and
`securityContext.readOnlyRootFilesystem` is `false`; setting it back to `true` fails the render with
an explanation rather than producing a crash loop. Everything else in the baseline is unaffected —
the container still runs as a non-root user, drops all capabilities, forbids privilege escalation
and keeps `seccompProfile: RuntimeDefault`. The bundled datastores keep the full preset, read-only
root filesystem included.

OCR writes every intermediate file to `PAPERLESS_SCRATCH_DIR` under `/tmp`. That is an `emptyDir`
rather than the container's own writable layer so that `sizeLimit` can bound it: the space is
charged against the node's ephemeral storage either way, but only a volume can be capped, and a
single OCR run on a large document is what exhausts a node.

The image detects a non-root start and skips the UID remapping and the recursive `chown` it does
when started as root — so `USERMAP_UID` and `USERMAP_GID` have no effect here, and `fsGroup` is
what makes the volumes writable.

Credentials never reach the ConfigMap. Every one is placed in a Secret and consumed with
`secretKeyRef`; the broker password is interpolated into the URL by the kubelet, from a variable
that is itself a `secretKeyRef`.

> [!CAUTION]
> `paperless.remoteUser.enabled` trusts an HTTP header as proof of identity. Only enable it where
> a proxy strips that header from incoming requests **and** a NetworkPolicy stops anything else
> from reaching the pod directly — otherwise any pod in the cluster can set it and be any user it
> likes.

## Network policies

`networkPolicy.enabled` renders one default-deny policy pair per workload: the application reaches
the broker, the database and the two converters, and none of them reaches back. Every egress rule
carries a `to:` selector — a rule that lists only `ports:` is not a restriction, because the API
reads a missing `to` as "all destinations", including the cloud metadata endpoint at
169.254.169.254.

What crosses the cluster boundary is opt-in and off by default:

| Value | Opens |
| --- | --- |
| `networkPolicy.egress.https` | TCP 443, for webhooks, mail fetching and remote OCR |
| `networkPolicy.egress.smtp` | TCP 587 and 465, for an external mail relay |
| `networkPolicy.egress.external` | the datastores, when they are not bundled |

`networkPolicy.engine` picks the dialect the same rules are written in — `kubernetes` (default),
`cilium`, or `both` for a CNI migration. The graph is derived once and rendered twice, so the two
cannot describe different topologies.

The Cilium dialect can state what the portable API cannot: a named egress destination instead of
"the entire public internet on port 587".

```yaml
networkPolicy:
  enabled: true
  engine: cilium
  egress:
    smtp: true
  cilium:
    toFQDNs:
      - matchName: smtp.example.com
    dnsMatchPatterns:
      - matchPattern: "*.example.com"
```

`dnsMatchPatterns` is what makes that enforceable: an FQDN rule matches the addresses Cilium's DNS
proxy saw returned for the name, so the DNS rule has to observe the lookup. Naming destinations
*replaces* the CIDR rule rather than adding to it — emitting both would leave the broad rule in
place and make the narrow one decorative.

## Monitoring

**paperless-ngx publishes no Prometheus metrics.** There is no queue depth to alert on, no
consumption failure counter and no OCR duration histogram, and this chart does not pretend
otherwise: there is no exporter and no ServiceMonitor. What it ships instead is built on series a
cluster already has — kube-state-metrics for workload health, the kubelet for volume usage.

| Toggle | What it creates |
| --- | --- |
| `metrics.prometheusRule.enabled` | a `PrometheusRule` with eleven alerts and six recording rules |
| `metrics.dashboard.enabled` | a labelled ConfigMap the Grafana sidecar loads |
| `metrics.dashboard.grafanaOperator.enabled` | one `GrafanaDashboard` per file, for grafana-operator v5 |

```yaml
metrics:
  prometheusRule:
    enabled: true
    scope: namespace
    labels:
      release: kube-prometheus-stack
  dashboard:
    enabled: true
```

The `labels` are not decoration: a Prometheus Operator selects rules by label, so without the one
your instance selects on the object is created and never loaded.

The alerts cover availability, absence, crash loops, restart rate, out-of-memory kills, volume
fill at two thresholds, the bundled datastores, and — when `backup.enabled` is set — a scheduled
export that failed, went stale or was suspended (see
[Knowing it still works](#knowing-it-still-works)). Each is evaluated against synthetic series
with `promtool` in CI, in both the committed form and the namespace-scoped form a cluster actually
receives.

Two things are worth knowing about how they select objects. Every selector matches this release
by **name or by label**, joined with `or`, because neither is sufficient alone: the objects carry
the chart name unless `nameOverride` or `fullnameOverride` renames them, and the
`app.kubernetes.io/name` join survives a rename but matches nothing at all on a stock
kube-prometheus-stack — kube-state-metrics stopped exporting object labels by default in v2. A
default install is therefore matched whatever the metrics stack is configured to export, and a
renamed one needs `--metric-labels-allowlist` to include that label; the install notes say so when
a rename is in play. And `metrics.prometheusRule.scope: namespace`
rewrites every selector to this release's namespace, because a `PrometheusRule` is not confined to
the namespace it lives in: left unscoped, two installs page each other for outages neither of them
had.

## Recipes

### A household archive

```yaml
paperless:
  secretKey: "" # from `openssl rand -base64 48`
  timeZone: Europe/Berlin
  admin:
    user: admin
    email: admin@example.com
    password: ""
  ocr:
    language: deu+eng

persistence:
  media:
    size: 100Gi
    annotations:
      helm.sh/resource-policy: keep

ingress:
  enabled: true
  host: paperless.example.com
  className: nginx
  tls:
    enabled: true
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt
    nginx.ingress.kubernetes.io/proxy-body-size: 100m
```

`ocr.language` only accepts languages already installed in the image. Upstream installs extra
Tesseract packs at container start when `PAPERLESS_OCR_LANGUAGES` is set, which needs a writable
root filesystem, root privileges and internet access from the pod — none of which this chart's
security baseline provides. Build a derived image instead.

### A shared archive on external datastores

```yaml
existingSecret: paperless-credentials

paperless:
  tasks:
    workers: 2
    webserverWorkers: 2

database:
  engine: postgresql
  host: postgres.data.svc.cluster.local
  sslMode: require

redis:
  host: valkey.data.svc.cluster.local

valkey:
  enabled: false

resources:
  limits:
    memory: 4Gi
  requests:
    cpu: 1
    memory: 1Gi

networkPolicy:
  enabled: true
  egress:
    external:
      enabled: true
      namespaceSelector:
        kubernetes.io/metadata.name: data
```

Raise the memory limit alongside `tasks.workers`: each worker processes a whole document, and a
large colour scan is hundreds of megabytes of rasterised pages. An out-of-memory kill during
consumption leaves the file in the consumption directory, where the next scan retries it — and
reproduces the kill.

### Settings the chart does not model

Anything in upstream's configuration reference can be passed through verbatim. Values are rendered
through the template engine, so release-scoped values work:

```yaml
paperless:
  extraConfig:
    PAPERLESS_APP_TITLE: "{{ .Release.Name }} archive"
    PAPERLESS_ENABLE_NLTK: "false"
    PAPERLESS_CONSUMER_IGNORE_PATTERNS: '[".DS_Store", "desktop.ini"]'
```

Never put credentials there — `extraConfig` lands in the ConfigMap, not the Secret. For a setting
whose value is sensitive, use `extraEnv` with a `secretKeyRef`, or mount the Secret and point the
image's `PAPERLESS_<NAME>_FILE` indirection at the file.

## Backup and restore

Two different things live under this heading and neither replaces the other.

A **volume snapshot** — Velero, a CSI `VolumeSnapshot`, restic against the claims — is a
crash-consistent copy of bytes. It restores quickly and it restores onto the same database engine
at a compatible application version. Nothing here competes with it.

An **export**, taken with upstream's `document_exporter`, is a logical backup: the documents plus a
`manifest.json` holding everything the database knows about them. It is slower and larger, and it
is the only form that restores into a *fresh* instance — which is what you need to move an archive
to another cluster, to recover from a corrupt database rather than a lost volume, or to make the
SQLite-to-PostgreSQL switch this chart's notes tell you is an export and re-import.

The chart automates the second. Run both.

### Scheduled exports

```yaml
persistence:
  export:
    enabled: true            # required; without a claim the export dies with the pod
    size: 50Gi
    annotations:
      helm.sh/resource-policy: keep

backup:
  enabled: true
  schedule: "0 3 * * *"
  timeZone: Europe/Berlin
  passphrase: <openssl rand -base64 32>
```

That is a nightly incremental export into the export claim: one continuously-updated tree, with
only the documents that changed since last night rewritten. Cheap, and the right default — but it
is a mirror, not a history, so a document deleted by mistake is gone from the backup on the next
run.

For a history, take dated archives instead:

```yaml
backup:
  mode: zip
  options:
    delete: false            # required in zip mode; see below
  zip:
    nameTemplate: paperless-export-%Y-%m-%dT%H%M%SZ
    retentionCount: 7
  scratch:
    existingClaim: paperless-backup-scratch   # for an archive larger than the node's disk
```

Two things about `zip` mode are worth knowing before you turn it on. The exporter assembles the
**entire** export tree in the scratch directory and compresses it afterwards, so the pod needs as
much free scratch space as the whole archive occupies — an `emptyDir` is charged against the
node's ephemeral storage and a pod that exceeds it is evicted mid-export, which is why
`backup.scratch` exists. And `--delete` means something different here: rather than pruning stale
documents it empties the target directory before moving the new archive in, which would destroy
every archive `retentionCount` is keeping. The chart handles retention itself and refuses to
render the combination.

### Getting it off the cluster

An export sitting on a PersistentVolume in the same cluster as the archive it backs up survives a
mistake in the web UI. It does not survive losing the cluster.

`backup.upload` takes an arbitrary container and runs it after the export. The chart takes no
dependency on any particular tool: the exporter becomes an `initContainer`, so the uploader starts
only once the export has succeeded, and a failed export never reaches it at all.

```yaml
backup:
  upload:
    enabled: true
    mountPath: /export
    container:
      name: upload
      image: rclone/rclone:1.71
      command: ["rclone", "sync", "/export", "remote:paperless-backups"]
      envFrom:
        - secretRef:
            name: rclone-config      # your credentials, your Secret
```

### How the export pod reaches the volumes

`media` and `data` default to `ReadWriteOnce`. Such a volume can be mounted by several pods that
share a node and cannot be attached to two nodes at all, so `backup.coScheduleWithApp` (on by
default) pins the backup pod to the application pod's node with a required `podAffinity`.

Without it, on RWO storage, the Job either lands correctly by luck or sits `Pending` until its
deadline — which presents as a backup that has silently not run for weeks. The cost is that a
backup cannot start while the application pod is down, which is the correct trade: it would have
nothing consistent to read anyway. On `ReadWriteMany` storage, turn it off.

The export takes the application's own media lock while it runs, so it does not race the consumer
for files. It is not a transactional snapshot of the database, though, which is why the default
schedule is a quiet hour.

### Restoring

The import is **not** a Job. `document_importer` needs the media and data directories to itself
and has to finish before anything else touches them — and the consumer, the scheduler and the
migrations all start the moment the application container does. So it runs as an `initContainer`
on the application pod: strictly before them, with the volumes already attached, working under
`ReadWriteOnce`, and failing the pod rather than bringing up a webserver that serves half an
archive.

```yaml
image:
  tag: 3.0.5                 # the version that produced the export

restore:
  enabled: true
  source:
    existingClaim: paperless-export-from-the-old-cluster
    subPath: paperless-export-2026-01-01T030000Z.zip   # or a directory holding manifest.json
  passphrase: <the backup.passphrase of the source instance>
```

The source can be one of three things, and exactly one:

| `restore.source` | Where the export is |
| --- | --- |
| `existingClaim` (+ `subPath`) | a PersistentVolumeClaim, mounted read-only |
| `useExportVolume` | this release's own export volume, for a rebuild in place |
| `url` (+ `checksum`) | an `http(s)://` zip, downloaded before the import |

#### From a URL

The common case is an archive that is already off-cluster — object storage, another cluster's
backup bucket, a release asset — where staging it into a PersistentVolume by hand is the only step
in the way:

```yaml
persistence:
  export:
    enabled: true              # required: the download lands here and needs room for the archive
    size: 50Gi

restore:
  enabled: true
  source:
    url: https://backups.example.com/paperless-2026-01-01T030000Z.zip?X-Amz-Signature=…
    checksum: 4f0e0b57b0a06a5e9f4a4fa0a24d0b0d3a2e6f2c1b8d7a9c0e1f2a3b4c5d6e7f
```

The URL is stored in the **Secret**, not the ConfigMap. The URLs that make this feature worth
having are presigned ones, where the signature in the query string is the whole of the
authorisation — so it is treated like the passphrases beside it, and `restore.source.url` is
ignored in favour of the `restore-url` key when you supply your own `existingSecret`.

`checksum` is optional and you should set it. Without it a truncated transfer imports as far as it
can and stops, which looks like a partial archive rather than a failed download; with it the pod
fails outright. It is verified on every start, not only after a fresh download, so a file left
behind by an earlier attempt gets the same scrutiny.

The download itself is written under a temporary name and moved into place only once `curl` has
succeeded — with `--fail`, so an HTTP error is an error rather than a 403 body saved as a zip — and
a retry after an interrupted transfer starts over instead of importing a truncated file.

Note that the import container's egress is the application's. With a NetworkPolicy in force, an
internet-hosted archive needs `networkPolicy.egress.https`; the install notes say so when the
combination is missing.

#### Constraints, in the order they bite

1. **The target must be empty.** The importer refuses to run against a database that already holds
   users or documents. This belongs on a fresh release; to restore over an existing instance,
   delete the media and data volumes first.
2. **The version must match.** An export carries an exact image of the database and the schema
   changes between releases. `document_importer` *warns* about a mismatch and then continues,
   which is the failure that costs an archive rather than reporting one. Pin `image.tag` to the
   source instance's version, import, then upgrade.
3. **API tokens are not exported.** Every client needs a new one afterwards.
4. **A zip import needs scratch space.** The archive is extracted in full to
   `/tmp` before it is read, so `persistence.scratchSizeLimit` (2 GiB by default) has to exceed
   the *uncompressed* size or the pod is evicted part-way through. A directory export has no such
   requirement.

The import runs once, guarded by a marker file on the data volume, and is a no-op on every start
after that — so `restore.enabled` can be left in your values instead of needing a follow-up
upgrade to remove it. Deleting the marker re-arms it.

### Knowing it still works

A backup nobody is alerted on is a backup nobody has. `metrics.prometheusRule.enabled` ships three
rules for this:

| Alert | Fires when |
| --- | --- |
| `PaperlessNgxBackupFailed` | a run exhausted its retries |
| `PaperlessNgxBackupStale` | no successful run in over two days |
| `PaperlessNgxBackupSuspended` | the schedule has been paused for six hours |

`PaperlessNgxBackupStale` is the one that matters. A failed Job is loud; a schedule that quietly
stopped firing leaves no Job, no failed pod and no error anywhere, and the usual way that ends is
someone discovering it during a restore. Its threshold is absolute rather than derived from
`backup.schedule` — a PromQL rule cannot read a cron expression — so it suits the daily default
and anything more frequent, and needs overriding on a weekly schedule.

Do not wait for the first scheduled run to find out whether any of this works:

```shell
kubectl create job -n [NAMESPACE] --from=cronjob/[RELEASE_NAME]-paperless-ngx-backup backup-manual-1
kubectl wait -n [NAMESPACE] --for=condition=complete job/backup-manual-1 --timeout=1h
```

And then, at least once, restore it into a scratch namespace. An export that has never been
imported is a hypothesis.

## Operating it

```shell
# create a superuser by hand
kubectl exec -n [NAMESPACE] deployment/[RELEASE_NAME]-paperless-ngx -- createsuperuser

# export the whole archive by hand, then copy it out
kubectl exec -n [NAMESPACE] deployment/[RELEASE_NAME]-paperless-ngx -- document_exporter ../export
kubectl cp [NAMESPACE]/[POD]:/usr/src/paperless/export ./paperless-export

# run tonight's scheduled export now
kubectl create job -n [NAMESPACE] --from=cronjob/[RELEASE_NAME]-paperless-ngx-backup backup-manual-1

# rebuild the search index after restoring the data volume, or after an import
kubectl exec -n [NAMESPACE] deployment/[RELEASE_NAME]-paperless-ngx -- document_index reindex
```

Upgrades to paperless-ngx itself run their database migrations on start, so a rollback to an
earlier `image.tag` after a major upgrade is not supported by the application. Export first.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` | Explicit affinity rules. Wins over `podAntiAffinity`. |
| automountServiceAccountToken | bool | `false` | Mount the ServiceAccount API token into the pod. Set on the pod itself, which is what actually keeps the token out of the container: the ServiceAccount-level setting is ignored as soon as a pod names a different account. |
| backup | object | `{"activeDeadlineSeconds":21600,"affinity":{},"backoffLimit":1,"coScheduleWithApp":true,"concurrencyPolicy":"Forbid","enabled":false,"existingSecretKey":"export-passphrase","failedJobsHistoryLimit":3,"mode":"incremental","nodeSelector":{},"options":{"batchSize":500,"compareChecksums":false,"compareJson":false,"delete":true,"noArchive":false,"noThumbnail":false,"splitManifest":false,"useFilenameFormat":false,"useFolderPrefix":false},"passphrase":"","podAnnotations":{},"podLabels":{},"resources":{},"resourcesPreset":"small","schedule":"0 3 * * *","scratch":{"existingClaim":"","sizeLimit":""},"startingDeadlineSeconds":3600,"successfulJobsHistoryLimit":3,"suspend":false,"target":"/usr/src/paperless/export","timeZone":"","tolerations":[],"ttlSecondsAfterFinished":86400,"upload":{"container":{},"enabled":false,"mountPath":"/export"},"zip":{"nameTemplate":"paperless-export-%Y-%m-%dT%H%M%SZ","retentionCount":7}}` | Scheduled logical backups, taken with the `document_exporter` management command. This is a different kind of backup from a volume snapshot and neither replaces the other: a snapshot is a crash-consistent copy of bytes that restores onto the same engine at a compatible version, while an export is documents plus a `manifest.json` that can be imported into a *fresh* instance — the only form that survives a move to another cluster or the SQLite to PostgreSQL switch.  The exporter takes the application's own media lock while it runs, so it does not race the consumer for files. It is not a transactional snapshot of the database, though, so schedule it for a quiet hour. |
| backup.activeDeadlineSeconds | int | `21600` | Seconds a run may take before it is killed. Six hours covers a first full export of a large archive over slow storage; without it a wedged export blocks every later run under `concurrencyPolicy: Forbid`. |
| backup.affinity | object | `{}` | Explicit affinity for the backup pod, replacing the co-scheduling rule entirely. Setting this while `backup.coScheduleWithApp` is still on is a contradiction the chart refuses to render, because the pod would then not be co-scheduled and nothing would say so. |
| backup.backoffLimit | int | `1` | Retries before a run is given up on. Low on purpose: an export that failed on a full volume or an unreachable database fails the same way three seconds later, and the next scheduled run is the real retry. |
| backup.coScheduleWithApp | bool | `true` | Schedule the backup pod onto the node the application pod is running on, with a required `podAffinity`.  This is what makes the default `ReadWriteOnce` claims work: an RWO volume can be mounted by several pods at once as long as they share a node, and cannot be attached to two nodes at all. With this off and RWO storage, the Job either happens to land correctly or sits Pending until its deadline — a backup that silently never runs.  The cost is that a backup cannot start while the application pod is down, which is the right trade: it would have nothing consistent to read anyway. Turn it off for `ReadWriteMany` storage, or when `backup.affinity` states the placement explicitly. |
| backup.concurrencyPolicy | string | `"Forbid"` | What to do when a run is still going at the next scheduled time. `Forbid` is the only safe answer here: two exporters writing one target directory interleave their output, and the second one's `--delete` pass removes files the first has not written yet. |
| backup.enabled | bool | `false` | Create the backup CronJob. Requires `persistence.export.enabled`, because the Job would otherwise write the archive into its own `emptyDir` and delete it again on completion — a backup that reports success nightly and restores nothing. The chart refuses to render rather than producing that. |
| backup.existingSecretKey | string | `"export-passphrase"` | Key inside `existingSecret` that holds the export passphrase. |
| backup.failedJobsHistoryLimit | int | `3` | Failed Jobs retained in the CronJob's history. Keep at least a couple: the failed Job's pod is the only place the reason is recorded. |
| backup.mode | string | `"incremental"` | What kind of export to take.  `incremental` keeps one continuously-updated export tree in the target directory, re-writing only the documents that changed since the last run. Cheapest by far, and the right default — but it is one snapshot, not a history, so a document deleted by mistake is gone from the backup on the next run.  `zip` produces a dated archive per run and keeps `backup.zip.retentionCount` of them, which is what gives you a history to roll back to. **It builds the whole export in the scratch directory first**, so it needs as much free space there as the entire archive occupies; see `backup.scratch`.  `dataOnly` exports the database contents and no files at all. Small and quick, and useful alongside a volume snapshot that already covers the media — but on its own it restores an instance that knows about every document and holds none of them. |
| backup.nodeSelector | object | `{}` | Node selector for the backup pod. Empty inherits the application's. |
| backup.options | object | `{"batchSize":500,"compareChecksums":false,"compareJson":false,"delete":true,"noArchive":false,"noThumbnail":false,"splitManifest":false,"useFilenameFormat":false,"useFolderPrefix":false}` | The exporter's own flags. Every one of these is a documented `document_exporter` option; the names here are the long forms. |
| backup.options.batchSize | int | `500` | `--batch-size`: database records serialised per batch. Lower it if the Job is killed for exceeding its memory limit on a large archive. |
| backup.options.compareChecksums | bool | `false` | `--compare-checksums`: decide what changed by hashing file contents rather than by comparing size and modification time. Slower on every run, and the only setting that notices a file corrupted in place. |
| backup.options.compareJson | bool | `false` | `--compare-json`: leave the manifest and metadata JSON files alone when their contents have not changed, which keeps an incremental export's modification times stable for a file-level backup tool watching the directory. |
| backup.options.delete | bool | `true` | `--delete`: remove exported files that are no longer in the dataset, so the target tracks the archive rather than growing forever. Meaningless in `zip` mode, where the same flag instead empties the target directory before the new archive is moved in — which would destroy every retained archive, so the chart refuses to render that combination. |
| backup.options.noArchive | bool | `false` | `--no-archive`: leave out the generated PDF/A files. They are reproducible from the originals by re-running OCR, so this trades a much smaller backup for a long restore. |
| backup.options.noThumbnail | bool | `false` | `--no-thumbnail`: leave out thumbnails, which are regenerated on demand. |
| backup.options.splitManifest | bool | `false` | `--split-manifest`: write one JSON file per document rather than a single `manifest.json`. Worth setting for a large archive — a monolithic manifest is one enormous file that a deduplicating backup tool has to re-transfer whenever any document changes. |
| backup.options.useFilenameFormat | bool | `false` | `--use-filename-format`: name exported files with `paperless.filenameFormat` instead of the exporter's own scheme. Makes the export browsable by a human; makes it less stable, because renaming a correspondent renames files. |
| backup.options.useFolderPrefix | bool | `false` | `--use-folder-prefix`: sort the export into `archive/`, `originals/`, `thumbnails/` and `json/` subdirectories instead of interleaving them. |
| backup.passphrase | string | `""` | Passphrase encrypting the sensitive fields of the export. Ignored when `existingSecret` is set. It reaches the exporter through an environment variable rather than on the command line, so it does not appear in the pod spec or in `kubectl describe`.  **Store it somewhere other than the backup.** An encrypted export whose passphrase is lost is not a backup, and the same passphrase has to be given to `restore` on the way back in. |
| backup.podAnnotations | object | `{}` | Additional annotations for the backup pod. |
| backup.podLabels | object | `{}` | Additional labels for the backup pod. |
| backup.resources | object | `{}` | Explicit resource requests and limits for the backup container. |
| backup.resourcesPreset | string | `"small"` | Named resource sizing for the backup container. Ignored when `backup.resources` is set. The exporter copies files and serialises database rows; it does not run OCR, so it needs a fraction of what the application does. |
| backup.schedule | string | `"0 3 * * *"` | Cron schedule. The default is nightly at 03:00, which is the quiet hour this wants. |
| backup.scratch | object | `{"existingClaim":"","sizeLimit":""}` | Scratch space for the backup pod, mounted at `/tmp` as `PAPERLESS_SCRATCH_DIR`.  Separate from `persistence.scratchSizeLimit` because the requirement differs by orders of magnitude. An incremental export streams straight into the target and needs almost none; a `zip` export assembles the **entire** export tree here before compressing it, so it needs as much free space as the whole archive occupies. An `emptyDir` is charged against the node's ephemeral storage, and a pod that exceeds its limit is evicted mid-export. |
| backup.scratch.existingClaim | string | `""` | Use an existing PersistentVolumeClaim for the scratch directory instead of an `emptyDir`. This is the answer for `zip` mode over an archive larger than the node's ephemeral storage. |
| backup.scratch.sizeLimit | string | `""` | Size limit of the `emptyDir`. Empty removes the limit, which is the default here because a limit too small for the archive turns a backup into an eviction. Ignored when `existingClaim` is set. |
| backup.startingDeadlineSeconds | int | `3600` | Seconds a missed schedule may still be started within. Bounded rather than unlimited so a controller outage does not fire every skipped run at once when it comes back. |
| backup.successfulJobsHistoryLimit | int | `3` | Completed Jobs retained in the CronJob's history. |
| backup.suspend | bool | `false` | Suspend the schedule without deleting the CronJob, for a maintenance window. |
| backup.target | string | `"/usr/src/paperless/export"` | Directory the export is written to, inside the export volume. |
| backup.timeZone | string | `""` | IANA time zone the schedule is interpreted in (`Europe/Berlin`). Empty uses the kube-controller-manager's zone, which is UTC on most clusters. Requires Kubernetes 1.27+; on older clusters the field is dropped rather than rejected. |
| backup.tolerations | list | `[]` | Tolerations for the backup pod. Empty inherits the application's. |
| backup.ttlSecondsAfterFinished | int | `86400` | Seconds a finished Job is kept before the TTL controller removes it. A day, so the logs of last night's backup are still there in the morning. |
| backup.upload | object | `{"container":{},"enabled":false,"mountPath":"/export"}` | Ship the finished export off the cluster. An export sitting on a PersistentVolume in the same cluster as the archive it backs up survives a mistake, but not the cluster.  The chart deliberately takes no dependency on any particular tool. When this is enabled the exporter moves to an `initContainer` and the container below becomes the pod's main container — so it runs only after the export has completed successfully, with the export volume mounted. Any image that can copy a directory somewhere (rclone, restic, the AWS CLI, `mc`) works, with its own credentials from its own Secret. |
| backup.upload.container | object | `{}` | The container itself, in the `corev1.Container` shape, verbatim apart from the export volume mount the chart appends. `name` and `image` are required; everything else — `command`, `env`, `envFrom`, `resources`, `securityContext` — is yours. Required when `backup.upload.enabled` is set. Rendered through the template engine, so `{{ .Release.Name }}` works.  Typed against the Kubernetes definition would be better, but a `$ref` to it makes the empty default itself invalid — `name` is a required property — so the object is left open and `validateValues` checks what actually matters instead. |
| backup.upload.enabled | bool | `false` | Run the upload container after each successful export. |
| backup.upload.mountPath | string | `"/export"` | Where the export volume is mounted in the upload container. |
| backup.zip | object | `{"nameTemplate":"paperless-export-%Y-%m-%dT%H%M%SZ","retentionCount":7}` | Dated archives, used when `backup.mode` is `zip`. |
| backup.zip.nameTemplate | string | `"paperless-export-%Y-%m-%dT%H%M%SZ"` | Base name of each archive, as a `strftime` format string; `.zip` is appended. Expanded by `date` **inside the container at run time**, not by Helm — a name templated at render time would be frozen at the moment of the last `helm upgrade`, and every run would overwrite the same file. |
| backup.zip.retentionCount | int | `7` | How many archives to keep. After a successful export the oldest are deleted until this many remain, so the export volume has to hold this many copies of the archive. |
| commonAnnotations | object | `{}` | Annotations added to every object this chart creates. |
| commonLabels | object | `{}` | Labels added to every object this chart creates. |
| database | object | `{"engine":"sqlite","existingSecretKey":"database-password","host":"","name":"paperless","options":{},"password":"","poolSize":0,"port":0,"sslMode":"","user":"paperless"}` | Which database paperless-ngx stores its metadata in. The documents themselves always live on the media volume; this holds the index, the tags, the correspondents and the audit log. |
| database.engine | string | `"sqlite"` | Database engine. `sqlite` keeps everything in the data volume and needs no second workload — the right answer for a single-user instance. PostgreSQL is upstream's recommendation for anything larger and is the only engine this chart can bundle. |
| database.existingSecretKey | string | `"database-password"` | Key inside `existingSecret` that holds the database password. |
| database.host | string | `""` | Database host. Empty resolves to the bundled PostgreSQL when `postgresql.enabled` is set; otherwise it is required for every engine except SQLite. |
| database.name | string | `"paperless"` | Database name. Also the database the bundled PostgreSQL creates on first start. |
| database.options | object | `{}` | Additional driver options, written verbatim into `PAPERLESS_DB_OPTIONS` as `key=value` pairs. This is upstream's consolidated replacement for the per-option environment variables, so it is where anything the driver supports but this chart does not model goes: `sslrootcert` and `sslcert` for PostgreSQL client certificates, `ssl.ca` for MariaDB, `connect_timeout`, `pool.min_size`. Nested options use dot notation, as upstream does.  An entry here wins over `sslMode` and `poolSize` when the keys collide, so an option the chart models can still be overridden without a second mechanism. Paths named here have to be mounted into the pod yourself, with `extraVolumes` and `extraVolumeMounts`. |
| database.password | string | `""` | Database password. Ignored when `existingSecret` is set. Required for every engine except SQLite — including the bundled PostgreSQL, whose superuser it becomes. |
| database.poolSize | int | `0` | Maximum size of the connection pool. `0` leaves pooling off, which is correct for a single-replica deployment talking to a database on the same network.  Rendered into `PAPERLESS_DB_OPTIONS` as `pool.max_size`, replacing the deprecated `PAPERLESS_DB_POOLSIZE`. PostgreSQL only — its driver is the one that pools — so the chart refuses to render it for another engine rather than accepting a setting nothing reads. |
| database.port | int | `0` | Database port. `0` uses the engine's default (5432 for PostgreSQL, 3306 for MariaDB). |
| database.sslMode | string | `""` | TLS mode for the connection. Empty uses the engine default, which is `prefer` for PostgreSQL and `PREFERRED` for MariaDB — both of which fall back to plaintext without telling anyone. Set `require` (PostgreSQL) or `REQUIRED` (MariaDB) for a database outside the cluster.  Rendered into `PAPERLESS_DB_OPTIONS` as `sslmode` for PostgreSQL and `ssl_mode` for MariaDB. The dedicated `PAPERLESS_DBSSLMODE` variable this used to emit is deprecated upstream and is removed in 3.2; it still works today and logs a startup warning, which is why the chart no longer emits it. Ignored for SQLite, which has no connection to secure. |
| database.user | string | `"paperless"` | Database user. Also the role the bundled PostgreSQL creates on first start. |
| existingConfigMap | string | `""` | Name of an existing ConfigMap holding the `PAPERLESS_*` settings. When set, the chart creates none of its own and every value under `paperless` below is ignored — the ConfigMap is mounted with `envFrom` exactly as the generated one would be. For a deployment that manages the application's configuration outside this chart; the credentials still come from `existingSecret`. |
| existingSecret | string | `""` | Name of an existing Secret holding every credential this chart consumes: the Django secret key, the admin password, and the database, broker and SMTP passwords. When set, the chart creates no Secret of its own and the plaintext fields below are ignored — which keeps them out of `values.yaml` and out of the Helm release object. The key names it looks for are the `existingSecretKey` fields beside each credential. |
| extraEnv | list | `[]` | Additional environment variables for the paperless-ngx container. The place for a setting whose value must come from a Secret: the image also resolves any `PAPERLESS_<NAME>_FILE` variable by reading the file it points at, which pairs with a mounted Secret from `extraVolumes`. |
| extraVolumeMounts | list | `[]` | Additional volume mounts added to the paperless-ngx container. |
| extraVolumes | list | `[]` | Additional volumes added to the pod. |
| fullnameOverride | string | `""` | Override the full generated resource name. |
| gateway | object | `{"annotations":{},"create":false,"enabled":false,"filters":[],"gatewayClassName":"","hostnames":[],"httpsRedirect":{"enabled":false,"sectionName":"http","statusCode":301},"parentRefs":[],"path":"/","timeouts":{},"tls":{"certificateRefs":[],"enabled":false,"mode":"Terminate"}}` | Publishing through the Gateway API. The cluster operator owns the `Gateway`; this chart owns the `HTTPRoute` that attaches to it. |
| gateway.annotations | object | `{}` | Annotations for the HTTPRoute and, when created, the Gateway. |
| gateway.create | bool | `false` | Also create a Gateway, for an install with no cluster-wide one to attach to. |
| gateway.enabled | bool | `false` | Create an HTTPRoute. Requires the Gateway API CRDs; the chart refuses to render without them rather than dropping the route silently. |
| gateway.filters | list | `[]` | HTTPRoute filters — header manipulation, request mirroring, URL rewriting. These are the typed replacement for what Ingress expressed as controller-specific annotations. |
| gateway.gatewayClassName | string | `""` | GatewayClass for the created Gateway. Required when `gateway.create` is set — a Gateway without one is never reconciled by anything. |
| gateway.hostnames | list | `[]` | Hostnames the route serves. Used as the default for `paperless.url`. Required unless `gateway.create` is set, because a route with no hostnames matches every hostname its listener accepts — on a shared Gateway that quietly steals other applications' traffic. |
| gateway.httpsRedirect | object | `{"enabled":false,"sectionName":"http","statusCode":301}` | A second route that redirects plaintext requests to HTTPS. |
| gateway.httpsRedirect.enabled | bool | `false` | Create the redirect route. |
| gateway.httpsRedirect.sectionName | string | `"http"` | Listener the redirect route binds to. It must be the plaintext one: bound to every listener, an HTTPS listener would redirect to itself forever. |
| gateway.httpsRedirect.statusCode | int | `301` | HTTP status code for the redirect. |
| gateway.parentRefs | list | `[]` | Gateways to attach to, as `{name, namespace, sectionName}` entries. Empty attaches to the Gateway this chart creates, when `gateway.create` is set. |
| gateway.path | string | `"/"` | Path prefix the route matches. |
| gateway.timeouts | object | `{}` | Route timeouts, e.g. `{request: 300s}`. Worth raising: uploading a large scan through the gateway can outlast a default request timeout. |
| gateway.tls | object | `{"certificateRefs":[],"enabled":false,"mode":"Terminate"}` | TLS for the created Gateway's HTTPS listener. |
| gateway.tls.certificateRefs | list | `[]` | Certificates the listener terminates with, as `{name}` or `{name, namespace}` entries. Required for a `Terminate` listener: unlike an Ingress, nothing looks a certificate up from the hostname. |
| gateway.tls.enabled | bool | `false` | Add an HTTPS listener. |
| gateway.tls.mode | string | `"Terminate"` | TLS mode for the listener. |
| image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| image.registry | string | `""` | Registry host. Empty means Docker Hub, which does not host paperless-ngx — the default below names GitHub's registry explicitly instead. |
| image.repository | string | `"ghcr.io/paperless-ngx/paperless-ngx"` | The container image repository. The official image, published by the paperless-ngx project, running its whole stack (webserver, consumer, task worker and scheduler) under one s6-overlay supervision tree. |
| image.tag | string | `"3.0.5@sha256:65a4cabf0169ea7fbd90ab7bb28ba3f8b5909613635acda1a03ad606f34b456b"` | The container image tag. Defaults to the chart's `appVersion` when empty. |
| imagePullSecrets | list | `[]` | Optional image pull secrets for private registries. |
| ingress | object | `{"annotations":{},"className":"","enabled":false,"host":"","path":"/","pathType":"Prefix","tls":{"enabled":false,"secretName":""}}` | Publishing through an Ingress controller. Mutually exclusive with `gateway` only in the sense that running both is usually a mistake; the chart lets you, for the window in which a cluster migrates from one to the other. |
| ingress.annotations | object | `{}` | Annotations for the Ingress. This is where controller-specific configuration goes — certificate issuance, and above all the request body size limit: uploading a document through the web UI fails at the controller's default (1 MiB on ingress-nginx) long before paperless sees it. For ingress-nginx that is `nginx.ingress.kubernetes.io/proxy-body-size: 100m`. |
| ingress.className | string | `""` | IngressClass to use. Empty uses the cluster's default class. |
| ingress.enabled | bool | `false` | Create an Ingress. |
| ingress.host | string | `""` | Hostname to publish. Required when the Ingress is enabled, and used as the default for `paperless.url`. |
| ingress.path | string | `"/"` | Path to publish under. Leave at `/` unless `paperless.rootPath` moves the application. |
| ingress.pathType | string | `"Prefix"` | Path matching mode. |
| ingress.tls | object | `{"enabled":false,"secretName":""}` | TLS configuration. |
| ingress.tls.enabled | bool | `false` | Serve the host over TLS. |
| ingress.tls.secretName | string | `""` | Secret holding the certificate. Empty uses `<fullname>-tls`, which is the name cert-manager creates from an `ingress.annotations` issuer annotation. |
| kubeVersionOverride | string | `""` | Kubernetes version to target when branching on API availability. Lets `helm template` render for a specific cluster version without a live connection. |
| livenessProbe | object | `{"enabled":true,"failureThreshold":5,"httpGet":{"path":"{{ .Values.paperless.rootPath }}/","port":"http"},"periodSeconds":30,"timeoutSeconds":10}` | Liveness probe. Restarts the container when the webserver stops answering. |
| livenessProbe.enabled | bool | `true` | Enable the liveness probe. |
| livenessProbe.failureThreshold | int | `5` | Consecutive failures before the container is restarted. Deliberately tolerant: a restart interrupts every document being processed, and the webserver shares a pod with workers that can saturate it. |
| livenessProbe.httpGet | object | `{"path":"{{ .Values.paperless.rootPath }}/","port":"http"}` | HTTP handler for the probe. |
| livenessProbe.httpGet.path | string | `"{{ .Values.paperless.rootPath }}/"` | Path to request. |
| livenessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| livenessProbe.periodSeconds | int | `30` | Probe interval. |
| livenessProbe.timeoutSeconds | int | `10` | Probe timeout. |
| metrics | object | `{"dashboard":{"enabled":false,"grafanaOperator":{"allowCrossNamespaceImport":true,"enabled":false,"folder":"","instanceSelector":{"matchLabels":{"dashboards":"grafana"}},"resyncPeriod":"5m"},"label":"grafana_dashboard","labelValue":"1"},"prometheusRule":{"enabled":false,"labels":{},"scope":"namespace"}}` | Monitoring. paperless-ngx exposes no Prometheus endpoint of its own, so everything here is built on series a cluster already has: kube-state-metrics for workload health and the kubelet for volume usage. That is a real limit, and it is stated rather than papered over — there are no alerts on queue depth or consumption failures, because nothing publishes them. |
| metrics.dashboard | object | `{"enabled":false,"grafanaOperator":{"allowCrossNamespaceImport":true,"enabled":false,"folder":"","instanceSelector":{"matchLabels":{"dashboards":"grafana"}},"resyncPeriod":"5m"},"label":"grafana_dashboard","labelValue":"1"}` | Grafana dashboards, shipped as a labelled ConfigMap and optionally as `GrafanaDashboard` custom resources. |
| metrics.dashboard.enabled | bool | `false` | Create the dashboard ConfigMap for the Grafana sidecar to discover. |
| metrics.dashboard.grafanaOperator | object | `{"allowCrossNamespaceImport":true,"enabled":false,"folder":"","instanceSelector":{"matchLabels":{"dashboards":"grafana"}},"resyncPeriod":"5m"}` | grafana-operator v5 delivery, which — unlike the sidecar — can be granted cross-namespace import from this side. |
| metrics.dashboard.grafanaOperator.allowCrossNamespaceImport | bool | `true` | Allow a Grafana in another namespace to import these dashboards. |
| metrics.dashboard.grafanaOperator.enabled | bool | `false` | Also create one `GrafanaDashboard` per dashboard file. Requires `metrics.dashboard.enabled`, because the custom resources reference the ConfigMap rather than inlining the JSON. |
| metrics.dashboard.grafanaOperator.folder | string | `""` | Grafana folder to file the dashboards under. Empty uses the operator's default. |
| metrics.dashboard.grafanaOperator.instanceSelector | object | `{"matchLabels":{"dashboards":"grafana"}}` | Which Grafana instances import them. An empty selector matches none, so the dashboards would be created and then ignored. |
| metrics.dashboard.grafanaOperator.resyncPeriod | string | `"5m"` | How often the operator re-applies them. |
| metrics.dashboard.label | string | `"grafana_dashboard"` | Label the Grafana sidecar selects dashboards on. |
| metrics.dashboard.labelValue | string | `"1"` | Value for that label. |
| metrics.prometheusRule | object | `{"enabled":false,"labels":{},"scope":"namespace"}` | The PrometheusRule carrying this chart's alerting rules. |
| metrics.prometheusRule.enabled | bool | `false` | Create the PrometheusRule. Requires the Prometheus Operator CRDs. |
| metrics.prometheusRule.labels | object | `{}` | Extra labels for the PrometheusRule, e.g. the `release` label a Prometheus Operator instance selects rules on. Without the one your instance selects, the object is created and never loaded. |
| metrics.prometheusRule.scope | string | `"namespace"` | How far the rules are scoped. A PrometheusRule is not confined to its own namespace, so `none` makes this release alert on every other paperless-ngx in the cluster as well. `namespace` rewrites every selector to match only this release's namespace. |
| nameOverride | string | `""` | Override the chart name used in resource names and labels. |
| namespaceOverride | string | `""` | Deploy into a namespace other than the release namespace. |
| networkPolicy | object | `{"cilium":{"description":"","dnsMatchPatterns":[],"enableDefaultDeny":true,"toFQDNs":[]},"egress":{"cidr":"0.0.0.0/0","except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"external":{"enabled":true,"namespaceSelector":{},"podSelector":{}},"extraRules":[],"https":false,"smtp":false},"enabled":false,"engine":"kubernetes","ingress":{"controller":{"enabled":true,"namespace":"ingress-nginx","podSelector":{"app.kubernetes.io/name":"ingress-nginx"}},"extraRules":[],"gateway":{"enabled":true,"namespaceSelector":{},"podSelector":{}}}}` | Network policies. Every workload in the release gets a default-deny pair, with exactly the rules the enabled components need — the application reaches the broker, the database and the document converters, and nothing reaches back. |
| networkPolicy.cilium | object | `{"description":"","dnsMatchPatterns":[],"enableDefaultDeny":true,"toFQDNs":[]}` | Cilium-only additions, used when `engine` is `cilium` or `both`. Every rule above is translated automatically; these are the ones the portable API cannot express. |
| networkPolicy.cilium.description | string | `""` | `spec.description`, which Cilium surfaces in `cilium policy get` and in Hubble flow verdicts — the one place a rule's reason is visible to whoever is debugging a dropped packet. |
| networkPolicy.cilium.dnsMatchPatterns | list | `[]` | What the DNS proxy may resolve, e.g. `- matchPattern: "*.example.com"`. Defaults to everything. An FQDN rule is enforced against the addresses Cilium's DNS proxy saw returned, so without a DNS rule observing the lookup every `toFQDNs` entry matches nothing. |
| networkPolicy.cilium.enableDefaultDeny | bool | `true` | State default-deny explicitly rather than relying on it being implied by the presence of rules. Requires Cilium 1.16+. |
| networkPolicy.cilium.toFQDNs | list | `[]` | Egress destinations by DNS name, replacing the internet-wide CIDR rules for `egress.https` and `egress.smtp` — e.g. `- matchName: smtp.example.com`. Ships empty, so an unconfigured install keeps the portable CIDR form. |
| networkPolicy.egress | object | `{"cidr":"0.0.0.0/0","except":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"],"external":{"enabled":true,"namespaceSelector":{},"podSelector":{}},"extraRules":[],"https":false,"smtp":false}` | Where the application may connect. Cluster DNS and the enabled in-release components are always permitted; these are the destinations outside the release. |
| networkPolicy.egress.cidr | string | `"0.0.0.0/0"` | Destination CIDR for the internet-facing egress rules. |
| networkPolicy.egress.except | list | `["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16"]` | CIDRs carved out of `cidr`. The defaults exclude private space and link-local 169.254.0.0/16, which is where the cloud instance metadata endpoint lives. |
| networkPolicy.egress.external | object | `{"enabled":true,"namespaceSelector":{},"podSelector":{}}` | A database or broker outside this release, when `postgresql.enabled` or `valkey.enabled` is false. |
| networkPolicy.egress.external.enabled | bool | `true` | Allow egress to the external database and broker addresses. |
| networkPolicy.egress.external.namespaceSelector | object | `{}` | Namespace selector for them. Empty means this release's namespace. |
| networkPolicy.egress.external.podSelector | object | `{}` | Pod selector for them. Empty selects every pod in the selected namespaces, which is as narrow as the portable API can be about a datastore it cannot name. |
| networkPolicy.egress.extraRules | list | `[]` | Egress rules appended verbatim to the application's policy. |
| networkPolicy.egress.https | bool | `false` | Allow outbound HTTPS to the public internet, minus RFC1918 space and the cloud metadata endpoint. Needed only for features that leave the cluster: outgoing mail over an external relay, webhooks, mail fetching, or a remote OCR endpoint. |
| networkPolicy.egress.smtp | bool | `false` | Allow outbound SMTP submission (TCP 587 and 465) to the same destinations. |
| networkPolicy.enabled | bool | `false` | Create the policies. |
| networkPolicy.engine | string | `"kubernetes"` | Which policy dialect to render. `kubernetes` emits the portable `networking.k8s.io/v1` objects; `cilium` emits `CiliumNetworkPolicy`, which states default-deny explicitly and can name egress destinations by DNS name; `both` emits both, for the window in which a cluster is migrating between CNIs. |
| networkPolicy.ingress | object | `{"controller":{"enabled":true,"namespace":"ingress-nginx","podSelector":{"app.kubernetes.io/name":"ingress-nginx"}},"extraRules":[],"gateway":{"enabled":true,"namespaceSelector":{},"podSelector":{}}}` | Who may reach the application's HTTP port. |
| networkPolicy.ingress.controller | object | `{"enabled":true,"namespace":"ingress-nginx","podSelector":{"app.kubernetes.io/name":"ingress-nginx"}}` | The Ingress controller. |
| networkPolicy.ingress.controller.enabled | bool | `true` | Allow ingress from the controller's pods. Enabled automatically alongside `ingress.enabled`; without it an enabled Ingress resolves and then times out. |
| networkPolicy.ingress.controller.namespace | string | `"ingress-nginx"` | Namespace the Ingress controller runs in. |
| networkPolicy.ingress.controller.podSelector | object | `{"app.kubernetes.io/name":"ingress-nginx"}` | Pod selector for the Ingress controller. |
| networkPolicy.ingress.extraRules | list | `[]` | Ingress rules appended verbatim to the application's policy, for peers this chart does not model — a backup job reaching the API, or a second namespace's scanner sidecar. |
| networkPolicy.ingress.gateway | object | `{"enabled":true,"namespaceSelector":{},"podSelector":{}}` | The Gateway API data plane. Its namespace and selector are derived from `gateway.parentRefs`, because the Gateway a policy must admit is by definition the one this chart's route attaches to. |
| networkPolicy.ingress.gateway.enabled | bool | `true` | Allow ingress from the Gateway's pods. Only has an effect while `gateway.enabled` is set. |
| networkPolicy.ingress.gateway.namespaceSelector | object | `{}` | Override the namespace selector for the Gateway's data plane. |
| networkPolicy.ingress.gateway.podSelector | object | `{}` | Override the pod selector for the Gateway's data plane. The default matches the `gateway.networking.k8s.io/gateway-name` label every implementation sets. |
| nodeSelector | object | `{}` | Node selector for pod assignment. |
| paperless.admin | object | `{"email":"","existingSecretKey":"admin-password","password":"","user":""}` | The superuser created on first start. Without it the instance comes up with no account at all and the only way in is `kubectl exec ... createsuperuser`. |
| paperless.admin.email | string | `""` | E-mail address of the superuser. |
| paperless.admin.existingSecretKey | string | `"admin-password"` | Key inside `existingSecret` that holds the admin password. |
| paperless.admin.password | string | `""` | Password of the superuser. Ignored when `existingSecret` is set. Required whenever `admin.user` is. |
| paperless.admin.user | string | `""` | Username of the superuser to create. Empty creates none. The account is created once; changing the password here afterwards does update it, and changing the username creates a second account rather than renaming the first. |
| paperless.allowedHosts | list | `[]` | Hostnames Django accepts a request for, beyond the one derived from `paperless.url`. Empty keeps the upstream default of `*`, which is safe here only because the Service and the NetworkPolicy already decide who may connect at all. |
| paperless.archiveFileGeneration | string | `"auto"` | When to produce an archived PDF/A alongside the original. `never` halves the storage a document costs and gives up full-text search of scans. |
| paperless.auditLog | bool | `true` | Record who changed what in the audit log. |
| paperless.consumer | object | `{"barcodes":{"asn":false,"asnPrefix":"ASN","enabled":false},"deleteDuplicates":false,"pollingInterval":0,"recursive":false,"stabilityDelay":5,"subdirsAsTags":false}` | The consumption directory watcher: what happens to files dropped into `/usr/src/paperless/consume`. |
| paperless.consumer.barcodes | object | `{"asn":false,"asnPrefix":"ASN","enabled":false}` | Barcode handling. Every option here costs an extra rendering pass per page. |
| paperless.consumer.barcodes.asn | bool | `false` | Read the archive serial number from a barcode. |
| paperless.consumer.barcodes.asnPrefix | string | `"ASN"` | Prefix identifying an ASN barcode. |
| paperless.consumer.barcodes.enabled | bool | `false` | Split one incoming file into several documents at pages carrying a separator barcode. |
| paperless.consumer.deleteDuplicates | bool | `false` | Delete an incoming file that is byte-identical to a document already stored, instead of reporting it as a failed consumption. |
| paperless.consumer.pollingInterval | int | `0` | Seconds between directory scans. `0` uses filesystem events instead, which is instant and correct on a local volume. **Network storage (NFS, SMB, most ReadWriteMany classes) delivers no events**, so a share that everybody drops scans into needs polling — 10 to 30 seconds is the usual choice. |
| paperless.consumer.recursive | bool | `false` | Also watch subdirectories of the consumption directory. |
| paperless.consumer.stabilityDelay | int | `5` | Seconds a file must stop changing before it is consumed, so a document that is still being copied is not ingested half-written. |
| paperless.consumer.subdirsAsTags | bool | `false` | Turn each subdirectory name into a tag. Requires `consumer.recursive`. |
| paperless.corsAllowedHosts | list | `[]` | Additional origins allowed to make cross-origin browser requests, scheme included. Needed only by a separate frontend or a browser extension; the web UI is same-origin. |
| paperless.csrfTrustedOrigins | list | `[]` | Additional origins trusted for unsafe requests (POST, PUT, DELETE), scheme included. `paperless.url` is trusted automatically; this is for the second hostname a migration or a split-horizon DNS setup adds. |
| paperless.email | object | `{"existingSecretKey":"email-password","from":"","host":"","password":"","port":587,"useSsl":false,"useTls":true,"user":""}` | Outgoing mail, used for password resets and for sharing documents by link. Incoming mail accounts are configured in the web UI instead, not here. |
| paperless.email.existingSecretKey | string | `"email-password"` | Key inside `existingSecret` that holds the SMTP password. |
| paperless.email.from | string | `""` | Envelope sender address. Empty uses the application default, which many relays reject. |
| paperless.email.host | string | `""` | SMTP host. Empty disables outgoing mail, and password resets stop working with it. |
| paperless.email.password | string | `""` | SMTP password. Ignored when `existingSecret` is set. |
| paperless.email.port | int | `587` | SMTP port. |
| paperless.email.useSsl | bool | `false` | Use implicit TLS (SMTPS, usually port 465). Mutually exclusive with `email.useTls`. |
| paperless.email.useTls | bool | `true` | Use STARTTLS. Mutually exclusive with `email.useSsl`. |
| paperless.email.user | string | `""` | SMTP username. |
| paperless.existingSecretKey | string | `"secret-key"` | Key inside `existingSecret` that holds the Django secret key. |
| paperless.extraConfig | object | `{}` | Additional `PAPERLESS_*` settings written to the ConfigMap verbatim, for anything this chart does not model (see the upstream configuration reference for the full list). Values are rendered through the template engine, so `{{ .Release.Name }}` works.  Never put credentials here — the ConfigMap is not a Secret. Use `extraEnv` with a `secretKeyRef`, or the `PAPERLESS_<NAME>_FILE` indirection the image supports. |
| paperless.filenameFormat | string | `""` | Storage layout of the original files inside the media volume, e.g. `{{ created_year }}/{{ correspondent }}/{{ title }}`. Empty stores everything flat under `documents/originals`. Changing it later renames files on disk on the next run of the `document_renamer` command; it does not reorganise them by itself. |
| paperless.filenameFormatRemoveNone | bool | `false` | Drop the placeholder from `filenameFormat` when the field is empty, rather than writing the literal word `none` into the path. |
| paperless.ocr | object | `{"clean":"clean","deskew":true,"imageDpi":0,"language":"eng","mode":"auto","outputType":"pdfa","pages":0,"rotatePages":true,"userArgs":{}}` | Optical character recognition, performed by OCRmyPDF and Tesseract. |
| paperless.ocr.clean | string | `"clean"` | Run unpaper to clean scans before recognition. `clean-final` also keeps the cleaned image in the archive file, which loses fidelity to the original scan. |
| paperless.ocr.deskew | bool | `true` | Straighten crooked scans before recognition. |
| paperless.ocr.imageDpi | int | `0` | Assume this DPI for images that do not declare one. `0` leaves it to OCRmyPDF, which refuses images with no resolution information rather than guessing. |
| paperless.ocr.language | string | `"eng"` | Tesseract language, as a three-letter ISO 639-2 code; several are combined with `+` (`deu+eng`). **Only languages already installed in the image work.** Upstream installs additional packs at container start when `PAPERLESS_OCR_LANGUAGES` is set, which needs a writable root filesystem, root privileges and internet access from the pod — none of which this chart's security baseline provides. Build a derived image instead. |
| paperless.ocr.mode | string | `"auto"` | When to run OCR. `auto` skips pages that already carry text, `redo` replaces existing text layers, `force` rasterises and re-recognises everything, `off` disables OCR entirely. |
| paperless.ocr.outputType | string | `"pdfa"` | Format of the generated archive file. The PDF/A variants are the archival ones and the reason to run this software at all. |
| paperless.ocr.pages | int | `0` | Only OCR the first N pages of a document. `0` means all of them. |
| paperless.ocr.rotatePages | bool | `true` | Detect and correct page rotation. |
| paperless.ocr.userArgs | object | `{}` | Extra arguments handed to OCRmyPDF verbatim, as a mapping. Rendered to the JSON object the application expects, e.g. `{"invalidate_digital_signatures": true}`. |
| paperless.proxy | object | `{"trustForwardedProto":true,"trustedProxies":[],"useXForwardedHost":true,"useXForwardedPort":true}` | How to interpret the headers a reverse proxy, Ingress controller or Gateway adds. |
| paperless.proxy.trustForwardedProto | bool | `true` | Treat `X-Forwarded-Proto: https` as proof the client request was HTTPS. Required for secure cookies and correct `https://` links whenever TLS is terminated at the proxy, which is the normal arrangement for both `ingress` and `gateway` below.  Only ever set this where a proxy actually strips and re-sets the header. A pod reachable directly would let any client claim its plaintext request was encrypted. |
| paperless.proxy.trustedProxies | list | `[]` | Proxy addresses whose forwarding headers are trusted, as IPs or CIDRs. Empty trusts the headers unconditionally, which is what the settings above already do; naming the Ingress controller's pod CIDR here narrows that. |
| paperless.proxy.useXForwardedHost | bool | `true` | Trust `X-Forwarded-Host`. On by default: behind an Ingress the `Host` header is the internal Service name, and without this the absolute URLs paperless generates — share links, password reset mails, the OAuth callback — point at a name nobody can resolve. |
| paperless.proxy.useXForwardedPort | bool | `true` | Trust `X-Forwarded-Port`, so generated URLs carry the port the client actually used rather than the container port. |
| paperless.remoteUser | object | `{"api":false,"enabled":false,"headerName":"HTTP_REMOTE_USER"}` | Authentication delegated to a proxy in front of paperless — an OAuth2 proxy, an authentication middleware, a service mesh — which authenticates the user and passes the resulting name in a header. |
| paperless.remoteUser.api | bool | `false` | Trust the header for the REST API as well. Separate from the UI switch because API clients usually authenticate with a token instead, and accepting both widens the surface. |
| paperless.remoteUser.enabled | bool | `false` | Trust the header for the web UI. **Only enable this when the proxy strips the header from incoming requests.** Anything that can reach the pod directly can otherwise set it and be whoever it likes, which is why the NetworkPolicy below matters as much as this flag. |
| paperless.remoteUser.headerName | string | `"HTTP_REMOTE_USER"` | WSGI name of the header carrying the username. Django's own spelling: `Remote-User` over the wire arrives as `HTTP_REMOTE_USER`. |
| paperless.rootPath | string | `""` | Serve paperless-ngx under a sub-path of the hostname (`/paperless`), for a reverse proxy that hosts several applications on one origin. Leave empty to serve at the root. The default probe paths follow this value, so they keep pointing at the application when it moves. |
| paperless.secretKey | string | `""` | Django secret key. It signs session cookies, password reset links and share links. There is no safe default, so the chart refuses to render without one here or in `existingSecret`: the upstream fallback is a published constant, and a deployment running on it hands anybody the ability to forge a session. Generate one with `openssl rand -base64 48`.  Changing it later invalidates every session and every outstanding share link; it does not touch stored documents. |
| paperless.tasks | object | `{"threadsPerWorker":0,"timeout":1800,"webserverWorkers":1,"workers":1}` | Task execution. These are the knobs that decide how much CPU and memory the pod actually uses, so they belong next to `resources` in your head. |
| paperless.tasks.threadsPerWorker | int | `0` | Threads each worker gives to OCR. `0` lets the application derive it from the worker count and the CPUs it can see — which, in a container, is the *node's* CPU count and not the limit, so a busy node is a good reason to pin this. |
| paperless.tasks.timeout | int | `1800` | Seconds after which a single task is abandoned. The default is generous because a large scanned document legitimately takes minutes. |
| paperless.tasks.webserverWorkers | int | `1` | Granian worker processes serving HTTP. One is enough for a household; raise it for an instance several people browse at once. |
| paperless.tasks.workers | int | `1` | Celery workers consuming documents in parallel. Each one processes a whole document, so this multiplies peak memory. |
| paperless.timeZone | string | `"UTC"` | IANA time zone used for dates in the UI, in filenames and in scheduled tasks. |
| paperless.updateCheck | bool | `false` | Let the web UI check GitHub for newer releases. Off by default: the version this pod runs is decided by `image.tag`, so the check can only ever report that the chart is behind, and it is an outbound call the NetworkPolicy would otherwise have to permit. |
| paperless.url | string | `""` | The external URL paperless-ngx is reached on, scheme included (`https://paperless.example.com`). Django validates the `Host` header and the origin of every unsafe request against it, so a wrong or missing value shows up as a working login page that rejects the login with a CSRF error. Empty derives it from `ingress.host`, or from the first `gateway.hostnames` entry, which is correct for the common single-hostname install. |
| persistence | object | `{"consume":{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"5Gi","storageClassName":""},"data":{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"5Gi","storageClassName":""},"export":{"accessMode":"ReadWriteOnce","annotations":{},"enabled":false,"existingClaim":"","size":"10Gi","storageClassName":""},"media":{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"20Gi","storageClassName":""},"scratchSizeLimit":"2Gi"}` | The four directories paperless-ngx keeps state in. They are separate claims rather than one, because they have genuinely different lifetimes and sizes: `media` grows forever and is the thing you back up, `data` is rebuildable from it, `consume` is a mailbox, and `export` is scratch space for a command you run by hand. |
| persistence.consume | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"5Gi","storageClassName":""}` | `/usr/src/paperless/consume` — the drop box. Files placed here are ingested and then removed, so this volume is a mailbox and not storage; it only needs to be large enough for the biggest batch anyone drops at once. |
| persistence.consume.accessMode | string | `"ReadWriteOnce"` | The access mode for the volume. |
| persistence.consume.annotations | object | `{}` | Annotations for the PersistentVolumeClaim. |
| persistence.consume.enabled | bool | `true` | Create a PersistentVolumeClaim. An emptyDir works for an instance fed only through the web UI, and loses whatever was queued on a restart. |
| persistence.consume.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. This is where a `ReadWriteMany` share belongs, so a scanner or another pod can write into it — set `paperless.consumer.pollingInterval` as well, because network storage delivers no filesystem events. |
| persistence.consume.size | string | `"5Gi"` | The storage size requested for the volume. |
| persistence.consume.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| persistence.data | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"5Gi","storageClassName":""}` | `/usr/src/paperless/data` — the search index, the trained classification model, the scheduler's state and, with `database.engine: sqlite`, the database file itself. |
| persistence.data.accessMode | string | `"ReadWriteOnce"` | The access mode for the volume. |
| persistence.data.annotations | object | `{}` | Annotations for the PersistentVolumeClaim. |
| persistence.data.enabled | bool | `true` | Create a PersistentVolumeClaim. Everything here except the SQLite database can be rebuilt from the media volume, but rebuilding is a manual `document_index reindex`. |
| persistence.data.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.data.size | string | `"5Gi"` | The storage size requested for the volume. |
| persistence.data.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| persistence.export | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":false,"existingClaim":"","size":"10Gi","storageClassName":""}` | `/usr/src/paperless/export` — where `document_exporter` writes. Backed by an emptyDir unless enabled, which is enough to run an export and copy it out with `kubectl cp`, and keeps the directory writable under a read-only root filesystem either way. |
| persistence.export.accessMode | string | `"ReadWriteOnce"` | The access mode for the volume. |
| persistence.export.annotations | object | `{}` | Annotations for the PersistentVolumeClaim. |
| persistence.export.enabled | bool | `false` | Create a PersistentVolumeClaim, for exports that should outlive the pod. |
| persistence.export.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.export.size | string | `"10Gi"` | The storage size requested for the volume. |
| persistence.export.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| persistence.media | object | `{"accessMode":"ReadWriteOnce","annotations":{},"enabled":true,"existingClaim":"","size":"20Gi","storageClassName":""}` | `/usr/src/paperless/media` — the documents themselves: originals, archived PDF/As and thumbnails. Losing this loses the archive. |
| persistence.media.accessMode | string | `"ReadWriteOnce"` | The access mode for the volume. `ReadWriteOnce` forces the Deployment to the `Recreate` update strategy, because a rolling update cannot hand a mounted volume over. |
| persistence.media.annotations | object | `{}` | Annotations for the PersistentVolumeClaim. `helm.sh/resource-policy: keep` belongs here more than anywhere else in this chart — without it `helm uninstall` deletes the archive. |
| persistence.media.enabled | bool | `true` | Create a PersistentVolumeClaim. When disabled an emptyDir is used, and every restart destroys every stored document. |
| persistence.media.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of creating one. |
| persistence.media.size | string | `"20Gi"` | The storage size requested for the volume. |
| persistence.media.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default; `"-"` disables dynamic provisioning. |
| persistence.scratchSizeLimit | string | `"2Gi"` | Size limit of the `emptyDir` backing `/tmp`, which holds every intermediate file OCR produces — rasterised pages, unpaper output, the PDF being assembled. It is charged against the node's ephemeral storage, and a pod that exceeds it is evicted, so this is the value to raise when large scans fail with no useful error. Empty removes the limit. |
| podAnnotations | object | `{}` | Additional annotations to add to the pod. |
| podAntiAffinity | string | `""` | Shorthand for spreading replicas across nodes. Of no use to a singleton; it exists because the library supports it. Ignored when `affinity` is set. |
| podLabels | object | `{}` | Additional labels to add to the pod. |
| podSecurityContext | object | `{"fsGroup":1000,"runAsGroup":1000,"runAsUser":1000}` | Pod security context, merged over the preset. The identity fields match the `paperless` account in the official image. The image detects that it was started as a non-root user and skips the UID remapping and the recursive `chown` it would otherwise do — so `USERMAP_UID` and `USERMAP_GID` have no effect here, and `fsGroup` is what actually makes the volumes writable. |
| podSecurityContext.fsGroup | int | `1000` | Group ID applied to the mounted volumes. |
| podSecurityContext.runAsGroup | int | `1000` | Primary group ID to run as. |
| podSecurityContext.runAsUser | int | `1000` | User ID to run as. |
| podSecurityContextPreset | string | `"restricted"` | Pod security context baseline. `restricted` applies the Pod Security Standards restricted profile (`runAsNonRoot`, `seccompProfile: RuntimeDefault`, `fsGroupChangePolicy: OnRootMismatch`) on top of the identity fields below. |
| postgresql | object | `{"enabled":false,"image":{"pullPolicy":"","registry":"","repository":"postgres","tag":"18.6@sha256:ae6c78831cbc35fa3a4aaf4d763ddacf6183d6004774cc2dc28b3920410d1d1a"},"persistence":{"enabled":true,"existingClaim":"","size":"8Gi","storageClassName":""},"resources":{},"resourcesPreset":"medium"}` | A single-instance PostgreSQL bundled with the release, so `helm install` produces a working stack on a bare cluster. Evaluation tier by design: one replica, no failover, no backups, no connection pooler. A production deployment points `database.host` at a managed instance or an operator-run cluster instead. |
| postgresql.enabled | bool | `false` | Run PostgreSQL as part of this release. Requires `database.engine: postgresql`. |
| postgresql.image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| postgresql.image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| postgresql.image.repository | string | `"postgres"` | Image repository. The official PostgreSQL image, matching the major version upstream's own compose stack ships. |
| postgresql.image.tag | string | `"18.6@sha256:ae6c78831cbc35fa3a4aaf4d763ddacf6183d6004774cc2dc28b3920410d1d1a"` | Image tag, pinned by digest. Changing the *major* version in place does not work: PostgreSQL refuses to start on a data directory written by a different major, and this chart runs no `pg_upgrade`. |
| postgresql.persistence | object | `{"enabled":true,"existingClaim":"","size":"8Gi","storageClassName":""}` | Storage for the database. This is the one volume in the release whose loss cannot be recovered from the documents themselves. |
| postgresql.persistence.enabled | bool | `true` | Create a PersistentVolumeClaim. Disabled means an emptyDir, and every restart is an empty paperless with the documents still on the media volume and nothing indexing them. |
| postgresql.persistence.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of the StatefulSet's own. |
| postgresql.persistence.size | string | `"8Gi"` | The storage size requested for the volume. |
| postgresql.persistence.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default. |
| postgresql.resources | object | `{}` | Explicit resource requests and limits for the database container. |
| postgresql.resourcesPreset | string | `"medium"` | Named resource sizing for the database container. Ignored when `postgresql.resources` is set. |
| priorityClassName | string | `""` | Optional Kubernetes PriorityClass name. |
| readinessProbe | object | `{"enabled":true,"failureThreshold":3,"httpGet":{"path":"{{ .Values.paperless.rootPath }}/","port":"http"},"periodSeconds":15,"timeoutSeconds":5}` | Readiness probe. Takes the pod out of the Service while it cannot serve requests. |
| readinessProbe.enabled | bool | `true` | Enable the readiness probe. |
| readinessProbe.failureThreshold | int | `3` | Consecutive failures before the pod is taken out of the Service. |
| readinessProbe.httpGet | object | `{"path":"{{ .Values.paperless.rootPath }}/","port":"http"}` | HTTP handler for the probe. |
| readinessProbe.httpGet.path | string | `"{{ .Values.paperless.rootPath }}/"` | Path to request. |
| readinessProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| readinessProbe.periodSeconds | int | `15` | Probe interval. |
| readinessProbe.timeoutSeconds | int | `5` | Probe timeout. |
| redis | object | `{"database":0,"existingSecretKey":"redis-password","host":"","password":"","port":6379,"prefix":"","tls":false,"url":"","username":""}` | The Redis-protocol broker paperless-ngx uses to hand work from the webserver and the directory watcher to the task workers. It is not optional: without it the application starts and then consumes nothing. |
| redis.database | int | `0` | Redis database index. |
| redis.existingSecretKey | string | `"redis-password"` | Key inside `existingSecret` that holds the broker password. |
| redis.host | string | `""` | Broker host. Empty resolves to the bundled Valkey when `valkey.enabled` is set. |
| redis.password | string | `""` | Broker password. Ignored when `existingSecret` is set. It is injected into the URL at container start rather than written into the ConfigMap, so it never appears in `kubectl describe`. Passwords containing `@`, `/` or `:` must be percent-encoded, because the value becomes part of a URL. |
| redis.port | int | `6379` | Broker port. |
| redis.prefix | string | `""` | Prefix for every key and channel, so several installs can share one broker without consuming each other's tasks. |
| redis.tls | bool | `false` | Connect with TLS (`rediss://`). |
| redis.url | string | `""` | Full broker URL (`redis://host:6379/0`, `rediss://…` for TLS), overriding every field below. Use it for a managed service whose URL carries options this chart does not model. A password embedded here ends up in the ConfigMap — put it in `redis.password` instead, which keeps it in the Secret. |
| redis.username | string | `""` | Broker username, for a server with ACLs configured. |
| replicaCount | int | `1` | Number of replicas. paperless-ngx is a singleton: the pod also runs the scheduler and the directory watcher, so a second replica double-schedules every periodic task and races the first one for files in the consumption directory. Only `0` (stopped) and `1` are meaningful. |
| resources | object | `{"limits":{"memory":"2Gi"},"requests":{"cpu":"250m","memory":"512Mi"}}` | Resource requests and limits for the paperless-ngx container. It runs the webserver, the directory watcher, the scheduler and the OCR workers in one pod, so the limit has to cover the worst document anyone feeds it — not the idle footprint, which is a tenth of this. |
| resources.limits | object | `{"memory":"2Gi"}` | Resource limits. |
| resources.limits.memory | string | `"2Gi"` | Maximum allowed memory usage. Raise it alongside `paperless.tasks.workers`: each worker processes a whole document, and a large colour scan is hundreds of megabytes of rasterised pages. No CPU limit is set on purpose — throttling OCR only makes it slower, while a memory limit is what protects the node. |
| resources.requests | object | `{"cpu":"250m","memory":"512Mi"}` | Resource requests. |
| resources.requests.cpu | string | `"250m"` | Minimum CPU requested. OCR is CPU-bound and bursty; this reserves enough to keep the web UI responsive while a document is being processed. |
| resources.requests.memory | string | `"512Mi"` | Minimum guaranteed memory allocation. |
| resourcesPreset | string | `""` | Named resource sizing for the paperless-ngx container. Ignored when `resources` is set, which it is by default — no preset is large enough for OCR. |
| restore | object | `{"batchSize":500,"enabled":false,"existingSecretKey":"import-passphrase","markerFile":".helm-restore-complete","passphrase":"","resources":{},"resourcesPreset":"medium","runMigrations":true,"source":{"checksum":"","existingClaim":"","subPath":"","url":"","urlExistingSecretKey":"restore-url","useExportVolume":false}}` | Importing an export produced by `document_exporter`, whether from a backup of this instance or from a different paperless-ngx altogether.  This is deliberately not a Job. `document_importer` needs the media and data directories to itself and has to finish before anything else touches them, which no separately-scheduled pod can promise — so the import runs as an `initContainer` on the application pod, ahead of the webserver, the consumer and the scheduler. That gives it exclusive access without a second copy of the volumes, works under `ReadWriteOnce`, and fails the pod rather than starting a half-imported archive.  It runs once, guarded by a marker file on the data volume, and is a no-op on every start after that — so these values can be left in place instead of being removed in a follow-up upgrade.  Importing a `.zip` export extracts it in full to the scratch directory first, so `persistence.scratchSizeLimit` has to be at least the archive's uncompressed size or the pod is evicted part-way through the import. A directory export carries no such requirement. |
| restore.batchSize | int | `500` | `--batch-size`: database records inserted per batch. Lower it when the import container is OOM-killed part-way through a large archive. |
| restore.enabled | bool | `false` | Run the import on the next start of the application pod.  **The target must be an empty installation.** The importer refuses to run against a database that already holds users or documents, so this belongs on a fresh release rather than on one that has been serving for a while. To restore over an existing instance, delete the media and data volumes first, so there is nothing left to refuse.  **The image version must match the one that produced the export.** An export carries an exact image of the database, and the schema changes between releases; the importer warns about a mismatch and then keeps going, which is the failure mode that costs an archive. Pin `image.tag` to the source instance's version, import, then upgrade. |
| restore.existingSecretKey | string | `"import-passphrase"` | Key inside `existingSecret` that holds the import passphrase. |
| restore.markerFile | string | `".helm-restore-complete"` | Name of the marker file written to the data volume once the import succeeds. Its presence is what makes the import run exactly once; deleting it re-arms the import on the next restart, which then fails against the now non-empty installation unless the volumes were cleared too. |
| restore.passphrase | string | `""` | Passphrase the export was encrypted with, matching `backup.passphrase` on the instance that produced it. Ignored when `existingSecret` is set. Passed through the environment rather than on the command line. |
| restore.resources | object | `{}` | Explicit resource requests and limits for the import container. |
| restore.resourcesPreset | string | `"medium"` | Named resource sizing for the import container. Ignored when `restore.resources` is set. |
| restore.runMigrations | bool | `true` | Run `manage.py migrate` before importing. Required and on by default: `document_importer` inserts rows and does not create the schema, so against a genuinely empty database it fails on the first table. Turn it off only when something else has already migrated. |
| restore.source | object | `{"checksum":"","existingClaim":"","subPath":"","url":"","urlExistingSecretKey":"restore-url","useExportVolume":false}` | Where the export to import lives. |
| restore.source.checksum | string | `""` | SHA-256 of the archive at `restore.source.url`, as the bare hex digest. Verified before the import and after a resumed download, so a truncated or substituted archive fails the pod instead of importing as far as it can and stopping.  Not a credential: it is a checksum, and it goes in the ConfigMap alongside the other settings. Empty skips verification, which is the wrong default for anything fetched over a network you do not own. |
| restore.source.existingClaim | string | `""` | Name of an existing PersistentVolumeClaim holding the export. Required unless `restore.source.useExportVolume` is set — the chart cannot import from a volume that does not exist. |
| restore.source.subPath | string | `""` | Path within the source volume holding the export — the directory that contains `manifest.json`, or a `.zip` file produced by `backup.mode: zip`. Empty imports from the root of the volume. Not used with `restore.source.url`, which names the archive itself. |
| restore.source.url | string | `""` | URL of a `.zip` export to download and import, instead of reading one from a volume. For the case where the export is already off-cluster — object storage, another cluster's backup bucket, a release asset — and staging it into a PersistentVolume by hand first is the only step in the way.  It is fetched into the export volume before the import, which is why `persistence.export.enabled` is required: it needs somewhere with room for the whole archive, and a download that survives a retry rather than starting over. Written to a temporary name and moved into place only once complete, so an interrupted transfer is never mistaken for a finished one.  **Treated as a credential and stored in the Secret, not the ConfigMap.** The URLs that make this useful are presigned ones — S3, GCS, an Azure SAS link — and the signature in the query string *is* the authorisation. Ignored when `existingSecret` is set; put it there under `restore.source.urlExistingSecretKey` instead.  Only `https://` and `http://` are accepted. With a NetworkPolicy in force the download is subject to the application's own egress rules, so an internet-hosted archive needs `networkPolicy.egress.https`. |
| restore.source.urlExistingSecretKey | string | `"restore-url"` | Key inside `existingSecret` that holds the download URL. |
| restore.source.useExportVolume | bool | `false` | Import from this release's own export volume instead of a separate claim, for the case where `backup` wrote it and the instance is being rebuilt in place. |
| revisionHistoryLimit | int | `3` | Number of old ReplicaSets retained for rollback. |
| securityContext | object | `{"readOnlyRootFilesystem":false}` | Container security context, merged over the preset. `readOnlyRootFilesystem` is deliberately turned back off: the image boots under s6-overlay, which refuses to start unless `/run` is writable and either owned by the UID it runs as or world-writable. An `emptyDir` is always created owned by uid 0, `fsGroup` moves only its group, and `emptyDir` has no `defaultMode` — so no volume can satisfy it, and a read-only root filesystem leaves it nothing else to write to. The rest of the baseline still applies: non-root, all capabilities dropped, no privilege escalation, `seccompProfile: RuntimeDefault`. Setting this back to `true` fails the render with an explanation rather than producing a crash loop. |
| securityContext.readOnlyRootFilesystem | bool | `false` | Whether the container's root filesystem is immutable. Must stay `false`; see above. |
| securityContextPreset | string | `"restricted"` | Container security context baseline. `restricted` drops all Linux capabilities and forbids privilege escalation, running as root and a writable root filesystem. |
| service.annotations | object | `{}` | Annotations for the Service, e.g. cloud load balancer configuration. |
| service.ipFamilyPolicy | string | `""` | IP family policy for the Service. |
| service.loadBalancerIP | string | `""` | Static IP to request for a LoadBalancer service. |
| service.loadBalancerSourceRanges | list | `[]` | CIDRs allowed to reach the LoadBalancer. Empty means everywhere. |
| service.nodePort | int | `0` | Node port to pin under `NodePort` or `LoadBalancer`. `0` lets Kubernetes allocate one. |
| service.port | int | `8000` | Port the Service listens on. |
| service.sessionAffinity | string | `""` | Session affinity. Irrelevant while `replicaCount` is 1, which it has to be. |
| service.type | string | `"ClusterIP"` | Service type. `ClusterIP` is right for the normal arrangement, where an Ingress or a Gateway publishes the application and is the only thing that terminates TLS. |
| serviceAccount.annotations | object | `{}` | Additional annotations for the service account. |
| serviceAccount.automountToken | bool | `false` | Whether to automount the service account token. |
| serviceAccount.create | bool | `true` | Whether to create a dedicated service account. |
| serviceAccount.name | string | `""` | Custom service account name (auto-generated if empty). |
| startupProbe | object | `{"enabled":true,"failureThreshold":60,"httpGet":{"path":"{{ .Values.paperless.rootPath }}/","port":"http"},"initialDelaySeconds":15,"periodSeconds":10,"timeoutSeconds":5}` | Startup probe. Generous on purpose: the first start runs database migrations, builds the search index and creates the superuser before the webserver accepts anything. |
| startupProbe.enabled | bool | `true` | Enable the startup probe. |
| startupProbe.failureThreshold | int | `60` | Consecutive failures before the container is considered failed. 60 x 10s covers a ten-minute first start, which a large migration on slow storage genuinely takes. |
| startupProbe.httpGet | object | `{"path":"{{ .Values.paperless.rootPath }}/","port":"http"}` | HTTP handler for the probe. The path follows `paperless.rootPath`; an unauthenticated request there is redirected to the login page, and a redirect counts as success. |
| startupProbe.httpGet.path | string | `"{{ .Values.paperless.rootPath }}/"` | Path to request. |
| startupProbe.httpGet.port | string | `"http"` | Named container port to probe. |
| startupProbe.initialDelaySeconds | int | `15` | Delay before the first probe. |
| startupProbe.periodSeconds | int | `10` | Probe interval. |
| startupProbe.timeoutSeconds | int | `5` | Probe timeout. |
| strategy | object | `{}` | Deployment update strategy. Empty falls back to `Recreate` whenever a ReadWriteOnce volume is attached, because a rolling update cannot hand that volume over. |
| terminationGracePeriodSeconds | int | `120` | Grace period for pod shutdown. Long enough for a task in flight to finish writing rather than leaving a half-processed document behind. |
| tika | object | `{"enabled":false,"gotenberg":{"allowList":"file:///tmp/.*","disableJavaScript":true,"enabled":true,"endpoint":"","image":{"pullPolicy":"","registry":"","repository":"gotenberg/gotenberg","tag":"8.36.0@sha256:87c16b9f364279d321bc9772d31fa58aa6abe036423c270698bd636c3a8e9466"},"resources":{},"resourcesPreset":"large"},"server":{"enabled":true,"endpoint":"","image":{"pullPolicy":"","registry":"","repository":"apache/tika","tag":"3.3.1.0@sha256:90b7fa1dc018434075fce9e1d9b88b1e3d0ea6979d0cf86e116c79a8073ae973"},"resources":{},"resourcesPreset":"large"}}` | Office and e-mail document support, provided by Apache Tika (text extraction) and Gotenberg (rendering to PDF). Without it, `.docx`, `.odt`, `.xlsx` and `.eml` files are rejected at consumption; PDFs and images never need it. |
| tika.enabled | bool | `false` | Enable the Tika parser in paperless-ngx. Both endpoints below have to resolve, whether they are bundled or external — the parser registers itself for those file types and then fails every one of them if the services are not there. |
| tika.gotenberg | object | `{"allowList":"file:///tmp/.*","disableJavaScript":true,"enabled":true,"endpoint":"","image":{"pullPolicy":"","registry":"","repository":"gotenberg/gotenberg","tag":"8.36.0@sha256:87c16b9f364279d321bc9772d31fa58aa6abe036423c270698bd636c3a8e9466"},"resources":{},"resourcesPreset":"large"}` | The Gotenberg server, which renders office documents and e-mail to PDF using LibreOffice and Chromium. |
| tika.gotenberg.allowList | string | `"file:///tmp/.*"` | Regular expression limiting what Chromium may load. The default confines it to the temporary directory the conversion request itself wrote, so tracking pixels, remote images and in-cluster URLs are all unreachable. |
| tika.gotenberg.disableJavaScript | bool | `true` | Disable JavaScript in the Chromium used to render `.eml` files. Leave it on: an e-mail is untrusted input, and a headless browser executing whatever it contains is a server-side request forgery primitive with a document library attached. |
| tika.gotenberg.enabled | bool | `true` | Run Gotenberg as part of this release. Ignored when `tika.gotenberg.endpoint` names an external one. |
| tika.gotenberg.endpoint | string | `""` | URL of an external Gotenberg server (`http://gotenberg.example.svc:3000`). |
| tika.gotenberg.image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| tika.gotenberg.image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| tika.gotenberg.image.repository | string | `"gotenberg/gotenberg"` | Image repository. The default edition, which carries both LibreOffice and Chromium — the trimmed `-chromium` and `-libreoffice` editions each break half of what paperless asks Gotenberg to do. |
| tika.gotenberg.image.tag | string | `"8.36.0@sha256:87c16b9f364279d321bc9772d31fa58aa6abe036423c270698bd636c3a8e9466"` | Image tag, pinned by digest. |
| tika.gotenberg.resources | object | `{}` | Explicit resource requests and limits for the Gotenberg container. |
| tika.gotenberg.resourcesPreset | string | `"large"` | Named resource sizing. Ignored when `tika.gotenberg.resources` is set. LibreOffice and Chromium are both memory-hungry; below `medium` conversions start failing rather than slowing down. |
| tika.server | object | `{"enabled":true,"endpoint":"","image":{"pullPolicy":"","registry":"","repository":"apache/tika","tag":"3.3.1.0@sha256:90b7fa1dc018434075fce9e1d9b88b1e3d0ea6979d0cf86e116c79a8073ae973"},"resources":{},"resourcesPreset":"large"}` | The Apache Tika server, which extracts text and metadata from office documents. |
| tika.server.enabled | bool | `true` | Run Tika as part of this release. Ignored when `tika.server.endpoint` names an external one. |
| tika.server.endpoint | string | `""` | URL of an external Tika server (`http://tika.example.svc:9998`). Set this to use one you already run, and the bundled server is not created. |
| tika.server.image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| tika.server.image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| tika.server.image.repository | string | `"apache/tika"` | Image repository. The plain image, not `-full`: paperless needs text extraction from office formats, and the extra parsers in the full image cost roughly a gigabyte. |
| tika.server.image.tag | string | `"3.3.1.0@sha256:90b7fa1dc018434075fce9e1d9b88b1e3d0ea6979d0cf86e116c79a8073ae973"` | Image tag, pinned by digest. |
| tika.server.resources | object | `{}` | Explicit resource requests and limits for the Tika container. |
| tika.server.resourcesPreset | string | `"large"` | Named resource sizing. Ignored when `tika.server.resources` is set. The JVM sizes its heap from the memory limit, so shrinking this shrinks the largest document Tika can parse. |
| tolerations | list | `[]` | Tolerations for pod assignment. |
| topologySpreadConstraints | list | `[]` | Pod topology spread constraints for availability. |
| valkey | object | `{"enabled":true,"image":{"pullPolicy":"","registry":"","repository":"valkey/valkey","tag":"9.1.1-alpine@sha256:ee91f7a174ac4d6a6b0685b3a60e321f0a9dbbb691f9b0e285be2ba1d1be8328"},"persistence":{"enabled":false,"existingClaim":"","size":"1Gi","storageClassName":""},"resources":{},"resourcesPreset":"small"}` | A single-instance Valkey bundled with the release. Valkey is the Redis fork upstream's own compose stack moved to. Evaluation tier, like the bundled PostgreSQL: one replica, no failover. |
| valkey.enabled | bool | `true` | Run Valkey as part of this release. On by default, because paperless-ngx cannot process a single document without a broker and an install that silently does nothing is a worse default than one extra pod. |
| valkey.image.pullPolicy | string | `""` | The image pull policy. Empty resolves automatically from the tag/digest. |
| valkey.image.registry | string | `""` | Registry host. Empty means Docker Hub. |
| valkey.image.repository | string | `"valkey/valkey"` | Image repository. |
| valkey.image.tag | string | `"9.1.1-alpine@sha256:ee91f7a174ac4d6a6b0685b3a60e321f0a9dbbb691f9b0e285be2ba1d1be8328"` | Image tag, pinned by digest. |
| valkey.persistence | object | `{"enabled":false,"existingClaim":"","size":"1Gi","storageClassName":""}` | Storage for the broker. |
| valkey.persistence.enabled | bool | `false` | Persist the queue across restarts. Off by default: what lives here is a work queue, and the cost of losing it is that documents already sitting in the consumption directory are re-consumed on the next scan — while persisting it means a poison task survives every restart. Turn it on when the consumption directory is a hand-fed drop box rather than a watched share. |
| valkey.persistence.existingClaim | string | `""` | Use an existing PersistentVolumeClaim instead of the StatefulSet's own. |
| valkey.persistence.size | string | `"1Gi"` | The storage size requested for the volume. |
| valkey.persistence.storageClassName | string | `""` | StorageClass for the claim. Empty uses the cluster default. |
| valkey.resources | object | `{}` | Explicit resource requests and limits for the broker container. |
| valkey.resourcesPreset | string | `"small"` | Named resource sizing for the broker container. Ignored when `valkey.resources` is set. |

## Source Code

* <https://github.com/paperless-ngx/paperless-ngx>
* <https://github.com/gotenberg/gotenberg>
* <https://github.com/apache/tika>

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| Tim Schönle | <contact@tim-schoenle.de> |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
