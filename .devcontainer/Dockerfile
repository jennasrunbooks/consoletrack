FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# Pre-create the directory under the non-root user so named volumes inherit ownership
USER vscode
RUN mkdir -p /home/vscode/.claude
