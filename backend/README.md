# backend

FastAPI API and the triage worker (same image), Python 3.12, managed with `uv`. The API contract
is [`../openapi.yaml`](../openapi.yaml); the layering and both state machines are in
[`../docs/architecture.md`](../docs/architecture.md).

| Path | What's there |
| --- | --- |
| `app/main.py` | App factory: `uvicorn --factory app.main:create_app` |
| `app/settings.py` | Every setting, from the environment variables in `../.env.example` |
| `app/api/` | Routers (HTTP only), auth dependencies, `Error` mapping |
| `app/services/` | Change and incident state machines, Accept, Alertmanager processing, intake health |
| `app/db/` | SQLAlchemy models, repository protocols and their SQL versions, Alembic migrations |
| `app/schemas.py` | API models matching `openapi.yaml` |
| `app/compose_entrypoint.py` | Docker Compose only: generated local secrets, `alembic upgrade head`, uvicorn |
| `Dockerfile` | The API and worker image (non-root, locked uv environment); arm64 on EKS |
| `app/intake/`, `app/agents/` | Filled by #9 (intake worker) and #10 (triage agent) |
| `tests/unit/` | Fast tests on in-memory repositories (no database) |
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
| Format | `make fmt` |

From `backend/`: `uv sync`, then `uv run pytest -m "not integration"`, `uv run alembic upgrade head`
(needs `DATABASE_URL`), and `uv run uvicorn --factory app.main:create_app --reload`.

Integration tests start a `postgres:16-alpine` container through Testcontainers. To use a
Postgres you already have, point `CT_TEST_DATABASE_URL` at an empty, disposable database (the
tests truncate every table and downgrade to base). The tests refuse any URL that isn't a loopback
host (`localhost`, `127.0.0.1`, `::1`) with `test` in the database name, e.g.
`CT_TEST_DATABASE_URL=postgresql+psycopg://user@localhost:5432/ct_test make test-integration`.
