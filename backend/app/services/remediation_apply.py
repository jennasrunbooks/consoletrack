"""Apply and verify an approved cluster remediation: the backend's apply loop
(product-spec.md → Remediation modes; docs/remediation-graduation.md).

Runs in the backend (the only process with the `remediator` ServiceAccount), never in the
worker or the agent. The same path for an auto fix and a human's approval:

    claim (approved → running, with a lease)
    read     the live Deployment
    validate every validator rule (remediation_validator.py)   refused → manual / failed
    dry_run  server-side, dryRun=All; only the memory may change refused → manual / failed
    patch    strategic merge
    rollout  wait until every replica runs the new template (policy verify.rolloutTimeout)
    verify   until verify.waitForResolved: no new OOM kill in the new pods, and the incident
             resolved (Alertmanager's notification) or the error ratio below the threshold
    → succeeded, or rollback (the inverse patch) → failed, incident back to awaiting_review

A refusal before anything is applied downgrades an auto fix to manual (it waits for a human,
with the reason) and fails a human's approval. Every step is appended to `remediations.steps`
and committed as it happens, so the incident page shows progress. A backend that dies mid-run
leaves a `running` row whose lease expires; the next claim rolls back whatever was applied.

Tracing: each claim continues the trace stored on the remediation by the gate, with one span
per phase: `remediation.validate` (read, validate, dry run), `remediation.apply` (the patch),
and `remediation.verify` (rollout, verify, and any rollback).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic import ValidationError

from app import telemetry
from app.agents.schema import SetMemoryLimit
from app.cluster import Cluster, ClusterError, RatioSource
from app.db.repositories import UnitOfWork
from app.enums import (
    IncidentStatus,
    RemediationKind,
    RemediationMode,
    RemediationStatus,
)
from app.services import incident_state, remediation_state
from app.services.incident_state import IncidentEvent
from app.services.quantity import InvalidQuantity, parse_bytes
from app.services.remediation_policy import Policy, PolicyConfig
from app.services.remediation_state import RemediationEvent
from app.services.remediation_validator import (
    MemoryPatch,
    Refused,
    only_memory_changed,
    plan_memory_patch,
)

log = logging.getLogger(__name__)

# The kinds this loop can apply; anything else approved is left alone.
APPLY_KINDS = frozenset({RemediationKind.set_memory_limit})
POLL_SECONDS = 10.0
# On top of the rollout and verify windows, so a live run never loses its claim.
LEASE_SLACK = timedelta(minutes=5)


class Stopped(Exception):
    """The backend is shutting down; the claim stays and its lease expires."""


@dataclass(frozen=True)
class Claim:
    remediation_id: int
    incident_id: int
    kind: RemediationKind
    mode: RemediationMode
    payload: dict[str, Any]
    # Set when an earlier claim died after patching: roll that back instead of starting over.
    applied: MemoryPatch | None
    # The gate's span, which this run's spans continue.
    traceparent: str | None = None


class RemediationApplier:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        cluster: Cluster,
        ratio: RatioSource | None,
        config: PolicyConfig,
        allowed_namespaces: frozenset[str],
        clock: Callable[[], datetime],
        stop: threading.Event | None = None,
        poll_seconds: float = POLL_SECONDS,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._cluster = cluster
        self._ratio = ratio
        self._config = config
        self._allowed = allowed_namespaces
        self._clock = clock
        self._stop = stop or threading.Event()
        self._poll = poll_seconds
        # Returns True when the backend is stopping. Tests pass one that moves a fake clock.
        self._wait = wait or self._stop.wait

    # --- the loop ------------------------------------------------------------------------

    def run_next(self) -> bool:
        """Apply the next approved remediation, if any. Returns whether there was one."""
        claim = self._claim()
        if claim is None:
            return False
        try:
            with telemetry.continuing(claim.traceparent):
                self._run(claim)
        except Stopped:
            log.warning("remediation=%s left running: backend stopping", claim.remediation_id)
        except Exception as exc:
            # Before the patch (after it, _run rolls back itself): nothing to undo.
            log.exception("remediation=%s failed unexpectedly", claim.remediation_id)
            self._finish_failed(claim, f"unexpected error: {type(exc).__name__}", step="error")
        return True

    def _claim(self) -> Claim | None:
        now = self._clock()
        with self._uow_factory() as uow:
            row = uow.remediations.next_to_apply(now, APPLY_KINDS)
            if row is None:
                return None
            recovering = row.status is RemediationStatus.running
            policy = self._policy(row.payload_json)
            remediation_state.apply(row, RemediationEvent.claimed, now=now)
            row.lease_until = (
                now + policy.verify.rollout_timeout + policy.verify.wait_for_resolved + LEASE_SLACK
            )
            applied = None
            if recovering and row.applied_at is not None and row.before_json and row.after_json:
                applied = _patch_from_row(row.payload_json, row.before_json, row.after_json)
            _step(row, "claimed", now, True, "recovering an interrupted run" if recovering else "")
            claim = Claim(
                remediation_id=row.id,
                incident_id=row.subject_id,
                kind=row.kind,
                mode=row.mode or RemediationMode.manual,
                payload=dict(row.payload_json),
                applied=applied,
                traceparent=row.traceparent,
            )
            uow.commit()
        return claim

    def _run(self, claim: Claim) -> None:
        if claim.applied is not None:
            with self._span("remediation.verify", claim):
                self._roll_back(claim, claim.applied, "the backend stopped before verify finished")
            return
        with self._span("remediation.validate", claim) as span:
            patch = self._validate(claim)
            if patch is None:
                return
            span.set_attribute("remediation.result", "passed")
        with self._span("remediation.apply", claim) as span:
            try:
                patched = self._cluster.patch_deployment(
                    patch.namespace, patch.deployment, patch.body(), dry_run=False
                )
            except ClusterError as exc:
                self._finish_failed(claim, f"patch: {exc}", step="patch")
                return
            applied_at = self._clock()
            self._record(claim, "patch", True, patch.summary(), applied_at=applied_at)
            span.set_attribute("remediation.result", "applied")
        with self._span("remediation.verify", claim):
            self._verify_or_roll_back(claim, patch, patched, applied_at)

    def _validate(self, claim: Claim) -> MemoryPatch | None:
        """Read, validate, and dry-run the patch; None after a refusal (already recorded)."""
        try:
            proposal = SetMemoryLimit.model_validate(claim.payload)
        except ValidationError:
            self._refuse(claim, "the stored proposal is not a valid set_memory_limit")
            return None
        policy = self._policy(claim.payload)
        try:
            deployment = self._cluster.get_deployment(proposal.namespace, proposal.deployment)
        except ClusterError as exc:
            self._refuse(claim, f"couldn't read the Deployment: {exc}", step="read")
            return None
        self._record(claim, "read", True, f"{proposal.namespace}/{proposal.deployment}")
        try:
            patch = plan_memory_patch(
                proposal, deployment, policy=policy, allowed_namespaces=self._allowed
            )
        except Refused as exc:
            self._refuse(claim, f"validator: {exc}", step="validate")
            return None
        self._record(claim, "validate", True, patch.summary(), patch=patch)
        try:
            dry = self._cluster.patch_deployment(
                patch.namespace, patch.deployment, patch.body(), dry_run=True
            )
            only_memory_changed(deployment, dry, patch)
        except (ClusterError, Refused) as exc:
            self._refuse(claim, f"dry run: {exc}", step="dry_run")
            return None
        self._record(claim, "dry_run", True, "server-side dry run accepted the patch")
        return patch

    def _verify_or_roll_back(
        self, claim: Claim, patch: MemoryPatch, patched: dict[str, Any], applied_at: datetime
    ) -> None:
        policy = self._policy(claim.payload)
        try:
            self._wait_for_rollout(patch, patched, policy)
            self._record(claim, "rollout", True, "every replica runs the new limit")
            detail = self._verify(claim, patch, policy, applied_at)
        except _Failed as exc:
            self._record(claim, exc.step, False, str(exc))
            self._roll_back(claim, patch, str(exc))
            return
        except Stopped:
            raise
        except ClusterError as exc:
            self._record(claim, "verify", False, str(exc))
            self._roll_back(claim, patch, str(exc))
            return
        except Exception as exc:
            log.exception("remediation=%s verify failed unexpectedly", claim.remediation_id)
            reason = f"unexpected error: {type(exc).__name__}"
            self._record(claim, "verify", False, reason)
            self._roll_back(claim, patch, reason)
            return
        self._record(claim, "verify", True, detail)
        self._finish(claim, RemediationEvent.succeeded, detail)

    @contextmanager
    def _span(self, name: str, claim: Claim) -> Iterator[Span]:
        attributes = {
            "remediation.id": claim.remediation_id,
            "incident.id": claim.incident_id,
            "remediation.kind": claim.kind.value,
            "remediation.mode": claim.mode.value,
        }
        with telemetry.tracer.start_as_current_span(name, attributes=attributes) as span:
            yield span

    # --- rollout and verify ----------------------------------------------------------------

    def _wait_for_rollout(
        self, patch: MemoryPatch, patched: dict[str, Any], policy: Policy
    ) -> None:
        generation = (patched.get("metadata") or {}).get("generation") or 0
        deadline = self._clock() + policy.verify.rollout_timeout
        while True:
            deployment = self._cluster.get_deployment(patch.namespace, patch.deployment)
            if _rolled_out(deployment, generation):
                return
            if self._clock() >= deadline:
                raise _Failed(
                    "rollout", f"the rollout didn't finish within {policy.verify.rollout_timeout}"
                )
            self._sleep()

    def _verify(
        self, claim: Claim, patch: MemoryPatch, policy: Policy, applied_at: datetime
    ) -> str:
        deployment = self._cluster.get_deployment(patch.namespace, patch.deployment)
        selector = ((deployment.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
        new_limit = parse_bytes(patch.after["limits"]["memory"])
        deadline = self._clock() + policy.verify.wait_for_resolved
        error_ratio = policy.verify.error_ratio
        while True:
            pods = self._cluster.list_pods(patch.namespace, selector)
            killed = _oom_killed_since(pods, patch.container, new_limit, applied_at)
            if killed:
                raise _Failed("verify", f"pod {killed} was OOMKilled again with the new limit")
            if self._incident_resolved(claim.incident_id):
                return "the alert resolved"
            if error_ratio is not None and self._ratio is not None:
                value = self._ratio.ratio(error_ratio.query)
                if value is not None and value < error_ratio.below:
                    return f"error ratio {value:.3f} is below {error_ratio.below:g}"
            if self._clock() >= deadline:
                raise _Failed(
                    "verify",
                    f"not resolved within {policy.verify.wait_for_resolved}: the alert is still "
                    "firing and the error ratio is not below the threshold",
                )
            self._sleep()

    def _incident_resolved(self, incident_id: int) -> bool:
        with self._uow_factory() as uow:
            incident = uow.incidents.get(incident_id)
            return incident is not None and incident.status is IncidentStatus.resolved

    # --- outcomes ------------------------------------------------------------------------

    def _refuse(self, claim: Claim, reason: str, *, step: str = "validate") -> None:
        """Nothing was applied: an auto fix goes back to a human; a human's approval fails."""
        if claim.mode is RemediationMode.auto:
            self._record(claim, step, False, reason)
            self._finish(claim, RemediationEvent.downgraded, f"downgraded to manual: {reason}")
        else:
            self._finish_failed(claim, reason, step=step)

    def _roll_back(self, claim: Claim, patch: MemoryPatch, reason: str) -> None:
        try:
            self._cluster.patch_deployment(
                patch.namespace, patch.deployment, patch.undo_body(), dry_run=False
            )
        except ClusterError as exc:
            self._record(claim, "rollback", False, f"rollback failed, a human must fix it: {exc}")
            self._finish(claim, RemediationEvent.failed, f"{reason}; rollback failed: {exc}")
            return
        undone = f"memory limit back to {patch.before['limits']['memory']}"
        self._record(claim, "rollback", True, undone)
        self._finish(claim, RemediationEvent.failed, f"{reason}; rolled back ({undone})")

    def _finish_failed(self, claim: Claim, reason: str, *, step: str) -> None:
        self._record(claim, step, False, reason)
        self._finish(claim, RemediationEvent.failed, reason)

    def _finish(self, claim: Claim, event: RemediationEvent, reason: str) -> None:
        now = self._clock()
        with self._uow_factory() as uow:
            row = uow.remediations.get(claim.remediation_id, for_update=True)
            if row is None:
                return
            remediation_state.apply(row, event, now=now, reason=reason)
            _step(row, event.value, now, event is RemediationEvent.succeeded, reason)
            incident = uow.incidents.get(claim.incident_id, for_update=True)
            if (
                event is not RemediationEvent.succeeded
                and incident is not None
                and incident.status is IncidentStatus.remediating
            ):
                # The incident stays `remediating` after a success until Alertmanager resolves
                # it; otherwise a human looks again.
                incident_state.apply(incident, IncidentEvent.remediation_failed, now=now)
            uow.commit()
        # On the phase span that ended the run.
        trace.get_current_span().set_attribute("remediation.result", event.value)
        telemetry.REMEDIATIONS.add(
            1, {"kind": claim.kind.value, "mode": claim.mode.value, "result": event.value}
        )
        log.info("remediation=%s %s: %s", claim.remediation_id, event.value, reason)

    def _record(
        self,
        claim: Claim,
        step: str,
        ok: bool,
        detail: str,
        *,
        patch: MemoryPatch | None = None,
        applied_at: datetime | None = None,
    ) -> None:
        now = self._clock()
        with self._uow_factory() as uow:
            row = uow.remediations.get(claim.remediation_id, for_update=True)
            if row is None:
                return
            _step(row, step, now, ok, detail)
            if patch is not None:
                row.before_json, row.after_json = patch.before, patch.after
            if applied_at is not None:
                row.applied_at = applied_at
            uow.commit()

    def _policy(self, payload: dict[str, Any]) -> Policy:
        return self._config.policy_for(RemediationKind.set_memory_limit, payload.get("namespace"))

    def _sleep(self) -> None:
        if self._wait(self._poll):
            raise Stopped


