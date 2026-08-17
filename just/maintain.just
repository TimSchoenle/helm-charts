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
