# helm/charts/demo-app

The demo workload's Kubernetes side, in the `demo` namespace: a small Python service
([`demo-app/`](../../../demo-app/)) that reads one object from its S3 bucket through Pod Identity
and serves `/healthz` and `/metrics` on :8080, plus a traffic generator (the same image) that
calls it. A `tier=app` release in `helm/helmfile.yaml.gotmpl`. Its AWS side (the IAM role and
policy, the bucket, the security group, and the `demo:demo-app` Pod Identity association) is
`terraform/demo-app/us-east-1`.

| Object | Notes |
|---|---|
| ServiceAccount `demo-app` | No Kubernetes token; Pod Identity binds it to `demo-app-role-<suffix>` |
| Deployment `demo-app` | Same pod security as the consoletrack chart (UID 10001, read-only root, no capabilities). Memory: 128Mi request, 256Mi limit |
| Service `demo-app` (:8080), ServiceMonitor | The app's own metrics: `demo_app_requests_total{code}`, `demo_app_s3_errors_total{error}`, `demo_app_allocated_bytes` |
| ServiceAccount, Deployment, Service, ServiceMonitor `demo-app-loadgen` (`loadgen.enabled`, default on) | The traffic generator (`DEMO_MODE=loadgen`): `GET http://demo-app.demo:8080/` at `loadgen.rps` (5) with a `loadgen.timeoutSeconds` (1s) timeout, exporting `demo_client_requests_total{result="ok\|http_error\|conn_error\|timeout"}`, scraped every 15s. No token, no Pod Identity; the faults never change it |

The image reads `PORT`, `AWS_REGION`, `DEMO_BUCKET`, `DEMO_OBJECT_KEY`, and, with the OOM fault,
`DEMO_ALLOCATE_MI`. An empty `DEMO_BUCKET` (kind) means no S3 read and no AWS calls.

## The user-impact alert

`DemoAppHighErrorRate` is measured on the client's side, from the traffic generator: an app that
is OOMKilled or never Ready serves nothing and exports nothing, so a rule on its own 5xx counter
would never fire. The rule ships in the consoletrack chart's alert rules with #16:

```text
DemoAppHighErrorRate:
  sum(rate(demo_client_requests_total{result!="ok"}[2m]))
    / sum(rate(demo_client_requests_total[2m])) > 0.05
  for: 1m, severity: critical
```

## Faults

Values toggles for the cluster-only baseline scenarios. Turn on at most one (the chart refuses
two); turning it off again is the fix.

| Value | Effect | Expected fix |
|---|---|---|
| `faults.oom` | Memory request and limit both drop to `oomMemoryLimit` (128Mi; normally 128Mi request, 256Mi limit) and the app allocates and holds `allocateMi` (128) before it listens: ~150 MiB used on kind, ~175 MiB on EKS (boto3 loaded), so it's OOMKilled on every start | Raise the memory limit to 256Mi (`set_memory_limit`, the `oom-memory-bump` policy: exactly 2x, under the 512Mi ceiling, ~80 MiB headroom) |
| `faults.badImage` | Image tag `does-not-exist`: ImagePullBackOff | Rollout undo |
| `faults.badProbe` | Liveness and readiness on a path the app doesn't serve: restarts, never Ready | Fix the probe path |

For example: `helmfile -l name=demo-app apply --set faults.oom=true` (a human, or the
`break-demo` skill on kind).
