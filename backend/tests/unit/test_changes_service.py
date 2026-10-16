from __future__ import annotations

from app.enums import ChangeStatus, Via
from app.services.changes import NewChange, record_change
from tests.unit.conftest import ACCOUNT, NOW
from tests.unit.fakes import FakeStore

NEW = NewChange(
    cloudtrail_event_id="evt-dup",
    event_time=NOW,
    principal_arn=f"arn:aws:sts::{ACCOUNT}:assumed-role/demo-app-developer/jdev",
    via=Via.console,
    region="us-east-1",
    service="ec2",
    event_name="AuthorizeSecurityGroupIngress",
    resource_arn=f"arn:aws:ec2:us-east-1:{ACCOUNT}:security-group/sg-0001",
)


def test_record_change_starts_detected_and_dedupes_on_event_id() -> None:
    uow = FakeStore().uow()
    first, created = record_change(uow, NEW)
    assert created
    assert first.status is ChangeStatus.detected
    again, created_again = record_change(uow, NEW)
    assert (again, created_again) == (first, False)
    assert len(uow.store.changes.rows) == 1
