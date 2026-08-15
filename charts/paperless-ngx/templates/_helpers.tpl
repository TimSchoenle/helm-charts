{{/*
Chart-specific helpers. Everything reusable lives in the `common` library chart; only names and
shapes that are genuinely specific to paperless-ngx belong here, namespaced by chart name.
*/}}

{{/*
Selector labels for the application's own pods.

`common.selectorLabels` is name-and-instance only, which every workload in this release carries
— the bundled Valkey, PostgreSQL, Gotenberg and Tika pods included. Selecting on it alone would
therefore put a database and a PDF renderer behind the HTTP Service, and hand the application's
network policy to all four. The component label is what separates them, so it belongs in the
selector rather than only in the metadata.

It is part of `Service.spec.selector` and `Deployment.spec.selector`, both immutable after
creation. Nothing version-dependent may ever be added here.
*/}}
{{- define "paperless-ngx.selectorLabels" -}}
{{ include "common.selectorLabels" . }}
app.kubernetes.io/component: server
{{- end -}}

{{/*
Name of one bundled component's objects, e.g. `RELEASE-paperless-ngx-valkey`.

Arguments:
  ctx        (required) root context
  component  (required) `postgresql`, `valkey`, `gotenberg` or `tika`
*/}}
{{- define "paperless-ngx.componentName" -}}
{{- include "common.fullname.suffixed" (dict "ctx" .ctx "suffix" .component) -}}
{{- end -}}

{{/*
Whether a bundled component is actually rendered.

Each has two conditions and they are not the same shape: the datastores are switched on
directly, while Tika and Gotenberg are switched on by the *feature* and then suppressed by an
external endpoint. Resolving that in one place is what keeps the workload, the Service, the
environment variable and the network policy from disagreeing about which of them exist.

Arguments:
  ctx        (required) root context
  component  (required) component name
*/}}
{{- define "paperless-ngx.componentEnabled" -}}
{{- $ctx := .ctx -}}
{{- $tika := $ctx.Values.tika -}}
{{- if eq .component "postgresql" -}}
{{- if $ctx.Values.postgresql.enabled -}}true{{- end -}}
{{- else if eq .component "valkey" -}}
{{- if $ctx.Values.valkey.enabled -}}true{{- end -}}
{{- else if eq .component "tika" -}}
{{- if and $tika.enabled $tika.server.enabled (not $tika.server.endpoint) -}}true{{- end -}}
{{- else if eq .component "gotenberg" -}}
{{- if and $tika.enabled $tika.gotenberg.enabled (not $tika.gotenberg.endpoint) -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The external URL the application is reached on.

Derived from whatever publishes it rather than restated: an `ingress.host` or the first
`gateway.hostnames` entry is already the answer, and a `PAPERLESS_URL` that disagrees with the
hostname a browser used is not a cosmetic mismatch — Django rejects the request as a CSRF
failure, at the login form, with the page itself loading perfectly. An explicit `paperless.url`
still wins, for a deployment fronted by something this chart cannot see.
*/}}
{{- define "paperless-ngx.url" -}}
{{- $ctx := . -}}
{{- if $ctx.Values.paperless.url -}}
{{- tpl $ctx.Values.paperless.url $ctx | trimSuffix "/" -}}
{{- else if and $ctx.Values.ingress.enabled $ctx.Values.ingress.host -}}
{{- printf "%s://%s" (ternary "https" "http" $ctx.Values.ingress.tls.enabled) (tpl $ctx.Values.ingress.host $ctx) -}}
{{- else if and $ctx.Values.gateway.enabled $ctx.Values.gateway.hostnames -}}
{{- /*
  A Gateway's listeners are the cluster operator's to declare, so the chart cannot read the
  scheme off them. HTTPS is assumed because that is what a Gateway in front of a document
  archive terminates; `paperless.url` is the override for the plaintext case.
*/ -}}
{{- printf "https://%s" (tpl (first $ctx.Values.gateway.hostnames) $ctx) -}}
{{- end -}}
{{- end -}}

