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
# Every document declared here binds exactly one image today — `tankovault`'s nine services each
# render their own per-component ConfigMap — so that first union is the identity. The scope is
# still the document rather than the image, because that is the boundary the format draws and the
# one a document with a second reader would arrive on.
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
# The union is tested here and nowhere else: every document in the repository binds a single image,
# so the merge is the identity in production and these fixtures are the only place its rules run.
# They are worth keeping — a document read by several binaries is what the format is for, and the
# merge is what such a document would land on. Beside it are the classification order, the two-step
# value check and the file-supplyable rule — every one of them normative, and every one of them a
# place where a plausible-looking implementation quietly under-checks.
[doc("Unit tests for the contract model, over fixtures rather than over a registry")]
[group('contracts')]
test-contract-union:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" -m unittest discover -s {{ scripts }}/tests -p 'test_contract_*.py' -v

# --------------------------------------------------------------------------------------------
# Reading a contract, rather than checking against one
# --------------------------------------------------------------------------------------------

# Explain what a chart's pinned images read: every setting, its spellings, and which image reads it.
#
# Every other recipe in this group is a gate — it holds a rendered manifest against a contract and
# reports the difference. None of them ever shows a contributor what the contract *says*, and the
# vendored file is the only place in this repository that states, per setting, what the binary
# calls it, what values it accepts, whether it is required, whether a file may supply it and what
# happens when nothing sets it. Answering "what can I put in `config.toml`, and why is the value I
# set being ignored?" out of a 400 KB JSON document is the job.
#
# The reader attribution is the part worth having. `tankovault` pins nine images across nine
# documents, and which of them reads a given setting is stated nowhere else: not in the chart, not
# in `config-contract.yaml`, not in the README. It is derived from the contracts the declaration
# binds, so it cannot go stale against them — a bump that moves a setting from one service to
# another moves this output with it — and it is how an operator tells a local change from a
# fleet-wide one before making it.
#
# Offline and read-only, like every recipe here but the refresh, and honouring the same staleness
# interlock the gates do: a contract that is not for the digest the chart pins describes some
# other build, and a confident wrong answer is worse here than in a gate, because nothing
# downstream re-checks what a person was told.
#
# Deliberately NOT in `just check`. It asserts nothing — it prints — and a recipe that cannot fail
# has no business in an aggregate whose whole output is a pass or a fail.
#
# `just explain` alone lists the charts that have a contract. A second argument selects by
# substring, or by glob when it carries one, over the setting's path and prints the full entry for
# each match; `--full` does that without a pattern and `--json` emits the same selection for
# something other than a person. A pattern matching nothing exits non-zero, which is `grep`'s
# convention and the reason is in the script's docstring: an answer indistinguishable from a typo
# in the pattern is not an answer.
[doc("Explain what a chart's pinned images read: settings, spellings, and which image reads which")]
[group('contracts')]
explain chart='' pattern='' *flags:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/explain-config.py '{{ chart }}' '{{ pattern }}' \
      --charts {{ charts }} {{ flags }}

# --------------------------------------------------------------------------------------------
# The credential surface
# --------------------------------------------------------------------------------------------

# List every credential the images in this repository declare secret, and how each one is spelt.
#
# Forty-seven key declarations across the tree carry `secret: true`, and until this recipe existed
# no document anywhere said what they are. Somebody writing a values file has to know which
# credentials a chart needs, what to name the keys of the Secret that carries them, which
# environment spelling and which `_FILE` spelling address the same value, and — for a chart whose
# services run different binaries — which of them actually reads each one. Every part of that
# answer was already published by the images and readable only by opening nine JSON documents by
# hand.
#
# Reads the committed contracts and nothing else: no render, no cluster, no network. Cheap enough
# to run while writing the values file it exists to help with, which is the only time it is
# wanted.
#
# Deliberately skips the staleness interlock the gates apply. A Renovate digest bump produces one
# run in which the pinned digest and the vendored contract disagree, and withholding the inventory
# then would remove the document at exactly the moment a reviewer is reading a credential change.
# Whether the vendored copy is current is `just check-contracts`' question, and it answers it in
# one line rather than by refusing to describe anything.
#
# `--json` for a machine; the table is the answer for a person.
[doc("List every credential the images declare secret, and how each one is spelt")]
[group('contracts')]
config-secrets *args:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/config-secrets.py --charts {{ charts }} {{ args }}

# Report what the charts deliver against the credentials the images declare. Report only.
#
# The gates in `check-config` are structurally unable to ask this. Gate 3 checks that every secret
# file a pod is *given* is a name the contract knows, so it refuses a name nothing declares and is
# blind by construction to the opposite defect: a credential the image declares and no channel
# supplies. That one renders cleanly, passes every gate, starts, reports healthy, and fails at the
# first request that needs it. Gate 3 is also scoped to the containers a `config-contract.yaml`
# names as consumers, so an init container or a sidecar sharing the pod is a container no gate
# opens — and several Deployments here run one that mounts the whole per-service credential set
# while running a different image entirely.
#
# Reuses `just render`, so the manifests read here are the ones every other gate reads: one
# render, one set of facts. Reconciled against every `ci/` values file each chart ships, because a
# credential delivered under one fixture is delivered — the chart has a channel for it — while
# over-projection is a property of the fixture that produced it and is reported naming that file.
#
# **Exits 0 whatever it finds, and that is the point of the first pass.** Its three questions are
# new, and a report over an established repository is where they earn their triage: a finding that
# turns out to be a considered design decision belongs in a document, not in a red pipeline on a
# pull request that changed nothing. A follow-up promotes it once these findings are triaged, and
# the promotion is one line of `config-secrets.py` — the findings are already `Finding`s at the
# level each has earned, so nothing else moves.
#
# Deliberately not in `just check` for the same reason: an aggregate a contributor is expected to
# run before pushing must not be a place where advisory output accumulates.
[doc("Report what the charts deliver against the credentials the images declare (report only)")]
[group('contracts')]
check-config-secrets out='rendered' *args:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    just render '{{ out }}'
    "$python" {{ scripts }}/config-secrets.py --reconcile '{{ out }}' --charts {{ charts }} {{ args }}
