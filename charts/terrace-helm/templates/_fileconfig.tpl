{{/*
File-backed configuration: the volumes, mounts and process environment that hand a workload
its settings as files instead of as environment variables.

Every application in this repository loads configuration in layers — struct defaults, a TOML
file, `<PREFIX>_`-prefixed environment variables, a directory of key-named secret files, and
`<PREFIX>_<KEY>_FILE` indirection — and **the last three are mutually exclusive per key**: a
key supplied by two of them fails the boot naming the key rather than being resolved by
precedence. Keeping the chart's own output entirely in files therefore makes that collision
structurally impossible, and it is also what lets the services that watch their configuration
pick up a rotated credential without a restart. Only the two variables that decide what the
layers *are* — `<PREFIX>_CONFIG` and `<PREFIX>_SECRETS_DIR` — are passed as environment, and
neither can itself be file-sourced.

Neither volume may ever be mounted with `subPath`: a subPath mount is resolved once at
container start and never receives kubelet updates, which would silently turn every
configuration change back into "restart the pod to pick it up".

Value contract, documented in this chart's `values.yaml`:

  configMount.configDir   directory the rendered `config.toml` is mounted at
  configMount.secretsDir  directory the credential files are mounted at
  existingSecret          an operator-supplied Secret, replacing the chart's own

The ConfigMap and Secret names are arguments rather than values: how a chart names its objects,
and whether an operator may substitute their own, is the chart's decision, not this library's.
*/}}

{{/*
The projected-volume sources holding one workload's credentials.

Each key is a configuration path with `__` for nesting and no dots, because that is how a file
in `<PREFIX>_SECRETS_DIR` has to be named — a `.` in the name is refused rather than treated as
a separator.

`optional: true` covers both a missing Secret and a missing key, which is what makes an
optional credential simply absent rather than a mount failure. A genuinely missing *required*
credential still fails loudly: the service refuses to boot and names the key.

Renders empty for an empty key list, which is the single predicate the volume, its mount and
`<PREFIX>_SECRETS_DIR` all read, so the three can never disagree.

Arguments:
  secretName  (required when keys is non-empty) the Secret the credentials are read from
  keys        list of secret file names
*/}}
{{- define "terrace-helm.fileConfig.secretSources" -}}
{{- $secretName := .secretName -}}
{{- with .keys }}
- secret:
    name: {{ required "terrace-helm.fileConfig: secretName is required when there are secret keys" $secretName }}
    optional: true
    items:
      {{- range $key := . }}
      - key: {{ $key }}
        path: {{ $key }}
      {{- end }}
{{- end }}
{{- end -}}

{{/*
The config and secrets volumes.

The config volume is a plain ConfigMap volume and the secrets volume is a `projected` one, so
the kubelet keeps both up to date in place and the applications that follow the `..data`
symlink a projected volume uses observe the change.

Arguments:
  configMapName  (required) the ConfigMap holding the rendered configuration
  secretName     (required when secretKeys is non-empty) the Secret holding the credentials
  secretKeys     list of secret file names; empty renders no secrets volume
*/}}
{{- define "terrace-helm.fileConfig.volumes" -}}
- name: config
  configMap:
    name: {{ required "terrace-helm.fileConfig.volumes: configMapName is required" .configMapName }}
{{- with (include "terrace-helm.fileConfig.secretSources" (dict "secretName" .secretName "keys" .secretKeys)) }}
- name: secrets
  projected:
    defaultMode: 0400
    sources:
      {{- . | nindent 6 }}
{{- end }}
{{- end -}}

{{/*
The matching read-only mounts.

Arguments:
  ctx         (required) root context
  secretKeys  list of secret file names; empty renders no secrets mount
*/}}
{{- define "terrace-helm.fileConfig.volumeMounts" -}}
{{- $ctx := .ctx -}}
- name: config
  mountPath: {{ $ctx.Values.configMount.configDir | quote }}
  readOnly: true
{{- if .secretKeys }}
- name: secrets
  mountPath: {{ $ctx.Values.configMount.secretsDir | quote }}
  readOnly: true
{{- end }}
{{- end -}}

{{/*
The two loader variables, and nothing else.

`<PREFIX>_SECRETS_DIR` is emitted only for a pod that actually mounts the secrets volume. The
directory it names is not optional to the service: a configured secrets directory that cannot
be read is a boot failure naming the path, not an empty layer.

Arguments:
  ctx         (required) root context
  prefix      (required) the application's variable prefix, e.g. `WEBHOOK_REDIRECT`
  secretKeys  list of secret file names; empty omits `<PREFIX>_SECRETS_DIR`
*/}}
{{- define "terrace-helm.fileConfig.env" -}}
{{- $ctx := .ctx -}}
- name: {{ .prefix }}_CONFIG
  value: {{ $ctx.Values.configMount.configDir | quote }}
{{- if .secretKeys }}
- name: {{ .prefix }}_SECRETS_DIR
  value: {{ $ctx.Values.configMount.secretsDir | quote }}
{{- end }}
{{- end -}}

{{/*
The keys a chart's own Secret should carry: every entry of `data` whose value is non-empty.

An unset optional credential is omitted entirely rather than written as an empty file, because
an empty file is a *supplied* value to the loader and a supplied-but-blank credential is not
the same thing as an absent one.

Arguments:
  data  (required) map of secret file name to value
*/}}
{{- define "terrace-helm.fileConfig.secretData" -}}
{{- $out := dict -}}
{{- range $key, $value := .data -}}
{{- if $value -}}
{{- $_ := set $out $key ($value | toString) -}}
{{- end -}}
{{- end -}}
{{- with $out -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}

{{/*
The secret file names a pod projects, as a YAML list: the chart's own non-empty credentials,
or — when the operator points at their own Secret — every key the chart knows how to consume,
since the chart cannot see inside it.

Parse with `fromYamlArray` before passing the result on as `secretKeys`.

Arguments:
  ctx   (required) root context
  data  (required) map of secret file name to value
*/}}
{{- define "terrace-helm.fileConfig.secretKeys" -}}
{{- $ctx := .ctx -}}
{{- $source := .data -}}
{{- if not $ctx.Values.existingSecret -}}
{{- $source = include "terrace-helm.fileConfig.secretData" (dict "data" .data) | fromYaml -}}
{{- end -}}
{{- with (keys $source | sortAlpha) -}}
{{- toYaml . -}}
{{- end -}}
{{- end -}}

{{/*
Whether this chart renders a Secret of its own. An `existingSecret` always wins, so that a
deployment can keep every credential out of `helm get values` entirely, and a chart with no
credential configured renders no empty Secret at all.

Arguments:
  ctx   (required) root context
  data  (required) map of secret file name to value
*/}}
{{- define "terrace-helm.fileConfig.createSecret" -}}
{{- if not .ctx.Values.existingSecret -}}
{{- if include "terrace-helm.fileConfig.secretData" (dict "data" .data) -}}true{{- end -}}
{{- end -}}
{{- end -}}
