# Rule tests

`promtool` evaluations of `charts/teamspeak/rules/*.yml` against synthetic series. Run them with
the rest of the repository's:

```shell
just test-rules teamspeak
```

The runner stages the committed rule files, renders this chart's `PrometheusRule` with
`render-values.yaml`, and runs both forms — because a cluster receives the rendered one, in which
every `teamspeak_scope=~".*"` placeholder has been replaced by
`namespace="rules-test", pod=~"teamspeak-.*"`. Validating only the committed files would leave
that substitution untested.

| File | Rules it exercises |
| --- | --- |
| `availability_test.yml` | `TeamSpeakExporterDown`, `TeamSpeakServerOffline`, `TeamSpeakServerRestarted`, `TeamSpeakQueryCommandFailures` |
| `capacity_test.yml` | `TeamSpeakSlotsNearlyFull` and the ratio it reads |
| `scoping_test.yml` | the scope substitution, against `rendered.rules.yml` |

Until chart 3.0.0 these rules lived inline in `templates/prometheusrule.yaml`. Rules written into
a Go template are rules `promtool` never sees: the runner discovers suites by the presence of
`charts/<chart>/rules/*.yml`, so this chart was skipped by the whole gate and reported as
"0 alerts" by `just audit-observability` while shipping five. That is the hole this directory
closes.

## Conventions

**Assert on `ALERTS`, not on the expression.** The rule manager writes that series as the alerts
evaluate, so a test that queries it exercises the rule exactly as shipped — its expression, its
`for` duration and its labels. A test that pastes the expression into itself still passes after
somebody breaks the real one.

**Assert the full label set.** The labels an alert carries are what its runbook interpolates, and
a rule that quietly stops producing `pod` produces a `kubectl logs` command with a hole in it
rather than an error. `.github/scripts/audit-observability.py` checks the other direction — that
no runbook names a label its expression cannot produce.

**Every alert needs a test.** The audit fails the build for any alert name that appears in no test
file. An untested alert is one whose first real evaluation happens during an incident.

**Prove the negative too.** Each suite asserts both that the alert fires when it should and that a
neighbouring release, a second virtual server or a single transient failure produces nothing.
Two of those are specific to this chart and worth the attention: a TeamSpeak instance hosts
several virtual servers that fail independently, and two releases share a namespace often enough
that `scope: release` rather than `namespace` is the default.
