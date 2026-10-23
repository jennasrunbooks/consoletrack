# demo-app

The workload the scenarios break, and the traffic generator that notices: one Python 3.12 image
(`ghcr.io/jennasrunbooks/consoletrack-demo-app`), managed with `uv`, standard library HTTP and
`prometheus-client`, plus `boto3` for the one S3 read. Deployed by
[`helm/charts/demo-app`](../helm/charts/demo-app/); its AWS side is `terraform/demo-app/us-east-1`.

| `DEMO_MODE` | Serves on `PORT` (8080) | Settings |
| --- | --- | --- |
| `server` (default) | `GET /` (the work), `/healthz`, `/metrics` | `DEMO_REPORT_MI` (2), `DEMO_REPORT_CACHE_MI` (140), `DEMO_BUCKET`, `DEMO_OBJECT_KEY`, `AWS_REGION` |
| `loadgen` | `/healthz`, `/metrics` | `LOADGEN_TARGET` (`http://demo-app.demo:8080/`), `LOADGEN_RPS` (5), `LOADGEN_TIMEOUT_SECONDS` (1), `LOADGEN_REPORT` (empty, or `large` for the OOM fault) |

**Server.** A `GET /` with `X-Demo-Report: large` builds a `DEMO_REPORT_MI` (2) MiB report,
writes to every page so it is resident, and keeps it in an in-memory cache; past
`DEMO_REPORT_CACHE_MI` (140) the oldest is evicted. The cache is bounded, but the bound plus the
app is more than a 128Mi limit: that is the OOM scenario, and only the traffic generator's
requests decide whether it happens. Nothing is logged about the cache. With `DEMO_BUCKET` set, every `GET /` reads `s3://$DEMO_BUCKET/$DEMO_OBJECT_KEY` through the SDK's
default credential chain (Pod Identity on EKS) and answers 503 with the error code
(`AccessDenied`, `NoCredentialsError`) when it can't, logging it for the triage agent; `/healthz`
stays 200, because the fault is the identity, not the pod. With `DEMO_BUCKET` empty (kind), boto3
is never imported, so there are no AWS calls. Metrics: `demo_app_requests_total{code}`,
`demo_app_s3_errors_total{error}`, `demo_app_report_cache_bytes`.

**Traffic generator.** Starts a `GET` every `1/LOADGEN_RPS` seconds whatever the previous ones
are doing (with `X-Demo-Report: large` when `LOADGEN_REPORT=large`), and counts what it saw in `demo_client_requests_total{result}`: `ok` (2xx),
`http_error` (any other status), `conn_error` (refused, reset, no such host), or `timeout`. All
four series exist from the start. `DemoAppHighErrorRate` is built on this, because an app that is
OOMKilled or never Ready exports nothing of its own.

**Memory.** Measured under the OOM fault (`make measure`, `make local-measure`, #79): 18 MiB
without boto3 (kind) and 73 MiB with the S3 client loaded (EKS, arm64), plus the report cache,
and flat once the cache is full. The cache defaults to 140 MiB, kind's sizing. The chart sets it
per cluster (96 MiB on EKS), so the fault peaks at about 160-170 MiB on both clusters: over a
128Mi limit, well under the 256Mi fix. See the chart README.

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
