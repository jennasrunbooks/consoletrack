"""The backend's apply loop: validate, dry run, patch, rollout, verify, and roll back on failure."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agents.schema import SetMemoryLimit
from app.cluster import ClusterError
from app.db.models import IncidentRow, RemediationRow
from app.enums import (
    IncidentStatus,
    RemediationMode,
    RemediationStatus,
)
from app.services import incident_state
from app.services.incident_state import IncidentEvent
from app.services.remediation_apply import RemediationApplier, _oom_killed_since, run_apply_loop
from app.services.remediation_policy import PolicyConfig
from tests.unit.fakes import FakeStore
from tests.unit.remediation_fakes import (
    OOM_PROPOSAL,
    Clock,
    FakeCluster,
    FakeRatio,
    deployment,
    policies,
    shipped_policies,
)
from tests.unit.test_policy_gate import gate, triaged


@pytest.fixture
def clock() -> Clock:
    return Clock()


def applier(
    store: FakeStore,
    cluster: FakeCluster,
    clock: Clock,
    *,
    ratio: FakeRatio | None = None,
    config: PolicyConfig | None = None,
) -> RemediationApplier:
    return RemediationApplier(
        uow_factory=store.uow,
        cluster=cluster,
        ratio=ratio if ratio is not None else FakeRatio(0.4, 0.2, 0.01),
        config=config or shipped_policies(),
        allowed_namespaces=frozenset({"demo"}),
        clock=clock,
        wait=clock.wait,
    )


def auto_approved(
    store: FakeStore, make_incident: Any, clock: Clock, **proposal: Any
) -> tuple[IncidentRow, RemediationRow]:
    """Triaged, then approved by the gate exactly as the worker does it."""
    payload = {**OOM_PROPOSAL, **proposal}
    incident, row = triaged(store, make_incident, proposal=payload)
    decision = gate(store, now=clock.now).submit_proposal(
        incident.id, SetMemoryLimit.model_validate(payload)
    )
    assert decision.approved
    return incident, row


def steps(row: RemediationRow) -> list[tuple[str, bool]]:
    return [(step["step"], step["ok"]) for step in row.steps]


def test_nothing_to_apply(store: FakeStore, clock: Clock) -> None:
    assert not applier(store, FakeCluster(clock=clock), clock).run_next()


def test_auto_fix_applies_and_verifies(store: FakeStore, make_incident: Any, clock: Clock) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    ratio = FakeRatio(0.4, 0.2, 0.01)
    assert applier(store, cluster, clock, ratio=ratio).run_next()

    assert row.status is RemediationStatus.succeeded
    assert cluster.limit == "256Mi"
    # Dry run first, then the same patch for real, then the annotations off the Deployment.
    assert [dry for _, dry in cluster.patches] == [True, False, False]
    assert cluster.patches[0][0] == cluster.patches[1][0]
    # The new revision is marked as this remediation's patch (#79); the Deployment isn't, so a
    # later human rollout's ReplicaSet won't be.
    assert cluster.revision_annotations == [
        {"consoletrack.io/remediation": str(row.id), "consoletrack.io/action": "patch"}
    ]
    assert cluster.patches[2][0] == {
        "metadata": {
            "annotations": {"consoletrack.io/remediation": None, "consoletrack.io/action": None}
        }
    }
    assert cluster.obj["metadata"]["annotations"] == {}
    assert row.before_json == {"limits": {"memory": "128Mi"}, "requests": {"memory": "128Mi"}}
    assert row.after_json == {"limits": {"memory": "256Mi"}, "requests": {"memory": "128Mi"}}
    assert row.applied_at is not None and row.lease_until is None
    assert steps(row) == [
        ("claimed", True),
        ("read", True),
        ("validate", True),
        ("dry_run", True),
        ("patch", True),
        ("rollout", True),
        ("verify", True),
        ("succeeded", True),
    ]
    assert "error ratio 0.010 is below 0.05" in row.steps[-1]["detail"]
    assert ratio.queries and "demo_client_requests_total" in ratio.queries[0]
    # Alertmanager's resolved notification closes the incident, not the loop.
    assert incident.status is IncidentStatus.remediating


def test_verify_passes_when_the_alert_resolves(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    incident_state.apply(incident, IncidentEvent.alert_resolved, now=clock.now)
    applier(store, FakeCluster(clock=clock), clock, ratio=FakeRatio(None)).run_next()
    assert row.status is RemediationStatus.succeeded
    assert row.steps[-1]["detail"] == "the alert resolved"


def test_verify_fails_on_a_new_oom_kill_and_rolls_back(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock, oom_after_patch=True)
    applier(store, cluster, clock).run_next()

    assert row.status is RemediationStatus.failed
    assert "OOMKilled again" in (row.error or "") and "rolled back" in (row.error or "")
    # The inverse patch put the faulted values back.
    assert cluster.limit == "128Mi"
    # Dry run, patch, clear; then the rollback and its clear.
    assert [dry for _, dry in cluster.patches] == [True, False, False, False, False]
    # Both revisions say they're ConsoleTrack's: triage must not read the rollback as a human
    # lowering the limit, or propose rolling back to the revision that just OOMed (#79).
    assert cluster.revision_annotations == [
        {"consoletrack.io/remediation": str(row.id), "consoletrack.io/action": "patch"},
        {"consoletrack.io/remediation": str(row.id), "consoletrack.io/action": "rollback"},
    ]
    assert cluster.obj["metadata"]["annotations"] == {}
    assert steps(row)[-3:] == [("verify", False), ("rollback", True), ("failed", False)]
    assert incident.status is IncidentStatus.awaiting_review


def test_a_failed_annotation_clear_doesnt_fail_the_fix(
    store: FakeStore, make_incident: Any, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    _, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    cluster.fail_on.add("clear")
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.succeeded
    assert "couldn't remove the remediation annotations" in caplog.text


def test_the_rollback_clears_its_annotations_even_if_the_controller_lags(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    """The rollback waits up to ANNOTATION_SETTLE for the controller to see it, then clears the
    Deployment's annotations regardless, so a later human rollout never inherits them."""

    class LaggingAfterRollback(FakeCluster):
        def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
            if len(self.revision_annotations) == 2:  # the rollback is in; never observed
                self.rollout_polls = None
            return super().get_deployment(namespace, name)

    _, row = auto_approved(store, make_incident, clock)
    cluster = LaggingAfterRollback(clock=clock, oom_after_patch=True)
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    status = cluster.obj["status"]
    assert status["observedGeneration"] < cluster.obj["metadata"]["generation"]
    assert cluster.obj["metadata"]["annotations"] == {}


