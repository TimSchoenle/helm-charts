{{/*
A credential the chart generates for itself and then remembers.

`lookup` reads the value back out of the Secret this chart already created, so a `helm upgrade`
never silently rotates a generated credential out from under whatever is already using it: a
rotated database password no longer matches the server it was initialised with, and a rotated
AniList key can no longer open the tokens it sealed. Only a genuine first install generates one.

MUST be called at most once per rendered document — the fallbacks are random, so a second call
in the same document would yield a different value. Everything that needs one therefore goes
through `tankovault.secretData`, which resolves each into a variable exactly once.

Note `helm template` has no cluster to look up, so a generated credential renders differently on
every invocation. Every `ci/` and test values file sets them explicitly for that reason.

Args: ctx, key (the Secret key it is remembered under), fallback (the value to generate).
*/}}
{{- define "tankovault.rememberedSecret" -}}
{{- $ctx := .ctx -}}
{{- $name := include "common.fullname" $ctx -}}
{{- $existing := lookup "v1" "Secret" (include "common.namespace" $ctx) $name -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- index $existing.data .key | b64dec -}}
{{- else -}}
{{- .fallback -}}
{{- end -}}
{{- end -}}

{{/*
The token signing secret: whatever the operator set, else generated and remembered.

Nothing outside the release ever verifies these tokens, so the value carries no meaning an
operator could supply better than `rand` can. Rotating it signs every user out, which is exactly
why it is looked up rather than regenerated on each upgrade.
*/}}
{{- define "tankovault.jwtSecret" -}}
{{- if .Values.auth.jwtSecret -}}
{{- .Values.auth.jwtSecret -}}
{{- else -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "auth__jwt_secret" "fallback" (randAlphaNum 48)) -}}
{{- end -}}
{{- end -}}

{{/*
The seeded administrator's password: whatever the operator set, else generated and remembered.

Unlike the other generated credentials this one is meant to be read back — NOTES.txt prints the
`kubectl get secret` line for it — because a human has to type it once. It is still generated
rather than defaulted, since a default administrator password is not a password.
*/}}
{{- define "tankovault.seedAdminPassword" -}}
{{- if .Values.bootstrap.seedAdmin.password -}}
{{- .Values.bootstrap.seedAdmin.password -}}
{{- else if .Values.bootstrap.seedAdmin.enabled -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "seed_admin_password" "fallback" (randAlphaNum 24)) -}}
{{- end -}}
{{- end -}}

{{/*
The bundled PostgreSQL password: whatever the operator set, else generated and remembered.
*/}}
{{- define "tankovault.postgresqlPassword" -}}
{{- if .Values.postgresql.auth.password -}}
{{- .Values.postgresql.auth.password -}}
{{- else -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "postgresql__password" "fallback" (randAlphaNum 32)) -}}
{{- end -}}
{{- end -}}

{{/*
The shared service-to-service token: whatever the operator set, else generated and remembered.

The `production` profile refuses to boot without it, and the value means nothing outside this
release — nothing but these six services ever presents it — so there is no decision for an
operator to make, only a step to forget. Outside `production` the token stays optional and stays
unset, because there the services accept internal calls with or without one.
*/}}
{{- define "tankovault.internalToken" -}}
{{- if .Values.internal.token -}}
{{- .Values.internal.token -}}
{{- else if eq .Values.profile "production" -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "internal__token" "fallback" (randAlphaNum 32)) -}}
{{- end -}}
{{- end -}}

{{/*
The AniList token-encryption key: whatever the operator set, else generated and remembered.

The service requires base64 of exactly 32 bytes, which is what `randAlphaNum 32 | b64enc`
produces. Unlike the client id and secret, which identify an application registered with a third
party, this key is purely local and its only requirement is that it never changes. Generated
only when `sync` is enabled, so a release that never links AniList carries no key for it.
*/}}
{{- define "tankovault.anilistTokenEncryptionKey" -}}
{{- if .Values.anilist.tokenEncryptionKey -}}
{{- .Values.anilist.tokenEncryptionKey -}}
{{- else if .Values.services.sync.enabled -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "anilist__token_encryption_key" "fallback" (randAlphaNum 32 | b64enc)) -}}
{{- end -}}
{{- end -}}

