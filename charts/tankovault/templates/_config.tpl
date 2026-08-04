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
{{- end -}}
