"""get_iam_role and simulate_access against moto's IAM (simulation through a canned response:
moto doesn't implement SimulatePrincipalPolicy)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError
from fastmcp.exceptions import ToolError

from aws_mcp.aws import AwsReader
from aws_mcp.tools import AwsTools

from .conftest import NOW, trust_policy

ROLE = "demo-app-role-use1-a1b2c3"
S3_READ = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": "arn:aws:s3:::demo-app-data-use1-a1b2c3/*",
        }
    ],
}


@pytest.fixture
def demo_role(iam: Any) -> dict[str, Any]:
    role = iam.create_role(
        RoleName=ROLE, Path="/demo-app/", AssumeRolePolicyDocument=trust_policy()
    )["Role"]
    iam.put_role_policy(
        RoleName=ROLE, PolicyName="demo-app-s3-read", PolicyDocument=json.dumps(S3_READ)
    )
    watched = iam.create_policy(
        PolicyName="demo-app-extra",
        Path="/demo-app/",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Action": "kms:Decrypt", "Resource": "*"}],
            }
        ),
    )["Policy"]
    other = iam.create_policy(
        PolicyName="shared-admin",
        Path="/platform/",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}],
            }
        ),
    )["Policy"]
    for policy in (watched, other):
        iam.attach_role_policy(RoleName=ROLE, PolicyArn=policy["Arn"])
    return role


@pytest.fixture
def outside_role(iam: Any) -> dict[str, Any]:
    return iam.create_role(
        RoleName="analytics-batch", Path="/analytics/", AssumeRolePolicyDocument=trust_policy()
    )["Role"]


def test_role_with_its_policies(tools: AwsTools, demo_role, sent_operations) -> None:
    result = tools.get_iam_role(ROLE)

    assert result["arn"] == demo_role["Arn"]
    assert result["path"] == "/demo-app/"
    assert result["trust_policy"]["Statement"][0]["Principal"] == {
        "Service": "pods.eks.amazonaws.com"
    }
    assert result["inline_policies"] == [{"name": "demo-app-s3-read", "document": S3_READ}]
    attached = {p["name"]: p for p in result["attached_policies"]}
    assert attached["demo-app-extra"]["document"]["Statement"][0]["Action"] == "kms:Decrypt"
    assert attached["demo-app-extra"]["default_version"] == "v1"
    # A policy outside the watched paths: named, never read (IAM would deny it too).
    assert attached["shared-admin"]["document"] is None
    assert "outside the watched IAM paths" in attached["shared-admin"]["note"]
    assert result["truncated"] is False
    assert isinstance(result["created"], str)
    policy_reads = [op for op in sent_operations if op[1] in ("GetPolicy", "GetPolicyVersion")]
    assert policy_reads == [("iam", "GetPolicy"), ("iam", "GetPolicyVersion")]


def test_only_policies_under_a_watched_path_are_read(tools: AwsTools) -> None:
    assert not tools._watched_policy("arn:aws:iam::aws:policy/demo-app/ReadOnlyAccess")
    assert not tools._watched_policy("arn:aws:iam::123456789012:policy/demo-app-other/lookalike")
    assert not tools._watched_policy("not-an-arn")
    assert tools._watched_policy("arn:aws:iam::123456789012:policy/demo-app/demo-app-extra")


def test_role_outside_the_watched_paths_is_refused(
    tools: AwsTools, outside_role, sent_operations
) -> None:
    with pytest.raises(ToolError, match=r"outside the watched IAM paths \(/demo-app/\)"):
        tools.get_iam_role("analytics-batch")

    # GetRole only (to learn the path); nothing about its policies.
    assert sent_operations == [("iam", "GetRole")]


def test_lookalike_path_is_refused(tools: AwsTools, iam: Any) -> None:
    iam.create_role(
        RoleName="sneaky", Path="/demo-app-not/", AssumeRolePolicyDocument=trust_policy()
    )

    with pytest.raises(ToolError, match="outside the watched IAM paths"):
        tools.get_iam_role("sneaky")


def test_missing_role(tools: AwsTools, moto) -> None:
    with pytest.raises(ToolError, match="no IAM role named nobody"):
        tools.get_iam_role("nobody")


@pytest.mark.parametrize("name", [f"arn:aws:iam::123456789012:role/{ROLE}", "a" * 65, "a b", ""])
def test_role_name_must_be_a_name(tools: AwsTools, name: str, sent_operations) -> None:
    with pytest.raises(ToolError, match="must be an IAM role name"):
        tools.get_iam_role(name)
    assert sent_operations == []


class DeniedAws:
    """IAM's answer for a role outside the role's policy: AccessDenied."""

    def call(self, service: str, operation: str, **params: Any) -> dict[str, Any]:
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, operation)

    def credentials_available(self) -> bool:
        return True


def test_iam_denial_reads_as_the_same_refusal(settings) -> None:
    tools = AwsTools(settings, DeniedAws(), now=lambda: NOW)

    with pytest.raises(ToolError, match="outside the watched IAM paths"):
        tools.get_iam_role("analytics-batch")
    with pytest.raises(ToolError, match="outside the watched IAM paths"):
        tools.simulate_access("analytics-batch", ["s3:GetObject"])


def test_many_policies_are_capped(tools: AwsTools, iam: Any, demo_role) -> None:
    for n in range(11):
        iam.put_role_policy(
            RoleName=ROLE, PolicyName=f"extra-{n:02}", PolicyDocument=json.dumps(S3_READ)
        )

    result = tools.get_iam_role(ROLE)

    assert len(result["inline_policies"]) == 10
    assert result["truncated"] is True


def test_policy_documents_are_redacted(tools: AwsTools, iam: Any, demo_role) -> None:
    iam.put_role_policy(
        RoleName=ROLE,
        PolicyName="leaky",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AKIAIOSFODNN7EXAMPLE",
                        "Effect": "Allow",
                        "Action": "s3:GetObject",
                        "Resource": "*",
                    }
                ],
            }
        ),
    )

    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(tools.get_iam_role(ROLE))


# --- simulate_access -----------------------------------------------------------------------


@pytest.fixture
def simulation(reader: AwsReader) -> list[dict[str, Any]]:
    """Answers SimulatePrincipalPolicy before the request is sent, and records its params."""
    requests: list[dict[str, Any]] = []

    def record(params: dict[str, Any], **_: Any) -> None:
        requests.append(params)

    def answer(**_: Any) -> tuple[AWSResponse, dict[str, Any]]:
        return AWSResponse("https://iam.amazonaws.com/", 200, {}, None), {
            "EvaluationResults": [
                {
                    "EvalActionName": "s3:GetObject",
                    "EvalResourceName": "arn:aws:s3:::demo-app-data-use1-a1b2c3/report.json",
                    "EvalDecision": "implicitDeny",
                    "MatchedStatements": [],
                    "MissingContextValues": [],
                },
                {
                    "EvalActionName": "s3:ListBucket",
                    "EvalResourceName": "arn:aws:s3:::demo-app-data-use1-a1b2c3",
                    "EvalDecision": "allowed",
                    "MatchedStatements": [
                        {
                            "SourcePolicyId": "demo-app-s3-read",
                            "SourcePolicyType": "IAM Policy",
                            "StartPosition": {"Line": 1, "Column": 40},
                            "EndPosition": {"Line": 1, "Column": 160},
                        }
                    ],
                    "MissingContextValues": [],
                    "PermissionsBoundaryDecisionDetail": {"AllowedByPermissionsBoundary": True},
                },
            ],
            "IsTruncated": False,
        }

    events = reader._client("iam").meta.events
    events.register("before-parameter-build.iam.SimulatePrincipalPolicy", record)
    events.register("before-call.iam.SimulatePrincipalPolicy", answer)
    return requests


def test_simulate_access(tools: AwsTools, demo_role, simulation) -> None:
    result = tools.simulate_access(
        ROLE,
        ["s3:GetObject", "s3:ListBucket"],
        [
            "arn:aws:s3:::demo-app-data-use1-a1b2c3/report.json",
            "arn:aws:s3:::demo-app-data-use1-a1b2c3",
        ],
    )

    [request] = simulation
    assert request["PolicySourceArn"] == demo_role["Arn"]
    assert request["ActionNames"] == ["s3:GetObject", "s3:ListBucket"]
    denied, allowed = result["results"]
    assert denied["decision"] == "implicitDeny" and denied["deciding_statements"] == []
    assert allowed["decision"] == "allowed"
    assert allowed["deciding_statements"][0]["policy_id"] == "demo-app-s3-read"
    assert allowed["allowed_by_permissions_boundary"] is True
    assert "resource policies" in result["note"]


def test_simulate_defaults_to_every_resource(tools: AwsTools, demo_role, simulation) -> None:
    tools.simulate_access(ROLE, ["s3:GetObject"])

    assert simulation[0]["ResourceArns"] == ["*"]


def test_simulate_refuses_roles_outside_the_paths(
    tools: AwsTools, outside_role, simulation
) -> None:
    with pytest.raises(ToolError, match="outside the watched IAM paths"):
        tools.simulate_access("analytics-batch", ["s3:GetObject"])
    assert simulation == []


@pytest.mark.parametrize(
    ("actions", "resources", "message"),
    [
        ([], None, "1 to 10 IAM actions"),
        (["s3:GetObject"] * 11, None, "1 to 10 IAM actions"),
        (["GetObject"], None, "not an IAM action"),
        (["s3:Get Object"], None, "not an IAM action"),
        (["s3:GetObject"], ["demo-bucket"], "not an ARN"),
        (["s3:GetObject"], ["*"] * 11, "at most 10"),
    ],
)
def test_simulate_validates_its_input(
    tools: AwsTools, demo_role, simulation, actions, resources, message
) -> None:
    with pytest.raises(ToolError, match=message):
        tools.simulate_access(ROLE, actions, resources)
    assert simulation == []
