# backend

FastAPI API and the triage worker (same image), Python 3.12, managed with `uv`.

- `app/api/` routers, `app/services/` state machines and the policy gate, `app/intake/` SQS
  consumer and CloudTrail parsing, `app/agents/` the triage agent loop, `app/db/` models and
  Alembic migrations.
- `tests/unit/`, `tests/integration/` (Testcontainers, needs Docker), `tests/contract/`
  (FastAPI schema vs `../openapi.yaml`).

Filled by #4 (backend + database). The API contract is [`../openapi.yaml`](../openapi.yaml).
