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

{{/*
Return the appropriate apiVersion for the Gateway API kinds.

Unlike every helper above, this one cannot branch on the Kubernetes version: Gateway API ships
as CRDs on its own release train, so a 1.34 cluster may have v1, v1beta1 or nothing at all.
The only honest source is the registered API surface.

The fallback is the GA version rather than an error. Callers that actually create an object
must gate on `common.gateway.validate` first, which refuses the render when neither version is
registered; this helper is also read by error messages, which have to name a version even in
the case where none exists.
*/}}
{{- define "common.capabilities.gateway.apiVersion" -}}
{{- if include "common.capabilities.apiVersions.has" (dict "ctx" . "api" "gateway.networking.k8s.io/v1") -}}
gateway.networking.k8s.io/v1
{{- else if include "common.capabilities.apiVersions.has" (dict "ctx" . "api" "gateway.networking.k8s.io/v1beta1") -}}
gateway.networking.k8s.io/v1beta1
{{- else -}}
gateway.networking.k8s.io/v1
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for the Cilium policy CRDs.

`cilium.io/v2` is the only version CiliumNetworkPolicy has ever been served under, so this is a
constant. It exists as a helper for the same reason `common.prometheus.apiVersion` does: the
guard, the render and the error messages must all name the same string.
*/}}
{{- define "common.capabilities.cilium.apiVersion" -}}
cilium.io/v2
{{- end -}}
