"""The policy gate: auto for the graduated OOM fix, manual for everything else, and every reason a
proposal stays with a human."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.agents.schema import RevertWorkflow, SetMemoryLimit
from app.db.models import IncidentRow, RemediationRow
from app.enums import (
    Confidence,
    IncidentStatus,
    RemediationKind,
    RemediationMode,
    RemediationStatus,
    SubjectType,
)
from app.services import incident_state
from app.services.incident_state import IncidentEvent
from app.services.policy_gate import ConfiguredPolicyGate, ManualPolicyGate
from app.services.remediation_policy import PolicyConfig
from app.settings import Settings
from tests.unit.conftest import NOW, add_analysis
from tests.unit.fakes import FakeStore
from tests.unit.remediation_fakes import OOM_PROPOSAL, policies, shipped_policies

PROPOSAL = SetMemoryLimit.model_validate(OOM_PROPOSAL)


def gate_settings(**fields: Any) -> Settings:
    return Settings(autofix_enabled=True, k8s_allowed_namespaces="demo", **fields)


def gate(
    store: FakeStore,
    config: PolicyConfig | None = None,
    settings: Settings | None = None,
    now: Any = NOW,
) -> ConfiguredPolicyGate:
    return ConfiguredPolicyGate(
        uow_factory=store.uow,
        config=config or shipped_policies(),
        settings=settings or gate_settings(),
        clock=lambda: now,
    )


def triaged(
    store: FakeStore,
    make_incident: Any,
    *,
    proposal: dict[str, Any] = OOM_PROPOSAL,
    confidence: Confidence = Confidence.high,
) -> tuple[IncidentRow, RemediationRow]:
    """An incident in awaiting_review with the agent's analysis and a proposed remediation, as
    record_triage leaves it."""
    incident = make_incident(alertname="DemoAppHighErrorRate")
    incident_state.apply(incident, IncidentEvent.triage_started, now=NOW)
    incident_state.apply(incident, IncidentEvent.analysis_written, now=NOW)
    add_analysis(store, SubjectType.incident, incident.id, confidence=confidence)
    row = store.remediations.insert(
        RemediationRow(
            subject_type=SubjectType.incident,
            subject_id=incident.id,
            kind=RemediationKind(proposal["kind"]),
            payload_json=dict(proposal),
            status=RemediationStatus.proposed,
        )
    )
    return incident, row


def test_the_oom_fix_is_approved_automatically(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    decision = gate(store).submit_proposal(incident.id, PROPOSAL)
    assert decision.approved and decision.mode is RemediationMode.auto
    assert decision.policy == "oom-memory-bump"
    assert row.status is RemediationStatus.approved
    assert (row.mode, row.policy) == (RemediationMode.auto, "oom-memory-bump")
    assert row.decided_by == "policy:oom-memory-bump" and row.decided_at == NOW
    assert (row.namespace, row.workload) == ("demo", "demo-app")
    assert incident.status is IncidentStatus.remediating


def assert_manual(
    store: FakeStore,
    incident: IncidentRow,
    row: RemediationRow,
    reason: str,
    **gate_args: Any,
) -> None:
    decision = gate(store, **gate_args).submit_proposal(incident.id, PROPOSAL)
    assert not decision.approved and decision.mode is RemediationMode.manual
    assert reason in decision.reason
    assert row.status is RemediationStatus.proposed and row.decided_by is None
    assert row.mode is RemediationMode.manual and reason in (row.gate_reason or "")
    assert incident.status is IncidentStatus.awaiting_review


def test_kill_switch_in_the_policy_file(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    assert_manual(
        store, incident, row, "remediation.autoEnabled is false", config=policies(autoEnabled=False)
    )


def test_kill_switch_env(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    off = Settings(autofix_enabled=False, k8s_allowed_namespaces="demo")
    assert_manual(store, incident, row, "AUTOFIX_ENABLED is false", settings=off)


def test_the_kill_switch_is_off_unless_set(
    store: FakeStore, make_incident: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `make test` exports .env, which may set it for make local.
    monkeypatch.delenv("AUTOFIX_ENABLED", raising=False)
    incident, row = triaged(store, make_incident)
    assert not Settings().autofix_enabled
    assert_manual(store, incident, row, "AUTOFIX_ENABLED", settings=Settings())


def test_a_manual_policy_stays_manual(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    assert_manual(
        store, incident, row, "policy oom-memory-bump is manual", config=policies(mode="manual")
    )


def test_no_policies_means_manual(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    assert_manual(store, incident, row, "policy default is manual", config=PolicyConfig())


def test_medium_confidence_is_approved(store: FakeStore, make_incident: Any) -> None:
    """The floor is medium (docs/remediation-graduation.md, #60): the agent's high/medium varied
    between identical eval runs while its proposal was right every time."""
    incident, _ = triaged(store, make_incident, confidence=Confidence.medium)
    decision = gate(store).submit_proposal(incident.id, PROPOSAL)
    assert decision.approved and decision.mode is RemediationMode.auto


def test_low_confidence_stays_manual(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident, confidence=Confidence.low)
    assert_manual(store, incident, row, "confidence is low; policy oom-memory-bump needs medium")


def test_validator_refusal_stays_manual(store: FakeStore, make_incident: Any) -> None:
    too_high = {**OOM_PROPOSAL, "memory_limit": "1Gi"}
    incident, row = triaged(store, make_incident, proposal=too_high)
    decision = gate(store).submit_proposal(incident.id, SetMemoryLimit.model_validate(too_high))
    assert decision.mode is RemediationMode.manual
    assert "validator" in decision.reason and "512Mi ceiling" in decision.reason
    assert row.status is RemediationStatus.proposed


def test_namespace_outside_the_allowlist_stays_manual(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    narrow = Settings(autofix_enabled=True, k8s_allowed_namespaces="other")
    assert_manual(store, incident, row, "K8S_ALLOWED_NAMESPACES", settings=narrow)


def earlier_auto(
    store: FakeStore,
    *,
    status: RemediationStatus,
    decided_at: Any,
    steps: list[tuple[str, bool]] = (),  # type: ignore[assignment]
) -> None:
    store.remediations.insert(
        RemediationRow(
            subject_type=SubjectType.incident,
            subject_id=999,
            kind=RemediationKind.set_memory_limit,
            payload_json=dict(OOM_PROPOSAL),
            status=status,
            mode=RemediationMode.auto,
            namespace="demo",
            workload="demo-app",
            decided_at=decided_at,
            steps=[{"step": n, "at": "", "ok": ok, "detail": ""} for n, ok in steps],
        )
    )


def test_rate_limit_one_auto_fix_per_workload_per_hour(
    store: FakeStore, make_incident: Any
) -> None:
    earlier_auto(store, status=RemediationStatus.succeeded, decided_at=NOW - timedelta(minutes=30))
    incident, row = triaged(store, make_incident)
    assert_manual(store, incident, row, "rate limit: 1 auto fix(es) of demo/demo-app")


def test_rate_limit_window_passes(store: FakeStore, make_incident: Any) -> None:
    earlier_auto(store, status=RemediationStatus.succeeded, decided_at=NOW - timedelta(minutes=61))
    incident, row = triaged(store, make_incident)
    assert gate(store).submit_proposal(incident.id, PROPOSAL).approved


def test_a_failed_auto_fix_turns_auto_off_for_that_workload(
    store: FakeStore, make_incident: Any
) -> None:
    earlier_auto(store, status=RemediationStatus.failed, decided_at=NOW - timedelta(days=3))
    incident, row = triaged(store, make_incident)
    assert_manual(store, incident, row, "failed before")


def test_an_interrupted_auto_fix_that_rolled_back_doesnt_count_as_failed(
    store: FakeStore, make_incident: Any
) -> None:
    # A backend restart cut it off after the patch; the next backend rolled it back (#105).
    earlier_auto(
        store,
        status=RemediationStatus.failed,
        decided_at=NOW - timedelta(days=3),
        steps=[("patch", True), ("interrupted", False), ("rollback", True), ("failed", False)],
    )
    incident, row = triaged(store, make_incident)
    assert gate(store).submit_proposal(incident.id, PROPOSAL).approved


def test_aws_kinds_are_never_auto(store: FakeStore, make_incident: Any) -> None:
    proposal = {"kind": "revert_workflow", "change_id": 1, "reason": "revert"}
    incident, row = triaged(store, make_incident, proposal=proposal)
    decision = gate(store).submit_proposal(incident.id, RevertWorkflow.model_validate(proposal))
    assert decision.mode is RemediationMode.manual and decision.policy == "default"
    assert row.status is RemediationStatus.proposed
    assert (row.namespace, row.workload) == (None, None)


def test_an_incident_resolved_meanwhile_stays_manual(store: FakeStore, make_incident: Any) -> None:
    incident, row = triaged(store, make_incident)
    incident_state.apply(incident, IncidentEvent.alert_resolved, now=NOW)
    decision = gate(store).submit_proposal(incident.id, PROPOSAL)
    assert decision.mode is RemediationMode.manual and "resolved" in decision.reason
    assert incident.status is IncidentStatus.resolved


@pytest.mark.parametrize("status", [RemediationStatus.approved, RemediationStatus.rejected])
def test_only_a_proposed_remediation_is_decided(
    store: FakeStore, make_incident: Any, status: RemediationStatus
) -> None:
    incident, row = triaged(store, make_incident)
    row.status = status
    decision = gate(store).submit_proposal(incident.id, PROPOSAL)
    assert decision.mode is RemediationMode.manual
    assert "no proposed remediation" in decision.reason
    assert row.status is status


def test_manual_gate_never_approves() -> None:
    decision = ManualPolicyGate().submit_proposal(1, PROPOSAL)
    assert decision.mode is RemediationMode.manual and not decision.approved
