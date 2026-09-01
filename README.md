<!--
Generated from .github/templates/README.md.hbs — edit that file, not this one.

The Documentation job in .github/workflows/ci.yaml renders it on every pull request and commits
the result back to the branch. A push to main whose README.md does not match its template fails
the `readme` job in .github/workflows/docs.yaml.

The chart table comes from one command:

    just chart-index

which reads every charts/*/Chart.yaml. The rest of the payload — repository, branch, the library
chart's name and version, and the docs index — is derived from charts/common/Chart.yaml and the
checkout by TimSchoenle/actions/actions/common/readme-variables, so no name, version or
description below is typed by hand.

Nothing in this comment may contain a mustache that is not a real reference.
-->

# helm-charts

Helm charts for the applications and utilities I run on Kubernetes, built on one shared library.

[![Latest chart](https://img.shields.io/github/v/release/TimSchoenle/helm-charts?sort=date&display_name=tag&label=latest%20chart)](https://github.com/TimSchoenle/helm-charts/releases)
[![Publish](https://img.shields.io/github/actions/workflow/status/TimSchoenle/helm-charts/release.yml?branch=main&label=publish)](https://github.com/TimSchoenle/helm-charts/actions/workflows/release.yml)

## What this is

Every one of the 9 application charts here depends on the `common` library chart, which holds the
shared template partials: the pod spec, both security contexts, the NetworkPolicies, the monitoring
objects and the file-backed configuration. A change to the security baseline is written once there
and reaches every chart on its next dependency build.

Packages are published to <https://timschoenle.github.io/helm-charts> by chart-releaser, one GitHub
Release per chart version.

## Quick start

```bash
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
helm install my-release timschoenle/<chart>
```

Several charts need a credential or an accepted licence that `helm install` cannot invent. Read
the chart's own README before installing it.

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Compatibility](#compatibility)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Security](#security)

## Features

### Charts

| Chart | Description |
| --- | --- |
| [cloudflare-access-webhook-redirect](charts/cloudflare-access-webhook-redirect) | A Helm chart for deploying the Cloudflare Access Webhook Redirect service. This service acts as an authentication proxy that validates requests using Cloudflare Access Service Auth tokens before forwarding them to target backend services. |
| [discord-alertmanager](charts/discord-alertmanager) | This chart deploys discord-alertmanager, a Discord operator surface for Prometheus Alertmanager. It receives the version-4 webhook envelope, renders each alert as a live status card in a Discord channel, and lets an operator acknowledge, ignore, silence or investigate it without leaving the client — with file-backed configuration that reloads in place instead of restarting pods, SQLite or PostgreSQL storage, optional Prometheus metrics and alerting rules, and an optional AlertmanagerConfig that registers the receiver with the Prometheus Operator instead of leaving it to be wired by hand. |
| [mp-stats-legacy-viewer](charts/mp-stats-legacy-viewer) | MP Stats Legacy Viewer |
| [netcup-offer-bot](charts/netcup-offer-bot) | This chart deploys the Netcup Offer Bot, which monitors https://www.netcup-sonderangebote.de/ RSS feed and sends notifications to Discord webhooks when new offers are available. |
| [paperless-ngx](charts/paperless-ngx) | This chart deploys paperless-ngx — a document management system that scans, indexes and archives your paper documents — hardened to the restricted Pod Security Standard, with per-directory persistence, scheduled document_exporter backups and document_importer restores, optional bundled Valkey, PostgreSQL, Gotenberg and Tika, Ingress and Gateway API publishing, Grafana dashboards and Prometheus alerting rules. |
| [portfolio](charts/portfolio) | Personal portfolio built with Rust (Yew frontend, Axum server). |
| [s3-bucket-perma-link](charts/s3-bucket-perma-link) | This chart deploys a simple web server that provides permanent links to specific S3 bucket resources. It allows you to define static URL paths that always point to specific files in your S3 buckets. |
| [tankovault](charts/tankovault) | This chart deploys the full TankoVault manga aggregator stack — frontend, api, control-plane, worker, notifier, sync, challenge-solver and render — hardened to the restricted Pod Security Standard, with file-backed configuration that reloads in place instead of restarting pods, optional bundled PostgreSQL, Valkey, NATS JetStream and TRAWL, and optional Prometheus metrics, alerting rules and Grafana dashboards. |
| [teamspeak](charts/teamspeak) | This chart deploys a TeamSpeak 3 server hardened to the restricted Pod Security Standard, with optional persistence, an optional Prometheus metrics exporter sidecar, Grafana dashboards and Prometheus alerting rules. |

[`common`](charts/common) is not published. Consumers pull it from `file://../common`, so a
chart is packaged with the library version its own `Chart.yaml` pinned at release time.

Versions are absent from that table on purpose. They change on nearly every pull request, and a
rendered version column turned this one file into a merge conflict across concurrent bump
branches. `helm search repo timschoenle --versions` lists what is published.

### What every chart gives you

**The [restricted Pod Security Standard][pss] by default.** Pods run non-root with no privilege
escalation, all Linux capabilities dropped, a read-only root filesystem,
`seccompProfile: RuntimeDefault` and no ServiceAccount token mounted. Setting
`podSecurityContextPreset` or `securityContextPreset` to `none` opts out, and the matching
`podSecurityContext` and `securityContext` values merge over whichever preset is in force. The
identity fields stay with the chart, because `runAsUser` has to match the image's own UID.

NetworkPolicies are opt-in through `networkPolicy.enabled`, and every generated egress rule
carries a `to:` selector: DNS to the cluster DNS service, HTTP and HTTPS to a configurable CIDR
that excludes RFC1918 space and `169.254.0.0/16`, the range the cloud instance metadata endpoint
sits in. The API reads a rule listing only `ports:` as permitting every destination, so a custom
rule must supply its own `to:`. Set `networkPolicy.engine` to `cilium` for FQDN and L7 rules, or
to `both` while a cluster migrates between CNIs.

**Monitoring objects that fail loudly.** Charts that ship metrics render ServiceMonitors or
PodMonitors, Prometheus rules and Grafana dashboards. All are off by default and all need the
operator CRDs; when those are missing the render fails instead of installing cleanly and leaving
you unmonitored.

Six charts vendor the configuration contract their image publishes and check the rendered
`config.toml`, every container environment variable and every secret file name against it. A key
the application stopped reading fails the pull request that bumps the digest, rather than starting
a pod that runs on a compiled default nobody chose.

[pss]: https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted

## Installation

From the published repository:

```bash
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
helm install my-release timschoenle/<chart> --version <x.y.z> -f values.yaml
```

From a checkout, which is what CI installs:

```bash
git clone https://github.com/TimSchoenle/helm-charts.git
cd helm-charts
just deps
helm install my-release ./charts/<chart> -f values.yaml
```

`just deps` extracts the `common` library into each chart. A fresh clone cannot render a
chart before it has run.

## Usage

Read a published chart without cloning it:

```bash
helm show readme timschoenle/<chart>
helm show values timschoenle/<chart>
```

A chart's major version bump means its values contract changed. The migration notes sit in that
chart's README under `Upgrading`, except for the two whose histories outgrew it:
[charts/tankovault/UPGRADING.md](charts/tankovault/UPGRADING.md) and
[charts/paperless-ngx/UPGRADING.md](charts/paperless-ngx/UPGRADING.md).

## Configuration

Values are documented where they are declared. Each key in a chart's `values.yaml` carries an
`@schema` block and a `-- ` comment, and from those `just schema` generates `values.schema.json`
while helm-docs generates the chart's README table. A value that a new major renamed or removed
then fails at render time with the offending key named, instead of being dropped in silence.

The keys `common` defines for every chart:

| Key | Purpose |
| --- | --- |
| `podSecurityContextPreset`, `securityContextPreset` | `restricted` or `none`, the baseline each security context merges over |
| `podSecurityContext`, `securityContext` | Kubernetes objects merged over the preset |
| `networkPolicy.*` | Ingress and egress rules, and which policy dialect renders them |
| `resources` | Requests and limits, typed against the Kubernetes schema |
| `metrics.*`, `grafana.*`, `prometheusRule.*` | The monitoring objects, all off by default |

Everything else belongs to the individual chart, whose README lists every value with its default.

## Compatibility

| | Supported |
| --- | --- |
| Helm | 3 and 4 |
| Library chart | `common` 2.4.0 |
| Kubernetes | No chart declares a `kubeVersion`. The render, validation and install matrices in [.github/workflows/ci.yaml](.github/workflows/ci.yaml) state what is tested. |

## Documentation

Every chart's README is the reference for its values, its prerequisites and its migration notes,
and `helm show readme` prints the published copy without a clone. The gates are documented beside
themselves: `justfile` and each file under `just/` open with a header saying what its recipes
prove and why a recipe is or is not part of `just check`.

## Contributing

Issues and pull requests are welcome. Open an issue with the chart name and version, your
Kubernetes and Helm versions, the values you installed with, and the output of `helm template` or
the failing pod's logs.

Three files per chart are generated, and CI regenerates and commits all of them on every pull
request, so contributing a values change needs no toolchain installed:

| Generated | Source |
| --- | --- |
| `charts/<chart>/README.md` | `charts/<chart>/README.md.gotmpl` and `values.yaml` |
| `charts/<chart>/values.schema.json` | the `@schema` blocks in `values.yaml` |
| `README.md` | `.github/templates/README.md.hbs` |

Every gate CI runs is a [`just`](https://just.systems) recipe, and the workflows call those
recipes rather than keeping a second copy of the logic:

```bash
just              # list every recipe, grouped
just deps         # resolve chart dependencies; a fresh clone needs this first
just test-unit    # helm unittest, every chart
just test-rules   # promtool tests for the charts that ship Prometheus rules
just lint         # helm lint the library chart, chart-testing lint the rest
just check        # everything CI runs that needs no cluster and no network
```

## Security

Report a vulnerability in a chart through
[GitHub's advisory form](https://github.com/TimSchoenle/helm-charts/security/advisories/new)
rather than in a public issue. There is no separate reporting address.

A vulnerability in an application one of these charts deploys belongs to that application's own
repository, which the chart's `Chart.yaml` names under `sources`.