class _Failed(Exception):
    def __init__(self, step: str, message: str) -> None:
        self.step = step
        super().__init__(message)


def _step(row: Any, step: str, at: datetime, ok: bool, detail: str) -> None:
    # A new list, so SQLAlchemy sees the JSONB change.
    entry = {"step": step, "at": at.isoformat(), "ok": ok, "detail": detail}
    row.steps = [*(row.steps or []), entry]


def _patch_from_row(
    payload: dict[str, Any], before: dict[str, Any], after: dict[str, Any]
) -> MemoryPatch | None:
    try:
        return MemoryPatch(
            namespace=payload["namespace"],
            deployment=payload["deployment"],
            container=payload["container"],
            before=before,
            after=after,
        )
    except KeyError:
        return None


def _rolled_out(deployment: dict[str, Any], generation: int) -> bool:
    """`kubectl rollout status`'s rule: the controller saw the patch, and every replica is
    updated and available, with no old ones left."""
    spec = deployment.get("spec") or {}
    status = deployment.get("status") or {}
    replicas = spec.get("replicas", 1)
    return (
        (status.get("observedGeneration") or 0) >= generation
        and (status.get("updatedReplicas") or 0) == replicas
        and (status.get("replicas") or 0) == replicas
        and (status.get("availableReplicas") or 0) == replicas
    )


