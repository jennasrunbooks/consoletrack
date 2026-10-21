# backend

FastAPI API and the triage worker (same image), Python 3.12, managed with `uv`. The API contract
is [`../openapi.yaml`](../openapi.yaml); the layering and both state machines are in
[`../docs/architecture.md`](../docs/architecture.md).

| Path | What's there |
| --- | --- |
| `app/main.py` | App factory: `uvicorn --factory app.main:create_app` |
| `app/worker.py` | The worker process: `python -m app.worker` (the triage loop; the intake joins with #9) |
| `app/settings.py` | Every setting, from the environment variables in `../.env.example` |
| `app/api/` | Routers (HTTP only), auth dependencies, `Error` mapping |
| `app/services/` | Change, incident, and remediation state machines, Accept, Alertmanager processing, the triage queue, the policy gate and its policies, the remediation validator and apply loop, intake health |
| `app/db/` | SQLAlchemy models, repository protocols and their SQL versions, Alembic migrations |
| `app/schemas.py` | API models matching `openapi.yaml` |
| `app/compose_entrypoint.py` | Docker Compose only: generated local secrets, `alembic upgrade head`, uvicorn |
| `Dockerfile` | The API and worker image (non-root, locked uv environment); arm64 on EKS |
| `app/agents/` | The triage agent loop: see [`app/agents/README.md`](app/agents/README.md) |
| `app/intake/` | Filled by #9 (intake worker) |
| `tests/unit/` | Fast tests on in-memory repositories (no database), the agent on a scripted model |
| `tests/contract/` | `app.openapi()` vs `../openapi.yaml` |
| `tests/integration/` | Real Postgres (Testcontainers), marker `integration` |

## Commands

From the repo root:

| Task | Command |
| --- | --- |
| Lint (ruff check + format check) | `make lint-backend` (part of `make lint`) |
| Unit tests, with `app/services/` coverage ≥ 90% | `make test-backend` (part of `make test`) |
| Contract test | `make test-contract` (part of `make test`) |
| Integration tests | `make test-integration` (needs Docker, or `CT_TEST_DATABASE_URL`) |
| Schema as SQL, no database | `make db-sql` |
| One real Messages API request with the triage agent's prompt, tools, and `TRIAGE_MODEL` (needs `ANTHROPIC_API_KEY`) | `make triage-smoke` |
| Format | `make fmt` |

From `backend/`: `uv sync`, then `uv run pytest -m "not integration"`, `uv run alembic upgrade head`
(needs `DATABASE_URL`), and `uv run uvicorn --factory app.main:create_app --reload`.

Integration tests start a `postgres:16-alpine` container through Testcontainers. To use a
Postgres you already have, point `CT_TEST_DATABASE_URL` at an empty, disposable database (the
tests truncate every table and downgrade to base). The tests refuse any URL that isn't a loopback
host (`localhost`, `127.0.0.1`, `::1`) with `test` in the database name, e.g.
`CT_TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/ct_test make test-integration`.
