{{/*
Shared scaffolding for the four workloads this chart can bundle alongside the application:
PostgreSQL, Valkey, Gotenberg and Tika.

They are deliberately plain — one replica, at most one claim, no operator, no clustering — and
they exist so that `helm install` produces a working stack on a bare cluster. Everything about
them is evaluation tier, and `database.host`, `redis.host`, `tika.server.endpoint` and
`tika.gotenberg.endpoint` are the production path.

What they are *not* is exempt from the chart's security baseline. Each runs under the identity
its image was built for, with the same restricted preset, read-only root filesystem and dropped
capabilities as the application — which is what the scoped render context below is for.
*/}}

{{/*
Object metadata shared by a component's Service and workload.

Arguments:
  ctx        (required) root context
  component  (required) component name, used as the name suffix and the component label
*/}}
{{- define "paperless-ngx.component.metadata" -}}
name: {{ include "paperless-ngx.componentName" (dict "ctx" .ctx "component" .component) }}
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
Selector labels for a bundled component. `common.selectorLabels` alone is identical for every
workload in the release, so the component label is what actually distinguishes them — and it has
to appear in both the selector and the pod template or the API server rejects the workload.
*/}}
{{- define "paperless-ngx.component.selectorLabels" -}}
{{- include "common.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
A scoped render context for a bundled component, so `common.container`, `common.resources`,
`common.probes` and the two security-context helpers apply to it as they do to the application.

`nameOverride` is pinned to the root context's resolved name. Without it the scoped context has
no `nameOverride` at all, `common.name` falls back to the chart name, and under an operator's
`nameOverride` the pod labels and the selector would disagree — which the API server rejects
outright, breaking every bundled component rather than renaming it.

Arguments:
  ctx              (required) root context
  image            (required) the component's image dict
  runAsUser        (required) the UID its image was built for
  runAsGroup       (required) matching GID
  resourcesPreset  named sizing, when `resources` is empty
  resources        explicit resources, which win
  livenessProbe    probe value blocks, in the shape `common.probe` reads
  readinessProbe
*/}}
{{- define "paperless-ngx.component.values" -}}
{{- $ctx := .ctx -}}
{{- $values := dict
      "nameOverride" (include "common.name" $ctx)
      "image" (deepCopy .image)
      "imagePullSecrets" ($ctx.Values.imagePullSecrets | default list)
      "resourcesPreset" (.resourcesPreset | default "")
      "resources" (.resources | default dict)
      "podSecurityContextPreset" $ctx.Values.podSecurityContextPreset
      "podSecurityContext" (dict "runAsUser" (.runAsUser | int) "runAsGroup" (.runAsGroup | int) "fsGroup" (.runAsGroup | int))
      "securityContextPreset" $ctx.Values.securityContextPreset
      "securityContext" dict
      "automountServiceAccountToken" false
      "serviceAccount" (dict "create" $ctx.Values.serviceAccount.create "name" (include "common.serviceAccountName" $ctx))
      "nodeSelector" ($ctx.Values.nodeSelector | default dict)
      "tolerations" ($ctx.Values.tolerations | default list)
      "priorityClassName" ($ctx.Values.priorityClassName | default "")
      "commonLabels" ($ctx.Values.commonLabels | default dict)
      "startupProbe" (dict "enabled" false)
      "livenessProbe" (.livenessProbe | default (dict "enabled" false))
      "readinessProbe" (.readinessProbe | default (dict "enabled" false))
-}}
{{- toYaml $values -}}
{{- end -}}

{{/*
The scoped context itself, ready to hand to the library partials.

Arguments: as `paperless-ngx.component.values`.
*/}}
{{- define "paperless-ngx.component.context" -}}
{{- $ctx := .ctx -}}
{{- $values := include "paperless-ngx.component.values" . | fromYaml -}}
{{- toYaml (dict
      "Values" $values
      "Chart" $ctx.Chart
      "Release" $ctx.Release
      "Capabilities" $ctx.Capabilities
      "Template" $ctx.Template
      "Files" $ctx.Files) -}}
{{- end -}}

{{/*
A ClusterIP Service for one component. Never a headless one: nothing here is discovered
per-pod, and every consumer is the application resolving a single name.

Arguments:
  ctx        (required) root context
  component  (required) component name
  port       (required) service and container port
*/}}
{{- define "paperless-ngx.component.service" -}}
apiVersion: v1
kind: Service
metadata:
  {{- include "paperless-ngx.component.metadata" (dict "ctx" .ctx "component" .component) | nindent 2 }}
spec:
  type: ClusterIP
  ports:
    - name: {{ .component }}
      port: {{ int .port }}
      targetPort: {{ .component }}
      protocol: TCP
  selector:
    {{- include "paperless-ngx.component.selectorLabels" (dict "ctx" .ctx "component" .component) | nindent 4 }}
{{- end -}}
