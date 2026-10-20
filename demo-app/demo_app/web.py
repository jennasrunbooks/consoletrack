"""The small HTTP layer both modes share: a threading server with a route table, `/metrics`
from a per-instance registry, and no per-request access log (5 requests a second would drown the
logs the triage agent reads)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

log = logging.getLogger(__name__)

# A route gets the request headers and returns (status, content type, body).
Response = tuple[int, str, bytes]
Route = Callable[[Mapping[str, str]], Response]

TEXT = "text/plain; charset=utf-8"


def text(status: int, body: str) -> Response:
    return status, TEXT, body.encode()


def make_server(
    port: int, routes: dict[str, Route], registry: CollectorRegistry, host: str = "0.0.0.0"
) -> ThreadingHTTPServer:
    routes = {
        "/healthz": lambda _: text(200, "ok\n"),
        "/metrics": lambda _: (200, CONTENT_TYPE_LATEST, generate_latest(registry)),
        **routes,
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "demo-app"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's naming)
            route = routes.get(self.path.split("?", 1)[0])
            if route is None:
                status, ctype, body = text(404, "not found\n")
            else:
                try:
                    status, ctype, body = route(self.headers)
                except Exception:
                    log.exception("unhandled error serving %s", self.path)
                    status, ctype, body = text(500, "internal error\n")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
