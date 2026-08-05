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
# `audit-observability.py` fails the run if one grows rules without a matching test suite at
# `charts/<chart>/rules-tests/`.
#
# promtool comes from the official Prometheus image, so there is no binary to pin a checksum for.
#
# Usage: .github/scripts/test-rules.sh
#        PROM_IMAGE=prom/prometheus:v3.7.3 .github/scripts/test-rules.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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

# Shared with every other renderer in the repository; see the file's header for why an offline
# render has to declare them.
api_version_args=()
while IFS= read -r api_version; do
  api_version_args+=(--api-versions "${api_version}")
done < <(grep -Ev '^[[:space:]]*(#|$)' "${repo_root}/.github/configs/render-api-versions.txt")

# The namespace the rendered rules are scoped to. Every suite's `scoping_test.yml` asserts that
# series from any other namespace are ignored, so this value is part of the contract between this
# runner and every chart's tests, and is named in them too.
render_namespace="rules-test"

# The static audit covers every chart at once, including those with dashboards but no rules.
echo "==> Auditing rules, runbooks, dashboards and test coverage"
"${python_bin}" "${repo_root}/.github/scripts/audit-observability.py" \
  --charts "${repo_root}/charts"

found_any=0
for chart_dir in "${repo_root}"/charts/*/; do
  chart="$(basename "${chart_dir}")"
  compgen -G "${chart_dir}rules/*.yml" >/dev/null || continue
  found_any=1
  suite="${chart_dir}rules-tests"

  echo "==> ${chart}: staging rules"
  work="$(mktemp -d)"
  # shellcheck disable=SC2064 # expand now: the loop rebinds ${work} on the next iteration
  trap "rm -rf '${work}'" EXIT

  cp "${chart_dir}"rules/*.yml "${work}/"

  # How to render *this* chart with its rules enabled is the chart's business, not this script's:
  # which values switch the PrometheusRule on, and which credentials a validator insists on before
  # it will render at all, differ per chart. The suite supplies them, so nothing chart-specific
  # lives here.
  render_values="${suite}/render-values.yaml"
  values_args=()
  if [ -f "${render_values}" ]; then
    values_args=(--values "${render_values}")
  fi

  # The library dependency has to be resolved before the chart can render at all.
  helm dependency build "${chart_dir}" >/dev/null 2>&1 || true
  helm template "${chart}" "${chart_dir}" \
    --namespace "${render_namespace}" \
    "${api_version_args[@]}" \
    "${values_args[@]}" \
    > "${work}/rendered-manifest.yaml"

  "${python_bin}" - "${work}/rendered-manifest.yaml" "${work}/rendered.rules.yml" \
    "${render_values}" <<'PY'
import sys, yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")) if d]
rules = [d for d in docs if d.get("kind") == "PrometheusRule"]
if len(rules) != 1:
    sys.exit(
        f"expected exactly one PrometheusRule in the render, found {len(rules)}. "
        f"Add or correct {sys.argv[3]}, which supplies the values this chart needs in order to "
        f"render its rules — typically whatever switches the PrometheusRule on, plus any "
        f"credentials the chart's validator refuses to render without."
    )
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    yaml.safe_dump({"groups": rules[0]["spec"]["groups"]}, fh,
                   sort_keys=False, width=10**6, allow_unicode=True)
PY

  cp "${suite}"/*_test.yml "${work}/"

  # The Prometheus image runs as `nobody`, while `mktemp -d` creates a directory only its owner
  # can enter. Docker Desktop's bind mounts ignore unix permissions, so this is invisible on
  # Windows and macOS and fails on a Linux CI runner with `permission denied`.
  chmod -R a+rX "${work}"

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
    test rules $(cd "${suite}" && ls -1 ./*_test.yml | tr '\n' ' ')

  rm -rf "${work}"
done

if [ "${found_any}" -eq 0 ]; then
  echo "==> no chart ships Prometheus rules; nothing to evaluate"
fi

echo "==> OK"
