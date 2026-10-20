"""Settings from the environment (helm/charts/demo-app sets them). Bad values fail at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

MODES = ("server", "loadgen")
REPORTS = ("", "large")


@dataclass(frozen=True)
class Config:
    mode: str = "server"
    port: int = 8080
    # Server: MiB each `X-Demo-Report: large` request adds to the report cache, and the cache's
    # bound (the OOM scenario: the bound is above a 128Mi limit).
    report_mi: int = 2
    report_cache_mi: int = 140
    # Server: the object GET / reads. An empty bucket means no S3 read and no AWS calls (kind).
    bucket: str = ""
    object_key: str = "seed.txt"
    region: str = ""
    # Loadgen: what it calls, how often, and how long it waits for each response, and the
    # X-Demo-Report value it sends ("" = none; "large" with the OOM fault).
    target: str = "http://demo-app.demo:8080/"
    rps: float = 5.0
    timeout_seconds: float = 1.0
    report: str = ""


def _number(env: Mapping[str, str], name: str, kind: type, default, minimum):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be a {kind.__name__}, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {raw!r}")
    return value


def load(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    mode = env.get("DEMO_MODE", "").strip() or "server"
    if mode not in MODES:
        raise ValueError(f"DEMO_MODE must be one of {', '.join(MODES)}, got {mode!r}")
    rps = _number(env, "LOADGEN_RPS", float, 5.0, 0.1)
    timeout = _number(env, "LOADGEN_TIMEOUT_SECONDS", float, 1.0, 0.05)
    report = env.get("LOADGEN_REPORT", "").strip().lower()
    if report not in REPORTS:
        raise ValueError(f"LOADGEN_REPORT must be empty or large, got {report!r}")
    return Config(
        mode=mode,
        port=_number(env, "PORT", int, 8080, 1),
        report_mi=_number(env, "DEMO_REPORT_MI", int, 2, 1),
        report_cache_mi=_number(env, "DEMO_REPORT_CACHE_MI", int, 140, 0),
        bucket=env.get("DEMO_BUCKET", "").strip(),
        object_key=env.get("DEMO_OBJECT_KEY", "").strip() or "seed.txt",
        region=env.get("AWS_REGION", "").strip(),
        target=env.get("LOADGEN_TARGET", "").strip() or Config.target,
        rps=rps,
        timeout_seconds=timeout,
        report=report,
    )
