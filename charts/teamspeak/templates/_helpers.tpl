{{/*
Chart-specific helpers. Everything reusable lives in the `common` library chart; only names
and shapes that are genuinely specific to TeamSpeak belong here, namespaced by chart name.
*/}}

{{/*
Whether a ServerQuery admin password is available to the pod, from either an operator-supplied
Secret or `serverQuery.adminPassword`.

When it is not, the server generates one on first start and prints it to the log — fine for a
hand-administered server, fatal for the metrics exporter, which has to log in.
*/}}
{{- define "teamspeak.hasAdminPassword" -}}
{{- if or .Values.existingSecret .Values.serverQuery.adminPassword -}}
true
{{- end -}}
{{- end -}}

{{/*
Whether the server talks to an external database rather than the bundled SQLite file.
*/}}
{{- define "teamspeak.usesExternalDatabase" -}}
{{- if ne .Values.database.plugin "ts3db_sqlite3" -}}
true
{{- end -}}
{{- end -}}

{{/*
Whether a database password is available to the pod.
*/}}
{{- define "teamspeak.hasDatabasePassword" -}}
{{- if or .Values.existingSecret .Values.database.password -}}
true
{{- end -}}
{{- end -}}

{{/*
Whether the encrypted (SSH) ServerQuery listener is enabled.
*/}}
{{- define "teamspeak.sshQueryEnabled" -}}
{{- if contains "ssh" .Values.serverQuery.protocols -}}
true
{{- end -}}
{{- end -}}

{{/*
Key inside the consumed Secret that holds the ServerQuery admin password.

The chart's own Secret uses a fixed key; only an operator-supplied `existingSecret` gets to
name it, because that Secret already exists and the chart has to adapt to it.
*/}}
{{- define "teamspeak.adminPasswordKey" -}}
{{- if .Values.existingSecret -}}
{{- .Values.serverQuery.existingSecretPasswordKey -}}
{{- else -}}
serveradmin-password
{{- end -}}
{{- end -}}

{{/*
Key inside the consumed Secret that holds the database password.
*/}}
{{- define "teamspeak.databasePasswordKey" -}}
{{- if .Values.existingSecret -}}
{{- .Values.database.existingSecretPasswordKey -}}
{{- else -}}
database-password
{{- end -}}
{{- end -}}

{{/*
Whether this chart has anything to put in a Secret of its own.

`common.createSecret` only asks whether an `existingSecret` was supplied; a server that lets
the image generate its admin password and uses SQLite has no secret material at all, and
rendering an empty Secret for it would be noise.
*/}}
{{- define "teamspeak.createSecret" -}}
{{- if include "common.createSecret" . -}}
{{- if or .Values.serverQuery.adminPassword (and (include "teamspeak.usesExternalDatabase" .) .Values.database.password) -}}
true
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Directory the license file is mounted into. `licensepath` names a directory, not a file, and
the file inside it has to be called `licensekey.dat`.
*/}}
{{- define "teamspeak.licenseMountPath" -}}
/etc/ts3server/license
{{- end -}}

{{/*
Fail the render with every configuration mistake at once, rather than one crash-looping pod
per attempt.

These are the constraints that no JSON schema can express, because they are relationships
between values rather than properties of one.
*/}}
{{- define "teamspeak.validateValues" -}}
{{- $messages := list -}}

{{- if not .Values.license.accept -}}
{{- $messages = append $messages "  - license.accept must be true. The TeamSpeak server refuses to start without an accepted end user license agreement (https://teamspeak.com/en/features/licensing/)." -}}
{{- end -}}

{{- if .Values.metrics.enabled -}}
{{- if not (include "teamspeak.hasAdminPassword" .) -}}
{{- $messages = append $messages "  - metrics.enabled requires a known ServerQuery password: set serverQuery.adminPassword or existingSecret. Left empty, the server generates a random one that only appears in its log, and the exporter cannot authenticate." -}}
{{- end -}}
{{- if not (contains "raw" .Values.serverQuery.protocols) -}}
{{- $messages = append $messages "  - metrics.enabled requires \"raw\" in serverQuery.protocols. The exporter speaks the plaintext ServerQuery protocol over the pod loopback interface, which never leaves the pod." -}}
{{- end -}}
{{- end -}}

{{- if include "teamspeak.usesExternalDatabase" . -}}
{{- if not .Values.database.host -}}
{{- $messages = append $messages (printf "  - database.host is required for the %s plugin." .Values.database.plugin) -}}
{{- end -}}
{{- if not (include "teamspeak.hasDatabasePassword" .) -}}
{{- $messages = append $messages (printf "  - database.password (or existingSecret) is required for the %s plugin." .Values.database.plugin) -}}
{{- end -}}
{{- end -}}

