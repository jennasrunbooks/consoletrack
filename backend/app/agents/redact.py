"""Redaction of credentials in tool results and arguments, before they are stored as evidence or
sent to the model (product-spec.md → Triage agent and guardrails → Guardrails).

The MCP servers redact first; this is the backend's own pass, so evidence stays redacted even if
a server misses something or a new server forgets to. The rules are the same as
`mcp-server/k8s/k8s_mcp/redact.py` (a separate uv project, so it can't be imported); keep the two
in sync. Best effort and deliberately greedy: a redacted word costs the agent little, a leaked
token costs a lot.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Key names whose value is a credential, in `key=value`, `key: value`, and `"key": "value"` form.
_SECRET_KEY = (
    r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|credentials?|auth)[\w.-]*"
)

_RULES: list[tuple[re.Pattern[str], str]] = [
    # PEM private keys, including a key cut off by the line or byte cap.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
        ),
        f"{REDACTED}(private-key)",
    ),
    # Authorization header values.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    # JWTs (ServiceAccount tokens, OIDC tokens).
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"),
        f"{REDACTED}(jwt)",
    ),
    # AWS access key IDs (long-term AKIA, temporary ASIA).
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), f"{REDACTED}(aws-access-key-id)"),
    # Provider tokens with a recognizable prefix.
    (
        re.compile(
            r"\b(?:sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,})"
        ),
        f"{REDACTED}(token)",
    ),
    # Credentials in connection strings: scheme://user:password@host.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@"), rf"\1{REDACTED}@"),
    # key=value (query strings, env dumps, flags): any non-empty value.
    (
        re.compile(rf"(?i)\b({_SECRET_KEY})(\s*=\s*[\"']?)([^\s\"',;&]+)"),
        rf"\1\2{REDACTED}",
    ),
    # "key": "value" (JSON).
    (
        re.compile(rf"(?i)(\"{_SECRET_KEY}\"\s*:\s*\")([^\"]+)"),
        rf"\1{REDACTED}",
    ),
    # key: value (YAML, log fields). Only values of 8+ characters, so error messages such as
    # "ExpiredToken: The security token ..." keep their wording.
    (
        re.compile(rf"(?i)\b({_SECRET_KEY})(:\s*)([^\s\"',;&]{{8,}})"),
        rf"\1\2{REDACTED}",
    ),
]


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """`redact` applied to every string inside a JSON-like value (tool arguments)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value
