{{/*
Shared scaffolding for the bundled datastores.

These are deliberately plain: one replica, one PVC, no operator and no clustering. They exist
so `helm install` produces a working stack on a bare cluster, and they are documented as
evaluation-tier throughout. `externalDatabase`, `externalRedis` and `externalNats` are the
production path, and `tankovault.validateValues` makes sure exactly one of the two is chosen.

Args: ctx (root), component (name suffix and label), values (the datastore's values block).
*/}}
{{- define "tankovault.datastore.metadata" -}}
name: {{ include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" .component) }}
namespace: {{ include "common.namespace" .ctx }}
labels:
  {{- include "common.labels" .ctx | nindent 2 }}
  app.kubernetes.io/component: {{ .component }}
{{- with (include "common.annotations" .ctx) }}
annotations:
  {{- . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Selector labels for a bundled datastore. `common.selectorLabels` alone would be identical for
every workload in the release, so the component is what actually distinguishes them.
*/}}
{{- define "tankovault.datastore.selectorLabels" -}}
{{- include "common.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
A scoped render context for a bundled datastore, so `common.resources`,
`common.containerSecurityContext` and `common.podSecurityContext` apply to it too.

Args: ctx (root), component, resources, runAsUser, readOnlyRootFilesystem.
*/}}
{{- define "tankovault.datastore.values" -}}
{{- $ctx := .ctx -}}
{{- /*
  `nameOverride` is pinned to the root context's resolved name, exactly as
  `tankovault.serviceValues` pins it for the services. Without it the scoped context has no
  `nameOverride` at all and `common.name` falls back to the chart name, while the selector
  and the object metadata are rendered against the root. Under `nameOverride` the two
  disagree and the API server rejects the workload outright — `selector` does not match
  template `labels` — so the setting breaks every bundled datastore rather than renaming it.
*/ -}}
{{- $values := dict
      "nameOverride" (include "common.name" $ctx)
      "image" (merge (deepCopy .image) (deepCopy ($ctx.Values.image | default dict)))
      "imagePullSecrets" ($ctx.Values.imagePullSecrets | default list)
      "resources" (.resources | default dict)
      "podSecurityContextPreset" "restricted"
      "podSecurityContext" (dict "runAsUser" (.runAsUser | int) "runAsGroup" (.runAsGroup | int) "fsGroup" (.fsGroup | int))
      "securityContextPreset" "restricted"
      "securityContext" dict
      "automountServiceAccountToken" false
      "serviceAccount" (dict "create" $ctx.Values.serviceAccount.create "name" (include "tankovault.serviceAccountName" $ctx))
      "startupProbe" (.startupProbe | default (dict "enabled" false))
      "livenessProbe" (.livenessProbe | default (dict "enabled" false))
      "readinessProbe" (.readinessProbe | default (dict "enabled" false))
-}}
{{- toYaml $values -}}
{{- end -}}
