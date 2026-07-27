{{/*
Name of the ServiceAccount the pod should run as.
*/}}
{{- define "common.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "common.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

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
