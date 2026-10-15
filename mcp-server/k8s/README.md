# k8s-mcp

Read-only FastMCP server (streamable HTTP) the triage agent uses to see the cluster: workloads,
pods, logs, events, rollout history, Prometheus queries. Runs under its own ServiceAccount with
`get`/`list`/`watch` in allowlisted namespaces only. The tool list is in `product-spec.md` →
Triage agent and guardrails.

Filled by the agent loop workstream (no issue filed yet).
