"""Entry point: `python -m aws_mcp` (container) or stdio for Claude Code against a sandbox."""

from __future__ import annotations

import sys

from aws_mcp import telemetry
from aws_mcp.aws import AwsReader
from aws_mcp.server import build_server
from aws_mcp.settings import Mode, Settings, Transport

FIXTURES_UNSUPPORTED = (
    "aws-mcp: AWS_MCP_MODE=fixtures isn't implemented yet; set AWS_MCP_MODE=live, or keep "
    "awsMcpEnabled false for the local environment (the worker runs without AWS tools)."
)


def main() -> None:
    settings = Settings()
    if settings.aws_mcp_mode is Mode.fixtures:
        sys.exit(FIXTURES_UNSUPPORTED)
    # stdio: stdout is the MCP protocol, so the logs go to stderr.
    stdio = settings.aws_mcp_transport is Transport.stdio
    telemetry.setup(settings.telemetry_exporter, stream=sys.stderr if stdio else sys.stdout)
    mcp = build_server(settings, AwsReader(settings))
    if stdio:
        mcp.run(transport="stdio", show_banner=False)
    else:
        # Stateless: each request stands alone, so any replica can serve any call.
        mcp.run(
            transport="http",
            host=settings.aws_mcp_host,
            port=settings.aws_mcp_port,
            path="/mcp",
            stateless_http=True,
            show_banner=False,
        )


if __name__ == "__main__":
    main()
