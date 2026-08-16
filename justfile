# Task runner for this chart repository.
#
# Every gate CI runs is a recipe here, and the workflows invoke these recipes rather than keeping
# their own copy of the logic — so what a pull request is checked with is exactly what `just check`
# runs locally, and neither can drift from the other. Recipes are grouped into the files imported
# below; `just --list` shows them by group.
#
# Prerequisites, per group:
#
#   deps, docs, render, test    helm (+ `just plugins`), python3 with PyYAML
#   docs                        helm-docs, for `just chart-readmes`
#   lint                        chart-testing (`ct`), kube-linter
#   render                      kubeconform, for `just validate-manifests`
#   test                        docker, for `just test-rules` and `just test-e2e`
#
# `just plugins` installs the Helm plugins. The rest are external binaries; a recipe that needs
# one and cannot find it fails saying so rather than skipping the check.

set shell := ["bash", "-euo", "pipefail", "-c"]
set windows-shell := ["bash", "-euo", "pipefail", "-c"]

import 'just/deps.just'
import 'just/docs.just'
import 'just/lint.just'
import 'just/render.just'
import 'just/test.just'

# --------------------------------------------------------------------------------------------
# Paths every recipe agrees on
# --------------------------------------------------------------------------------------------

charts := "charts"
configs := ".github/configs"
scripts := ".github/scripts"

# Test-only consumer of the `common` library chart. It lives outside `charts/` so that
# chart-testing and chart-releaser never see it, which is also why no `charts/*` loop reaches it.
fixture := ".github/testdata/common-fixture"

# API groups an offline render has to be told the target cluster provides. Read by `render-chart`,
# and so by every renderer in the repository — the list cannot drift between the policy scan, the
# manifest validation and the rule tests. See the file's own header for why it is needed.
api_versions := configs / "render-api-versions.txt"

# --------------------------------------------------------------------------------------------
# Pinned toolchain
# --------------------------------------------------------------------------------------------

# `just plugins` installs these and the CI composite action calls that recipe, so a plugin bump is
# a single edit here rather than one per workflow.
helm_unittest_version := "1.1.2"
helm_schema_version := "0.18.1"

# --------------------------------------------------------------------------------------------
# Defaults for the parameters CI overrides from a matrix
# --------------------------------------------------------------------------------------------

# Highest version in the validate-manifests matrix; also what the immutable-field check pins the
# StatefulSet schema to.
kube_version := "1.34.0"

# promtool comes from the official Prometheus image, so there is no binary to pin a checksum for.
prom_image := "prom/prometheus:v3.7.3"

# The namespace the rendered Prometheus rules are scoped to. Every suite's `scoping_test.yml`
# asserts that series from any other namespace are ignored and names this value, so it is part of
# the contract between `test-rules` and each chart's tests.
rules_namespace := "rules-test"

# Mirrors `remote`/`target-branch` in ct.yaml — keep the two in step.
target_branch := "origin/main"

# PodMonitor and the other operator CRDs are not part of the Kubernetes API surface, so their
# schemas come from the community catalog. Held as a variable because the `{{ ... }}` placeholders
# kubeconform expects would otherwise be read as just interpolations.
crd_schemas := 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

# --------------------------------------------------------------------------------------------
# Shared shell preamble
# --------------------------------------------------------------------------------------------

# Sets `$python` to an interpreter that actually has PyYAML. CI runners ship `python3`; a Git Bash
# shell on Windows commonly has only `python`, and often also a `python3` on PATH that is the
# Microsoft Store install stub rather than an interpreter. So each candidate is executed rather
# than merely located. Expanded into the recipes that need it.
resolve_python := '''
python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import yaml" >/dev/null 2>&1; then
    python="$candidate"
    break
  fi
done
if [ -z "$python" ]; then
  echo "error: no working python with PyYAML on PATH (tried python3, python)" >&2
  exit 1
fi
'''

# --------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------

[private]
default:
    @just --list

# Every gate CI runs that does not need a Kubernetes cluster; `just test-install` is the rest.
#
# Ordered by what each stage needs rather than by what CI does first — CI runs these as parallel
# jobs, so the order is free here, and putting the helm-only gates ahead of the ones that want
# `ct` and `kube-linter` means a shell without those still gets everything else checked before it
# stops.
[doc("Every gate CI runs that does not need a Kubernetes cluster")]
[group('meta')]
check: deps test validate-manifests check-immutable lint lint-policy

# Install the pinned Helm plugins. The CI composite action calls this recipe too, so the versions
# above are the only place they are declared.
[doc("Install the pinned Helm plugins")]
[group('meta')]
plugins:
    #!/usr/bin/env bash
    set -euo pipefail

    # Helm 4 verifies plugin signatures on install by default and neither plugin publishes a
    # provenance file, so verification has to be disabled there. Helm 3 does not know the flag at
    # all and aborts with "unknown flag: --verify". Probe for the flag rather than branching on the
    # version, so this survives the whole version matrix.
    verify_args=()
    if helm plugin install --help | grep -q -- '--verify'; then
      verify_args=(--verify=false)
    fi

    helm plugin install https://github.com/helm-unittest/helm-unittest \
      --version '{{ helm_unittest_version }}' ${verify_args[@]+"${verify_args[@]}"}

    helm plugin install https://github.com/dadav/helm-schema \
      --version '{{ helm_schema_version }}' ${verify_args[@]+"${verify_args[@]}"}

# Report the installed toolchain.
[group('meta')]
versions:
    @helm version
    @helm plugin list
