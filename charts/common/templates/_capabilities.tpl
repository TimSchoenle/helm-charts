{{/*
Return the Kubernetes version to branch on, as a bare semver.

`.Capabilities.KubeVersion.GitVersion` carries vendor suffixes on managed clusters
(`v1.29.4-gke.1043000`, `v1.28.9-eks-036c24b`). `semverCompare` treats those suffixes as
pre-release identifiers, so `v1.29.4-gke.1043000` compares *lower* than `1.29.0` and the
API-version branches below silently pick the older, often removed, API. Strip to
major.minor.patch first.

`kubeVersionOverride` lets `helm template --dry-run` (no cluster) target a specific version.
*/}}
{{- define "common.capabilities.kubeVersion" -}}
{{- if .Values.kubeVersionOverride -}}
{{- .Values.kubeVersionOverride -}}
{{- else -}}
{{- regexFind "^v?[0-9]+\\.[0-9]+(\\.[0-9]+)?" .Capabilities.KubeVersion.GitVersion -}}
{{- end -}}
{{- end -}}

{{/*
Return true if the given API is registered on the target cluster.

Usage:
  {{ if (include "common.capabilities.apiVersions.has" (dict "ctx" $ "api" "monitoring.coreos.com/v1")) }}
*/}}
{{- define "common.capabilities.apiVersions.has" -}}
{{- if .ctx.Capabilities.APIVersions.Has .api -}}
true
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for PodDisruptionBudget
*/}}
{{- define "common.capabilities.pdb.apiVersion" -}}
{{- if semverCompare ">=1.21-0" (include "common.capabilities.kubeVersion" .) -}}
policy/v1
{{- else -}}
policy/v1beta1
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for HorizontalPodAutoscaler
*/}}
{{- define "common.capabilities.hpa.apiVersion" -}}
{{- if semverCompare ">=1.23-0" (include "common.capabilities.kubeVersion" .) -}}
autoscaling/v2
{{- else -}}
autoscaling/v2beta2
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for Ingress
*/}}
{{- define "common.capabilities.ingress.apiVersion" -}}
{{- if semverCompare ">=1.19-0" (include "common.capabilities.kubeVersion" .) -}}
networking.k8s.io/v1
{{- else if semverCompare ">=1.14-0" (include "common.capabilities.kubeVersion" .) -}}
networking.k8s.io/v1beta1
{{- else -}}
extensions/v1beta1
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for NetworkPolicy
*/}}
{{- define "common.capabilities.networkPolicy.apiVersion" -}}
{{- if semverCompare ">=1.7-0" (include "common.capabilities.kubeVersion" .) -}}
networking.k8s.io/v1
{{- else -}}
extensions/v1beta1
{{- end -}}
{{- end -}}
