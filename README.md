<!--
Template for the repository README. CI renders it on every pull request and commits the
result to README.md, so edit this file — never README.md itself.

Variables come from .github/scripts/chart-index.py, which reads every charts/*/Chart.yaml.
-->
# Helm Charts

Helm charts for the applications and utilities I run on Kubernetes. Every chart is built on
the same `common` library, so they share one values contract, one label scheme and one
security baseline.

## TL;DR

```bash
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
helm install my-release timschoenle/<chart-name> -f values.yaml
```

Requires Kubernetes 1.19+ and Helm 3.0+. CI validates every chart against Helm 3.21 and 4.2.

## Charts

Each chart's README is the reference for its values, its prerequisites and its migration
notes. Follow the link before installing — several charts need a credential or an accepted
licence that `helm install` cannot invent for you.

| Chart | Description |
|-------|-------------|
| [cloudflare-access-webhook-redirect](./charts/cloudflare-access-webhook-redirect) | A Helm chart for deploying the Cloudflare Access Webhook Redirect service. This service acts as an authentication proxy that validates requests using Cloudflare Access Service Auth tokens before forwarding them to target backend services. |
| [mp-stats-legacy-viewer](./charts/mp-stats-legacy-viewer) | MP Stats Legacy Viewer |
| [netcup-offer-bot](./charts/netcup-offer-bot) | This chart deploys the Netcup Offer Bot, which monitors https://www.netcup-sonderangebote.de/ RSS feed and sends notifications to Discord webhooks when new offers are available. |
| [paperless-ngx](./charts/paperless-ngx) | This chart deploys paperless-ngx — a document management system that scans, indexes and archives your paper documents — hardened to the restricted Pod Security Standard, with per-directory persistence, scheduled document_exporter backups and document_importer restores, optional bundled Valkey, PostgreSQL, Gotenberg and Tika, Ingress and Gateway API publishing, Grafana dashboards and Prometheus alerting rules. |
| [portfolio](./charts/portfolio) | Personal portfolio built with Rust (Yew frontend, Axum server). |
| [s3-bucket-perma-link](./charts/s3-bucket-perma-link) | This chart deploys a simple web server that provides permanent links to specific S3 bucket resources. It allows you to define static URL paths that always point to specific files in your S3 buckets. |
| [tankovault](./charts/tankovault) | This chart deploys the full TankoVault manga aggregator stack — frontend, api, control-plane, worker, notifier, sync, challenge-solver and render — hardened to the restricted Pod Security Standard, with file-backed configuration that reloads in place instead of restarting pods, optional bundled PostgreSQL, Valkey, NATS JetStream and TRAWL, and optional Prometheus metrics, alerting rules and Grafana dashboards. |
| [teamspeak](./charts/teamspeak) | This chart deploys a TeamSpeak 3 server hardened to the restricted Pod Security Standard, with optional persistence, an optional Prometheus metrics exporter sidecar, Grafana dashboards and Prometheus alerting rules. |

The [`common`](./charts/common) library chart is not published. It holds the shared
template partials every chart above composes, and is consumed locally via `file://../common`.

Versions are deliberately absent from this table: they change on nearly every pull request,
and a rendered version column would turn each of those into a merge conflict on this one
file. Run `helm search repo timschoenle --versions` for what is published.

## What every chart gives you

**The [restricted Pod Security Standard][pss] by default.** Pods run non-root with no
privilege escalation, all Linux capabilities dropped, a read-only root filesystem,
`seccompProfile: RuntimeDefault` and no ServiceAccount token mounted. Relax any of it per
chart through `podSecurityContextPreset` / `securityContextPreset` and the matching context
values.

**NetworkPolicies that actually restrict.** Opt in with `networkPolicy.enabled`. Every
generated egress rule carries a `to:` selector — DNS to the cluster DNS service, HTTP/HTTPS
to a configurable CIDR that excludes RFC1918 space and the link-local range covering the
cloud instance metadata endpoint. A rule with only `ports:` would permit every destination,
so custom rules must supply their own `to:`.

**Optional observability.** Charts that ship metrics render ServiceMonitors or PodMonitors,
Prometheus rules and Grafana dashboards. All are off by default and all require the relevant
operator CRDs; when those are missing the chart fails the render rather than installing
cleanly and leaving you unmonitored.

[pss]: https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted

## Reading a chart's documentation

```bash
helm show readme timschoenle/<chart-name>   # the chart README, as published
helm show values timschoenle/<chart-name>   # every value with its default
```

## Upgrading

A chart's major version bump means its values contract changed. Migration notes live in that
chart's own README under an `Upgrading` heading; for `tankovault` and `paperless-ngx`, whose
histories are longer, they live in
[charts/tankovault/UPGRADING.md](./charts/tankovault/UPGRADING.md) and
[charts/paperless-ngx/UPGRADING.md](./charts/paperless-ngx/UPGRADING.md).

Every chart also publishes a `values.schema.json`, so a value that a new major removed or
renamed fails at render time with the offending key named, rather than being silently
dropped.

## Working on the charts

`README.md` and `values.schema.json` are generated. Edit the sources instead:

| Generated | Source |
|---|---|
| `charts/<chart>/README.md` | `charts/<chart>/README.md.gotmpl` and `values.yaml` |
| `charts/<chart>/values.schema.json` | `charts/<chart>/values.yaml` |
| `README.md` (this file) | `.github/templates/README.md.hbs` |

CI regenerates all three on every pull request and commits the result back to the branch, so
there is no toolchain to install locally.

Every check CI runs is a [`just`](https://just.systems) recipe, and the workflows invoke those
same recipes rather than their own copy of the logic — so any gate can be reproduced locally with
one command:

```bash
just              # list every recipe, grouped
just deps         # resolve chart dependencies; a fresh clone needs this first
just test-unit    # helm unittest, every chart
just test-rules   # promtool tests for charts that ship Prometheus rules
just lint         # helm lint the library chart, chart-testing lint the rest
just check        # everything CI runs that needs no Kubernetes cluster
```

Recipes live in `justfile` and `just/*.just`, one file per group.

## Reporting issues

Open an [issue](https://github.com/timschoenle/helm-charts/issues) with the chart name and
version, your Kubernetes and Helm versions, the values you installed with, and the output of
`helm template` or the failing pod's logs.
