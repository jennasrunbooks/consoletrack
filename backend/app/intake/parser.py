"""CloudTrail event parsing: one SQS message body in, one `ParsedEvent` out. Pure functions, no I/O.

The EventBridge rule (`terraform/consoletrack/us-east-1/patterns/changes.json`) already drops
reads, AWS-service calls, the cluster's own roles, and pipeline sessions. `ignore_reason`
repeats those checks as defense in depth, so a widened rule doesn't turn the cluster's own
traffic into change records.

Everything in a CloudTrail record that the caller controls (request parameters, the user agent,
the session name) is untrusted text. It is stored capped and with credential-like fields
removed, and it never goes into a log line; `IntakeError` messages name fields, not values.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agents.redact import REDACTED, redact
from app.enums import Risk, Via
from app.services.changes import NewChange

DETAIL_TYPE = "AWS API Call via CloudTrail"
# The rule's `source` list (aws.iam, aws.ec2, ...), as CloudTrail eventSource values.
WATCHED_SERVICES = frozenset({"iam", "ec2", "s3", "kms", "eks"})
# Session issuers the rule excludes: the cluster's node, CNI, CSI, load balancer, DNS roles.
CLUSTER_ROLE_PREFIXES = ("ct-eks-", "ct-vpc-cni-", "ct-ebs-csi-", "ct-lbc-", "ct-external-dns-")
PIPELINE_ROLE_PREFIX = "ct-gh-"
PIPELINE_SESSION_PREFIX = "gh-"
CANARY_ROLE_PREFIX = "ct-canary-"
CANARY_TAG_KEY = "ct:canary"

MAX_REQUEST_PARAMETERS_BYTES = 8_192
MAX_STRING_CHARS = 1_024
MAX_USER_AGENT_CHARS = 512
MAX_DEPTH = 12
TRUNCATED = "…[truncated]"

# Request-parameter keys dropped wherever they appear (lowercased, without - and _).
CREDENTIAL_KEYS = frozenset(
    {
        "accesskeyid",
        "secretaccesskey",
        "sessiontoken",
        "password",
        "newpassword",
        "oldpassword",
        "passwordhash",
        "secret",
        "secretstring",
        "secretbinary",
        "clientsecret",
        "privatekey",
        "token",
        "authtoken",
        "credentials",
        "userdata",
    }
)


class IntakeError(ValueError):
    """The message isn't a CloudTrail event this parser can read. SQS redelivers it, then the
    dead-letter queue keeps it."""


@dataclass(frozen=True)
class ParserConfig:
    # WATCHED_IAM_PATHS, e.g. ("/demo-app/",).
    watched_iam_paths: tuple[str, ...] = ("/demo-app/",)
    # CANARY_SECURITY_GROUP_ID; unset accepts any one security group (the canary role can only
    # tag its own group anyway).
    canary_group_id: str | None = None


@dataclass(frozen=True)
class CanaryHeartbeat:
    event_time: datetime


@dataclass(frozen=True)
class Ignored:
    reason: str


type ParsedEvent = NewChange | CanaryHeartbeat | Ignored


def parse(body: str | bytes, config: ParserConfig) -> ParsedEvent:
    try:
        envelope = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise IntakeError("message body isn't JSON") from None
    if not isinstance(envelope, dict):
        raise IntakeError("message body isn't a JSON object")
    if envelope.get("detail-type") != DETAIL_TYPE:
        return Ignored("not a CloudTrail API call")
    detail = envelope.get("detail")
    if not isinstance(detail, dict):
        raise IntakeError("EventBridge event without a detail object")
    return parse_record(detail, config)


def parse_record(detail: dict[str, Any], config: ParserConfig) -> ParsedEvent:
    """One CloudTrail record (the EventBridge `detail`)."""
    event = _Event(detail)
    reason = ignore_reason(event)
    if reason is not None:
        return Ignored(reason)
    if is_canary(event, config):
        return CanaryHeartbeat(event.time)
    service = event.service
    resource_arn, resource_name = resource_of(event)
    return NewChange(
        cloudtrail_event_id=event.require("eventID"),
        event_time=event.time,
        principal_arn=event.principal_arn,
        via=via_of(event),
        region=event.require("awsRegion"),
        service=service,
        event_name=event.name,
        resource_arn=resource_arn,
        resource_name=resource_name,
        session_name=_cap(event.session_name, 256),
        source_ip=_cap(event.str_field("sourceIPAddress"), 256),
        user_agent=_cap(event.user_agent, MAX_USER_AGENT_CHARS),
        request_parameters=sanitize_parameters(detail.get("requestParameters")),
        risk=risk_of(event, config),
    )


class _Event:
    """Typed access to the parts of a CloudTrail record the rules read."""

    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        identity = detail.get("userIdentity")
        self.identity: dict[str, Any] = identity if isinstance(identity, dict) else {}
        context = self.identity.get("sessionContext")
        self.context: dict[str, Any] = context if isinstance(context, dict) else {}
        issuer = self.context.get("sessionIssuer")
        self.issuer: dict[str, Any] = issuer if isinstance(issuer, dict) else {}
        params = detail.get("requestParameters")
        self.params: dict[str, Any] = params if isinstance(params, dict) else {}

    def str_field(self, name: str, source: dict[str, Any] | None = None) -> str | None:
        value = (self.detail if source is None else source).get(name)
        return value if isinstance(value, str) and value else None

    def require(self, name: str) -> str:
        value = self.str_field(name)
        if value is None:
            raise IntakeError(f"CloudTrail record without {name}")
        return value

    @property
    def name(self) -> str:
        return self.require("eventName")

    @property
    def service(self) -> str:
        return self.require("eventSource").removesuffix(".amazonaws.com")

    @property
    def time(self) -> datetime:
        raw = self.require("eventTime")
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            raise IntakeError("CloudTrail record with an unreadable eventTime") from None
        if value.tzinfo is None:
            raise IntakeError("CloudTrail record with an eventTime without a time zone")
        return value

    @property
    def account(self) -> str:
        return (
            self.str_field("recipientAccountId")
            or self.str_field("accountId", self.identity)
            or "unknown"
        )

    @property
    def principal_arn(self) -> str:
        # An assumed role's stable ARN is its issuer's; the session goes in session_name.
        arn = self.str_field("arn", self.issuer) or self.str_field("arn", self.identity)
        if arn is None:
            raise IntakeError("CloudTrail record without a principal ARN")
        return arn

    @property
    def role_name(self) -> str:
        return self.str_field("userName", self.issuer) or ""

    @property
    def session_name(self) -> str | None:
        if self.str_field("type", self.identity) != "AssumedRole":
            return None
        principal = self.str_field("principalId", self.identity) or ""
        _, sep, session = principal.partition(":")
        return session if sep and session else None

    @property
    def user_agent(self) -> str:
        return self.str_field("userAgent") or ""

    @property
    def from_console(self) -> bool:
        flags = (
            self.detail.get("sessionCredentialFromConsole"),
            self.context.get("sessionCredentialFromConsole"),
        )
        return any(str(flag).lower() == "true" for flag in flags)

    @property
    def invoked_by(self) -> str | None:
        return self.str_field("invokedBy", self.identity)


def ignore_reason(event: _Event) -> str | None:
    """Why the EventBridge rule should have dropped this event, or None."""
    if event.service not in WATCHED_SERVICES:
        return "unwatched service"
    if event.detail.get("readOnly") is True or str(event.detail.get("readOnly")) == "true":
        return "read-only call"
    if event.invoked_by not in (None, "AWS Internal"):
        return "called by an AWS service"
    if event.role_name.startswith(CLUSTER_ROLE_PREFIXES):
        return "the cluster's own role"
    if is_pipeline_role(event) and (event.session_name or "").startswith(PIPELINE_SESSION_PREFIX):
        return "pipeline session"
    if event.str_field("errorCode") is not None:
        # A denied or failed call changed nothing; CloudTrail still records it.
        return "the call failed"
    return None


def is_pipeline_role(event: _Event) -> bool:
    return event.role_name.startswith(PIPELINE_ROLE_PREFIX)


def is_canary(event: _Event, config: ParserConfig) -> bool:
    """The canary's own tag write, and nothing else from its role (anything else is a change)."""
    if not event.role_name.startswith(CANARY_ROLE_PREFIX) or event.name != "CreateTags":
        return False
    resources = [item.get("resourceId") for item in _items(event.params.get("resourcesSet"))]
    keys = {item.get("key") for item in _items(event.params.get("tagSet"))}
    if keys != {CANARY_TAG_KEY} or len(resources) != 1:
        return False
    group = resources[0]
    if config.canary_group_id:
        return group == config.canary_group_id
    return isinstance(group, str) and group.startswith("sg-")