{{- if and .Values.service.querySsh.enabled (not (include "teamspeak.sshQueryEnabled" .)) -}}
{{- $messages = append $messages "  - service.querySsh.enabled requires \"ssh\" in serverQuery.protocols; the server would not be listening on that port." -}}
{{- end -}}

{{- if and (not .Values.persistence.data.enabled) .Values.persistence.data.existingClaim -}}
{{- $messages = append $messages "  - persistence.data.existingClaim is set but persistence.data.enabled is false, so the claim would be ignored and the server would run on an emptyDir." -}}
{{- end -}}

{{- /* Observability. */ -}}
{{- if .Values.metrics.enabled -}}
{{- if .Values.metrics.prometheusRule.enabled -}}
{{- with (include "common.prometheus.rules.errors" (dict
      "ctx" .
      "values" .Values.metrics.prometheusRule
      "feature" "metrics.prometheusRule.enabled"
      "scopePlaceholder" (include "teamspeak.rules.scopePlaceholder" .)
      "scopeMatcher" (include "teamspeak.rules.scopeMatcher" .))) -}}
{{- range splitList "\n" . -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- with (include "common.grafana.dashboard.errors" (dict "ctx" . "values" .Values.metrics.dashboard)) -}}
{{- range splitList "\n" . -}}
{{- $messages = append $messages (printf "  - %s" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- if $messages -}}
{{- fail (printf "\n\nVALUES VALIDATION FAILED for chart %q:\n%s\n" .Chart.Name (join "\n" $messages)) -}}
{{- end -}}
{{- end -}}

{{/*
The scope placeholder every selector in `rules/*.yml` carries, and what it is replaced with.

A PrometheusRule is not confined to the namespace it lives in, so an unscoped
`ts3_serverinfo_online{...}` matches every other TeamSpeak in the cluster and two installs alert
on each other's outages. The rule files therefore carry `teamspeak_scope=~".*"` — an always-true
matcher on a label nothing emits — which `common.prometheus.rules.*` swaps for a real one. See
the library's `_prometheus.tpl` for why the substitution runs in that direction rather than
rewriting PromQL from a Go template.

Three settings rather than the two the other charts offer, because this chart is small enough to
be installed twice in one namespace — a public server and a private one — and `namespace` alone
would let the two alert on each other:

  release    namespace *and* the pod-name prefix, which is what the inline rules this replaced
             matched on. Targets are selected by `pod` rather than by `job` because the `job`
             label a PodMonitor produces depends on operator configuration this chart does not
             control, while `namespace` and `pod` are always present on a PodMonitor target.
  namespace  every TeamSpeak in this namespace, which is what a single install per namespace
             wants and what the other charts here mean by scoping.
  none       no matcher at all. The placeholder is already a no-op, so the rules install as the
             committed files.
*/}}
{{- define "teamspeak.rules.scopePlaceholder" -}}
teamspeak_scope=~".*"
{{- end -}}

{{- define "teamspeak.rules.scopeMatcher" -}}
{{- if eq .Values.metrics.prometheusRule.scope "release" -}}
{{- printf "namespace=%q, pod=~%q" (include "common.namespace" .) (printf "%s-.*" (include "common.fullname" .)) -}}
{{- else if eq .Values.metrics.prometheusRule.scope "namespace" -}}
{{- printf "namespace=%q" (include "common.namespace" .) -}}
{{- end -}}
{{- end -}}

{{/*
The volume backing /var/ts3server: the SQLite database, the server keypair, uploaded files
and the logs.

Resolves to an existing claim, the claim this chart creates, or — when persistence is
disabled — an emptyDir, in which case every restart produces a different server: new unique
ID, new admin token, no channels and no permissions.
*/}}
{{- define "teamspeak.dataVolume" -}}
- name: data
{{- if not .Values.persistence.data.enabled }}
  emptyDir: {}
{{- else }}
  persistentVolumeClaim:
    claimName: {{ .Values.persistence.data.existingClaim | default (include "common.fullname" .) }}
{{- end }}
{{- end -}}

{{/*
All pod volumes this chart contributes.

`run` backs /var/run/ts3server, which the image's entrypoint writes ts3server.ini and
ts3db.ini into on every start. It sits outside the data volume, so a read-only root
filesystem needs it to be its own writable mount — without it the container dies before the
server binary is ever executed.
*/}}
{{- define "teamspeak.volumes" -}}
{{ include "teamspeak.dataVolume" . }}
{{- /* The writable runtime config directory; see the comment above. */}}
- name: run
  emptyDir:
    sizeLimit: 8Mi
{{- if .Values.license.existingSecret }}
- name: license
  secret:
    secretName: {{ tpl .Values.license.existingSecret . }}
    defaultMode: 0440
    items:
      - key: {{ .Values.license.existingSecretKey }}
        path: licensekey.dat
{{- end }}
{{- end -}}

{{/*
Volume mounts for the server container.
*/}}
{{- define "teamspeak.volumeMounts" -}}
- name: data
  mountPath: /var/ts3server
- name: run
  mountPath: /var/run/ts3server
{{- if .Values.license.existingSecret }}
- name: license
  mountPath: {{ include "teamspeak.licenseMountPath" . }}
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Container ports for the server.

The raw ServerQuery port is always declared: the probes and the metrics sidecar reach it over
the pod loopback interface. Whether anything outside the pod can is decided by the Service
and the NetworkPolicy, not here.
*/}}
{{- define "teamspeak.containerPorts" -}}
- name: voice
  containerPort: {{ int .Values.server.voicePort }}
  protocol: UDP
