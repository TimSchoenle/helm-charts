{{/*
How `bootstrap migrate` actually runs, resolving `auto`.

`job` is a `pre-install,pre-upgrade` hook, which is the right shape when the database already
exists. It cannot work with the bundled PostgreSQL, though: Helm runs pre-install hooks before
any of the release's own resources, so the StatefulSet the migration would connect to does not
exist yet and the hook could only ever time out. `initContainer` has no such ordering problem
and upstream sanctions it explicitly; concurrent runs are safe because sqlx takes a Postgres
advisory lock for the duration.
*/}}
{{- define "tankovault.migrateMode" -}}
{{- $mode := .Values.bootstrap.migrate.mode -}}
{{- if eq $mode "auto" -}}
{{- ternary "initContainer" "job" .Values.postgresql.enabled -}}
{{- else -}}
{{- $mode -}}
{{- end -}}
{{- end -}}

{{/*
Container ports for one service: the request-facing listener, plus the Prometheus scrape on
its own isolated port. The scrape is a separate listener in the application, not a path on the
main one, so it has to be a separate container port too.
*/}}
{{- define "tankovault.containerPorts" -}}
{{- $spec := include "tankovault.spec" .service | fromYaml -}}
- name: http
  containerPort: {{ $spec.port }}
  protocol: TCP
{{- if .ctx.Values.metrics.enabled }}
- name: metrics
  containerPort: {{ .ctx.Values.metrics.port }}
  protocol: TCP
{{- end }}
{{- end -}}

{{/*
Volumes that exist only for the `render` tier.

Every other service ships on `scratch` — the image is the binary, the musl loader, libgcc_s
and a CA bundle — so a read-only root filesystem costs it nothing beyond the `/tmp` emptyDir
`common.volumeMounts` already provides. `render` is a Debian base driving a real Chromium,
which writes a profile and a crashpad database under `$HOME` and needs far more shared memory
than the 64Mi Kubernetes gives `/dev/shm` by default. A root-owned or missing `$HOME` mount
here surfaces as `chrome_crashpad_handler: --database is required`, not as a permission error.
*/}}
{{- define "tankovault.renderVolumes" -}}
{{- $render := .Values.services.render -}}
- name: dshm
  emptyDir:
    medium: Memory
    sizeLimit: {{ $render.shmSize }}
- name: home
  emptyDir: {}
{{- end -}}

{{- define "tankovault.renderVolumeMounts" -}}
{{- $render := .Values.services.render -}}
- name: dshm
  mountPath: /dev/shm
- name: home
  mountPath: {{ $render.homeDir | quote }}
{{- end -}}

