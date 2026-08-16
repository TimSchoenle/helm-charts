# Rule tests

`promtool` evaluations of `charts/paperless-ngx/rules/*.yml` against synthetic series. Run them
with the rest of the repository's:

```shell
bash .github/scripts/test-rules.sh
```

The runner stages the committed rule files, renders this chart's `PrometheusRule` with
`render-values.yaml`, and runs both forms — because a cluster receives the rendered one, in which
every `paperless_ngx_scope=~".*"` placeholder has been replaced by `namespace="rules-test"`.
Validating only the committed files would leave that substitution untested.

| File | Rules it exercises |
| --- | --- |
| `availability_test.yml` | `PaperlessNgxDown`, `PaperlessNgxAbsent`, `PaperlessNgxDatastoreDown` |
| `workload_test.yml` | `PaperlessNgxCrashLooping`, `PaperlessNgxRestarting`, `PaperlessNgxOOMKilled`, `PaperlessNgxVolumeFillingUp`, `PaperlessNgxVolumeAlmostFull` |
| `backup_test.yml` | `PaperlessNgxBackupFailed`, `PaperlessNgxBackupStale`, `PaperlessNgxBackupSuspended` |
| `scoping_test.yml` | the namespace substitution, against `rendered.rules.yml` |

## Conventions

**Assert on `ALERTS`, not on the expression.** The rule manager writes that series as the alerts
evaluate, so a test that queries it exercises the rule exactly as shipped — its expression, its
`for` duration and its labels. A test that pastes the expression into itself still passes after
somebody breaks the real one.

**Assert the full label set.** The labels an alert carries are what its runbook interpolates, and
a rule that quietly stops producing `namespace` produces a runbook with a hole in it rather than
an error. `.github/scripts/audit-observability.py` checks the other direction — that no runbook
names a label its expression cannot produce.

**Every alert needs a test.** The audit fails the build for any alert name that appears in no test
file. An untested alert is one whose first real evaluation happens during an incident.

**Prove the negative too.** Each suite asserts both that the alert fires when it should and that a
neighbouring release, a healthy replica or another application's claim produces nothing. The
name-based selectors these rules depend on make that the interesting half.
