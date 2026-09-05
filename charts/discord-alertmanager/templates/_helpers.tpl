{{/*
The configuration this chart derives from its own first-class values, as the tree the image
reads.

Optional settings are wrapped in `with` rather than written empty. To the loader an empty value
is a *supplied* value, so writing one would configure the setting blank rather than leaving the
binary on its compiled default — which is what an operator who set nothing meant.
*/}}
{{- define "discord-alertmanager.derivedConfig" -}}
alertmanager:
  {{- with .Values.alertmanager.basicUsername }}
  basic_username: {{ . | quote }}
  {{- end }}
  {{- with .Values.alertmanager.caBundle }}
  ca_bundle: {{ . | quote }}
  {{- end }}
  {{- with .Values.alertmanager.connectTimeoutSecs }}
  connect_timeout_secs: {{ . }}
  {{- end }}
  {{- with .Values.alertmanager.endpoints }}
  endpoints:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  retry:
    {{- with .Values.alertmanager.retry.initialBackoffMs }}
    initial_backoff_ms: {{ . }}
    {{- end }}
    {{- with .Values.alertmanager.retry.maxBackoffSecs }}
    max_backoff_secs: {{ . }}
    {{- end }}
    {{- with .Values.alertmanager.retry.maxElapsedSecs }}
    max_elapsed_secs: {{ . }}
    {{- end }}
  {{- with .Values.alertmanager.timeoutSecs }}
  timeout_secs: {{ . }}
  {{- end }}
discord:
  capabilities:
    {{- with .Values.discord.capabilities.admin }}
    admin:
      {{- toYaml . | nindent 6 }}
    {{- end }}
    {{- with .Values.discord.capabilities.operate }}
    operate:
      {{- toYaml . | nindent 6 }}
    {{- end }}
    {{- with .Values.discord.capabilities.silence }}
    silence:
      {{- toYaml . | nindent 6 }}
    {{- end }}
    {{- with .Values.discord.capabilities.view }}
    view:
      {{- toYaml . | nindent 6 }}
    {{- end }}
  {{- with .Values.discord.captureReplyText }}
  capture_reply_text: {{ . }}
  {{- end }}
  {{- with .Values.discord.devGuildId }}
  dev_guild_id: {{ . }}
  {{- end }}
engine:
  {{- with .Values.engine.deadmanWindowSecs }}
  deadman_window_secs: {{ . }}
  {{- end }}
  {{- with .Values.engine.dispatchers }}
  dispatchers: {{ . }}
  {{- end }}
  {{- with .Values.engine.escalationIntervalSecs }}
  escalation_interval_secs: {{ . }}
  {{- end }}
  {{- with .Values.engine.outboxBatchSize }}
  outbox_batch_size: {{ . }}
  {{- end }}
  {{- with .Values.engine.outboxLeaseSecs }}
  outbox_lease_secs: {{ . }}
  {{- end }}
  persist_events: {{ .Values.engine.persistEvents }}
  {{- with .Values.engine.pruneIntervalSecs }}
  prune_interval_secs: {{ . }}
  {{- end }}
  {{- with .Values.engine.reconcileIntervalSecs }}
  reconcile_interval_secs: {{ . }}
  {{- end }}
  {{- with .Values.engine.regroupWindowSecs }}
  regroup_window_secs: {{ . }}
  {{- end }}
  retention:
    {{- with .Values.engine.retention.auditDays }}
    audit_days: {{ . }}
    {{- end }}
    {{- with .Values.engine.retention.eventsDays }}
    events_days: {{ . }}
    {{- end }}
    {{- with .Values.engine.retention.resolvedDays }}
    resolved_days: {{ . }}
    {{- end }}
  {{- with .Values.engine.silenceSyncIntervalSecs }}
  silence_sync_interval_secs: {{ . }}
  {{- end }}
  storm:
    {{- with .Values.engine.storm.forumThreshold }}
    forum_threshold: {{ . }}
    {{- end }}
    {{- with .Values.engine.storm.threshold }}
    threshold: {{ . }}
    {{- end }}
    {{- with .Values.engine.storm.windowSecs }}
    window_secs: {{ . }}
    {{- end }}
