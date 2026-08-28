{{/*
Grafana dashboard delivery, both mechanisms, in one place.

Grafana ships no Kubernetes-native dashboard type, so a chart that wants to hand Grafana a
dashboard has exactly two options, and only one of them can cross a namespace boundary on its
own terms:

  - The sidecar (`kiwigrid/k8s-sidecar`, as used by the grafana chart and kube-prometheus-stack)
    watches labelled ConfigMaps. Which namespaces it watches is configured on the *Grafana*
    release — `sidecar.dashboards.searchNamespace`, defaulting to Grafana's own namespace. A
    chart cannot influence that from here: it emits the ConfigMap and hopes.
  - grafana-operator v5's `GrafanaDashboard` carries the grant on the dashboard itself.
    `allowCrossNamespaceImport` lets a CR in the release's namespace bind to a Grafana CR in
    another, which is the only way a chart can make cross-namespace import its own decision.

The two are rendered together rather than as alternatives, so a cluster running both keeps
working, and the JSON is stored exactly once: the CRs point at the ConfigMap via `configMapRef`
instead of inlining it. That indirection also sidesteps a delimiter collision — Grafana's legend
syntax uses `{{ }}`, so dashboard JSON must arrive as file data and never pass through the
template engine, which `.Files.Get` into a ConfigMap satisfies and an inlined `spec.json` would
not.

Value contract (the `values` argument), matching what consuming charts expose:

  enabled: false                    # create the sidecar ConfigMap
  namespace: ""                     # where to put the objects; empty means the release's own
  label: grafana_dashboard          # label the sidecar selects on
  labelValue: "1"
  folder: ""                        # Grafana folder, for both delivery mechanisms
  grafanaOperator:
    enabled: false                  # additionally create one GrafanaDashboard per file
    allowCrossNamespaceImport: true
    instanceSelector:
      matchLabels:
        dashboards: grafana
    folder: ""                      # overrides `folder` for the operator path only
    resyncPeriod: 5m

Foldering, and why one key drives two mechanisms
------------------------------------------------
The two carriers file a dashboard in a folder by unrelated means: the sidecar reads a
`grafana_folder` *annotation* on the ConfigMap, the operator reads `spec.folder` on the custom
resource. An operator who wants their dashboards in a folder means it of whichever carrier is
running, so `folder` sets both. `grafanaOperator.folder` stays available for the case where the
two must differ, and wins for the operator path when set.

Namespace, and why it is worth an override
-------------------------------------------
The sidecar's search namespace is configured on the *Grafana* release and defaults to Grafana's
own. A chart cannot reach that, so the only move left on this side is to put the ConfigMap where
the sidecar is already looking. `namespace` does that, and the GrafanaDashboard resources follow
it, since `configMapRef` resolves within the custom resource's own namespace.

Arguments shared by every partial below:
  ctx     (required) root context
  values  (required) the dashboard config above
  glob    file glob for the dashboard JSON, relative to the consuming chart root
          (default "dashboards/*.json")
  suffix  name suffix for the ConfigMap (default "dashboards")
*/}}

