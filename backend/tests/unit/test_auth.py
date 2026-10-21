"""Login, logout, the session cookie, the login rate limit, and the startup checks (#19)."""

from __future__ import annotations

import io
import logging
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import hash_password as hash_password_cli
from app.api.deps import SESSION_COOKIE
from app.enums import Role
from app.main import create_app
from app.schemas import User
from app.services import auth
from app.services.auth import Authenticator, LoginRateLimiter, SessionSigner
from app.services.errors import InvalidCredentials, TooManyLoginAttempts
from app.settings import Settings
from tests.conftest import TEST_SESSION_SECRET
from tests.unit.conftest import (
    ADMIN_HASH,
    ADMIN_PASSWORD,
    NOW,
    VIEWER_PASSWORD,
    session_token,
)
from tests.unit.fakes import FakeStore

LOGIN = "/api/v1/auth/login"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(store: FakeStore, settings: Settings, clock: Clock) -> FastAPI:
    return create_app(settings, uow_factory=store.uow, clock=clock)


def browser(app: FastAPI, ip: str = "203.0.113.10") -> TestClient:
    # https, so the client's cookie jar sends the Secure cookie back like a browser would.
    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


def set_cookie(response) -> SimpleCookie:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie


def log_in(client: TestClient, username: str, password: str):
    return client.post(LOGIN, json={"username": username, "password": password})


# --- Login -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("username", "password", "role"),
    [("admin", ADMIN_PASSWORD, "admin"), ("viewer", VIEWER_PASSWORD, "viewer")],
)
def test_login_sets_the_session_and_returns_the_user(
    app: FastAPI, username: str, password: str, role: str
) -> None:
    client = browser(app)
    response = log_in(client, username, password)
    assert response.status_code == 200, response.text
    assert response.json() == {"username": username, "role": role}
    assert client.get("/api/v1/auth/me").json() == {"username": username, "role": role}


def test_cookie_flags_are_exactly_the_contract(app: FastAPI) -> None:
    response = log_in(browser(app), "admin", ADMIN_PASSWORD)
    header = response.headers["set-cookie"]
    attributes = {part.strip().split("=")[0].lower() for part in header.split(";")[1:]}
    assert attributes == {"httponly", "secure", "samesite", "path", "max-age"}
    morsel = set_cookie(response)[SESSION_COOKIE]
    assert morsel["httponly"] and morsel["secure"]
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/"
    assert morsel["max-age"] == str(8 * 3600)


