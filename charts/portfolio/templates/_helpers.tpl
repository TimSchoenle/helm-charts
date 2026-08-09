{{/*
The configuration the chart derives from its own first-class values, as the TOML tree the
server reads.

`PORT`, `IP` and `RUST_LOG` are deliberately absent: they belong to the Dioxus toolchain, which
reads them from the environment itself, not to the `PORTFOLIO_` namespace this file describes.
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
{{- if not .Values.configExtraToml -}}
{{- $config := include "portfolio.effectiveConfig" . | fromYaml -}}
{{- $hashInlineScripts := $config | dig "csp" "hash_inline_scripts" true -}}
{{- $scriptNonce := $config | dig "csp" "cloudflare" "script_nonce" true -}}
{{- if and (not $hashInlineScripts) $scriptNonce -}}
{{- $messages := list "  - csp.hashInlineScripts is false while csp.cloudflare.scriptNonce is true: dropping the hashes restores 'unsafe-inline', which a browser ignores as soon as the policy carries a nonce, so every document would render blank. Turn scriptNonce off as well, or leave hashInlineScripts on." -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}
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
- name: PORTFOLIO_ISR__CACHE_DIR
  value: {{ $config | dig "isr" "cache_dir" "" | quote }}
{{- end -}}
