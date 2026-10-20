# The demo workload (DEMO_MODE=server, the default) and its traffic generator (DEMO_MODE=loadgen):
# one locked uv environment, run as an unprivileged user with nothing written at runtime. Build
# for the Graviton nodes with `docker buildx build --platform linux/arm64 demo-app/`;
# `make demo-app-image` builds it for the local machine. Bases are pinned by version and
# multi-arch index digest, the same as backend/Dockerfile; bump both together.

FROM ghcr.io/astral-sh/uv:0.13.2@sha256:b9fddcde095a7aeafd499024adb8927df6619c7c5672f5ff7126474715f62ba4 AS uv

FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
COPY --from=uv /uv /usr/local/bin/uv
# The image's own Python, never a downloaded one; compiled bytecode, so the runtime user needn't
# write any (the root filesystem is read-only in the cluster).
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# --locked fails if uv.lock is stale; dev tools (pytest, ruff) stay out.
RUN uv sync --locked --no-dev --no-install-project
COPY demo_app ./demo_app
RUN python -m compileall -q demo_app

FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /nonexistent demo-app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/demo_app /app/demo_app
# No *_created series on /metrics: Prometheus doesn't need them for rate().
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PROMETHEUS_DISABLE_CREATED_SERIES=True
WORKDIR /app
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["python", "-m", "demo_app"]
