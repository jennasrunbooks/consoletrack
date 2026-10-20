# demo-app

The workload the scenarios break, and the traffic generator that notices: one Python 3.12 image
(`ghcr.io/jennasrunbooks/consoletrack-demo-app`), managed with `uv`, standard library HTTP and
`prometheus-client`, plus `boto3` for the one S3 read. Deployed by
[`helm/charts/demo-app`](../helm/charts/demo-app/); its AWS side is `terraform/demo-app/us-east-1`.

| `DEMO_MODE` | Serves on `PORT` (8080) | Settings |
| --- | --- | --- |
| `server` (default) | `GET /` (the work), `/healthz`, `/metrics` | `DEMO_ALLOCATE_MI`, `DEMO_ALLOCATE_DELAY_SECONDS` (3), `DEMO_BUCKET`, `DEMO_OBJECT_KEY`, `AWS_REGION` |
| `loadgen` | `/healthz`, `/metrics` | `LOADGEN_TARGET` (`http://demo-app.demo:8080/`), `LOADGEN_RPS` (5), `LOADGEN_TIMEOUT_SECONDS` (1) |

**Server.** `DEMO_ALLOCATE_MI` allocates that many MiB at startup, writes to every page so it is
resident, and holds it, before the server listens: that makes the OOM fault deterministic. It
waits `DEMO_ALLOCATE_DELAY_SECONDS` (3) first, so the kill comes a few seconds after start. The
reason Kubernetes reports isn't reliable either way: on kind it has read `Error` (exit 137)
before settling to `OOMKilled`, so the triage prompt treats exit 137 soon after start as a
likely OOM. With
`DEMO_BUCKET` set, every `GET /` reads `s3://$DEMO_BUCKET/$DEMO_OBJECT_KEY` through the SDK's
default credential chain (Pod Identity on EKS) and answers 503 with the error code
(`AccessDenied`, `NoCredentialsError`) when it can't, logging it for the triage agent; `/healthz`
stays 200, because the fault is the identity, not the pod. With `DEMO_BUCKET` empty (kind), boto3
is never imported, so there are no AWS calls. Metrics: `demo_app_requests_total{code}`,
`demo_app_s3_errors_total{error}`, `demo_app_allocated_bytes`.

**Traffic generator.** Starts a `GET` every `1/LOADGEN_RPS` seconds whatever the previous ones
are doing, and counts what it saw in `demo_client_requests_total{result}`: `ok` (2xx),
`http_error` (any other status), `conn_error` (refused, reset, no such host), or `timeout`. All
four series exist from the start. `DemoAppHighErrorRate` is built on this, because an app that is
OOMKilled or never Ready exports nothing of its own.

**Memory.** About 25 MiB without boto3 and 50 MiB with the S3 client loaded, on top of whatever
`DEMO_ALLOCATE_MI` holds. The chart's OOM fault (128 MiB held under a 128Mi limit, fixed by 256Mi)
is sized from these numbers; see the chart README.

## Commands

From the repo root:

| Task | Command |
| --- | --- |
| Lint (ruff check + format check) | `make lint-demo-app` (part of `make lint`) |
| Unit tests (loopback fakes, no AWS) | `make test-demo-app` (part of `make test`) |
| Image for this machine, tagged `:local` | `make demo-app-image` (needs Docker) |
| Into kind | `make local` loads it when a `consoletrack` kind cluster exists |
| arm64, for EKS | `docker buildx build --platform linux/arm64 demo-app/` (the deploy pipeline pushes it) |

From `demo-app/`: `uv sync`, then `uv run pytest`, `uv run python -m demo_app`, or
`DEMO_MODE=loadgen LOADGEN_TARGET=http://127.0.0.1:8080/ PORT=8081 uv run python -m demo_app`.