{{/*
Pod annotations.

By default this is just `.Values.defaults.podAnnotations` — deliberately WITHOUT the
`checksum/config` annotations every other chart in this repository uses. Those exist to force
a rollout when configuration changes, and here a rollout is the wrong answer: each service
watches the directories its configuration came from and rebuilds its runtime in place when the
kubelet refreshes the mounted ConfigMap or Secret. Annotating the checksum would throw away
that property and restart the fleet on every value change.

`configReload.rolloutOnChange` restores the conventional behaviour for operators who would
rather have config changes look like an ordinary image bump.
*/}}
{{- define "tankovault.podAnnotations" -}}
{{- $ctx := .ctx -}}
{{- $annotations := $ctx.Values.podAnnotations | default dict -}}
{{- if .root.Values.configReload.rolloutOnChange -}}
{{- $annotations = merge (deepCopy $annotations) (dict
      "checksum/config" (include "tankovault.configToml" (dict "ctx" .root "service" .service) | sha256sum)
      "checksum/secret" (include "tankovault.secretData" .root | sha256sum)) -}}
{{- end -}}
{{- with $annotations }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
The Deployment for one service.

Builds the scoped render context described in `tankovault.serviceValues` and then hands it to
the `common` library unchanged, so probes, resources, security contexts, image resolution,
affinity and the pod spec all come from the same helpers every other chart in this repository
uses — just resolved per service instead of per chart.
*/}}
{{- define "tankovault.deployment" -}}
{{- $root := .ctx -}}
{{- $service := .service -}}
{{- $spec := include "tankovault.spec" $service | fromYaml -}}
{{- $values := include "tankovault.serviceValues" (dict "ctx" $root "service" $service) | fromYaml -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
{{- $volumes := include "tankovault.volumes" (dict "ctx" $root "service" $service) | fromYamlArray -}}
{{- $mounts := include "tankovault.volumeMounts" (dict "ctx" $root "service" $service) | fromYamlArray -}}
{{- if eq $service "render" -}}
{{- $volumes = concat $volumes (include "tankovault.renderVolumes" $root | fromYamlArray) -}}
{{- $mounts = concat $mounts (include "tankovault.renderVolumeMounts" $root | fromYamlArray) -}}
{{- end -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "common.fullname" $ctx }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- with (include "common.annotations" $ctx) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  {{- if not (and $values.autoscaling $values.autoscaling.enabled) }}
  replicas: {{ $values.replicaCount }}
  {{- end }}
  revisionHistoryLimit: {{ $values.revisionHistoryLimit }}
  selector:
    matchLabels:
      {{- include "common.selectorLabels" $ctx | nindent 6 }}
  {{- with (include "common.updateStrategy" (dict "ctx" $ctx)) }}
  strategy:
    {{- . | nindent 4 }}
  {{- end }}
  template:
    metadata:
      labels:
        {{- include "common.podLabels" $ctx | nindent 8 }}
      {{- with (include "tankovault.podAnnotations" (dict "ctx" $ctx "root" $root "service" $service)) }}
      annotations:
        {{- . | nindent 8 }}
      {{- end }}
    spec:
      {{- include "common.podSpec.common" $ctx | nindent 6 }}
      {{- if eq (include "tankovault.migrateMode" $root) "initContainer" }}
      initContainers:
        {{- include "tankovault.bootstrapContainer" (dict "ctx" $root "command" "migrate" "name" "migrate") | nindent 8 }}
      {{- end }}
      containers:
        {{- include "common.container" (dict
              "ctx" $ctx
              "name" $spec.slug
              "ports" (include "tankovault.containerPorts" (dict "ctx" $root "service" $service) | fromYamlArray)
              "env" (include "tankovault.env" (dict "ctx" $root) | fromYamlArray)
              "volumeMounts" $mounts
            ) | nindent 8 }}
      {{- with (include "common.volumes" (dict "ctx" $ctx "volumes" $volumes)) }}
      volumes:
        {{- . | nindent 8 }}
      {{- end }}
{{- end -}}

{{/*
The Service for one service. Always exposes the application port, and the metrics port too
when metrics are on, so a single ServiceMonitor per service can select it.
*/}}
{{- define "tankovault.service" -}}
{{- $root := .ctx -}}
{{- $service := .service -}}
{{- $spec := include "tankovault.spec" $service | fromYaml -}}
{{- $values := include "tankovault.serviceValues" (dict "ctx" $root "service" $service) | fromYaml -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "common.fullname" $ctx }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- $annotations := merge (deepCopy ($values.service.annotations | default dict)) ($root.Values.commonAnnotations | default dict) }}
  {{- with $annotations }}
  annotations:
    {{- include "common.tplvalues.render" (dict "value" . "context" $ctx) | nindent 4 }}
  {{- end }}
spec:
  type: {{ $values.service.type }}
  ports:
    - name: http
      port: {{ $spec.port }}
      targetPort: http
      protocol: TCP
    {{- if $root.Values.metrics.enabled }}
    - name: metrics
      port: {{ $root.Values.metrics.port }}
      targetPort: metrics
      protocol: TCP
    {{- end }}
  selector:
    {{- include "common.selectorLabels" $ctx | nindent 4 }}
{{- end -}}

{{/*
The HorizontalPodAutoscaler for one service, when it declares one.
*/}}
{{- define "tankovault.hpa" -}}
{{- $root := .ctx -}}
{{- $service := .service -}}
{{- $values := include "tankovault.serviceValues" (dict "ctx" $root "service" $service) | fromYaml -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
{{- $autoscaling := $values.autoscaling -}}
apiVersion: {{ include "common.capabilities.hpa.apiVersion" $ctx }}
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "common.fullname" $ctx }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- with (include "common.annotations" $ctx) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "common.fullname" $ctx }}
  minReplicas: {{ $autoscaling.minReplicas }}
  maxReplicas: {{ $autoscaling.maxReplicas }}
  metrics:
    {{- with $autoscaling.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ . }}
    {{- end }}
    {{- with $autoscaling.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ . }}
    {{- end }}
{{- end -}}

{{/*
The PodDisruptionBudget for one service, when it declares one.
*/}}
{{- define "tankovault.pdb" -}}
{{- $root := .ctx -}}
{{- $service := .service -}}
{{- $values := include "tankovault.serviceValues" (dict "ctx" $root "service" $service) | fromYaml -}}
{{- $ctx := dict "Values" $values "Chart" $root.Chart "Release" $root.Release "Capabilities" $root.Capabilities "Template" $root.Template "Files" $root.Files -}}
{{- $pdb := $values.podDisruptionBudget -}}
apiVersion: {{ include "common.capabilities.pdb.apiVersion" $ctx }}
kind: PodDisruptionBudget
metadata:
  name: {{ include "common.fullname" $ctx }}
  namespace: {{ include "common.namespace" $ctx }}
  labels:
    {{- include "common.labels" $ctx | nindent 4 }}
  {{- with (include "common.annotations" $ctx) }}
  annotations:
    {{- . | nindent 4 }}
  {{- end }}
spec:
  {{- if $pdb.maxUnavailable }}
  maxUnavailable: {{ $pdb.maxUnavailable }}
  {{- else }}
  minAvailable: {{ $pdb.minAvailable | default 1 }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "common.selectorLabels" $ctx | nindent 6 }}
{{- end -}}
