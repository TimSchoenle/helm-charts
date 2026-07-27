{{/*
Checksum of the configuration a template contributes to the pod.

Hashes only the `data`, `stringData` and `binaryData` of the rendered documents — never the
surrounding manifest. Hashing the raw render, as this helper used to, folded `metadata` into
the digest, so `helm.sh/chart` alone made every chart version bump roll every pod and break
every committed snapshot even when no configuration had changed. It also made the digest
depend on the template engine's whitespace handling, so two Helm builds could disagree on
the checksum for a byte-identical ConfigMap.

Documents are keyed by `metadata.name`, so neither key order, document order nor formatting
affects the result: only the mounted values do.

Renders empty when the template produces no configuration at all (a Secret behind a disabled
`if`, say), so the caller omits the annotation instead of publishing a constant hash of the
empty string.

Arguments:
  ctx       (required) root context
  template  template path relative to the chart's templates/ directory
*/}}
{{- define "common.configChecksum" -}}
{{- $ctx := .ctx -}}
{{- $path := .template -}}
{{- $documents := dict -}}
{{- range $index, $document := splitList "\n---" (include (print $ctx.Template.BasePath "/" $path) $ctx) -}}
{{- $parsed := $document | fromYaml -}}
{{- with $parsed.Error -}}
{{- fail (printf "common.configChecksum: %q did not render valid YAML: %s" $path .) -}}
{{- end -}}
{{- $configuration := dict -}}
{{- range $field := (list "data" "stringData" "binaryData") -}}
{{- with ($parsed | dig $field dict) -}}
{{- $_ := set $configuration $field . -}}
{{- end -}}
{{- end -}}
{{- with $configuration -}}
{{- $_ := set $documents ($parsed | dig "metadata" "name" (printf "document-%d" $index)) . -}}
{{- end -}}
{{- end -}}
{{- with $documents -}}
{{- toYaml . | sha256sum -}}
{{- end -}}
{{- end -}}

{{/*
Pod annotations, including config checksums that force a rollout when mounted
configuration changes.

Replaces the previous `common.podAnnotations`, which hardcoded
`include (print $.Template.BasePath "/configmap.yaml")` and therefore failed to render in
any chart that has no configmap.yaml. Template paths are now passed in explicitly, so a
path that does not resolve is a template error rather than silent breakage.

Templates that render no configuration contribute no annotation, so the checksum appearing
or disappearing is itself a meaningful rollout trigger.

Arguments:
  ctx        (required) root context
  templates  list of template paths relative to the chart's templates/ directory
             (e.g. list "configmap.yaml" "secret.yaml")

Usage:
  {{- with (include "common.podAnnotations" (dict "ctx" $ "templates" (list "configmap.yaml"))) }}
  annotations:
    {{- . | nindent 8 }}
  {{- end }}
*/}}
{{- define "common.podAnnotations" -}}
{{- $ctx := .ctx -}}
{{- $annotations := dict -}}
{{- range $path := .templates | default list -}}
{{- with (include "common.configChecksum" (dict "ctx" $ctx "template" $path)) -}}
{{- $_ := set $annotations (printf "checksum/%s" ($path | trimSuffix ".yaml" | replace "." "-")) . -}}
{{- end -}}
{{- end -}}
{{- $annotations = mustMergeOverwrite $annotations (deepCopy ($ctx.Values.podAnnotations | default dict)) -}}
{{- with $annotations -}}
{{- include "common.tplvalues.render" (dict "value" . "context" $ctx) -}}
{{- end -}}
{{- end -}}
