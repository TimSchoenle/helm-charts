{{/*
Pod-level security context.

The library supplies the *policy* fields required by the restricted Pod Security Standard
and leaves identity (runAsUser / runAsGroup / fsGroup) to the chart, because those must
match the UID baked into each application image. Chart values are merged on top, so any
individual field can be overridden or removed by setting it explicitly.

Set `podSecurityContextPreset: none` to opt out of the defaults entirely.
*/}}
{{- define "common.podSecurityContext" -}}
{{- $defaults := dict -}}
{{- if ne (.Values.podSecurityContextPreset | default "restricted") "none" -}}
{{- $defaults = dict
      "runAsNonRoot" true
      "fsGroupChangePolicy" "OnRootMismatch"
      "seccompProfile" (dict "type" "RuntimeDefault")
-}}
{{- end -}}
{{- $merged := mustMergeOverwrite (deepCopy $defaults) (deepCopy (.Values.podSecurityContext | default dict)) -}}
{{- if $merged -}}
{{- toYaml $merged -}}
{{- end -}}
{{- end -}}

{{/*
Container-level security context.

Defaults satisfy the restricted Pod Security Standard: no privilege escalation, all Linux
capabilities dropped, immutable root filesystem. Chart values are merged on top.

Set `securityContextPreset: none` to opt out of the defaults entirely.
*/}}
{{- define "common.containerSecurityContext" -}}
{{- $defaults := dict -}}
{{- if ne (.Values.securityContextPreset | default "restricted") "none" -}}
{{- $defaults = dict
      "allowPrivilegeEscalation" false
      "privileged" false
      "readOnlyRootFilesystem" true
      "runAsNonRoot" true
      "capabilities" (dict "drop" (list "ALL"))
-}}
{{- end -}}
{{- $merged := mustMergeOverwrite (deepCopy $defaults) (deepCopy (.Values.securityContext | default dict)) -}}
{{- if $merged -}}
{{- toYaml $merged -}}
{{- end -}}
{{- end -}}

{{/*
Whether the container ends up with a read-only root filesystem.

Charts use this to decide whether to provision the writable /tmp emptyDir that a read-only
root filesystem makes necessary. Returns "true" or the empty string.
*/}}
{{- define "common.readOnlyRootFilesystem" -}}
{{- $sc := fromYaml (include "common.containerSecurityContext" .) -}}
{{- if $sc.readOnlyRootFilesystem -}}
true
{{- end -}}
{{- end -}}
