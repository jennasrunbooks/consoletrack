"""Login for the two fixed accounts, signed session cookies, and the login rate limit.

**Accounts.** `admin` and `viewer` are fixed; there is no user table. Each logs in with the
password whose argon2id hash is in `ADMIN_PASSWORD_HASH` / `VIEWER_PASSWORD_HASH` (create one
with `make hash-password`). An unset hash means that account can't log in.

**Sessions.** The cookie is the username and an expiry, signed with `SESSION_SECRET`. Nothing is
stored server-side, so logout clears the browser's cookie but can't revoke a copy of it before
it expires (8 hours). Rotating `SESSION_SECRET` ends every session at once. The role is looked
up from the username on each request, never read from the cookie.

**CSRF.** No CSRF tokens in Phase 1: the cookie is `SameSite=Strict`, so the browser doesn't send
it on cross-site requests, and every write endpoint takes JSON only (FastAPI rejects a form or
`text/plain` body with 422), which a cross-site form can't send.
"""

from __future__ import annotations

import hashlib
import logging
import math
import secrets
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from functools import cached_property

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY
from itsdangerous import BadSignature, URLSafeSerializer

from app.enums import Role
from app.schemas import User
from app.services.errors import InvalidCredentials, TooManyLoginAttempts

log = logging.getLogger(__name__)

ACCOUNTS: Mapping[str, Role] = {"admin": Role.admin, "viewer": Role.viewer}
SESSION_TTL = timedelta(hours=8)
MIN_SECRET_BYTES = 32
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=5)

# argon2-cffi's defaults: argon2id with RFC 9106's low-memory profile (t=3, m=64 MiB, p=4).
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def check_password_hash(name: str, value: str) -> None:
    """Fail fast on a value that isn't an argon2id hash. The message never includes the value."""
    try:
        parameters = extract_parameters(value)
    except InvalidHashError:
        raise ValueError(
            f"{name} is not an argon2 hash; create one with `make hash-password`"
        ) from None
    if parameters.type is not Type.ID:
        raise ValueError(f"{name} must be an argon2id hash; create one with `make hash-password`")


class Authenticator:
    def __init__(self, password_hashes: Mapping[str, str | None]) -> None:
        self._hashes: dict[str, str] = {}
        for username, value in password_hashes.items():
            if username not in ACCOUNTS:
                raise ValueError(f"unknown account {username!r}")
            if value:
                check_password_hash(f"{username.upper()}_PASSWORD_HASH", value)
                self._hashes[username] = value
            else:
                log.warning(
                    "%s_PASSWORD_HASH is not set; %s can't log in", username.upper(), username
                )

    @cached_property
    def _dummy_hash(self) -> str:
        """Verified for unknown usernames, so they cost as much as a wrong password.

        Uses a configured hash's parameters; computed on first use, not at startup.
        """
        reference = next(iter(self._hashes.values()), None)
        parameters = extract_parameters(reference) if reference else RFC_9106_LOW_MEMORY
        return PasswordHasher.from_parameters(parameters).hash(secrets.token_urlsafe(16))

    def verify(self, username: str, password: str) -> User:
        """The user, or `InvalidCredentials`: the same error for an unknown user and a wrong
        password. argon2's verify is constant-time in the password."""
        stored = self._hashes.get(username)
        try:
            _hasher.verify(stored or self._dummy_hash, password)
        except (VerificationError, InvalidHashError):
            raise InvalidCredentials() from None
        if stored is None:  # unreachable unless the random dummy password is guessed
            raise InvalidCredentials()
        return User(username=username, role=ACCOUNTS[username])


class SessionSigner:
    """Issues and reads `ct_session` values: `{"u": username, "exp": unix seconds}`, signed."""

    def __init__(self, secret: str, ttl: timedelta = SESSION_TTL) -> None:
        if len(secret.encode()) < MIN_SECRET_BYTES:
            raise ValueError(f"the session secret must be at least {MIN_SECRET_BYTES} bytes")
        self.ttl = ttl
        self._serializer = URLSafeSerializer(
            secret, salt="ct-session", signer_kwargs={"digest_method": hashlib.sha256}
        )

    def issue(self, user: User, now: datetime) -> str:
        return self._serializer.dumps(
            {"u": user.username, "exp": int((now + self.ttl).timestamp())}
        )

    def read(self, token: str, now: datetime) -> User | None:
        """The session's user, or None if the token is tampered with, malformed, or expired."""
        try:
            payload = self._serializer.loads(token)
        except BadSignature:
            return None
        if not isinstance(payload, dict):
            return None
        username, expires = payload.get("u"), payload.get("exp")
        if not isinstance(username, str) or username not in ACCOUNTS:
            return None
        if not isinstance(expires, int) or now.timestamp() >= expires:
            return None
        return User(username=username, role=ACCOUNTS[username])


class LoginRateLimiter:
    """At most `MAX_FAILED_LOGINS` failures per key (username, client IP) in a sliding window.

    In memory, which is right for the single backend replica: a restart forgets the counts, and
    running a second replica would need a shared store (Postgres or Redis) instead.
    """

    def __init__(
        self, max_failures: int = MAX_FAILED_LOGINS, window: timedelta = FAILED_LOGIN_WINDOW
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()  # sync routes run in a thread pool

    def _prune(self, key: str, now: datetime) -> deque[datetime] | None:
        failures = self._failures.get(key)
        while failures and failures[0] <= now - self.window:
            failures.popleft()
        if failures is not None and not failures:
            del self._failures[key]
            return None
        return failures

    def check(self, keys: Iterable[str], now: datetime) -> None:
        """Raise `TooManyLoginAttempts` if any key is at the limit."""
        with self._lock:
            retry_after = 0.0
            for key in keys:
                failures = self._prune(key, now)
                if failures is not None and len(failures) >= self.max_failures:
                    reset = failures[-self.max_failures] + self.window
                    retry_after = max(retry_after, (reset - now).total_seconds())
            if retry_after:
                raise TooManyLoginAttempts(max(1, math.ceil(retry_after)))

    def record_failure(self, keys: Iterable[str], now: datetime) -> None:
        with self._lock:
            for key in keys:
                self._failures.setdefault(key, deque()).append(now)
            # Keep memory bounded: drop every key whose failures have all aged out.
            for key in list(self._failures):
                self._prune(key, now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def login(
    authenticator: Authenticator,
    limiter: LoginRateLimiter,
    *,
    username: str,
    password: str,
    client_ip: str,
    now: datetime,
) -> User:
    """Check the rate limit, then the password. Logs the outcome and the username, never the
    password or the client's IP address (docs/observability.md → Privacy)."""
    user_key, ip_key = f"user:{username}", f"ip:{client_ip}"
    try:
        limiter.check((user_key, ip_key), now)
    except TooManyLoginAttempts:
        log.warning("login rate-limited username=%r", username)
        raise
    try:
        user = authenticator.verify(username, password)
    except InvalidCredentials:
        limiter.record_failure((user_key, ip_key), now)
        log.warning("login failed username=%r", username)
        raise
    # A success resets the username's count but not the IP's, so logging in as viewer can't
    # reset the counter for guesses at admin from the same address.
    limiter.clear(user_key)
    log.info("login succeeded username=%r", username)
    return user
