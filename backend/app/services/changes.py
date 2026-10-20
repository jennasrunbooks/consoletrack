"""The change log: recording, listing, reading, and the Accept decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.agents.schema import SubmitAnalysis
from app.db.models import AnalysisRow, ChangeRow
from app.db.repositories import ChangeFilters, UnitOfWork
from app.enums import ChangeStatus, Confidence, Risk, SubjectType, Via
from app.schemas import Change, ChangeCounts, ChangePage
from app.services import change_state
from app.services.change_state import ChangeEvent
from app.services.errors import NotFound
from app.services.views import change_detail, change_summaries

CLOSED_WINDOW = timedelta(days=7)
RISK_ORDER = [Risk.low, Risk.medium, Risk.high]


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


def record_change_analysis(
    uow: UnitOfWork,
    change_id: int,
    *,
    run_id: int,
    analysis: SubmitAnalysis | None,
    stopped: str,
    now: datetime,
) -> None:
    """Store an agent run's result for a change.

    With an analysis: the analysis row, the agent's title and risk on the change, and
    `detected → awaiting_review`. Without one (a limit, a failure): an analysis row that says
    why, and the change stays `detected`, where a reviewer can still Accept it. A change that was
    already reviewed keeps its state.
    """
    row = uow.changes.get(change_id, for_update=True)
    if row is None:
        raise NotFound("change", change_id)
    if analysis is None:
        uow.analyses.add(
            AnalysisRow(
                subject_type=SubjectType.change,
                subject_id=change_id,
                agent_run_id=run_id,
                summary=(
                    f"The agent stopped before submitting an analysis: {stopped}. "
                    "Review the change from its CloudTrail record."
                ),
                confidence=Confidence.low,
                flags=[],
            )
        )
        uow.commit()
        return
    uow.analyses.add(
        AnalysisRow(
            subject_type=SubjectType.change,
            subject_id=change_id,
            agent_run_id=run_id,
            summary=analysis.summary,
            root_cause=analysis.root_cause,
            risk=analysis.risk,
            risk_reason=analysis.risk_reason,
            confidence=analysis.confidence,
            flags=[flag.value for flag in analysis.flags],
        )
    )
    row.title = analysis.title or row.title
    # The agent can raise the intake's risk rating, never lower it (e.g. a pipeline-role call
    # without a gh- session name stays high).
    row.risk = max(row.risk or Risk.low, analysis.risk, key=RISK_ORDER.index)
    if row.status is ChangeStatus.detected:
        change_state.apply(row, ChangeEvent.analysis_written, now=now)
    uow.commit()
