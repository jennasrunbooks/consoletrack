"""get_resource_changes and get_principal_activity against stubbed LookupEvents, fed with the
intake's recorded CloudTrail fixtures (backend/tests/fixtures/cloudtrail/)."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from typing import Any

import pytest
from botocore.stub import ANY, Stubber
from fastmcp.exceptions import ToolError

from aws_mcp import tools as tools_module
from aws_mcp.tools import AwsTools

from .conftest import NOW, Clock, fixture_detail, lookup_event

SG = "sg-0demo1234567890a"
SG_ARN = f"arn:aws:ec2:us-east-1:123456789012:security-group/{SG}"
SSO_SESSION = (
    "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_Admin_0example/alex@example.com"
)


def expect(
    stubber: Stubber,
    events: list[dict[str, Any]],
    attribute: dict[str, str] | None = None,
    token: str | None = None,
    next_token: str | None = None,
) -> None:
    params: dict[str, Any] = {
        "LookupAttributes": [attribute] if attribute else ANY,
        "StartTime": NOW - timedelta(hours=24),
        "EndTime": NOW,
        "MaxResults": 50,
    }
    if token:
        params["NextToken"] = token
    response: dict[str, Any] = {"Events": events}
    if next_token:
        response["NextToken"] = next_token
    stubber.add_response("lookup_events", response, params)


def sg_event() -> dict[str, Any]:
    return lookup_event(
        fixture_detail("console-authorize-sg-ingress.json"),
        [("AWS::EC2::SecurityGroup", SG)],
    )


# --- get_resource_changes ------------------------------------------------------------------


def test_resource_changes_for_the_drift_scenario(tools: AwsTools, cloudtrail: Stubber) -> None:
    read_only = lookup_event(fixture_detail("excluded/read-only-describe.json"))
    expect(
        cloudtrail,
        [sg_event(), read_only],
        {"AttributeKey": "ResourceName", "AttributeValue": SG},
    )

    result = tools.get_resource_changes(SG_ARN, 24)

    assert result["lookup_resource_name"] == SG
    assert result["truncated"] is False and result["throttled"] is False
    # The read-only DescribeSecurityGroups is left out: writes only.
    [event] = result["events"]
    assert event["event_name"] == "AuthorizeSecurityGroupIngress"
    assert event["via"] == "console"
    assert event["principal_arn"] == SSO_SESSION
    assert event["role_arn"].endswith(":role/AWSReservedSSO_Admin_0example")
    assert event["source_ip"] == "203.0.113.x"
    assert event["resources"] == [{"type": "AWS::EC2::SecurityGroup", "name": SG}]
    untrusted = event["untrusted_data"]
    assert untrusted["user_agent"] == "console.amazonaws.com"
    assert untrusted["session_name"] == "alex@example.com"
    cidr = untrusted["request_parameters"]["ipPermissions"]["items"][0]["ipRanges"]["items"][0]
    assert cidr == {"cidrIp": "0.0.0.0/0"}
    assert "untrusted_data" in result["note"]


@pytest.mark.parametrize(
    ("arn", "name"),
    [
        # The intake records a role without its path; CloudTrail indexes roles by name.
        ("arn:aws:iam::123456789012:role/demo-app-role-use1-a1b2c3", "demo-app-role-use1-a1b2c3"),
        ("arn:aws:iam::123456789012:role/demo-app/demo-app-role", "demo-app-role"),
        (
            "arn:aws:iam::123456789012:policy/demo-app/demo-app-read",
            "arn:aws:iam::123456789012:policy/demo-app/demo-app-read",
        ),
        ("arn:aws:s3:::demo-app-data-use1-a1b2c3", "demo-app-data-use1-a1b2c3"),
        ("arn:aws:s3:::demo-app-data-use1-a1b2c3/reports/a.json", "demo-app-data-use1-a1b2c3"),
        (
            "arn:aws:eks:us-east-1:123456789012:podidentityassociation/ct-eks/a-0123456789",
            "a-0123456789",
        ),
        (
            "arn:aws:kms:us-east-1:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab",
            "arn:aws:kms:us-east-1:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab",
        ),
    ],
)
def test_resource_name_by_arn(tools: AwsTools, cloudtrail: Stubber, arn: str, name: str) -> None:
    expect(cloudtrail, [], {"AttributeKey": "ResourceName", "AttributeValue": name})

    assert tools.get_resource_changes(arn, 24)["events"] == []


@pytest.mark.parametrize(
    "arn",
    ["sg-0demo1234567890a", "ec2:CreateTags:sg-1,sg-2", "arn:aws:ec2", "x" * 3000, ""],
)
def test_not_an_arn_is_refused_without_a_call(
    tools: AwsTools, cloudtrail: Stubber, arn: str
) -> None:
    with pytest.raises(ToolError, match="not an ARN"):
        tools.get_resource_changes(arn, 24)


@pytest.mark.parametrize("hours", [0, -1, 73, 10_000])
def test_hours_are_bounded(tools: AwsTools, cloudtrail: Stubber, hours: int) -> None:
    with pytest.raises(ToolError, match="between 1 and 72"):
        tools.get_resource_changes(SG_ARN, hours)
    with pytest.raises(ToolError, match="between 1 and 72"):
        tools.get_principal_activity(SSO_SESSION, hours)


def test_via_for_each_kind_of_caller(tools: AwsTools, cloudtrail: Stubber) -> None:
    files = {
        "cli-put-role-policy.json": "cli",
        "sdk-put-role-policy.json": "sdk",
        "laptop-terraform-modify-sg-rules.json": "terraform",
        "cloudshell-create-tags.json": "cloudshell",
        "console-s3-aws-internal.json": "console",
    }
    expect(cloudtrail, [lookup_event(fixture_detail(f)) for f in files])

    events = tools.get_resource_changes(SG_ARN, 24)["events"]

    assert [e["via"] for e in events] == list(files.values())


# --- redaction -----------------------------------------------------------------------------

ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
SESSION_TOKEN = "IQoJb3JpZ2luX2VjEXAMPLEexampleEXAMPLEexampleEXAMPLE0123456789"


def leaky_event(source_ip: str = "198.51.100.23") -> dict[str, Any]:
    detail = copy.deepcopy(fixture_detail("console-authorize-sg-ingress.json"))
    detail["sourceIPAddress"] = source_ip
    detail["userIdentity"]["accessKeyId"] = "ASIAIOSFODNN7EXAMPLE"
    detail["userAgent"] = f"aws-cli/2.31.0 debug key={ACCESS_KEY}"
    detail["requestParameters"]["ipPermissions"]["items"][0]["ipRanges"]["items"].append(
        {"cidrIp": f"{source_ip}/32", "description": f"my laptop {source_ip}"}
    )
    detail["requestParameters"]["sessionToken"] = SESSION_TOKEN
    detail["requestParameters"]["note"] = (
        f"aws_secret_access_key={SECRET_KEY} token {SESSION_TOKEN} id {ACCESS_KEY}"
    )
    detail["errorMessage"] = f"password: hunter2-literal-value from {source_ip}"
    return lookup_event(detail)


def test_keys_tokens_and_the_source_ip_never_leave_the_server(
    tools: AwsTools, cloudtrail: Stubber
) -> None:
    expect(cloudtrail, [leaky_event()])

    result = tools.get_resource_changes(SG_ARN, 24)
    text = json.dumps(result)

    for secret in (
        ACCESS_KEY,
        "ASIAIOSFODNN7EXAMPLE",
        SECRET_KEY,
        SESSION_TOKEN,
        "hunter2-literal-value",
        "198.51.100.23",
    ):
        assert secret not in text, secret
    [event] = result["events"]
    assert event["source_ip"] == "198.51.100.x"
    ranges = event["untrusted_data"]["request_parameters"]["ipPermissions"]["items"][0]
    # The world-open rule stays readable; the caller's own /32 keeps only its network.
    assert [r["cidrIp"] for r in ranges["ipRanges"]["items"]] == ["0.0.0.0/0", "198.51.100.x/32"]
    assert event["untrusted_data"]["request_parameters"]["sessionToken"] == "[REDACTED]"


def test_ipv6_and_service_callers(tools: AwsTools, cloudtrail: Stubber) -> None:
    internal = fixture_detail("console-s3-aws-internal.json")
    expect(cloudtrail, [leaky_event("2001:db8:1234:5678::9"), lookup_event(internal)])

    events = tools.get_resource_changes(SG_ARN, 24)["events"]

    assert events[0]["source_ip"] == "2001:db8:1234::/48"
    assert "2001:db8:1234:5678::9" not in json.dumps(events)
    assert events[1]["source_ip"] == internal["sourceIPAddress"]


# --- caps ----------------------------------------------------------------------------------


def numbered(n: int) -> dict[str, Any]:
    detail = copy.deepcopy(fixture_detail("console-authorize-sg-ingress.json"))
    detail["eventID"] = f"event-{n}"
    return lookup_event(detail)


def test_events_per_call_are_capped(tools: AwsTools, cloudtrail: Stubber) -> None:
    expect(cloudtrail, [numbered(n) for n in range(15)], next_token="t1")
    expect(cloudtrail, [numbered(n) for n in range(15, 30)], token="t1")

    result = tools.get_resource_changes(SG_ARN, 24)

    assert len(result["events"]) == tools_module.MAX_EVENTS
    assert result["events"][0]["event_id"] == "event-0"
    assert result["truncated"] is True


def test_pages_per_call_are_capped(tools: AwsTools, cloudtrail: Stubber) -> None:
    expect(cloudtrail, [numbered(1)], next_token="t1")
    expect(cloudtrail, [numbered(2)], token="t1", next_token="t2")
    expect(cloudtrail, [numbered(3)], token="t2", next_token="t3")

    result = tools.get_resource_changes(SG_ARN, 24)

    # Three requests, then stop: more events exist, so the result says it's partial.
    assert len(result["events"]) == 3
    assert result["truncated"] is True


def test_long_fields_are_cut(tools: AwsTools, cloudtrail: Stubber) -> None:
    detail = copy.deepcopy(fixture_detail("cli-put-role-policy.json"))
    detail["requestParameters"]["policyDocument"] = "x" * 10_000
    detail["requestParameters"]["many"] = list(range(500))
    expect(cloudtrail, [lookup_event(detail)])

    [event] = tools.get_resource_changes(SG_ARN, 24)["events"]

    params = event["untrusted_data"]["request_parameters"]
    assert params["policyDocument"].endswith("…[truncated]")
    assert len(params["policyDocument"]) < 4_100
    assert len(params["many"]) == tools_module.MAX_LIST_ITEMS
    assert event["truncated"] is True


def test_oversized_parameters_become_a_cut_string(tools: AwsTools, cloudtrail: Stubber) -> None:
    detail = copy.deepcopy(fixture_detail("cli-put-role-policy.json"))
    detail["requestParameters"] = {f"k{i}": "v" * 3_000 for i in range(20)}
    expect(cloudtrail, [lookup_event(detail)])

    [event] = tools.get_resource_changes(SG_ARN, 24)["events"]

    params = event["untrusted_data"]["request_parameters"]
    assert isinstance(params, str) and params.endswith("…[truncated]")
    assert len(json.dumps(event)) < tools_module.MAX_EVENT_BYTES


def test_whole_result_is_capped(
    tools: AwsTools, cloudtrail: Stubber, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools_module, "MAX_RESULT_BYTES", 8_000)
    expect(cloudtrail, [numbered(n) for n in range(10)])

    result = tools.get_resource_changes(SG_ARN, 24)

    assert 0 < len(result["events"]) < 10
    assert result["truncated"] is True
    assert len(json.dumps(result)) <= 8_000


def test_unparseable_records_are_skipped(tools: AwsTools, cloudtrail: Stubber) -> None:
    broken = {**sg_event(), "CloudTrailEvent": "{not json"}
    expect(cloudtrail, [broken, sg_event()])

    assert len(tools.get_resource_changes(SG_ARN, 24)["events"]) == 1


# --- cache and throttling ------------------------------------------------------------------


def test_identical_lookups_are_cached_briefly(
    tools: AwsTools, cloudtrail: Stubber, clock: Clock
) -> None:
    expect(cloudtrail, [sg_event()])

    first = tools.get_resource_changes(SG_ARN, 24)
    clock.now += 60
    second = tools.get_resource_changes(SG_ARN, 24)

    # No second LookupEvents: the Stubber would fail on an unexpected request.
    assert first["cached"] is False and second["cached"] is True
    assert second["events"] == first["events"]

    clock.now += 61
    expect(cloudtrail, [])
    assert tools.get_resource_changes(SG_ARN, 24)["events"] == []


def test_different_arguments_are_separate_entries(tools: AwsTools, cloudtrail: Stubber) -> None:
    expect(cloudtrail, [sg_event()])
    cloudtrail.add_response("lookup_events", {"Events": []})

    tools.get_resource_changes(SG_ARN, 24)
    assert tools.get_resource_changes(SG_ARN, 48)["cached"] is False


def test_throttling_returns_what_it_has_and_isnt_cached(
    tools: AwsTools, cloudtrail: Stubber
) -> None:
    expect(cloudtrail, [numbered(1)], next_token="t1")
    cloudtrail.add_client_error("lookup_events", "ThrottlingException", "Rate exceeded")

    result = tools.get_resource_changes(SG_ARN, 24)

    assert result["throttled"] is True
    assert [e["event_id"] for e in result["events"]] == ["event-1"]
    assert "Retry in a minute" in result["note"]

    expect(cloudtrail, [])
    assert tools.get_resource_changes(SG_ARN, 24)["cached"] is False


def test_cache_can_be_turned_off(settings, reader, clock: Clock, cloudtrail: Stubber) -> None:
    tools = AwsTools(
        settings.model_copy(update={"lookup_cache_seconds": 0}), reader, lambda: NOW, clock
    )
    expect(cloudtrail, [])
    expect(cloudtrail, [])

    tools.get_resource_changes(SG_ARN, 24)
    assert tools.get_resource_changes(SG_ARN, 24)["cached"] is False


def test_cache_is_bounded(tools: AwsTools, cloudtrail: Stubber) -> None:
    for _ in range(tools_module.CACHE_ENTRIES + 1):
        cloudtrail.add_response("lookup_events", {"Events": []})
    for hours in range(1, 73):
        tools.get_resource_changes(SG_ARN, hours)
    for n in range(tools_module.CACHE_ENTRIES + 1 - 72):
        tools.get_resource_changes(f"{SG_ARN}{n}", 1)

    assert len(tools._lookups._items) == tools_module.CACHE_ENTRIES


def test_other_errors_are_tool_errors(tools: AwsTools, cloudtrail: Stubber) -> None:
    cloudtrail.add_client_error("lookup_events", "InvalidTimeRangeException", "bad range")
    cloudtrail.add_client_error("lookup_events", "AccessDeniedException", "no")

    with pytest.raises(ToolError, match="LookupEvents failed: InvalidTimeRangeException"):
        tools.get_resource_changes(SG_ARN, 24)
    with pytest.raises(ToolError, match="denied to aws-mcp's IAM role"):
        tools.get_resource_changes(SG_ARN, 12)


# --- get_principal_activity ----------------------------------------------------------------


def test_activity_of_a_session(tools: AwsTools, cloudtrail: Stubber) -> None:
    same_session_other_role = copy.deepcopy(fixture_detail("console-authorize-sg-ingress.json"))
    same_session_other_role["userIdentity"]["arn"] = (
        "arn:aws:sts::123456789012:assumed-role/OtherRole/alex@example.com"
    )
    expect(
        cloudtrail,
        [
            sg_event(),
            lookup_event(same_session_other_role),
            lookup_event(fixture_detail("excluded/read-only-describe.json")),
        ],
        {"AttributeKey": "Username", "AttributeValue": "alex@example.com"},
    )

    result = tools.get_principal_activity(SSO_SESSION, 24)

    assert result["principal_arn"] == SSO_SESSION
    assert [e["principal_arn"] for e in result["events"]] == [SSO_SESSION]


def test_activity_of_a_user(tools: AwsTools, cloudtrail: Stubber) -> None:
    expect(
        cloudtrail,
        [
            lookup_event(fixture_detail("cli-put-role-policy.json")),
            lookup_event(fixture_detail("cli-delete-role-policy.json")),
            sg_event(),
        ],
        {"AttributeKey": "Username", "AttributeValue": "alex"},
    )

    events = tools.get_principal_activity("arn:aws:iam::123456789012:user/alex", 24)["events"]

    assert [e["event_name"] for e in events] == ["PutRolePolicy", "DeleteRolePolicy"]


def test_activity_of_a_role_scans_writes(tools: AwsTools, cloudtrail: Stubber) -> None:
    expect(
        cloudtrail,
        [sg_event(), lookup_event(fixture_detail("cli-put-role-policy.json"))],
        {"AttributeKey": "ReadOnly", "AttributeValue": "false"},
    )

    events = tools.get_principal_activity(
        "arn:aws:iam::123456789012:role/AWSReservedSSO_Admin_0example", 24
    )["events"]

    assert [e["event_name"] for e in events] == ["AuthorizeSecurityGroupIngress"]


@pytest.mark.parametrize(
    "arn",
    [
        "arn:aws:s3:::demo-app-data",
        "arn:aws:sts::123456789012:assumed-role/OnlyRole",
        "arn:aws:iam::123456789012:group/admins",
    ],
)
def test_activity_needs_a_principal(tools: AwsTools, cloudtrail: Stubber, arn: str) -> None:
    with pytest.raises(ToolError, match="principal_arn must be"):
        tools.get_principal_activity(arn, 24)
