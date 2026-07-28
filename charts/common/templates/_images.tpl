{{/*
Build a fully qualified container image reference.

Two call forms are accepted:
  {{ include "common.image" . }}                                      -> .Values.image
  {{ include "common.image" (dict "ctx" $ "image" .Values.sidecar) }} -> explicit image dict


`image.tag` is the single source of the image version and may pin a digest inline
(`v1.2.3@sha256:...`), which renders as `repository:v1.2.3@sha256:...`. The digest is what
actually pins the pull, while the tag is kept alongside it because it is the only
human-readable version marker visible in `kubectl get pod`, event logs and registry UIs.
Dropping it in favour of a bare digest buys no extra immutability and costs all of that
legibility.
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
{{- $name := $repository -}}
{{- if $registry -}}
{{- $name = printf "%s/%s" $registry $repository -}}
{{- end -}}
{{- if $tag -}}
{{- $name = printf "%s:%s" $name $tag -}}
{{- end -}}
{{- $name -}}
{{- end -}}

{{/*
Resolve the image pull policy.

An explicit `image.pullPolicy` always wins. Otherwise a mutable `latest` tag gets `Always`
(anything else pins the node to whatever it happened to cache first) while a tag pinned by
version or by an embedded digest gets `IfNotPresent`.
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
{{- else if contains "@" $tag -}}
IfNotPresent
{{- else if eq $tag "latest" -}}
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
