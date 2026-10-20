"""Docker Compose entrypoint: local secrets, `alembic upgrade head`, then uvicorn.

Compose only (`docker-compose.yaml`); on EKS `scripts/create-secrets.sh` writes the secrets and
the chart runs the migration. The backend refuses to start without a `SESSION_SECRET`, and a
reviewer trying it locally shouldn't have to make one, or hash a password, first. So:

- A value set in the environment (from `.env` or the shell) always wins.
- Otherwise `SESSION_SECRET`, an admin password, and `ALERT_WEBHOOK_TOKEN` are generated once
  and kept in `STATE_DIR` (a named volume), so restarts keep the same login and sessions. Only
  the password's hash reaches the app; while the generated hash is in use, the password is also
  in `STATE_DIR/admin-password` for `make local` to print.
- While the generated webhook token is in use, it is also in `STATE_DIR/alert-webhook-token`,
  which `make local` copies into kind for Alertmanager (scripts/local-kind.sh).
- The viewer account is never generated: without `VIEWER_PASSWORD_HASH` it can't log in.

`docker compose down --volumes` (`make local-down`) deletes the volume and with it these values.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from collections.abc import MutableMapping
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.services.auth import MIN_SECRET_BYTES, hash_password

STATE_DIR = Path(os.environ.get("CT_LOCAL_STATE_DIR", "/run/consoletrack"))
BACKEND = Path(__file__).resolve().parents[1]
UVICORN = ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _new_secrets() -> dict[str, str]:
    password = secrets.token_urlsafe(18)
    return {
        "SESSION_SECRET": secrets.token_hex(MIN_SECRET_BYTES),
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "admin_password": password,
        "ALERT_WEBHOOK_TOKEN": secrets.token_urlsafe(32),
    }


def generated_secrets(state_dir: Path) -> dict[str, str]:
    """The generated values, created on first use and read back afterwards.

    A volume from an older version may lack a newer value; only that one is added.
    """
    path = state_dir / "secrets.json"
    values = json.loads(path.read_text()) if path.exists() else {}
    missing = {k: v for k, v in _new_secrets().items() if k not in values}
    if missing:
        values |= missing
        _write_private(path, json.dumps(values))
    return values


def apply_defaults(environ: MutableMapping[str, str], state_dir: Path) -> list[str]:
    """Fill each unset or empty secret from the generated ones; return the names filled.

    Writes `admin-password` and `alert-webhook-token` while the generated value is in use and
    removes them otherwise, so `make local` never hands out a value the backend wouldn't accept.
    """
    values = generated_secrets(state_dir)
    filled = []
    for name in ("SESSION_SECRET", "ADMIN_PASSWORD_HASH", "ALERT_WEBHOOK_TOKEN"):
        if not environ.get(name):
            environ[name] = values[name]
            filled.append(name)
    for name, file, value in (
        ("ADMIN_PASSWORD_HASH", "admin-password", values["admin_password"]),
        ("ALERT_WEBHOOK_TOKEN", "alert-webhook-token", values["ALERT_WEBHOOK_TOKEN"]),
    ):
        if name in filled:
            _write_private(state_dir / file, value + "\n")
        else:
            (state_dir / file).unlink(missing_ok=True)
    return filled


def main() -> None:
    filled = apply_defaults(os.environ, STATE_DIR)
    if filled:
        # Names only, never values.
        print(f"compose-entrypoint: generated {', '.join(filled)}", file=sys.stderr)
    config = Config(str(BACKEND / "alembic.ini"))
    command.upgrade(config, "head")
    os.execvp(UVICORN[0], UVICORN)


if __name__ == "__main__":
    main()
