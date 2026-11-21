{{- define "ef.name" -}}{{ .Release.Name }}{{- end -}}

{{- define "ef.labels" -}}
app.kubernetes.io/name: enterprise-fabric
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ef.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "ef.secretName" -}}
{{- if .Values.secrets.existingSecret -}}{{ .Values.secrets.existingSecret }}{{- else -}}{{ include "ef.name" . }}-secrets{{- end -}}
{{- end -}}

{{/* Pod security for every container: unprivileged, no escalation, no capabilities. */}}
{{- define "ef.podSecurity" -}}
runAsNonRoot: true
seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{- define "ef.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: ["ALL"]}
{{- end -}}

{{/* The environment shared by the API and the worker. */}}
{{- define "ef.pythonEnv" -}}
envFrom:
  - configMapRef: {name: {{ include "ef.name" . }}-config}
  - secretRef: {name: {{ include "ef.secretName" . }}}
{{- end -}}
