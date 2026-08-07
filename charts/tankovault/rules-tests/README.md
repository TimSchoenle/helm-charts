# tankovault rule tests

promtool unit tests for [`charts/tankovault/rules/*.yml`](../rules), run by
[`.github/scripts/test-rules.sh`](../../../.github/scripts/test-rules.sh) and by the **Rule &
Dashboard Tests** CI job.

Run them from the repository root:

```bash
.github/scripts/test-rules.sh
```

Needs Docker (promtool comes from the official Prometheus image), Helm, and a Python with PyYAML.

## Why these exist

`helm unittest` proves the chart *emits* a `PrometheusRule`. It cannot prove the PromQL inside it
is right, and every defect these tests were written to catch was a rule that rendered perfectly
and did nothing:

- an alert whose readiness signal came from an exporter the chart does not deploy, so the
  expression was valid and permanently unsatisfiable;
- a ratio between two different counters, which silently dropped the one provider it existed to
  find, because `+` matches on identical label sets;
- a `> 10` threshold on a `histogram_quantile` that cannot return more than 10.

None of those fail a syntax check. All three fail these tests.

## How the assertions are written

Every alert assertion queries `ALERTS`, the series the rule manager writes as rules evaluate:

```yaml
- expr: ALERTS{alertname="TankoVaultDependencyDown", alertstate="firing"}
  eval_time: 8m
  exp_samples:
    - labels: 'ALERTS{alertname="TankoVaultDependencyDown", alertstate="firing", namespace="tv", job="api", dependency="postgres", severity="critical"}'
      value: 1
```

That exercises the rule **as shipped** — its expression, its `for` duration, and its labels. The
tempting alternative, pasting the expression into `promql_expr_test`, tests a copy: break the real
rule and the test still passes.

`alertstate="pending"` is asserted wherever the `for` duration is itself the point, so a fuse
being shortened to nothing is caught.

Runbook prose is deliberately **not** asserted. `alert_rule_test` compares annotations exactly,
which would mean duplicating every runbook here and breaking a logic test on every wording change.
`audit-observability.py` checks the annotations instead, and checks the thing that actually
matters about them — that every `{{ $labels.X }}` names a label the alert can carry.

## Layout

| File | Covers |
|---|---|
| `availability_test.yml` | liveness, readiness, dependencies, rollout convergence |
| `request_path_test.yml` | error ratios, latency quantiles, concurrency |
| `edge_policy_test.yml` | rate limiting, feature gating, authentication, the database pool |
| `scan_pipeline_test.yml` | scheduling, throughput, backlog, providers, content |
| `delivery_test.yml` | notifications, SSE, the fetch tier, AniList |
| `scoping_test.yml` | namespace isolation, against the **rendered** rules |

Test files are `*_test.yml`. Anything else in this directory is configuration, not a suite.

`scoping_test.yml` is the odd one out: it declares `rule_files: [rendered.rules.yml]`, a file the
runner produces by `helm template`-ing the chart into the namespace `rules-test`. The scope
substitution rewrites every expression in the chart, so it is a different string from the one the
other files exercise — validating only the committed form would leave the mechanism untested. If
you change that namespace in the runner, change it here too.

### `render-values.yaml`

The values the runner passes to `helm template` to produce that rendered form. It lives here
rather than in the runner because *which* values switch a chart's PrometheusRule on, and which
credentials its validator refuses to render without, are facts about the chart — a generic runner
has no business knowing them. Every chart's suite supplies its own; a chart whose rules render
under plain defaults needs no such file at all.

Nothing in it has to resolve at runtime, since no pod is ever started.

## Adding a rule

Add the alert, then add a test naming it. The audit fails the build on any alert with no test, so
this is not a convention that can quietly lapse — including for a chart that does not exist yet:
any chart with a `rules/` directory is discovered, and is expected to have a suite at
`.github/testdata/<chart>-rules/`.

Two things worth knowing when writing expectations:

- **Ratios are absent, not zero, when the numerator has no series.** A channel with no `error`
  series produces no ratio at all. That is deliberate — it is what stops an idle service tripping
  a threshold — so expect `exp_samples: []`, not `value: 0`.
- **promtool compares floats exactly.** `1/6` is `0.16666666666666669` here, not
  `0.16666666666666666`. Take the value from the failure output rather than computing it.
