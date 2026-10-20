"""Kubernetes memory quantities, for the validator's comparisons.

Only the forms a memory limit takes in practice: a plain byte count, a binary suffix (Ki, Mi,
Gi, Ti), or a decimal one (k, M, G, T). Anything else (exponents, fractions, milli) is refused
rather than guessed at, and a refused value sends the proposal to a human.
"""

from __future__ import annotations

import re

_QUANTITY = re.compile(r"^([0-9]+)(Ki|Mi|Gi|Ti|k|M|G|T)?$")
_FACTORS = {
    None: 1,
    "Ki": 2**10,
    "Mi": 2**20,
    "Gi": 2**30,
    "Ti": 2**40,
    "k": 10**3,
    "M": 10**6,
    "G": 10**9,
    "T": 10**12,
}


class InvalidQuantity(ValueError):
    pass


def parse_bytes(value: object) -> int:
    match = _QUANTITY.match(str(value).strip()) if isinstance(value, str | int) else None
    if match is None:
        raise InvalidQuantity(f"unsupported memory quantity {value!r}")
    return int(match.group(1)) * _FACTORS[match.group(2)]


def format_mi(n_bytes: int) -> str:
    """For messages: `256Mi`, or the byte count when it isn't a whole number of MiB."""
    return f"{n_bytes // 2**20}Mi" if n_bytes % 2**20 == 0 else f"{n_bytes}"
