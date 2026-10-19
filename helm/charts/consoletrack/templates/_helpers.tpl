{{/* Labels on every object. */}}
{{- define "consoletrack.labels" -}}
app.kubernetes.io/part-of: consoletrack
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{/* Labels for one component: (dict "ctx" $ "name" "backend"). */}}
{{- define "consoletrack.componentLabels" -}}
{{- /* selectorLabels minus instance, which consoletrack.labels already sets: a key twice in one
mapping is invalid YAML that strict parsers reject. */}}
app.kubernetes.io/name: {{ .name }}
{{ include "consoletrack.labels" .ctx }}
{{- end }}

{{- define "consoletrack.selectorLabels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
{{- end }}

{{/* ghcr.io/jennasrunbooks/consoletrack-<image>:<tag>: (dict "ctx" $ "image" "backend"). */}}
{{- define "consoletrack.image" -}}
{{- $tag := .ctx.Values.image.tag | default .ctx.Chart.AppVersion -}}
{{ printf "%s/consoletrack-%s:%s" .ctx.Values.image.registry .image $tag }}
{{- end }}

{{/*
Pod-level security: non-root with a numeric UID (so the kubelet can verify runAsNonRoot), the
runtime's default seccomp profile. Pass the UID: (dict "uid" 10001).
*/}}
{{- define "consoletrack.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* Container-level security, the same for every container in this chart. */}}
{{- define "consoletrack.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: [ALL]
{{- end }}

{{/* The app's database URL, built from the CloudNativePG `<cluster>-app` Secret. */}}
{{- define "consoletrack.databaseEnv" -}}
- name: DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.name }}-app
      key: username
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.name }}-app
      key: password
# The operator's own `uri` key says postgresql://, which SQLAlchemy maps to psycopg2; the app
# ships psycopg 3. CloudNativePG's generated passwords are alphanumeric, so no URL escaping.
- name: DATABASE_URL
  value: {{ printf "postgresql+psycopg://$(DB_USER):$(DB_PASSWORD)@%s-rw:5432/%s" .Values.postgres.name .Values.postgres.database | quote }}
{{- end }}

{{/* Settings the backend and the worker share. */}}
{{- define "consoletrack.appEnv" -}}
- name: AWS_REGION
  value: {{ .Values.aws.region | quote }}
- name: INTAKE_MODE
  value: {{ .Values.app.intakeMode | quote }}
- name: TRIAGE_MODEL
  value: {{ .Values.app.triageModel | quote }}
- name: TRIAGE_MODEL_ESCALATION
  value: {{ .Values.app.triageModelEscalation | quote }}
- name: K8S_ALLOWED_NAMESPACES
  value: {{ join "," .Values.allowedNamespaces | quote }}
- name: REMEDIATION_POLICIES_FILE
  value: /etc/consoletrack/remediation/policies.yaml
{{- end }}

{{/* An env var from one key of an existing Secret: (dict "name" "X" "secret" "s" "key" "k"). */}}
{{- define "consoletrack.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
{{- end }}

{{/* Every writable path is an emptyDir: the root filesystem is read-only. */}}
{{- define "consoletrack.tmpVolume" -}}
- name: tmp
  emptyDir:
    sizeLimit: 64Mi
{{- end }}

{{- define "consoletrack.remediationVolume" -}}
- name: remediation
  configMap:
    name: remediation-policies
{{- end }}

{{/* HTTP liveness and readiness probes: (dict "port" "http" "live" "/healthz" "ready" "/readyz"). */}}
{{- define "consoletrack.httpProbes" -}}
livenessProbe:
  httpGet:
    path: {{ .live }}
    port: {{ .port }}
  periodSeconds: 20
  timeoutSeconds: 3
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: {{ .ready }}
    port: {{ .port }}
  periodSeconds: 10
  timeoutSeconds: 3
  failureThreshold: 3
startupProbe:
  httpGet:
    path: {{ .live }}
    port: {{ .port }}
  periodSeconds: 5
  failureThreshold: 24
{{- end }}

{{/* Fail the render on settings that would deploy something broken or exposed. */}}
{{- define "consoletrack.validate" -}}
{{- if not (has .Values.target (list "eks" "local")) }}
{{- fail (printf "target must be eks or local, not %q" .Values.target) }}
{{- end }}
{{- if not (has .Values.exposure (list "port-forward" "alb")) }}
{{- fail (printf "exposure must be port-forward or alb, not %q" .Values.exposure) }}
{{- end }}
{{- if eq .Values.exposure "alb" }}
{{- if not .Values.domain }}
{{- fail "exposure=alb requires domain: without a certificate the ALB would serve plain HTTP (.env.example → EXPOSURE)" }}
{{- end }}
{{- if not .Values.certificateArn }}
{{- fail "exposure=alb requires certificateArn (terraform/consoletrack/us-east-1 → acm.tf)" }}
{{- end }}
{{- if not .Values.allowedCidrs }}
{{- fail "exposure=alb requires allowedCidrs: an empty list would open the ALB to the internet" }}
{{- end }}
{{- if eq .Values.target "local" }}
{{- fail "exposure=alb needs the Load Balancer Controller, which the local (kind) target doesn't run" }}
{{- end }}
{{- end }}
{{- if eq .Values.target "eks" }}
{{- range $key := list "nameSuffix" "clusterName" "changesQueueUrl" "changesDlqUrl" "trailName" "canarySecurityGroupId" }}
{{- if not (index $.Values.aws $key) }}
{{- fail (printf "aws.%s is required on EKS (Terraform outputs → helm/environments/values.generated.yaml)" $key) }}
{{- end }}
{{- end }}
{{- end }}
{{- if and (eq .Values.target "local") (eq .Values.app.intakeMode "live") }}
{{- fail "target=local has no AWS: set app.intakeMode=replay" }}
{{- end }}
{{- end }}
