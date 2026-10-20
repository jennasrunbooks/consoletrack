from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import get, serving
from prometheus_client import CollectorRegistry

from demo_app import loadgen
from demo_app.config import Config

TIMEOUT = 0.3


class Fake(BaseHTTPRequestHandler):
    """/ok → 200, /fail → 500, /slow → 200 after the client gave up, /hangup → closes early."""

    release = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/hangup":
            self.close_connection = True
            return
        if self.path == "/slow":
            Fake.release.wait(TIMEOUT * 5)
        status = 500 if self.path == "/fail" else 200
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture(scope="module")
def fake() -> str:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    httpd.daemon_threads = True
    with serving(httpd) as url:
        yield url
        Fake.release.set()


@pytest.mark.parametrize(
    ("path", "result"),
    [("/ok", "ok"), ("/fail", "http_error"), ("/slow", "timeout"), ("/hangup", "conn_error")],
)
def test_result_labels(fake: str, path: str, result: str) -> None:
    assert loadgen.call(f"{fake}{path}", TIMEOUT) == result


def test_connection_refused(closed_port: int) -> None:
    assert loadgen.call(f"http://127.0.0.1:{closed_port}/", TIMEOUT) == "conn_error"


def test_unresolvable_host_is_conn_error() -> None:
    # What the client sees if the Service is gone (.invalid never resolves).
    assert loadgen.call("http://demo-app.invalid:8080/", TIMEOUT) == "conn_error"


def metrics_of(gen: loadgen.Loadgen) -> dict[str, float]:
    out = {}
    for metric in gen.registry.collect():
        for sample in metric.samples:
            if sample.name == "demo_client_requests_total":
                out[sample.labels["result"]] = sample.value
    return out


def test_every_result_series_exists_before_any_request() -> None:
    gen = loadgen.Loadgen(Config(mode="loadgen"), CollectorRegistry())
    assert metrics_of(gen) == dict.fromkeys(loadgen.RESULTS, 0.0)


def test_counts_by_result(fake: str, closed_port: int) -> None:
    gen = loadgen.Loadgen(Config(mode="loadgen"), CollectorRegistry())
    for target in (f"{fake}/ok", f"{fake}/ok", f"{fake}/fail", f"http://127.0.0.1:{closed_port}/"):
        gen.config = Config(mode="loadgen", target=target, timeout_seconds=TIMEOUT)
        gen.once()
    assert metrics_of(gen) == {"ok": 2.0, "http_error": 1.0, "conn_error": 1.0, "timeout": 0.0}


def test_metrics_endpoint(fake: str) -> None:
    gen, httpd = loadgen.build(
        Config(mode="loadgen", port=0, target=f"{fake}/fail", timeout_seconds=TIMEOUT),
        host="127.0.0.1",
    )
    gen.once()
    with serving(httpd) as url:
        assert get(f"{url}/healthz") == (200, "ok\n")
        _, metrics = get(f"{url}/metrics")
    assert 'demo_client_requests_total{result="http_error"} 1.0' in metrics
    assert 'demo_client_requests_total{result="ok"} 0.0' in metrics


def test_run_keeps_a_steady_rate_against_a_slow_target(fake: str) -> None:
    # 20 rps against a target that never answers in time: requests still start on schedule.
    gen = loadgen.Loadgen(
        Config(mode="loadgen", target=f"{fake}/slow", rps=20, timeout_seconds=TIMEOUT),
        CollectorRegistry(),
    )
    runner = threading.Thread(target=gen.run)
    runner.start()
    time.sleep(1.0)
    gen.stop()
    runner.join(timeout=5)
    assert not runner.is_alive()
    counts = metrics_of(gen)
    assert counts["ok"] == 0
    assert 15 <= counts["timeout"] <= 25
