from __future__ import annotations

import sys
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
    assert all(buf[i] == 1 for i in range(0, len(buf), server.PAGE))


@pytest.mark.skipif(not STATUS.exists(), reason="needs Linux /proc")
def test_allocate_is_resident() -> None:
    # Reserved-but-untouched memory wouldn't count against the cgroup limit; this must.
    before = rss_bytes()
    buf = server.allocate(32)
    assert rss_bytes() - before >= 30 * MIB
    del buf


def no_sleep(_: float) -> None:
    pass


def test_allocation_is_held_and_reported() -> None:
    app, httpd = server.build(Config(port=0, allocate_mi=4), host="127.0.0.1", sleep=no_sleep)
    assert app.held is not None and len(app.held) == 4 * MIB
    with serving(httpd) as url:
        _, metrics = get(f"{url}/metrics")
    assert "demo_app_allocated_bytes 4.194304e+06" in metrics


def test_waits_then_allocates_then_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    # The kill comes a few seconds after start; listening last means the faulted pod is never
    # Ready.
    order: list[tuple] = []
    real_allocate, real_make_server = server.allocate, server.make_server

    def allocate(mib: int) -> bytearray:
        order.append(("allocate", mib))
        return real_allocate(mib)

    def make_server(*args: object):
        order.append(("listen",))
        return real_make_server(*args)

    monkeypatch.setattr(server, "allocate", allocate)
    monkeypatch.setattr(server, "make_server", make_server)
    _, httpd = server.build(
        Config(port=0, allocate_mi=1, allocate_delay_seconds=2.5),
        host="127.0.0.1",
        sleep=lambda seconds: order.append(("wait", seconds)),
    )
    httpd.server_close()
    assert order == [("wait", 2.5), ("allocate", 1), ("listen",)]


def test_the_startup_log_does_not_give_the_size_away(caplog: pytest.LogCaptureFixture) -> None:
    # The triage agent reads the faulted container's log; the size would hand it the answer.
    caplog.set_level("INFO", logger="demo_app.server")
    _, httpd = server.build(
        Config(port=0, allocate_mi=1, allocate_delay_seconds=3), host="127.0.0.1", sleep=no_sleep
    )
    httpd.server_close()
    first = caplog.messages[0]
    assert first == "allocating memory in 3s"
    assert "MiB" not in first and "DEMO_ALLOCATE_MI" not in first


def test_default_delay_is_three_seconds() -> None:
    waits: list[float] = []
    _, httpd = server.build(Config(port=0, allocate_mi=1), host="127.0.0.1", sleep=waits.append)
    httpd.server_close()
    assert waits == [3.0]


def test_no_allocation_by_default() -> None:
    waits: list[float] = []
    app, httpd = server.build(Config(port=0), host="127.0.0.1", sleep=waits.append)
    httpd.server_close()
    assert app.held is None
    # No fault, no wait: a normal start listens straight away.
    assert waits == []


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
    assert app.work()[0] == 200


def test_unknown_path_is_404() -> None:
    # The bad-probe fault points the probes here.
    _, httpd = server.build(Config(port=0), host="127.0.0.1")
    with serving(httpd) as url:
        assert get(f"{url}/not-a-health-check")[0] == 404
