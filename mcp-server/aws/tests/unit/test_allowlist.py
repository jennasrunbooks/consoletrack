"""Read-only by construction: the allowlist, the code that uses it, and the IAM policy behind it.

- every (service, operation) the code names is in `ALLOWED_OPERATIONS`, and every allowed one is
  a read that the `aws_mcp` policy in pod-identity.tf grants;
- nothing outside `aws.py` creates a client or imports boto3;
- an unlisted call fails before a request, through the wrapper and through the client directly;
- S3 GetObject reads the configured state object and nothing else.

`conftest.sent_operations` checks the same allowlist at runtime in every test.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from aws_mcp.aws import ALLOWED_OPERATIONS, AwsReader, OperationNotAllowed
from aws_mcp.settings import DEMO_APP_STATE_KEY

from .conftest import REPO, STATE_BUCKET

PACKAGE = Path(__file__).resolve().parents[2] / "aws_mcp"
POD_IDENTITY_TF = REPO / "terraform" / "consoletrack" / "us-east-1" / "pod-identity.tf"
READ_PREFIXES = ("Get", "List", "Describe", "Lookup", "Simulate")


def named_operations() -> set[tuple[str, str]]:
    """Every `.call("service", "Operation", ...)` and `._aws(...)` with literal names."""
    found: set[tuple[str, str]] = set()
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("call", "_aws")
                and len(node.args) >= 2
                and all(isinstance(a, ast.Constant) for a in node.args[:2])
            ):
                found.add((node.args[0].value, node.args[1].value))
    return found


def terraform_actions() -> set[str]:
    """The actions in `data "aws_iam_policy_document" "aws_mcp"`."""
    text = POD_IDENTITY_TF.read_text()
    start = text.index('data "aws_iam_policy_document" "aws_mcp"')
    end = text.index("\ndata ", start + 1)
    block = text[start:end]
    actions: set[str] = set()
    for listed in re.findall(r"actions\s*=\s*\[(.*?)\]", block, re.S):
        actions.update(re.findall(r'"([a-z0-9-]+:[A-Za-z]+)"', listed))
    return actions


def test_the_code_names_only_allowed_operations() -> None:
    named = named_operations()

    # Sanity: the walk sees the calls (a refactor that hides them would make this vacuous).
    assert len(named) >= 9
    assert named <= ALLOWED_OPERATIONS, named - ALLOWED_OPERATIONS


def test_every_allowed_operation_is_used() -> None:
    assert named_operations() == ALLOWED_OPERATIONS


def test_every_allowed_operation_is_a_read() -> None:
    for service, operation in ALLOWED_OPERATIONS:
        assert operation.startswith(READ_PREFIXES), f"{service}:{operation}"


def test_every_allowed_operation_is_granted_by_the_terraform_policy() -> None:
    granted = terraform_actions()
    assert "cloudtrail:LookupEvents" in granted  # the parse found the block

    needed = {f"{service}:{operation}" for service, operation in ALLOWED_OPERATIONS}

    assert needed <= granted, needed - granted


def test_terraform_policy_has_no_write_action() -> None:
    for action in terraform_actions():
        assert action.split(":")[1].startswith(READ_PREFIXES), action


def test_only_aws_py_touches_boto3() -> None:
    for path in PACKAGE.glob("*.py"):
        if path.name == "aws.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names]
                module = getattr(node, "module", None) or ""
                assert "boto3" not in names and not module.startswith("boto3"), path.name
            # session.client("iam"), session.resource("s3"), reader._client("iam").
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("client", "resource", "_client")
            ):
                raise AssertionError(f"{path.name} creates an AWS client: {ast.unparse(node)}")


def test_unlisted_operation_fails_in_the_wrapper(reader: AwsReader, iam: Any) -> None:
    iam.create_role(RoleName="victim", AssumeRolePolicyDocument="{}")

    with pytest.raises(OperationNotAllowed, match="never calls iam:DeleteRole"):
        reader.call("iam", "DeleteRole", RoleName="victim")

    assert iam.get_role(RoleName="victim")["Role"]["RoleName"] == "victim"


@pytest.mark.parametrize(
    ("service", "method", "params"),
    [
        ("iam", "delete_role", {"RoleName": "victim"}),
        ("iam", "put_role_policy", {"RoleName": "r", "PolicyName": "p", "PolicyDocument": "{}"}),
        ("ec2", "authorize_security_group_ingress", {"GroupId": "sg-1"}),
        ("s3", "put_object", {"Bucket": STATE_BUCKET, "Key": DEMO_APP_STATE_KEY, "Body": b""}),
        ("s3", "list_objects_v2", {"Bucket": STATE_BUCKET}),
        ("cloudtrail", "stop_logging", {"Name": "trail"}),
        ("iam", "list_roles", {}),
    ],
)
def test_unlisted_operation_fails_on_the_client_itself(
    reader: AwsReader, service: str, method: str, params: dict[str, Any]
) -> None:
    client = reader._client(service)

    with pytest.raises(OperationNotAllowed):
        getattr(client, method)(**params)


@pytest.mark.parametrize(
    ("bucket", "key"),
    [
        (STATE_BUCKET, "consoletrack/us-east-1/terraform.tfstate"),
        (STATE_BUCKET, f"{DEMO_APP_STATE_KEY}.backup"),
        ("someone-elses-bucket", DEMO_APP_STATE_KEY),
    ],
)
def test_get_object_reads_only_the_state_object(reader: AwsReader, s3, bucket, key) -> None:
    s3.put_object(Bucket=STATE_BUCKET, Key="consoletrack/us-east-1/terraform.tfstate", Body=b"{}")

    with pytest.raises(OperationNotAllowed, match="only the demo-app root's state"):
        reader.call("s3", "GetObject", Bucket=bucket, Key=key)
    with pytest.raises(OperationNotAllowed):
        reader._client("s3").get_object(Bucket=bucket, Key=key)


def test_get_object_without_a_bucket_is_refused(settings, session) -> None:
    reader = AwsReader(settings.model_copy(update={"tf_state_bucket": ""}), session)

    with pytest.raises(OperationNotAllowed):
        reader.call("s3", "GetObject", Bucket="", Key=DEMO_APP_STATE_KEY)


def test_the_state_object_itself_is_readable(reader: AwsReader, s3) -> None:
    s3.put_object(Bucket=STATE_BUCKET, Key=DEMO_APP_STATE_KEY, Body=b"{}")

    body = reader.call("s3", "GetObject", Bucket=STATE_BUCKET, Key=DEMO_APP_STATE_KEY)["Body"]

    assert body.read() == b"{}"
