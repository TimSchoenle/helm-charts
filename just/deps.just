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
deps chart='': deps-libraries && deps-extract
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
# with `--dependency-update`: it needs `charts/common/charts/` built and must not find the
# extracted copies `deps-extract` leaves under a consumer, which it would package alongside the
# archive.
[doc("Resolve the library charts' own dependencies, in dependency order")]
[group('deps')]
deps-libraries:
    #!/usr/bin/env bash
    set -euo pipefail
    for library in {{ libraries }}; do
      echo "==> dependencies: {{ charts }}/${library}"
      helm dependency update '{{ charts }}'/"${library}"
    done

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
      # A library's own dependencies stay archived. Helm loads an extracted directory alongside
      # its archive, so extracting them would put a second copy into every chart that bundles the
      # library, and no schema pass reads a library's dependencies.
      grep -q '^type:[[:space:]]*library' "${dir%charts/}Chart.yaml" && continue
      for archive in "$dir"*.tgz; do
        [ -f "$archive" ] || continue
        # Replace the previous extraction rather than unpacking over it. Helm loads the extracted
        # directory alongside the archive, so a template file a library release moved or deleted
        # would otherwise survive here and silently redefine the partials it used to hold — every
        # local render and unit test then exercises dead library code.
        top="$(tar -tzf "$archive" | awk -F/ 'NR == 1 { print $1 }')"
        [ -n "$top" ] && rm -rf "${dir}${top}"
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
