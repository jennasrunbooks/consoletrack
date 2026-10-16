"""Request dependencies: the unit of work, the clock, auth, and the webhook bearer token.

**Auth placeholder.** The real admin/viewer login (`POST /api/v1/auth/login`) ships with the
auth work. Until then sessions live in an in-memory `SessionStore` that nothing in production
fills, so every request without a session gets 401: reads and writes fail closed. The
dependency interface (`current_user`, `require_user`, `require_admin`) is what the login work
keeps; only the store behind it changes.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated, Protocol

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from app.db.repositories import UnitOfWork
from app.enums import Role
from app.schemas import User
from app.settings import Settings

SESSION_COOKIE = "ct_session"

# scheme_name values are the securitySchemes names in openapi.yaml. auto_error=False so a
# missing credential becomes our own 401 Error body instead of FastAPI's default.
session_cookie = APIKeyCookie(name=SESSION_COOKIE, scheme_name="sessionCookie", auto_error=False)
webhook_bearer = HTTPBearer(scheme_name="webhookBearer", auto_error=False)


class Unauthorized(Exception):
    pass


class Forbidden(Exception):
    pass


class SessionStore(Protocol):
    def get(self, token: str) -> User | None: ...


class InMemorySessionStore:
    """Placeholder until login exists; tests put sessions here."""

    def __init__(self) -> None:
        self._sessions: dict[str, User] = {}

    def get(self, token: str) -> User | None:
        return self._sessions.get(token)

    def put(self, token: str, user: User) -> None:
        self._sessions[token] = user


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_uow(request: Request) -> Iterator[UnitOfWork]:
    factory: Callable[[], UnitOfWork] = request.app.state.uow_factory
    with factory() as uow:
        yield uow


def get_now(request: Request) -> datetime:
    clock: Callable[[], datetime] = request.app.state.clock
    return clock()


def utcnow() -> datetime:
    return datetime.now(UTC)


def current_user(
    request: Request, token: Annotated[str | None, Security(session_cookie)]
) -> User | None:
    if not token:
        return None
    store: SessionStore = request.app.state.sessions
    return store.get(token)


def require_user(user: Annotated[User | None, Depends(current_user)]) -> User:
    if user is None:
        raise Unauthorized("log in first")
    return user


def require_admin(user: Annotated[User | None, Security(current_user, scopes=["admin"])]) -> User:
    if user is None:
        raise Unauthorized("log in first")
    if user.role is not Role.admin:
        raise Forbidden("this action requires the admin role")
    return user


def require_webhook_token(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(webhook_bearer)],
) -> None:
    expected = settings.alert_webhook_token
    if expected is None or not expected.get_secret_value():
        raise Unauthorized("ALERT_WEBHOOK_TOKEN is not configured")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), expected.get_secret_value().encode()
    ):
        raise Unauthorized("missing or invalid bearer token")


Uow = Annotated[UnitOfWork, Depends(get_uow)]
Now = Annotated[datetime, Depends(get_now)]
Viewer = Annotated[User, Depends(require_user)]
Admin = Annotated[User, Depends(require_admin)]
