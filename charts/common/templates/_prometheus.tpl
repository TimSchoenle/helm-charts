{{/*
Prometheus Operator objects: the recording and alerting rules, the presets an operator tunes them
with, and the CRD guard the scrape objects share with them.

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
  labels: {}                 # merged into metadata.labels, and templated — what a ruleSelector matches on

  # Presets. Every one of these is refused loudly when it names something the chart does not
  # ship; see "Why every preset is validated" below.
  disabledGroups: []         # rule group names to drop entirely (alerting groups only)
  disabledAlerts: []         # alert names to drop
  additionalRuleLabels: {}   # merged into every surviving alert's labels, appended ones included
  severityOverrides: {}      # alertName -> severity, the most specific label wins
  forOverrides: {}           # alertName -> Prometheus duration
  thresholds: {}             # alertName -> tunableName -> value, for declared tunables only
  additionalRuleGroups: []   # extra groups, appended verbatim

Arguments:
  ctx               (required) root context
  values            (required) the config above
  glob              file glob for the rule files, relative to the consuming chart root
                    (default "rules/*.yml")
  tunables          path to the tunable declarations (default "rules/tunables.yaml")
  scopePlaceholder  a literal label matcher the rule files write into every selector, to be
                    swapped for `scopeMatcher` below (optional; see the scoping note)
  scopeMatcher      what to swap it for, e.g. `namespace="prod"`. Empty leaves the files alone.

Scoping, and why it is a substitution rather than a rewrite
-----------------------------------------------------------
A `PrometheusRule` is not confined to the namespace it lives in. `up{job="api"} == 0` matches an
`api` job belonging to somebody else's release in another namespace just as happily as its own,
so two installs of the same chart in one cluster alert on each other. The Prometheus Operator's
own answer is `enforcedNamespaceLabel`, which parses every expression and injects a namespace
matcher — but that is configured on the *Prometheus* resource, not reachable from a chart, and a
chart that assumes it will silently be unscoped wherever it is not set.

What a chart can do is carry the matcher itself. Doing that by rewriting PromQL from a Go
template would mean parsing PromQL in a Go template, which is not a thing anyone should attempt.
So the direction is inverted: the *rule files* mark every selector with a placeholder that is a
genuine no-op — `foo_scope=~".*"` matches series that do not carry `foo_scope`, which is all of
them — and this partial swaps that exact literal for a real matcher. The rule files stay valid,
loadable PromQL before and after, the substitution is a string replace over a token an author put
there deliberately, and a chart that wants no scoping passes no matcher and gets its files
untouched.

The swap is applied to `expr` fields only, never to the parsed document as a whole: alert
annotations are prose an operator reads, and a placeholder-shaped substring in prose must not
turn into a label matcher.

Presets, and why thresholds are declared rather than discovered
---------------------------------------------------------------
Everything an operator can change here falls into one of two categories, and the split is not
cosmetic.

`for`, `severity` and the label set are *structured fields* of a rule. Overriding them is a map
lookup — no text is rewritten, nothing can be misparsed, and the result is a rule that differs
from the shipped one in exactly the field named. Those are offered for every alert the chart
ships, unconditionally.

A threshold is not a field. It is a substring of a PromQL expression, and the chart cannot find
it without parsing PromQL. Worse, it frequently is not the only thing that looks like it: an
expression of the shape

    :server_errors:ratio_rate5m{...} > 0.05 and :requests:rate5m{...} > 0.05

carries `0.05` twice, meaning two unrelated things, and no textual rule distinguishes them. And
some thresholds must not move at all — a `histogram_quantile` compared against a bucket boundary
reports an observed number, and against anything else an interpolated one; a comparison against
the top finite bucket has to be `>=`, because the function cannot return more than that boundary
and `>` would be permanently unsatisfiable.

So thresholds are opt-in per alert, declared by the chart author in `rules/tunables.yaml`:

  tunables:
    ExampleVolumeFillingUp:
      ratio:
        description: Fraction of the volume in use that counts as filling up.
        type: number            # number | integer
        default: 0.85           # rendered literally; must appear in `anchor` exactly once
        minimum: 0              # optional bounds, enforced at render time
        maximum: 1

An override may be a number or the string spelling of one — `--set` hands every float to the
chart as text — and nothing else. The value is pasted into PromQL, so a non-numeric one is
refused rather than escaped.
        anchor: ':volume_used:ratio{example_scope=~".*"} > 0.85'

`anchor` is the smallest substring of that alert's expression which contains the threshold and
occurs exactly once in it. Substitution replaces the default inside the anchor, then the anchor
inside the expression, so the ambiguous case above is resolved by the author choosing an anchor
wide enough to be unique — and a chart whose anchor is *not* unique, or whose declared default
does not appear in it, fails the render rather than silently installing an untuned rule.

The file sits at `rules/tunables.yaml`, deliberately outside the `rules/*.yml` glob: promtool
reads the rule files and would reject an unknown top-level key, and the CRD would prune one.
Declarations therefore live beside the rules without ever being shipped as part of them.

Thresholds are applied *before* the scope substitution, so an anchor is written against the
committed text — placeholder and all — rather than against whatever a particular install
rewrites it to.

Why every preset is validated
------------------------------
The failure this refuses to produce is silence. An operator who writes
`disabledAlerts: [FooBarDwon]` has typed a name that matches nothing; a lenient implementation
renders happily and leaves them believing an alert is off while it pages at 3am. The same goes
for an override keyed by an alert that was disabled two lines above, for a threshold on an alert
whose author never declared one, and for a `for` that is not a duration Prometheus can parse. All
of them are refused with the list of names that would have worked.

Recording rules are deliberately out of reach of every preset. Dashboards and other alerts
consume the series they produce, so dropping one — or attaching a label to one, which changes the
identity of the series it records — breaks consumers that nothing would report. `disabledGroups`
therefore refuses a group containing recording rules, `disabledAlerts` matches alerting rules
only, and `additionalRuleLabels` is applied only to alerts.

Label precedence, most specific last:

  the rule file's own labels  <  additionalRuleLabels  <  severityOverrides

`additionalRuleLabels` beating the rule file is the point of it — an operator adding
`tier: staging` to route a whole release away from the on-call rotation means it — and
`severityOverrides`, being per alert, beats both.

`additionalRuleGroups` are emitted as written apart from those routing labels, which reach them
too — an operator who put `tier: staging` on the install meant it of their own rules as well, and
the alternative routes exactly the rules they cared most about back into the production rotation.
Precedence flips for them: a label the operator wrote on their own rule wins, because that is
already the operator speaking rather than a default of the chart's.

Nothing here is passed through `tpl`, for the same reason the rule files are not: Prometheus
renders label and annotation values with its own templating, and a `$labels` reference in one of
them must reach Prometheus intact rather than being resolved to an empty string by Go on the way
past.
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
What the chart's rule files ship, as a YAML document the callers below parse back:

  alerts:  alertName -> the group it belongs to
  groups:  groupName -> "alerting", "recording", or both joined by `+`
  exprs:   alertName -> its expression exactly as committed

Derived from the files rather than configured, so a chart that grows a rule grows the preset
surface for it in the same commit, and an operator naming something that is not here is told so
with the real list.
*/}}
{{- define "common.prometheus.rules.index" -}}
{{- $ctx := .ctx -}}
{{- $alerts := dict -}}
{{- $groups := dict -}}
{{- $exprs := dict -}}
{{- range $path, $_ := $ctx.Files.Glob (.glob | default "rules/*.yml") -}}
{{- $parsed := $ctx.Files.Get $path | fromYaml -}}
{{- range $group := ($parsed.groups | default list) -}}
{{- $kinds := list -}}
{{- range $rule := ($group.rules | default list) -}}
{{- if hasKey $rule "alert" -}}
{{- $_ := set $alerts (toString $rule.alert) $group.name -}}
{{- $_ := set $exprs (toString $rule.alert) (toString ($rule.expr | default "")) -}}
{{- $kinds = append $kinds "alerting" -}}
{{- else if hasKey $rule "record" -}}
{{- $kinds = append $kinds "recording" -}}
{{- end -}}
{{- end -}}
{{- $_ := set $groups $group.name (join "+" (uniq $kinds)) -}}
{{- end -}}
{{- end -}}
{{- toYaml (dict "alerts" $alerts "groups" $groups "exprs" $exprs) -}}
{{- end -}}

