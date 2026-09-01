{{/*
Render a values map as TOML.

The applications behind these charts read a TOML configuration file, and neither Helm nor
Sprig ships a `toToml`. This renderer covers nested tables, scalars, arrays of scalars and
arrays of tables. Anything outside that shape fails loudly rather than emitting a file the
service would reject at boot; every consuming chart pairs it with a verbatim `configExtraToml`
escape hatch for the rest.

Four details carry the whole thing:

  1. Scalars are emitted with `toRawJson`. A TOML basic string, integer, float, boolean and
     array-of-scalars are all spelled exactly as their JSON equivalents, so one conversion is
     correct for every leaf type, including escaping inside strings. `toRawJson`, not `toJson`:
     the latter HTML-escapes `<`, `>` and `&` into `\uXXXX`, which TOML does parse back to the
     same string, but which turns every URL carrying a query string into something no reviewer
     can read.

  2. **Every scalar in a table is emitted before any of its sub-tables.** In TOML a key
     belongs to the most recent `[table]` header, so emitting `[database]` before a sibling
     top-level scalar would silently reparent that scalar into `database`. Hence three passes,
     not one.

  3. **An array of tables is `[[name]]`, and it is a third pass for the same reason.** A TOML
     header names an absolute path, so `[[routes]]` opens a new element and the `[routes.target]`
     after it belongs to *that* element — the elements are emitted one after another, each
     followed by its own sub-tables, and a sibling table emitted later re-anchors itself simply
     by naming its own full path. Which of TOML's two array forms a slice is depends on what is
     inside it, and `common.toml.arrayKind` is where that is decided once: an empty slice and a
     slice of scalars are `key = [...]`, a slice of tables is `[[key]]`, and a slice holding both
     has no spelling in TOML at all.

  4. **`intKeys` names the leaves that are integers too large for Helm to hold.** Helm parses a
     values *file* through `encoding/json`, so every number in one arrives as a `float64` and
     anything above 2^53 is silently rounded before a template ever runs: a Discord snowflake
     written as `guild_id: 123456789012345678` reaches the chart as `123456789012345680`, and
     nothing downstream can tell. The only faithful spelling in a values file is a quoted string,
     and `intKeys` is where a caller says which of its string leaves are to be written back out
     as TOML integers. A path that is listed and does not hold digits fails by name rather than
     emitting a quoted number the service would reject.

Keys outside TOML's bare-key alphabet are quoted (see `common.toml.key`), which is what makes
a table keyed by a request path or a URL pattern — `[bucket.entries."docs/handbook"]`,
`"/webhook/.*" = ["ALL"]` — render as valid TOML rather than as a parse error at boot.

Map iteration in Go templates is sorted by key, so output is deterministic and a no-op
`helm upgrade` produces a byte-identical ConfigMap — which matters for the services that watch
their configuration directory, because a rewritten file is what wakes the watcher.

Arguments:
  value    (required) the map to render
  prefix   parent table path, used by the recursion (callers pass nothing)
  intKeys  dotted paths whose string leaves are written as TOML integers. An array element's
           index is not part of the path, so one entry covers every element of an array of
           tables: `routes.target.id` names that key in all of them.

Usage: {{ include "common.toml" (dict "value" $map) | trim }}
*/}}
{{- define "common.toml" -}}
{{- $value := .value | default dict -}}
{{- $prefix := .prefix | default "" -}}
{{- $intKeys := .intKeys | default list -}}
{{- /* Pass 1: scalars and inline arrays, before any header that would reparent them. */ -}}
{{- range $k, $v := $value -}}
{{- if and (not (kindIs "map" $v)) (not (kindIs "invalid" $v)) -}}
{{- if ne (include "common.toml.arrayKind" (dict "value" $v "prefix" $prefix "key" $k)) "tables" -}}
{{- $key := include "common.toml.key" $k -}}
{{- $path := ternary (printf "%s.%s" $prefix $key) $key (ne $prefix "") }}
{{ $key }} = {{ include "common.toml.scalar" (dict "value" $v "path" $path "intKeys" $intKeys) }}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* Pass 2: sub-tables. */ -}}
{{- range $k, $v := $value -}}
{{- if kindIs "map" $v -}}
{{- $key := include "common.toml.key" $k -}}
{{- $path := ternary (printf "%s.%s" $prefix $key) $key (ne $prefix "") }}

[{{ $path }}]
{{- include "common.toml" (dict "value" $v "prefix" $path "intKeys" $intKeys) -}}
{{- end -}}
{{- end -}}
{{- /* Pass 3: arrays of tables, each element followed by its own sub-tables. */ -}}
{{- range $k, $v := $value -}}
{{- if eq (include "common.toml.arrayKind" (dict "value" $v "prefix" $prefix "key" $k)) "tables" -}}
{{- $key := include "common.toml.key" $k -}}
{{- $path := ternary (printf "%s.%s" $prefix $key) $key (ne $prefix "") -}}
{{- range $item := $v }}

