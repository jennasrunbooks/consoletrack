#!/usr/bin/env python3
"""Send the recorded CloudTrail fixtures to the local change queue (ElasticMQ), for
INTAKE_MODE=replay. The worker then records them like live events; intake health shows Replay.

    cd backend && uv run python ../scripts/replay-fixtures.py [--endpoint http://localhost:9324]

The endpoint defaults to AWS_ENDPOINT_URL_SQS, then http://localhost:9324 (Compose publishes
ElasticMQ there). Requests are unsigned: ElasticMQ needs no credentials, and this script never
talks to AWS.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import boto3  # noqa: E402
from botocore import UNSIGNED  # noqa: E402
from botocore.config import Config  # noqa: E402

from app.intake.replay import FIXTURES, fixture_files, local_queue_url, send_fixtures  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("AWS_ENDPOINT_URL_SQS", "http://localhost:9324"),
        help="ElasticMQ endpoint (default: AWS_ENDPOINT_URL_SQS or http://localhost:9324)",
    )
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    args = parser.parse_args()
    if "amazonaws.com" in args.endpoint:
        parser.error("replay sends to ElasticMQ only, not AWS")
    sqs = boto3.client(
        "sqs",
        endpoint_url=args.endpoint,
        region_name="us-east-1",
        config=Config(signature_version=UNSIGNED),
    )
    url = local_queue_url(args.endpoint)
    count = send_fixtures(sqs, url, fixture_files(args.fixtures))
    print(f"sent {count} fixture events to {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
