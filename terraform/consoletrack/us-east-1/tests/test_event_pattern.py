"""Checks patterns/changes.json, the intake's EventBridge pattern, against recorded events.

Run by `make test` (no AWS, no Terraform). A small local matcher implements the subset of
EventBridge pattern syntax the file uses: exact values, nested fields, `exists`, and
`anything-but` with `wildcard`. Any other operator fails the test, so extending the pattern
means extending the matcher. Humans can cross-check against the real service with:

    aws events test-event-pattern \
        --event-pattern file://terraform/consoletrack/us-east-1/patterns/changes.json \
        --event file://terraform/consoletrack/us-east-1/tests/fixtures/events/matched/canary-create-tags.json
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATTERN = json.loads((ROOT / "patterns" / "changes.json").read_text())
EVENTS = ROOT / "tests" / "fixtures" / "events"
ACCOUNT_ID = "123456789012"

_MISSING = object()


def _wildcard(pattern: str, value: str) -> bool:
    # EventBridge's `*` matches any run of characters, "/" and ":" included.
    return re.fullmatch(".*".join(re.escape(p) for p in pattern.split("*")), value) is not None


def _condition(cond, value) -> bool:
    if not isinstance(cond, dict):
        return value is not _MISSING and value == cond
    (op, arg), *rest = cond.items()
    assert not rest, f"one operator per condition: {cond}"
    if op == "exists":
        return (value is not _MISSING) is arg
    if op == "anything-but":
        if value is _MISSING:
            return False  # EventBridge never matches a missing field with anything-but
        assert isinstance(arg, dict) and set(arg) == {"wildcard"}, f"unsupported: {cond}"
        wildcards = arg["wildcard"] if isinstance(arg["wildcard"], list) else [arg["wildcard"]]
        return isinstance(value, str) and not any(_wildcard(w, value) for w in wildcards)
    raise AssertionError(f"matcher doesn't support {op!r}; extend it with the pattern")


def matches(pattern: dict, event: dict) -> bool:
    for key, rule in pattern.items():
        value = event.get(key, _MISSING) if isinstance(event, dict) else _MISSING
        if isinstance(rule, dict):
            if not isinstance(value, dict) or not matches(rule, value):
                return False
        else:
            values = value if isinstance(value, list) else [value]
            if not any(_condition(c, v) for c in rule for v in values):
                return False
    return True


def _load(kind: str) -> list[tuple[str, dict]]:
    return [(p.name, json.loads(p.read_text())) for p in sorted((EVENTS / kind).glob("*.json"))]


MATCHED = _load("matched")
EXCLUDED = _load("excluded")


@pytest.mark.parametrize(("name", "event"), MATCHED, ids=[n for n, _ in MATCHED])
def test_out_of_band_writes_match(name, event):
    assert matches(PATTERN, event), f"{name} should reach the change queue"


@pytest.mark.parametrize(("name", "event"), EXCLUDED, ids=[n for n, _ in EXCLUDED])
def test_pipeline_services_and_platform_are_excluded(name, event):
    assert not matches(PATTERN, event), f"{name} should be filtered out"


def test_required_cases_are_present():
    # The cases issue #8 asks for, by file name.
    assert {n for n, _ in MATCHED} >= {
        "console-authorize-sg-ingress.json",
        "cli-put-role-policy.json",
        "laptop-terraform-modify-sg-rules.json",
        "canary-create-tags.json",
        "console-s3-aws-internal.json",
    }
    assert {n for n, _ in EXCLUDED} >= {
        "pipeline-gh-session.json",
        "invoked-by-service.json",
        "invoked-by-ec2-service.json",
        "ebs-csi-create-volume.json",
    }


def test_canary_is_never_excluded():
    excluded = PATTERN["detail"]["userIdentity"]["arn"][0]["anything-but"]["wildcard"]
    arn = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/ct-canary-use1-a1b2c3/eks-session"
    assert not any(_wildcard(w, arn) for w in excluded)


def test_fixtures_use_the_placeholder_account_only():
    for path in EVENTS.rglob("*.json"):
        numbers = set(re.findall(r"(?<!\d)\d{12,}(?!\d)", path.read_text()))
        assert numbers <= {ACCOUNT_ID}, f"{path.name} has other 12+ digit numbers: {numbers}"


def test_matcher_rejects_operators_it_does_not_know():
    with pytest.raises(AssertionError, match="doesn't support"):
        matches({"a": [{"prefix": "x"}]}, {"a": "xy"})
