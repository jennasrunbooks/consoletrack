from __future__ import annotations

import stat
from pathlib import Path

from app.compose_entrypoint import apply_defaults
from app.services.auth import MIN_SECRET_BYTES, Authenticator


def test_generates_missing_secrets_once(tmp_path: Path) -> None:
    first: dict[str, str] = {"SESSION_SECRET": ""}
    assert apply_defaults(first, tmp_path) == ["SESSION_SECRET", "ADMIN_PASSWORD_HASH"]
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
    for name in ("secrets.json", "admin-password"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_environment_wins_and_hides_the_unused_password(tmp_path: Path) -> None:
    apply_defaults({}, tmp_path)
    environ = {"SESSION_SECRET": "s" * MIN_SECRET_BYTES, "ADMIN_PASSWORD_HASH": "from-env"}
    assert apply_defaults(environ, tmp_path) == []
    assert environ == {"SESSION_SECRET": "s" * MIN_SECRET_BYTES, "ADMIN_PASSWORD_HASH": "from-env"}
    assert not (tmp_path / "admin-password").exists()
