# Task runner for this chart repository.
#
# Every gate CI runs is a recipe here, and the workflows invoke these recipes rather than keeping
# their own copy of the logic — so what a pull request is checked with is exactly what `just check`
# runs locally, and neither can drift from the other. Recipes are grouped into the files imported
# below; `just --list` shows them by group.
#
# Prerequisites, per group:
#
#   contracts                   python3 with PyYAML; terrace-contract, for the gates
#   contracts                   oras and cosign, for `just contracts` alone — the one networked
#                               recipe in the repository, and not part of `just check`
#   deps, docs, render, test    helm (+ `just plugins`), python3 with PyYAML
#   docs                        helm-docs, for `just chart-readmes`
#   lint                        chart-testing (`ct`), kube-linter; ruff, for `just lint-python`
#   maintain                    python3 with PyYAML
#   render                      kubeconform, for `just validate-manifests`
#   test                        docker, for `just test-rules` and `just test-e2e`
#
# `just plugins` installs the Helm plugins. The rest are external binaries; a recipe that needs
# one and cannot find it fails saying so rather than skipping the check.

set shell := ["bash", "-euo", "pipefail", "-c"]
set windows-shell := ["bash", "-euo", "pipefail", "-c"]

import 'just/contracts.just'
import 'just/deps.just'
import 'just/docs.just'
import 'just/lint.just'
import 'just/maintain.just'
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
# renovate: datasource=github-tags depName=helm-unittest/helm-unittest extractVersion=^v(?<version>.*)$
helm_unittest_version := "1.1.2"
# renovate: datasource=github-tags depName=dadav/helm-schema
helm_schema_version := "0.18.1"

# The shared contract toolchain, which is what the gates in `just/contracts.just` now are.
#
# It replaces ~2,000 lines of Python here and the pinned `jv` binary that Python delegated JSON
# Schema to. The rules did not move sideways: they moved *up*, into a binary every implementation
# of the contract format shares, so a rule this repository proved against nine charts is the same
# rule a Java service's build runs. See `docs/contract-cli-plan.md` in TimSchoenle/terrace-config.
#
# Pinned as a single binary by release URL, exactly as `kubeconform` and `ruff` already are, and
# for the reason `jv` was: the scripts here are stdlib + PyYAML, and on the Git Bash shell this
# repository is developed from, an install step that needs a toolchain is the difference between a
# gate that runs locally and one that does not.
#
# `TERRACE_CONTRACT_BIN` overrides it, which is what makes a chart-side fix testable against an
# unreleased build rather than waiting for a release to prove it.
# renovate: datasource=github-tags depName=TimSchoenle/terrace-config extractVersion=^terrace-contract-v(?<version>.*)$
terrace_contract_version := "0.1.0"

# Linter for `.github/scripts`. Pinned as a single binary by release URL for exactly the reason
# `terrace-contract` above is: a `pip install` inside a recipe is the difference between a gate
# that runs on the Git Bash shell this repository is developed from and one that does not. ruff ships that
# way; mypy does not, which is why type checking is not part of the gate — see `just/lint.just`.
#
# Pinned rather than floating because a linter is a gate: a new release that adds a rule would
# turn a pull request red for something its author did not write, and the fix would be a version
# bump made under time pressure rather than a considered one.
# renovate: datasource=github-tags depName=astral-sh/ruff extractVersion=^v?(?<version>.*)$
ruff_version := "0.16.3"

# Registry client and signature verifier for `just contracts`. Only the contract refresh needs
# these — every gate that reads a contract reads the committed file — which is why they are absent
# from the `check` aggregate and from every job but the Documentation one.
# renovate: datasource=github-tags depName=oras-project/oras extractVersion=^v(?<version>.*)$
oras_version := "1.3.4"
# renovate: datasource=github-tags depName=sigstore/cosign
cosign_version := "v3.1.3"

# The workflow identity a contract must be signed by before it will be vendored. Anything else is
# refused: a contract that cannot be proven to belong to the pinned digest is worse than none,
# because every gate downstream would trust it.
#
# Measured against a real signature rather than assumed — the design document's guess was
# `release.yml@refs/tags/.*`, and what `timschoenle/portfolio` is actually signed by is
# release-please's workflow running on `main`. The repository name stays a wildcard because the
# first-party images share one release pipeline; the workflow path and the ref do not, because
# those are what make this a constraint rather than "signed by anyone with a GitHub account".
#
# Both default-branch spellings, because the repositories genuinely differ: `portfolio` and
# `mp-stats-legacy-viewer` release from `main`, while `netcup-offer-bot`,
# `s3-bucket-perma-link` and `cloudflare-access-webhook-redirect` release from `master`. Measured
# against every first-party image this repository pins, not guessed — leaving it at `main` would
# have failed three of the five with a signature error the day someone declared a contract for
# them, which reads nothing like the actual cause.
#
# Signing on a branch rather than a tag is weaker than the design document assumed: any push to
# the default branch of a producing repository can mint a signature this accepts. Tightening it
# is the producer's change to make, not this repository's to work around.
contract_signer := "https://github.com/TimSchoenle/[^/]+/.github/workflows/release-please.yaml@refs/heads/(main|master)"

