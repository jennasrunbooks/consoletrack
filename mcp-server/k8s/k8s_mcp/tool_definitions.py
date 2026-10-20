"""Print the tools as the triage agent receives them, as JSON, without a cluster.

`python -m k8s_mcp.tool_definitions` lists the tools through an in-memory MCP client, the same
`tools/list` answer the worker gets over HTTP, and prints `[{name, description, input_schema}]`.
`make triage-smoke` sends these to the real Messages API, so a schema the API rejects shows up
before a real run. Needs `K8S_ALLOWED_NAMESPACES` like the server; touches no cluster.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, NoReturn

from fastmcp import Client

from k8s_mcp.server import build_server
from k8s_mcp.settings import Settings


class _NoCluster:
    """Listing tools never reads the cluster; any call means that changed."""

    def __getattr__(self, name: str) -> NoReturn:
        raise RuntimeError(f"tool_definitions must not call the Kubernetes API ({name})")


async def definitions(settings: Settings) -> list[dict[str, Any]]:
    server = build_server(settings, _NoCluster(), None)  # type: ignore[arg-type]
    async with Client(server) as client:
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in await client.list_tools()
        ]


def main() -> None:
    print(json.dumps(asyncio.run(definitions(Settings())), indent=2))


if __name__ == "__main__":
    main()
