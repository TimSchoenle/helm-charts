#!/usr/bin/env bash
# Render every application chart against every one of its ci/ values files into a directory,
# one file per (chart, values) pair. Used by the policy scan, and handy locally.
#
# Usage: .github/scripts/render-charts.sh <output-dir>
set -euo pipefail

out="${1:?usage: render-charts.sh <output-dir>}"
mkdir -p "$out"

# `helm template` reports the built-in API surface but no CRDs, and charts that ship custom
# resources refuse to render rather than dropping them silently when the API is missing — a
# silent skip would leave a real install succeeding with the object never created. Declaring the
# CRD APIs here is what tells an offline render that the target cluster has them.
# shellcheck disable=SC2054 # not a list literal
api_versions=(
  --api-versions monitoring.coreos.com/v1
  --api-versions grafana.integreatly.org/v1beta1
)

for chart in charts/*/; do
  name="$(basename "$chart")"
  # A library chart renders nothing on its own.
  [ "$name" = "common" ] && continue

  for values in "$chart"ci/*.yaml; do
    [ -e "$values" ] || continue
    target="$out/${name}--$(basename "$values")"
    echo "Rendering ${name} with $(basename "$values")"
    helm template "$name" "$chart" --namespace default "${api_versions[@]}" --values "$values" > "$target"
  done
done
