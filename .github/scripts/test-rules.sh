#!/usr/bin/env bash
# Evaluate every chart's Prometheus rules against synthetic series with `promtool`.
#
# `helm unittest` can prove a chart emits a PrometheusRule; it cannot prove the PromQL inside it is
# correct, and a rule that is syntactically valid but silently matches nothing is the failure mode
# this whole gate exists to catch. So the rules are run: fed fabricated series and checked for
# whether each alert fires when it should and stays quiet when it should not.
#
# Two forms are checked per chart, because a chart ships one and installs the other:
#
#   raw       `charts/<chart>/rules/*.yml` as committed, carrying the scope placeholder. This is
#             what a reader edits.
#   rendered  the `PrometheusRule` as `helm template` produces it, with the placeholder replaced
#             by a real `namespace="..."` matcher. This is what a cluster evaluates, and it is a
#             different string — so validating only the raw form leaves the substitution untested.
#
# Charts are discovered, not listed: any chart with a `rules/` directory is picked up, and
# `audit-observability.py` fails the run if one grows rules without a matching test suite under
# `.github/testdata/<chart>-rules/`.
#
# promtool comes from the official Prometheus image, so there is no binary to pin a checksum for.
#
# Usage: .github/scripts/test-rules.sh
#        PROM_IMAGE=prom/prometheus:v3.7.3 .github/scripts/test-rules.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
testdata="${repo_root}/.github/testdata"
prom_image="${PROM_IMAGE:-prom/prometheus:v3.7.3}"

# CI runners ship `python3`; a Git Bash shell on Windows commonly has only `python`, and often
# also a `python3` on PATH that is the Microsoft Store's install stub rather than an interpreter.
# So each candidate is executed rather than merely located.
python_bin=""
for candidate in python3 python; do
  if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c "import yaml" >/dev/null 2>&1
  then
    python_bin="${candidate}"
    break
  fi
done
if [ -z "${python_bin}" ]; then
  echo "error: no working python with PyYAML on PATH (tried python3, python)" >&2
  exit 1
fi

# The namespace the rendered rules are scoped to. `scoping_test.yml` asserts that series from any
# other namespace are ignored, so this value is part of the contract and is named there too.
render_namespace="tankovault-test"

# The static audit covers every chart at once, including those with dashboards but no rules.
echo "==> Auditing rules, runbooks, dashboards and test coverage"
"${python_bin}" "${repo_root}/.github/scripts/audit-observability.py" \
  --charts "${repo_root}/charts" \
  --testdata "${testdata}"

found_any=0
for chart_dir in "${repo_root}"/charts/*/; do
  chart="$(basename "${chart_dir}")"
  compgen -G "${chart_dir}rules/*.yml" >/dev/null || continue
  found_any=1
  suite="${testdata}/${chart}-rules"

  echo "==> ${chart}: staging rules"
  work="$(mktemp -d)"
  # shellcheck disable=SC2064 # expand now: the loop rebinds ${work} on the next iteration
  trap "rm -rf '${work}'" EXIT

  cp "${chart_dir}"rules/*.yml "${work}/"

  # `helm template` needs the library dependency resolved and the operator CRD declared, since the
  # chart refuses to render its Prometheus objects rather than dropping them silently.
  helm dependency build "${chart_dir}" >/dev/null 2>&1 || true
  helm template "${chart}" "${chart_dir}" \
    --namespace "${render_namespace}" \
    --api-versions monitoring.coreos.com/v1 \
    --set metrics.enabled=true \
    --set metrics.prometheusRule.enabled=true \
    --set postgresql.enabled=true \
    --set nats.enabled=true \
    --set services.sync.enabled=false \
    --set services.challengeSolver.enabled=false \
    > "${work}/rendered-manifest.yaml"

  "${python_bin}" - "${work}/rendered-manifest.yaml" "${work}/rendered.rules.yml" <<'PY'
import sys, yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")) if d]
rules = [d for d in docs if d.get("kind") == "PrometheusRule"]
if len(rules) != 1:
    sys.exit(f"expected exactly one PrometheusRule in the render, found {len(rules)}")
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    yaml.safe_dump({"groups": rules[0]["spec"]["groups"]}, fh,
                   sort_keys=False, width=10**6, allow_unicode=True)
PY

  cp "${suite}"/*.yml "${work}/"

  # Docker needs a native path for the bind mount. Under Git Bash the staging directory is an
  # MSYS path (`/tmp/...`) that the daemon cannot resolve, so it is translated; on Linux
  # `cygpath` is absent and the path is already correct.
  mount="${work}"
  if command -v cygpath >/dev/null 2>&1; then
    mount="$(cygpath -w "${work}")"
  fi

  echo "==> ${chart}: checking rule syntax, committed and rendered"
  # shellcheck disable=SC2046 # deliberate word splitting: one argument per rule file
  MSYS_NO_PATHCONV=1 docker run --rm --entrypoint /bin/promtool -v "${mount}:/w" -w /w "${prom_image}" \
    check rules rendered.rules.yml $(cd "${chart_dir}rules" && ls -1 ./*.yml | tr '\n' ' ')

  echo "==> ${chart}: evaluating rules against synthetic series"
  # shellcheck disable=SC2046 # deliberate word splitting: one argument per test file
  MSYS_NO_PATHCONV=1 docker run --rm --entrypoint /bin/promtool -v "${mount}:/w" -w /w "${prom_image}" \
    test rules $(cd "${suite}" && ls -1 ./*.yml | tr '\n' ' ')

  rm -rf "${work}"
done

if [ "${found_any}" -eq 0 ]; then
  echo "==> no chart ships Prometheus rules; nothing to evaluate"
fi

echo "==> OK"
