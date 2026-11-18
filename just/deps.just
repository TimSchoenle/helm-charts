# Chart dependencies.
#
# `charts/*/charts/`, `charts/*/Chart.lock` and `charts/*/definitions/` are gitignored, so a fresh
# clone can neither lint, render nor test until `just deps` has run.

# Resolve the library dependencies, for one chart or for all of them plus the fixture.
#
# The libraries go first, through `deps-libraries`. `helm dependency update` copies a `file://`
# dependency from its directory as it stands, `charts/` included, and never resolves that
# dependency's own dependencies. `common` depends on `terrace-helm`, so a chart updated before
# `charts/common/charts/` holds `terrace-helm` would bundle a `common` that cannot render.
[group('deps')]
deps chart='': deps-libraries
    #!/usr/bin/env bash
    set -euo pipefail

    update() {
      echo "==> dependencies: $1"
      helm dependency update "$1"
    }

    if [ -n '{{ chart }}' ]; then
      case ' {{ libraries }} ' in
        *' {{ chart }} '*) ;;
        *) update '{{ charts }}/{{ chart }}' ;;
      esac
      exit 0
    fi

    for dir in {{ charts }}/*/; do
      [ -f "${dir}Chart.yaml" ] || continue
      case ' {{ libraries }} ' in
        *" $(basename "$dir") "*) continue ;;
      esac
      update "${dir%/}"
    done
    update '{{ fixture }}'

# Resolve the library charts' own dependencies, in dependency order, and nothing else.
#
# What the release workflow runs before chart-releaser, which packages every other chart itself
# with `--dependency-update`: it needs `charts/common/charts/` built, and nothing more.
[doc("Resolve the library charts' own dependencies, in dependency order")]
[group('deps')]
deps-libraries:
    #!/usr/bin/env bash
    set -euo pipefail
    for library in {{ libraries }}; do
      echo "==> dependencies: {{ charts }}/${library}"
      helm dependency update '{{ charts }}'/"${library}"
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
