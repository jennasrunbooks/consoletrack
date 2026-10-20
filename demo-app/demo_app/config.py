"""Settings from the environment (helm/charts/demo-app sets them). Bad values fail at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

MODES = ("server", "loadgen")


@dataclass(frozen=True)
class Config:
    mode: str = "server"
    port: int = 8080
    # Server: MiB to allocate, touch, and hold at startup (the OOM fault), after waiting
    # allocate_delay_seconds. Only used when allocate_mi is set.
    allocate_mi: int = 0
    allocate_delay_seconds: float = 3.0
    # Server: the object GET / reads. An empty bucket means no S3 read and no AWS calls (kind).
    bucket: str = ""
    object_key: str = "seed.txt"
    region: str = ""
    # Loadgen: what it calls, how often, and how long it waits for each response.
    target: str = "http://demo-app.demo:8080/"
    rps: float = 5.0
    timeout_seconds: float = 1.0


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
    return Config(
        mode=mode,
        port=_number(env, "PORT", int, 8080, 1),
        allocate_mi=_number(env, "DEMO_ALLOCATE_MI", int, 0, 0),
        allocate_delay_seconds=_number(env, "DEMO_ALLOCATE_DELAY_SECONDS", float, 3.0, 0),
        bucket=env.get("DEMO_BUCKET", "").strip(),
        object_key=env.get("DEMO_OBJECT_KEY", "").strip() or "seed.txt",
        region=env.get("AWS_REGION", "").strip(),
        target=env.get("LOADGEN_TARGET", "").strip() or Config.target,
        rps=rps,
        timeout_seconds=timeout,
    )
