"""Request dependencies: the unit of work, the clock, auth, and the webhook bearer token.

**Auth.** `require_session` (the `Viewer` alias) guards every read and `require_admin` (`Admin`)
every write; `tests/unit/test_route_auth.py` fails if an `/api/v1` route has neither. The
session cookie is signed and read by `app.services.auth.SessionSigner`; a missing, tampered,
or expired cookie is 401, and a viewer on a write is 403.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from app.db.repositories import UnitOfWork
from app.enums import Role
from app.schemas import User
from app.services.auth import Authenticator, LoginRateLimiter, SessionSigner
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


def get_sessions(request: Request) -> SessionSigner:
    return request.app.state.sessions


def get_authenticator(request: Request) -> Authenticator:
    return request.app.state.authenticator


def get_login_limiter(request: Request) -> LoginRateLimiter:
    return request.app.state.login_limiter


def client_ip(request: Request) -> str:
    # The direct peer. Behind the Gateway or an ALB this is the proxy unless uvicorn runs with
    # --proxy-headers and --forwarded-allow-ips set to the proxy, which rewrites it from
    # X-Forwarded-For.
    return request.client.host if request.client else "unknown"


def current_user(
    sessions: Annotated[SessionSigner, Depends(get_sessions)],
    now: Annotated[datetime, Depends(get_now)],
    token: Annotated[str | None, Security(session_cookie)],
) -> User | None:
    if not token:
        return None
    return sessions.read(token, now)


def require_session(user: Annotated[User | None, Depends(current_user)]) -> User:
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
Viewer = Annotated[User, Depends(require_session)]
Admin = Annotated[User, Depends(require_admin)]