{{/*
Whether the chart has to carry a credential of its own at all. `common.createSecret` only asks
whether an `existingSecret` was supplied; this also refuses to render an empty Secret for an
install whose every credential is absent.
*/}}
{{- define "paperless-ngx.createSecret" -}}
{{- if include "common.createSecret" . -}}
{{- if include "paperless-ngx.secretData" . | fromYaml -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The credentials this chart puts in its own Secret, as YAML. Empty values are omitted entirely
rather than written as empty strings: an empty password is a *supplied* password, and the
container would use it instead of falling back.
*/}}
{{- define "paperless-ngx.secretData" -}}
{{- $data := dict -}}
{{- with .Values.paperless.secretKey -}}
{{- $_ := set $data "secret-key" . -}}
{{- end -}}
{{- with .Values.paperless.admin.password -}}
{{- $_ := set $data "admin-password" . -}}
{{- end -}}
{{- if ne .Values.database.engine "sqlite" -}}
{{- with .Values.database.password -}}
{{- $_ := set $data "database-password" . -}}
{{- end -}}
{{- end -}}
{{- with .Values.redis.password -}}
{{- $_ := set $data "redis-password" . -}}
{{- end -}}
{{- if .Values.paperless.email.host -}}
{{- with .Values.paperless.email.password -}}
{{- $_ := set $data "email-password" . -}}
{{- end -}}
{{- end -}}
{{- with $data -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}

{{/*
Key inside the consumed Secret that holds one credential.

The chart's own Secret uses fixed key names; only an operator-supplied `existingSecret` gets to
name them, because that object already exists and the chart has to adapt to it.

Arguments:
  ctx       (required) root context
  key       (required) the fixed name this chart uses
  override  the `existingSecretKey` value for that credential
*/}}
{{- define "paperless-ngx.secretKeyName" -}}
{{- if .ctx.Values.existingSecret -}}
{{- .override | default .key -}}
{{- else -}}
{{- .key -}}
{{- end -}}
{{- end -}}

{{/*
Whether a given credential is available to the pod at all — from this chart's Secret or from an
operator-supplied one, whose contents the chart cannot see and therefore has to assume.

Arguments:
  ctx    (required) root context
  value  the plaintext value configured for it
*/}}
{{- define "paperless-ngx.hasCredential" -}}
{{- if or .ctx.Values.existingSecret .value -}}true{{- end -}}
{{- end -}}

{{/*
Host of the database server: the bundled PostgreSQL's Service, or the configured one.
*/}}
{{- define "paperless-ngx.databaseHost" -}}
{{- if include "paperless-ngx.componentEnabled" (dict "ctx" . "component" "postgresql") -}}
{{- include "paperless-ngx.componentName" (dict "ctx" . "component" "postgresql") -}}
{{- else -}}
{{- tpl (.Values.database.host | default "") . -}}
{{- end -}}
{{- end -}}

{{/*
Port of the database server. An explicit `database.port` wins; otherwise the engine's default,
which is what the application would fall back to anyway — stated here so the NetworkPolicy and
the environment cannot disagree about it.
*/}}
{{- define "paperless-ngx.databasePort" -}}
{{- if .Values.database.port -}}
{{- .Values.database.port | int -}}
{{- else if eq .Values.database.engine "mariadb" -}}
3306
{{- else -}}
5432
{{- end -}}
{{- end -}}

{{/*
Host of the broker: the bundled Valkey's Service, or the configured one. Empty when
`redis.url` supplies the whole address, which is the one case nothing here can decompose.
*/}}
{{- define "paperless-ngx.redisHost" -}}
{{- if include "paperless-ngx.componentEnabled" (dict "ctx" . "component" "valkey") -}}
{{- include "paperless-ngx.componentName" (dict "ctx" . "component" "valkey") -}}
{{- else -}}
{{- tpl (.Values.redis.host | default "") . -}}
{{- end -}}
{{- end -}}

{{/*
The broker URL, as the value of `PAPERLESS_REDIS`.

When a password is configured the URL embeds `$(REDIS_PASSWORD)`, which the kubelet expands from
the environment variable defined ahead of it — so the password reaches the process without ever
being written into the ConfigMap or shown by `kubectl describe pod`. This is also why
`PAPERLESS_REDIS` is an `env` entry rather than a ConfigMap key: `envFrom` values are not
expanded.
*/}}
{{- define "paperless-ngx.redisUrl" -}}
{{- $ctx := . -}}
{{- if $ctx.Values.redis.url -}}
{{- tpl $ctx.Values.redis.url $ctx -}}
{{- else -}}
{{- $scheme := ternary "rediss" "redis" $ctx.Values.redis.tls -}}
{{- $credentials := "" -}}
{{- if include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $ctx.Values.redis.password) -}}
{{- $credentials = printf "%s:$(REDIS_PASSWORD)@" $ctx.Values.redis.username -}}
{{- else if $ctx.Values.redis.username -}}
{{- $credentials = printf "%s@" $ctx.Values.redis.username -}}
{{- end -}}
{{- printf "%s://%s%s:%d/%d" $scheme $credentials (include "paperless-ngx.redisHost" $ctx) (int $ctx.Values.redis.port) (int $ctx.Values.redis.database) -}}
{{- end -}}
{{- end -}}

