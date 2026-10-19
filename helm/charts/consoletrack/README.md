# helm/charts/consoletrack

Chart for ConsoleTrack itself, in the `consoletrack` namespace. A `tier=app` release in
`helm/helmfile.yaml.gotmpl`, applied by `make deploy` or CI (`helmfile -l tier=app apply`),
never by Terraform. Every object is namespaced, so CI's namespaced deploy role can apply it.

## What it creates

| Component | Kind | ServiceAccount | Kubernetes token | AWS (Pod Identity) |
|---|---|---|---|---|
| `backend` (API, :8000) | Deployment + Service, `alembic upgrade head` init container | `remediator` | yes: approved patches | none |
| `worker` (SQS consumer + agent loop) | Deployment | `worker` | no | `ct-worker-<region>-<suffix>` |
| `frontend` (nginx, :8080) | Deployment + Service | `frontend` | no | none |
| `k8s-mcp` (:8080) | Deployment + Service | `k8s-mcp` | yes: read-only | none |
| `aws-mcp` (:8080) | Deployment + Service | `aws-mcp` | no | `ct-aws-mcp-<region>-<suffix>` |
| `canary` (every 15 min, EKS only) | CronJob | `canary` | no | `ct-canary-<region>-<suffix>` |
| Postgres | CloudNativePG `Cluster` `consoletrack-db` (1 instance, 5 Gi gp3) | operator-managed | | |

Plus:

- **RBAC** (`templates/rbac-*.yaml`), one Role per namespace and nothing cluster-scoped:
  `k8s-mcp-read` (get/list/watch on pods, pods/log, events, deployments, replicasets,
  statefulsets) and `remediator` (get/patch deployments, get/list replicasets) in each of
  `allowedNamespaces`; `consoletrack-deployer` for the `consoletrack-deployers` group (CI's
  deploy role) in `consoletrack` and `demo`.
- **Exposure.** `port-forward` (default): no Gateway; the frontend's nginx also proxies `/api`
  to the backend, and `make open` port-forwards the `frontend` Service. `alb`: a Gateway on the
  `alb` GatewayClass with a `LoadBalancerConfiguration` (internet-facing, `sourceRanges` =
  `allowedCidrs`, the ACM certificate on HTTPS:443), an HTTP→HTTPS redirect route, and one route
  sending `/api` to the backend and `/` to the frontend, with IP targets. `/metrics` is never
  routed.
- **Observability** (`templates/monitoring.yaml`): a ServiceMonitor for the backend's
  `/metrics`; the `ConsoleTrackCanaryMissing` rule (EKS only); an AlertmanagerConfig sending
  alerts from `consoletrack` and the allowlisted namespaces to `/api/v1/alerts/webhook` with the
  bearer token from `consoletrack-webhook`; and the "Intake health" dashboard
  (`files/intake-health.json`, uid `consoletrack-intake`) for the Grafana sidecar.
- **Remediation policies**: the `remediation:` block from `values.yaml` as the
  `remediation-policies` ConfigMap, mounted into the backend and worker. Every policy is
  `manual` and `autoEnabled` is false; graduating one is a reviewed PR.

## Pod security

Every container: requests and limits, `runAsNonRoot` with a numeric UID, the `RuntimeDefault`
seccomp profile, `allowPrivilegeEscalation: false`, a read-only root filesystem (an emptyDir at
`/tmp` for anything that writes), and `drop: [ALL]`. Long-running containers have liveness and
readiness probes; the canary Job is bounded by `activeDeadlineSeconds` instead.
`helm/tests/test_rendered.py` enforces all of it.

## What the images must provide

The images come from their own issues; this chart sets these names, and the image owners match
them (or change both in one PR).

| Image | Command and port | Probes | Environment |
|---|---|---|---|
| `consoletrack-backend` | image default (`uvicorn … :8000`); `alembic upgrade head` in the init container | `GET /healthz`, `GET /readyz` | `DATABASE_URL`, `AWS_REGION`, `INTAKE_MODE`, `TRIAGE_MODEL(_ESCALATION)`, `GRAFANA_URL`, `K8S_ALLOWED_NAMESPACES`, `REMEDIATION_POLICIES_FILE`, `ADMIN_PASSWORD_HASH`, `VIEWER_PASSWORD_HASH`, `SESSION_SECRET`, `ALERT_WEBHOOK_TOKEN` |
| `consoletrack-backend` as the worker | `python -m app.intake.consumer` | touches `WORKER_HEARTBEAT_FILE` after every poll; unhealthy when it's older than 120 s | the shared settings above plus `CHANGES_QUEUE_URL`, `CHANGES_DLQ_URL`, `TRAIL_NAME`, `K8S_MCP_URL`, `AWS_MCP_URL`, `ANTHROPIC_API_KEY`, `WORKER_HEARTBEAT_FILE` |
| `consoletrack-frontend` | image default (nginx :8080) | `GET /healthz` | none |
| `consoletrack-k8s-mcp` | image default (:8080, `/mcp`) | `GET /healthz`, `GET /readyz` | `K8S_MCP_TRANSPORT=http`, `K8S_MCP_PORT`, `K8S_ALLOWED_NAMESPACES`, `PROMETHEUS_URL` |
| `consoletrack-aws-mcp` | image default (:8080, `/mcp`) | `GET /healthz`, `GET /readyz` | `AWS_MCP_MODE` (`live`/`fixtures`), `AWS_MCP_PORT`, `AWS_REGION`, `WATCHED_IAM_PATHS`, `EKS_CLUSTER_NAME`, `TF_STATE_BUCKET`, `TF_STATE_KEY` |

All run as UID 10001 except the frontend (nginx-unprivileged, UID 101), with nothing written
outside `/tmp`. `DATABASE_URL` is built from the CloudNativePG Secret as
`postgresql+psycopg://…@consoletrack-db-rw:5432/consoletrack`.

## Values

The deployment-specific values come from the Helmfile environment
(`helm/environments/README.md`); `values.yaml` documents each one. On `eks` the chart fails to
render without the Terraform outputs it needs, and with `exposure: alb` without `domain`,
`certificateArn`, and `allowedCidrs`.
