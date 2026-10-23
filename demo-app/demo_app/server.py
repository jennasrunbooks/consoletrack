"""The demo app (`DEMO_MODE=server`): `GET /` is the work, `/healthz` and `/metrics` the rest.

With `DEMO_BUCKET` set, every `GET /` reads one S3 object through the SDK's default credential
chain (Pod Identity on EKS) and answers 503 when it can't: the identity scenario. With it empty
(kind), boto3 is never imported, so there are no AWS calls at all.

A `GET /` with `X-Demo-Report: large` also builds a report and keeps it in an in-memory cache,
`report_mi` MiB each, at most `report_cache_mi` in all. The cache is bounded, but its bound is
above a 128Mi limit: the OOM scenario, driven by the traffic generator asking for reports, so
the app's own Deployment never changes (the app outgrew its limit; nothing rolled out).

Once the cache is full, a new report is written into the oldest one's buffer instead of a new
allocation, so the cache allocates nothing more and memory stays at the cache plus the app.
Allocating a fresh buffer and freeing the oldest on every request (each on its own thread) held
on to freed memory on EKS (Graviton): with the fault on and a 140 MiB cache, that version peaked
at 213 MiB, leaving 43 MiB under the 256Mi fix; this one peaks at 159 MiB (make measure, #79).
On kind both versions peak at 158-160 MiB.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Mapping

from prometheus_client import CollectorRegistry, Counter, Gauge

from demo_app.config import Config
from demo_app.web import Response, make_server, text

log = logging.getLogger(__name__)

MIB = 1024 * 1024
PAGE = 4096

# Reads the configured object and returns its bytes; raises when it can't.
Reader = Callable[[], bytes]


REPORT_HEADER = "X-Demo-Report"


def allocate(mib: int) -> bytearray:
    """Allocate `mib` MiB and write to every page so it is resident, not just reserved: memory
    that is only reserved wouldn't count against the container's limit."""
    return write_report(bytearray(mib * MIB))


def write_report(buf: bytearray) -> bytearray:
    """ "Build" a report in `buf`, in place: every page written, nothing allocated."""
    for i in range(0, len(buf), PAGE):
        buf[i] = (buf[i] + 1) % 256 or 1
    return buf


def s3_reader(bucket: str, key: str, region: str, client=None) -> Reader:
    """A Reader for s3://bucket/key. Imports boto3 only here, so an empty bucket never loads it."""
    if client is None:
        import boto3

        client = boto3.client("s3", region_name=region or None)

    def read() -> bytes:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()

    return read


def _error_code(exc: Exception) -> str:
    # botocore's ClientError carries the service's code (AccessDenied, NoSuchKey); anything else
    # (no credentials, endpoint unreachable) is named by its type.
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


class ReportCache:
    """Reports held in memory, at most `cap_mi` MiB. Full, it rewrites the oldest report's buffer
    in place: no allocation or free per request, so memory doesn't drift above the cap."""

    def __init__(self, report_mi: int, cap_mi: int, registry: CollectorRegistry) -> None:
        self.report_mi = report_mi
        self.max_reports = cap_mi // report_mi
        self.reports: deque[bytearray] = deque()
        self.lock = threading.Lock()
        self.size = Gauge(
            "demo_app_report_cache_bytes", "Bytes held in the report cache", registry=registry
        )

    def add(self) -> None:
        if self.max_reports == 0:
            return
        with self.lock:
            if len(self.reports) < self.max_reports:
                report = allocate(self.report_mi)
            else:
                report = write_report(self.reports.popleft())
            self.reports.append(report)
            self.size.set(sum(len(r) for r in self.reports))


class App:
    def __init__(
        self, reader: Reader | None, registry: CollectorRegistry, cache: ReportCache
    ) -> None:
        self.reader = reader
        self.registry = registry
        self.cache = cache
        self.requests = Counter(
            "demo_app_requests", "GET / requests by status code", ["code"], registry=registry
        )
        self.s3_errors = Counter(
            "demo_app_s3_errors", "Failed S3 reads by error code", ["error"], registry=registry
        )

    def work(self, headers: Mapping[str, str]) -> Response:
        if headers.get(REPORT_HEADER, "").strip().lower() == "large":
            self.cache.add()
        if self.reader is None:
            response = text(200, "ok\n")
        else:
            try:
                body = self.reader()
            except Exception as exc:
                code = _error_code(exc)
                self.s3_errors.labels(error=code).inc()
                log.warning("S3 read failed: %s: %s", code, exc)
                response = text(503, f"S3 read failed: {code}\n")
            else:
                response = text(200, f"ok: read {len(body)} bytes\n")
        self.requests.labels(code=str(response[0])).inc()
        return response


def build(config: Config, *, reader: Reader | None = None, host: str = "0.0.0.0"):
    if reader is None and config.bucket:
        reader = s3_reader(config.bucket, config.object_key, config.region)
    registry = CollectorRegistry()
    cache = ReportCache(config.report_mi, config.report_cache_mi, registry)
    app = App(reader, registry, cache)
    return app, make_server(config.port, {"/": app.work}, registry, host)