def test_wrong_password_and_unknown_user_get_the_same_401(anon: TestClient) -> None:
    wrong = log_in(anon, "admin", "not-the-password")
    unknown = log_in(anon, "root", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert (
        wrong.json()
        == unknown.json()
        == {
            "code": "unauthorized",
            "message": "invalid username or password",
        }
    )
    assert "set-cookie" not in wrong.headers and "set-cookie" not in unknown.headers


def test_unknown_user_still_runs_a_password_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same work for an unknown user as for a wrong password, so timing doesn't tell them apart."""
    calls: list[str] = []
    real = auth._hasher

    class Spy:
        def verify(self, stored: str, password: str) -> bool:
            calls.append(stored)
            return real.verify(stored, password)

    monkeypatch.setattr(auth, "_hasher", Spy())
    authenticator = Authenticator({"admin": ADMIN_HASH, "viewer": None})
    for username in ("admin", "viewer", "nobody"):
        with pytest.raises(InvalidCredentials):
            authenticator.verify(username, "guess")
    assert len(calls) == 3
    # The dummy hash has the configured hash's (cost) parameters.
    assert calls[1] == calls[2] != ADMIN_HASH
    assert calls[1].split("$")[3] == ADMIN_HASH.split("$")[3]


def test_an_account_without_a_hash_cannot_log_in(store: FakeStore) -> None:
    app = create_app(Settings(admin_password_hash=ADMIN_HASH), uow_factory=store.uow)
    assert log_in(browser(app), "viewer", VIEWER_PASSWORD).status_code == 401
    assert log_in(browser(app), "admin", ADMIN_PASSWORD).status_code == 200


def test_login_validates_the_body(anon: TestClient) -> None:
    assert anon.post(LOGIN, json={"username": "admin"}).status_code == 422
    assert anon.post(LOGIN, json={"username": "", "password": "x"}).status_code == 422
    response = anon.post(LOGIN, content="username=admin", headers={"content-type": "text/plain"})
    assert response.status_code == 422


def test_login_logs_username_and_ip_never_the_password(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.auth")
    client = browser(app, ip="198.51.100.7")
    log_in(client, "admin", "wrong-password-123")
    log_in(client, "admin", ADMIN_PASSWORD)
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "login failed username='admin' ip=198.51.100.7",
        "login succeeded username='admin' ip=198.51.100.7",
    ]
    text = caplog.text
    assert "wrong-password-123" not in text and ADMIN_PASSWORD not in text
    assert ADMIN_HASH not in text and TEST_SESSION_SECRET not in text


# --- Rate limit ------------------------------------------------------------------------------


def test_five_failures_for_a_username_lock_it_for_five_minutes(app: FastAPI, clock: Clock) -> None:
    for i in range(5):
        # A different address each time: the username limit alone trips.
        assert log_in(browser(app, ip=f"203.0.113.{i}"), "admin", "guess").status_code == 401
    other_ip = browser(app, ip="203.0.113.99")
    locked = log_in(other_ip, "admin", ADMIN_PASSWORD)  # even the right password
    assert locked.status_code == 429
    assert locked.json()["code"] == "too_many_requests"
    assert locked.headers["retry-after"] == "300"
    # The other account isn't affected.
    assert log_in(other_ip, "viewer", VIEWER_PASSWORD).status_code == 200

    clock.now = NOW + timedelta(minutes=5)
    assert log_in(other_ip, "admin", ADMIN_PASSWORD).status_code == 200


def test_five_failures_from_an_address_lock_it_for_every_username(app: FastAPI) -> None:
    client = browser(app, ip="192.0.2.1")
    for username in ("admin", "viewer", "root", "ops", "a"):
        assert log_in(client, username, "guess").status_code == 401
    assert log_in(client, "viewer", VIEWER_PASSWORD).status_code == 429
    assert log_in(browser(app, ip="192.0.2.2"), "viewer", VIEWER_PASSWORD).status_code == 200


def test_rate_limited_attempts_are_logged(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    client = browser(app, ip="192.0.2.5")
    for _ in range(6):
        log_in(client, "admin", "guess")
    assert caplog.records[-1].getMessage() == "login rate-limited username='admin' ip=192.0.2.5"


def test_limiter_window_slides_and_success_clears_only_the_username() -> None:
    limiter = LoginRateLimiter(max_failures=2, window=timedelta(minutes=5))
    keys = ("user:admin", "ip:a")
    limiter.record_failure(keys, NOW)
    limiter.record_failure(keys, NOW + timedelta(minutes=1))
    with pytest.raises(TooManyLoginAttempts) as exc:
        limiter.check(keys, NOW + timedelta(minutes=2))
    assert exc.value.retry_after == 180
    limiter.check(keys, NOW + timedelta(minutes=5))  # the first failure aged out

    limiter.record_failure(keys, NOW + timedelta(minutes=5))
    limiter.clear("user:admin")
    limiter.check(["user:admin"], NOW + timedelta(minutes=5))
    with pytest.raises(TooManyLoginAttempts):
        limiter.check(["ip:a"], NOW + timedelta(minutes=5))


def test_limiter_forgets_keys_whose_failures_aged_out() -> None:
    limiter = LoginRateLimiter()
    limiter.record_failure(["user:x", "ip:1"], NOW)
    limiter.record_failure(["user:y", "ip:2"], NOW + timedelta(minutes=6))
    assert set(limiter._failures) == {"user:y", "ip:2"}


# --- Session cookie --------------------------------------------------------------------------


def test_expired_session_is_401(app: FastAPI, clock: Clock) -> None:
    client = browser(app)
    log_in(client, "viewer", VIEWER_PASSWORD)
    clock.now = NOW + timedelta(hours=8) - timedelta(seconds=1)
    assert client.get("/api/v1/auth/me").status_code == 200
    clock.now = NOW + timedelta(hours=8)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda token: token[:-2] + ("AA" if token[-2:] != "AA" else "BB"), id="sig"),
        pytest.param(lambda token: token.replace(".", "x.", 1), id="payload"),
        pytest.param(lambda token: "not-a-session", id="garbage"),
        pytest.param(
            lambda token: SessionSigner("x" * 32).issue(
                User(username="admin", role=Role.admin), NOW
            ),
            id="other-secret",
        ),
    ],
)
def test_tampered_session_is_401(app: FastAPI, anon: TestClient, tamper) -> None:
    anon.cookies.set(SESSION_COOKIE, tamper(session_token(app, "admin")))
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_session_role_comes_from_the_username_not_the_cookie() -> None:
    signer = SessionSigner(TEST_SESSION_SECRET)
    exp = int((NOW + timedelta(hours=1)).timestamp())
    forged = signer._serializer.dumps({"u": "viewer", "r": "admin", "exp": exp})
    assert signer.read(forged, NOW) == User(username="viewer", role=Role.viewer)
    for payload in ({"u": "root", "exp": exp}, {"u": "admin"}, {"u": ["admin"], "exp": exp}, []):
        assert signer.read(signer._serializer.dumps(payload), NOW) is None


