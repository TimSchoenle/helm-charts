# Test suites.

# Every suite that needs neither a Kubernetes cluster nor a chart-specific driver.
[group('test')]
test: test-unit test-library test-rules

# `helm unittest` over one chart, or over every chart that has a tests/ directory.
[group('test')]
test-unit chart='':
    #!/usr/bin/env bash
    set -euo pipefail

    if [ -n '{{ chart }}' ]; then
      helm unittest '{{ charts }}/{{ chart }}'
      exit 0
    fi

    for dir in {{ charts }}/*/; do
      if [ -d "${dir}tests" ]; then
        echo "==> unit tests: ${dir%/}"
        helm unittest "${dir%/}"
      else
        echo "==> no tests for ${dir%/}, skipping"
      fi
    done

# Rewrite the committed rendered-manifest snapshots from what the charts render now.
#
# A snapshot suite asserts the whole rendered object, so *any* change to what a chart emits fails
# it — which is the point, and which makes it the artefact most easily forgotten after a change
# that was correct. A new configuration key reaching `config.toml` is exactly that: the ConfigMap
# gains a line, three snapshot cases fail, and nothing else in the repository is wrong. Measured
# on `discord-alertmanager`, whose pull request went red on this alone after every other generated
# file had been brought up to date.
#
# Deliberately does not touch `charts/*/charts/`. Rebuilding a chart's dependencies during a
# `helm unittest -u` run empties the committed `.snap` in silence — the suite renders against a
# half-extracted tree and writes *that* down — so this recipe assumes `just deps` has already run
# and never runs it itself.
#
# Read the diff. A snapshot is a record of what a chart renders, and `-u` accepts whatever it
# renders today, including a regression. `just test-unit` is what asserts it afterwards.
[doc("Rewrite the committed rendered-manifest snapshots from what the charts render now")]
[group('test')]
update-snapshots chart='':
    #!/usr/bin/env bash
    set -euo pipefail

    if [ -n '{{ chart }}' ]; then
      helm unittest -u '{{ charts }}/{{ chart }}'
      exit 0
    fi

    for dir in {{ charts }}/*/; do
      if [ -d "${dir}tests" ]; then
        echo "==> snapshots: ${dir%/}"
        helm unittest -u "${dir%/}"
      fi
    done

# A library chart renders nothing on its own, so `charts/common` cannot be tested directly. This
# fixture instantiates every partial it ships.
[doc("helm unittest the fixture that instantiates every partial the common library ships")]
[group('test')]
test-library:
    helm unittest {{ fixture }}

# Evaluate every chart's Prometheus rules against synthetic series with promtool.
#
# `helm unittest` can prove a chart emits a PrometheusRule; it cannot prove the PromQL inside it is
# correct, and a rule that is syntactically valid but silently matches nothing is the failure mode
# this gate exists to catch. So the rules are run: fed fabricated series and checked for whether
# each alert fires when it should and stays quiet when it should not.
#
# Two forms are checked per chart, because a chart ships one and installs the other:
#
#   raw       `charts/<chart>/rules/*.yml` as committed, carrying the scope placeholder. This is
#             what a reader edits.
#   rendered  the PrometheusRule as `helm template` produces it, with the placeholder replaced by a
#             real `namespace="..."` matcher. This is what a cluster evaluates, and it is a
#             different string — so validating only the raw form leaves the substitution untested.
#
# Charts are discovered, not listed: any chart with a `rules/` directory is picked up, and the
# static audit fails the run if one grows rules without a matching suite at
# `charts/<chart>/rules-tests/`.
[doc("Evaluate every chart's Prometheus rules against synthetic series with promtool")]
[group('test')]
test-rules chart='':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}

    # The static audit covers every chart at once, including those with dashboards but no rules.
    echo "==> auditing rules, runbooks, dashboards and test coverage"
    "$python" {{ scripts }}/audit-observability.py --charts {{ charts }}

    staging="$(mktemp -d)"
    trap 'rm -rf "$staging"' EXIT

    found=0
    for dir in {{ charts }}/*/; do
      name="$(basename "$dir")"
      if [ -n '{{ chart }}' ] && [ "$name" != '{{ chart }}' ]; then
        continue
      fi
      compgen -G "${dir}rules/*.yml" >/dev/null || continue
      found=1

      suite="${dir}rules-tests"
      work="${staging}/${name}"
      mkdir -p "$work"

      echo "==> ${name}: staging rules"
      cp "${dir}"rules/*.yml "$work/"

      # How to render *this* chart with its rules enabled is the chart's business: which values
      # switch the PrometheusRule on, and which credentials a validator insists on before it will
      # render at all, differ per chart. The suite supplies them, so nothing chart-specific lives
      # here.
      render_values=""
      if [ -f "${suite}/render-values.yaml" ]; then
        render_values="${suite}/render-values.yaml"
      fi

      just render-chart "$name" "${work}/rendered-manifest.yaml" \
        '{{ rules_namespace }}' "$render_values"

      "$python" {{ scripts }}/extract-prometheus-rules.py \
        --manifest "${work}/rendered-manifest.yaml" \
        --output "${work}/rendered.rules.yml" \
        --render-values "${suite}/render-values.yaml"

      cp "$suite"/*_test.yml "$work/"

      rule_files=()
      for file in "${dir}"rules/*.yml; do rule_files+=("$(basename "$file")"); done
      test_files=()
      for file in "$suite"/*_test.yml; do test_files+=("$(basename "$file")"); done

      # The Prometheus image runs as `nobody`, while `mktemp -d` creates a directory only its owner
      # can enter. Docker Desktop's bind mounts ignore unix permissions, so this is invisible on
      # Windows and macOS and fails on a Linux CI runner with `permission denied`.
      chmod -R a+rX "$work"

      # Docker needs a native path for the bind mount. Under Git Bash the staging directory is an
      # MSYS path (`/tmp/...`) that the daemon cannot resolve, so it is translated; on Linux
      # `cygpath` is absent and the path is already correct.
      mount="$work"
      if command -v cygpath >/dev/null 2>&1; then
        mount="$(cygpath -w "$work")"
      fi

      echo "==> ${name}: checking rule syntax, committed and rendered"
      MSYS_NO_PATHCONV=1 docker run --rm --entrypoint /bin/promtool \
        -v "${mount}:/w" -w /w '{{ prom_image }}' \
        check rules rendered.rules.yml "${rule_files[@]}"

      echo "==> ${name}: evaluating rules against synthetic series"
      MSYS_NO_PATHCONV=1 docker run --rm --entrypoint /bin/promtool \
        -v "${mount}:/w" -w /w '{{ prom_image }}' \
        test rules "${test_files[@]}"
    done

    if [ "$found" -eq 0 ]; then
      echo "==> no chart ships Prometheus rules; nothing to evaluate"
    fi

# Run a chart's end-to-end driver: the chart's own scripts, against the real application.
#
# Every other gate renders a chart and inspects the result. This one runs a chart's shell against
# the image `values.yaml` pins, which is the only way to test the parts of a chart that are a
# program rather than a manifest — and so it is per-chart by nature. A chart qualifies by having a
# driver at `.github/scripts/e2e/<chart>.sh`; charts without one are covered by the render, lint,
# unit-test and install gates and need nothing here.
[doc("Run a chart's end-to-end driver: the chart's own scripts, against the real application")]
[group('test')]
test-e2e chart:
    bash {{ scripts }}/e2e/{{ chart }}.sh

# chart-testing install & upgrade for every chart that changed. Needs a Kubernetes cluster.
#
# `--upgrade` is what makes this an upgrade test rather than only an install test: for every changed
# chart, `ct` first installs the revision on the target branch and then upgrades it in place to the
# revision under test. That is the only check here that exercises a live object being patched, so it
# is the one that can see the whole class of immutable-field rejections — on any kind, not just the
# StatefulSet spec `just check-immutable` diffs. It skips charts whose major version was bumped,
# where a breaking change is declared rather than accidental.
#
# The old revision's ci/ values files drive the install, and the same paths are then used for the
# upgrade. `--skip-missing-values` skips the ones the new revision renamed or removed, which would
# otherwise fail with nothing to upgrade with.
#
# A chart added by the branch has no revision on the target branch to upgrade *from*, and `ct` does
# not exempt that case the way it exempts a major-version bump: it checks the previous revision out
# and builds the chart's dependencies there before it ever notices the chart is new, so the whole
# run dies on a directory that was never supposed to exist. The changed charts are therefore
# partitioned by whether they exist on the target branch, and only the pre-existing ones are put
# through `--upgrade`; new charts get a plain install, which is the entire test that is meaningful
# for them.
[doc("chart-testing install & upgrade for every changed chart; needs a Kubernetes cluster")]
[group('test')]
test-install:
    #!/usr/bin/env bash
    set -euo pipefail

    existing=()
    added=()
    while read -r chart; do
      [ -n "$chart" ] || continue
      if git cat-file -e "{{ target_branch }}:${chart}/Chart.yaml" 2>/dev/null; then
        existing+=("$chart")
      else
        added+=("$chart")
      fi
    done < <(ct list-changed --config {{ configs }}/ct.yaml)

    if [ "${#existing[@]}" -gt 0 ]; then
      charts="$(IFS=,; echo "${existing[*]}")"
      echo "==> install & upgrade: ${charts}"
      ct install --config {{ configs }}/ct.yaml --upgrade --skip-missing-values --charts "$charts"
    fi

    if [ "${#added[@]}" -gt 0 ]; then
      charts="$(IFS=,; echo "${added[*]}")"
      echo "==> install (new chart, no previous revision): ${charts}"
      ct install --config {{ configs }}/ct.yaml --charts "$charts"
    fi

    if [ "${#existing[@]}" -eq 0 ] && [ "${#added[@]}" -eq 0 ]; then
      echo "==> no chart changed against {{ target_branch }}; nothing to install"
    fi