{{/*
Endpoint of the Tika server: the bundled one, or the external one configured for it.
*/}}
{{- define "paperless-ngx.tikaEndpoint" -}}
{{- if .Values.tika.server.endpoint -}}
{{- tpl .Values.tika.server.endpoint . -}}
{{- else -}}
{{- printf "http://%s:9998" (include "paperless-ngx.componentName" (dict "ctx" . "component" "tika")) -}}
{{- end -}}
{{- end -}}

{{/*
Endpoint of the Gotenberg server.
*/}}
{{- define "paperless-ngx.gotenbergEndpoint" -}}
{{- if .Values.tika.gotenberg.endpoint -}}
{{- tpl .Values.tika.gotenberg.endpoint . -}}
{{- else -}}
{{- printf "http://%s:3000" (include "paperless-ngx.componentName" (dict "ctx" . "component" "gotenberg")) -}}
{{- end -}}
{{- end -}}

{{/*
Container ports for the application.
*/}}
{{- define "paperless-ngx.containerPorts" -}}
- name: http
  containerPort: 8000
  protocol: TCP
{{- end -}}

{{/*
Environment for the application container that cannot live in the ConfigMap: the credentials,
which are pulled from the Secret by reference, and the broker URL, which interpolates one of
them.

Order matters. The kubelet expands `$(REDIS_PASSWORD)` only against variables defined *earlier*
in the list; a later definition is left as the literal text `$(REDIS_PASSWORD)`, and the symptom
is a broker connection that fails to authenticate with a password nobody can find in the pod
spec.
*/}}
{{- define "paperless-ngx.env" -}}
{{- $ctx := . -}}
{{- $secret := include "common.secretName" $ctx -}}
{{- if include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $ctx.Values.redis.password) }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "redis-password" "override" $ctx.Values.redis.existingSecretKey) }}
{{- end }}
- name: PAPERLESS_REDIS
  value: {{ include "paperless-ngx.redisUrl" $ctx | quote }}
- name: PAPERLESS_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "secret-key" "override" $ctx.Values.paperless.existingSecretKey) }}
{{- if and $ctx.Values.paperless.admin.user (include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $ctx.Values.paperless.admin.password)) }}
- name: PAPERLESS_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "admin-password" "override" $ctx.Values.paperless.admin.existingSecretKey) }}
{{- end }}
{{- if ne $ctx.Values.database.engine "sqlite" }}
- name: PAPERLESS_DBPASS
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "database-password" "override" $ctx.Values.database.existingSecretKey) }}
{{- end }}
{{- if and $ctx.Values.paperless.email.host (include "paperless-ngx.hasCredential" (dict "ctx" $ctx "value" $ctx.Values.paperless.email.password)) }}
- name: PAPERLESS_EMAIL_HOST_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ include "paperless-ngx.secretKeyName" (dict "ctx" $ctx "key" "email-password" "override" $ctx.Values.paperless.email.existingSecretKey) }}
{{- end }}
{{- end -}}

