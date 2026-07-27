#!/usr/bin/env bash
# Render every application chart against every one of its ci/ values files into a directory,
# one file per (chart, values) pair. Used by the policy scan, and handy locally.
#
# Usage: .github/scripts/render-charts.sh <output-dir>
set -euo pipefail

out="${1:?usage: render-charts.sh <output-dir>}"
mkdir -p "$out"

for chart in charts/*/; do
  name="$(basename "$chart")"
  # A library chart renders nothing on its own.
  [ "$name" = "common" ] && continue

  for values in "$chart"ci/*.yaml; do
    [ -e "$values" ] || continue
    target="$out/${name}--$(basename "$values")"
    echo "Rendering ${name} with $(basename "$values")"
    helm template "$name" "$chart" --namespace default --values "$values" > "$target"
  done
done
