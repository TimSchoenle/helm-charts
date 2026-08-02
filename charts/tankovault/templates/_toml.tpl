{{/*
Render a values map as TOML.

TankoVault's configuration file layer is TOML only, and neither Helm nor Sprig ships a
`toToml`. This renderer covers exactly the shape `docs/CONFIGURATION.md` describes: nested
tables, scalars, and arrays of scalars. Anything outside that shape fails loudly rather than
emitting a file the service would reject at boot — `configExtraToml` is the escape hatch.

Two details carry the whole thing:

  1. Scalars are emitted with `toJson`. A TOML basic string, integer, float, boolean and
     array-of-scalars are all spelled exactly as their JSON equivalents, so one conversion is
     correct for every leaf type, including escaping inside strings.

  2. **Every scalar in a table is emitted before any of its sub-tables.** In TOML a key
     belongs to the most recent `[table]` header, so emitting `[database]` before a sibling
     top-level scalar would silently reparent that scalar into `database`. Hence two passes,
     not one.

Map iteration in Go templates is sorted by key, so output is deterministic and a no-op
`helm upgrade` produces a byte-identical ConfigMap — which matters here, because a changed
ConfigMap is what wakes every service's config watcher.

Usage: {{ include "tankovault.toml" (dict "value" $map) | trim }}
*/}}
{{- define "tankovault.toml" -}}
{{- $value := .value | default dict -}}
{{- $prefix := .prefix | default "" -}}
{{- range $k, $v := $value -}}
{{- if and (not (kindIs "map" $v)) (not (kindIs "invalid" $v)) -}}
{{- if kindIs "slice" $v -}}
{{- range $item := $v -}}
{{- if or (kindIs "map" $item) (kindIs "slice" $item) -}}
{{- fail (printf "tankovault: config key %q is an array of tables, which this chart cannot render as TOML. Put it in `configExtraToml` verbatim instead." (printf "%s%s" (ternary (printf "%s." $prefix) "" (ne $prefix "")) $k)) -}}
{{- end -}}
{{- end -}}
{{- end }}
{{ $k }} = {{ toJson $v }}
{{- end -}}
{{- end -}}
{{- range $k, $v := $value -}}
{{- if kindIs "map" $v -}}
{{- $path := ternary (printf "%s.%s" $prefix $k) $k (ne $prefix "") }}

[{{ $path }}]
{{- include "tankovault.toml" (dict "value" $v "prefix" $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Merge a list of maps into one, deeply, later entries winning, and render it as TOML.

Used to fold the chart-derived wiring into the operator's own `config` tree before rendering,
so the two never end up as competing fragments that have to be ordered against each other.

Usage: {{ include "tankovault.tomlMerged" (dict "maps" (list $derived $user)) }}
*/}}
{{- define "tankovault.tomlMerged" -}}
{{- $merged := dict -}}
{{- range $m := .maps -}}
{{- $merged = mergeOverwrite $merged (deepCopy ($m | default dict)) -}}
{{- end -}}
{{- include "tankovault.toml" (dict "value" $merged) -}}
{{- end -}}