{{/*
The claim backing one directory: an operator's own, the one this chart creates, or an emptyDir
when persistence is off.

Arguments:
  ctx     (required) root context
  volume  (required) `media`, `data`, `consume` or `export`
*/}}
{{- define "paperless-ngx.dataVolume" -}}
{{- $ctx := .ctx -}}
{{- $values := index $ctx.Values.persistence .volume -}}
- name: {{ .volume }}
{{- if not $values.enabled }}
  emptyDir: {}
{{- else }}
  persistentVolumeClaim:
    claimName: {{ $values.existingClaim | default (include "paperless-ngx.componentName" (dict "ctx" $ctx "component" .volume)) }}
{{- end }}
{{- end -}}

{{/*
All pod volumes this chart contributes.

Two of them are not application data:

  tmp    `PAPERLESS_SCRATCH_DIR`, where every intermediate file OCR produces is written:
         rasterised pages, unpaper output, the PDF being assembled. It is an `emptyDir` rather
         than the container's own writable layer so that `sizeLimit` can cap it — this is
         charged against the node's ephemeral storage either way, but only a volume can be
         bounded, and an OCR run on a large document is what exhausts a node.
  export `document_exporter`'s output directory. It is one of the image's declared volumes, so
         it exists in the layer and is read-only without a mount of its own — and the container
         reports that as a warning at start and as a failure only when somebody runs an export.

Note that /run is deliberately *not* mounted; see `paperless-ngx.validateValues` for why.
*/}}
{{- define "paperless-ngx.volumes" -}}
{{- $ctx := . -}}
{{- range $volume := list "media" "data" "consume" "export" }}
{{ include "paperless-ngx.dataVolume" (dict "ctx" $ctx "volume" $volume) }}
{{- end }}
- name: tmp
  emptyDir:
    {{- with $ctx.Values.persistence.scratchSizeLimit }}
    sizeLimit: {{ . }}
    {{- end }}
{{- end -}}

{{/*
Volume mounts for the application container. `subPath` is deliberately not used anywhere: the
paths below are whole directories the application owns, and a subPath mount would also silently
stop receiving updates to the volume it came from.
*/}}
{{- define "paperless-ngx.volumeMounts" -}}
- name: media
  mountPath: /usr/src/paperless/media
- name: data
  mountPath: /usr/src/paperless/data
- name: consume
  mountPath: /usr/src/paperless/consume
- name: export
  mountPath: /usr/src/paperless/export
- name: tmp
  mountPath: /tmp
{{- end -}}

{{/*
Fail the render with every configuration mistake at once, rather than one crash-looping pod per
attempt.

These are the constraints no JSON schema can express, because they are relationships between
values rather than properties of one.
*/}}
{{- define "paperless-ngx.validateValues" -}}
{{- $messages := list -}}
{{- $values := .Values -}}

{{- /*
  The image boots under s6-overlay, which refuses to start unless /run is writable and either
  belongs to the UID it runs as or is world-writable (mode 1777, as it is in the image itself).
  Neither is reachable from here: an `emptyDir` is always created owned by uid 0, `fsGroup`
  moves only its group and caps the mode at 2775, `emptyDir` has no `defaultMode` to raise it,
  and the capabilities that would let the container fix it by hand are the ones the restricted
  preset drops. Mounting anything at /run therefore replaces a directory s6-overlay accepts with
  one it rejects, and a read-only root filesystem leaves it nothing else to write to.

  So the root filesystem stays writable for this container, and /run is left as the image ships
  it. Every other part of the restricted baseline is unaffected: the container still runs as a
  non-root user, drops all capabilities, forbids privilege escalation and keeps the default
  seccomp profile. Caught here because the alternative is a crash loop whose only symptom is a
  message from s6-overlay's preinit about permissions on a directory nobody configured.
*/ -}}
{{- if (include "common.readOnlyRootFilesystem" .) -}}
{{- $messages = append $messages "  - securityContext.readOnlyRootFilesystem must stay false for this chart. The image's s6-overlay init requires a writable /run that it owns or that is world-writable, and no Kubernetes volume can be either, so the container would fail to boot. The rest of the restricted baseline (runAsNonRoot, dropped capabilities, no privilege escalation, seccomp) is unaffected and still applies." -}}
{{- end -}}