def test_after_a_failed_auto_fix_that_workload_gets_no_more_auto_attempts(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    auto_approved(store, make_incident, clock)
    applier(store, FakeCluster(clock=clock, oom_after_patch=True), clock).run_next()
    clock.now += timedelta(hours=2)  # past the rate limit
    incident, row = triaged(store, make_incident)
    decision = gate(store, now=clock.now).submit_proposal(
        incident.id, SetMemoryLimit.model_validate(OOM_PROPOSAL)
    )
    assert decision.mode is RemediationMode.manual and "failed before" in decision.reason
    assert row.status is RemediationStatus.proposed


def test_verify_times_out_and_rolls_back(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    start = clock.now
    applier(store, cluster, clock, ratio=FakeRatio(0.3)).run_next()
    assert row.status is RemediationStatus.failed
    assert "not resolved within 0:05:00" in (row.error or "")
    assert clock.now - start >= timedelta(minutes=5)
    assert cluster.limit == "128Mi"
    assert incident.status is IncidentStatus.awaiting_review


def test_rollout_timeout_rolls_back(store: FakeStore, make_incident: Any, clock: Clock) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock, rollout_polls=None)
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    assert "rollout didn't finish within 0:03:00" in (row.error or "")
    assert ("rollout", False) in steps(row) and ("rollback", True) in steps(row)
    assert cluster.limit == "128Mi"
    assert incident.status is IncidentStatus.awaiting_review


def test_a_failed_rollback_is_recorded(store: FakeStore, make_incident: Any, clock: Clock) -> None:
    _, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock, oom_after_patch=True)
    original = cluster.patch_deployment

    def patch(namespace: str, name: str, body: dict[str, Any], *, dry_run: bool) -> Any:
        if len(cluster.patches) == 2:
            cluster.fail_on.add("patch")
        return original(namespace, name, body, dry_run=dry_run)

    cluster.patch_deployment = patch  # type: ignore[method-assign]
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    assert "rollback failed" in (row.error or "")
    assert ("rollback", False) in steps(row)


