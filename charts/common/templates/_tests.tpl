{{/*
Common network connection test template
Accepts a list of checks in .Values.tests.connectivity or passed via context.
Context should be a dict: "ctx" (Root Context), "checks" (List of checks).
Check format:
  name: string (optional)
  target: string (host:port)
  expect: string ("success" or "failure", default "success")
  timeout: int (seconds, default 5)
*/}}
{{- define "common.test.start_pod" -}}
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "common.fullname" . }}-test-connection"
  labels:
    {{- include "common.labels" . | nindent 4 }}
    helm.sh/chart: "{{ .Chart.Name }}-{{ .Chart.Version }}"
  annotations:
    "helm.sh/hook": test
spec:
  containers:
    - name: connectivity-test
      image: busybox
      command: ['/bin/sh', '-c']
      args:
        - |
          echo "Starting connectivity tests..."
          FAILED=0
          {{- range .Values.tests.connectivity }}
          target="{{ .target }}"
          expect="{{ .expect | default "success" }}"
          timeout="{{ .timeout | default 5 }}"
          echo "Testing connection to $target (expecting $expect)..."
          
          if nc -z -w $timeout ${target%:*} ${target##*:}; then
            result="success"
          else
            result="failure"
          fi

          if [ "$result" = "$expect" ]; then
            echo "PASS: Connection to $target result: $result"
          else
            echo "FAIL: Connection to $target result: $result, expected: $expect"
            FAILED=1
          fi
          {{- end }}
          exit $FAILED
  restartPolicy: Never
{{- end -}}