{{- /* The one credential with no safe default. */ -}}
{{- if not (include "paperless-ngx.hasCredential" (dict "ctx" . "value" $values.paperless.secretKey)) -}}
{{- $messages = append $messages "  - paperless.secretKey is required (or point existingSecret at a Secret holding it). It signs session cookies and share links, and the upstream fallback is a published constant — an instance running on it will hand a forged session to anybody who knows the default. Generate one with `openssl rand -base64 48`." -}}
{{- end -}}

{{- /* Database. */ -}}
{{- if ne $values.database.engine "sqlite" -}}
{{- if not (include "paperless-ngx.databaseHost" .) -}}
{{- $messages = append $messages (printf "  - database.host is required for the %s engine, unless postgresql.enabled runs one in this release." $values.database.engine) -}}
{{- end -}}
{{- if not (include "paperless-ngx.hasCredential" (dict "ctx" . "value" $values.database.password)) -}}
{{- $messages = append $messages (printf "  - database.password (or existingSecret) is required for the %s engine. The application otherwise falls back to the literal password `paperless`, which the bundled PostgreSQL would then be created with." $values.database.engine) -}}
{{- end -}}
{{- end -}}
{{- if $values.postgresql.enabled -}}
{{- if ne $values.database.engine "postgresql" -}}
{{- $messages = append $messages (printf "  - postgresql.enabled runs a PostgreSQL server, but database.engine is %q, so nothing would ever connect to it. Set database.engine=postgresql, or turn the bundled server off." $values.database.engine) -}}
{{- end -}}
{{- if $values.database.host -}}
{{- $messages = append $messages "  - postgresql.enabled and database.host are both set. The bundled server wins and the configured host is ignored, which is exactly the kind of disagreement that is only noticed when the wrong database turns out to be empty. Remove one of them." -}}
{{- end -}}
{{- end -}}
{{- if and (eq $values.database.engine "sqlite") (not $values.persistence.data.enabled) -}}
{{- $messages = append $messages "  - database.engine=sqlite keeps the database in the data volume, but persistence.data.enabled is false, so it would live on an emptyDir and every restart would return an empty archive with the documents still on the media volume. Enable persistence.data, or use an external database." -}}
{{- end -}}

{{- /* Broker. */ -}}
{{- if not (or $values.valkey.enabled $values.redis.url (include "paperless-ngx.redisHost" .)) -}}
{{- $messages = append $messages "  - no message broker is configured. paperless-ngx hands every document to a task worker through Redis, so without one it starts, serves the web UI, and consumes nothing at all — a failure with no error message. Enable valkey.enabled, or set redis.host / redis.url." -}}
{{- end -}}
{{- if and $values.valkey.enabled (or $values.redis.url $values.redis.host) -}}
{{- $messages = append $messages "  - valkey.enabled runs a broker in this release while redis.host / redis.url names another one. The bundled broker wins and the external address is ignored. Remove one of them." -}}
{{- end -}}

{{- /* Superuser. */ -}}
{{- if and $values.paperless.admin.user (not (include "paperless-ngx.hasCredential" (dict "ctx" . "value" $values.paperless.admin.password))) -}}
{{- $messages = append $messages "  - paperless.admin.user is set but no password is available. The account would be created without one and could never log in." -}}
{{- end -}}
{{- if and $values.paperless.admin.password (not $values.paperless.admin.user) -}}
{{- $messages = append $messages "  - paperless.admin.password is set but paperless.admin.user is empty, so no account is created and the password does nothing." -}}
{{- end -}}