[[{{ $path }}]]
{{- include "common.toml" (dict "value" $item "prefix" $path "intKeys" $intKeys) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
One TOML value: a JSON scalar, or an integer this chart was told to write as one.

`toRawJson` is the whole conversion for every ordinary leaf — see the header — and `intKeys` is
the one exception, for the values Helm cannot carry as numbers at all.

Arguments:
  value    (required) the leaf
  path     (required) its dotted path, for `intKeys` and for the message
  intKeys  dotted paths to write as TOML integers
*/}}
{{- define "common.toml.scalar" -}}
{{- $path := .path -}}
{{- if not (has $path (.intKeys | default list)) -}}
{{- toRawJson .value -}}
{{- else if kindIs "slice" .value -}}
{{- $out := list -}}
{{- range $item := .value -}}
{{- $out = append $out (include "common.toml.integer" (dict "value" $item "path" $path)) -}}
{{- end -}}
[{{ join "," $out }}]
{{- else -}}
{{- include "common.toml.integer" (dict "value" .value "path" $path) -}}
{{- end -}}
{{- end -}}

{{/*
One integer named by `intKeys`, written without quotes.

A string is the spelling that survives Helm's values parser, so it is the expected one and is
checked before it is unquoted: a listed path holding something that is not digits would otherwise
emit a bare token that is not valid TOML at all.

A number reaching here was written unquoted in the values file and has *already* been through
`float64` — so it is passed through unchanged when it is small enough to have survived that, and
refused when it is not. Refusing is the point: the value is wrong by then, and emitting it would
send an operator's alerts to whatever channel the rounded id happens to name.

Arguments:
  value  (required) the leaf
  path   (required) its dotted path, for the message
*/}}
{{- define "common.toml.integer" -}}
{{- $v := .value -}}
{{- if kindIs "string" $v -}}
{{- if not (regexMatch "^-?[0-9]+$" $v) -}}
{{- fail (printf "common.toml: config key %q is written as a TOML integer, but holds %q. Quote the digits and nothing else." .path $v) -}}
{{- end -}}
{{- $v -}}
{{- else if and (kindIs "float64" $v) (gt $v 9007199254740992.0) -}}
{{- fail (printf "common.toml: config key %q holds %v, which is above 2^53 and was already rounded by the time this chart saw it — Helm parses every number in a values file as a float64. Quote it: `%s: \"<digits>\"`." .path $v (last (splitList "." .path))) -}}
{{- else -}}
{{- toRawJson $v -}}
{{- end -}}
{{- end -}}

{{/*
Which of TOML's two array forms a slice is: `tables` or `scalars`, and empty for anything that
is not a slice at all, so a caller can compare without testing the kind twice.

The distinction is the element type and nothing else, which is why it is decided here rather
than inferred at each of the two call sites: a slice read as the wrong form emits a file that
parses and means something different, and that is the failure this whole renderer exists to
avoid.

Two shapes have no TOML spelling and fail by name rather than being emitted:

  - **A mixed array.** `[[k]]` and `k = [...]` are different syntax, not two encodings of one
    value, so an array holding both a table and a scalar cannot be written at all.
  - **An array of arrays.** TOML has no nested header form; the inline `[[1, 2], [3]]` it does
    have is an array of *arrays of scalars*, which is not what a slice of slices coming out of
    a values tree means here.

Arguments:
  value   (required) the value to classify
  prefix  parent table path, for the message
  key     the key holding it, for the message
*/}}
{{- define "common.toml.arrayKind" -}}
{{- if kindIs "slice" .value -}}
{{- $where := printf "%s%s" (ternary (printf "%s." .prefix) "" (ne (.prefix | toString) "")) (.key | toString) -}}
{{- $tables := 0 -}}
{{- $scalars := 0 -}}
{{- range $item := .value -}}
{{- if kindIs "map" $item -}}
{{- $tables = add1 $tables -}}
{{- else if kindIs "slice" $item -}}
{{- fail (printf "common.toml: config key %q holds an array of arrays, which TOML has no header form for. Put it in `configExtraToml` verbatim instead." $where) -}}
{{- else -}}
{{- $scalars = add1 $scalars -}}
{{- end -}}
{{- end -}}
{{- if and (gt $tables 0) (gt $scalars 0) -}}
{{- fail (printf "common.toml: config key %q mixes tables and scalars in one array. TOML spells an array of tables `[[%s]]` and an inline array `%s = [...]`; no array is both, so this cannot be written at all." $where $where $where) -}}
{{- end -}}
{{- ternary "tables" "scalars" (gt $tables 0) -}}
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
  maps     (required) list of maps, lowest precedence first
  intKeys  dotted paths to write as TOML integers; see `common.toml`

Usage: {{ include "common.tomlMerged" (dict "maps" (list $derived $user)) }}
*/}}
{{- define "common.tomlMerged" -}}
{{- $merged := dict -}}
{{- range $m := .maps -}}
{{- $merged = mergeOverwrite $merged (deepCopy ($m | default dict)) -}}
{{- end -}}
{{- include "common.toml" (dict "value" $merged "intKeys" (.intKeys | default list)) -}}
{{- end -}}

{{/*
The complete TOML document a chart mounts: its merged configuration tree followed by the
verbatim escape hatch.

Arguments:
  ctx      (required) root context, for `.Values.configExtraToml`
  maps     (required) list of maps, lowest precedence first
  intKeys  dotted paths to write as TOML integers; see `common.toml`

Usage: {{ include "common.configToml" (dict "ctx" $ "maps" (list $derived $.Values.config)) }}
*/}}
{{- define "common.configToml" -}}
{{- include "common.tomlMerged" (dict "maps" .maps "intKeys" (.intKeys | default list)) | trim }}
{{- with .ctx.Values.configExtraToml }}

{{ . | trim }}
{{- end }}
{{- end -}}
