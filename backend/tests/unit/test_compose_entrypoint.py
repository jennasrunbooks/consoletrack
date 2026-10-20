from __future__ import annotations

import json
import stat
from pathlib import Path

from app.compose_entrypoint import apply_defaults
from app.services.auth import MIN_SECRET_BYTES, Authenticator


def test_generates_missing_secrets_once(tmp_path: Path) -> None:
    first: dict[str, str] = {"SESSION_SECRET": ""}
    assert apply_defaults(first, tmp_path) == [
        "SESSION_SECRET",
        "ADMIN_PASSWORD_HASH",
        "ALERT_WEBHOOK_TOKEN",
    ]
    assert len(first["SESSION_SECRET"].encode()) >= MIN_SECRET_BYTES

    second: dict[str, str] = {}
    apply_defaults(second, tmp_path)
    assert second == first  # restarts keep the same login and sessions


def test_generated_password_matches_the_hash(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    apply_defaults(environ, tmp_path)
    password = (tmp_path / "admin-password").read_text().strip()
    authenticator = Authenticator({"admin": environ["ADMIN_PASSWORD_HASH"], "viewer": None})
    assert authenticator.verify("admin", password).username == "admin"


def test_files_are_private(tmp_path: Path) -> None:
    apply_defaults({}, tmp_path)
    for name in ("secrets.json", "admin-password", "alert-webhook-token"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_environment_wins_and_hides_the_unused_values(tmp_path: Path) -> None:
    apply_defaults({}, tmp_path)
    environ = {
        "SESSION_SECRET": "s" * MIN_SECRET_BYTES,
        "ADMIN_PASSWORD_HASH": "from-env",
        "ALERT_WEBHOOK_TOKEN": "from-env",
    }
    expected = dict(environ)
    assert apply_defaults(environ, tmp_path) == []
    assert environ == expected
    assert not (tmp_path / "admin-password").exists()
    assert not (tmp_path / "alert-webhook-token").exists()


def test_generated_webhook_token_is_written_for_kind(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    apply_defaults(environ, tmp_path)
    token = (tmp_path / "alert-webhook-token").read_text().strip()
    assert token == environ["ALERT_WEBHOOK_TOKEN"]


def test_adds_values_missing_from_an_older_volume(tmp_path: Path) -> None:
    old: dict[str, str] = {}
    apply_defaults(old, tmp_path)
    path = tmp_path / "secrets.json"
    stored = json.loads(path.read_text())
    del stored["ALERT_WEBHOOK_TOKEN"]
    path.write_text(json.dumps(stored))

    environ: dict[str, str] = {}
    assert "ALERT_WEBHOOK_TOKEN" in apply_defaults(environ, tmp_path)
    # The login and sessions from before are kept.
    assert environ["SESSION_SECRET"] == old["SESSION_SECRET"]
    assert environ["ADMIN_PASSWORD_HASH"] == old["ADMIN_PASSWORD_HASH"]
