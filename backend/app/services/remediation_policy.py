"""Remediation policies: the chart's `remediation:` block (values.yaml → ConfigMap
`remediation-policies` → REMEDIATION_POLICIES_FILE), parsed and checked.

A config that fails these checks stops the worker and the backend from starting, so a typo can
never quietly widen what runs in `auto`:

- unknown keys are errors;
- `auto` is only for a policy that names a cluster kind the backend can apply and verify
  (`set_memory_limit` today), never an AWS-side kind and never a catch-all `match: {}`;
- bounds may only be tighter than the validator's own (2x the current limit, 512Mi);
- `allowedChanges` may only name the fields the kind can change.

No file (REMEDIATION_POLICIES_FILE unset: tests, plain Compose) means no policies: every
proposal goes to a human.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.enums import AWS_REMEDIATION_KINDS, Confidence, RemediationKind, RemediationMode
from app.services.quantity import InvalidQuantity, parse_bytes

# The validator's own limits; a policy can tighten them, never loosen them.
MAX_MULTIPLIER = 2.0
CEILING = "512Mi"
# The only kinds the backend can apply and verify, so the only ones a policy may set to `auto`.
AUTO_KINDS = frozenset({RemediationKind.set_memory_limit})
MEMORY_FIELDS = ("resources.limits.memory", "resources.requests.memory")
DEFAULT_POLICY = "default"

_DURATION = re.compile(r"^([1-9][0-9]*)(s|m|h)$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours"}


class PolicyConfigError(ValueError):
    pass


def parse_duration(value: object) -> timedelta:
    if isinstance(value, timedelta):
        return value
    match = _DURATION.match(str(value))
    if match is None:
        raise ValueError(f"{value!r} is not a duration like 30s, 5m, or 1h")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


Duration = Annotated[timedelta, BeforeValidator(parse_duration)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Match(_Config):
    kind: RemediationKind | None = None
    namespace: str | None = None


class Bounds(_Config):
    max_multiplier: Annotated[float, Field(alias="maxMultiplier", gt=1)] = MAX_MULTIPLIER
    ceiling: str = CEILING

    @field_validator("ceiling")
    @classmethod
    def _ceiling(cls, value: str) -> str:
        try:
            parse_bytes(value)
        except InvalidQuantity as exc:
            raise ValueError(str(exc)) from None
        return value

    @model_validator(mode="after")
    def _tighter(self) -> Self:
        if self.max_multiplier > MAX_MULTIPLIER:
            raise ValueError(f"maxMultiplier may not exceed {MAX_MULTIPLIER:g}")
        if parse_bytes(self.ceiling) > parse_bytes(CEILING):
            raise ValueError(f"ceiling may not exceed {CEILING}")
        return self

    @property
    def ceiling_bytes(self) -> int:
        return parse_bytes(self.ceiling)


class RateLimit(_Config):
    per_workload: Annotated[int, Field(alias="perWorkload", ge=1)] = 1
    window: Duration = timedelta(hours=1)


class ErrorRatio(_Config):
    # PromQL returning one ratio (0-1); the fix holds when it drops below `below`.
    query: Annotated[str, Field(min_length=1)]
    below: Annotated[float, Field(gt=0, lt=1)]


class Verify(_Config):
    rollout_timeout: Annotated[Duration, Field(alias="rolloutTimeout")] = timedelta(minutes=3)
    wait_for_resolved: Annotated[Duration, Field(alias="waitForResolved")] = timedelta(minutes=5)
    error_ratio: Annotated[ErrorRatio | None, Field(alias="errorRatio")] = None
    on_failure: Annotated[Literal["rollback"], Field(alias="onFailure")] = "rollback"


class Policy(_Config):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    match: Match = Match()
    mode: RemediationMode = RemediationMode.manual
    allowed_changes: Annotated[tuple[str, ...], Field(alias="allowedChanges")] = MEMORY_FIELDS
    bounds: Bounds = Bounds()
    min_confidence: Annotated[Confidence | None, Field(alias="minConfidence")] = None
    rate_limit: Annotated[RateLimit, Field(alias="rateLimit")] = RateLimit()
    verify: Verify = Verify()

    @model_validator(mode="after")
    def _auto_is_narrow(self) -> Self:
        unknown = set(self.allowed_changes) - set(MEMORY_FIELDS)
        if unknown:
            raise ValueError(f"allowedChanges may only list {', '.join(MEMORY_FIELDS)}")
        if self.mode is not RemediationMode.auto:
            return self
        kind = self.match.kind
        if kind in AWS_REMEDIATION_KINDS:
            raise ValueError(f"{kind} changes AWS and can never be auto (Revert or Codify only)")
        if kind not in AUTO_KINDS:
            raise ValueError(
                "auto needs match.kind set to a kind the backend can apply and verify: "
                + ", ".join(sorted(AUTO_KINDS))
            )
        if not self.match.namespace:
            raise ValueError("auto needs match.namespace")
        return self

    def matches(self, kind: RemediationKind, namespace: str | None) -> bool:
        if self.match.kind is not None and self.match.kind != kind:
            return False
        return self.match.namespace is None or self.match.namespace == namespace


MANUAL_DEFAULT = Policy(name=DEFAULT_POLICY)


class PolicyConfig(_Config):
    auto_enabled: Annotated[bool, Field(alias="autoEnabled")] = False
    policies: tuple[Policy, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> Self:
        names = [policy.name for policy in self.policies]
        if len(names) != len(set(names)):
            raise ValueError("policy names must be unique")
        return self

    def policy_for(self, kind: RemediationKind, namespace: str | None) -> Policy:
        """The first policy that matches, else an all-manual default."""
        return next((p for p in self.policies if p.matches(kind, namespace)), MANUAL_DEFAULT)


NO_POLICIES = PolicyConfig()


def parse_policies(text: str) -> PolicyConfig:
    try:
        return PolicyConfig.model_validate(yaml.safe_load(text) or {})
    except (yaml.YAMLError, ValidationError) as exc:
        raise PolicyConfigError(f"invalid remediation policies: {exc}") from None


def load_policies(path: str | None) -> PolicyConfig:
    if not path:
        return NO_POLICIES
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise PolicyConfigError(f"can't read REMEDIATION_POLICIES_FILE {path}: {exc}") from None
    return parse_policies(text)
