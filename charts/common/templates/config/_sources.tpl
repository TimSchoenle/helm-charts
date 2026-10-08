{{/*
Where a chart's configuration and credentials come from: the operator's existing ConfigMap or
Secret when one is named, otherwise the object the chart creates itself.
*/}}

{{/*
Name of the Secret to consume: an operator-supplied `existingSecret` if set, otherwise the
one this chart creates.
*/}}
{{- define "common.secretName" -}}
{{- if .Values.existingSecret -}}
{{- tpl .Values.existingSecret . -}}
{{- else -}}
{{- include "common.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
Whether this chart should create the Secret itself.
*/}}
{{- define "common.createSecret" -}}
{{- if not .Values.existingSecret -}}
true
{{- end -}}
{{- end -}}

{{/*
Name of the ConfigMap to consume: an operator-supplied `existingConfigMap` if set,
otherwise the one this chart creates.
*/}}
{{- define "common.configMapName" -}}
{{- if .Values.existingConfigMap -}}
{{- tpl .Values.existingConfigMap . -}}
{{- else -}}
{{- include "common.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
Whether this chart should create the ConfigMap itself.
*/}}
{{- define "common.createConfigMap" -}}
{{- if not .Values.existingConfigMap -}}
true
{{- end -}}
{{- end -}}
