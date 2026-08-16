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
[doc("Generate every chart's values.schema.json from the @schema blocks in its values.yaml")]
[group('docs')]
schema:
    helm schema --add-schema-reference --chart-search-root {{ charts }}

# Render every chart's README.md from its README.md.gotmpl.
[group('docs')]
chart-readmes:
    helm-docs --chart-search-root {{ charts }} --template-files README.md.gotmpl

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
