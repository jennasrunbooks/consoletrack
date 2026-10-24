"""Redaction rules and the settings' fail-closed checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aws_mcp.redact import REDACTED, mask_ip, redact_data, redact_text
from aws_mcp.settings import DEMO_APP_STATE_KEY, Settings


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("key AKIAIOSFODNN7EXAMPLE in a log", "AKIAIOSFODNN7EXAMPLE"),
        ("temp ASIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE"),
        ("IQoJb3JpZ2luX2VjEXAMPLEexampleEXAMPLE0123456789", "IQoJb3JpZ2luX2VjEXAMPLE"),
        ("FwoGZXIvYXdzEXAMPLEexampleEXAMPLE0123456789", "FwoGZXIvYXdzEXAMPLE"),
        ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG", "wJalrXUtnFEMI"),
        ('{"password": "hunter2-literal"}', "hunter2-literal"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ("postgres://app:s3cr3t-value@db:5432/app", "s3cr3t-value"),
        ("eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "eyJhbGciOi.eyJzdWIiOi"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abcdefghijklmnop"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "MIIE"),
    ],
)
def test_text_rules(text: str, leaked: str) -> None:
    assert leaked not in redact_text(text)


def test_error_wording_survives() -> None:
    message = "ExpiredToken: The security token included in the request is expired"
    assert redact_text(message) == message


def test_credential_fields_are_replaced_whole() -> None:
    data = {
        "userIdentity": {"accessKeyId": "ASIAEXAMPLE", "arn": "arn:aws:iam::123456789012:user/a"},
        "responseElements": {
            "credentials": {"sessionToken": "x", "secretAccessKey": "y", "expiration": "z"}
        },
        "requestParameters": {"tokenCode": "123456", "keyId": "alias/demo", "secret": None},
        "list": [{"Password": 42}],
    }

    redacted = redact_data(data)

    assert redacted["userIdentity"] == {
        "accessKeyId": REDACTED,
        "arn": "arn:aws:iam::123456789012:user/a",
    }
    assert redacted["responseElements"]["credentials"] == REDACTED
    # keyId names a key, it isn't one; a None value has nothing to hide.
    assert redacted["requestParameters"] == {
        "tokenCode": REDACTED,
        "keyId": "alias/demo",
        "secret": None,
    }
    assert redacted["list"] == [{"Password": REDACTED}]


def test_iam_condition_keys_arent_credentials() -> None:
    condition = {"DateGreaterThan": {"aws:TokenIssueTime": "2026-10-17T00:00:00Z"}}

    assert redact_data(condition) == condition


def test_replacements_apply_everywhere() -> None:
    data = {"a": "from 203.0.113.10", "b": ["203.0.113.10/32"], "c": 5}

    assert redact_data(data, {"203.0.113.10": "203.0.113.x"}) == {
        "a": "from 203.0.113.x",
        "b": ["203.0.113.x/32"],
        "c": 5,
    }


def test_replacements_match_whole_addresses() -> None:
    text = "203.0.113.1 and 203.0.113.10/32 and 1203.0.113.1, then 203.0.113.1."

    assert redact_data(text, {"203.0.113.1": "203.0.113.x"}) == (
        "203.0.113.x and 203.0.113.10/32 and 1203.0.113.1, then 203.0.113.x."
    )


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("198.51.100.23", "198.51.100.x"),
        ("2001:db8:1234:5678::9", "2001:db8:1234::/48"),
        ("AWS Internal", "AWS Internal"),
        ("ec2.amazonaws.com", "ec2.amazonaws.com"),
        ("", ""),
    ],
)
def test_mask_ip(value: str, masked: str) -> None:
    assert mask_ip(value) == masked


# --- settings ------------------------------------------------------------------------------


def test_watched_paths_are_split() -> None:
    settings = Settings(watched_iam_paths=" /demo-app/, /other/ ")

    assert settings.watched_iam_paths == ["/demo-app/", "/other/"]
    assert settings.tf_state_key == DEMO_APP_STATE_KEY


@pytest.mark.parametrize("paths", ["", "/", "demo-app", "/demo-app", ","])
def test_watched_paths_fail_closed(paths: str) -> None:
    with pytest.raises(ValidationError):
        Settings(watched_iam_paths=paths)


def test_no_watched_paths_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WATCHED_IAM_PATHS", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_only_the_demo_app_state_key() -> None:
    with pytest.raises(ValidationError, match="TF_STATE_KEY must be"):
        Settings(watched_iam_paths="/demo-app/", tf_state_key="consoletrack/terraform.tfstate")
