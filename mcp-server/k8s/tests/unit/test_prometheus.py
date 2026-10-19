from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from k8s_mcp.prometheus import HttpPrometheus, PrometheusError

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def prometheus(handler) -> HttpPrometheus:
    return HttpPrometheus("http://prom:9090/", 5.0, transport=httpx.MockTransport(handler))


def test_instant_query_sends_query_time_and_timeout() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector"}})

    assert prometheus(handler).query("up", NOW) == {"resultType": "vector"}
    (request,) = seen
    assert request.url.path == "/api/v1/query"
    assert request.url.params["query"] == "up"
    assert request.url.params["timeout"] == "5.0s"
    assert float(request.url.params["time"]) == NOW.timestamp()


def test_range_query() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix"}})

    prometheus(handler).query_range("up", NOW - timedelta(minutes=5), NOW, 15)
    assert seen[0].url.path == "/api/v1/query_range"
    assert seen[0].url.params["step"] == "15"


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (
            lambda r: httpx.Response(
                400, json={"status": "error", "errorType": "bad_data", "error": "parse error"}
            ),
            "rejected the query: bad_data: parse error",
        ),
        (lambda r: httpx.Response(502, text="<html>bad gateway</html>"), "HTTP 502"),
    ],
)
def test_errors(handler, message: str) -> None:
    with pytest.raises(PrometheusError, match=message):
        prometheus(handler).query("up", NOW)


def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(PrometheusError, match="timed out after 5.0s"):
        prometheus(handler).query("up", NOW)


def test_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(PrometheusError, match="unreachable: ConnectError"):
        prometheus(handler).query("up", NOW)