# --- via -------------------------------------------------------------------------------------

CLOUDSHELL_MARKER = "exec-env/cloudshell"
CONSOLE_USER_AGENTS = ("console.amazonaws.com", "signin.amazonaws.com", "aws internal")
TERRAFORM_MARKERS = ("terraform/", "terraform-provider-aws/")

# First match wins (product-spec.md → What counts as out-of-band). CloudShell comes before the
# console flag: its credentials come from the console session, so CloudTrail sets both.
VIA_RULES: list[tuple[Via, Callable[[_Event, str], bool]]] = [
    (Via.cloudshell, lambda e, ua: CLOUDSHELL_MARKER in ua),
    (Via.console, lambda e, ua: e.from_console),
    (Via.cli, lambda e, ua: ua.startswith("aws-cli/") or " aws-cli/" in ua),
    (Via.terraform_outside_pipeline, lambda e, ua: any(m in ua for m in TERRAFORM_MARKERS)),
    (
        Via.console,
        lambda e, ua: (
            ua in CONSOLE_USER_AGENTS
            or ua.endswith(".console.aws.amazon.com")
            or e.invoked_by == "AWS Internal"
        ),
    ),
]


def via_of(event: _Event) -> Via:
    user_agent = event.user_agent.lower()
    for via, matches in VIA_RULES:
        if matches(event, user_agent):
            return via
    return Via.sdk


