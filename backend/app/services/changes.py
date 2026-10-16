"""The change log: recording, listing, reading, and the Accept decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.db.models import ChangeRow
from app.db.repositories import ChangeFilters, UnitOfWork
from app.enums import ChangeStatus, Risk, Via
from app.schemas import Change, ChangeCounts, ChangePage
from app.services import change_state
from app.services.change_state import ChangeEvent
from app.services.errors import NotFound
from app.services.views import change_detail, change_summaries

CLOSED_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class NewChange:
    """A parsed out-of-band write, as the intake (#9) hands it over."""

    cloudtrail_event_id: str
    event_time: datetime
    principal_arn: str
    via: Via
    region: str
    service: str
    event_name: str
    resource_arn: str
    session_name: str | None = None
    source_ip: str | None = None
    user_agent: str | None = None
    resource_name: str | None = None
    tf_address: str | None = None
    request_parameters: dict[str, Any] | None = None
    drift: dict[str, Any] | None = None
    risk: Risk | None = None


def record_change(uow: UnitOfWork, new: NewChange) -> tuple[ChangeRow, bool]:
    """Store a change in `detected`, deduplicated on the CloudTrail event ID.

    Returns (row, created). The caller commits.
    """
    existing = uow.changes.get_by_event_id(new.cloudtrail_event_id)
    if existing is not None:
        return existing, False
    row = ChangeRow(**vars(new), status=change_state.INITIAL)
    return uow.changes.add(row), True


def list_changes(
    uow: UnitOfWork, filters: ChangeFilters, *, page: int, page_size: int, now: datetime
) -> ChangePage:
    rows, total = uow.changes.list(filters, offset=(page - 1) * page_size, limit=page_size)
    # The status chips keep their numbers while one status is selected.
    data = uow.changes.counts(filters.without_status(), closed_since=now - CLOSED_WINDOW)
    by_status = {status: data.by_status.get(status, 0) for status in ChangeStatus}
    return ChangePage(
        items=change_summaries(uow, rows),
        total=total,
        page=page,
        page_size=page_size,
        counts=ChangeCounts(
            by_status=by_status,
            total=sum(by_status.values()),
            awaiting_review=by_status[ChangeStatus.awaiting_review],
            high_risk_open=data.high_risk_open,
            linked_to_incident=data.linked_to_incident,
            closed_last_7_days=data.closed_since,
        ),
    )


def get_change(uow: UnitOfWork, change_id: int) -> Change:
    row = uow.changes.get(change_id)
    if row is None:
        raise NotFound("change", change_id)
    return change_detail(uow, row)


def accept_change(
    uow: UnitOfWork, change_id: int, *, note: str, actor: str, now: datetime
) -> Change:
    """Close the change as `accepted` with the admin's note as the reason."""
    row = uow.changes.get(change_id, for_update=True)
    if row is None:
        raise NotFound("change", change_id)
    change_state.apply(row, ChangeEvent.accepted, now=now, actor=actor, reason=note)
    uow.commit()
    return change_detail(uow, row)
