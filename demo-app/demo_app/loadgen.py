"""The traffic generator (`DEMO_MODE=loadgen`): calls the demo app at a steady rate and counts
what the client saw, so `DemoAppHighErrorRate` still fires when the app serves nothing at all
(OOMKilled, never Ready) and exports no metrics of its own."""

from __future__ import annotations

import http.client
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from prometheus_client import CollectorRegistry, Counter

from demo_app.config import Config
from demo_app.web import make_server

RESULTS = ("ok", "http_error", "conn_error", "timeout")


def call(url: str, timeout: float) -> str:
    """One GET; returns its `result` label."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
            return "ok" if 200 <= resp.status < 300 else "http_error"
    except urllib.error.HTTPError:
        return "http_error"
    except urllib.error.URLError as exc:
        return "timeout" if isinstance(exc.reason, TimeoutError) else "conn_error"
    # socket.timeout is TimeoutError (3.10+): a slow response after the connection succeeded.
    except TimeoutError:
        return "timeout"
    # Refused, reset, or closed mid-response (RemoteDisconnected is a ConnectionResetError).
    except (OSError, http.client.HTTPException):
        return "conn_error"


class Loadgen:
    def __init__(self, config: Config, registry: CollectorRegistry) -> None:
        self.config = config
        self.registry = registry
        self.requests = Counter(
            "demo_client_requests",
            "Requests to the demo app by what the client saw",
            ["result"],
            registry=registry,
        )
        # Every series exists from the start, so the error ratio is defined before the first
        # failure.
        for result in RESULTS:
            self.requests.labels(result=result)
        self.stopped = threading.Event()

    def once(self) -> str:
        result = call(self.config.target, self.config.timeout_seconds)
        self.requests.labels(result=result).inc()
        return result

    def run(self) -> None:
        """Start a request every 1/rps seconds, whatever the previous ones are doing; a slow
        target costs threads (at most rps × timeout in flight), never rate."""
        interval = 1 / self.config.rps
        workers = max(1, int(self.config.rps * self.config.timeout_seconds) + 2)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="loadgen") as pool:
            next_at = time.monotonic()
            while not self.stopped.is_set():
                pool.submit(self.once)
                next_at += interval
                # After a stall (paused process), skip the missed ticks instead of bursting.
                next_at = max(next_at, time.monotonic())
                self.stopped.wait(next_at - time.monotonic())

    def stop(self) -> None:
        self.stopped.set()


def build(config: Config, host: str = "0.0.0.0"):
    loadgen = Loadgen(config, CollectorRegistry())
    return loadgen, make_server(config.port, {}, loadgen.registry, host)