# --- resource ----------------------------------------------------------------------------------


def _items(value: Any) -> Iterator[dict[str, Any]]:
    """CloudTrail's EC2 lists: {"items": [...]}."""
    items = value.get("items") if isinstance(value, dict) else value
    if isinstance(items, list):
        yield from (item for item in items if isinstance(item, dict))


def _sg_id(params: dict[str, Any]) -> str | None:
    request = params.get("ModifySecurityGroupRulesRequest")
    if isinstance(request, dict):
        params = request
    for key in ("groupId", "GroupId"):
        if isinstance(params.get(key), str):
            return params[key]
    return None


def _tagged_resources(params: dict[str, Any]) -> list[str]:
    ids = [item.get("resourceId") for item in _items(params.get("resourcesSet"))]
    return [i for i in ids if isinstance(i, str)]


def _ec2_resource(event: _Event, region: str) -> tuple[str, str] | None:
    params = event.params
    if event.name in SG_RULE_EVENTS:
        group = _sg_id(params)
        if group:
            return f"arn:aws:ec2:{region}:{event.account}:security-group/{group}", group
    if event.name in ("CreateTags", "DeleteTags"):
        resources = _tagged_resources(params)
        if len(resources) == 1 and resources[0].startswith("sg-"):
            group = resources[0]
            return f"arn:aws:ec2:{region}:{event.account}:security-group/{group}", group
        if resources:
            return f"ec2:{event.name}:{','.join(resources)}", ",".join(resources)
    return None


def _iam_resource(event: _Event) -> tuple[str, str] | None:
    params, account = event.params, event.account
    policy_arn = params.get("policyArn")
    if event.name in IAM_POLICY_VERSION_EVENTS and isinstance(policy_arn, str):
        return policy_arn, policy_arn.rpartition("/")[2]
    role = params.get("roleName")
    if isinstance(role, str):
        # The record has the role's name, not its path; aws-mcp (#9 PR 2) resolves the path.
        return f"arn:aws:iam::{account}:role/{role}", role
    user = params.get("userName")
    if isinstance(user, str):
        return f"arn:aws:iam::{account}:user/{user}", user
    if isinstance(policy_arn, str):
        return policy_arn, policy_arn.rpartition("/")[2]
    return None


def _s3_resource(event: _Event) -> tuple[str, str] | None:
    bucket = event.params.get("bucketName")
    if isinstance(bucket, str):
        return f"arn:aws:s3:::{bucket}", bucket
    return None


def _kms_resource(event: _Event, region: str) -> tuple[str, str] | None:
    key = event.params.get("keyId")
    if not isinstance(key, str):
        return None
    if key.startswith("arn:"):
        return key, key.rpartition("/")[2]
    return f"arn:aws:kms:{region}:{event.account}:key/{key}", key


