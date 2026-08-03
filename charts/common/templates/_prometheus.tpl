{{/*
Prometheus Operator objects: the recording and alerting rules, and the CRD guard the scrape
objects share with them.

Worth being explicit about how this differs from `common.grafana.dashboard.*`, because the two
look symmetrical and are not. A dashboard has two possible carriers — a sidecar ConfigMap or a
`GrafanaDashboard` — and only the second can reach across namespaces on its own terms. Alerting
rules have exactly one carrier, `PrometheusRule`, and it is already the operator's CRD: there is
no sidecar path to graduate from, and no per-object equivalent of `allowCrossNamespaceImport`.
Which namespaces a Prometheus picks rules up from is decided entirely on the Prometheus custom
resource, by `ruleNamespaceSelector` and `ruleSelector`, and a chart cannot influence that from
its own side. What it *can* do is carry the labels that a `ruleSelector` matches on, which is
what `values.labels` is for — a cluster whose Prometheus selects `release: kube-prometheus-stack`
needs that label here or the rules are created and never loaded.

So what this shares with the dashboard partials is not a second delivery mechanism, it is the
failure behaviour: a missing CRD stops the render instead of silently dropping the objects.

Value contract (the `values` argument):

  enabled: false
  labels: {}   # merged into metadata.labels, and templated — what a ruleSelector matches on

Arguments:
  ctx     (required) root context
  values  (required) the config above
  glob    file glob for the rule files, relative to the consuming chart root
          (default "rules/*.yml")
*/}}

{{/*
The API the Prometheus Operator's objects live under. Kept in one place so the guard, the render
and every error message agree.
*/}}
{{- define "common.prometheus.apiVersion" -}}
monitoring.coreos.com/v1
{{- end -}}

{{/*
A message when the Prometheus Operator CRDs are missing, empty when they are present. Returned
rather than raised so consuming charts can fold it into an aggregated report.

Arguments:
  ctx      (required) root context
  feature  what is being refused, named as the operator would recognise it
           (e.g. "metrics.serviceMonitor.enabled")

A capability guard that skipped the objects instead would render nothing in CI, where
`helm template` reports the built-in API surface but no CRDs, and would leave a real install
succeeding with no scrape target and no alerts — the failure this refuses to produce. Offline
renders opt in with `--api-versions monitoring.coreos.com/v1`.
*/}}
{{- define "common.prometheus.operatorErrors" -}}
{{- $api := include "common.prometheus.apiVersion" . -}}
{{- if not (include "common.capabilities.apiVersions.has" (dict "ctx" .ctx "api" $api)) -}}
{{- printf "%s is enabled, but the cluster registers no `%s` API. Install the Prometheus Operator CRDs first, or pass `--api-versions %s` if you are rendering offline with `helm template`. Rendering regardless would produce manifests the API server rejects at apply time." .feature $api $api -}}
{{- end -}}
{{- end -}}

{{/*
Every problem the chart can detect in the rules configuration, as newline-separated messages;
empty when it is sound.
*/}}
{{- define "common.prometheus.rules.errors" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $glob := .glob | default "rules/*.yml" -}}
{{- $messages := list -}}
{{- if $values.enabled -}}
{{- with (include "common.prometheus.operatorErrors" (dict "ctx" $ctx "feature" (.feature | default "the Prometheus rules"))) -}}
{{- $messages = append $messages . -}}
{{- end -}}
{{- if not (include "common.prometheus.rules.groups" (dict "ctx" $ctx "glob" $glob)) -}}
{{- $messages = append $messages (printf "the Prometheus rules are enabled, but no rule group could be read from %q. The files are either absent, empty, or not parseable as a Prometheus rule file with a top-level `groups:` key." $glob) -}}
{{- end -}}
{{- end -}}
{{- join "\n" $messages -}}
{{- end -}}

{{/*
Raise the errors above directly. For charts without an aggregated validator of their own.
*/}}
{{- define "common.prometheus.rules.validate" -}}
{{- $errors := include "common.prometheus.rules.errors" . -}}
{{- if $errors -}}
{{- fail (printf "\n\nPROMETHEUS RULES CONFIGURATION INVALID for chart %q:\n\n  - %s\n" .ctx.Chart.Name (join "\n  - " (splitList "\n" $errors))) -}}
{{- end -}}
{{- end -}}

{{/*
Every rule group across every matching file, as YAML.

Read through `.Files.Get` and round-tripped with `fromYaml`/`toYaml` rather than `tpl`: alert
annotations carry Prometheus' own `{{ $labels.job }}` and `{{ $value }}` templating, which Go
would try to evaluate — and either fail on, or worse, quietly resolve to empty strings, leaving
every alert with an annotation that names nothing.
*/}}
{{- define "common.prometheus.rules.groups" -}}
{{- $ctx := .ctx -}}
{{- $groups := list -}}
{{- range $path, $_ := $ctx.Files.Glob (.glob | default "rules/*.yml") -}}
{{- $parsed := $ctx.Files.Get $path | fromYaml -}}
{{- $groups = concat $groups ($parsed.groups | default list) -}}
{{- end -}}
{{- if $groups -}}
{{- toYaml $groups -}}
{{- end -}}
{{- end -}}

{{/*
The PrometheusRule itself: one object per release, holding every group from every rule file.
*/}}
{{- define "common.prometheus.rules.prometheusRule" -}}
{{- $ctx := .ctx -}}
{{- $values := .values -}}
{{- $groups := include "common.prometheus.rules.groups" (dict "ctx" $ctx "glob" (.glob | default "rules/*.yml")) -}}
apiVersion: {{ include "common.prometheus.apiVersion" . }}
kind: PrometheusRule
metadata:
  name: {{ include "common.fullname" $ctx }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
    {{- with $values.labels }}
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
    {{- end }}
  {{- with (include "common.annotations" $ctx) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  groups:
    {{- $groups | nindent 4 }}
{{- end -}}
