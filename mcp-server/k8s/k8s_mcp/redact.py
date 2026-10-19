"""Redaction of credentials in text that came from the cluster (logs, events, commands).

Best effort and deliberately greedy: a redacted word in an error message costs the agent little,
a leaked token costs a lot. Runs before anything leaves the server, so neither the model nor the
evidence table sees the original value. The backend redacts again before storing evidence.
"""

from __future__ import annotations

import re

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
