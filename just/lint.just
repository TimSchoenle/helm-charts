# Chart linting and policy.

# Everything the Lint Charts job runs.
[group('lint')]
lint: lint-library lint-charts

# `helm lint` the common library. `ct` cannot lint or install a library chart, so `common` stays in
# `excluded-charts` there and is covered here plus by the fixture's unit tests.
[doc("helm lint the common library chart, which chart-testing cannot lint")]
[group('lint')]
lint-library:
    helm lint {{ charts }}/common

# Print the charts that changed against the target branch, one path per line, nothing if none did.
[group('lint')]
lint-changed:
    @ct list-changed --config {{ configs }}/ct.yaml

# chart-testing lint over every chart that changed against the target branch.
[group('lint')]
lint-charts:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "$(ct list-changed --config {{ configs }}/ct.yaml)" ]; then
      echo "==> no chart changed against {{ target_branch }}; nothing to lint"
      exit 0
    fi
    ct lint --debug --config {{ configs }}/ct.yaml --lint-conf {{ configs }}/lintconf.yaml

# kube-linter over the rendered manifests, enforcing the restricted Pod Security Standard the
# charts claim, so a later change cannot quietly regress the baseline.
[doc("kube-linter over the rendered manifests, enforcing the restricted Pod Security Standard")]
[group('lint')]
lint-policy out='rendered':
    just render '{{ out }}'
    kube-linter lint --config {{ configs }}/kube-linter.yaml '{{ out }}'
