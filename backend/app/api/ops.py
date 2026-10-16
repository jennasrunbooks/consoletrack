"""Intake health, stats, and the probe and metrics endpoints."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.api.deps import Now, Uow, Viewer, get_settings
from app.api.errors import NotImplementedYet, error_responses
from app.schemas import IntakeHealth, ProbeStatus, Stats
from app.services.intake_health import get_intake_health
from app.settings import IntakeMode, Settings

log = logging.getLogger(__name__)
router = APIRouter()
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _intake_health(request: Request, uow: Uow, now: datetime, settings: Settings) -> IntakeHealth:
    return get_intake_health(
        uow,
        request.app.state.intake_status,
        now=now,
        region=settings.aws_region,
        replay=settings.intake_mode is IntakeMode.replay,
        grafana_url=settings.grafana_url,
    )


@router.get(
    "/api/v1/intake/health",
    tags=["intake"],
    operation_id="getIntakeHealth",
    response_model=IntakeHealth,
    responses=error_responses(401),
)
def intake_health(
    request: Request, uow: Uow, now: Now, settings: SettingsDep, _: Viewer
) -> IntakeHealth:
    """Whether AWS change events are reaching ConsoleTrack."""
    return _intake_health(request, uow, now, settings)


@router.get(
    "/api/v1/stats",
    tags=["stats"],
    operation_id="getStats",
    response_model=Stats,
    responses=error_responses(400, 401, 422, 501),
    openapi_extra={"x-phase": 2},
)
def stats(_: Viewer, since: datetime | None = None, until: datetime | None = None) -> Stats:
    """Change analytics (Phase 2)."""
    raise NotImplementedYet("GET /api/v1/stats", "Phase 2")


@router.get(
    "/metrics",
    tags=["ops"],
    operation_id="getMetrics",
    response_class=Response,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
)
def metrics(request: Request, uow: Uow, now: Now, settings: SettingsDep) -> Response:
    """Prometheus metrics. Served inside the cluster only; the Gateway routes /api, not /metrics."""
    request.app.state.metrics.observe_intake(_intake_health(request, uow, now, settings))
    return Response(
        request.app.state.metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get(
    "/healthz",
    tags=["ops"],
    operation_id="healthz",
    response_model=ProbeStatus,
    response_model_exclude_none=True,
)
def healthz() -> ProbeStatus:
    """Liveness probe."""
    return ProbeStatus(status="ok")


@router.get(
    "/readyz",
    tags=["ops"],
    operation_id="readyz",
    response_model=ProbeStatus,
    response_model_exclude_none=True,
    responses={503: {"model": ProbeStatus, "description": "Not ready."}},
)
def readyz(request: Request) -> ProbeStatus | JSONResponse:
    """Readiness probe (checks the database)."""
    try:
        request.app.state.db_check()
    except Exception:
        log.exception("readiness check: database unreachable")
        body = ProbeStatus(status="error", checks={"database": "error"})
        return JSONResponse(body.model_dump(mode="json"), status_code=503)
    return ProbeStatus(status="ok", checks={"database": "ok"})
