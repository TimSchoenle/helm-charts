{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
server reads.

`PORT`, `IP` and `RUST_LOG` are deliberately absent: they belong to the Dioxus toolchain, which
reads them from the environment itself, not to the `PORTFOLIO_` namespace this file describes.

`sentry` is written only while its switch is on: every key under it is inert otherwise, so
writing the block unconditionally would put fourteen settings that do nothing into the document
an operator reads to find out what the server is doing. Optional keys inside it are omitted
rather than written empty — an empty `sentry.environment` is a *supplied* value to the loader,
and "reported with a blank environment tag" is not what an operator who left it unset meant. The
DSN never appears here at all: it is a credential and goes to the Secret under `sentry__dsn`.
*/}}
{{- define "portfolio.derivedConfig" -}}
assets:
  dist_dir: {{ .Values.assets.distDir | quote }}
csp:
  hash_inline_scripts: {{ .Values.csp.hashInlineScripts }}
  cloudflare:
    script_nonce: {{ .Values.csp.cloudflare.scriptNonce }}
    turnstile: {{ .Values.csp.cloudflare.turnstile }}
    web_analytics: {{ .Values.csp.cloudflare.webAnalytics }}
isr:
  cache_dir: {{ .Values.isr.cacheDir | quote }}
  ttl_secs: {{ .Values.isr.ttlSecs }}
{{- if .Values.sentry.enabled }}
sentry:
  enabled: true
  {{- with .Values.sentry.environment }}
  environment: {{ . | quote }}
  {{- end }}
  {{- with .Values.sentry.release }}
  release: {{ . | quote }}
  {{- end }}
  {{- with .Values.sentry.serverName }}
  server_name: {{ . | quote }}
  {{- end }}
  sample_rate: {{ .Values.sentry.sampleRate }}
  traces_sample_rate: {{ .Values.sentry.tracesSampleRate }}
  capture_level: {{ .Values.sentry.captureLevel | quote }}
  breadcrumb_level: {{ .Values.sentry.breadcrumbLevel | quote }}
  max_breadcrumbs: {{ .Values.sentry.maxBreadcrumbs }}
  attach_stacktraces: {{ .Values.sentry.attachStacktraces }}
  send_default_pii: {{ .Values.sentry.sendDefaultPii }}
  http_transactions: {{ .Values.sentry.httpTransactions }}
  span_attributes: {{ .Values.sentry.spanAttributes }}
  debug: {{ .Values.sentry.debug }}
{{- end }}
{{- end -}}

{{/*
The configuration that actually reaches the server: the derived tree with the operator's own
`config` tree merged over it, so `config` can both extend and override the values above.

Not included: `configExtraToml`, which is appended verbatim and never parsed.
*/}}
{{- define "portfolio.effectiveConfig" -}}
{{- $derived := include "portfolio.derivedConfig" . | fromYaml -}}
{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.config | default dict))) -}}
{{- end -}}

{{/*
The complete `config.toml`: the effective tree, then the verbatim escape hatch.
*/}}
{{- define "portfolio.configToml" -}}
{{- $config := include "portfolio.effectiveConfig" . | fromYaml -}}
{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}
{{- end -}}

