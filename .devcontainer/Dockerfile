FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# Pre-create the directory under the non-root user so named volumes inherit ownership
USER vscode
RUN mkdir -p /home/vscode/.claude

ARG HELMFILE_VERSION=1.8.2
ARG HELMFILE_SHA256_ARM64=sha256:9163f0d7ad088d243d7305ba09c3465aac02dfebd658d46c62dbdaa11c224b16
RUN curl -fsSLo /tmp/helmfile.tgz \
      "https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/helmfile_${HELMFILE_VERSION}_linux_arm64.tar.gz" \
 && echo "${HELMFILE_SHA256_ARM64}  /tmp/helmfile.tgz" | sha256sum -c - \
 && tar -xzf /tmp/helmfile.tgz -C /usr/local/bin helmfile \
 && rm /tmp/helmfile.tgz