{{/*
The chart's tunable declarations, as YAML; empty when the chart declares none.

Read from `rules/tunables.yaml`, which is outside the rule glob on purpose — see the header.
*/}}
{{- define "common.prometheus.rules.tunables" -}}
{{- $ctx := .ctx -}}
{{- $path := .tunables | default "rules/tunables.yaml" -}}
{{- $raw := $ctx.Files.Get $path -}}
{{- if $raw -}}
{{- $doc := $raw | fromYaml -}}
{{- if hasKey $doc "Error" -}}
{{- fail (printf "chart %q ships %s but it does not parse as YAML: %s" $ctx.Chart.Name $path (toString (get $doc "Error"))) -}}
{{- end -}}
{{- with ($doc.tunables | default dict) -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
A threshold override as the literal that will be substituted into PromQL, or empty when the value
is not a number.

Accepts the numeric kinds and the string spelling of one, because `--set threshold=0.25` hands
the chart a string: Helm's `--set` parser promotes integers and booleans and leaves every float as
text. The string form is also the exact spelling an operator wrote, which is what should reach the
expression.

Everything else is refused, and that is a guard rather than a nicety — the result is pasted into a
rule expression, so a value like `0 or vector(1)` would otherwise be arbitrary PromQL.
*/}}
{{- define "common.prometheus.rules.numericLiteral" -}}
{{- $value := .value -}}
{{- $literal := "" -}}
{{- if or (kindIs "int" $value) (kindIs "int64" $value) (kindIs "float64" $value) (kindIs "string" $value) -}}
{{- $literal = printf "%v" $value -}}
{{- end -}}
{{- if regexMatch "^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$" $literal -}}
{{- $literal -}}
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
{{- else -}}
{{- if and .scopePlaceholder .scopeMatcher -}}
{{- if eq (include "common.prometheus.rules.scopedExprCount" (dict "ctx" $ctx "glob" $glob "scopePlaceholder" .scopePlaceholder)) "0" -}}
{{- $messages = append $messages (printf "the rules are asked to be scoped to %s, but not one expression in %q contains the placeholder %q. The substitution would be a no-op and the rules would install unscoped — matching series from every other namespace in the cluster. Either write the placeholder into the rule selectors or turn the scoping off, but do not leave the two disagreeing." .scopeMatcher $glob .scopePlaceholder) -}}
{{- end -}}
{{- end -}}
{{- $presets := include "common.prometheus.rules.presetErrors" (dict "ctx" $ctx "values" $values "glob" $glob "tunables" .tunables) -}}
{{- if $presets -}}
{{- $messages = concat $messages (splitList "\n" $presets) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- join "\n" $messages -}}
{{- end -}}

