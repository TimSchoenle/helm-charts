{{/*
Build a fully qualified container image reference.

Two call forms are accepted:
  {{ include "common.image" . }}                                      -> .Values.image
  {{ include "common.image" (dict "ctx" $ "image" .Values.sidecar) }} -> explicit image dict


Tag and digest are combined rather than ranked: a digest alongside a tag renders as
`repo:v1.2.3@sha256:...`. The digest is what actually pins the pull, while the tag is
retained because it is the only human-readable version marker visible in `kubectl get pod`,
event logs and registry UIs. Dropping it in favour of the digest alone buys no extra
immutability and costs all of that legibility.

A tag that already embeds a digest (`v1.2.3@sha256:...`) is split and recombined into the
same form, so charts using the old single-field style keep working. Supplying an embedded
digest and `image.digest` that disagree is ambiguous and fails the render rather than
silently picking one.
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
{{- $digest := $image.digest | default "" | toString -}}
{{- if contains "@" $tag -}}
{{- $parts := splitList "@" $tag -}}
{{- $embedded := $parts | last -}}
{{- if and $digest (ne $digest $embedded) -}}
{{- fail (printf "common.image: image.tag embeds digest %q but image.digest is %q; set only one" $embedded $digest) -}}
{{- end -}}
{{- $tag = index $parts 0 -}}
{{- $digest = $embedded -}}
{{- end -}}
{{- $name := $repository -}}
{{- if $registry -}}
{{- $name = printf "%s/%s" $registry $repository -}}
{{- end -}}
{{- if $tag -}}
{{- $name = printf "%s:%s" $name $tag -}}
{{- end -}}
{{- if $digest -}}
{{- $name = printf "%s@%s" $name $digest -}}
{{- end -}}
{{- $name -}}
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
{{- $digest := $image.digest | default "" | toString -}}
{{- if contains "@" $tag -}}
{{- $digest = $tag | splitList "@" | last -}}
{{- $tag = $tag | splitList "@" | first -}}
{{- end -}}
{{- if $image.pullPolicy -}}
{{- $image.pullPolicy -}}
{{- else if $digest -}}
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
