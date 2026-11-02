"""Single runtime identity for the frozen R1 behavior window semantics."""

from __future__ import annotations

import hashlib
from typing import Final

BEHAVIOR_WINDOW_SEMANTICS: Final = "close-stamped-open-left-v2"
BEHAVIOR_SEMANTICS_HASH: Final = hashlib.sha256(
    (
        f"behavior-pressure-research.v1:ten-cluster:five-facet:mid-rank:{BEHAVIOR_WINDOW_SEMANTICS}"
    ).encode()
).hexdigest()

__all__ = ["BEHAVIOR_SEMANTICS_HASH", "BEHAVIOR_WINDOW_SEMANTICS"]