ingest:
  {{- with .Values.ingest.bind }}
  bind: {{ . | quote }}
  {{- end }}
  {{- with .Values.ingest.bodyLimitBytes }}
  body_limit_bytes: {{ . }}
  {{- end }}
  {{- with .Values.ingest.maxConcurrentRequests }}
  max_concurrent_requests: {{ . }}
  {{- end }}
  {{- with .Values.ingest.requestTimeoutSecs }}
  request_timeout_secs: {{ . }}
  {{- end }}
  {{- with .Values.ingest.shutdownDrainSecs }}
  shutdown_drain_secs: {{ . }}
  {{- end }}
  {{- with .Values.ingest.webhookPath }}
  webhook_path: {{ . | quote }}
  {{- end }}
links:
  {{- with .Values.links.allowedHosts }}
  allowed_hosts:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with .Values.links.buttons }}
  buttons:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with .Values.links.grafanaBase }}
  grafana_base: {{ . | quote }}
  {{- end }}
  {{- with .Values.links.prometheusBase }}
  prometheus_base: {{ . | quote }}
  {{- end }}
  {{- with .Values.links.windowLeadSecs }}
  window_lead_secs: {{ . }}
  {{- end }}
  {{- with .Values.links.windowTrailSecs }}
  window_trail_secs: {{ . }}
  {{- end }}
observability:
  {{- with .Values.observability.adminChannelId }}
  admin_channel_id: {{ . }}
  {{- end }}
  metrics_enabled: {{ .Values.observability.metricsEnabled }}
render:
  {{- with .Values.render.debounceSecs }}
  debounce_secs: {{ . }}
  {{- end }}
  {{- with .Values.render.descriptionBudget }}
  description_budget: {{ . }}
  {{- end }}
  {{- with .Values.render.keyLabels }}
  key_labels:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  show_fingerprint: {{ .Values.render.showFingerprint }}
  {{- with .Values.render.threadArchiveAfterMinutes }}
  thread_archive_after_minutes: {{ . }}
  {{- end }}
{{- with .Values.routes }}
routes:
  {{- toYaml . | nindent 2 }}
{{- end }}
storage:
  {{- with .Values.storage.backend }}
  backend: {{ . | quote }}
  {{- end }}
  postgres:
    {{- with .Values.storage.postgres.acquireTimeoutSecs }}
    acquire_timeout_secs: {{ . }}
    {{- end }}
    {{- with .Values.storage.postgres.maxConnections }}
    max_connections: {{ . }}
    {{- end }}
    migrate_on_start: {{ .Values.storage.postgres.migrateOnStart }}
  sqlite:
    {{- with .Values.storage.sqlite.acquireTimeoutSecs }}
    acquire_timeout_secs: {{ . }}
    {{- end }}
    {{- with .Values.storage.sqlite.maxConnections }}
    max_connections: {{ . }}
    {{- end }}
    migrate_on_start: {{ .Values.storage.sqlite.migrateOnStart }}
    {{- with .Values.storage.sqlite.path }}
    path: {{ . | quote }}
    {{- end }}
telemetry:
  {{- with .Values.telemetry.logFormat }}
  log_format: {{ . | quote }}
  {{- end }}
  {{- with .Values.telemetry.logLevel }}
  log_level: {{ . | quote }}
  {{- end }}
  sentry:
    {{- with .Values.telemetry.sentry.attachStacktrace }}
    attach_stacktrace: {{ . }}
    {{- end }}
    {{- with .Values.telemetry.sentry.breadcrumbLevel }}
    breadcrumb_level: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.debug }}
    debug: {{ . }}
    {{- end }}
    {{- with .Values.telemetry.sentry.environment }}
    environment: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.eventLevel }}
    event_level: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.maxBreadcrumbs }}
    max_breadcrumbs: {{ . }}
    {{- end }}
    {{- with .Values.telemetry.sentry.release }}
    release: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.sampleRate }}
    sample_rate: {{ . }}
    {{- end }}
    {{- with .Values.telemetry.sentry.sendDefaultPii }}
    send_default_pii: {{ . }}
    {{- end }}
    {{- with .Values.telemetry.sentry.serverName }}
    server_name: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.shutdownTimeoutSecs }}
    shutdown_timeout_secs: {{ . }}
    {{- end }}
    {{- with .Values.telemetry.sentry.spanLevel }}
    span_level: {{ . | quote }}
    {{- end }}
    {{- with .Values.telemetry.sentry.tracesSampleRate }}
    traces_sample_rate: {{ . }}
    {{- end }}
{{- end -}}

