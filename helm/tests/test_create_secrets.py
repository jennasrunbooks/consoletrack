"""scripts/create-secrets.sh against a stub kubectl: what it creates, and where the values go.

The stub records every argument and every stdin it is given. The rules under test: each Secret
only if missing, values only on stdin (never in an argument), the admin password printed once on
first creation only, and every kubectl call naming the --context.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "create-secrets.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="scripts/create-secrets.sh needs openssl"
)

KUBECTL = r"""#!/usr/bin/env bash
# Stub: logs argv and stdin; "creates" a Secret when `apply` follows `create secret`.
state=$STUB_DIR/state; mkdir -p "$state"
echo "$*" >>"$STUB_DIR/argv"
args=" $* "
if [[ $args == *" get secret "* ]]; then
	name=$(sed -E 's/.* get secret ([^ ]+).*/\1/' <<<"$args")
	[[ -e $state/$name ]]; exit
fi
if [[ $args == *" create secret generic "* ]]; then
	name=$(sed -E 's/.* create secret generic ([^ ]+).*/\1/' <<<"$args")
	cat >"$STUB_DIR/stdin-$name"
	echo "$name" >"$STUB_DIR/pending"
	echo "kind: Secret"
elif [[ $args == *" create namespace "* ]]; then
	echo "kind: Namespace"
elif [[ $args == *" apply "* ]]; then
	cat >/dev/null
	if [[ -s $STUB_DIR/pending ]]; then
		touch "$state/$(cat "$STUB_DIR/pending")"
		: >"$STUB_DIR/pending"
	fi
fi
"""

FAKE_HASH = r"""#!/usr/bin/env bash
read -r password
echo "$password" >>"$STUB_DIR/hashed"
echo '$argon2id$v=19$m=8,t=1,p=1$fake$fake'
"""


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("kubectl", KUBECTL), ("fake-hash", FAKE_HASH)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def run(stub: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{stub / 'bin'}:{os.environ['PATH']}",
        "STUB_DIR": str(stub),
        "ANTHROPIC_API_KEY": "sk-ant-test-key",
        "KUBE_CONTEXT": "consoletrack-eks",
        "CREATE_SECRETS_HASH": str(stub / "bin" / "fake-hash"),
        **extra,
    }
    return subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


def test_creates_each_secret_and_prints_the_admin_password_once(stub: Path) -> None:
    first = run(stub)
    assert first.returncode == 0, first.stderr
    for name in ("consoletrack-api-key", "consoletrack-auth", "consoletrack-webhook"):
        assert (stub / "state" / name).exists()
    password = (stub / "hashed").read_text().strip()
    assert len(password) == 24
    assert f"password:  {password}" in first.stdout
    assert "username:  admin" in first.stdout

    # Values only on stdin: not one argument of any kubectl call carries them.
    argv = (stub / "argv").read_text()
    assert "sk-ant-test-key" not in argv
    assert password not in argv
    assert "argon2id" not in argv
    assert "sk-ant-test-key" in (stub / "stdin-consoletrack-api-key").read_text()
    auth = (stub / "stdin-consoletrack-auth").read_text()
    assert auth.startswith("admin-password-hash=$argon2id$")
    assert (
        "\nviewer-password-hash=\n" in auth
    )  # no VIEWER_PASSWORD_HASH: the viewer can't log in
    assert len(auth.split("session-secret=")[1].strip()) == 64
    assert (
        len((stub / "stdin-consoletrack-webhook").read_text().strip().split("=")[1])
        == 64
    )
    # The plaintext password reaches only the hasher, never a Secret.
    assert password not in auth


def test_every_kubectl_call_names_the_context(stub: Path) -> None:
    assert run(stub).returncode == 0
    for line in (stub / "argv").read_text().splitlines():
        assert line.startswith("--context consoletrack-eks ")


def test_rerun_creates_nothing_and_prints_no_password(stub: Path) -> None:
    assert run(stub).returncode == 0
    (stub / "argv").unlink()
    (stub / "hashed").unlink()
    again = run(stub)
    assert again.returncode == 0, again.stderr
    assert "password:" not in again.stdout
    assert not (stub / "hashed").exists()
    assert " create secret " not in (stub / "argv").read_text()
    assert again.stdout.count("left as is") == 3


def test_own_hashes_are_used_and_no_password_is_generated(stub: Path) -> None:
    admin = "$argon2id$v=19$m=8,t=1,p=1$own$admin"
    viewer = "$argon2id$v=19$m=8,t=1,p=1$own$viewer"
    result = run(stub, ADMIN_PASSWORD_HASH=admin, VIEWER_PASSWORD_HASH=viewer)
    assert result.returncode == 0, result.stderr
    assert "password:" not in result.stdout
    assert not (stub / "hashed").exists()
    auth = (stub / "stdin-consoletrack-auth").read_text()
    assert f"admin-password-hash={admin}\nviewer-password-hash={viewer}\n" in auth


def test_a_value_that_is_not_an_argon2id_hash_is_refused(stub: Path) -> None:
    result = run(stub, ADMIN_PASSWORD_HASH="hunter2")
    assert result.returncode == 1
    assert "hunter2" not in result.stdout + result.stderr
    assert not (stub / "state" / "consoletrack-auth").exists()


def test_missing_api_key_stops_before_creating_anything(stub: Path) -> None:
    result = run(stub, ANTHROPIC_API_KEY="")
    assert result.returncode == 1
    assert "ANTHROPIC_API_KEY" in result.stderr
    assert not (stub / "state" / "consoletrack-api-key").exists()


def test_the_banner_is_also_written_to_a_private_file(stub: Path) -> None:
    banner = stub / "banner"
    result = run(stub, CREATE_SECRETS_BANNER_FILE=str(banner))
    assert result.returncode == 0, result.stderr
    password = (stub / "hashed").read_text().strip()
    assert password in banner.read_text()
    assert stat.S_IMODE(banner.stat().st_mode) == 0o600
