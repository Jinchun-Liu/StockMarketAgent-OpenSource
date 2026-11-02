"""Shared secret-like text signatures for every public artifact scanner."""

from __future__ import annotations

import re
from typing import Final


def _ascii(hex_value: str) -> str:
    return bytes.fromhex(hex_value).decode("ascii")


SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(_ascii("2d2d2d2d2d424547494e205b412d5a205d2a50524956415445204b45592d2d2d2d2d")),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(
        r"(?i)\b(?:aws_secret_access_key|authorization|cookie|access_token|"
        r"refresh_token|password)\s*[:=]\s*(?:['\"][^'\"]{8,}['\"]|"
        r"[A-Za-z0-9_+/=-]{8,})"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)


def contains_secret_pattern(value: str) -> bool:
    """Return whether public text resembles a private key or credential."""

    return any(pattern.search(value) is not None for pattern in SECRET_PATTERNS)


__all__ = ["SECRET_PATTERNS", "contains_secret_pattern"]
