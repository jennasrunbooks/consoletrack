# app/agents

The triage agent loop (#10). It only reads: its tools are the MCP servers' read-only tools plus
`submit_analysis`, and it moves incidents and changes only through `app/services/triage.py`,
`app/services/changes.py`, and the two state machines. Proposals stop at the policy gate
(`app/services/policy_gate.py`).

| File | What's there |
| --- | --- |
| `schema.py` | The `submit_analysis` tool (sent with `strict: true`, so the API enforces its required fields) and its Pydantic model (`SCHEMA_VERSION`), which still checks the bounds strict mode can't express, and the three proposal kinds |
| `prompts/` | Versioned prompt sets (`PROMPT_VERSION`, now `v4`; recorded on every `agent_runs` row): `system_vN.md`, `incident_triage_vN.md`, `change_analysis_vN.md` |
| `model.py` | `ModelClient` protocol, `AnthropicClient`, and the transient/permanent error split |
| `tools.py` | `ToolProvider`, the MCP client (`McpToolProvider`), `Toolbox`, `open_toolbox` (k8s-mcp required, aws-mcp optional) |
| `runner.py` | The loop: limits, evidence rows, the `agent_runs` row |
| `redact.py` | The backend's redaction pass (same rules as k8s-mcp's) |
| `triage.py` | Incident triage end to end: claim, run, at most one escalation, record, gate |
| `changes.py` | Change analysis for the intake (#9): `ChangeAnalysis.analyze(change_id)` |
| `smoke.py` | `make triage-smoke`: one real request with the real prompt, k8s-mcp's tool definitions, and `TRIAGE_MODEL`; passes when `submit_analysis` comes back and validates |

Tests use `tests/unit/agent_fakes.py`: a scripted `FakeModelClient` and in-process k8s-mcp and
aws-mcp fakes. No test calls the Anthropic API or needs `ANTHROPIC_API_KEY`; `make triage-smoke`
is the one manual check against the real API (one Haiku-class request).

Run the worker locally against kind with Compose: `COMPOSE_PROFILES=kind docker compose up`
with `ANTHROPIC_API_KEY` exported (see `docker-compose.yaml` → `worker`). On kind (Compose and the
`local` Helmfile environment) `AWS_MCP_URL` is empty until aws-mcp ships with #9.
