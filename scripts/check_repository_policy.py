#!/usr/bin/env python3
"""Reject tracked private artifacts, oversized files and unapproved package registries."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

MAX_TRACKED_BYTES = 1_048_576
ALLOWED_FILES = {
    ".env.example",
    "data/raw/.gitkeep",
    "data/interim/.gitkeep",
    "data/processed/.gitkeep",
    "data/private/.gitkeep",
    "artifacts/models/.gitkeep",
    "artifacts/checkpoints/.gitkeep",
    "artifacts/mlruns/.gitkeep",
    "artifacts/reports/.gitkeep",
}
PROTECTED_PREFIXES = (
    "data/raw/",
    "data/interim/",
    "data/processed/",
    "data/private/",
    "artifacts/models/",
    "artifacts/checkpoints/",
    "artifacts/mlruns/",
    "artifacts/reports/",
    "secrets/",
)
PROTECTED_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".safetensors",
    ".gguf",
    ".parquet",
    ".sqlite",
    ".sqlite3",
    ".db",
)
ALLOWED_NPM_HOSTS = {"registry.npmjs.org"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked_files(root: Path) -> tuple[list[str], list[str]]:
    git_binary = shutil.which("git")
    if git_binary is None:
        return [], ["cannot enumerate tracked files: git executable not found"]
    try:
        result = subprocess.run(  # noqa: S603 - fixed, read-only Git query
            [git_binary, "ls-files", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], [f"cannot enumerate tracked files: {exc}"]
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        return [], [f"git ls-files failed: {detail}"]
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item], []


def _is_protected(relative: str) -> bool:
    if relative in ALLOWED_FILES:
        return False
    name = PurePosixPath(relative).name.lower()
    if relative == ".env" or name.startswith(".env."):
        return True
    return relative.startswith(PROTECTED_PREFIXES) or name.endswith(PROTECTED_SUFFIXES)


def _resolved_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "resolved" and isinstance(child, str):
                urls.append(child)
            else:
                urls.extend(_resolved_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.extend(_resolved_urls(child))
    return urls


def validate(root: Path) -> list[str]:
    """Return repository-policy violations without mutating the worktree."""

    tracked, errors = _tracked_files(root)
    for relative in tracked:
        path = root / relative
        if _is_protected(relative):
            errors.append(f"protected or private file is tracked: {relative}")
        if path.is_file() and path.stat().st_size > MAX_TRACKED_BYTES:
            errors.append(f"tracked file exceeds {MAX_TRACKED_BYTES} bytes: {relative}")

    lock_path = root / "apps/web/package-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot validate npm lock registry: {exc}")
    else:
        for url in _resolved_urls(lock):
            host = urlparse(url).hostname
            if host not in ALLOWED_NPM_HOSTS:
                errors.append(f"unapproved npm registry host {host!r}: {url}")
    return errors


def main() -> int:
    errors = validate(_repo_root())
    if errors:
        print("Repository policy check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Repository policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
