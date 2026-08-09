{{/*
Render a values map as TOML.

The applications behind these charts read a TOML configuration file, and neither Helm nor
Sprig ships a `toToml`. This renderer covers nested tables, scalars and arrays of scalars.
Anything outside that shape fails loudly rather than emitting a file the service would reject
at boot; every consuming chart pairs it with a verbatim `configExtraToml` escape hatch for the
rest.

Two details carry the whole thing:

  1. Scalars are emitted with `toRawJson`. A TOML basic string, integer, float, boolean and
     array-of-scalars are all spelled exactly as their JSON equivalents, so one conversion is
     correct for every leaf type, including escaping inside strings. `toRawJson`, not `toJson`:
     the latter HTML-escapes `<`, `>` and `&` into `\uXXXX`, which TOML does parse back to the
     same string, but which turns every URL carrying a query string into something no reviewer
     can read.

  2. **Every scalar in a table is emitted before any of its sub-tables.** In TOML a key
     belongs to the most recent `[table]` header, so emitting `[database]` before a sibling
     top-level scalar would silently reparent that scalar into `database`. Hence two passes,
     not one.

Keys outside TOML's bare-key alphabet are quoted (see `common.toml.key`), which is what makes
a table keyed by a request path or a URL pattern — `[bucket.entries."docs/handbook"]`,
`"/webhook/.*" = ["ALL"]` — render as valid TOML rather than as a parse error at boot.

Map iteration in Go templates is sorted by key, so output is deterministic and a no-op
`helm upgrade` produces a byte-identical ConfigMap — which matters for the services that watch
their configuration directory, because a rewritten file is what wakes the watcher.

Arguments:
  value   (required) the map to render
  prefix  parent table path, used by the recursion (callers pass nothing)

Usage: {{ include "common.toml" (dict "value" $map) | trim }}
*/}}
{{- define "common.toml" -}}
{{- $value := .value | default dict -}}
{{- $prefix := .prefix | default "" -}}
{{- range $k, $v := $value -}}
{{- if and (not (kindIs "map" $v)) (not (kindIs "invalid" $v)) -}}
{{- if kindIs "slice" $v -}}
{{- range $item := $v -}}
{{- if or (kindIs "map" $item) (kindIs "slice" $item) -}}
{{- fail (printf "common.toml: config key %q is an array of tables, which this renderer cannot express. Put it in `configExtraToml` verbatim instead." (printf "%s%s" (ternary (printf "%s." $prefix) "" (ne $prefix "")) $k)) -}}
{{- end -}}
{{- end -}}
{{- end }}
{{ include "common.toml.key" $k }} = {{ toRawJson $v }}
{{- end -}}
{{- end -}}
{{- range $k, $v := $value -}}
{{- if kindIs "map" $v -}}
{{- $key := include "common.toml.key" $k -}}
{{- $path := ternary (printf "%s.%s" $prefix $key) $key (ne $prefix "") }}

[{{ $path }}]
{{- include "common.toml" (dict "value" $v "prefix" $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
One TOML key, bare where TOML allows it and quoted where it does not.

TOML's bare keys are `[A-Za-z0-9_-]+`. Everything else — a request path, a regex, a dotted
name — has to be a quoted key, and a JSON string is exactly a TOML basic string, so
`toRawJson` both quotes and escapes it. Keys already inside the bare alphabet are emitted
untouched, so the ordinary snake_case configuration tree renders exactly as it did before
quoting existed.

Arguments: the key, as the dot value.
*/}}
{{- define "common.toml.key" -}}
{{- $key := . | toString -}}
{{- if regexMatch "^[A-Za-z0-9_-]+$" $key -}}
{{- $key -}}
{{- else -}}
{{- toRawJson $key -}}
{{- end -}}
{{- end -}}

{{/*
Merge a list of maps into one, deeply, later entries winning, and render it as TOML.

Used to fold the chart-derived wiring into the operator's own `config` tree before rendering,
so the two never end up as competing fragments that have to be ordered against each other.

Arguments:
  maps  (required) list of maps, lowest precedence first

Usage: {{ include "common.tomlMerged" (dict "maps" (list $derived $user)) }}
*/}}
{{- define "common.tomlMerged" -}}
{{- $merged := dict -}}
{{- range $m := .maps -}}
{{- $merged = mergeOverwrite $merged (deepCopy ($m | default dict)) -}}
{{- end -}}
{{- include "common.toml" (dict "value" $merged) -}}
{{- end -}}

{{/*
The complete TOML document a chart mounts: its merged configuration tree followed by the
verbatim escape hatch.

Arguments:
  ctx   (required) root context, for `.Values.configExtraToml`
  maps  (required) list of maps, lowest precedence first

Usage: {{ include "common.configToml" (dict "ctx" $ "maps" (list $derived $.Values.config)) }}
*/}}
{{- define "common.configToml" -}}
{{- include "common.tomlMerged" (dict "maps" .maps) | trim }}
{{- with .ctx.Values.configExtraToml }}

{{ . | trim }}
{{- end }}
{{- end -}}
