{{/*
Expand the name of the chart.
*/}}
{{- define "common.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "common.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create a fully qualified name for a subresource, truncated so the suffix always fits
within the 63 character limit.

Usage:
  {{ include "common.fullname.suffixed" (dict "ctx" $ "suffix" "ingress") }}
*/}}
{{- define "common.fullname.suffixed" -}}
{{- $suffix := .suffix -}}
{{- $max := int (sub 62 (len $suffix)) -}}
{{- printf "%s-%s" (include "common.fullname" .ctx | trunc $max | trimSuffix "-") $suffix -}}
{{- end -}}

{{/*
Create a content-addressed name for a subresource: the ordinary suffixed name with a truncated
digest of the caller's content appended.

For resources whose spec is immutable in place. A Job is the motivating case: `spec.template`
and `spec.selector` cannot be changed after creation, so re-applying a Job under a stable name
either fails with `field is immutable` or is accepted as a no-op that never re-runs the work.
Folding the spec into the name turns a spec change into a different object — the new one runs,
the old one is left for the pruner — while an unchanged spec resolves to the same name and the
completed Job stays untouched.

Eight hex characters. A collision means the caller silently reuses the existing object rather
than creating one; across the handful of distinct specs a single release ever produces that is
not a risk worth more characters, and the failure is a skipped re-run, not corruption.

Truncation is delegated to `common.fullname.suffixed`, so the 63 character limit is enforced by
exactly the same code and the digest can never be the part that gets cut.

Usage:
  {{ include "common.fullname.hashed" (dict "ctx" $ "suffix" "migrate" "content" $podTemplate) }}
*/}}
{{- define "common.fullname.hashed" -}}
{{- $suffix := printf "%s-%s" .suffix (sha256sum .content | trunc 8) -}}
{{- include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" $suffix) -}}
{{- end -}}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "common.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Allow the namespace to be overridden, defaulting to the release namespace.
*/}}
{{- define "common.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride -}}
{{- end -}}
