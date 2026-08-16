# Rendering charts and validating what comes out.

# Render one chart to one file.
#
# Every renderer in the repository goes through here, so the CRD API declarations, the retry and
# the namespace handling cannot drift between the policy scan, the manifest validation and the
# rule tests — and the manifests kube-linter sees are byte-identical to the ones kubeconform sees.
#
#   just render-chart paperless-ngx out.yaml
#   just render-chart tankovault out.yaml rules-test charts/tankovault/rules-tests/render-values.yaml
[doc("Render one chart to one file; the single render path every other recipe goes through")]
[group('render')]
render-chart chart target namespace='default' values='' kube='':
    #!/usr/bin/env bash
    set -euo pipefail

    # `helm template` reports the built-in API surface but no CRDs, and charts that ship custom
    # resources refuse to render rather than dropping them silently. Declaring the CRD APIs is what
    # tells an offline render that the cluster has them; see the config file's own header.
    args=(--namespace '{{ namespace }}')
    while IFS= read -r api_version; do
      args+=(--api-versions "$api_version")
    done < <(grep -Ev '^[[:space:]]*(#|$)' '{{ api_versions }}')

    if [ -n '{{ values }}' ]; then
      args+=(--values '{{ values }}')
    fi
    if [ -n '{{ kube }}' ]; then
      args+=(--kube-version '{{ kube }}')
    fi

    # Every `values.schema.json` in this repository states its Kubernetes types by URL, so
    # validating values is a network call and a reset connection fails a chart that is entirely
    # valid. Retried rather than tolerated: a real schema violation fails all three attempts just as
    # it fails one, and each attempt rewrites the target from scratch so a half-written render
    # cannot survive.
    for attempt in 1 2 3; do
      if helm template '{{ chart }}' '{{ charts }}/{{ chart }}' "${args[@]}" > '{{ target }}'; then
        exit 0
      fi
      if [ "$attempt" -lt 3 ]; then
        echo "retrying {{ chart }} (attempt $((attempt + 1)) of 3)"
        sleep "$((attempt * 3))"
      fi
    done

    echo "FAILED to render: {{ chart }}" >&2
    exit 1

# Render every application chart against every one of its ci/ values files, one file per pair.
#
# Each output is named `<chart>--<values>.yaml`, so a tool reporting a file identifies the offending
# pair. Every pair is attempted before the recipe exits, so one chart that fails to render does not
# hide the state of the rest.
[doc("Render every application chart against every one of its ci/ values files")]
[group('render')]
render out='rendered' kube='':
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p '{{ out }}'

    failed=0
    for dir in {{ charts }}/*/; do
      name="$(basename "$dir")"

      # A library chart renders nothing on its own. Read from Chart.yaml rather than matched
      # against a hard-coded name, so a second library chart needs no change here.
      if grep -q '^type:[[:space:]]*library' "${dir}Chart.yaml"; then
        continue
      fi

      for values in "${dir}"ci/*.yaml; do
        [ -e "$values" ] || continue
        echo "==> rendering ${name} with $(basename "$values")"
        if ! just render-chart "$name" "{{ out }}/${name}--$(basename "$values")" \
             default "$values" '{{ kube }}'; then
          failed=1
        fi
      done
    done

    exit "$failed"

# Render every chart and validate the output against the Kubernetes schemas for one version.
#
# kubeconform validates every file before it exits, so one broken chart does not hide the rest.
[doc("Render every chart and validate the output with kubeconform")]
[group('render')]
validate-manifests kube=kube_version:
    #!/usr/bin/env bash
    set -euo pipefail

    rendered="$(mktemp -d)"
    trap 'rm -rf "$rendered"' EXIT

    render_status=0
    just render "$rendered" '{{ kube }}' || render_status=1

    echo "==> validating rendered manifests against Kubernetes {{ kube }}"
    kubeconform \
      -strict \
      -summary \
      -kubernetes-version '{{ kube }}' \
      -schema-location default \
      -schema-location '{{ crd_schemas }}' \
      "$rendered"

    exit "$render_status"

# Guard the fields Kubernetes refuses to update in place.
#
# Immutable fields are the one class of defect that renders, lints and installs cleanly and only
# fails on an upgrade of an already-running release. `just test-install` catches it on a real API
# server for the charts a pull request touches; this is the cheap and complete half of that pair —
# no cluster, every chart, and it still runs for a chart whose major version was bumped, which is
# exactly when an immutable field is most likely to have moved.
#
# The Kubernetes version pins which StatefulSet schema the mutable-field list is cross-checked
# against; keep it in step with the highest version `just validate-manifests` is run at.
[doc("Guard the fields Kubernetes refuses to update in place")]
[group('render')]
check-immutable kube=kube_version:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    KUBE_VERSION='{{ kube }}' "$python" {{ scripts }}/check-immutable-fields.py