{{/*
Refuse the one Content-Security-Policy combination the server refuses, at render time instead
of at boot.

Dropping the inline-script hashes restores `'unsafe-inline'`, and a browser ignores
`'unsafe-inline'` as soon as the policy carries any nonce — so hashing off with the Cloudflare
nonce still on is a policy that admits no inline script at all, and the page renders blank. The
server fails its boot on it; catching it here turns a CrashLoopBackOff into a failed `helm
upgrade` that names the two keys.

The reverse pair is legitimate and deliberately not rejected: hashing on with the nonce off is
simply a deployment with no Cloudflare bot product in front of it.

Checked against the *effective* tree, so the pair is caught whether it arrives through the
first-class values or through `config`. `configExtraToml` is appended verbatim and never parsed,
so a chart that has one steps out of the way rather than rejecting what it cannot see.
*/}}
{{- define "portfolio.validateValues" -}}
{{- $messages := list -}}
{{- if not .Values.configExtraToml -}}
{{- $config := include "portfolio.effectiveConfig" . | fromYaml -}}
{{- $hashInlineScripts := $config | dig "csp" "hash_inline_scripts" true -}}
{{- $scriptNonce := $config | dig "csp" "cloudflare" "script_nonce" true -}}
{{- if and (not $hashInlineScripts) $scriptNonce -}}
{{- $messages = append $messages "  - csp.hashInlineScripts is false while csp.cloudflare.scriptNonce is true: dropping the hashes restores 'unsafe-inline', which a browser ignores as soon as the policy carries a nonce, so every document would render blank. Turn scriptNonce off as well, or leave hashInlineScripts on." -}}
{{- end -}}
{{- end -}}
{{- /*
Sentry, checked outside the `configExtraToml` guard above.

Both halves of "a reporter that reports nowhere is worse than none". Upstream refuses to boot
with the switch on and no DSN, so that is a CrashLoopBackOff rather than a degraded feature, and
a rejected `helm upgrade` beats one. Unlike the CSP pair, the DSN cannot arrive through
`configExtraToml` — it is a credential and travels the Secret, not the configuration document —
so there is nothing here the escape hatch could be supplying, and stepping out of the way would
only hide the fault. An `existingSecret` is taken as the answer, because the chart cannot see
inside one.

The converse is the ordinary dead-value check: a DSN set while the switch is off is neither
projected into the pod nor written into the Secret, so it would sit in the release meaning
nothing — and a DSN is a credential to leave lying around.
*/ -}}
{{- if and .Values.sentry.enabled (not .Values.sentry.dsn) (not .Values.existingSecret) -}}
{{- $messages = append $messages "  - sentry.enabled is set but no DSN is available. The server refuses to boot rather than installing a client that reports nowhere, so this is a CrashLoopBackOff, not a degraded feature. Set `sentry.dsn`, or put `sentry__dsn` in the Secret named by `existingSecret`." -}}
{{- end -}}
{{- if and (not .Values.sentry.enabled) .Values.sentry.dsn -}}
{{- $messages = append $messages "  - sentry.dsn is set while sentry.enabled is false. No client is installed, so the DSN is neither projected into the pod nor written into the Secret and would sit in the release meaning nothing. Set `sentry.enabled=true`, or clear the DSN." -}}
{{- end -}}
{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}

{{/*
The credential this chart manages, keyed by the file name the loader reads it from: a
configuration path with `__` for nesting and no dots, because a `.` in the name is refused
rather than treated as a separator.

The Sentry DSN is the only one, and it appears only while the switch is on. Listed
unconditionally it would survive into `portfolio.secretKeys` under an `existingSecret` — which
cannot be read from here, so every key the chart knows about is projected — and a pod that
reports to nothing would still carry a secrets volume and a `PORTFOLIO_SECRETS_DIR` pointing
into it.
*/}}
{{- define "portfolio.secretData" -}}
{{- if .Values.sentry.enabled }}
sentry__dsn: {{ .Values.sentry.dsn | quote }}
{{- end }}
{{- end -}}

{{/*
The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`.
*/}}
{{- define "portfolio.secretKeys" -}}
{{- $data := include "portfolio.secretData" . | fromYaml -}}
{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}
{{- end -}}

{{/*
The container environment.

Three variables the Dioxus toolchain reads for itself, one that points the layered loader at the
mounted configuration — and one that exists only to defeat the image.

`PORTFOLIO_ISR__CACHE_DIR` is baked into the published image, and the environment layer outranks
the TOML layer. Left alone, that baked value would silently win over whatever this chart wrote
into `config.toml`, so an operator who moved the cache would find it had not moved. Emitting the
variable with the *effective* value — the same one the file carries — makes the two agree by
construction. The environment and the file are not mutually exclusive layers, so supplying both
is legal; only the environment, the secrets directory and `_FILE` collide with one another.
*/}}
{{- define "portfolio.env" -}}
{{- $config := include "portfolio.effectiveConfig" . | fromYaml }}
- name: PORT
  value: {{ .Values.server.port | quote }}
- name: IP
  value: {{ .Values.server.host | quote }}
- name: RUST_LOG
  value: {{ .Values.logLevel | quote }}
- name: PORTFOLIO_CONFIG
  value: {{ .Values.configMount.configDir | quote }}
{{- /*
Emitted only for a pod that actually mounts the secrets volume — which today means only one
that reports to Sentry. The directory it names is not optional to the server: a configured
secrets directory that cannot be read is a boot failure naming the path, not an empty layer.
*/}}
{{- if (include "portfolio.secretKeys" .) }}
- name: PORTFOLIO_SECRETS_DIR
  value: {{ .Values.configMount.secretsDir | quote }}
{{- end }}
- name: PORTFOLIO_ISR__CACHE_DIR
  value: {{ $config | dig "isr" "cache_dir" "" | quote }}
{{- end -}}
