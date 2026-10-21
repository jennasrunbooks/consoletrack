"""App factory. Run with `uvicorn --factory app.main:create_app`.

Everything the routes reach through `app.state` (unit of work, clock, intake status) can be
swapped by the caller, which is how unit tests run the real routes on fake repositories. Auth
always comes from `settings`: the app refuses to start without a `SESSION_SECRET` of at least
32 bytes, or with a password hash that isn't argon2id.

With REMEDIATION_APPLY_ENABLED the backend also runs the remediation apply loop
(`app/services/remediation_apply.py`) in its lifespan: the only process that patches the cluster.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from fastapi import FastAPI
from pydantic import SecretStr

from app.api import auth, changes, evidence, incidents, ops, webhooks
from app.api.deps import utcnow
from app.api.errors import register_error_handlers
from app.cluster import KubernetesCluster, PrometheusRatio
from app.db.repositories import UnitOfWork
from app.db.session import make_db_check, make_engine, make_uow_factory
from app.metrics import Metrics
from app.services.auth import MIN_SECRET_BYTES, Authenticator, LoginRateLimiter, SessionSigner
from app.services.intake_health import IntakeStatusSource, UnreportedIntakeStatus
from app.services.remediation_apply import RemediationApplier, run_apply_loop
from app.services.remediation_policy import PolicyConfig, load_policies
from app.settings import Settings, get_settings

log = logging.getLogger("app.main")


def create_app(
    settings: Settings | None = None,
    *,
    uow_factory: Callable[[], UnitOfWork] | None = None,
    db_check: Callable[[], None] | None = None,
    intake_status: IntakeStatusSource | None = None,
    clock: Callable[[], datetime] = utcnow,
    applier: RemediationApplier | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    secret = settings.session_secret.get_secret_value() if settings.session_secret else ""
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise RuntimeError(
            f"SESSION_SECRET is missing or shorter than {MIN_SECRET_BYTES} bytes; generate one"
            " with `openssl rand -hex 32` (see .env.example)"
        )
    try:
        authenticator = Authenticator(
            {
                "admin": _secret(settings.admin_password_hash),
                "viewer": _secret(settings.viewer_password_hash),
            }
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None
    engine = None
    if uow_factory is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set; see .env.example")
        engine = make_engine(settings.database_url)
        uow_factory = make_uow_factory(engine)
        db_check = db_check or make_db_check(engine)
    # A bad policy file stops the backend here, like the worker (remediation_policy.py).
    policies = load_policies(settings.remediation_policies_file)
    stop = threading.Event()
    if applier is None and engine is not None and settings.remediation_apply_enabled:
        applier = _real_applier(settings, uow_factory, policies, clock, stop)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if applier is not None:
            task = asyncio.create_task(
                run_apply_loop(applier, poll_seconds=settings.remediation_poll_seconds, stop=stop)
            )
        yield
        stop.set()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
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
    app.state.sessions = SessionSigner(secret)
    app.state.authenticator = authenticator
    app.state.login_limiter = LoginRateLimiter()
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


def _real_applier(
    settings: Settings,
    uow_factory: Callable[[], UnitOfWork],
    policies: PolicyConfig,
    clock: Callable[[], datetime],
    stop: threading.Event,
) -> RemediationApplier:
    if not settings.prometheus_url:
        log.info("PROMETHEUS_URL is empty: verify waits for the resolved alert only")
    return RemediationApplier(
        uow_factory=uow_factory,
        cluster=KubernetesCluster(),
        ratio=PrometheusRatio(settings.prometheus_url) if settings.prometheus_url else None,
        config=policies,
        allowed_namespaces=settings.allowed_namespaces,
        clock=clock,
        stop=stop,
    )


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None
