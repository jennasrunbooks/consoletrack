"""Redaction of credentials and the caller's address in everything aws-mcp returns.

Best effort and deliberately greedy, like k8s-mcp's: a redacted word costs the agent little, a
leaked key costs a lot. Runs before anything leaves the server, so neither the model nor the
evidence table sees the original value; the backend redacts again before storing evidence.

- `redact_text`: credential-shaped substrings (AWS access key IDs, JWTs, provider tokens,
  `key=value` secrets, connection-string passwords, PEM keys). The text rules match k8s-mcp's.
- `redact_data`: walks JSON-like data. A value under a credential-like key (`accessKeyId`,
  `sessionToken`, `password`, ...) is replaced whole; every other string goes through
  `redact_text`.
- `mask_ip`: an operator's address keeps only its network: /24 for IPv4 (`198.51.100.x`), /48
  for IPv6. Non-addresses (`AWS Internal`, a service name) pass through.

Account IDs stay: the agent needs ARNs, and the UI and recordings handle those.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

REDACTED = "[REDACTED]"

# Key names whose value is a credential, in `key=value`, `key: value`, and `"key": "value"` form.
_SECRET_KEY = (
    r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|credentials?|auth)[\w.-]*"
)
# A whole JSON key that holds a credential (accessKeyId, secretAccessKey, sessionToken, ...).
# Narrower than _SECRET_KEY: "auth" would catch authorizationType and similar plain fields.
_SECRET_FIELD = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|credentials?)"
)

_RULES: list[tuple[re.Pattern[str], str]] = [
    # PEM private keys, including a key cut off by a size cap.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
        ),
        f"{REDACTED}(private-key)",
    ),
    # Authorization header values.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    # JWTs.
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"),
        f"{REDACTED}(jwt)",
    ),
    # AWS access key IDs (long-term AKIA, temporary ASIA).
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), f"{REDACTED}(aws-access-key-id)"),
    # STS session tokens: long base64 blobs as STS issues them.
    (re.compile(r"\b(?:IQoJb3JpZ2lu|FwoGZXIvYXdz)[A-Za-z0-9/+=]{20,}"), f"{REDACTED}(token)"),
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
    # "key": "value" (JSON inside a string).
    (
        re.compile(rf"(?i)(\"{_SECRET_KEY}\"\s*:\s*\")([^\"]+)"),
        rf"\1{REDACTED}",
    ),
    # key: value. Only values of 8+ characters, so error messages such as
    # "ExpiredToken: The security token ..." keep their wording.
    (
        re.compile(rf"(?i)\b({_SECRET_KEY})(:\s*)([^\s\"',;&]{{8,}})"),
        rf"\1\2{REDACTED}",
    ),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_data(value: Any, replace: dict[str, str] | None = None) -> Any:
    """A redacted copy of JSON-like data. `replace` maps addresses (the caller's IP) to their
    masked form, applied to every string before the credential rules."""
    if isinstance(value, dict):
        return {
            # The whole value, including a structure such as AssumeRole's `credentials`. Keys
            # with a colon are IAM condition keys (aws:TokenIssueTime), never credentials.
            key: REDACTED
            if isinstance(key, str)
            and item is not None
            and ":" not in key
            and _SECRET_FIELD.search(key)
            else redact_data(item, replace)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item, replace) for item in value]
    if isinstance(value, str):
        for original, masked in (replace or {}).items():
            # Whole addresses only: masking 203.0.113.1 leaves 203.0.113.10 alone.
            value = re.sub(rf"(?<![\w.:]){re.escape(original)}(?![\w:])", masked, value)
        return redact_text(value)
    return value


def mask_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if isinstance(address, ipaddress.IPv4Address):
        return ".".join(value.split(".")[:3]) + ".x"
    return str(ipaddress.ip_network(f"{address}/48", strict=False))
