{{/*
Fail fast, and report every problem at once.

These are the misconfigurations that either cannot be expressed in the JSON schema (they are
relationships between values, not shapes) or that the schema would only catch after a partial
rollout. Each message names the value to change, because the alternative is an operator
reading a Rust panic or, worse, a stack that starts cleanly and misbehaves later — a wrong
pepper produces an administrator account that can never log in, and a `memory` rate-limit
backend across replicas silently multiplies every limit by the replica count.
*/}}
{{- define "tankovault.validateValues" -}}
{{- $ctx := . -}}
{{- $errors := list -}}
{{- $managed := not $ctx.Values.existingSecret -}}

{{- /*
Chart-managed credentials. A credential that means nothing outside this release is generated when
left empty rather than demanded (see `tankovault.rememberedSecret` and its callers), so the only
thing left to check is a value the operator did supply. With an existingSecret the chart neither
sees nor generates any of them, and the values here are unused, so it says nothing about them.
*/ -}}
{{- if $managed -}}
{{- if and $ctx.Values.auth.jwtSecret (lt (len $ctx.Values.auth.jwtSecret) 32) -}}
{{- $errors = append $errors (printf "auth.jwtSecret must be at least 32 characters (got %d). Shorter values are refused at boot." (len $ctx.Values.auth.jwtSecret)) -}}
{{- end -}}
{{- if and $ctx.Values.internal.token (lt (len $ctx.Values.internal.token) 32) -}}
{{- $errors = append $errors (printf "internal.token must be at least 32 characters (got %d)." (len $ctx.Values.internal.token)) -}}
{{- end -}}
{{- end -}}

{{- /* Exactly one database source. */ -}}
{{- $dbSources := list -}}
{{- if $ctx.Values.postgresql.enabled -}}{{- $dbSources = append $dbSources "postgresql.enabled" -}}{{- end -}}
{{- if $ctx.Values.externalDatabase.url -}}{{- $dbSources = append $dbSources "externalDatabase.url" -}}{{- end -}}
{{- if $ctx.Values.externalDatabase.existingSecret -}}{{- $dbSources = append $dbSources "externalDatabase.existingSecret" -}}{{- end -}}
{{- if gt (len $dbSources) 1 -}}
{{- $errors = append $errors (printf "exactly one database source may be set, but %s are all configured." (join ", " $dbSources)) -}}
{{- else if and (eq (len $dbSources) 0) $managed -}}
{{- $errors = append $errors "no database configured. Set `postgresql.enabled=true` for the bundled single-instance PostgreSQL, or point `externalDatabase.url` / `externalDatabase.existingSecret` at your own." -}}
{{- end -}}

{{- /*
The AniList application credentials are issued by a third party and cannot be invented here.
The token-encryption key can — it is purely local — so an unset one is generated rather than
rejected; see `tankovault.anilistTokenEncryptionKey`.
*/ -}}
{{- if and $ctx.Values.services.sync.enabled $managed -}}
{{- $anilist := $ctx.Values.anilist -}}
{{- $missing := list -}}
{{- if not $anilist.clientId -}}{{- $missing = append $missing "anilist.clientId" -}}{{- end -}}
{{- if not $anilist.clientSecret -}}{{- $missing = append $missing "anilist.clientSecret" -}}{{- end -}}
{{- if $missing -}}
{{- $errors = append $errors (printf "services.sync.enabled requires the AniList application credentials; missing: %s. Register an application at https://anilist.co/settings/developer, or set services.sync.enabled=false." (join ", " $missing)) -}}
{{- end -}}
{{- end -}}

{{- /* The redirect URI has to resolve to something, and it must be the frontend's. */ -}}
{{- if and $ctx.Values.services.sync.enabled (not $ctx.Values.anilist.redirectUri) (not (include "tankovault.externalUrl" $ctx)) -}}
{{- $errors = append $errors "services.sync.enabled needs a callback URL: either enable the ingress (so it can be derived) or set `anilist.redirectUri`. It must point at the frontend's /account/anilist-callback, not at the API." -}}
{{- end -}}

{{- /*
An unset seed password is generated and printed by NOTES.txt; with an existingSecret the chart
cannot generate one, so the Secret must already carry `seed_admin_password`.
*/ -}}

{{- /* The challenge solver is useless without a backend to delegate to. */ -}}
{{- if and $ctx.Values.services.challengeSolver.enabled (not (include "tankovault.flaresolverrUrl" $ctx)) -}}
{{- $errors = append $errors "services.challengeSolver.enabled requires a solver backend: set `flaresolverr.enabled=true` or point `externalFlaresolverr.url` at one." -}}
{{- end -}}

{{- /* Publishing a privileged service is a security defect, not a preference. */ -}}
{{- if not $ctx.Values.allowUnsafeExposure -}}
{{- range $service := list "controlPlane" "sync" "render" "challengeSolver" -}}
{{- $values := index $ctx.Values.services $service -}}
{{- if and $values.enabled $values.service (ne $values.service.type "ClusterIP") -}}
{{- $errors = append $errors (printf "services.%s.service.type is %q. control-plane, sync, render and challenge-solver expose privileged contracts and upstream publishes none of them; reaching them directly bypasses the frontend proxy and the rate limiter. Set it back to ClusterIP, or set allowUnsafeExposure=true if you genuinely mean it." $service $values.service.type) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /* A worker with nothing to talk to will start and then do nothing useful. */ -}}
{{- if and $ctx.Values.services.worker.enabled (not (include "tankovault.natsUrl" $ctx)) -}}
{{- $errors = append $errors "services.worker.enabled requires NATS JetStream: set `nats.enabled=true` or point `externalNats.url` at one. The worker consumes its scan tasks from a JetStream work queue." -}}
{{- end -}}
{{- if and $ctx.Values.services.controlPlane.enabled (not (include "tankovault.natsUrl" $ctx)) -}}
{{- $errors = append $errors "services.controlPlane.enabled requires NATS JetStream: set `nats.enabled=true` or point `externalNats.url` at one." -}}
{{- end -}}

{{- if $errors -}}
{{- fail (printf "\n\nTankoVault chart configuration is invalid:\n\n  - %s\n" (join "\n  - " $errors)) -}}
{{- end -}}
{{- end -}}
