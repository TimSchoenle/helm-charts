#!/usr/bin/env bash
# Render every application chart against every one of its ci/ values files into a directory,
# one file per (chart, values) pair.
#
# This is the only place a chart is rendered for inspection: the policy scan lints the output
# and the manifest validation feeds it to kubeconform, so both see byte-identical manifests and
# neither carries its own copy of the render loop. Handy locally for the same reason.
#
# Every pair is attempted before the script exits, so one chart that fails to render does not
# hide the state of the rest.
#
# Usage: bash .github/scripts/render-charts.sh <output-dir> [extra helm args...]
#        bash .github/scripts/render-charts.sh rendered --kube-version 1.34.0
set -euo pipefail

out="${1:?usage: render-charts.sh <output-dir> [extra helm args...]}"
shift

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$out"

api_version_args=()
while IFS= read -r api_version; do
  api_version_args+=(--api-versions "$api_version")
done < <(grep -Ev '^[[:space:]]*(#|$)' "${repo_root}/.github/configs/render-api-versions.txt")

failed=0

for chart in charts/*/; do
  name="$(basename "$chart")"
  # A library chart renders nothing on its own. Detected from `Chart.yaml` rather than matched
  # against a hard-coded name, so a second library chart needs no change here.
  grep -q '^type:[[:space:]]*library' "$chart/Chart.yaml" && continue

  for values in "$chart"ci/*.yaml; do
    [ -e "$values" ] || continue
    target="$out/${name}--$(basename "$values")"
    echo "Rendering ${name} with $(basename "$values")"
    if ! helm template "$name" "$chart" \
          --namespace default \
          "${api_version_args[@]}" \
          --values "$values" \
          "$@" > "$target"; then
      echo "FAILED to render: ${name} with $(basename "$values")"
      failed=1
    fi
  done
done

exit "$failed"