{{/*
The configuration that actually reaches the image: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "discord-alertmanager.effectiveConfig" -}}
{{- $derived := include "discord-alertmanager.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
Every Discord snowflake in the configuration, as the dotted paths `common.toml` writes them at.

A snowflake is a `u64` and the ones Discord issues are all above 2^53, which is the largest
integer Helm can carry out of a values file — it parses one through `encoding/json`, so every
number in it arrives as a `float64` and `guild_id: 123456789012345678` becomes
`123456789012345680` before any template runs. Nothing downstream can detect that, and the result
is a route that posts to whatever channel the rounded id names.

So these are written as quoted strings in `values.yaml`, which is the only spelling that survives,
and named here so the renderer writes them back out as the TOML integers the image reads. The
schema for `routes` refuses an unquoted one, and `common.toml` refuses a rounded one arriving
through the untyped `config` escape hatch.
*/}}
{{- define "discord-alertmanager.snowflakes" -}}
- routes.guild_id
- routes.target.id
- routes.mentions.roles
- routes.mentions.users
- routes.escalation.roles
- routes.escalation.users
{{- end -}}

{{/*
The complete configuration document: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "discord-alertmanager.configToml" -}}
{{- $config := include "discord-alertmanager.effectiveConfig" . | fromYaml -}}
{{- $snowflakes := include "discord-alertmanager.snowflakes" . | fromYamlArray -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config) "intKeys" $snowflakes) -}}
{{- end -}}

{{/*
The credentials this chart manages, each keyed by the file name the loader reads it from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.
*/}}
{{- define "discord-alertmanager.secretData" -}}
alertmanager__basic_password: {{ .Values.alertmanager.basicPassword | quote }}
alertmanager__bearer_token: {{ .Values.alertmanager.bearerToken | quote }}
discord__token: {{ .Values.discord.token | quote }}
ingest__webhook_token: {{ .Values.ingest.webhookToken | quote }}
storage__postgres__url: {{ .Values.storage.postgres.url | quote }}
telemetry__sentry__dsn: {{ .Values.telemetry.sentry.dsn | quote }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "discord-alertmanager.secretKeys" -}}
{{- $data := include "discord-alertmanager.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}


{{/*
The port the listener actually binds, taken from `ingest.bind` rather than from a second value.

`ingest.bind` is the image's own key and the only thing that decides where the process listens.
A separate `containerPort` value would be a second spelling of the same fact, and the failure it
produces is silent: the Service, the probes and the NetworkPolicy would all agree with each other
and disagree with the process, which answers on a port nothing is pointed at.

The port is the part after the last `:`, which is what makes this correct for a bracketed IPv6
address (`[::]:9099`) as well as for `0.0.0.0:9099`.
*/}}
{{- define "discord-alertmanager.ingestPort" -}}
{{- with (include "discord-alertmanager.ingestPortError" .) -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" $.Chart.Name .) -}}
{{- end -}}
{{- splitList ":" (.Values.ingest.bind | default "0.0.0.0:9099") | last -}}
{{- end -}}

{{/*
The complaint about `ingest.bind`, or nothing.

Returned rather than raised so `discord-alertmanager.validateValues` can fold it in with every
other message and an operator gets one report instead of the first fault in template order.
`discord-alertmanager.ingestPort` raises it directly as well, because it is reached from
`service.yaml` and `NOTES.txt`, neither of which runs the validator.
*/}}
{{- define "discord-alertmanager.ingestPortError" -}}
{{- $bind := .Values.ingest.bind | default "0.0.0.0:9099" -}}
{{- if not (regexMatch "^[0-9]+$" (splitList ":" $bind | last)) -}}
{{- printf "  - ingest.bind (%q) does not end in a port number. It is `host:port`, e.g. `0.0.0.0:9099` or `[::]:9099`, and the Service, the probes and the NetworkPolicy all take the container port from it." $bind -}}
{{- end -}}
{{- end -}}

{{/*
`ingest.webhook_path` with a leading slash, which is what a URL needs and what the image itself
normalises to. Written here so the AlertmanagerConfig cannot end up posting to a path the
listener does not serve because one side wrote `webhook` and the other `/webhook`.
*/}}
{{- define "discord-alertmanager.webhookPath" -}}
{{- $path := .Values.ingest.webhookPath | default "/webhook" -}}
{{- if hasPrefix "/" $path -}}{{- $path -}}{{- else -}}/{{ $path }}{{- end -}}
{{- end -}}

{{/*
The URL Alertmanager posts to.

`alertmanagerConfig.url` wins where an operator has set one — Alertmanager may be reaching this
release through an Ingress, or from outside the cluster entirely. Otherwise the in-cluster
Service address is derived from the objects this chart itself renders, so a changed
`service.port` or `ingest.webhookPath` moves the receiver with it instead of leaving it pointed
at a 404.

Fully qualified rather than short: the AlertmanagerConfig may legitimately live in the
Alertmanager's namespace rather than this release's, and a short name would resolve there.
*/}}
{{- define "discord-alertmanager.webhookUrl" -}}
{{- with .Values.alertmanagerConfig.url -}}
{{- . -}}
{{- else -}}
{{- printf "http://%s.%s.svc:%v%s"
      (include "common.fullname" .)
      (include "common.namespace" .)
      .Values.service.port
      (include "discord-alertmanager.webhookPath" .) -}}
{{- end -}}
{{- end -}}

{{/*
Whether this release keeps its state in a file this pod has to be given a volume for.
*/}}
{{- define "discord-alertmanager.usesSqlite" -}}
{{- if eq (.Values.storage.backend | default "sqlite") "sqlite" -}}true{{- end -}}
{{- end -}}

{{/*
The volume backing the SQLite database: the operator's claim, the chart's own, or an `emptyDir`.

Rendered only for a SQLite release. A PostgreSQL one keeps its state in the database and needs no
volume, so giving it one would mean an empty claim left behind by a backend switch, billed
monthly and read by nothing.
*/}}
{{- define "discord-alertmanager.dataVolume" -}}
name: data
{{- if not .Values.persistence.data.enabled }}
emptyDir: {}
{{- else }}
persistentVolumeClaim:
  claimName: {{ .Values.persistence.data.existingClaim | default (include "common.fullname" .) }}
{{- end }}
{{- end -}}

{{/*
The literal every rule file writes into its selectors, and what this release swaps it for.

See `common.prometheus.rules.*` for why the substitution runs this way round. The short of it is
that a `PrometheusRule` is not confined to the namespace it lives in, so `up{job="..."} == 0`
would match a second release of this chart elsewhere in the cluster and the two would alert on
each other. The rule files carry a matcher that is a genuine no-op — `discord_alertmanager_scope=~".*"` matches
series that do not carry `discord_alertmanager_scope`, which is all of them — and this replaces it.
*/}}
{{- define "discord-alertmanager.rules.scopePlaceholder" -}}
discord_alertmanager_scope=~".*"
{{- end -}}

{{- define "discord-alertmanager.rules.scopeMatcher" -}}
{{- if eq .Values.metrics.prometheusRule.scope "namespace" -}}
namespace={{ include "common.namespace" . | quote }}
{{- end -}}
{{- end -}}

{{/*
Refuse a render that could only produce a pod which crash-loops, a receiver that never delivers,
or an object nothing will load.

Every check here is about a failure that is otherwise *silent or late*: the render succeeds, the
release installs, and the fault shows up as a restarting pod at 3am or as an alert that simply
never arrives. A rejected `helm upgrade` is the cheapest place to find any of them.

The credential checks read `existingSecret` as the answer wherever they can, because the chart
cannot see inside one.
*/}}
{{- define "discord-alertmanager.validateValues" -}}
{{- $messages := list -}}

{{- with (include "discord-alertmanager.ingestPortError" .) -}}
{{- $messages = append $messages . -}}
{{- end -}}

{{- /*
  The two settings the process constructs at boot and refuses to start without.

  `SerenitySink::from_token` and `AlertmanagerClient::new` both run in `main` before the listener
  binds, and the second returns `Config("no Alertmanager endpoint is configured")` for an empty
  list. Neither is marked `required` in the contract — the types carry defaults, because a `Vec`
  has one and a token is a `SecretString` — so the contract cannot express this and the chart has
  to.
*/ -}}
{{- if and (not .Values.discord.token) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - discord.token is not set. The bot builds its gateway client before it binds the listener, so a release without a token is a CrashLoopBackOff rather than a degraded feature. Set `discord.token`, or put `discord__token` in the Secret named by `existingSecret`." -}}
{{- end -}}
{{- $config := include "discord-alertmanager.effectiveConfig" . | fromYaml -}}
{{- if not .Values.configExtraToml -}}
{{- if not ($config | dig "alertmanager" "endpoints" list) -}}
{{- $messages = append $messages "  - alertmanager.endpoints is empty. `AlertmanagerClient::new` refuses to build without at least one peer and the process exits during startup, so this is a CrashLoopBackOff and not a bot that merely cannot silence. Set `alertmanager.endpoints`, e.g. `[http://alertmanager-operated.monitoring.svc:9093]`." -}}
{{- end -}}
{{- end -}}

{{- /*
  SQLite, the volume it lives on, and the replica count that volume permits.
*/ -}}
{{- if (include "discord-alertmanager.usesSqlite" .) -}}
{{- /*
  Checked against `.Values.storage.sqlite.path` rather than against the effective tree, and the
  distinction is the same one `configExtraToml` gets everywhere else in this repository: `config`
  is the documented escape hatch, and an operator writing `config.storage.sqlite.path` has
  reached past the chart's own value on purpose — most plausibly because they mounted the volume
  themselves through `extraVolumes` and `extraVolumeMounts`, which this check cannot see. The
  chart steps out of the way there instead of refusing a layout it does not own.

  It is also what keeps the key probeable: `just contract-tests` proves the round trip by writing
  a synthetic value into `config` and looking for it in the rendered document, and a guard over
  the effective tree would reject every probe this key could be given.
*/ -}}
{{- $path := .Values.storage.sqlite.path -}}
{{- $dir := .Values.persistence.data.mountPath | trimSuffix "/" -}}
{{- if and $path (not (hasPrefix (printf "%s/" $dir) $path)) -}}
{{- $messages = append $messages (printf "  - storage.sqlite.path (%q) is not a file under persistence.data.mountPath (%q). The container runs with a read-only root filesystem, so the database can only be created inside the mounted volume; anywhere else the pod starts, fails to open the file and crash-loops." $path $dir) -}}
{{- end -}}
{{- if .Values.autoscaling.enabled -}}
{{- $messages = append $messages "  - autoscaling.enabled is set while storage.backend is `sqlite`. The database is one file on one ReadWriteOnce volume, which a second pod cannot attach on another node — and the outbox lease that makes several dispatchers safe is only cross-process on PostgreSQL. Set `storage.backend: postgres`, or leave the release at one replica." -}}
{{- end -}}
{{- if gt (int .Values.replicaCount) 1 -}}
{{- $messages = append $messages (printf "  - replicaCount is %d while storage.backend is `sqlite`. The database is one file on one ReadWriteOnce volume, which a second pod cannot attach on another node. Set `storage.backend: postgres`, or leave the release at one replica." (int .Values.replicaCount)) -}}
{{- end -}}
{{- else -}}
{{- if and (not .Values.storage.postgres.url) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - storage.backend is `postgres` but storage.postgres.url is not set. The pool is opened during startup, so this is a CrashLoopBackOff. Set `storage.postgres.url`, or put `storage__postgres__url` in the Secret named by `existingSecret`." -}}
{{- end -}}
{{- end -}}

{{- /*
  The observability objects, each against the switch that decides whether it has anything to read.
*/ -}}
{{- if and .Values.metrics.serviceMonitor.enabled (not .Values.observability.metricsEnabled) -}}
{{- $messages = append $messages "  - metrics.serviceMonitor.enabled is set while observability.metricsEnabled is false. `/metrics` is not mounted at all in that case and answers 404, so the ServiceMonitor would scrape nothing and report nothing. Set `observability.metricsEnabled: true`, or turn the ServiceMonitor off." -}}
{{- end -}}

{{- /*
  The AlertmanagerConfig, and the two ways it silently delivers nothing.

  The enforced namespace matcher is not checked here: whether it is enforced is a field on the
  Alertmanager custom resource, which no chart can read, so a check could only guess. It is
  documented on the value and repeated in NOTES.txt instead.
*/ -}}
{{- if .Values.alertmanagerConfig.enabled -}}
{{- $auth := .Values.alertmanagerConfig.auth -}}
{{- $sameNamespace := or (not .Values.alertmanagerConfig.namespace) (eq .Values.alertmanagerConfig.namespace (include "common.namespace" .)) -}}
{{- if $auth.enabled -}}
{{- if and (not $auth.secretName) (not $sameNamespace) -}}
{{- $messages = append $messages (printf "  - alertmanagerConfig.namespace (%q) is not this release's namespace and alertmanagerConfig.auth.secretName is empty. The Prometheus Operator resolves the credential in the AlertmanagerConfig's own namespace, so it would look for this chart's Secret somewhere it does not exist and every delivery would fail with 401. Name a Secret in that namespace, or turn `alertmanagerConfig.auth.enabled` off." .Values.alertmanagerConfig.namespace) -}}
{{- end -}}
{{- if and (not $auth.secretName) $sameNamespace (not .Values.ingest.webhookToken) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - alertmanagerConfig.auth.enabled is set but no webhook token is available. The receiver would send an `Authorization` header referencing a Secret key this chart never writes. Set `ingest.webhookToken`, name an `existingSecret` carrying `ingest__webhook_token`, or turn `alertmanagerConfig.auth.enabled` off." -}}
{{- end -}}
{{- end -}}
{{- if and (not $auth.enabled) .Values.ingest.webhookToken -}}
{{- $messages = append $messages "  - ingest.webhookToken is set while alertmanagerConfig.auth.enabled is false. The listener rejects an unauthenticated post with 401, so the receiver this chart renders would fail every delivery. Turn `alertmanagerConfig.auth.enabled` on, or clear the token." -}}
{{- end -}}
{{- end -}}

{{- /*
  The CRD guards. A capability check in a template can only *skip*, and skipping is the one
  behaviour that must not happen here: a release that quietly drops its ServiceMonitor,
  PrometheusRule or AlertmanagerConfig installs cleanly and is never scraped, never alerts, and
  never receives. Failing the render is what makes the fault visible.
*/ -}}
{{- if .Values.metrics.serviceMonitor.enabled -}}
{{- with (include "common.prometheus.operatorErrors" (dict "ctx" $ "feature" "metrics.serviceMonitor.enabled")) -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}
{{- if .Values.metrics.prometheusRule.enabled -}}
{{- with (include "common.prometheus.operatorErrors" (dict "ctx" $ "feature" "metrics.prometheusRule.enabled")) -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}
{{- /*
  The AlertmanagerConfig is the one Prometheus Operator object here that does *not* live under
  `monitoring.coreos.com/v1`, so `common.prometheus.operatorErrors` is the wrong check for it: a
  cluster can register v1 and still be running an operator too old to serve v1alpha1.
*/ -}}
{{- if .Values.alertmanagerConfig.enabled -}}
{{- $api := "monitoring.coreos.com/v1alpha1" -}}
{{- if not (include "common.capabilities.apiVersions.has" (dict "ctx" . "api" $api)) -}}
{{- $messages = append $messages (printf "  - alertmanagerConfig.enabled is set, but the cluster registers no `%s` API. The AlertmanagerConfig CRD ships with the Prometheus Operator; install it, turn `alertmanagerConfig.enabled` off, or pass `--api-versions %s` if you are rendering offline with `helm template`." $api $api) -}}
{{- end -}}
{{- end -}}

{{- /*
  The gateway checks are not folded in here. `common.gateway.routes` raises them itself from
  `httproute.yaml`, and reporting them twice would put the same sentence in front of an operator
  under two different headings.
*/ -}}

{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}
