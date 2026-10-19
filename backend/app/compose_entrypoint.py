"""Docker Compose entrypoint: local secrets, `alembic upgrade head`, then uvicorn.

Compose only (`docker-compose.yaml`); on EKS `scripts/create-secrets.sh` writes the secrets and
the chart runs the migration. The backend refuses to start without a `SESSION_SECRET`, and a
reviewer trying it locally shouldn't have to make one, or hash a password, first. So:

- A value set in the environment (from `.env` or the shell) always wins.
- Otherwise `SESSION_SECRET` and an admin password are generated once and kept in `STATE_DIR`
  (a named volume), so restarts keep the same login and sessions. Only the password's hash
  reaches the app; while the generated hash is in use, the password is also in
  `STATE_DIR/admin-password` for `make local` to print.
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


def generated_secrets(state_dir: Path) -> dict[str, str]:
    """The generated values, created on first use and read back afterwards."""
    path = state_dir / "secrets.json"
    if path.exists():
        return json.loads(path.read_text())
    password = secrets.token_urlsafe(18)
    values = {
        "SESSION_SECRET": secrets.token_hex(MIN_SECRET_BYTES),
        "ADMIN_PASSWORD_HASH": hash_password(password),
        "admin_password": password,
    }
    _write_private(path, json.dumps(values))
    return values


def apply_defaults(environ: MutableMapping[str, str], state_dir: Path) -> list[str]:
    """Fill each unset or empty secret from the generated ones; return the names filled.

    Writes `admin-password` while the generated admin hash is in use and removes it otherwise,
    so `make local` never prints a password that wouldn't log in.
    """
    values = generated_secrets(state_dir)
    filled = []
    for name in ("SESSION_SECRET", "ADMIN_PASSWORD_HASH"):
        if not environ.get(name):
            environ[name] = values[name]
            filled.append(name)
    password_file = state_dir / "admin-password"
    if "ADMIN_PASSWORD_HASH" in filled:
        _write_private(password_file, values["admin_password"] + "\n")
    else:
        password_file.unlink(missing_ok=True)
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
