{{/*
Container resource requests and limits.

`resources` (explicit) wins over `resourcesPreset` (a named t-shirt size). Presets set a
memory limit and CPU/memory requests but deliberately omit a CPU limit: a CPU limit cannot
prevent noisy neighbours the way a memory limit prevents OOM-ing the node, it only causes
CFS throttling of your own workload once it is hit. Charts that genuinely need one can set
`resources.limits.cpu` directly.

Usage:
  resources:
    {{- include "common.resources" . | nindent 12 }}
*/}}
{{- define "common.resources" -}}
{{- $presets := dict
      "nano"   (dict "requests" (dict "cpu" "10m"  "memory" "32Mi")  "limits" (dict "memory" "64Mi"))
      "micro"  (dict "requests" (dict "cpu" "25m"  "memory" "64Mi")  "limits" (dict "memory" "128Mi"))
      "small"  (dict "requests" (dict "cpu" "50m"  "memory" "128Mi") "limits" (dict "memory" "256Mi"))
      "medium" (dict "requests" (dict "cpu" "100m" "memory" "256Mi") "limits" (dict "memory" "512Mi"))
      "large"  (dict "requests" (dict "cpu" "250m" "memory" "512Mi") "limits" (dict "memory" "1Gi"))
-}}
{{- $preset := .Values.resourcesPreset | default "" -}}
{{- $resources := .Values.resources | default dict -}}
{{- if $resources -}}
{{- toYaml $resources -}}
{{- else if $preset -}}
{{- if not (hasKey $presets $preset) -}}
{{- fail (printf "resourcesPreset %q is not one of: %s" $preset (keys $presets | sortAlpha | join ", ")) -}}
{{- end -}}
{{- toYaml (get $presets $preset) -}}
{{- end -}}
{{- end -}}
