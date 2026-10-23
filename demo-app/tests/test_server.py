from __future__ import annotations

import sys
import threading
import urllib.request
from pathlib import Path

import pytest
from conftest import get, serving

from demo_app import server
from demo_app.config import Config

MIB = server.MIB
STATUS = Path("/proc/self/status")


def rss_bytes() -> int:
    for line in STATUS.read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise AssertionError("no VmRSS")


def test_allocate_touches_every_page() -> None:
    buf = server.allocate(2)
    assert len(buf) == 2 * MIB
    assert all(buf[i] != 0 for i in range(0, len(buf), server.PAGE))


@pytest.mark.skipif(not STATUS.exists(), reason="needs Linux /proc")
def test_allocate_is_resident() -> None:
    # Reserved-but-untouched memory wouldn't count against the cgroup limit; this must.
    before = rss_bytes()
    buf = server.allocate(32)
    assert rss_bytes() - before >= 30 * MIB
    del buf


def get_with(url: str, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as resp:
        return resp.status


def cache_bytes(metrics: str) -> float:
    (line,) = [m for m in metrics.splitlines() if m.startswith("demo_app_report_cache_bytes ")]
    return float(line.split()[1])


def test_large_reports_are_cached() -> None:
    app, httpd = server.build(Config(port=0, report_mi=1, report_cache_mi=8), host="127.0.0.1")
    with serving(httpd) as url:
        for _ in range(3):
            assert get_with(f"{url}/", {"X-Demo-Report": "large"}) == 200
        _, metrics = get(f"{url}/metrics")
    assert len(app.cache.reports) == 3
    assert cache_bytes(metrics) == 3 * MIB


def test_the_cache_is_bounded() -> None:
    # Bounded, so a large enough limit holds it: 256Mi is the scenario's fix.
    app, httpd = server.build(Config(port=0, report_mi=2, report_cache_mi=7), host="127.0.0.1")
    httpd.server_close()
    for _ in range(10):
        app.work({"X-Demo-Report": "large"})
    assert len(app.cache.reports) == 3  # 7 // 2
    assert app.registry.get_sample_value("demo_app_report_cache_bytes") == 6 * MIB


def test_a_full_cache_reuses_its_buffers() -> None:
    """Full, the cache writes each new report into the oldest buffer: no allocation or free per
    request, from any request thread, so memory stays at the cache (#79)."""
    app, httpd = server.build(Config(port=0, report_mi=1, report_cache_mi=4), host="127.0.0.1")
    httpd.server_close()
    for _ in range(4):
        app.work({"X-Demo-Report": "large"})
    buffers = {id(r) for r in app.cache.reports}
    first = app.cache.reports[0]
    before = bytes(first[0 : server.PAGE * 2 : server.PAGE])

    threads = [
        threading.Thread(target=app.work, args=({"X-Demo-Report": "large"},)) for _ in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert {id(r) for r in app.cache.reports} == buffers
    assert len(app.cache.reports) == 4
    # The oldest buffer was rewritten (a new report), not just moved.
    assert bytes(first[0 : server.PAGE * 2 : server.PAGE]) != before
    assert all(first[i] != 0 for i in range(0, len(first), server.PAGE))
    assert app.registry.get_sample_value("demo_app_report_cache_bytes") == 4 * MIB


@pytest.mark.parametrize("headers", [{}, {"X-Demo-Report": "small"}, {"X-Other": "large"}])
def test_other_requests_cache_nothing(headers: dict[str, str]) -> None:
    app, httpd = server.build(Config(port=0), host="127.0.0.1")
    httpd.server_close()
    assert app.work(headers)[0] == 200
    assert len(app.cache.reports) == 0


def test_default_sizing_outgrows_128mi() -> None:
    # The app's own default is kind's sizing (no boto3: 18 MiB measured, #79): a full cache
    # passes a 128Mi limit and fits in 256Mi with more than 64 MiB to spare. The chart sets
    # DEMO_REPORT_CACHE_MI per cluster (96 on EKS, where the app is 73 MiB).
    cfg = Config()
    assert (cfg.report_mi, cfg.report_cache_mi) == (2, 140)
    assert 18 + cfg.report_cache_mi > 128
    assert 256 - (18 + cfg.report_cache_mi) >= 64


def test_no_cache_means_no_allocation() -> None:
    app, httpd = server.build(Config(port=0, report_cache_mi=0), host="127.0.0.1")
    httpd.server_close()
    app.work({"X-Demo-Report": "large"})
    assert len(app.cache.reports) == 0


def test_serves_without_aws_when_bucket_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry makes any `import boto3` raise: proof that no AWS client is ever built.
    monkeypatch.setitem(sys.modules, "boto3", None)
    _, httpd = server.build(Config(port=0, bucket=""), host="127.0.0.1")
    with serving(httpd) as url:
        assert get(f"{url}/") == (200, "ok\n")
        assert get(f"{url}/healthz") == (200, "ok\n")
        _, metrics = get(f"{url}/metrics")
    assert 'demo_app_requests_total{code="200"} 1.0' in metrics


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class FakeClientError(Exception):
    """Shaped like botocore's ClientError: the service's code is in .response."""

    def __init__(self, code: str) -> None:
        super().__init__(f"An error occurred ({code}) when calling the GetObject operation")
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def get_object(self, **kwargs: str) -> dict:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"Body": FakeBody(b"seed data\n")}


def test_s3_read_ok() -> None:
    s3 = FakeS3()
    reader = server.s3_reader("demo-app-data-use1-test", "seed.txt", "us-east-1", client=s3)
    _, httpd = server.build(Config(port=0), reader=reader, host="127.0.0.1")
    with serving(httpd) as url:
        assert get(f"{url}/") == (200, "ok: read 10 bytes\n")
    assert s3.calls == [{"Bucket": "demo-app-data-use1-test", "Key": "seed.txt"}]


@pytest.mark.parametrize(
    ("error", "code"),
    [(FakeClientError("AccessDenied"), "AccessDenied"), (ConnectionError("no route"), None)],
)
def test_s3_read_failure_is_a_5xx(error: Exception, code: str | None) -> None:
    code = code or type(error).__name__
    reader = server.s3_reader("b", "seed.txt", "us-east-1", client=FakeS3(error))
    _, httpd = server.build(Config(port=0), reader=reader, host="127.0.0.1")
    with serving(httpd) as url:
        assert get(f"{url}/") == (503, f"S3 read failed: {code}\n")
        # The app itself stays healthy: the fault is the identity, not the pod.
        assert get(f"{url}/healthz")[0] == 200
        _, metrics = get(f"{url}/metrics")
    assert 'demo_app_requests_total{code="503"} 1.0' in metrics
    assert f'demo_app_s3_errors_total{{error="{code}"}} 1.0' in metrics


def test_bucket_builds_a_boto3_client(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple] = []

    class FakeBoto3:
        @staticmethod
        def client(service: str, region_name: str | None = None) -> FakeS3:
            built.append((service, region_name))
            return FakeS3()

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3)
    app, httpd = server.build(
        Config(port=0, bucket="demo-app-data-use1-test", region="us-east-1"), host="127.0.0.1"
    )
    httpd.server_close()
    assert built == [("s3", "us-east-1")]
    assert app.work({})[0] == 200


def test_unknown_path_is_404() -> None:
    # The bad-probe fault points the probes here.
    _, httpd = server.build(Config(port=0), host="127.0.0.1")
    with serving(httpd) as url:
        assert get(f"{url}/not-a-health-check")[0] == 404
