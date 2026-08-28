# Repository maintenance: keeping mechanically derived values in step with what they derive from.

# --------------------------------------------------------------------------------------------
# Kubernetes schema references
# --------------------------------------------------------------------------------------------
#
# Every `@schema` block that types a value as a Kubernetes object states that type by URL, pinned
# to one Kubernetes release:
#
#   # @schema
#   # $ref: https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/v1.34.0/_definitions.json#/definitions/io.k8s.api.core.v1.PodSecurityContext
#   # @schema
#
# helm-schema copies those URLs verbatim into `values.schema.json`, which puts several thousand
# copies of one release number across the charts and nothing that derives them from `kube_version`.
# A bump would otherwise leave every chart validating its values against the superseded release,
# and silently: the old URL keeps resolving, so nothing fails — the schema is simply the wrong one.
# `sync-kube-refs` makes the release a single edit in `justfile` again, and the CI documentation job
# runs it so a bump that forgets to is corrected on the pull request that made it.
#
# Both recipes take the release as a parameter, so one can be tried without editing anything, but
# the default is `kube_version` and that is the value to change: it is what `just validate-manifests`
# and `just check-immutable` already pin to, so a chart cannot end up typed against a release the
# rendered manifests are never validated against.
#
# What counts as a reference, why `values.schema.json` is rewritten rather than regenerated, and why
# the rewrite is byte-level lives in the script's own header.
#
# `resolve_python` is reused even though this script imports nothing beyond the standard library:
# one answer to "which interpreter" for the whole repository is worth more to a contributor than a
# marginally looser prerequisite here.

# Repin every Kubernetes `$ref` in the charts to the pinned Kubernetes release.
[doc("Repin every Kubernetes $ref in the charts to the pinned Kubernetes release")]
[group('maintain')]
sync-kube-refs version=kube_version:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/kube-schema-refs.py '{{ version }}' --charts {{ charts }}

# Report every Kubernetes `$ref` in the charts that is not pinned to the wanted release, and fail
# if any is not. Reads only what is checked in, unlike the rewrite.
[doc("Report every Kubernetes $ref in the charts not pinned to the wanted release")]
[group('maintain')]
check-kube-refs version=kube_version:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/kube-schema-refs.py '{{ version }}' --charts {{ charts }} --check

# --------------------------------------------------------------------------------------------
# The chart chassis
# --------------------------------------------------------------------------------------------
#
# `just new-chart` writes a new chart from `.github/templates/chart/values.chassis.yaml` — the
# twenty-nine `values.yaml` blocks that are the same in every chart because `charts/common` owns
# the logic and these files only name it. Nothing keeps that copy and the charts in step: a block
# gains a sentence in one chart, the scaffold keeps the old wording, and the next chart created
# inherits it.
#
# The chassis is **not** the authority. It was seeded from the majority text of the existing
# charts, so it is right about most blocks by construction and has no claim to be right about any
# particular one — which is why this reports and does not fail.

# Report where a chart's shared values blocks have drifted from the scaffold's copy. Report only.
#
# Compared as text, comments included, because the comments are the interesting part: the
# `@schema` blocks are what `values.schema.json` is generated from, and the `# --` descriptions
# are published into every chart's README by helm-docs. Two blocks parsing to the same mapping can
# still produce a different schema and a different README.
#
# Blocks a chart does not carry at all are not reported. `teamspeak` has no `extraVolumeMounts`
# and does not need one; that is a fact about the chart rather than drift. `tankovault` shares
# only 8 of the 29 because it is nine services and keeps its podspec under `defaults:` and
# `services.*` — the scaffold does not model that shape, and saying so is more useful than
# reporting 21 absences.
#
# Deliberately NOT in `just check`, for the reason `explain` and `check-config-secrets` are not:
# it prints rather than asserts, and an aggregate whose whole output is a pass or a fail is not
# where advisory output belongs.
#
#   just check-chassis                    every chart, with a diff per drifted block
#   just check-chassis '' --summary       the counts alone
#   just check-chassis portfolio          one chart
[doc("Report where a chart's shared values blocks have drifted from the scaffold's copy")]
[group('maintain')]
check-chassis chart='' *flags:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/check-chassis.py '{{ chart }}' \
      --charts {{ charts }} --chassis .github/templates/chart/values.chassis.yaml {{ flags }}

# Declare a rule threshold as tunable, deriving its `anchor` from the rule itself.
#
# The hard part of a tunable is the anchor: the slice of the alert's expression that contains the
# number and occurs in it exactly once. Hand-copied, it is the field that goes wrong — an anchor
# matching nothing makes the override a silent no-op, one matching twice rewrites a comparison the
# author did not mean. `just audit-observability` catches both, but on the pull request rather than
# in the editor, and the repair is a hunt through PromQL. Deriving it removes the hunt.
#
# Not every number in a rule is a threshold. Bucket boundaries and structural `> 0` comparisons are
# deliberately not tunable, and the header of each chart's `rules/tunables.yaml` says which and
# why — this cannot tell them apart and does not try. Read that before declaring one.
#
#   just add-tunable tankovault TankoVaultDatabasePoolSaturated
#       list what the alert compares against, with the anchor each number would get
#
#   just add-tunable tankovault TankoVaultDatabasePoolSaturated saturation 0.9
#       print the declaration
#
#   just add-tunable tankovault TankoVaultDatabasePoolSaturated saturation 0.9 --write
#       insert it, then fill in the `description:` and run `just schema`
[doc("Declare a rule threshold as tunable, deriving its anchor from the rule")]
[group('maintain')]
add-tunable chart alert name='' literal='' *flags:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    args=(--charts {{ charts }} --chart '{{ chart }}' --alert '{{ alert }}')
    if [ -n '{{ name }}' ]; then args+=(--name '{{ name }}'); fi
    if [ -n '{{ literal }}' ]; then args+=(--literal '{{ literal }}'); fi
    "$python" {{ scripts }}/add-tunable.py "${args[@]}" {{ flags }}
