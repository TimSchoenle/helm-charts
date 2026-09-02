# Generated documentation.
#
# `values.schema.json`, every chart's `README.md` and the root `README.md` are outputs. Edit
# `values.yaml` (its `@schema` blocks), `README.md.gotmpl` and `.github/templates/README.md.hbs`
# instead. CI regenerates all three on every pull request and commits the result back to the
# branch, so running these locally is a convenience rather than a requirement.

# Regenerate every generated file that has a local toolchain. The root README is rendered from
# handlebars by a workflow action and has no local equivalent; `just chart-index` prints its input.
[doc("Regenerate every generated file that has a local toolchain")]
[group('docs')]
docs: schema chart-readmes

# Generate every chart's `values.schema.json` from the `@schema` blocks in its values.yaml.
#
# Needs the dependency tree extracted, not just built: the generator resolves the library chart's
# definitions from `charts/<chart>/charts/common/`. Run `just deps` first.
#
# Four invocations. The first two regenerate the `@schema` blocks that are themselves derived —
# the alert preset lists, which enumerate names out of `rules/*.yml` and `rules/tunables.yaml`,
# and the config shapes, which are the element schemas the images publish — and both have to run
# *before* helm-schema, since they edit helm-schema's input.
#
# The other two are helm-schema, because one pass cannot cover both. Walking `charts/` finds
# `charts/common` and the extracted copy of it under every consumer, and the generator keys its
# results by chart *name* — so the vendored copies shadow the library itself and its own
# `values.schema.json` is never written. That is silent: the stale file stays checked in and the
# run exits 0. The second pass points straight at the library, with `--no-dependencies` because a
# library chart has none.
[doc("Generate every chart's values.schema.json from the @schema blocks in its values.yaml")]
[group('docs')]
schema: schema-presets config-shapes
    helm schema --add-schema-reference --chart-search-root {{ charts }}
    helm schema --add-schema-reference --chart-search-root {{ charts }}/common --no-dependencies

# Regenerate the alert presets' `@schema` blocks from the rules they constrain.
#
# `disabledAlerts` enumerates the alerts the chart ships, `thresholds` enumerates what
# `rules/tunables.yaml` declares, and so on for all seven. Hand-written, those lists are a copy of
# a fact that already exists in the rule files, and the copy nothing regenerates is the one that
# goes stale — so they are derived, and `just check-preset-schema` fails the build when they drift.
[doc("Regenerate the alert presets' @schema blocks from the rules they constrain")]
[group('docs')]
schema-presets:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/schema-presets.py --charts {{ charts }}

# Fail when a chart's alert preset schemas no longer match its rules.
#
# The asserting half of `schema-presets`. In `just check` because a rule renamed or retired without
# a regeneration leaves a schema that rejects a name the chart now ships, or accepts one it does
# not — and both read as the chart's bug rather than the schema's.
[doc("Fail when a chart's alert preset schemas no longer match its rules")]
[group('docs')]
check-preset-schema:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/schema-presets.py --charts {{ charts }} --check

# Render every chart's README.md from its README.md.gotmpl.
[group('docs')]
chart-readmes:
    helm-docs --chart-search-root {{ charts }} --template-files README.md.gotmpl

# Fail when a chart value carries no `# --` description.
#
# The input side of the two recipes above. helm-docs renders these comments into every chart's
# README table and helm-schema copies them into `values.schema.json`, so a value without one is
# undocumented in both outputs — and both regenerate cleanly regardless, which is why nothing
# noticed that 134 grouping keys had none.
#
# Required of a key that has an `@schema` block, because helm-schema emits a property for it, and
# of a key that has children, because helm-docs gives a documented group its own README row. The
# contents of an already-documented mapping are exempt; the script's header says why, and why the
# alternative rule produces `# -- The name.` above `name`.
#
# In `just check`: it asserts, reads only committed files, and needs no network, no cluster and no
# render.
[doc("Fail when a chart value carries no `# --` description")]
[group('docs')]
check-values-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/check-values-docs.py \
      --charts {{ charts }} --chassis .github/templates/chart/values.chassis.yaml

# Print the chart index the root README is rendered from, as one line of JSON.
#
# Every value comes from a chart's own Chart.yaml, so the table cannot drift from what the charts
# declare: adding a chart is enough.
[doc("Print the chart index the root README is rendered from, as one line of JSON")]
[group('docs')]
chart-index:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/chart-index.py {{ charts }}
