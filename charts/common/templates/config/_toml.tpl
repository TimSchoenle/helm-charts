{{/*
TOML rendering, delegated to the `terrace-helm` library chart this library depends on.

The renderer lives in `terrace-helm` so that Helm repositories outside this one can depend on it
instead of copying it; its templates carry the full documentation. These names stay so that no
chart here has to change: each one includes its `terrace-helm` counterpart with the same
arguments and renders exactly what it does.
*/}}

{{- define "common.toml" -}}
{{- include "terrace-helm.toml" . -}}
{{- end -}}

{{- define "common.toml.scalar" -}}
{{- include "terrace-helm.toml.scalar" . -}}
{{- end -}}

{{- define "common.toml.integer" -}}
{{- include "terrace-helm.toml.integer" . -}}
{{- end -}}

{{- define "common.toml.arrayKind" -}}
{{- include "terrace-helm.toml.arrayKind" . -}}
{{- end -}}

{{- define "common.toml.key" -}}
{{- include "terrace-helm.toml.key" . -}}
{{- end -}}

{{- define "common.tomlMerged" -}}
{{- include "terrace-helm.tomlMerged" . -}}
{{- end -}}

{{- define "common.configToml" -}}
{{- include "terrace-helm.configToml" . -}}
{{- end -}}

{{- define "common.toml.extraErrors" -}}
{{- include "terrace-helm.toml.extraErrors" . -}}
{{- end -}}
