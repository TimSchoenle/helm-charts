{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
server reads.

`dist_dir` and `data_dir` are restated rather than inherited. The image ships a `/config.toml`
and points `MP_STATS_CONFIG` at it; pointing that variable at this chart's mount **replaces**
that file rather than layering over it, so every key it carried has to be carried here or the
server falls back to the workspace-relative defaults and refuses to start on a missing
`index.html`.
*/}}
{{- define "mp-stats-legacy-viewer.derivedConfig" -}}
server:
  bind_addr: {{ printf "%s:%v" .Values.server.host (.Values.server.port | toString) | quote }}
  dist_dir: {{ .Values.server.distDir | quote }}
  data_dir: {{ .Values.server.dataDir | quote }}
{{- end -}}

{{/*
The configuration that actually reaches the server: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "mp-stats-legacy-viewer.effectiveConfig" -}}
{{- $derived := include "mp-stats-legacy-viewer.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "mp-stats-legacy-viewer.configToml" -}}
{{- $config := include "mp-stats-legacy-viewer.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}
