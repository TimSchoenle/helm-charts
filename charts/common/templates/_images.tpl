{{/*
Build a fully qualified container image reference.

Two call forms are accepted:
  {{ include "common.image" . }}                                      -> .Values.image
  {{ include "common.image" (dict "ctx" $ "image" .Values.sidecar) }} -> explicit image dict


Resolution order for the identifier: `image.digest` wins over `image.tag`; a tag that
already embeds a digest (`v1.2.3@sha256:...`) is passed through untouched, so charts
migrating from the old single-field form keep working.
*/}}
{{- define "common.image" -}}
{{- $ctx := . -}}
{{- $image := dict -}}
{{- if hasKey . "ctx" -}}
{{- $ctx = .ctx -}}
{{- $image = .image | default $ctx.Values.image -}}
{{- else -}}
{{- $image = .Values.image -}}
{{- end -}}
{{- $registry := $image.registry | default "" -}}
{{- $repository := $image.repository | required "image.repository is required" -}}
{{- $tag := $image.tag | default $ctx.Chart.AppVersion | toString -}}
{{- $digest := $image.digest | default "" -}}
{{- $name := $repository -}}
{{- if $registry -}}
{{- $name = printf "%s/%s" $registry $repository -}}
{{- end -}}
{{- if $digest -}}
{{- printf "%s@%s" $name $digest -}}
{{- else if contains "@" $tag -}}
{{- printf "%s@%s" $name (splitList "@" $tag | last) -}}
{{- else -}}
{{- printf "%s:%s" $name $tag -}}
{{- end -}}
{{- end -}}

{{/*
Resolve the image pull policy.

An explicit `image.pullPolicy` always wins. Otherwise a mutable `latest` tag gets `Always`
(anything else pins the node to whatever it happened to cache first) while a pinned tag or
a digest gets `IfNotPresent`.
*/}}
{{- define "common.imagePullPolicy" -}}
{{- $ctx := . -}}
{{- $image := dict -}}
{{- if hasKey . "ctx" -}}
{{- $ctx = .ctx -}}
{{- $image = .image | default $ctx.Values.image -}}
{{- else -}}
{{- $image = .Values.image -}}
{{- end -}}
{{- $tag := $image.tag | default $ctx.Chart.AppVersion | toString -}}
{{- if $image.pullPolicy -}}
{{- $image.pullPolicy -}}
{{- else if $image.digest -}}
IfNotPresent
{{- else if or (eq $tag "latest") (hasSuffix ":latest" $tag) -}}
Always
{{- else -}}
IfNotPresent
{{- end -}}
{{- end -}}

{{/*
Render the imagePullSecrets block, if any are configured.
Emits nothing when the list is empty, so callers can `nindent` unconditionally.
Accepts both `- name: regcred` and the shorthand `- regcred`.
*/}}
{{- define "common.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
{{- if typeIs "string" . }}
  - name: {{ . }}
{{- else }}
  - {{ toYaml . | nindent 4 | trim }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}
