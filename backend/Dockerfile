# ConsoleTrack backend API and worker (one image): the locked uv environment, run as an
# unprivileged user. Build for the Graviton nodes with
# `docker buildx build --platform linux/arm64 backend/`; Compose builds it for the local machine.
# Bases are pinned by version and multi-arch index digest; bump both together.

FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
COPY --from=uv /uv /usr/local/bin/uv
# The image's own Python, never a downloaded one; compiled bytecode, so the runtime user needn't
# write any.
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --locked fails if uv.lock is stale; dev tools (pytest, ruff, testcontainers) stay out.
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
# /run/consoletrack holds Compose's generated local secrets (app/compose_entrypoint.py); a named
# volume mounted there starts with this ownership.
RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /nonexistent consoletrack \
    && install -d -o 10001 -g 10001 -m 0700 /run/consoletrack
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY alembic.ini ./
COPY app ./app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
