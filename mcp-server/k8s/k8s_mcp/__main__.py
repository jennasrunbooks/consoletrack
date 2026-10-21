"""Entry point: `python -m k8s_mcp` (container) or through `.mcp.json` (Claude Code, stdio)."""

from __future__ import annotations

import sys

from k8s_mcp import telemetry
from k8s_mcp.kube import KubernetesReader
from k8s_mcp.prometheus import HttpPrometheus
from k8s_mcp.server import build_server
from k8s_mcp.settings import Settings, Transport


def main() -> None:
    settings = Settings()
    # stdio: stdout is the MCP protocol, so the logs go to stderr.
    stdio = settings.k8s_mcp_transport is Transport.stdio
    telemetry.setup(settings.telemetry_exporter, stream=sys.stderr if stdio else sys.stdout)
    prometheus = (
        HttpPrometheus(settings.prometheus_url, settings.prometheus_timeout_seconds)
        if settings.prometheus_url
        else None
    )
    mcp = build_server(settings, KubernetesReader(settings.k8s_context), prometheus)
    if settings.k8s_mcp_transport is Transport.stdio:
        mcp.run(transport="stdio", show_banner=False)
    else:
        # Stateless: each request stands alone, so any replica can serve any call.
        mcp.run(
            transport="http",
            host=settings.k8s_mcp_host,
            port=settings.k8s_mcp_port,
            path="/mcp",
            stateless_http=True,
            show_banner=False,
        )


if __name__ == "__main__":
    main()