def test_validator_refusal_downgrades_an_auto_fix_to_manual(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    # Someone raised the limit meanwhile: 256Mi is no longer above it.
    cluster = FakeCluster(deployment(limit="300Mi"), clock=clock)
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.proposed
    assert row.mode is RemediationMode.manual and row.decided_by is None
    assert "downgraded to manual: validator" in (row.gate_reason or "")
    assert "not above the current 300Mi" in (row.gate_reason or "")
    assert cluster.patches == []
    assert incident.status is IncidentStatus.awaiting_review
    # A downgrade isn't a failed auto fix: it doesn't block the workload.
    assert row.mode is not RemediationMode.auto


def test_dry_run_that_changes_more_than_memory_is_refused(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    _, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    cluster.dry_run_extra = {"image": "mutated:latest"}
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.proposed
    assert "dry run: the dry run changed more" in (row.gate_reason or "")
    assert [dry for _, dry in cluster.patches] == [True]


def test_dry_run_rejected_by_the_api_server(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    _, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    cluster.fail_on.add("dry_run")
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.proposed
    assert "422" in (row.gate_reason or "")
    assert cluster.limit == "128Mi"


def manually_approved(
    store: FakeStore, make_incident: Any, clock: Clock, **proposal: Any
) -> tuple[IncidentRow, RemediationRow]:
    from app.services.incidents import approve_remediation

    payload = {**OOM_PROPOSAL, **proposal}
    incident, row = triaged(store, make_incident, proposal=payload)
    approve_remediation(store.uow(), incident.id, actor="admin", note=None, now=clock.now)
    return incident, row


def test_a_manual_approval_takes_the_same_path(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = manually_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    # No policies at all: the validator's own bounds apply, and with no error-ratio query
    # verify waits for Alertmanager's resolved notification.
    incident_state.apply(incident, IncidentEvent.alert_resolved, now=clock.now)
    applier(store, cluster, clock, config=PolicyConfig()).run_next()
    assert row.status is RemediationStatus.succeeded
    assert row.decided_by == "admin" and row.mode is RemediationMode.manual
    assert [s for s, _ in steps(row)][:2] == ["approved", "claimed"]
    assert cluster.limit == "256Mi"


def test_a_manual_approval_the_validator_refuses_fails(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = manually_approved(store, make_incident, clock, memory_limit="512Mi")
    cluster = FakeCluster(clock=clock)  # 512Mi is 4x the current 128Mi
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    assert "more than 2x the current 128Mi" in (row.error or "")
    assert cluster.patches == []
    assert incident.status is IncidentStatus.awaiting_review


def test_unreadable_deployment(store: FakeStore, make_incident: Any, clock: Clock) -> None:
    _, row = manually_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    cluster.fail_on.add("get")
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    assert "403" in (row.error or "")


def test_an_interrupted_run_is_rolled_back(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    """A backend that died after the patch leaves `running` with a lease; once it expires the
    next backend undoes the patch rather than trusting an unverified fix."""
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    first = applier(store, cluster, clock)
    first._wait = lambda _: True  # stops at the first wait, mid-rollout
    first.run_next()
    assert row.status is RemediationStatus.running and cluster.limit == "256Mi"
    assert not applier(store, cluster, clock).run_next()  # still leased
    clock.now = row.lease_until + timedelta(seconds=1)
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    assert "backend stopped before verify finished" in (row.error or "")
    assert cluster.limit == "128Mi"
    assert incident.status is IncidentStatus.awaiting_review


def test_a_tighter_policy_is_honoured(store: FakeStore, make_incident: Any, clock: Clock) -> None:
    incident, row = manually_approved(store, make_incident, clock)
    tight = policies(bounds={"maxMultiplier": 1.5, "ceiling": "512Mi"})
    applier(store, FakeCluster(clock=clock), clock, config=tight).run_next()
    assert row.status is RemediationStatus.failed
    assert "more than 1.5x" in (row.error or "")


def test_a_failed_patch_is_recorded_and_blocks_auto(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)
    cluster.fail_on.add("patch")
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed and "patch: Kubernetes API 422" in row.error
    assert row.applied_at is None and cluster.limit == "128Mi"
    assert incident.status is IncidentStatus.awaiting_review
    assert row.mode is RemediationMode.auto  # counts as a failed auto fix


def test_an_unexpected_error_before_the_patch_fails_without_touching_anything(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    _, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)

    def boom(namespace: str, name: str) -> Any:
        raise RuntimeError("bug")

    cluster.get_deployment = boom  # type: ignore[method-assign]
    assert applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed
    assert row.error == "unexpected error: RuntimeError"
    assert cluster.patches == []


@pytest.mark.parametrize("error", [RuntimeError("bug"), ClusterError("Kubernetes API 500")])
def test_an_error_during_verify_rolls_back(
    store: FakeStore, make_incident: Any, clock: Clock, error: Exception
) -> None:
    incident, row = auto_approved(store, make_incident, clock)
    cluster = FakeCluster(clock=clock)

    def pods(namespace: str, selector: dict[str, str]) -> Any:
        raise error

    cluster.list_pods = pods  # type: ignore[method-assign]
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.failed and "rolled back" in (row.error or "")
    assert cluster.limit == "128Mi"
    assert incident.status is IncidentStatus.awaiting_review


def test_an_invalid_stored_proposal_is_downgraded(
    store: FakeStore, make_incident: Any, clock: Clock
) -> None:
    _, row = auto_approved(store, make_incident, clock)
    row.payload_json = {**OOM_PROPOSAL, "memory_limit": "lots"}
    cluster = FakeCluster(clock=clock)
    applier(store, cluster, clock).run_next()
    assert row.status is RemediationStatus.proposed
    assert "not a valid set_memory_limit" in (row.gate_reason or "")
    assert cluster.patches == []


def pod(limit: str, reason: str | None, finished: str | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {"name": "demo-app", "state": {"running": {}}}
    if reason:
        status["lastState"] = {"terminated": {"reason": reason, "finishedAt": finished}}
    return {
        "metadata": {"name": f"demo-app-{limit}"},
        "spec": {"containers": [{"name": "demo-app", "resources": {"limits": {"memory": limit}}}]},
        "status": {"containerStatuses": [status]},
    }


def test_only_new_pods_oom_kills_after_the_patch_count() -> None:
    new = 256 * 2**20
    since = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    after = "2026-10-16T12:01:00Z"
    before = "2026-10-16T11:59:00Z"
    # An old pod with the faulted limit, still crash-looping during the rollout.
    assert _oom_killed_since([pod("128Mi", "OOMKilled", after)], "demo-app", new, since) is None
    # A new pod's earlier exit for another reason, or an OOM from before the patch.
    assert _oom_killed_since([pod("256Mi", "Error", after)], "demo-app", new, since) is None
    assert _oom_killed_since([pod("256Mi", "OOMKilled", before)], "demo-app", new, since) is None
    assert _oom_killed_since([pod("256Mi", "OOMKilled", after)], "demo-app", new, since) == (
        "demo-app-256Mi"
    )
    # No timestamp: assume the worst.
    assert _oom_killed_since([pod("256Mi", "OOMKilled")], "demo-app", new, since)


def test_the_loop_runs_until_stopped() -> None:
    stop = threading.Event()
    calls: list[int] = []

    class Applier:
        def run_next(self) -> bool:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("one bad iteration")
            if len(calls) == 3:
                stop.set()
            return False

    asyncio.run(run_apply_loop(Applier(), poll_seconds=0, stop=stop))  # type: ignore[arg-type]
    assert len(calls) == 3
