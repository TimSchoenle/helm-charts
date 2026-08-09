{{/*
TankoVault's TOML rendering, which is the `common` library's.

The renderer used to live here; it now backs every chart in this repository that mounts a
configuration file, so it belongs in the library the charts share. These two names stay as the
chart's own vocabulary — `tankovault.configToml` and the tests call them — and forward
unchanged, so the rendered ConfigMap is byte-identical to what the local implementation
produced.

See `common.toml` for the shape it accepts and why scalars are emitted before sub-tables.
`configExtraToml` remains the escape hatch for anything outside it, notably arrays of tables.
*/}}
{{- define "tankovault.toml" -}}
{{- include "common.toml" . -}}
{{- end -}}

{{- define "tankovault.tomlMerged" -}}
{{- include "common.tomlMerged" . -}}
{{- end -}}
