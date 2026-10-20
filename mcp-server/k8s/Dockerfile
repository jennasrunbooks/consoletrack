# k8s-mcp: read-only Kubernetes MCP server, streamable HTTP on :8080 (/mcp, /healthz, /readyz).
# Build for the Graviton nodes with `docker buildx build --platform linux/arm64 mcp-server/k8s/`.
# Bases are pinned by version and multi-arch index digest; bump both together.

FROM ghcr.io/astral-sh/uv:0.13.2@sha256:b9fddcde095a7aeafd499024adb8927df6619c7c5672f5ff7126474715f62ba4 AS uv

FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Runtime dependencies only, exactly as locked.
RUN uv sync --locked --no-dev --no-install-project
COPY k8s_mcp ./k8s_mcp

FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
WORKDIR /app
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
# Unprivileged, numeric so runAsNonRoot can verify it. The ServiceAccount token is mounted
# read-only by the kubelet; nothing in the image needs to write.
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)" || exit 1
CMD ["python", "-m", "k8s_mcp"]
