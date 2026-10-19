{{- define "demo-app.labels" -}}
{{ include "demo-app.selectorLabels" . }}
{{ include "demo-app.commonLabels" . }}
{{- end }}

{{- define "demo-app.commonLabels" -}}
app.kubernetes.io/part-of: demo-app
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "demo-app.selectorLabels" -}}
app.kubernetes.io/name: demo-app
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- /* A different app.kubernetes.io/name, so the demo-app Service never selects these pods. */}}
{{- define "demo-app.loadgenSelectorLabels" -}}
app.kubernetes.io/name: demo-app-loadgen
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "demo-app.loadgenLabels" -}}
{{ include "demo-app.loadgenSelectorLabels" . }}
app.kubernetes.io/component: loadgen
{{ include "demo-app.commonLabels" . }}
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
