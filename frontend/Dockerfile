# ConsoleTrack dashboard: static files served by unprivileged nginx on :8080, with /healthz.
# Build for the Graviton nodes with `docker buildx build --platform linux/arm64 frontend/`.
# Bases are pinned by version and multi-arch index digest; bump both together.

# The build output is plain static files, so this stage runs on the builder's own platform
# (no emulation when cross-building for arm64).
FROM --platform=$BUILDPLATFORM node:26.11.1-alpine3.24@sha256:143494b1da2945f061539253adc65e4f1569ddf07da2d384c022c791a9d90a4a AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
# src/api/schema.d.ts is committed (CI checks it matches openapi.yaml), so ../openapi.yaml
# isn't needed in this build context.
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31.7-alpine3.24@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
# The base image already runs as the unprivileged nginx user (uid 101); say so explicitly.
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
