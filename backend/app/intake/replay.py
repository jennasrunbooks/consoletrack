"""Replay: put the recorded CloudTrail fixtures on a queue, as EventBridge would.

`scripts/replay-fixtures.py` calls this against ElasticMQ for `INTAKE_MODE=replay`; the
integration test does the same. The fixtures (`backend/tests/fixtures/cloudtrail/*.json`, not
`excluded/`) are a shared contract: see that folder's README.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "cloudtrail"
# The account ID in every fixture; ElasticMQ's queue URLs use it too (compose/elasticmq.conf).
LOCAL_ACCOUNT = "123456789012"
LOCAL_QUEUE = "ct-changes-use1-local"
LOCAL_DLQ = "ct-changes-dlq-use1-local"


def fixture_files(directory: Path = FIXTURES) -> list[Path]:
    return sorted(directory.glob("*.json"))


def local_queue_url(endpoint: str, name: str = LOCAL_QUEUE) -> str:
    return f"{endpoint.rstrip('/')}/{LOCAL_ACCOUNT}/{name}"


def send_fixtures(sqs: Any, queue_url: str, files: Iterable[Path]) -> int:
    sent = 0
    for path in files:
        sqs.send_message(QueueUrl=queue_url, MessageBody=path.read_text())
        sent += 1
    return sent
