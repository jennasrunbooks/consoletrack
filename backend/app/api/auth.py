"""/api/v1/auth: login and logout ship with the auth work; /me reads the placeholder session."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Viewer
from app.api.errors import NotImplementedYet, error_responses
from app.schemas import LoginRequest, User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
AUTH_WORK = "the admin/viewer login work (no issue yet)"


@router.post(
    "/login",
    operation_id="login",
    response_model=User,
    responses=error_responses(400, 401, 422),
)
def login(body: LoginRequest) -> User:
    """Log in and receive a session cookie."""
    raise NotImplementedYet("POST /api/v1/auth/login", AUTH_WORK)


@router.post("/logout", operation_id="logout", status_code=204, responses=error_responses(401))
def logout(_: Viewer) -> None:
    """End the current session."""
    raise NotImplementedYet("POST /api/v1/auth/logout", AUTH_WORK)


@router.get(
    "/me", operation_id="getCurrentUser", response_model=User, responses=error_responses(401)
)
def me(user: Viewer) -> User:
    """The logged-in account and its role."""
    return user
