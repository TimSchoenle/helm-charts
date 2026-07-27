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
