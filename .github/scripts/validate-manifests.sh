#!/usr/bin/env bash
# Render every application chart against every one of its ci/ values files for a given
# Kubernetes version and validate the result with kubeconform.
#
# Every combination is checked before the script exits, so one broken chart does not hide
# the rest.
#
# Usage: KUBE_VERSION=1.31.0 .github/scripts/validate-manifests.sh
set -uo pipefail

kube_version="${KUBE_VERSION:?KUBE_VERSION must be set}"

# PodMonitor and other operator CRDs are not part of the Kubernetes API surface, so their
# schemas come from the community catalog.
crd_schema='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

# Charts that ship custom resources refuse to render when the API is missing, rather than
# dropping the objects silently, so an offline render has to declare the CRD APIs the target
# cluster is assumed to have. Without this the objects would never reach kubeconform.
# shellcheck disable=SC2054 # not a list literal
api_versions=(
  --api-versions monitoring.coreos.com/v1
  --api-versions grafana.integreatly.org/v1beta1
)

failed=0

for chart in charts/*/; do
  name="$(basename "$chart")"
  [ "$name" = "common" ] && continue

  for values in "$chart"ci/*.yaml; do
    [ -e "$values" ] || continue

    echo "::group::${name} <- $(basename "$values") (k8s ${kube_version})"
    if ! helm template "$name" "$chart" \
          --namespace default \
          --kube-version "${kube_version}" \
          "${api_versions[@]}" \
          --values "$values" |
        kubeconform \
          -strict \
          -summary \
          -kubernetes-version "${kube_version}" \
          -schema-location default \
          -schema-location "${crd_schema}"; then
      echo "FAILED: ${name} with $(basename "$values")"
      failed=1
    fi
    echo "::endgroup::"
  done
done

exit "$failed"
