{{/*
The volume backing /app/data, where the bot records which offers it has already announced.

Resolves to an existing claim, the claim this chart creates, or — when persistence is
disabled — an emptyDir, in which case every restart re-announces all current offers.
*/}}
{{- define "netcup-offer-bot.dataVolume" -}}
name: data
{{- if not .Values.persistence.data.enabled }}
emptyDir: {}
{{- else }}
persistentVolumeClaim:
  claimName: {{ .Values.persistence.data.existingClaim | default (include "common.fullname" .) }}
{{- end }}
{{- end -}}