def _eks_resource(event: _Event, region: str) -> tuple[str, str] | None:
    response = event.detail.get("responseElements")
    association = response.get("association") if isinstance(response, dict) else None
    if isinstance(association, dict) and isinstance(association.get("associationArn"), str):
        arn = association["associationArn"]
        return arn, arn.rpartition("/")[2]
    cluster = event.params.get("clusterName") or event.params.get("name")
    if not isinstance(cluster, str):
        return None
    association_id = event.params.get("associationId")
    if isinstance(association_id, str):
        prefix = f"arn:aws:eks:{region}:{event.account}:podidentityassociation"
        return f"{prefix}/{cluster}/{association_id}", association_id
    return f"arn:aws:eks:{region}:{event.account}:cluster/{cluster}", cluster


def resource_of(event: _Event) -> tuple[str, str | None]:
    """(resource_arn, resource_name). Unknown events get a readable `service:event` id."""
    region = event.str_field("awsRegion") or "unknown"
    service = event.service
    found: tuple[str, str] | None = None
    if service == "ec2":
        found = _ec2_resource(event, region)
    elif service == "iam":
        found = _iam_resource(event)
    elif service == "s3":
        found = _s3_resource(event)
    elif service == "kms":
        found = _kms_resource(event, region)
    elif service == "eks":
        found = _eks_resource(event, region)
    if found is None:
        return f"{service}:{event.name}", None
    arn, name = found
    return arn[:2048], name[:512]


# --- risk --------------------------------------------------------------------------------------

SG_RULE_EVENTS = frozenset(
    {
        "AuthorizeSecurityGroupIngress",
        "AuthorizeSecurityGroupEgress",
        "RevokeSecurityGroupIngress",
        "RevokeSecurityGroupEgress",
        "ModifySecurityGroupRules",
        "UpdateSecurityGroupRuleDescriptionsIngress",
        "UpdateSecurityGroupRuleDescriptionsEgress",
    }
)
SG_OPENING_EVENTS = frozenset({"AuthorizeSecurityGroupIngress", "ModifySecurityGroupRules"})
ADMIN_PORTS = frozenset({22, 3389})
ANYWHERE = frozenset({"0.0.0.0/0", "::/0"})
IAM_POLICY_VERSION_EVENTS = frozenset({"CreatePolicyVersion", "SetDefaultPolicyVersion"})
IAM_PERMISSION_EVENTS = frozenset(
    {
        "PutRolePolicy",
        "DeleteRolePolicy",
        "AttachRolePolicy",
        "DetachRolePolicy",
        "UpdateAssumeRolePolicy",
        "PutRolePermissionsBoundary",
        "DeleteRolePermissionsBoundary",
        "PutUserPolicy",
        "DeleteUserPolicy",
        "AttachUserPolicy",
        "DetachUserPolicy",
        *IAM_POLICY_VERSION_EVENTS,
    }
)
TAG_EVENTS = frozenset(
    {
        "CreateTags",
        "DeleteTags",
        "TagRole",
        "UntagRole",
        "TagPolicy",
        "UntagPolicy",
        "TagResource",
        "UntagResource",
        "PutBucketTagging",
        "DeleteBucketTagging",
    }
)


@dataclass(frozen=True)
class _Rule:
    risk: Risk
    matches: Callable[[_Event, ParserConfig], bool]
    # Why, for the PR table and test ids.
    why: str


def _open_ports(event: _Event) -> Iterable[tuple[int | None, int | None, str]]:
    """(from, to, cidr) for every world-reachable range the call adds."""
    params = event.params
    for perm in _items(params.get("ipPermissions")):
        protocol = str(perm.get("ipProtocol"))
        low, high = (None, None) if protocol == "-1" else (perm.get("fromPort"), perm.get("toPort"))
        for item in _items(perm.get("ipRanges")):
            yield low, high, str(item.get("cidrIp"))
        for item in _items(perm.get("ipv6Ranges")):
            yield low, high, str(item.get("cidrIpv6"))
    request = params.get("ModifySecurityGroupRulesRequest")
    if isinstance(request, dict):
        rules = request.get("SecurityGroupRule")
        for entry in rules if isinstance(rules, list) else [rules]:
            rule = entry.get("SecurityGroupRule") if isinstance(entry, dict) else None
            if not isinstance(rule, dict):
                continue
            protocol = str(rule.get("IpProtocol"))
            low = None if protocol == "-1" else rule.get("FromPort")
            high = None if protocol == "-1" else rule.get("ToPort")
            for key in ("CidrIpv4", "CidrIpv6"):
                if key in rule:
                    yield low, high, str(rule[key])


