from __future__ import annotations

import pytest

from k8s_mcp.redact import redact


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstuvwxyz"),
        ("auth basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
        ("token eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl", "eyJhbGciOiJSUzI1NiJ9"),
        ("key AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        ("temp ASIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE"),
        ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
        ("x sk-ant-api03-abcdefghijkl y", "sk-ant-api03-abcdefghijkl"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnop"),
        ("github_pat_11ABCDEFG0123456789_abcdefghij", "github_pat_11ABCDEFG"),
        ("mysql://root:toor@db/app", "toor"),
        ("redis://default:p%40ss@cache:6379", "p%40ss"),
        ("DATABASE_URL=postgres://u:pw@h/db", "pw@"),
        ("password=hunter2", "hunter2"),
        ("PASSWORD = 'hunter2'", "hunter2"),
        ("/cb?code=1&access_token=abc123&x=1", "abc123"),
        ('{"client_secret": "very secret value"}', "very secret value"),
        ("db_password: correcthorsebattery", "correcthorsebattery"),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----",
            "MIIEow",
        ),
        ("-----BEGIN PRIVATE KEY-----\nMIIEtruncated", "MIIEtruncated"),
    ],
)
def test_redacts(text: str, secret: str) -> None:
    out = redact(text)
    assert secret not in out
    assert "[REDACTED]" in out


@pytest.mark.parametrize(
    "text",
    [
        "fatal error: runtime: out of memory",
        "api error ExpiredToken: The security token included in the request is expired",
        "AccessDenied: User: arn:aws:sts::123456789012:assumed-role/demo-app/x is not authorized",
        "GET /healthz 200 12ms",
        "Liveness probe failed: HTTP probe failed with statuscode: 404",
    ],
)
def test_leaves_ordinary_lines_alone(text: str) -> None:
    assert redact(text) == text