{{/*
Name of the ConfigMap holding the dashboard JSON. Both the ConfigMap and the CRs that reference
it resolve the name through this, so the two cannot drift apart.
*/}}
{{- define "common.grafana.dashboard.configMapName" -}}
{{- include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" (.suffix | default "dashboards")) -}}
{{- end -}}

{{/*
Where the dashboard objects go. Resolved through one partial for the same reason the name is:
a `configMapRef` is resolved in the custom resource's own namespace, so a ConfigMap and a
GrafanaDashboard that disagree produce an import that silently never happens.
*/}}
{{- define "common.grafana.dashboard.namespace" -}}
{{- $values := .values | default dict -}}
{{- $values.namespace | default (include "common.namespace" .ctx) -}}
{{- end -}}

{{/*
The Grafana folder for one delivery mechanism, falling back to the shared `folder`.

Arguments:
  values     the dashboard config
  override   the mechanism's own folder key, which wins when set
*/}}
{{- define "common.grafana.dashboard.folder" -}}
{{- .override | default (.values.folder | default "") -}}
{{- end -}}

{{/*
Every problem the chart can detect before the API server does, as newline-separated messages;
empty when the configuration is sound. Returned rather than raised so a consuming chart can fold
them into its own aggregated validation and report everything at once.

The CRD check is a hard error rather than a silent skip. A capability guard that drops the
objects instead would render an empty manifest in CI (`helm template` reports the built-in API
surface but no CRDs) and, worse, leave an operator who forgot the operator with a successful
install and no dashboard. Offline renders opt in with
`--api-versions grafana.integreatly.org/v1beta1`.
*/}}
{{- define "common.grafana.dashboard.errors" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $operator := $values.grafanaOperator | default dict -}}
{{- $messages := list -}}
{{- if $operator.enabled -}}
{{- if not $values.enabled -}}
{{- $messages = append $messages "the Grafana Operator dashboards need the dashboard ConfigMap as well: the GrafanaDashboard resources reference it through `configMapRef` rather than inlining the JSON, so enabling the operator path while the ConfigMap is disabled would leave the operator with nothing to import. Enable the dashboard ConfigMap, or turn the operator path off." -}}
{{- end -}}
{{- if not (include "common.capabilities.apiVersions.has" (dict "ctx" $ctx "api" "grafana.integreatly.org/v1beta1")) -}}
{{- $messages = append $messages "the Grafana Operator dashboards are enabled, but the cluster registers no `grafana.integreatly.org/v1beta1` API. Install grafana-operator v5 (https://github.com/grafana/grafana-operator) first, or pass `--api-versions grafana.integreatly.org/v1beta1` if you are rendering offline with `helm template`. Rendering regardless would produce manifests the API server rejects at apply time." -}}
{{- end -}}
{{- $selector := $operator.instanceSelector | default dict -}}
{{- if and (not $selector.matchLabels) (not $selector.matchExpressions) -}}
{{- $messages = append $messages "the Grafana Operator `instanceSelector` selects nothing. Unlike a Kubernetes label selector, an empty `instanceSelector` matches no Grafana instance at all, so the dashboards would be created and then never imported by anything. Set `matchLabels` or `matchExpressions` to the labels on your Grafana custom resource." -}}
{{- end -}}
{{- $resync := $operator.resyncPeriod | default "" -}}
{{- if and $resync (not (regexMatch "^[0-9]+(\\.[0-9]+)?(ns|us|µs|ms|s|m|h)([0-9]+(\\.[0-9]+)?(ns|us|µs|ms|s|m|h))*$" $resync)) -}}
{{- $messages = append $messages (printf "the Grafana Operator `resyncPeriod` is %q, which is not a Go duration. Use a value the operator can parse, such as `5m` or `1h30m`." $resync) -}}
{{- end -}}
{{- end -}}
{{- join "\n" $messages -}}
{{- end -}}

{{/*
Raise the errors above directly. For charts without an aggregated validator of their own; charts
that have one should consume `common.grafana.dashboard.errors` instead.
*/}}
{{- define "common.grafana.dashboard.validate" -}}
{{- $errors := include "common.grafana.dashboard.errors" . -}}
{{- if $errors -}}
{{- fail (printf "\n\nGRAFANA DASHBOARD CONFIGURATION INVALID for chart %q:\n\n  - %s\n" .ctx.Chart.Name (join "\n  - " (splitList "\n" $errors))) -}}
{{- end -}}
{{- end -}}

{{/*
The sidecar ConfigMap: every matching dashboard file under one object, labelled for discovery.
*/}}
{{- define "common.grafana.dashboard.configMap" -}}
{{- $ctx := .ctx -}}
{{- $values := .values -}}
{{- $dashboards := $ctx.Files.Glob (.glob | default "dashboards/*.json") -}}
{{- if not $dashboards -}}
{{- fail (printf "chart %q enables Grafana dashboards but ships no files matching %q" $ctx.Chart.Name (.glob | default "dashboards/*.json")) -}}
{{- end -}}
{{- /* The sidecar files a dashboard by annotation; the operator by a field on its own CR. */ -}}
{{- $folder := include "common.grafana.dashboard.folder" (dict "values" $values) -}}
{{- $annotations := dict -}}
{{- if $folder -}}
{{- $annotations = dict "grafana_folder" $folder -}}
{{- end -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "common.grafana.dashboard.configMapName" . }}
  namespace: {{ include "common.grafana.dashboard.namespace" . }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
    {{ $values.label | default "grafana_dashboard" }}: {{ $values.labelValue | default "1" | quote }}
  {{- with (include "common.tplvalues.merge" (dict "values" (list $annotations $ctx.Values.commonAnnotations) "context" $ctx)) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
data:
  {{- range $path, $_ := $dashboards }}
  {{ base $path }}: |
    {{- $ctx.Files.Get $path | nindent 4 }}
  {{- end }}
{{- end -}}

{{/*
One GrafanaDashboard per dashboard file. Separate objects rather than one per chart, because a
`configMapRef` addresses a single key and the operator's folder and import status are per
dashboard.
*/}}
{{- define "common.grafana.dashboard.customResources" -}}
{{- $args := . -}}
{{- $ctx := .ctx -}}
{{- $operator := .values.grafanaOperator -}}
{{- $folder := include "common.grafana.dashboard.folder" (dict "values" .values "override" $operator.folder) -}}
{{- $configMap := include "common.grafana.dashboard.configMapName" . -}}
{{- $allowCrossNamespaceImport := true -}}
{{- if hasKey $operator "allowCrossNamespaceImport" -}}
{{- $allowCrossNamespaceImport = $operator.allowCrossNamespaceImport -}}
{{- end -}}
{{- range $path, $_ := $ctx.Files.Glob (.glob | default "dashboards/*.json") }}
{{- /*
Named after the dashboard file. Charts conventionally prefix those with their own name
(`tankovault-overview.json`), which the release name and chart name in front of the suffix
already supply, so it is dropped to keep `RELEASE-tankovault-tankovault-overview` from happening.
*/ -}}
{{- $slug := base $path | trimSuffix ".json" | trimPrefix (printf "%s-" $ctx.Chart.Name) }}
---
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: {{ include "common.fullname.suffixed" (dict "ctx" $ctx "suffix" $slug) }}
  namespace: {{ include "common.grafana.dashboard.namespace" $args }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- with (include "common.annotations" $ctx) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  allowCrossNamespaceImport: {{ $allowCrossNamespaceImport }}
  instanceSelector:
    {{- toYaml $operator.instanceSelector | nindent 4 }}
  {{- with $operator.resyncPeriod }}
  resyncPeriod: {{ . | quote }}
  {{- end }}
  {{- with $folder }}
  folder: {{ . | quote }}
  {{- end }}
  configMapRef:
    name: {{ $configMap }}
    key: {{ base $path }}
{{- end }}
{{- end -}}
