"""moto for IAM, EC2, and S3; botocore's Stubber for CloudTrail (moto has no LookupEvents); and a
recorder that fails any test in which aws-mcp's own clients send an operation outside the
allowlist."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import botocore.client
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from aws_mcp.aws import ALLOWED_OPERATIONS, AwsReader, OperationNotAllowed
from aws_mcp.settings import Settings
from aws_mcp.tools import AwsTools

REPO = Path(__file__).resolve().parents[4]
CLOUDTRAIL_FIXTURES = REPO / "backend" / "tests" / "fixtures" / "cloudtrail"
NOW = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)
ACCOUNT = "123456789012"
STATE_BUCKET = "ct-tfstate-test"
USER_AGENT_MARK = "consoletrack-aws-mcp"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def sent_operations(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    """Every operation an aws-mcp client sends, whatever path it took. Test setup clients
    (creating roles and groups in moto) aren't aws-mcp's and aren't recorded."""
    sent: list[tuple[str, str]] = []
    original = botocore.client.BaseClient._make_api_call

    def record(self: Any, operation_name: str, api_params: dict[str, Any]) -> Any:
        mine = USER_AGENT_MARK in (self.meta.config.user_agent_extra or "")
        try:
            return original(self, operation_name, api_params)
        except OperationNotAllowed:
            # Refused before a request was built: not sent.
            mine = False
            raise
        finally:
            if mine:
                sent.append((self.meta.service_model.service_name, operation_name))

    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", record)
    yield sent
    unlisted = set(sent) - ALLOWED_OPERATIONS
    assert not unlisted, f"aws-mcp sent operations outside the allowlist: {unlisted}"


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_CONTAINER_CREDENTIALS_FULL_URI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        watched_iam_paths="/demo-app/",
        tf_state_bucket=STATE_BUCKET,
        aws_region="us-east-1",
        lookup_cache_seconds=120,
    )


@pytest.fixture
def moto() -> Iterator[None]:
    with mock_aws():
        yield


@pytest.fixture
def session(moto: None) -> boto3.Session:
    return boto3.Session(region_name="us-east-1")


@pytest.fixture
def reader(settings: Settings, session: boto3.Session) -> AwsReader:
    return AwsReader(settings, session)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tools(settings: Settings, reader: AwsReader, clock: Clock) -> AwsTools:
    return AwsTools(settings, reader, now=lambda: NOW, clock=clock)


@pytest.fixture
def cloudtrail(reader: AwsReader) -> Iterator[Stubber]:
    stubber = Stubber(reader._client("cloudtrail"))
    with stubber:
        yield stubber
    stubber.assert_no_pending_responses()


@pytest.fixture
def iam(session: boto3.Session) -> Any:
    return session.client("iam")


@pytest.fixture
def ec2(session: boto3.Session) -> Any:
    return session.client("ec2")


@pytest.fixture
def s3(session: boto3.Session) -> Any:
    client = session.client("s3")
    client.create_bucket(Bucket=STATE_BUCKET)
    return client


# --- CloudTrail events ---------------------------------------------------------------------


def fixture_detail(name: str) -> dict[str, Any]:
    """The CloudTrail record inside one of the intake's EventBridge fixtures."""
    return json.loads((CLOUDTRAIL_FIXTURES / name).read_text())["detail"]


def lookup_event(detail: dict[str, Any], resources: list[tuple[str, str]] = ()) -> dict[str, Any]:
    """A LookupEvents `Events` item, as CloudTrail returns it, for one record."""
    identity = detail.get("userIdentity", {})
    return {
        "EventId": detail["eventID"],
        "EventName": detail["eventName"],
        "ReadOnly": str(detail.get("readOnly", False)).lower(),
        "EventTime": datetime.fromisoformat(detail["eventTime"].replace("Z", "+00:00")),
        "EventSource": detail["eventSource"],
        "Username": identity.get("principalId", ":").partition(":")[2] or "unknown",
        "Resources": [{"ResourceType": t, "ResourceName": n} for t, n in resources],
        "CloudTrailEvent": json.dumps(detail),
    }


def trust_policy(service: str = "pods.eks.amazonaws.com") -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": service},
                    "Action": ["sts:AssumeRole", "sts:TagSession"],
                }
            ],
        }
    )
