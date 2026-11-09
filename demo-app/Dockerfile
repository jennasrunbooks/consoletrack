# The demo workload (DEMO_MODE=server, the default) and its traffic generator (DEMO_MODE=loadgen):
# one locked uv environment, run as an unprivileged user with nothing written at runtime. Build
# for the Graviton nodes with `docker buildx build --platform linux/arm64 demo-app/`;
# `make demo-app-image` builds it for the local machine. Bases are pinned by version and
# multi-arch index digest, the same as backend/Dockerfile; bump both together.

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
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

FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
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
