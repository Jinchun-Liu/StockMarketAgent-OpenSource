"""Validated access to versioned, environment-independent YAML policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.environ.get("STOCK_ASSISTANT_PROJECT_ROOT", str(Path.cwd()))).resolve()


@dataclass(frozen=True, slots=True)
class VersionedConfig:
    version: str
    content_hash: str
    data: dict[str, Any]


@lru_cache(maxsize=16)
def load_versioned_config(relative_path: str) -> VersionedConfig:
    """Load repository policy and retain the exact hash used at runtime."""

    path = (PROJECT_ROOT / relative_path).resolve()
    if PROJECT_ROOT not in path.parents:
        raise ValueError("configuration path must stay inside the project")
    content = path.read_bytes()
    document: dict[str, Any] = yaml.safe_load(content)
    version = document.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{relative_path} must declare a non-empty version")
    return VersionedConfig(
        version=version,
        content_hash=sha256(content).hexdigest(),
        data=document,
    )
