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

`firstInstallOnly` narrows that to installs this chart has not created a Secret for yet. It is
for credentials that were optional before they were generated: a release already running without
one must keep running without it, because introducing the value later is not a no-op — the
password pepper invalidates every stored password. An absent key next to an existing Secret is
therefore read as a decision, not as a gap to fill.

Args: ctx, key (the Secret key it is remembered under), fallback (the value to generate),
firstInstallOnly (optional).
*/}}
{{- define "tankovault.rememberedSecret" -}}
{{- $ctx := .ctx -}}
{{- $name := include "common.fullname" $ctx -}}
{{- $existing := lookup "v1" "Secret" (include "common.namespace" $ctx) $name -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- index $existing.data .key | b64dec -}}
{{- else if and .firstInstallOnly $existing -}}
{{- /* The release predates this key and has been running without it. Leave it that way. */ -}}
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
The password pepper: whatever the operator set, else generated on a first install and remembered.

Every argon2id hash is peppered with it, so a database leak alone cannot be brute-forced offline
— worth having by default. It is also the one credential whose *appearance* is destructive: a
release that has been storing unpeppered hashes would find every one of them unverifiable the
moment a pepper existed. Hence `firstInstallOnly`: a Secret without the key stays without it, and
only an install that has no Secret at all gets one. The corollary is that losing this Secret
loses every password with it, which is true of the pepper however it was set.
*/}}
{{- define "tankovault.passwordPepper" -}}
{{- if .Values.auth.passwordPepper -}}
{{- .Values.auth.passwordPepper -}}
{{- else -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "auth__password_pepper" "fallback" (randAlphaNum 32) "firstInstallOnly" true) -}}
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
One caller's internal token: whatever the operator set, else generated and remembered.

Read only when `internal.identity` is `token`. There is one of these per *caller*, not one per
service and not one for the release: holding `api`'s token opens the routes `api` is allowed to
call and nothing else, so a compromised `challenge-solver` — which is only ever a callee — yields
no credential at all.

The value means nothing outside this release and upstream length-checks it at 32 characters in
every profile, so an unset one is generated rather than demanded. Generated only for callers that
are actually deployed; a token for a service nobody enabled is a secret nothing reads.

Args: ctx, caller (a caller name, e.g. `api`).
*/}}
{{- define "tankovault.internalToken" -}}
{{- $explicit := index (.ctx.Values.internal.tokens | default dict) .caller -}}
{{- if $explicit -}}
{{- $explicit -}}
{{- else -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" .ctx "key" (include "tankovault.internalTokenKey" .caller) "fallback" (randAlphaNum 48)) -}}
{{- end -}}
{{- end -}}

{{/*
The Secret key one caller's token is stored under.

Deliberately not a configuration path, which every other key in this Secret is. One token is read
by two different services under two different names — the caller reads it as
`internal.caller.token`, each of its callees as `internal.peers.<caller>.token` — and a Secret
key can only be spelled one way. The *projected path* is what has to be the configuration path,
and `tankovault.internalSecretItems` maps this key onto whichever one the reading pod needs.
*/}}
{{- define "tankovault.internalTokenKey" -}}
{{- printf "internal__tokens__%s" . -}}
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
The MFA secret-encryption key: whatever the operator set, else generated and remembered.

Seals every enrolled TOTP secret at rest, and the service requires base64 of exactly 32 bytes —
what `randAlphaNum 32 | b64enc` produces. A TOTP secret is symmetric, unlike a password hash:
whoever reads the column can mint that account's codes, so a database dump must not be enough on
its own.

Generated rather than left to the operator, because unset does not fail — it disables
authenticator-app enrolment and says so only in a boot log. The account that step lands on is the
seeded administrator, which holds every write permission and so is required to enrol a second
factor before it can administer anything; without this key its only remaining option is a hardware
security key. A `helm install` that quietly demands one is not a working first install.

Only `api` reads it, so only `api` is given it. Like the AniList key it must never change once
enrolments exist: rotating it locks every enrolled account out of its second factor.
*/}}
{{- define "tankovault.mfaEncryptionKey" -}}
{{- if .Values.auth.mfaEncryptionKey -}}
{{- .Values.auth.mfaEncryptionKey -}}
{{- else if .Values.services.api.enabled -}}
{{- include "tankovault.rememberedSecret" (dict "ctx" . "key" "auth__mfa_encryption_key" "fallback" (randAlphaNum 32 | b64enc)) -}}
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
{{- with include "tankovault.passwordPepper" $ctx }}{{- $_ := set $data "auth__password_pepper" . }}{{- end -}}
{{- with include "tankovault.mfaEncryptionKey" $ctx }}{{- $_ := set $data "auth__mfa_encryption_key" . }}{{- end -}}
{{- if eq $ctx.Values.internal.identity "token" -}}
{{- range $caller, $service := (include "tankovault.internalCallers" $ctx | fromYaml) -}}
{{- $_ := set $data (include "tankovault.internalTokenKey" $caller) (include "tankovault.internalToken" (dict "ctx" $ctx "caller" $caller)) -}}
{{- end -}}
{{- end -}}
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
The internal-token projections for one workload, as `key`/`path` pairs.

