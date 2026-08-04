{{/*
Endpoints of the datastores, resolved once so every consumer agrees.

Each returns an empty string when the dependency is not configured, which is a supported
state for Redis and NATS — the rate limiter falls back to per-replica counters and the API's
live stream degrades, rather than the service refusing to boot.
*/}}
{{- define "tankovault.redisUrl" -}}
{{- if .Values.valkey.enabled -}}
{{- printf "redis://%s:6379" (include "common.fullname.suffixed" (dict "ctx" . "suffix" "valkey")) -}}
{{- else if not .Values.externalRedis.existingSecret -}}
{{- .Values.externalRedis.url -}}
{{- end -}}
{{- end -}}

{{- define "tankovault.natsUrl" -}}
{{- if .Values.nats.enabled -}}
{{- printf "nats://%s:4222" (include "common.fullname.suffixed" (dict "ctx" . "suffix" "nats")) -}}
{{- else -}}
{{- .Values.externalNats.url -}}
{{- end -}}
{{- end -}}

{{/*
Where the bundled NATS exporter reads from: NATS' monitoring listener, not its client port.

A separate value from `natsUrl` because they are different protocols on different ports — the
services speak the NATS protocol on 4222, the exporter scrapes HTTP on 8222 — and an operator
running an external NATS commonly publishes only one of the two. Empty when neither the bundled
NATS nor an explicit URL is available, which `tankovault.validateValues` refuses rather than
letting an exporter start and fail to connect forever.
*/}}
{{- define "tankovault.natsMonitoringUrl" -}}
{{- if .Values.metrics.natsExporter.url -}}
{{- .Values.metrics.natsExporter.url -}}
{{- else if .Values.nats.enabled -}}
{{- printf "http://%s:8222" (include "common.fullname.suffixed" (dict "ctx" . "suffix" "nats")) -}}
{{- end -}}
{{- end -}}

{{/*
The scope placeholder the rule files carry, and what it is swapped for.

A `PrometheusRule` is not confined to its own namespace: `up{job="api"} == 0` matches an `api`
job in every namespace Prometheus scrapes, so a second release — of this chart or anyone else's —
makes both alert on each other's outages. Every selector in `rules/*.yml` therefore carries
`tankovault_scope=~".*"`, an always-true matcher on a label nothing emits, which
`common.prometheus.rules.*` replaces with a real one. See the note in the library's
`_prometheus.tpl` for why the substitution runs in that direction.

The matcher is empty for `scope: none`, which leaves the placeholder in place — it is already a
no-op, so unscoped rules are the vendored files unchanged.
*/}}
{{- define "tankovault.rules.scopePlaceholder" -}}
tankovault_scope=~".*"
{{- end -}}

{{- define "tankovault.rules.scopeMatcher" -}}
{{- if eq .Values.metrics.prometheusRule.scope "namespace" -}}
{{- printf "namespace=%q" (include "common.namespace" .) -}}
{{- end -}}
{{- end -}}

{{- define "tankovault.trawlUrl" -}}
{{- if .Values.trawl.enabled -}}
{{- printf "http://%s:8191" (include "common.fullname.suffixed" (dict "ctx" . "suffix" "trawl")) -}}
{{- else -}}
{{- .Values.externalTrawl.url -}}
{{- end -}}
{{- end -}}

{{/*
Where the bundled TRAWL keeps its per-domain solved-cookie jar.

Defaults to the Redis the services already use, because replaying a solved session instead of
re-solving it is the difference between a sub-second fetch and a cold browser launch. Sharing
one instance is safe by construction — TankoVault namespaces every key under `tankovault:`,
TRAWL writes `session:<domain>` — and it is the shape upstream's compose stack ships.

Empty is a supported state: TRAWL then solves from a cold browser every time. It is also the
only possible one when `externalRedis.existingSecret` carries the URL, since the chart never
reads a Secret's contents.
*/}}
{{- define "tankovault.trawlRedisUrl" -}}
{{- if .Values.trawl.redis.enabled -}}
{{- default (include "tankovault.redisUrl" .) .Values.trawl.redis.url -}}
{{- end -}}
{{- end -}}