- name: filetransfer
  containerPort: {{ int .Values.server.fileTransferPort }}
  protocol: TCP
- name: query
  containerPort: {{ int .Values.server.queryPort }}
  protocol: TCP
{{- if include "teamspeak.sshQueryEnabled" . }}
- name: query-ssh
  containerPort: {{ int .Values.server.querySshPort }}
  protocol: TCP
{{- end }}
{{- end -}}

{{/*
Environment for the server container that cannot live in the ConfigMap: the license
acceptance flag and the two credentials, which are pulled from the Secret by reference so
they never appear in the ConfigMap or in `kubectl describe`.
*/}}
{{- define "teamspeak.env" -}}
- name: TS3SERVER_LICENSE
  value: accept
{{- if .Values.license.existingSecret }}
- name: TS3SERVER_LICENSEPATH
  value: {{ printf "%s/" (include "teamspeak.licenseMountPath" .) | quote }}
{{- end }}
{{- if include "teamspeak.hasAdminPassword" . }}
- name: TS3SERVER_SERVERADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "common.secretName" . }}
      key: {{ include "teamspeak.adminPasswordKey" . }}
{{- end }}
{{- if and (include "teamspeak.usesExternalDatabase" .) (include "teamspeak.hasDatabasePassword" .) }}
- name: TS3SERVER_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "common.secretName" . }}
      key: {{ include "teamspeak.databasePasswordKey" . }}
{{- end }}
{{- end -}}

{{/*
The Prometheus exporter sidecar, rendered as a YAML list item.

Written out here rather than through `common.container` on purpose: that helper wires the
chart's root-level probes, resources and extraEnv into whatever it builds, all of which
belong to the server container. The sidecar gets its own, and its own resource requests, so
the pod keeps a Burstable QoS class and satisfies the repository's kube-linter baseline.

It connects over 127.0.0.1, which the server's default query_ip_allowlist.txt exempts from
both the IP allowlist and the flood limiter — so no ServerQuery port has to be exposed, and
no credentials cross the network.
*/}}
{{- define "teamspeak.exporterContainer" -}}
- name: metrics
  image: {{ include "common.image" (dict "ctx" . "image" .Values.metrics.image) | quote }}
  imagePullPolicy: {{ include "common.imagePullPolicy" (dict "ctx" . "image" .Values.metrics.image) }}
  {{- with (include "common.containerSecurityContext" .) }}
  securityContext:
    {{- . | nindent 4 }}
  {{- end }}
  args:
    - {{ printf "-listen=:%d" (int .Values.metrics.port) | quote }}
    - {{ printf "-remote=127.0.0.1:%d" (int .Values.server.queryPort) | quote }}
    - {{ printf "-user=%s" .Values.metrics.user | quote }}
    - {{ printf "-enablechannelmetrics=%t" .Values.metrics.channelMetrics | quote }}
    - {{ printf "-ignorefloodlimits=%t" .Values.metrics.ignoreFloodLimits | quote }}
  ports:
    - name: metrics
      containerPort: {{ int .Values.metrics.port }}
      protocol: TCP
  env:
    - name: SERVERQUERY_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ include "common.secretName" . }}
          key: {{ include "teamspeak.adminPasswordKey" . }}
  {{- /*
    Probed over TCP rather than by fetching /metrics: every scrape of that endpoint issues
    ServerQuery commands against the server, and a probe has no business adding that load.
  */}}
  livenessProbe:
    tcpSocket:
      port: metrics
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 5
  readinessProbe:
    tcpSocket:
      port: metrics
    periodSeconds: 15
    timeoutSeconds: 5
    failureThreshold: 3
  {{- with .Values.metrics.resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end -}}
