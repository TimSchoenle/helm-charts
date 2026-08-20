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

# --------------------------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------------------------

# Lint `.github/scripts` — 11,000 lines of Python that had no linter at all.
#
# That absence was not a decision anybody made. Seven entry scripts already carried
# `# noqa: E402` on their imports, written for a linter that never landed, so the intent was
# there and the tool was not. The first run over the tree found 98 issues: four unused imports,
# two f-strings with no placeholder, a loop variable nobody read, three mutable class attributes,
# twelve unsorted import blocks and forty-five `# noqa` comments for a rule this linter does not
# raise.
#
# Configured in `.github/configs/ruff.toml` beside every other tool's configuration, rather than
# in a `pyproject.toml` at the root: `.github/scripts` is a directory of scripts a task runner
# invokes, not a package, and a `pyproject.toml` would imply a distribution that does not exist.
# Which rules are on and why is documented there.
#
# **Lints, does not format.** The files are hand-formatted to a house style the formatter
# disagrees with in places — notably the long argument lists laid out to be read as tables — and
# reflowing 11,000 lines to settle that is a change nobody asked for.
#
# **No type checking.** mypy would be the obvious companion and is deliberately absent: it ships
# as a pip package rather than a single binary, and this repository has already decided, in
# `justfile`'s note on `jv`, that a `pip install` inside a recipe is the difference between a
# gate that reproduces locally and one that does not. Adopting it means solving that first, and
# doing it badly is worse than the gap.
#
# In `just check`, because it asserts, needs no network, no cluster and no render, and runs over
# a directory every other gate in this repository depends on.
[doc("Lint the Python behind every gate in this repository")]
[group('lint')]
lint-python:
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v ruff >/dev/null 2>&1; then
      echo "error: ruff is not on PATH. It is pinned as ruff_version in justfile and installed" >&2
      echo "       by release URL, exactly as jv and kubeconform are:" >&2
      echo "       https://github.com/astral-sh/ruff/releases/tag/{{ ruff_version }}" >&2
      exit 1
    fi

    ruff check --config {{ configs }}/ruff.toml {{ scripts }}
