"""The only AWS API calls aws-mcp can make.

Every call goes through `AwsReader.call(service, operation, **params)`, which refuses any
(service, operation) outside `ALLOWED_OPERATIONS` before a request is built. The same check runs
again as a botocore `before-parameter-build` hook on every client this module creates, so a
client used directly (by mistake, or by a future change) can't make an unlisted call either. S3
`GetObject` is further pinned to the one state object in `Settings`.

Every operation here is a read, and each one is granted by the `aws_mcp` policy in
`terraform/consoletrack/us-east-1/pod-identity.tf` (a unit test compares the two lists). IAM
denies everything else again in AWS.
"""

from __future__ import annotations

from typing import Any

import boto3
from botocore import xform_name
from botocore.config import Config

from aws_mcp.settings import Settings

ALLOWED_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("cloudtrail", "LookupEvents"),
        ("iam", "GetRole"),
        ("iam", "ListRolePolicies"),
        ("iam", "GetRolePolicy"),
        ("iam", "ListAttachedRolePolicies"),
        ("iam", "GetPolicy"),
        ("iam", "GetPolicyVersion"),
        ("iam", "SimulatePrincipalPolicy"),
        ("ec2", "DescribeSecurityGroups"),
        ("s3", "GetObject"),
    }
)

# LookupEvents is rate limited per account; adaptive mode backs off client side on throttling.
_CONFIG = Config(
    retries={"mode": "adaptive", "max_attempts": 4},
    connect_timeout=5,
    read_timeout=15,
    user_agent_extra="consoletrack-aws-mcp",
)


class OperationNotAllowed(Exception):
    """A call outside the allowlist, or S3 GetObject of anything but the state object."""


class AwsReader:
    """Lazily created boto3 clients, guarded by the allowlist.

    `session` is for tests and local use (`AWS_PROFILE` works through the default session).
    """

    def __init__(self, settings: Settings, session: boto3.Session | None = None) -> None:
        self._region = settings.aws_region
        self._state = (settings.tf_state_bucket, settings.tf_state_key)
        self._session = session or boto3.Session()
        self._clients: dict[str, Any] = {}

    def call(self, service: str, operation: str, **params: Any) -> dict[str, Any]:
        self._check(service, operation, params)
        return getattr(self._client(service), xform_name(operation))(**params)

    def credentials_available(self) -> bool:
        """Readiness: credentials resolve (Pod Identity or a profile). No API call."""
        return self._session.get_credentials() is not None

    def _client(self, service: str) -> Any:
        if service not in self._clients:
            client = self._session.client(service, region_name=self._region, config=_CONFIG)
            client.meta.events.register("before-parameter-build", self._guard)
            self._clients[service] = client
        return self._clients[service]

    def _guard(self, params: dict[str, Any], model: Any, **_: Any) -> None:
        self._check(model.service_model.service_name, model.name, params)

    def _check(self, service: str, operation: str, params: dict[str, Any]) -> None:
        if (service, operation) not in ALLOWED_OPERATIONS:
            raise OperationNotAllowed(f"aws-mcp never calls {service}:{operation}")
        if (service, operation) == ("s3", "GetObject"):
            bucket, key = self._state
            if not bucket or params.get("Bucket") != bucket or params.get("Key") != key:
                raise OperationNotAllowed("aws-mcp reads only the demo-app root's state object")