Empty under `mtls`, where identification is the client certificate and no token exists at all —
this is the half of "never both credentials at once" that the peer configuration cannot express
on its own.

Each pair maps a stored token onto the configuration path the *reading* service knows it by: its
own token arrives as `internal__caller__token`, and each caller it accepts arrives as
`internal__peers__<caller>__token`. `worker` gets both, being the one service that is a caller
and a callee.

Args: ctx (root), service.
*/}}
{{- define "tankovault.internalSecretItems" -}}
{{- $ctx := .ctx -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
{{- if eq $ctx.Values.internal.identity "token" -}}
{{- $peers := include "tankovault.internalPeers" (dict "ctx" $ctx "service" .service) | fromYaml }}
{{- with $spec.internalCaller }}
- key: {{ include "tankovault.internalTokenKey" . }}
  path: internal__caller__token
{{- end }}
{{- range $peer, $peerService := $peers }}
- key: {{ include "tankovault.internalTokenKey" $peer }}
  path: internal__peers__{{ $peer }}__token
{{- end }}
{{- end -}}
{{- end -}}

{{/*
The projected-volume sources for one workload's credentials.

Each pod receives only the keys its own service reads: `docs/CONFIGURATION.md` lists which
services consume which block, and `tankovault.serviceSpecs` encodes it. A `worker` therefore
never has `auth__jwt_secret` on its filesystem, so compromising the tier that parses untrusted
HTML does not yield the token-signing key.

Every entry is a `key`/`path` pair rather than a bare key, because the two are not always the
same string: the file name in `TANKOVAULT_SECRETS_DIR` is a configuration path and must be, while
the Secret key underneath it is only storage. `database__url` out of an `externalDatabase`
Secret and the internal tokens both rely on that.

`optional: true` covers both a missing Secret and a missing key, which is what makes an
optional credential — an SMTP password, a Discord webhook — simply absent rather than a mount
failure. A genuinely missing *required* credential still fails loudly: the service refuses to
boot and names the key.

Args: ctx, service (a service key), or `keys` for a caller with its own key list. The internal
and per-service NATS projections are derived from the service and so appear only in the first
form; the bootstrap steps that pass `keys` are not internal peers and speak to NATS not at all.
*/}}
{{- define "tankovault.secretSources" -}}
{{- $ctx := .ctx -}}
{{- $service := .service -}}
{{- $keys := .keys -}}
{{- $spec := dict -}}
{{- if not $keys -}}
{{- $spec = include "tankovault.spec" $service | fromYaml -}}
{{- $keys = $spec.secretKeys | default list -}}
{{- end -}}
{{- $dbExternal := and (has "database__url" $keys) $ctx.Values.externalDatabase.existingSecret -}}
{{- $redisExternal := and (has "redis__url" $keys) $ctx.Values.externalRedis.existingSecret -}}
{{- $items := list -}}
{{- range $k := $keys -}}
{{- if and (eq $k "database__url") $dbExternal -}}
{{- else if and (eq $k "redis__url") $redisExternal -}}
{{- else -}}
{{- $items = append $items (dict "key" $k "path" $k) -}}
{{- end -}}
{{- end -}}
{{- if $service -}}
{{- with include "tankovault.internalSecretItems" (dict "ctx" $ctx "service" $service) | fromYamlArray -}}
{{- $items = concat $items . -}}
{{- end -}}
{{- end -}}
{{- if and $items (or $ctx.Values.existingSecret (include "tankovault.createSecret" $ctx)) }}
- secret:
    name: {{ include "tankovault.secretName" $ctx }}
    optional: true
    items:
      {{- range $item := $items }}
      - key: {{ $item.key }}
        path: {{ $item.path }}
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
{{- if and $spec.needsNats $ctx.Values.externalNats.perServiceSecret }}
{{- /*
  A per-service NATS account, delivered as this service's own connection URL. It carries the
  credential, so it is a Secret key rather than a config value and it replaces the derived
  `nats.url` entirely — supplying both would leave the account a service actually uses depending
  on which layer won. Not `optional`: a pod that silently starts with no broker URL is the
  failure this exists to prevent.
*/}}
- secret:
    name: {{ $ctx.Values.externalNats.perServiceSecret }}
    items:
      - key: {{ $spec.slug }}
        path: nats__url
{{- end }}
{{- end -}}
