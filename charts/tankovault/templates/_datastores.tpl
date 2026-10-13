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

Args: ctx (root), component, resources, runAsUser, readOnlyRootFilesystem, and optionally
podAntiAffinity/affinity/nodeSelector/tolerations/topologySpreadConstraints/priorityClassName/
strategy. Unlike the per-service workloads, a bundled datastore has no `defaults` block to fall
back to — these are taken from the caller's own values verbatim, exactly like `resources` and
`image` already are, so a datastore that sets none of them renders with no placement constraints
and the Kubernetes default update strategy.

`component` narrows the `podAntiAffinity` shorthand to the datastore's own pods. Every bundled
datastore shares the release's `app.kubernetes.io/name`, so without it `hard` on one of them reads
"never share a node with any datastore of this release" — and a second TRAWL replica stays Pending
on a cluster with fewer nodes than bundled pods. The per-service workloads need no equivalent:
their `name` is already per-service.
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
      "podAntiAffinity" (.podAntiAffinity | default "")
      "affinity" (.affinity | default dict)
      "nodeSelector" (.nodeSelector | default dict)
      "tolerations" (.tolerations | default list)
      "topologySpreadConstraints" (.topologySpreadConstraints | default list)
      "priorityClassName" (.priorityClassName | default "")
      "podAntiAffinityLabels" (dict "app.kubernetes.io/component" .component)
      "strategy" (.strategy | default dict)
      "kubeVersionOverride" ($ctx.Values.kubeVersionOverride | default "")
-}}
{{- toYaml $values -}}
{{- end -}}

{{/*
The PodDisruptionBudget for a bundled datastore that opts into one.

The selector is the component-scoped one, for the same reason the workload's is: every bundled
datastore shares the release's `app.kubernetes.io/name`, and a budget selecting on that alone would
count Valkey and PostgreSQL pods as TRAWL replicas.

`maxUnavailable` wins when it is set, and `0` counts as set: it is a deliberate "never evict", not
an absent value. `tankovault.validateValues` refuses a budget that sets both, which the API server
would reject, and one on a single replica, which would block every node drain.

Args: ctx (root), component, pdb (the datastore's `podDisruptionBudget` block).
*/}}
{{- define "tankovault.datastore.pdb" -}}
{{- $pdb := .pdb -}}
apiVersion: {{ include "common.capabilities.pdb.apiVersion" .ctx }}
kind: PodDisruptionBudget
metadata:
  {{- include "tankovault.datastore.metadata" (dict "ctx" .ctx "component" .component) | nindent 2 }}
spec:
  {{- if not (kindIs "invalid" $pdb.maxUnavailable) }}
  maxUnavailable: {{ $pdb.maxUnavailable }}
  {{- else }}
  minAvailable: {{ kindIs "invalid" $pdb.minAvailable | ternary 1 $pdb.minAvailable }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "tankovault.datastore.selectorLabels" (dict "ctx" .ctx "component" .component) | nindent 6 }}
{{- end -}}
