"""The worker process: `python -m app.worker` (helm `worker.command`, Compose `worker`).

Today it runs the triage loop: poll the incident queue and triage one incident at a time. The
heartbeat file (the pod's probes check its age) is touched after every poll and every
`HEARTBEAT_SECONDS` during a run, because a run can take as long as the probe's maximum age;
a blocked event loop stops both. The change intake (#9) joins this process as a second task.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import telemetry
from app.agents.changes import ChangeAnalysis
from app.agents.model import AnthropicClient
from app.agents.runner import AgentRunner, RunLimits
from app.agents.tools import open_aws_toolbox, open_toolbox
from app.agents.triage import IncidentTriage
from app.api.deps import utcnow
from app.db.session import make_engine, make_uow_factory
from app.services.policy_gate import ConfiguredPolicyGate
from app.services.remediation_policy import load_policies
from app.settings import Settings, get_settings

log = logging.getLogger("app.worker")

HEARTBEAT_SECONDS = 15


@dataclass(frozen=True)
class Agents:
    triage: IncidentTriage
    # For the intake (#9): `await agents.changes.analyze(change_id)` after recording a change.
    changes: ChangeAnalysis


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
    )


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
    asyncio.run(
        run_triage_loop(
            agents.triage,
            poll_seconds=settings.worker_poll_seconds,
            heartbeat_file=settings.worker_heartbeat_file,
        )
    )


if __name__ == "__main__":
    main()