def _covers_admin_port(low: Any, high: Any) -> bool:
    if low is None or high is None or low == -1:
        return True  # all ports
    try:
        return any(int(low) <= port <= int(high) for port in ADMIN_PORTS)
    except (TypeError, ValueError):
        return True


def _world_open(event: _Event, *, admin_only: bool) -> bool:
    if event.name not in SG_OPENING_EVENTS:
        return False
    return any(
        cidr in ANYWHERE and (not admin_only or _covers_admin_port(low, high))
        for low, high, cidr in _open_ports(event)
    )


def _iam_watched(event: _Event, config: ParserConfig) -> bool:
    """Whether the IAM resource sits under a watched path.

    A policy ARN carries its path. A role or user name doesn't: by the naming convention
    (AGENTS.md) watched roles are named after their path, so `/demo-app/` watches `demo-app-*`.
    """
    arn, name = resource_of(event)
    for path in config.watched_iam_paths:
        if f":policy{path}" in arn or f":role{path}" in arn or f":user{path}" in arn:
            return True
        segment = path.strip("/").split("/")[-1]
        if segment and name and name.startswith(f"{segment}-"):
            return True
    return False


# First match wins; nothing matched is medium.
RISK_RULES: list[_Rule] = [
    _Rule(
        Risk.high,
        lambda e, c: (
            is_pipeline_role(e) and not (e.session_name or "").startswith(PIPELINE_SESSION_PREFIX)
        ),
        "a pipeline role without a gh- session",
    ),
    _Rule(Risk.high, lambda e, c: _world_open(e, admin_only=True), "SSH/RDP open to the world"),
    _Rule(Risk.medium, lambda e, c: _world_open(e, admin_only=False), "a port open to the world"),
    _Rule(
        Risk.high,
        lambda e, c: e.service == "iam" and e.name in IAM_PERMISSION_EVENTS and _iam_watched(e, c),
        "IAM permissions on a watched path",
    ),
    _Rule(
        Risk.high,
        lambda e, c: (
            e.service == "s3"
            and e.name in {"DeleteBucketPublicAccessBlock", "DeletePublicAccessBlock"}
        ),
        "S3 public access block removed",
    ),
    _Rule(
        Risk.high,
        lambda e, c: (
            e.service == "kms" and e.name in {"PutKeyPolicy", "ScheduleKeyDeletion", "DisableKey"}
        ),
        "KMS key policy or key state",
    ),
    _Rule(Risk.low, lambda e, c: e.name in TAG_EVENTS, "tags only"),
]


def risk_of(event: _Event, config: ParserConfig) -> Risk:
    for rule in RISK_RULES:
        if rule.matches(event, config):
            return rule.risk
    return Risk.medium


# --- untrusted text ----------------------------------------------------------------------------


def _cap(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return value[: limit - len(TRUNCATED)] + TRUNCATED


def _normalized(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "")


def _scrub(value: Any, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return TRUNCATED
    if isinstance(value, dict):
        return {
            str(key)[:128]: REDACTED
            if _normalized(str(key)) in CREDENTIAL_KEYS
            else _scrub(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _cap(redact(value), MAX_STRING_CHARS)
    return value


def sanitize_parameters(params: Any) -> dict[str, Any] | None:
    """Request parameters as stored: credentials removed, strings and total size capped."""
    if params is None:
        return None
    scrubbed = _scrub(params if isinstance(params, dict) else {"value": params}, 0)
    encoded = json.dumps(scrubbed, sort_keys=True, default=str)
    if len(encoded.encode()) <= MAX_REQUEST_PARAMETERS_BYTES:
        return scrubbed
    excerpt = encoded.encode()[: MAX_REQUEST_PARAMETERS_BYTES - 512].decode(errors="ignore")
    return {"_truncated": True, "_original_bytes": len(encoded.encode()), "excerpt": excerpt}
