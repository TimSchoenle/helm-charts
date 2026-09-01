# discord-alertmanager rule tests

promtool unit tests for [`charts/discord-alertmanager/rules/*.yml`](../rules), run by
[`just test-rules`](../../../just/test.just) and by the **Rule & Dashboard Tests** CI job.

Run them from the repository root:

```bash
just test-rules discord-alertmanager
```

Needs Docker (promtool comes from the official Prometheus image), Helm, and a Python with PyYAML.

## Why these exist

`helm unittest` proves the chart *emits* a `PrometheusRule`. It cannot prove the PromQL inside it
is right, and the defects these were written against are all rules that render perfectly and do
nothing:

- an `absent()` guard that fires the moment the target is merely unscraped rather than when the
  counter has genuinely never been incremented;
- a ratio between two `rate()` selectors that returns nothing at all while the denominator is
  zero, which is exactly the quiet period an operator most wants covered;
- a `job` matcher written against the Service name, which stops matching the moment the release
  is renamed.

None of those fail a syntax check.

## How the assertions are written

Every alert assertion queries `ALERTS`, the series the rule manager writes as rules evaluate:

```yaml
- expr: ALERTS{alertname="DiscordAlertmanagerDown", alertstate="firing"}
  eval_time: 8m
  exp_samples:
    - labels: 'ALERTS{alertname="DiscordAlertmanagerDown", alertstate="firing", namespace="rules-test", job="discord-alertmanager", severity="critical"}'
      value: 1
```

The label set has to be complete and exact, which is what makes these tests catch a rule that
fires with the wrong labels — an alert Alertmanager then routes somewhere nobody is looking.

## The two forms of the rules

`availability_test.yml`, `ingest_test.yml` and `delivery_test.yml` run the rule files **as
committed**, carrying the `discord_alertmanager_scope=~".*"` placeholder. That matcher is a
genuine no-op: nothing carries a `discord_alertmanager_scope` label, so `=~".*"` matches every
series.

`scoping_test.yml` runs `rendered.rules.yml` — the `PrometheusRule` as `helm template` produces
it, with the placeholder replaced by `namespace="rules-test"`. The substitution rewrites every
expression in the chart and is therefore a different string from the one the other suites
exercise; validating only the committed form would leave the mechanism that rewrites it entirely
untested.
