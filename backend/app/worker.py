"""The worker process: `python -m app.worker` (helm `worker.command`, Compose `worker`).

It runs, as tasks on one event loop:

- the triage loop: poll the incident queue and triage one incident at a time;
- with CHANGES_QUEUE_URL set, the change intake (`app/intake/`): the SQS consumer, the change
  analysis loop, and (live mode) the 60-second health report.

The heartbeat file (the pod's probes check its age) is touched after every poll and every
`HEARTBEAT_SECONDS` during a run, because a run can take as long as the probe's maximum age;
a blocked event loop stops both, which is why the intake's boto3 and database calls run in
threads.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from app import telemetry
from app.agents.changes import ChangeAnalysis
from app.agents.model import AnthropicClient
from app.agents.runner import AgentRunner, RunLimits
from app.agents.tools import open_aws_toolbox, open_toolbox
from app.agents.triage import IncidentTriage
from app.api.deps import utcnow
from app.db.repositories import UnitOfWork
from app.db.session import make_engine, make_uow_factory
from app.intake.consumer import ChangeAnalysisLoop, ChangeConsumer, QueueStats, SqsQueue
from app.intake.health import HealthReporter
from app.intake.parser import ParserConfig
from app.services.policy_gate import ConfiguredPolicyGate
from app.services.remediation_policy import load_policies
from app.settings import IntakeMode, Settings, get_settings

log = logging.getLogger("app.worker")

HEARTBEAT_SECONDS = 15


@dataclass(frozen=True)
class Agents:
    triage: IncidentTriage
    # The intake's analysis loop calls `await agents.changes.analyze(change_id)`.
    changes: ChangeAnalysis
    uow_factory: Callable[[], UnitOfWork]


def build_agents(settings: Settings, clock: Callable[[], datetime] = utcnow) -> Agents:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set; see .env.example")
    uow_factory = make_uow_factory(make_engine(settings.database_url))
    runner = AgentRunner(
        AnthropicClient(), uow_factory, limits=RunLimits.from_settings(settings), clock=clock
    )
    return Agents(
        triage=IncidentTriage(
            uow_factory=uow_factory,
            runner=runner,
            gate=ConfiguredPolicyGate(
                uow_factory=uow_factory,
                config=load_policies(settings.remediation_policies_file),
                settings=settings,
                clock=clock,
            ),
            settings=settings,
            toolbox_factory=open_toolbox,
            clock=clock,
        ),
        changes=ChangeAnalysis(
            uow_factory=uow_factory,
            runner=runner,
            settings=settings,
            toolbox_factory=open_aws_toolbox,
            clock=clock,
        ),
        uow_factory=uow_factory,
    )


def intake_tasks(
    settings: Settings,
    agents: Agents,
    *,
    clock: Callable[[], datetime] = utcnow,
    client: Callable[..., Any] = boto3.client,
) -> list[Coroutine[Any, Any, None]]:
    """The change intake's loops, or none when CHANGES_QUEUE_URL is empty."""
    if not settings.changes_queue_url:
        log.info("CHANGES_QUEUE_URL is empty: the change intake is off")
        return []
    replay = settings.intake_mode is IntakeMode.replay
    # Replay reads ElasticMQ (AWS_ENDPOINT_URL_SQS), which needs no credentials.
    config = Config(signature_version=UNSIGNED) if replay else None
    sqs = client("sqs", region_name=settings.aws_region, config=config)
    stats = QueueStats()
    analysis = ChangeAnalysisLoop(
        analysis=agents.changes,
        uow_factory=agents.uow_factory,
        clock=clock,
        lease=timedelta(seconds=settings.triage_lease_seconds),
        max_runs=settings.change_analysis_max_runs,
        poll_seconds=settings.worker_poll_seconds,
    )
    consumer = ChangeConsumer(
        queue=SqsQueue(
            sqs,
            settings.changes_queue_url,
            visibility_timeout=settings.intake_visibility_timeout_seconds,
        ),
        uow_factory=agents.uow_factory,
        parser_config=ParserConfig(
            watched_iam_paths=settings.watched_paths,
            canary_group_id=settings.canary_security_group_id or None,
        ),
        clock=clock,
        stats=stats,
        on_new_change=analysis.wake,
    )
    tasks = [consumer.run(), analysis.run()]
    if not replay:
        reporter = HealthReporter(
            sqs=sqs,
            cloudtrail=client("cloudtrail", region_name=settings.aws_region),
            queue_url=settings.changes_queue_url,
            dlq_url=settings.changes_dlq_url,
            trail_name=settings.trail_name,
            stats=stats,
            uow_factory=agents.uow_factory,
            clock=clock,
        )
        tasks.append(reporter.run(settings.intake_report_seconds))
    return tasks


def touch(path: str | None) -> None:
    if path:
        Path(path).touch()


async def heartbeat(path: str | None) -> None:
    while path:
        touch(path)
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def run_triage_loop(
    triage: IncidentTriage, *, poll_seconds: float, heartbeat_file: str | None
) -> None:
    beat = asyncio.create_task(heartbeat(heartbeat_file))
    try:
        await _poll(triage, poll_seconds=poll_seconds, heartbeat_file=heartbeat_file)
    finally:
        beat.cancel()


async def _poll(triage: IncidentTriage, *, poll_seconds: float, heartbeat_file: str | None) -> None:
    while True:
        try:
            busy = await triage.run_next()
        except Exception:
            # The claim's lease brings the incident back; keep the loop alive.
            log.exception("triage loop iteration failed")
            busy = False
        touch(heartbeat_file)
        if not busy:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    settings = get_settings()
    telemetry.setup(telemetry.WORKER, settings)
    if not settings.aws_mcp_url:
        log.info("AWS_MCP_URL is empty: triage runs with k8s-mcp tools only")
    agents = build_agents(settings)
    asyncio.run(run_worker(settings, agents))


async def run_worker(settings: Settings, agents: Agents) -> None:
    # Every loop catches its own errors; one that still escapes ends the process (and the pod
    # restarts it) rather than leaving the others running half a worker.
    await asyncio.gather(
        run_triage_loop(
            agents.triage,
            poll_seconds=settings.worker_poll_seconds,
            heartbeat_file=settings.worker_heartbeat_file,
        ),
        *intake_tasks(settings, agents),
    )


if __name__ == "__main__":
    main()
