"""Resolve immutable project runtime resources in source and container layouts."""

from __future__ import annotations

from pathlib import Path


def runtime_resource_path(relative_path: str) -> Path:
    """Return a required file below ``resources`` without allowing path traversal."""

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("runtime resource path must be a safe relative path")
    roots = (
        Path.cwd() / "resources",
        Path(__file__).resolve().parents[3] / "resources",
    )
    for root in roots:
        candidate = root.joinpath(relative)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"required runtime resource is unavailable: resources/{relative.as_posix()}"
    )


__all__ = ["runtime_resource_path"]
