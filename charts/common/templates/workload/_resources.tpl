{{/*
Container resource requests and limits, emitted verbatim from `resources`.

There are no named t-shirt sizes. A preset hides the numbers that actually get scheduled behind
a word whose meaning differs per chart, and the only way to learn what `medium` reserved was to
read the library — so every chart states its requests and limits outright instead.

The convention those values follow: a memory limit and CPU/memory requests, but no CPU limit. A
CPU limit cannot prevent noisy neighbours the way a memory limit prevents OOM-ing the node; it
only causes CFS throttling of the workload that owns it once it is hit. A chart that genuinely
wants one sets `resources.limits.cpu`.

Usage:
  resources:
    {{- include "common.resources" . | nindent 12 }}
*/}}
{{- define "common.resources" -}}
{{- with .Values.resources -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}
