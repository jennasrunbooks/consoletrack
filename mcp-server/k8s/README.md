# k8s-mcp

Read-only FastMCP server the triage agent uses to see the cluster: workloads, pods, logs,
events, rollout history, Prometheus queries. Streamable HTTP on `:8080/mcp` in the cluster,
stdio when Claude Code starts it through the repo's `.mcp.json`. Spec: `product-spec.md` →
Triage agent and guardrails; permissions: [docs/permissions.md](../../docs/permissions.md#triage-agent-runtime).

## Tools

All six are reads (`readOnlyHint: true`). Each refuses a namespace outside
`K8S_ALLOWED_NAMESPACES` before calling the API.

| Tool | Returns | Limits |
| --- | --- | --- |
| `get_workload(namespace, kind, name)` | Image, resources, probes, replicas, conditions, env var **names**; `stateful: true` for StatefulSets | `kind`: Deployment, StatefulSet, ReplicaSet. Secret/ConfigMap refused. No env values, no annotations |
| `get_pods(namespace, selector)` | Phase, restarts, each container's state and last termination reason and exit code | 50 pods; container-written termination messages left out |
| `get_pod_logs(namespace, pod, container, previous, tail)` | Last lines in `untrusted_data`, redacted | `tail` clamped to 1–200, 2,000 chars per line, 256 KiB per request |
| `get_events(namespace, involved_object)` | Warning events, newest first, in `untrusted_data`, redacted | 50 events; `involved_object` is `name` or `Kind/name` |
| `get_rollout_history(namespace, deployment)` | The Deployment's ReplicaSets: revision, images, created, replicas | 10 revisions |
| `query_prometheus(promql, minutes)` | `minutes=0` instant query, else a range ending now | `PROMETHEUS_MAX_MINUTES` (60), `PROMETHEUS_TIMEOUT_SECONDS` (10), 20 series; namespace matchers must be `namespace="<allowlisted>"` |

`/healthz` is liveness; `/readyz` lists one pod in every allowlisted namespace, so it fails
if the API server is unreachable or the RBAC is missing.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `K8S_ALLOWED_NAMESPACES` | required | Comma-separated. Must match the namespaces with a Role in `rbac/k8s-mcp.yaml`; empty refuses to start |
| `K8S_CONTEXT` | current context | kubeconfig context; ignored in-cluster |
| `PROMETHEUS_URL` | unset | e.g. `http://kube-prometheus-stack-prometheus.monitoring:9090`; unset makes `query_prometheus` return an error |
| `PROMETHEUS_TIMEOUT_SECONDS` | `10` | Sent to Prometheus as `timeout`, and the client's own limit (+1 s) |
| `PROMETHEUS_MAX_MINUTES` | `60` | Longest range query |
| `K8S_MCP_TRANSPORT` | `http` | `http` or `stdio` |
| `K8S_MCP_HOST`, `K8S_MCP_PORT` | `0.0.0.0`, `8080` | HTTP listener |

## RBAC

`rbac/k8s-mcp.yaml` holds the ServiceAccount (`consoletrack/k8s-mcp`) and one Role +
RoleBinding for `demo`. #12 moves it into `helm/charts/consoletrack/templates/` and loops the
Role over the allowlist. It grants `get`/`list`/`watch` on pods, pods/log, events, deployments,
replicasets, statefulsets, and nothing else.

## Tests

```bash
make test-k8s-mcp   # unit tests, fake Kubernetes client, no cluster or network (part of make test)
make lint-k8s-mcp   # ruff (part of make lint)
make test-k8s       # kind cluster + real RBAC (needs Docker; runs in CI, not in the devcontainer)
```

`make test-k8s` (`scripts/test-k8s.sh`) creates a throwaway kind cluster with its own kubeconfig
file, applies `rbac/k8s-mcp.yaml` and `tests/kind/fixtures.yaml`, then checks with
`kubectl auth can-i` that the ServiceAccount is denied Secrets, ConfigMaps, exec, writes, and
other namespaces, and runs the tools with the ServiceAccount's own token. `KEEP_CLUSTER=true`
keeps the cluster for re-runs. Without `K8S_MCP_KIND_KUBECONFIG` the kind tests are collected
and skipped.

## Local use with Claude Code

The repo's `.mcp.json` starts k8s-mcp over stdio with `uv run`, against the kubeconfig context
`kind-consoletrack` and namespace `demo`. Override with `K8S_MCP_CONTEXT`,
`K8S_ALLOWED_NAMESPACES`, and `K8S_MCP_PROMETHEUS_URL` (e.g. `http://localhost:9090` through a
port-forward) in the shell that starts Claude Code. It needs a kubeconfig, so it works on the
host, not in the devcontainer (which has none by design).

With your own kind admin context, RBAC doesn't narrow anything; only the in-server checks
apply. To get the real ServiceAccount's permissions locally, apply `rbac/k8s-mcp.yaml` to kind
yourself and point `KUBECONFIG` at a file that uses its token
(`kubectl -n consoletrack create token k8s-mcp`).

## Image

```bash
docker buildx build --platform linux/arm64 -t ghcr.io/jennasrunbooks/consoletrack-k8s-mcp mcp-server/k8s/
```

Runs as uid 10001, `python -m k8s_mcp`, with a `HEALTHCHECK` on `/healthz`.
