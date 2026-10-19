{{- define "demo-app.labels" -}}
{{ include "demo-app.selectorLabels" . }}
app.kubernetes.io/part-of: demo-app
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "demo-app.selectorLabels" -}}
app.kubernetes.io/name: demo-app
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "demo-app.validate" -}}
{{- $on := 0 }}
{{- range $fault := list "oom" "badImage" "badProbe" }}
{{- if index $.Values.faults $fault }}{{ $on = add1 $on }}{{ end }}
{{- end }}
{{- if gt $on 1 }}
{{- fail "turn on at most one of faults.oom, faults.badImage, faults.badProbe" }}
{{- end }}
{{- end }}