{{/*
Configuration this chart derives rather than asking the operator to restate.

Returned as a YAML map, merged under the operator's own `config` before being rendered to
TOML. Everything here is either a fact about the topology the chart just built (where each
service listens, where its peers are) or a value that has to agree with the browser-visible
origin — `anilist.redirect_uri`, `email.base_url` and `auth.webauthn_origin` are all bound to
that origin by AniList, by mail clients and by the browser's passkey implementation
respectively, and getting any of them wrong fails only at runtime.
*/}}
{{- define "tankovault.derivedConfig" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
{{- $spec := include "tankovault.spec" $service | fromYaml -}}
{{- $external := include "tankovault.externalUrl" $ctx -}}
{{- $redis := include "tankovault.redisUrl" $ctx -}}
{{- $nats := include "tankovault.natsUrl" $ctx -}}
bind_addr: {{ printf "0.0.0.0:%v" $spec.port | quote }}
telemetry:
  service_name: {{ $spec.slug | quote }}
metrics:
  enabled: {{ $ctx.Values.metrics.enabled }}
  listen: {{ printf "0.0.0.0:%v" $ctx.Values.metrics.port | quote }}
{{- if and $spec.needsNats $nats }}
nats:
  url: {{ $nats | quote }}
{{- end }}
{{- if and $spec.needsRedis $redis }}
redis:
  url: {{ $redis | quote }}
rate_limit:
  backend: redis
{{- end }}
{{- if eq $service "frontend" }}
frontend:
  api_upstream: {{ include "tankovault.url" (dict "ctx" $ctx "service" "api") | quote }}
{{- end }}
{{- if eq $service "api" }}
{{- if $ctx.Values.services.controlPlane.enabled }}
control_plane_url: {{ include "tankovault.url" (dict "ctx" $ctx "service" "controlPlane") | quote }}
{{- end }}
{{- if $ctx.Values.services.sync.enabled }}
sync_url: {{ include "tankovault.url" (dict "ctx" $ctx "service" "sync") | quote }}
{{- end }}
{{- if $ctx.Values.services.worker.enabled }}
worker_url: {{ include "tankovault.url" (dict "ctx" $ctx "service" "worker") | quote }}
{{- end }}
{{- if $external }}
auth:
  webauthn_origin: {{ $external | quote }}
{{- end }}
{{- end }}
{{- if and (eq $service "worker") $ctx.Values.services.challengeSolver.enabled }}
worker:
  challenge_solver_endpoint: {{ include "tankovault.url" (dict "ctx" $ctx "service" "challengeSolver") | quote }}
{{- end }}
{{- if eq $service "challengeSolver" }}
{{- with include "tankovault.trawlUrl" $ctx }}
solver:
  trawl_endpoint: {{ . | quote }}
{{- end }}
{{- end }}
{{- if eq $service "sync" }}
anilist:
  redirect_uri: {{ default (printf "%s/account/anilist-callback" $external) $ctx.Values.anilist.redirectUri | quote }}
{{- end }}
{{- if or (eq $service "api") (eq $service "notifier") }}
{{- $email := $ctx.Values.email }}
{{- $baseUrl := default $external $email.baseUrl }}
{{- if or $email.host $baseUrl }}
email:
  {{- with $email.host }}
  host: {{ . | quote }}
  port: {{ $email.port }}
  security: {{ $email.security | quote }}
  {{- end }}
  {{- with $email.from }}
  from: {{ . | quote }}
  {{- end }}
  {{- with $baseUrl }}
  base_url: {{ . | quote }}
  {{- end }}
{{- end }}
{{- end }}
{{- if eq $service "notifier" }}
{{- with $ctx.Values.channels.emailTo }}
channels:
  email_to:
    {{- toYaml . | nindent 4 }}
{{- end }}
{{- end }}
{{- if eq $service "api" }}
{{- with (include "tankovault.legal.config" $ctx) }}
legal:
  {{- . | nindent 2 }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
The `[legal]` block, or empty when the operator has published nothing.

Only `api` gets it: `api` is the one service that reads it, serving the index unauthenticated at
`GET /v1/legal` and each document at `GET /v1/legal/{slug}` — the frontend's footer is built from
that index, so it needs the API, not the files. Emitting the block on the other seven would give
them a configuration key they never read and a volume they never open.

`sources` is a path, `content` is the text itself, and both arrive here as `sources` because that
is the only shape the service understands. The difference is who owns the file: `content` is
written into a ConfigMap by this chart under `<slug>.<locale>.md`, `sources` names a path the
operator has mounted some other way. Both resolve against `legal.dir` unless absolute, and both
are read on demand behind an mtime check — so correcting a policy is `kubectl edit configmap`
plus the kubelet's refresh interval, never a restart. `content` therefore deliberately does not
feed the pod's `checksum/config` annotation.

A document carrying `url` instead is a link to something hosted elsewhere and mounts nothing.
*/}}
{{- define "tankovault.legal.config" -}}
{{- $ctx := . -}}
{{- $documents := dict -}}
{{- range $slug, $doc := $ctx.Values.legal.documents -}}
{{- $doc = $doc | default dict -}}
{{- $entry := dict -}}
{{- with $doc.title }}{{- $_ := set $entry "title" . }}{{- end -}}
{{- with $doc.updated }}{{- $_ := set $entry "updated" . }}{{- end -}}
{{- if $doc.url -}}
{{- $_ := set $entry "url" $doc.url -}}
{{- else -}}
{{- $sources := dict -}}
{{- range $locale, $_body := ($doc.content | default dict) -}}
{{- $_ := set $sources $locale (printf "%s.%s.md" $slug $locale) -}}
{{- end -}}
{{- /* An explicit `sources` path wins: it names a file the operator mounted deliberately. */ -}}
{{- range $locale, $path := ($doc.sources | default dict) -}}
{{- $_ := set $sources $locale $path -}}
{{- end -}}
{{- $_ := set $entry "sources" $sources -}}
{{- end -}}
{{- $_ := set $documents $slug $entry -}}
{{- end -}}
{{- if $documents -}}
dir: {{ $ctx.Values.legal.dir | quote }}
documents:
  {{- toYaml $documents | nindent 2 }}
{{- end -}}
{{- end -}}

{{/*
The inline document bodies, as ConfigMap `data`. Empty when every published document is a `url`
or points at a path the operator mounts themselves, in which case no ConfigMap is created and no
volume is added.
*/}}
{{- define "tankovault.legal.files" -}}
{{- range $slug, $doc := .Values.legal.documents -}}
{{- $doc = $doc | default dict -}}
{{- if not $doc.url -}}
{{- range $locale, $body := ($doc.content | default dict) }}
{{ printf "%s.%s.md" $slug $locale }}: |
  {{- $body | nindent 2 }}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The complete TOML document for one service.

Rendered as a single file rather than as several fragments merged by the service at runtime.
The service does support a directory of `*.toml` fragments, but relying on that would put the
precedence of a global value against a per-service one in a merge implementation this chart
does not own. Merging here, with `mergeOverwrite`, makes the result auditable in
`kubectl get configmap -o yaml` and identical to what the chart's tests assert.

Precedence, lowest to highest: `.Values.config`, chart-derived wiring,
`.Values.services.<name>.config`.
*/}}
{{- define "tankovault.configToml" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
{{- $derived := include "tankovault.derivedConfig" (dict "ctx" $ctx "service" $service) | fromYaml -}}
{{- $svc := (index $ctx.Values.services $service) | default dict -}}
{{- include "tankovault.tomlMerged" (dict "maps" (list $ctx.Values.config $derived $svc.config)) | trim }}
{{- with $ctx.Values.configExtraToml }}

{{ . | trim }}
{{- end }}
{{- end -}}

{{/*
Process-level environment. Deliberately tiny.

`docs/CONFIGURATION.md` §7: a key supplied by both the environment and a file **fails the
boot**, naming the key and both sources. Keeping configuration entirely in files means that
collision is structurally impossible, and it is also what makes reload work — an environment
variable cannot be rotated under a running process. Only the keys that are read before the
layered configuration exists are passed this way; none of them can be file-sourced.

`TANKOVAULT_SECRETS_DIR` is emitted only for a pod that actually mounts the secrets volume.
The directory it names is not optional to the service: a configured secrets directory that
cannot be read is a boot failure naming the path, not an empty layer. A pod projects only its
own `secretKeys`, and `frontend` has none — so pointing it at a directory no volume provides
crash-loops it on `which could not be read: No such file or directory`.

Args: ctx (root), secrets (truthy when the pod mounts the secrets volume).
*/}}
{{- define "tankovault.env" -}}
- name: TANKOVAULT_PROFILE
  value: {{ .ctx.Values.profile | quote }}
- name: TANKOVAULT_CONFIG
  value: {{ .ctx.Values.configReload.configDir | quote }}
{{- if .secrets }}
- name: TANKOVAULT_SECRETS_DIR
  value: {{ .ctx.Values.configReload.secretsDir | quote }}
{{- end }}
{{- end -}}

{{/*
The config and secrets volumes.

The config volume is a plain ConfigMap volume and the secrets volume is a `projected` one, so
the kubelet keeps both up to date in place. Neither may ever be mounted with `subPath`: a
subPath mount is resolved once at container start and never receives updates, which would
silently turn every configuration change back into "restart the pod to pick it up".
*/}}
{{/*
Whether one service's pod carries a secrets volume at all — the single predicate the volume,
its mount and `TANKOVAULT_SECRETS_DIR` all read, so the three can never disagree. Empty when
the service projects no keys; `frontend` is the case that exists today.

Args: ctx (root), service.
*/}}
{{- define "tankovault.hasSecrets" -}}
{{- include "tankovault.secretSources" (dict "ctx" .ctx "service" .service) | trim -}}
{{- end -}}

{{- define "tankovault.volumes" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
- name: config
  configMap:
    name: {{ include "tankovault.fullname" (dict "ctx" $ctx "service" $service) }}-config
{{- $sources := include "tankovault.hasSecrets" (dict "ctx" $ctx "service" $service) }}
{{- if $sources }}
- name: secrets
  projected:
    defaultMode: 0400
    sources:
      {{- $sources | nindent 6 }}
{{- end }}
{{- if and (eq $service "api") (include "tankovault.legal.files" $ctx | trim) }}
- name: legal
  configMap:
    name: {{ include "common.fullname.suffixed" (dict "ctx" $ctx "suffix" "legal") }}
{{- end }}
{{- end -}}

{{- define "tankovault.volumeMounts" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
- name: config
  mountPath: {{ $ctx.Values.configReload.configDir | quote }}
  readOnly: true
{{- if include "tankovault.hasSecrets" (dict "ctx" $ctx "service" $service) }}
- name: secrets
  mountPath: {{ $ctx.Values.configReload.secretsDir | quote }}
  readOnly: true
{{- end }}
{{- if and (eq $service "api") (include "tankovault.legal.files" $ctx | trim) }}
- name: legal
  mountPath: {{ $ctx.Values.legal.dir | quote }}
  readOnly: true
{{- end }}
{{- end -}}
