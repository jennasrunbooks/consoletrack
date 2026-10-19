# ConsoleTrack dashboard: static files served by unprivileged nginx on :8080, with /healthz.
# Build for the Graviton nodes with `docker buildx build --platform linux/arm64 frontend/`.
# Bases are pinned by version and multi-arch index digest; bump both together.

# The build output is plain static files, so this stage runs on the builder's own platform
# (no emulation when cross-building for arm64).
FROM --platform=$BUILDPLATFORM node:24.22.0-alpine3.24@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
# src/api/schema.d.ts is committed (CI checks it matches openapi.yaml), so ../openapi.yaml
# isn't needed in this build context.
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24@sha256:782bb89768cc87b7f95c18b95baa1af250e5d451f26794f59363540161147c01
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
# The base image already runs as the unprivileged nginx user (uid 101); say so explicitly.
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
