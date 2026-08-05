#!/usr/bin/env bash
# Validate every chart's rendered output against the Kubernetes schemas for a given version.
#
# The render is `render-charts.sh`, so this script owns only the validation. Both it and the
# policy scan used to carry their own copy of the same loop and had to be kept identical by
# hand; now the manifests kube-linter sees and the manifests kubeconform sees cannot diverge.
# Each rendered file is named `<chart>--<values>.yaml`, so kubeconform's own output identifies
# the offending pair.
#
# kubeconform validates every file before it exits, so one broken chart does not hide the rest.
#
# Usage: KUBE_VERSION=1.31.0 bash .github/scripts/validate-manifests.sh
set -euo pipefail

kube_version="${KUBE_VERSION:?KUBE_VERSION must be set}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# PodMonitor and other operator CRDs are not part of the Kubernetes API surface, so their
# schemas come from the community catalog.
crd_schema='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

rendered="$(mktemp -d)"
trap 'rm -rf "${rendered}"' EXIT

echo "::group::Rendering every chart for Kubernetes ${kube_version}"
render_status=0
bash "${repo_root}/.github/scripts/render-charts.sh" "${rendered}" --kube-version "${kube_version}" \
  || render_status=1
echo "::endgroup::"

echo "Validating rendered manifests against Kubernetes ${kube_version}"
kubeconform \
  -strict \
  -summary \
  -kubernetes-version "${kube_version}" \
  -schema-location default \
  -schema-location "${crd_schema}" \
  "${rendered}"

exit "${render_status}"