def test_viewer_write_is_403_with_an_error_body(viewer: TestClient) -> None:
    response = viewer.post("/api/v1/changes/1/accept", json={"note": "fine"})
    assert response.status_code == 403
    assert response.json() == {
        "code": "forbidden",
        "message": "this action requires the admin role",
    }


# --- Me and logout ---------------------------------------------------------------------------


def test_me_is_the_session_user_or_401(viewer: TestClient, anon: TestClient) -> None:
    assert viewer.get("/api/v1/auth/me").json() == {"username": "viewer", "role": "viewer"}
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_logout_clears_the_cookie(app: FastAPI) -> None:
    client = browser(app)
    log_in(client, "viewer", VIEWER_PASSWORD)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    morsel = set_cookie(response)[SESSION_COOKIE]
    assert morsel.value == ""
    assert morsel["max-age"] == "0"
    assert morsel["httponly"] and morsel["secure"] and morsel["path"] == "/"
    assert morsel["samesite"].lower() == "strict"
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 401


# --- Startup checks --------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [None, "", "x" * 31, "é" * 15])
def test_app_refuses_to_start_without_a_32_byte_session_secret(
    store: FakeStore, secret: str | None
) -> None:
    with pytest.raises(RuntimeError, match="SESSION_SECRET is missing or shorter than 32 bytes"):
        create_app(Settings(session_secret=secret), uow_factory=store.uow)
    create_app(Settings(session_secret="é" * 16), uow_factory=store.uow)  # 32 bytes is enough


def test_app_refuses_a_password_hash_that_is_not_argon2id(store: FakeStore) -> None:
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD_HASH is not an argon2 hash") as exc:
        create_app(Settings(admin_password_hash="plaintext-password"), uow_factory=store.uow)
    assert "plaintext-password" not in str(exc.value)
    argon2i = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.I).hash("pw")
    with pytest.raises(RuntimeError, match="VIEWER_PASSWORD_HASH must be an argon2id hash"):
        create_app(Settings(viewer_password_hash=argon2i), uow_factory=store.uow)


def test_settings_repr_hides_the_secrets() -> None:
    settings = Settings(admin_password_hash=ADMIN_HASH)
    assert ADMIN_HASH not in repr(settings) and TEST_SESSION_SECRET not in repr(settings)


def test_unknown_account_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown account"):
        Authenticator({"root": ADMIN_HASH})


# --- make hash-password ----------------------------------------------------------------------


def test_hash_password_prints_a_make_escaped_argon2id_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["s3cret-pass", "s3cret-pass"])
    monkeypatch.setattr(hash_password_cli.getpass, "getpass", lambda prompt: next(answers))
    monkeypatch.setattr(
        hash_password_cli,
        "hash_password",
        PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash,
    )
    assert hash_password_cli.main([]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("$$argon2id$$v=19$$")
    assert PasswordHasher().verify(out.replace("$$", "$"), "s3cret-pass")
    assert "s3cret-pass" not in out


@pytest.mark.parametrize("answers", [["", ""], ["one", "two"]])
def test_hash_password_rejects_empty_or_mismatched(
    monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> None:
    replies = iter(answers)
    monkeypatch.setattr(hash_password_cli.getpass, "getpass", lambda prompt: next(replies))
    assert hash_password_cli.main([]) == 1


def test_hash_password_stdin_prints_a_plain_argon2id_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(hash_password_cli.sys, "stdin", io.StringIO("s3cret-pass\n"))
    monkeypatch.setattr(
        hash_password_cli,
        "hash_password",
        PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash,
    )
    assert hash_password_cli.main(["--stdin"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("$argon2id$v=19$")
    assert PasswordHasher().verify(out, "s3cret-pass")


def test_hash_password_stdin_rejects_an_empty_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hash_password_cli.sys, "stdin", io.StringIO("\n"))
    assert hash_password_cli.main(["--stdin"]) == 1