def _oom_killed_since(
    pods: list[dict[str, Any]], container: str, limit: int, since: datetime
) -> str | None:
    """A pod running the new limit whose container was OOMKilled after the patch."""
    for pod in pods:
        if not _runs_limit(pod, container, limit):
            continue
        for status in (pod.get("status") or {}).get("containerStatuses") or []:
            if status.get("name") != container:
                continue
            for state in (status.get("state") or {}, status.get("lastState") or {}):
                terminated = state.get("terminated") or {}
                if terminated.get("reason") != "OOMKilled":
                    continue
                finished = _timestamp(terminated.get("finishedAt"))
                if finished is None or finished >= since:
                    return (pod.get("metadata") or {}).get("name", "?")
    return None


def _runs_limit(pod: dict[str, Any], container: str, limit: int) -> bool:
    """Old pods (the faulted limit) may still be crash-looping during the rollout; only pods
    with the new limit count."""
    for spec in (pod.get("spec") or {}).get("containers") or []:
        if spec.get("name") == container:
            memory = ((spec.get("resources") or {}).get("limits") or {}).get("memory")
            try:
                return memory is not None and parse_bytes(memory) == limit
            except InvalidQuantity:
                return False
    return False


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def run_apply_loop(
    applier: RemediationApplier, *, poll_seconds: float, stop: threading.Event
) -> None:
    """The backend's loop: one remediation at a time, in a thread (the cluster client blocks)."""
    while not stop.is_set():
        try:
            busy = await asyncio.to_thread(applier.run_next)
        except Exception:
            # The claim's lease brings the remediation back; keep the loop alive.
            log.exception("remediation apply loop iteration failed")
            busy = False
        if not busy:
            await asyncio.sleep(poll_seconds)
