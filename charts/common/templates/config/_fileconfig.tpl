{{/*
File-backed configuration, delegated to the `terrace-helm` library chart this library depends on;
its templates carry the full documentation.

`terrace-helm` takes the ConfigMap and Secret names as arguments. These wrappers supply them from
this library's naming, `common.configMapName` and `common.secretName`, so the `ctx`-only call
signature every chart here uses is unchanged.

Arguments: as for the `terrace-helm.fileConfig.*` template of the same name, with `ctx` (the root
context) in place of the names.
*/}}

{{- define "common.fileConfig.secretSources" -}}
{{- include "terrace-helm.fileConfig.secretSources" (dict "secretName" (include "common.secretName" .ctx) "keys" .keys) -}}
{{- end -}}

{{- define "common.fileConfig.volumes" -}}
{{- include "terrace-helm.fileConfig.volumes" (dict "configMapName" (include "common.configMapName" .ctx) "secretName" (include "common.secretName" .ctx) "secretKeys" .secretKeys) -}}
{{- end -}}

{{- define "common.fileConfig.volumeMounts" -}}
{{- include "terrace-helm.fileConfig.volumeMounts" . -}}
{{- end -}}

{{- define "common.fileConfig.env" -}}
{{- include "terrace-helm.fileConfig.env" . -}}
{{- end -}}

{{- define "common.fileConfig.secretData" -}}
{{- include "terrace-helm.fileConfig.secretData" . -}}
{{- end -}}

{{- define "common.fileConfig.secretKeys" -}}
{{- include "terrace-helm.fileConfig.secretKeys" . -}}
{{- end -}}

{{- define "common.fileConfig.createSecret" -}}
{{- include "terrace-helm.fileConfig.createSecret" . -}}
{{- end -}}
