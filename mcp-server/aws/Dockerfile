# aws-mcp: read-only AWS MCP server, streamable HTTP on :8080 (/mcp, /healthz, /readyz).
# Build for the Graviton nodes with `docker buildx build --platform linux/arm64 mcp-server/aws/`.
# Bases are pinned by version and multi-arch index digest; bump both together.

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Runtime dependencies only, exactly as locked.
RUN uv sync --locked --no-dev --no-install-project
COPY aws_mcp ./aws_mcp

FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
# Unprivileged, numeric so runAsNonRoot can verify it. No Kubernetes token; AWS credentials come
# from Pod Identity's projected token. Nothing in the image needs to write.
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)" || exit 1
CMD ["python", "-m", "aws_mcp"]
