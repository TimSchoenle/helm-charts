# Upgrading the teamspeak chart

Migration notes, newest first. Only the versions listed here need anything beyond
`helm upgrade`; everything in between is an image or dependency bump.

| Version | Applies to | Step |
|---|---|---|
| [3.0.0](#300) | releases that set `metrics.dashboards.*` | rename the key to `metrics.dashboard` |
| [3.0.0](#300) | releases that set `metrics.dashboards.annotations.grafana_folder` | set `metrics.dashboard.folder` instead |
| [3.0.0](#300) | releases that set `metrics.prometheusRule.rules.*` | replace with the `disabledAlerts`, `severityOverrides`, `forOverrides` and `thresholds` presets |
| [3.0.0](#300) | releases that set `metrics.prometheusRule.additionalRules` | move the rules into `metrics.prometheusRule.additionalRuleGroups` |

The values contract is enforced by `values.schema.json`, so a key this major removed or renamed
fails the render with the offending path named, rather than being silently ignored.

## 3.0.0

Nothing outside `metrics` changed. A release that has never switched the exporter on needs no
change at all, and one that runs the exporter but neither the rules nor the dashboard only needs
the `metrics.dashboards` rename if it set the key.

### The alerting rules moved out of the template

Until now the five alerts were written inline in `templates/prometheusrule.yaml`, one `{{ if }}`
block per alert, each with an `enabled`, a `for`, a `severity` and sometimes a threshold under
`metrics.prometheusRule.rules`. They now live in [`rules/`](rules) as plain Prometheus rule files
and are rendered by the same library partial the other charts in this repository use.

The alerts themselves are unchanged in intent, and the values that tuned them all have a
replacement — but they are different keys, so this is a major.

| Before | After |
|---|---|
| `rules.<name>.enabled: false` | `disabledAlerts: [<AlertName>]` |
| `rules.<name>.severity` | `severityOverrides: {<AlertName>: ...}` |
| `rules.<name>.for` | `forOverrides: {<AlertName>: ...}` |
| `rules.slotsNearlyFull.threshold` | `thresholds: {TeamSpeakSlotsNearlyFull: {ratio: ...}}` |
| `rules.serverRestarted.thresholdSeconds` | `thresholds: {TeamSpeakServerRestarted: {seconds: ...}}` |
| `additionalRules: [...]` | `additionalRuleGroups: [{name: ..., rules: [...]}]` |

The lower-case rule keys map to alert names as follows: `exporterDown` →
`TeamSpeakExporterDown`, `serverOffline` → `TeamSpeakServerOffline`, `serverRestarted` →
`TeamSpeakServerRestarted`, `slotsNearlyFull` → `TeamSpeakSlotsNearlyFull`,
`queryCommandFailures` → `TeamSpeakQueryCommandFailures`.

```yaml
# Before
metrics:
  prometheusRule:
    enabled: true
    rules:
      serverRestarted:
        enabled: false
      slotsNearlyFull:
        threshold: 0.95
        severity: critical
    additionalRules:
      - alert: TeamSpeakSiteRule
        expr: ts3_serverinfo_online == 0
        for: 1h
        labels:
          severity: info

# After
metrics:
  prometheusRule:
    enabled: true
    disabledAlerts:
      - TeamSpeakServerRestarted
    thresholds:
      TeamSpeakSlotsNearlyFull:
        ratio: 0.95
    severityOverrides:
      TeamSpeakSlotsNearlyFull: critical
    additionalRuleGroups:
      - name: teamspeak-site
        rules:
          - alert: TeamSpeakSiteRule
            expr: ts3_serverinfo_online == 0
            for: 1h
            labels:
              severity: info
```

Why it was worth a major rather than an alias: rules written inside a Go template are rules
`promtool` never sees. The repository's rule gate discovers test suites by the presence of
`charts/<chart>/rules/*.yml`, so this chart was skipped by `just test-rules` entirely and reported
as shipping *zero* alerts by `just audit-observability` — while shipping five. It now has a
promtool suite, the runbook-label audit, the scope audit and the same preset validation as
everything else: a preset naming an alert the chart does not ship is refused at render time with
the list of names that would have worked, instead of being quietly ignored.

Three behaviour changes came with the move:

- **`TeamSpeakQueryCommandFailures` counts instead of rating.** It compared
  `rate(ts3_client_commands_failed_total[15m]) > 0`, which one refused command holds above zero
  for a full fifteen minutes — so a single transient failure during a reconnect paged. It now
  compares `increase(...[15m])` against a tunable count, defaulting to three.
- **Two recording rules are new**: `namespace_virtualserver:ts3_slots_used:ratio` and
  `namespace_pod:ts3_client_commands_failed:increase15m`. They are what the two alerts above read,
  so the number an alert fires on and the number you can graph are the same one.
- **`TeamSpeakQueryCommandFailures` reports `pod` rather than the release name.** Its runbook
  interpolates `{{ $labels.pod }}` into a `kubectl logs` command, which the old inline form could
  not do because the expression aggregated nothing.

### `metrics.prometheusRule.scope`

New, defaulting to `release`, which reproduces exactly what the inline rules did: every selector
is rewritten to `namespace="<release namespace>", pod=~"<fullname>-.*"`. `namespace` widens it to
every TeamSpeak in the namespace and `none` leaves the rules unscoped, which makes this release
alert on every other TeamSpeak in the cluster. Nothing needs setting to keep today's behaviour.

### `metrics.dashboards` is now `metrics.dashboard`

Singular, matching the other charts in this repository, and it gained the grafana-operator v5
delivery path they already had.

```yaml
# Before
metrics:
  dashboards:
    enabled: true
    namespace: monitoring
    annotations:
      grafana_folder: TeamSpeak

# After
metrics:
  dashboard:
    enabled: true
    namespace: monitoring
    folder: TeamSpeak
```

`annotations` is gone as a general escape hatch and `folder` replaces the one thing it was there
for. It sets the sidecar's `grafana_folder` annotation *and* the operator's `spec.folder`, since
an operator asking for a folder means it of whichever mechanism is installed;
`metrics.dashboard.grafanaOperator.folder` overrides it for the operator path alone. Arbitrary
annotations on the ConfigMap are still reachable through `commonAnnotations`.

`metrics.dashboards.namespace` carries over unchanged as `metrics.dashboard.namespace`, and now
moves the `GrafanaDashboard` resources with it — `configMapRef` resolves in the custom resource's
own namespace, so the two cannot be split.

### The dashboard itself

`teamspeak.json` gained an alert overlay scoped to the selected namespace, a folder link to the
other dashboards, descriptions on the four panels that lacked them, and `$__rate_interval` in
place of the hardcoded `[5m]` windows — which were producing empty graphs at wide time ranges. It
is also no longer `editable`: an edit made in Grafana was discarded without warning on the
sidecar's next reload of the ConfigMap.
