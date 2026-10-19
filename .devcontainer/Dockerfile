FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# Headless Chromium for Playwright browser tests. Debian's build, so it's patched through apt and
# doesn't depend on Node, which the features install after this Dockerfile runs.
# The base image's Yarn apt repo has an outdated signing key; we don't use Yarn, so drop it.
RUN grep -rl 'dl.yarnpkg.com' /etc/apt/sources.list.d/ | xargs -r rm -f \
 && apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-liberation \
 && rm -rf /var/lib/apt/lists/*
ENV CHROMIUM_PATH=/usr/bin/chromium

# Pre-create the directory under the non-root user so named volumes inherit ownership
USER vscode
RUN mkdir -p /home/vscode/.claude

# helm-diff from its release tarball, checksum-pinned. Helm 4 can't verify plugins installed from
# a git URL, and the tarball already contains the binary, so no install hook runs.
ARG HELM_DIFF_VERSION=3.15.15
ARG HELM_DIFF_SHA256_ARM64=fc046bf3c3f63a435741fe8c37a9d740dfa17ea7f5f0c2a9da2c0222bb4384e6
RUN mkdir -p /home/vscode/.local/share/helm/plugins/helm-diff \
 && curl -fsSLo /tmp/helm-diff.tgz \
      "https://github.com/databus23/helm-diff/releases/download/v${HELM_DIFF_VERSION}/helm-diff-linux-arm64.tgz" \
 && echo "${HELM_DIFF_SHA256_ARM64}  /tmp/helm-diff.tgz" | sha256sum -c - \
 && tar -xzf /tmp/helm-diff.tgz -C /home/vscode/.local/share/helm/plugins/helm-diff --strip-components=1 \
 && rm /tmp/helm-diff.tgz