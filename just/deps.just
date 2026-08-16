# Chart dependencies.
#
# `charts/*/charts/`, `charts/*/Chart.lock` and `charts/*/definitions/` are gitignored, so a fresh
# clone can neither lint, render nor test until `just deps` has run.

# Resolve the `common` library dependency, for one chart or for all of them plus the fixture.
[group('deps')]
deps chart='': && deps-extract
    #!/usr/bin/env bash
    set -euo pipefail

    update() {
      echo "==> dependencies: $1"
      helm dependency update "$1"
    }

    if [ -n '{{ chart }}' ]; then
      update '{{ charts }}/{{ chart }}'
      exit 0
    fi

    for dir in {{ charts }}/*/; do
      [ -f "${dir}Chart.yaml" ] || continue
      update "${dir%/}"
    done
    update '{{ fixture }}'

# Unpack each built dependency archive next to itself. helm-schema resolves the library chart's
# `common.*` definitions from the extracted tree rather than from the archive, so schema
# generation fails on unresolved refs without this.
[doc("Unpack each built dependency archive so helm-schema can resolve the library's definitions")]
[group('deps')]
deps-extract:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in {{ charts }}/*/charts/; do
      [ -d "$dir" ] || continue
      for archive in "$dir"*.tgz; do
        [ -f "$archive" ] || continue
        tar -xzf "$archive" -C "$dir"
      done
    done

# Remove everything the recipes in this repository generate. All of it is gitignored.
[doc("Remove everything the recipes in this repository generate")]
[group('deps')]
clean:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf rendered
    for dir in {{ charts }}/*/ {{ fixture }}/; do
      rm -rf "${dir}charts" "${dir}Chart.lock" "${dir}definitions"
    done
