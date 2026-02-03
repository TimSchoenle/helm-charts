{{/*
Expand the name of the chart.
*/}}
{{- define "common.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "common.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "common.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "common.labels" -}}
helm.sh/chart: {{ include "common.chart" . }}
{{ include "common.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "common.selectorLabels" -}}
app.kubernetes.io/name: {{ include "common.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "common.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "common.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the appropriate apiVersion for PodDisruptionBudget
*/}}
{{- define "common.capabilities.pdb.apiVersion" -}}
{{- if semverCompare ">=1.21-0" .Capabilities.KubeVersion.GitVersion -}}
policy/v1
{{- else -}}
policy/v1beta1
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for HorizontalPodAutoscaler
*/}}
{{- define "common.capabilities.hpa.apiVersion" -}}
{{- if semverCompare ">=1.23-0" .Capabilities.KubeVersion.GitVersion -}}
autoscaling/v2
{{- else -}}
autoscaling/v2beta2
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for Ingress
*/}}
{{- define "common.capabilities.ingress.apiVersion" -}}
{{- if semverCompare ">=1.19-0" .Capabilities.KubeVersion.GitVersion -}}
networking.k8s.io/v1
{{- else if semverCompare ">=1.14-0" .Capabilities.KubeVersion.GitVersion -}}
networking.k8s.io/v1beta1
{{- else -}}
extensions/v1beta1
{{- end -}}
{{- end -}}

{{/*
Return the appropriate apiVersion for NetworkPolicy
*/}}
{{- define "common.capabilities.networkPolicy.apiVersion" -}}
{{- if semverCompare ">=1.7-0" .Capabilities.KubeVersion.GitVersion -}}
networking.k8s.io/v1
{{- else -}}
extensions/v1beta1
{{- end -}}
{{- end -}}

{{/*
Create a default container image name
*/}}
{{- define "common.image" -}}
{{- $registry := .Values.image.registry | default "" -}}
{{- $repository := .Values.image.repository | required "image.repository is required" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion | toString -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
{{- end -}}

{{/*
Create a default image pull policy
*/}}
{{- define "common.imagePullPolicy" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion | toString -}}
{{- if .Values.image.pullPolicy -}}
{{- .Values.image.pullPolicy -}}
{{- else if hasSuffix "latest" $tag -}}
Always
{{- else -}}
IfNotPresent
{{- end -}}
{{- end -}}

{{/*
Create a default secret name
*/}}
{{- define "common.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- include "common.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
Create a default configmap name
*/}}
{{- define "common.configMapName" -}}
{{- if .Values.existingConfigMap -}}
{{- .Values.existingConfigMap -}}
{{- else -}}
{{- include "common.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
Return true if a configmap should be created
*/}}
{{- define "common.createConfigMap" -}}
{{- if not .Values.existingConfigMap -}}
true
{{- end -}}
{{- end -}}

{{/*
Return true if a secret should be created
*/}}
{{- define "common.createSecret" -}}
{{- if not .Values.existingSecret -}}
true
{{- end -}}
{{- end -}}

{{/*
Validate required values
*/}}
{{- define "common.validateValues" -}}
{{- $messages := list -}}
{{- range $key, $value := . -}}
  {{- if not $value -}}
    {{- $messages = append $messages (printf "%s is required" $key) -}}
  {{- end -}}
{{- end -}}
{{- if $messages -}}
  {{- fail (join ", " $messages) -}}
{{- end -}}
{{- end -}}

{{/*
Compile all validation warnings into a single message and call fail.
*/}}
{{- define "common.validateValues.error" -}}
{{- $messages := list -}}
{{- $messages := append $messages (include "common.validateValues" .) -}}
{{- $message := join "\n" $messages -}}
{{- if $message -}}
{{-   printf "\nVALUES VALIDATION:\n%s" $message | fail -}}
{{- end -}}
{{- end -}}

{{/*
Return podAnnotations with checksum/config annotation
*/}}
{{- define "common.podAnnotations" -}}
{{- with .Values.podAnnotations }}
{{ toYaml . }}
{{- end }}
{{- if .Values.enableConfigChecksum }}
checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- end }}
{{- if .Values.enableSecretChecksum }}
checksum/secret: {{ include (print $.Template.BasePath "/secret.yaml") . | sha256sum }}
{{- end }}
{{- end }}

