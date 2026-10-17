"""Every /api/v1 route requires a session, and every write requires admin.

Walks FastAPI's route table, so a new route added without `Viewer` or `Admin` fails here.
`app.routes` holds included routers unflattened, so the walk goes through
`iter_route_contexts`, which yields each route with its router's prefix and dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts

from app.api.deps import Admin, Viewer, require_admin, require_session
from app.main import create_app
from app.settings import Settings
from tests.unit.fakes import FakeStore

# No session: login itself, and the webhooks, which have their own auth (bearer token, HMAC).
PUBLIC = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/alerts/webhook"),
    ("POST", "/api/v1/integrations/github/workflow-run"),
}
# Writes either role may make.
ANY_ROLE_WRITES = {("POST", "/api/v1/auth/logout")}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _calls(dependency)


def api_routes(app: FastAPI) -> list[Any]:
    return [
        route
        for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, APIRoute) and route.path.startswith("/api/v1")
    ]


def unguarded_routes(app: FastAPI) -> list[str]:
    problems = []
    for route in api_routes(app):
        calls = set(_calls(route.dependant))
        for method in sorted(route.methods):
            key = (method, route.path)
            if key in PUBLIC:
                continue
            if method in WRITE_METHODS and key not in ANY_ROLE_WRITES:
                if require_admin not in calls:
                    problems.append(f"{method} {route.path}: write without require_admin")
            elif not calls & {require_session, require_admin}:
                problems.append(f"{method} {route.path}: no require_session or require_admin")
    return problems


def make_app() -> FastAPI:
    return create_app(Settings(), uow_factory=FakeStore().uow)


def test_every_api_route_requires_a_session_and_writes_require_admin() -> None:
    assert unguarded_routes(make_app()) == []


def test_the_walk_sees_every_operation_in_the_contract() -> None:
    app = make_app()
    walked = {(method, route.path) for route in api_routes(app) for method in route.methods}
    in_schema = {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if path.startswith("/api/v1")
    }
    assert in_schema, "no /api/v1 operations at all"
    assert walked == in_schema


def test_a_route_added_without_a_dependency_fails_the_check() -> None:
    app = make_app()

    @app.get("/api/v1/unguarded")
    def unguarded() -> None: ...

    @app.post("/api/v1/viewer-write")
    def viewer_write(_: Viewer) -> None: ...

    @app.get("/api/v1/admin-read")
    def admin_read(_: Admin) -> None: ...

    @app.get("/not-api")
    def not_api() -> None: ...

    assert unguarded_routes(app) == [
        "GET /api/v1/unguarded: no require_session or require_admin",
        "POST /api/v1/viewer-write: write without require_admin",
    ]