{{/*
Everything wrong with the presets specifically, as newline-separated messages. Split out of
`common.prometheus.rules.errors` because it is the long half and the two have nothing to say to
each other; callers want the union, which is what `errors` returns.
*/}}
{{- define "common.prometheus.rules.presetErrors" -}}
{{- $ctx := .ctx -}}
{{- $values := .values -}}
{{- $glob := .glob -}}
{{- $messages := list -}}
{{- $index := fromYaml (include "common.prometheus.rules.index" (dict "ctx" $ctx "glob" $glob)) -}}
{{- $shippedAlerts := $index.alerts -}}
{{- $shippedGroups := $index.groups -}}
{{- $exprs := $index.exprs -}}
{{- $declared := fromYaml (include "common.prometheus.rules.tunables" (dict "ctx" $ctx "tunables" .tunables)) -}}
{{- $alertNames := join ", " (keys $shippedAlerts | sortAlpha) -}}
{{- $groupNames := join ", " (keys $shippedGroups | sortAlpha) -}}
{{- $disabledAlerts := $values.disabledAlerts | default list -}}
{{- $disabledGroups := $values.disabledGroups | default list -}}
{{- /*
Which alerts will not survive the filtering, so an override on one can be refused.
*/ -}}
{{- $off := dict -}}
{{- range $name := $disabledAlerts -}}
{{- $_ := set $off (toString $name) true -}}
{{- end -}}
{{- range $name, $group := $shippedAlerts -}}
{{- if has $group $disabledGroups -}}
{{- $_ := set $off $name true -}}
{{- end -}}
{{- end -}}
{{- /*
`disabledGroups`: the group must exist, and must not be one the recorded series come from.
*/ -}}
{{- range $name := $disabledGroups -}}
{{- if not (hasKey $shippedGroups (toString $name)) -}}
{{- $messages = append $messages (printf "`disabledGroups` names the rule group %q, which this chart does not ship, so it would silently disable nothing. The groups it does ship are: %s." $name $groupNames) -}}
{{- else if contains "recording" (toString (get $shippedGroups (toString $name))) -}}
{{- $messages = append $messages (printf "`disabledGroups` names %q, which contains recording rules. Dashboards and other alerts read the series those rules produce, so dropping the group would leave panels blank and expressions matching nothing, and neither would report an error. Disable the alerts that consume it with `disabledAlerts` instead." $name) -}}
{{- end -}}
{{- end -}}
{{- /*
`disabledAlerts`: the alert must exist. A typo here is an alert believed to be off.
*/ -}}
{{- range $name := $disabledAlerts -}}
{{- if not (hasKey $shippedAlerts (toString $name)) -}}
{{- $messages = append $messages (printf "`disabledAlerts` names %q, which this chart does not ship. Left as it is, the alert you meant stays armed while the values file says otherwise. The alerts this chart ships are: %s." $name $alertNames) -}}
{{- end -}}
{{- end -}}
{{- /*
Overrides must name a shipped alert, and one that is still there to be overridden.
*/ -}}
{{- range $key := (list "severityOverrides" "forOverrides" "thresholds") -}}
{{- range $name, $_ := ((get $values $key) | default dict) -}}
{{- if not (hasKey $shippedAlerts $name) -}}
{{- $messages = append $messages (printf "`%s` is keyed by %q, which is not an alert this chart ships. The alerts it ships are: %s." $key $name $alertNames) -}}
{{- else if hasKey $off $name -}}
{{- $messages = append $messages (printf "`%s` configures %q, but the same values disable that alert. One of the two is not what you meant: drop the override, or stop disabling the alert." $key $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /*
A `for` Prometheus cannot parse is rejected by the rule manager at load time, which takes the
whole group down with it rather than just this alert.
*/ -}}
{{- range $name, $duration := ($values.forOverrides | default dict) -}}
{{- if not (regexMatch "^(0|([0-9]+(ms|s|m|h|d|w|y))+)$" (toString $duration)) -}}
{{- $messages = append $messages (printf "`forOverrides` sets %q to %q, which is not a Prometheus duration. Prometheus refuses to load a rule group containing one, so this would disarm every rule in the group, not just this alert. Use a form like `30s`, `5m` or `1h30m`." $name (toString $duration)) -}}
{{- end -}}
{{- end -}}
{{- /*
Every declaration must be substitutable, whether or not anybody overrides it. A chart whose
anchor has drifted from its expression would otherwise render an untuned rule in silence.
*/ -}}
{{- range $alert, $decls := $declared -}}
{{- if not (hasKey $shippedAlerts $alert) -}}
{{- $messages = append $messages (printf "the chart declares tunables for %q, which is not an alert it ships. The declaration is dead, and an operator setting it would be refused for the wrong reason." $alert) -}}
{{- else -}}
{{- $expr := toString (get $exprs $alert) -}}
{{- range $tunable, $decl := $decls -}}
{{- $anchor := toString ($decl.anchor | default "") -}}
{{- $literal := printf "%v" $decl.default -}}
{{- if not $anchor -}}
{{- $messages = append $messages (printf "the tunable `%s.%s` declares no `anchor`, so there is nothing to substitute into." $alert $tunable) -}}
{{- else if ne (sub (len (splitList $anchor $expr)) 1) 1 -}}
{{- $messages = append $messages (printf "the tunable `%s.%s` anchors on %q, which occurs %d times in that alert's expression. An anchor has to occur exactly once — widen it until it does, or the substitution would rewrite the wrong comparison." $alert $tunable $anchor (sub (len (splitList $anchor $expr)) 1)) -}}
{{- else if ne (sub (len (splitList $literal $anchor)) 1) 1 -}}
{{- $messages = append $messages (printf "the tunable `%s.%s` declares the default %s, which occurs %d times in its anchor %q. It has to occur exactly once, spelled the way the expression spells it, or an override would edit the wrong number." $alert $tunable $literal (sub (len (splitList $literal $anchor)) 1) $anchor) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /*
An override must name a tunable its alert actually declares, and stay inside its bounds.
*/ -}}
{{- range $alert, $overrides := ($values.thresholds | default dict) -}}
{{- if hasKey $shippedAlerts $alert -}}
{{- $decls := (get $declared $alert) | default dict -}}
{{- range $tunable, $value := $overrides -}}
{{- if not (hasKey $decls $tunable) -}}
{{- if $decls -}}
{{- $messages = append $messages (printf "`thresholds.%s` sets %q, which that alert does not declare as tunable. It declares: %s." $alert $tunable (join ", " (keys $decls | sortAlpha))) -}}
{{- else -}}
{{- $messages = append $messages (printf "`thresholds.%s` sets %q, but no threshold of that alert is tunable. Its comparison is derived rather than chosen — a histogram bucket boundary, or a constant the application itself defines — and moving it would produce a rule that reads plausibly and reports an interpolated or unreachable number. Disable the alert with `disabledAlerts` and re-add your own through `additionalRuleGroups` if you need different arithmetic." $alert $tunable) -}}
{{- end -}}
{{- else -}}
{{- $decl := get $decls $tunable -}}
{{- $type := toString ($decl.type | default "number") -}}
{{- $literal := include "common.prometheus.rules.numericLiteral" (dict "value" $value) -}}
{{- if not $literal -}}
{{- $messages = append $messages (printf "`thresholds.%s.%s` is %v, which is not a number. The value is substituted into a rule expression, so anything else is either PromQL that will not parse or PromQL you did not mean to write." $alert $tunable $value) -}}
{{- else if and (eq $type "integer") (not (regexMatch "^[+-]?[0-9]+$" $literal)) -}}
{{- $messages = append $messages (printf "`thresholds.%s.%s` is %s, but that tunable is declared as an integer." $alert $tunable $literal) -}}
{{- else -}}
{{- if and (hasKey $decl "minimum") (lt (float64 $literal) (float64 $decl.minimum)) -}}
{{- $messages = append $messages (printf "`thresholds.%s.%s` is %s, below the declared minimum of %v." $alert $tunable $literal $decl.minimum) -}}
{{- end -}}
{{- if and (hasKey $decl "maximum") (gt (float64 $literal) (float64 $decl.maximum)) -}}
{{- $messages = append $messages (printf "`thresholds.%s.%s` is %s, above the declared maximum of %v." $alert $tunable $literal $decl.maximum) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /*
An appended group the operator got structurally wrong is rejected by the API server with a
message about `spec.groups[3]`, which is not where they wrote it.
*/ -}}
{{- range $i, $group := ($values.additionalRuleGroups | default list) -}}
{{- if not (kindIs "map" $group) -}}
{{- $messages = append $messages (printf "`additionalRuleGroups[%d]` is not a mapping. Each entry is a Prometheus rule group: a `name`, a list of `rules`, and optionally an `interval`." $i) -}}
{{- else -}}
{{- if not ($group.name | default "") -}}
{{- $messages = append $messages (printf "`additionalRuleGroups[%d]` has no `name`. Prometheus identifies a group by it and refuses a group without one." $i) -}}
{{- else if hasKey $shippedGroups (toString $group.name) -}}
{{- $messages = append $messages (printf "`additionalRuleGroups[%d]` is named %q, which is already the name of a group this chart ships. Prometheus refuses a PrometheusRule holding two groups of the same name; pick another." $i $group.name) -}}
{{- end -}}
{{- if not ($group.rules | default list) -}}
{{- $messages = append $messages (printf "`additionalRuleGroups[%d]` (%s) has no `rules`. An empty group is refused by Prometheus at load time." $i ($group.name | default "unnamed")) -}}
{{- end -}}
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
Every rule group across every matching file, as YAML, with the presets applied.

Read through `.Files.Get` and round-tripped with `fromYaml`/`toYaml` rather than `tpl`: alert
annotations carry Prometheus' own templating, which Go would try to evaluate — and either fail
on, or worse, quietly resolve to empty strings, leaving every alert with an annotation that names
nothing. Every preset below is therefore applied to the *parsed* document, never to its text.

Called without `values` this is the shipped rule set unchanged, which is what the emptiness check
in `common.prometheus.rules.errors` relies on.
*/}}
{{- define "common.prometheus.rules.groups" -}}
{{- $ctx := .ctx -}}
{{- $values := .values | default dict -}}
{{- $placeholder := .scopePlaceholder | default "" -}}
{{- $matcher := .scopeMatcher | default "" -}}
{{- $disabledGroups := $values.disabledGroups | default list -}}
{{- $disabledAlerts := $values.disabledAlerts | default list -}}
{{- $extraLabels := $values.additionalRuleLabels | default dict -}}
{{- $severities := $values.severityOverrides | default dict -}}
{{- $durations := $values.forOverrides | default dict -}}
{{- $thresholds := $values.thresholds | default dict -}}
{{- $declared := dict -}}
{{- if $thresholds -}}
{{- $declared = fromYaml (include "common.prometheus.rules.tunables" (dict "ctx" $ctx "tunables" .tunables)) -}}
{{- end -}}
{{- $groups := list -}}
{{- range $path, $_ := $ctx.Files.Glob (.glob | default "rules/*.yml") -}}
{{- $parsed := $ctx.Files.Get $path | fromYaml -}}
{{- $groups = concat $groups ($parsed.groups | default list) -}}
{{- end -}}
{{- $kept := list -}}
{{- range $group := $groups -}}
{{- if not (has $group.name $disabledGroups) -}}
{{- $rules := list -}}
{{- range $rule := ($group.rules | default list) -}}
{{- $alert := "" -}}
{{- if hasKey $rule "alert" -}}
{{- $alert = toString $rule.alert -}}
{{- end -}}
{{- if not (and $alert (has $alert $disabledAlerts)) -}}
{{- /*
1. Thresholds, against the committed text — before the scope swap rewrites part of it.
*/ -}}
{{- if and $alert (hasKey $declared $alert) (hasKey $thresholds $alert) -}}
{{- $decls := get $declared $alert -}}
{{- range $tunable, $value := (get $thresholds $alert) -}}
{{- if hasKey $decls $tunable -}}
{{- $decl := get $decls $tunable -}}
{{- $anchor := toString $decl.anchor -}}
{{- $literal := include "common.prometheus.rules.numericLiteral" (dict "value" $value) -}}
{{- if not $literal -}}
{{- fail (printf "chart %q was given %v as the threshold `%s.%s`, which is not a number. It would be substituted into a rule expression verbatim." $ctx.Chart.Name $value $alert $tunable) -}}
{{- end -}}
{{- $tuned := replace (printf "%v" $decl.default) $literal $anchor -}}
{{- $_ := set $rule "expr" (replace $anchor $tuned (toString $rule.expr)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /*
2. Scoping. Both halves are required before anything is touched: with a placeholder and no
matcher there is nothing to swap in, and removing the placeholder instead would be the wrong
repair — it is a valid, deliberately-true matcher, so leaving it produces exactly the unscoped
rules the caller asked for.
*/ -}}
{{- if and $placeholder $matcher (hasKey $rule "expr") -}}
{{- $_ := set $rule "expr" (replace $placeholder $matcher (toString $rule.expr)) -}}
{{- end -}}
{{- /*
3. Routing. Alerts only: a label on a recording rule changes the identity of the series it
records, and every dashboard panel and expression reading it would stop matching.
*/ -}}
{{- if $alert -}}
{{- if $extraLabels -}}
{{- $_ := set $rule "labels" (merge (deepCopy $extraLabels) ($rule.labels | default dict)) -}}
{{- end -}}
{{- if hasKey $severities $alert -}}
{{- $labels := $rule.labels | default dict -}}
{{- $_ := set $labels "severity" (toString (get $severities $alert)) -}}
{{- $_ := set $rule "labels" $labels -}}
{{- end -}}
{{- if hasKey $durations $alert -}}
{{- $_ := set $rule "for" (toString (get $durations $alert)) -}}
{{- end -}}
{{- end -}}
{{- $rules = append $rules $rule -}}
{{- end -}}
{{- end -}}
{{- if $rules -}}
{{- $_ := set $group "rules" $rules -}}
{{- $kept = append $kept $group -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /*
Appended groups. Emitted as written apart from the routing labels, which are a property of the
release rather than of the rules — an operator who put `tier: staging` on this install meant it of
their own rules too. `deepCopy` because `.Values` is shared across every template in the render
and this must not write back into it.
*/ -}}
{{- range $group := ($values.additionalRuleGroups | default list) -}}
{{- $appended := deepCopy $group -}}
{{- if $extraLabels -}}
{{- range $rule := ($appended.rules | default list) -}}
{{- if hasKey $rule "alert" -}}
{{- /* The rule's own labels win here, unlike in the shipped rules above: this is the operator's
       own writing, not a default of the chart's for them to override. */ -}}
{{- $_ := set $rule "labels" (merge ($rule.labels | default dict) (deepCopy $extraLabels)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $kept = append $kept $appended -}}
{{- end -}}
{{- if $kept -}}
{{- toYaml $kept -}}
{{- end -}}
{{- end -}}

{{/*
How many rule expressions carry the scope placeholder, as a string. Zero while scoping is asked
for means the rule files and the caller disagree about the token, which would otherwise install
as a set of quietly unscoped rules.
*/}}
{{- define "common.prometheus.rules.scopedExprCount" -}}
{{- $ctx := .ctx -}}
{{- $placeholder := .scopePlaceholder -}}
{{- $count := 0 -}}
{{- range $path, $_ := $ctx.Files.Glob (.glob | default "rules/*.yml") -}}
{{- $parsed := $ctx.Files.Get $path | fromYaml -}}
{{- range $group := ($parsed.groups | default list) -}}
{{- range $rule := ($group.rules | default list) -}}
{{- if and (hasKey $rule "expr") (contains $placeholder (toString $rule.expr)) -}}
{{- $count = add1 $count -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $count -}}
{{- end -}}

{{/*
The PrometheusRule itself: one object per release, holding every group that survived the presets.
*/}}
{{- define "common.prometheus.rules.prometheusRule" -}}
{{- $ctx := .ctx -}}
{{- $values := .values -}}
{{- $groups := include "common.prometheus.rules.groups" (dict
      "ctx" $ctx
      "values" $values
      "glob" (.glob | default "rules/*.yml")
      "tunables" .tunables
      "scopePlaceholder" .scopePlaceholder
      "scopeMatcher" .scopeMatcher) -}}
{{- if not $groups -}}
{{- fail (printf "chart %q would render a PrometheusRule with no groups. Either it ships no rule files, or the presets disabled every group in them; the API server rejects an empty `spec.groups` either way." $ctx.Chart.Name) -}}
{{- end -}}
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
