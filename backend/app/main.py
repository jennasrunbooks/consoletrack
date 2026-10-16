"""App factory. Run with `uvicorn --factory app.main:create_app`.

Everything the routes reach through `app.state` (unit of work, clock, sessions, intake status)
can be swapped by the caller, which is how unit tests run the real routes on fake repositories.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from app.api import auth, changes, evidence, incidents, ops, webhooks
from app.api.deps import InMemorySessionStore, SessionStore, utcnow
from app.api.errors import register_error_handlers
from app.db.repositories import UnitOfWork
from app.db.session import make_db_check, make_engine, make_uow_factory
from app.metrics import Metrics
from app.services.intake_health import IntakeStatusSource, UnreportedIntakeStatus
from app.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    uow_factory: Callable[[], UnitOfWork] | None = None,
    db_check: Callable[[], None] | None = None,
    sessions: SessionStore | None = None,
    intake_status: IntakeStatusSource | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> FastAPI:
    settings = settings or get_settings()
    engine = None
    if uow_factory is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set; see .env.example")
        engine = make_engine(settings.database_url)
        uow_factory = make_uow_factory(engine)
        db_check = db_check or make_db_check(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:  # only the engine this factory created
            engine.dispose()

    app = FastAPI(
        title="ConsoleTrack API",
        version="0.1.0",
        # One schema per model, like openapi.yaml, instead of Foo-Input / Foo-Output pairs.
        separate_input_output_schemas=False,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.uow_factory = uow_factory
    app.state.db_check = db_check or (lambda: None)
    app.state.sessions = sessions if sessions is not None else InMemorySessionStore()
    app.state.intake_status = intake_status or UnreportedIntakeStatus()
    app.state.clock = clock
    app.state.metrics = Metrics()

    register_error_handlers(app)
    for router in (
        auth.router,
        changes.router,
        incidents.router,
        evidence.router,
        webhooks.router,
        ops.router,
    ):
        app.include_router(router)
    return app