{{/*
Every credential the chart itself manages, as a `key: value` YAML map.

Keys are configuration paths with `__` for nesting and no dots, because that is exactly how
`docs/CONFIGURATION.md` §7 says a file in `TANKOVAULT_SECRETS_DIR` must be named — a `.` in the
name is refused rather than treated as a separator. Empty values are omitted entirely so that
an unset optional credential is absent rather than present-and-blank, which the service treats
differently.
*/}}
{{- define "tankovault.secretData" -}}
{{- $ctx := . -}}
{{- $data := dict -}}
{{- with include "tankovault.jwtSecret" $ctx }}{{- $_ := set $data "auth__jwt_secret" . }}{{- end -}}
{{- with $ctx.Values.auth.passwordPepper }}{{- $_ := set $data "auth__password_pepper" . }}{{- end -}}
{{- with include "tankovault.internalToken" $ctx }}{{- $_ := set $data "internal__token" . }}{{- end -}}
{{- with $ctx.Values.anilist.clientId }}{{- $_ := set $data "anilist__client_id" . }}{{- end -}}
{{- with $ctx.Values.anilist.clientSecret }}{{- $_ := set $data "anilist__client_secret" . }}{{- end -}}
{{- with include "tankovault.anilistTokenEncryptionKey" $ctx }}{{- $_ := set $data "anilist__token_encryption_key" . }}{{- end -}}
{{- with $ctx.Values.email.username }}{{- $_ := set $data "email__username" . }}{{- end -}}
{{- with $ctx.Values.email.password }}{{- $_ := set $data "email__password" . }}{{- end -}}
{{- with $ctx.Values.channels.discordWebhookUrl }}{{- $_ := set $data "channels__discord_webhook_url" . }}{{- end -}}
{{- with $ctx.Values.channels.webhookUrl }}{{- $_ := set $data "channels__webhook_url" . }}{{- end -}}
{{- with include "tankovault.seedAdminPassword" $ctx }}{{- $_ := set $data "seed_admin_password" . }}{{- end -}}
{{- if $ctx.Values.postgresql.enabled -}}
{{- $password := include "tankovault.postgresqlPassword" $ctx -}}
{{- $auth := $ctx.Values.postgresql.auth -}}
{{- $host := include "common.fullname.suffixed" (dict "ctx" $ctx "suffix" "postgresql") -}}
{{- $_ := set $data "postgresql__password" $password -}}
{{- $_ := set $data "database__url" (printf "postgres://%s:%s@%s:5432/%s" $auth.username $password $host $auth.database) -}}
{{- else if and $ctx.Values.externalDatabase.url (not $ctx.Values.externalDatabase.existingSecret) -}}
{{- $_ := set $data "database__url" $ctx.Values.externalDatabase.url -}}
{{- end -}}
{{- toYaml $data -}}
{{- end -}}

{{/*
Whether this chart renders a Secret of its own. An `existingSecret` always wins, so that a
deployment can keep every credential out of `helm get values` entirely.
*/}}
{{- define "tankovault.createSecret" -}}
{{- if not .Values.existingSecret -}}
{{- if include "tankovault.secretData" . | fromYaml -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The name of the Secret holding the chart-managed credentials.
*/}}
{{- define "tankovault.secretName" -}}
{{- default (include "common.fullname" .) .Values.existingSecret -}}
{{- end -}}

{{/*
The projected-volume sources for one workload's credentials.

Each pod receives only the keys its own service reads: `docs/CONFIGURATION.md` lists which
services consume which block, and `tankovault.serviceSpecs` encodes it. A `worker` therefore
never has `auth__jwt_secret` on its filesystem, so compromising the tier that parses untrusted
HTML does not yield the token-signing key.

`optional: true` covers both a missing Secret and a missing key, which is what makes an
optional credential — an SMTP password, a Discord webhook — simply absent rather than a mount
failure. A genuinely missing *required* credential still fails loudly: the service refuses to
boot and names the key.

Args: ctx, service (a service key), or `keys` for a caller with its own key list.
*/}}
{{- define "tankovault.secretSources" -}}
{{- $ctx := .ctx -}}
{{- $keys := .keys -}}
{{- if not $keys -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- $keys = $spec.secretKeys | default list -}}
{{- end -}}
{{- $dbExternal := and (has "database__url" $keys) $ctx.Values.externalDatabase.existingSecret -}}
{{- $redisExternal := and (has "redis__url" $keys) $ctx.Values.externalRedis.existingSecret -}}
{{- $own := list -}}
{{- range $k := $keys -}}
{{- if and (eq $k "database__url") $dbExternal -}}
{{- else if and (eq $k "redis__url") $redisExternal -}}
{{- else -}}
{{- $own = append $own $k -}}
{{- end -}}
{{- end -}}
{{- if and $own (or $ctx.Values.existingSecret (include "tankovault.createSecret" $ctx)) }}
- secret:
    name: {{ include "tankovault.secretName" $ctx }}
    optional: true
    items:
      {{- range $k := $own }}
      - key: {{ $k }}
        path: {{ $k }}
      {{- end }}
{{- end }}
{{- if $dbExternal }}
- secret:
    name: {{ $ctx.Values.externalDatabase.existingSecret }}
    items:
      - key: {{ $ctx.Values.externalDatabase.urlKey }}
        path: database__url
{{- end }}
{{- if $redisExternal }}
- secret:
    name: {{ $ctx.Values.externalRedis.existingSecret }}
    items:
      - key: {{ $ctx.Values.externalRedis.urlKey }}
        path: redis__url
{{- end }}
{{- end -}}
