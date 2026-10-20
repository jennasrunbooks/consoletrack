"""The demo app (`DEMO_MODE=server`): `GET /` is the work, `/healthz` and `/metrics` the rest.

With `DEMO_BUCKET` set, every `GET /` reads one S3 object through the SDK's default credential
chain (Pod Identity on EKS) and answers 503 when it can't: the identity scenario. With it empty
(kind), boto3 is never imported, so there are no AWS calls at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge

from demo_app.config import Config
from demo_app.web import Response, make_server, text

log = logging.getLogger(__name__)

MIB = 1024 * 1024
PAGE = 4096

# Reads the configured object and returns its bytes; raises when it can't.
Reader = Callable[[], bytes]


def allocate(mib: int) -> bytearray:
    """Allocate `mib` MiB and write to every page so it is resident, not just reserved.

    The caller keeps the result for the life of the process: that held memory is what makes the
    OOM fault deterministic.
    """
    buf = bytearray(mib * MIB)
    for i in range(0, len(buf), PAGE):
        buf[i] = 1
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


class App:
    def __init__(
        self, reader: Reader | None, registry: CollectorRegistry, held: bytearray | None = None
    ) -> None:
        self.reader = reader
        self.registry = registry
        self.held = held
        self.requests = Counter(
            "demo_app_requests", "GET / requests by status code", ["code"], registry=registry
        )
        self.s3_errors = Counter(
            "demo_app_s3_errors", "Failed S3 reads by error code", ["error"], registry=registry
        )
        Gauge(
            "demo_app_allocated_bytes",
            "Memory allocated and held at startup (DEMO_ALLOCATE_MI)",
            registry=registry,
        ).set(len(held) if held is not None else 0)

    def work(self) -> Response:
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
    """Allocate first, then listen: with the OOM fault the container dies before it is Ready."""
    held = allocate(config.allocate_mi) if config.allocate_mi else None
    if held is not None:
        log.info("allocated and holding %d MiB", config.allocate_mi)
    if reader is None and config.bucket:
        reader = s3_reader(config.bucket, config.object_key, config.region)
    app = App(reader, CollectorRegistry(), held)
    return app, make_server(config.port, {"/": app.work}, app.registry, host)
