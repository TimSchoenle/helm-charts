{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
server reads.

`PORT`, `IP` and `RUST_LOG` are deliberately absent: they belong to the Dioxus toolchain, which
reads them from the environment itself, not to the `PORTFOLIO_` namespace this file describes.
*/}}
{{- define "portfolio.derivedConfig" -}}
assets:
  dist_dir: {{ .Values.assets.distDir | quote }}
isr:
  cache_dir: {{ .Values.isr.cacheDir | quote }}
  ttl_secs: {{ .Values.isr.ttlSecs }}
{{- end -}}

{{/*
The configuration that actually reaches the server: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "portfolio.effectiveConfig" -}}
{{- $derived := include "portfolio.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "portfolio.configToml" -}}
{{- $config := include "portfolio.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}

{{/*
The container environment.

Three variables the Dioxus toolchain reads for itself, one that points the layered loader at the
mounted configuration — and one that exists only to defeat the image.

`PORTFOLIO_ISR__CACHE_DIR` is baked into the published image, and the environment layer outranks
the TOML layer. Left alone, that baked value would silently win over whatever this chart wrote
into `config.toml`, so an operator who moved the cache would find it had not moved. Emitting the
variable with the *effective* value — the same one the file carries — makes the two agree by
construction. The environment and the file are not mutually exclusive layers, so supplying both
is legal; only the environment, the secrets directory and `_FILE` collide with one another.
*/}}
{{- define "portfolio.env" -}}
{{- $config := include "portfolio.effectiveConfig" . | fromYaml }}
- name: PORT
  value: {{ .Values.server.port | quote }}
- name: IP
  value: {{ .Values.server.host | quote }}
- name: RUST_LOG
  value: {{ .Values.logLevel | quote }}
- name: PORTFOLIO_CONFIG
  value: {{ .Values.configMount.configDir | quote }}
- name: PORTFOLIO_ISR__CACHE_DIR
  value: {{ $config | dig "isr" "cache_dir" "" | quote }}
{{- end -}}