{{/*
Return the proper Service port
*/}}
{{- define "common.servicePort" -}}
{{- .Values.service.port | default 80 -}}
{{- end -}}

{{/*
Return the proper container port
*/}}
{{- define "common.containerPort" -}}
{{- .Values.containerPort | default 8080 -}}
{{- end -}}

{{/*
Merge default environment variables with custom ones
*/}}
{{- define "common.env" -}}
{{- $defaultEnv := .defaultEnv | default list -}}
{{- $customEnv := .customEnv | default list -}}
{{- $envMap := dict -}}
{{- range $defaultEnv -}}
  {{- $_ := set $envMap .name . -}}
{{- end -}}
{{- range $customEnv -}}
  {{- $_ := set $envMap .name . -}}
{{- end -}}
{{- values $envMap | toYaml -}}
{{- end -}}

{{/*
Return the appropriate securityContext
*/}}
{{- define "common.securityContext" -}}
{{- if .Values.securityContext -}}
{{ toYaml .Values.securityContext }}
{{- else -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
{{- end -}}
{{- end -}}

{{/*
Return the appropriate podSecurityContext
*/}}
{{- define "common.podSecurityContext" -}}
{{- if .Values.podSecurityContext -}}
{{ toYaml .Values.podSecurityContext }}
{{- else -}}
runAsNonRoot: true
runAsUser: 10001
fsGroup: 10001
{{- end -}}
{{- end -}}

{{/*
Return the appropriate resources
*/}}
{{- define "common.resources" -}}
{{- if .Values.resources -}}
{{ toYaml .Values.resources }}
{{- else -}}
limits:
  cpu: 100m
  memory: 128Mi
requests:
  cpu: 10m
  memory: 64Mi
{{- end -}}
{{- end -}}

{{/*
Return the appropriate probe configuration
*/}}
{{- define "common.probe" -}}
{{- $probe := . -}}
{{- if $probe.enabled -}}
{{- if $probe.httpGet }}
httpGet:
{{ toYaml $probe.httpGet | indent 2 }}
{{- else if $probe.tcpSocket }}
tcpSocket:
{{ toYaml $probe.tcpSocket | indent 2 }}
{{- else if $probe.exec }}
exec:
{{ toYaml $probe.exec | indent 2 }}
{{- end }}
initialDelaySeconds: {{ $probe.initialDelaySeconds | default 0 }}
periodSeconds: {{ $probe.periodSeconds | default 10 }}
timeoutSeconds: {{ $probe.timeoutSeconds | default 5 }}
successThreshold: {{ $probe.successThreshold | default 1 }}
failureThreshold: {{ $probe.failureThreshold | default 3 }}
{{- end -}}
{{- end -}}

{{/*
Create priorityClassName if specified
*/}}
{{- define "common.priorityClassName" -}}
{{- if .Values.priorityClassName }}
priorityClassName: {{ .Values.priorityClassName }}
{{- end }}
{{- end }}

{{/*
Create topology spread constraints
*/}}
{{- define "common.topologySpreadConstraints" -}}
{{- if .Values.topologySpreadConstraints }}
topologySpreadConstraints:
{{ toYaml .Values.topologySpreadConstraints | indent 2 }}
{{- end }}
{{- end }}

{{/*
Create affinity rules
*/}}
{{- define "common.affinity" -}}
{{- if .Values.affinity }}
{{ toYaml .Values.affinity }}
{{- else if .Values.podAntiAffinity }}
podAntiAffinity:
  {{- if eq .Values.podAntiAffinity "hard" }}
  requiredDuringSchedulingIgnoredDuringExecution:
  - labelSelector:
      matchLabels:
        {{- include "common.selectorLabels" . | nindent 8 }}
    topologyKey: kubernetes.io/hostname
  {{- else if eq .Values.podAntiAffinity "soft" }}
  preferredDuringSchedulingIgnoredDuringExecution:
  - weight: 100
    podAffinityTerm:
      labelSelector:
        matchLabels:
          {{- include "common.selectorLabels" . | nindent 10 }}
      topologyKey: kubernetes.io/hostname
  {{- end }}
{{- end }}
{{- end }}

{{/*
Create NetworkPolicy
*/}}
{{/*
Create NetworkPolicy Ingress
*/}}
{{- define "common.networkPolicy.ingress" -}}
{{- if .Values.networkPolicy.enabled }}
kind: NetworkPolicy
apiVersion: {{ include "common.capabilities.networkPolicy.apiVersion" . }}
metadata:
  name: {{ include "common.fullname" . | trunc 54 | trimSuffix "-" }}-ingress
  labels:
    {{- include "common.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Ingress
  ingress:
    {{- if and .Values.networkPolicy.ingress .Values.networkPolicy.ingress.enabled }}
    {{- if and .Values.networkPolicy.ingress.monitoring .Values.networkPolicy.ingress.monitoring.enabled }}
    - from:
      - namespaceSelector:
          matchLabels:
            name: {{ .Values.networkPolicy.ingress.monitoring.namespace }}
    {{- end }}
    {{- if and .Values.networkPolicy.ingress.controller .Values.networkPolicy.ingress.controller.enabled }}
    - from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: {{ .Values.networkPolicy.ingress.controller.namespace }}
        podSelector:
          matchLabels:
            {{- toYaml .Values.networkPolicy.ingress.controller.selector | nindent 12 }}
    {{- end }}
    {{- range .Values.networkPolicy.ingress.customRules }}
    - {{ toYaml . | nindent 6 }}
    {{- end }}
    {{- if .Values.networkPolicy.extraIngress }}
    {{- toYaml .Values.networkPolicy.extraIngress | nindent 4 }}
    {{- end }}
    {{- end }}
{{- end }}
{{- end }}

{{/*
Create NetworkPolicy Egress
*/}}
{{- define "common.networkPolicy.egress" -}}
{{- if .Values.networkPolicy.enabled }}
kind: NetworkPolicy
apiVersion: {{ include "common.capabilities.networkPolicy.apiVersion" . }}
metadata:
  name: {{ include "common.fullname" . | trunc 55 | trimSuffix "-" }}-egress
  labels:
    {{- include "common.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "common.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Egress
  egress:
    {{- if and .Values.networkPolicy.egress .Values.networkPolicy.egress.enabled }}
    {{- if and .Values.networkPolicy.egress.dns .Values.networkPolicy.egress.dns.enabled }}
    - ports:
      - port: 53
        protocol: UDP
      - port: 53
        protocol: TCP
    {{- end }}
    {{- if and .Values.networkPolicy.egress.http .Values.networkPolicy.egress.http.enabled }}
    - ports:
      - port: 80
        protocol: TCP
    {{- end }}
    {{- if and .Values.networkPolicy.egress.https .Values.networkPolicy.egress.https.enabled }}
    - ports:
      - port: 443
        protocol: TCP
    {{- end }}
    {{- if and .Values.networkPolicy.egress.sentry .Values.networkPolicy.egress.sentry.enabled }}
    - ports:
      - port: 443
        protocol: TCP
    {{- end }}
    {{- range .Values.networkPolicy.egress.customRules }}
    - {{ toYaml . | nindent 6 }}
    {{- end }}
    {{- if .Values.networkPolicy.extraEgress }}
    {{- toYaml .Values.networkPolicy.extraEgress | nindent 4 }}
    {{- end }}
    {{- end }}
{{- end }}
{{- end }}

{{/*
Create NetworkPolicy Wrapper
*/}}
{{- define "common.networkPolicy" -}}
{{- if .Values.networkPolicy.enabled }}
{{- include "common.networkPolicy.ingress" . }}
---
{{- include "common.networkPolicy.egress" . }}
{{- end }}
{{- end }}