{{- /* Office document support. */ -}}
{{- if $values.tika.enabled -}}
{{- if not (or $values.tika.server.enabled $values.tika.server.endpoint) -}}
{{- $messages = append $messages "  - tika.enabled needs a Tika server: either tika.server.enabled to run one, or tika.server.endpoint to name one. With the parser registered and no server behind it, every office document fails to consume." -}}
{{- end -}}
{{- if not (or $values.tika.gotenberg.enabled $values.tika.gotenberg.endpoint) -}}
{{- $messages = append $messages "  - tika.enabled needs a Gotenberg server: either tika.gotenberg.enabled to run one, or tika.gotenberg.endpoint to name one. Tika extracts the text and Gotenberg renders the archive PDF; the parser needs both." -}}
{{- end -}}
{{- end -}}

{{- /* Publishing. */ -}}
{{- if and $values.ingress.enabled (not $values.ingress.host) -}}
{{- $messages = append $messages "  - ingress.host is required when ingress.enabled is true." -}}
{{- end -}}
{{- if and (not (include "paperless-ngx.url" .)) (or $values.ingress.enabled $values.gateway.enabled) -}}
{{- $messages = append $messages "  - the application is published but no external URL could be derived, so Django would reject every form submission from it as a CSRF failure. Set paperless.url." -}}
{{- end -}}
{{- if and $values.paperless.rootPath (not (hasPrefix "/" $values.paperless.rootPath)) -}}
{{- $messages = append $messages (printf "  - paperless.rootPath must start with a slash (got %q)." $values.paperless.rootPath) -}}
{{- end -}}
{{- with (include "common.gateway.errors" (dict "ctx" . "values" $values.gateway)) -}}
{{- range splitList "\n" . -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}

{{- /* Mail. */ -}}
{{- if and $values.paperless.email.useTls $values.paperless.email.useSsl -}}
{{- $messages = append $messages "  - paperless.email.useTls and paperless.email.useSsl are mutually exclusive: the first upgrades a plaintext connection with STARTTLS, the second opens an encrypted one. Django refuses to start with both." -}}
{{- end -}}

{{- /* Storage. */ -}}
{{- range $volume := list "media" "data" "consume" "export" -}}
{{- $persistence := index $values.persistence $volume -}}
{{- if and (not $persistence.enabled) $persistence.existingClaim -}}
{{- $messages = append $messages (printf "  - persistence.%s.existingClaim is set but persistence.%s.enabled is false, so the claim would be ignored and the directory would run on an emptyDir." $volume $volume) -}}
{{- end -}}
{{- end -}}

{{- /* Observability. */ -}}
{{- with (include "common.prometheus.rules.errors" (dict
      "ctx" .
      "values" $values.metrics.prometheusRule
      "feature" "metrics.prometheusRule.enabled"
      "scopePlaceholder" (include "paperless-ngx.rules.scopePlaceholder" .)
      "scopeMatcher" (include "paperless-ngx.rules.scopeMatcher" .))) -}}
{{- range splitList "\n" . -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}
{{- with (include "common.grafana.dashboard.errors" (dict "ctx" . "values" $values.metrics.dashboard)) -}}
{{- range splitList "\n" . -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}

{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}

{{/*
The scope placeholder every selector in `rules/*.yml` carries, and what it is replaced with.

A PrometheusRule is not confined to the namespace it lives in, so an unscoped
`kube_deployment_status_replicas_available{...}` matches every other paperless-ngx in the
cluster and two installs alert on each other's outages. The rule files therefore carry
`paperless_ngx_scope=~".*"` — an always-true matcher on a label nothing emits — which
`common.prometheus.rules.*` swaps for a real one. See the library's `_prometheus.tpl` for why
the substitution runs in that direction rather than rewriting PromQL from a Go template.

The matcher is empty for `scope: none`, which leaves the placeholder in place. It is already a
no-op, so unscoped rules are the committed files unchanged.
*/}}
{{- define "paperless-ngx.rules.scopePlaceholder" -}}
paperless_ngx_scope=~".*"
{{- end -}}

{{- define "paperless-ngx.rules.scopeMatcher" -}}
{{- if eq .Values.metrics.prometheusRule.scope "namespace" -}}
{{- printf "namespace=%q" (include "common.namespace" .) -}}
{{- end -}}
{{- end -}}