# --------------------------------------------------------------------------------------------
# Defaults for the parameters CI overrides from a matrix
# --------------------------------------------------------------------------------------------

# Highest version in the validate-manifests matrix; also what the immutable-field check pins the
# StatefulSet schema to, and the release every chart's Kubernetes `$ref` names. The references are
# not derived from this value, so `just sync-kube-refs` is what carries a change here into the
# charts — the CI documentation job runs it, and `just check-kube-refs` reports the gap.
# renovate: datasource=github-tags depName=kubernetes/kubernetes extractVersion=^v(?<version>.*)$
kube_version := "1.34.0"

# promtool comes from the official Prometheus image, so there is no binary to pin a checksum for.
# renovate: datasource=docker depName=prom/prometheus
prom_image := "prom/prometheus:v3.7.3"

# The namespace the rendered Prometheus rules are scoped to. Every suite's `scoping_test.yml`
# asserts that series from any other namespace are ignored and names this value, so it is part of
# the contract between `test-rules` and each chart's tests.
rules_namespace := "rules-test"

# Mirrors `remote`/`target-branch` in ct.yaml — keep the two in step.
target_branch := "origin/main"

# The commit of the community CRD catalog every schema in this repository is read at.
#
# Pinned rather than tracking `main` for the reason `kube_version` is pinned: the catalog is a
# third party's branch, and an unpinned reference lets a push there change what CI accepts and
# what an operator's values are validated against, silently and with nothing in this repository
# recording that it moved. `just sync-crd-refs` carries a change here into the charts and the CI
# documentation job runs it, exactly as `sync-kube-refs` does for the Kubernetes references.
#
# Bumping it is a deliberate act: read the catalog's log for the groups below first. Its Gateway
# API schemas track the **experimental** channel, so they accept fields — `rules[].retry`,
# `rules[].sessionPersistence`, `filters[].externalAuth`, `spec.useDefaultGateways` — that a
# standard-channel cluster prunes at apply time. That looseness is inherited rather than chosen;
# it already applied to `kubeconform` before any chart value referenced the catalog.
# renovate: datasource=git-refs depName=https://github.com/datreeio/CRDs-catalog currentValue=main
crd_catalog_ref := "866b2653a5334db9aed20ad74701e20fd464471b"

# PodMonitor and the other operator CRDs are not part of the Kubernetes API surface, so their
# schemas come from the community catalog. Held as a variable because the `{{ ... }}` placeholders
# kubeconform expects would otherwise be read as just interpolations.
crd_schemas := 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/' + crd_catalog_ref + '/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

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

# The contract toolchain, resolved the way `resolve_python` resolves an interpreter: an override
# first, then PATH, and a message naming the fix rather than a skipped gate. A recipe that needs an
# external binary and cannot find it fails saying so — the posture every pinned tool here takes.
resolve_contract := '''
contract="${TERRACE_CONTRACT_BIN:-}"
if [ -z "$contract" ] && command -v terrace-contract >/dev/null 2>&1; then
  contract="terrace-contract"
fi
if [ -z "$contract" ]; then
  echo "error: terrace-contract is not on PATH. It is the shared toolchain the configuration" >&2
  echo "       gates delegate to; install the pinned release, or set TERRACE_CONTRACT_BIN to a" >&2
  echo "       local build:" >&2
  echo "         cargo build --release --manifest-path <terrace-config>/cli/Cargo.toml" >&2
  echo "         export TERRACE_CONTRACT_BIN=<terrace-config>/cli/target/release/terrace-contract" >&2
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
#
# `check-contract-tests` is here and its siblings `check-contracts`, `check-kube-refs` and
# `check-crd-refs` are not, and the difference is who repairs the drift. Those three are repaired
# by the Documentation job, which refreshes and commits on the pull request itself, so failing
# `check` on them would fail a contributor for something the automation is about to fix. Nothing
# regenerates the contract suites — deliberately, because a generated *test* that lands without
# its author having read it is a test nobody has read — so a contract that gained a key would
# otherwise leave its suite silently short of it, which is precisely the drift the suites exist to
# remove. It reads two committed files and writes nothing, so it costs the aggregate nothing to
# carry. If the Documentation job ever adopts `just contract-tests`, this belongs back out beside
# the other three.
[doc("Every gate CI runs that does not need a Kubernetes cluster")]
[group('meta')]
check: deps test validate-manifests check-immutable check-config check-contract-coverage check-config-bindings check-config-shapes check-config-readme check-contract-tests check-values-docs check-preset-schema test-contract-scripts lint-python lint lint-policy

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
