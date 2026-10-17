"""/api/v1/auth: login, logout, and the current user (app/services/auth.py has the details)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import (
    SESSION_COOKIE,
    Now,
    Viewer,
    client_ip,
    get_authenticator,
    get_login_limiter,
    get_sessions,
)
from app.api.errors import error_responses
from app.schemas import LoginRequest, User
from app.services import auth
from app.services.auth import Authenticator, LoginRateLimiter, SessionSigner

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# The flags openapi.yaml documents for ct_session. Secure stays on everywhere: browsers treat
# http://localhost as a secure context, and a public deployment is HTTPS only.
COOKIE_FLAGS = {"path": "/", "secure": True, "httponly": True, "samesite": "strict"}


@router.post(
    "/login",
    operation_id="login",
    response_model=User,
    responses=error_responses(400, 401, 422, 429),
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    now: Now,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
    limiter: Annotated[LoginRateLimiter, Depends(get_login_limiter)],
    sessions: Annotated[SessionSigner, Depends(get_sessions)],
) -> User:
    """Log in and receive a session cookie."""
    user = auth.login(
        authenticator,
        limiter,
        username=body.username,
        password=body.password,
        client_ip=client_ip(request),
        now=now,
    )
    response.set_cookie(
        SESSION_COOKIE,
        sessions.issue(user, now),
        max_age=int(sessions.ttl.total_seconds()),
        **COOKIE_FLAGS,
    )
    return user


@router.post("/logout", operation_id="logout", status_code=204, responses=error_responses(401))
def logout(_: Viewer, response: Response) -> None:
    """End the current session."""
    response.delete_cookie(SESSION_COOKIE, **COOKIE_FLAGS)


@router.get(
    "/me", operation_id="getCurrentUser", response_model=User, responses=error_responses(401)
)
def me(user: Viewer) -> User:
    """The logged-in account and its role."""
    return user
