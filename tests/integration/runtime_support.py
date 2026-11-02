"""Synthetic Docker runtime helpers shared by integration and smoke E2E tests."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from scripts.workspace_runtime import WorkspaceRuntime, runtime_environment

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def create_foundation_workspace(destination: Path) -> Path:
    """Copy only the Compose definition needed for infrastructure-only tests."""

    deploy = destination / "deploy"
    deploy.mkdir(parents=True)
    shutil.copy2(REPOSITORY_ROOT / "deploy/compose.yaml", deploy / "compose.yaml")
    return destination


def create_full_workspace(destination: Path) -> Path:
    """Copy the bounded build context without Git metadata, caches or private files."""

    destination.mkdir(parents=True)
    for filename in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "alembic.ini",
    ):
        shutil.copy2(REPOSITORY_ROOT / filename, destination / filename)
    ignored = shutil.ignore_patterns(
        ".DS_Store",
        ".next",
        ".runtime",
        "__pycache__",
        "*.pyc",
        "node_modules",
    )
    for directory in (
        "src",
        "apps",
        "configs",
        "rules",
        "resources",
        "schemas",
        "infra",
        "deploy",
    ):
        shutil.copytree(
            REPOSITORY_ROOT / directory,
            destination / directory,
            ignore=ignored,
        )
    scripts = destination / "scripts"
    scripts.mkdir()
    shutil.copy2(
        REPOSITORY_ROOT / "scripts/deny_node_network.cjs",
        scripts / "deny_node_network.cjs",
    )
    return destination


def compose_command(runtime: WorkspaceRuntime, arguments: Sequence[str]) -> list[str]:
    """Build the exact project-scoped Compose command used by a test probe."""

    return [
        "docker",
        "compose",
        "-p",
        runtime.project_name,
        "-f",
        str(runtime.compose_file),
        *arguments,
    ]


def run_compose(
    runtime: WorkspaceRuntime,
    arguments: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed synthetic probe without a host shell."""

    return subprocess.run(  # noqa: S603 - fixed docker executable and structured args
        compose_command(runtime, arguments),
        cwd=runtime.workspace,
        env=runtime_environment(),
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=900,
        shell=False,
    )
