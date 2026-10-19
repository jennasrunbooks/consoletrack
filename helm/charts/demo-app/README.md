# helm/charts/demo-app

The demo workload's Kubernetes side, in the `demo` namespace: a small Python service that
reads one object from its S3 bucket through Pod Identity and serves `/healthz` and `/metrics`
on :8080. A `tier=app` release in `helm/helmfile.yaml.gotmpl`. Its AWS side (the IAM role and
policy, the bucket, the security group, and the `demo:demo-app` Pod Identity association) is
`terraform/demo-app/us-east-1`.

| Object | Notes |
|---|---|
| ServiceAccount `demo-app` | No Kubernetes token; Pod Identity binds it to `demo-app-role-<suffix>` |
| Deployment `demo-app` | Same pod security as the consoletrack chart (UID 10001, read-only root, no capabilities) |
| Service `demo-app` (:8080), ServiceMonitor | Request and error metrics for `DemoAppHighErrorRate` (the rule ships with the observability issue) |

The image reads `PORT`, `AWS_REGION`, `DEMO_BUCKET`, `DEMO_OBJECT_KEY`, and, with the OOM fault,
`DEMO_ALLOCATE_MI`. An empty `DEMO_BUCKET` (kind) means no S3 read.

## Faults

Values toggles for the cluster-only baseline scenarios. Turn on at most one (the chart refuses
two); turning it off again is the fix.

| Value | Effect | Expected fix |
|---|---|---|
| `faults.oom` | Memory request and limit drop to `oomMemoryLimit` (48Mi) and the app allocates `allocateMi` (96) at startup: OOMKilled | Raise the memory limit (the `oom-memory-bump` policy) |
| `faults.badImage` | Image tag `does-not-exist`: ImagePullBackOff | Rollout undo |
| `faults.badProbe` | Liveness and readiness on a path the app doesn't serve: restarts, never Ready | Fix the probe path |

For example: `helmfile -l name=demo-app apply --set faults.oom=true` (a human, or the
`break-demo` skill on kind).
