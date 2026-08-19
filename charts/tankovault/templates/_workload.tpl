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
The Argo CD sync wave a resource takes, or nothing at all when the release is not being ordered
by waves.

Empty is the load-bearing case: it is what every caller tests to decide whether to emit the
annotation, so `helm install` consumers and `ordering: helmHook` consumers keep rendering
exactly the manifests they rendered before this knob existed. Only `mode` resolving to `job`
qualifies — an `initContainer` migration is ordered by the pod it lives in and has no Job to
sequence against.

Args: ctx (root), offset (added to `argoSyncWaveBase`; 0 for the Job, 1 for the workloads).
*/}}
{{- define "tankovault.migrateSyncWave" -}}
{{- $migrate := .ctx.Values.bootstrap.migrate -}}
{{- if and (eq (include "tankovault.migrateMode" .ctx) "job") (eq $migrate.ordering "argoSyncWave") -}}
{{- add $migrate.argoSyncWaveBase .offset -}}
{{- end -}}
{{- end -}}

{{/*
Container ports for one service: the request-facing listener, plus the Prometheus scrape on
its own isolated port. The scrape is a separate listener in the application, not a path on the
main one, so it has to be a separate container port too.

Under `internal.identity=mtls` a service that serves the mutually-authenticated listener gets a
third: `/health` and `/ready` in plaintext, because a kubelet probe presents no client certificate
and would otherwise be answered with a TLS alert. It carries the two probes and nothing else — the
scrape stays on `metrics`, so a deployment that merged the scrape onto the authenticated port
keeps it there. Deliberately not published on the Service: the kubelet reaches the pod directly,
and a credential-free listener needs no second door.
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
{{- if include "tankovault.servesInternalTls" (dict "ctx" .ctx "service" .service) }}
- name: probes
  containerPort: {{ .ctx.Values.internal.tls.probePort }}
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

In `initContainer` migrate mode the migration is attached only to the pods of services that
use the database. Those are exactly the pods that carry `database__url`: a pod projects only
its own `secretKeys`, so on `frontend` — which has none — the shared bootstrap volumeMounts
would name a `secrets` volume the pod does not define and the API server rejects the
Deployment outright, while on `render` and `challenge-solver` the volume exists but holds no
database URL and the migration could only fail. Migrating from the pods that actually need the
schema also keeps the ordering guarantee intact.
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
  {{- /*
    One wave above the migration Job, so Argo CD holds the rollout until the Job reports
    Complete. That is the whole of what `pre-upgrade` used to buy, recovered inside the Sync
    phase — see `bootstrap.migrate.ordering` for why the hook itself cannot stay.
  */}}
  {{- $syncWave := include "tankovault.migrateSyncWave" (dict "ctx" $root "offset" 1) }}
  {{- if or (include "common.annotations" $ctx) $syncWave }}
  annotations:
    {{- with (include "common.annotations" $ctx) }}
    {{- . | nindent 4 }}
    {{- end }}
    {{- with $syncWave }}
    "argocd.argoproj.io/sync-wave": {{ . | quote }}
    {{- end }}
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
      {{- /*
        Not a preference, and not a hardening nicety: a precondition of the configuration
        contracts this chart declares in `config-contract.yaml`.

        Kubernetes injects `<SERVICE_NAME>_SERVICE_HOST`, `<SERVICE_NAME>_PORT` and five more per
        Service in the namespace, and this chart creates one Service per component — so a release
        named `tankovault` puts dozens of variables inside the `TANKOVAULT_` namespace the loader
        owns. The environment layer outranks the mounted file, so one of them can *supply* a key
        a service's `config.toml` already set, and the deployment silently runs on the wrong value.

        The kubelet injects these at pod admission and `helm template` does not, so a rendered
        manifest never carries one and no gate here could catch the regression —
        `just check-config` can only require the switch.
      */}}
      enableServiceLinks: false
      {{- if and (eq (include "tankovault.migrateMode" $root) "initContainer") $spec.needsDatabase }}
      initContainers:
        {{- include "tankovault.bootstrapContainer" (dict "ctx" $root "command" "migrate" "name" "migrate") | nindent 8 }}
      {{- end }}
      containers:
        {{- include "common.container" (dict
              "ctx" $ctx
              "name" $spec.slug
              "ports" (include "tankovault.containerPorts" (dict "ctx" $root "service" $service) | fromYamlArray)
              "env" (include "tankovault.env" (dict
                    "ctx" $root
                    "secrets" (include "tankovault.hasSecrets" (dict "ctx" $root "service" $service))
                  ) | fromYamlArray)
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
