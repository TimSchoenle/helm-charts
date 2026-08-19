# Configuration contracts: proving a chart's rendered settings are ones its image will accept.
#
# Every other gate in this repository looks at the chart or at Kubernetes. `values.schema.json`
# describes the chart's values; kubeconform describes Kubernetes objects; `helm unittest` describes
# what the templates emit. None of them can read the *application's* configuration, and a ConfigMap
# holding a `config.toml` full of keys the binary stopped reading is a valid ConfigMap by all three.
# `serde` ignores an unknown key by design, so the pod starts, reports healthy, and runs on a
# compiled default nobody chose.
#
# An image built on `terrace-config` publishes a JSON document naming every setting it reads —
# attached to the image digest as an OCI referrer and embedded in the image itself. This group
# vendors that document per chart and validates three things against it:
#
#   the document     the rendered `config.toml`, against the union of every image that reads it
#   the environment  every variable on every declared container, against that container's image
#   the file names   the secrets directory's contents and every `_FILE` target
#
# Exactly one recipe here touches a network — `contracts`, the refresh. Everything else reads the
# committed file, so a registry outage cannot fail a pull request that changes no image, and
# re-running CI on an old commit validates against what was true then rather than what the registry
# serves today. That is also what keeps `just check` network-free.
#
# The contract diff lands in the same pull request as the digest bump, which is the design's whole
# point: a reviewer of a Renovate bump sees the removed key next to the chart's failing gate.

# --------------------------------------------------------------------------------------------
# The refresh — the only networked recipe
# --------------------------------------------------------------------------------------------

# Refresh every vendored contract from the image each chart pins.
#
# Resolves each declared image to a digest, verifies the signature, discovers the contract attached
# to that digest, checks that the image's `dev.terrace.config.prefix` label and the document's own
# dialect agree, and writes `charts/<chart>/contracts/<name>.json` — the published bytes inside a
# `source` envelope recording which digest they came from. That record is what the offline
# staleness interlock reads.
#
# Run by the Documentation job, whose existing commit step carries the result back to the branch.
# Running it locally is a convenience; `just check-contracts` reports the gap without a network.
#
# Deliberately NOT in `just check`: `check` is the cluster-free set and, since this group landed,
# the network-free set. The only reason a contributor's `just check` should need connectivity is
# the Kubernetes `$ref` resolution that already exists.
#
# Needs `oras` and `cosign`, both pinned in `justfile`. A chart whose image publishes no contract
# fails here rather than being skipped: declaring a document commits the chart to having one.
[doc("Refresh every vendored contract from the image each chart pins (the only networked recipe)")]
[group('contracts')]
contracts chart='':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    CONTRACT_SIGNER='{{ contract_signer }}' \
      "$python" {{ scripts }}/refresh-contracts.py {{ chart }} --charts {{ charts }}

# Report vendored contracts that no longer match the images their charts pin.
#
# The offline half of the recipe above, for a contributor who wants to know whether a refresh is
# owed without running one. `just check-config` catches the same gap per document and with a far
# better message; this catches it across the whole tree in one line.
[doc("Report vendored contracts that no longer match the images their charts pin")]
[group('contracts')]
check-contracts:
    #!/usr/bin/env bash
    set -euo pipefail

    stale=0
    git diff --exit-code -- '{{ charts }}/*/contracts/' || stale=1

    # `git diff` cannot see a file that was never added, and a refresh for a chart whose contract
    # is new writes exactly that — so an untracked contract would otherwise read as "no changes".
    untracked="$(git ls-files --others --exclude-standard -- '{{ charts }}/*/contracts/*')"
    if [ -n "$untracked" ]; then
      echo "untracked vendored contracts:" >&2
      echo "$untracked" | sed 's/^/  /' >&2
      stale=1
    fi

    if [ "$stale" -ne 0 ]; then
      echo "error: vendored contracts are stale; run 'just contracts'" >&2
      exit 1
    fi

# --------------------------------------------------------------------------------------------
# The gates — offline
# --------------------------------------------------------------------------------------------

# Validate every chart's rendered configuration against the contracts of the images it pins.
#
# Reuses `just render`, so the manifests these gates read are byte-identical to the ones
# kubeconform and kube-linter see — one render, one set of facts, and no third opinion about what
# the chart produces.
#
# Reads no network. The contracts are committed files, and the JSON Schema engine is refused any
# reference it would have to fetch: the union is walked first and a `$ref` that does not begin with
# `#` is fatal. Chart `values.schema.json` files legitimately name Kubernetes types by URL, which is
# why `render-chart` carries a retry; an *app config* schema must not, or an offline gate silently
# becomes a networked one and a third party gains a say in what CI accepts.
#
# Every violation is reported before it exits, matching `just render` and `validate-manifests`.
[doc("Validate every chart's rendered configuration against its images' contracts")]
[group('contracts')]
check-config out='rendered':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    just render '{{ out }}'
    "$python" {{ scripts }}/check-config.py '{{ out }}' --charts {{ charts }}

# Report which charts a configuration contract covers, and which it does not.
#
# Adopting a contract is opt-in: a chart carrying a `config-contract.yaml` is covered, and one
# without is reported rather than failed. The images here adopt the format on their own release
# schedules, so a gate that failed every chart whose image had not caught up would be red for
# reasons nobody in this repository can fix — and would end up disabled, which is worse than
# absent.
#
# What still fails is a chart contradicting itself: one that declares documents and leaves one of
# its own first-party images unaccounted for, so a new service added to a multi-image chart cannot
# be covered by nothing while the chart still looks covered.
#
# Needs no render, so it is cheap enough to sit in `just check` beside the gates that do.
[doc("Report which charts a configuration contract covers, and which it does not")]
[group('contracts')]
check-contract-coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/check-config.py --coverage-only --charts {{ charts }}

# Unit tests for the contract model, over fixtures rather than over a registry.
#
# The union is the piece most worth testing: `tankovault` renders one document read by eight
# binaries, and the rules for merging their contracts are what decide whether this scales past one
# chart. Beside it are the classification order, the two-step value check and the file-supplyable
# rule — every one of them normative, and every one of them a place where a plausible-looking
# implementation quietly under-checks.
[doc("Unit tests for the contract model, over fixtures rather than over a registry")]
[group('contracts')]
test-contract-union:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" -m unittest discover -s {{ scripts }}/tests -p 'test_contract_*.py' -v
