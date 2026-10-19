"""docker-compose.yaml invariants that `docker compose config` doesn't check.

Runs without Docker (`make test-compose`, part of `make test`); `make lint-compose` validates
the file itself with `docker compose config` wherever Docker is installed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yaml"
SERVICES = yaml.safe_load(COMPOSE.read_text())["services"]
# Names that would hold a real credential: each must come from .env or the shell.
SECRET_NAME = re.compile(r"SECRET|TOKEN|PASSWORD|API_KEY|HASH")
# The only literal credential: Postgres inside Compose, also in .env.example's DATABASE_URL.
LOCAL_ONLY = {("postgres", "POSTGRES_PASSWORD")}
ACCOUNT_ID = re.compile(r"\b\d{12}\b")


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_ports_bind_to_loopback_only(name: str) -> None:
    for port in SERVICES[name].get("ports", []):
        assert isinstance(port, str) and port.startswith("127.0.0.1:"), (name, port)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_secrets_are_never_literals(name: str) -> None:
    for key, value in (SERVICES[name].get("environment") or {}).items():
        if SECRET_NAME.search(key) and (name, key) not in LOCAL_ONLY:
            assert str(value) == f"${{{key}:-}}", (name, key)


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_images_are_pinned_or_built(name: str) -> None:
    service = SERVICES[name]
    if "build" in service:
        assert service["image"].endswith(":local"), name
    else:
        assert re.search(r":[\w.-]+@sha256:[0-9a-f]{64}$", service["image"]), name


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_dependencies_wait_for_health(name: str) -> None:
    depends_on = SERVICES[name].get("depends_on", {})
    assert isinstance(depends_on, dict), name
    for dependency, condition in depends_on.items():
        assert condition == {"condition": "service_healthy"}, (name, dependency)
        assert "healthcheck" in SERVICES[dependency], dependency


def test_no_account_ids() -> None:
    for path in [COMPOSE, *COMPOSE.parent.joinpath("compose").glob("*.conf")]:
        found = set(ACCOUNT_ID.findall(path.read_text())) - {"123456789012"}
        assert not found, path
