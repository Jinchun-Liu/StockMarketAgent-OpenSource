#!/usr/bin/env python3
"""Thin Codex wrapper for implementation and one read-only final review."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "/usr/bin/git"
REQUIRED_FIELDS = (
    "User Capability",
    "Runnable Path",
    "Acceptance Evidence",
    "Non-goals",
)


class DeliveryError(ValueError):
    """The requested agent action violates the ordinary delivery path."""


def _git(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(  # noqa: S603 - Git executable is resolved locally.
        (GIT, *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise DeliveryError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def _git_succeeds(*arguments: str) -> bool:
    completed = subprocess.run(  # noqa: S603 - Git executable is resolved locally.
        (GIT, *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _require_short_branch() -> str:
    branch = _git("branch", "--show-current")
    if not branch or branch == "main" or branch.startswith("integration/"):
        raise DeliveryError("agent actions require one short implementation branch")
    return branch


def _require_clean() -> None:
    if _git("status", "--porcelain"):
        raise DeliveryError("agent action requires a clean worktree")


def _policy_strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DeliveryError(f"protected path policy {label} must be a string list")
    return tuple(item for item in value if isinstance(item, str))


def _task_text(raw_path: str) -> str:
    path = Path(raw_path).expanduser().resolve()
    try:
        relative = path.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise DeliveryError("prompt file must be inside the repository") from exc
    try:
        standards: object = json.loads(
            (ROOT / "governance/standards.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryError("cannot load protected path policy") from exc
    if not isinstance(standards, dict):
        raise DeliveryError("governance standards must be an object")
    protected = standards.get("protected_paths")
    if not isinstance(protected, dict):
        raise DeliveryError("protected path policy must be an object")
    exceptions = _policy_strings(protected.get("exceptions"), "exceptions")
    exact = _policy_strings(protected.get("exact"), "exact")
    prefixes = _policy_strings(protected.get("prefixes"), "prefixes")
    name_prefixes = _policy_strings(protected.get("name_prefixes"), "name_prefixes")
    suffixes = _policy_strings(protected.get("suffixes"), "suffixes")
    if relative not in exceptions and (
        relative in exact
        or any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in prefixes)
        or any(path.name.startswith(prefix) for prefix in name_prefixes)
        or path.suffix in suffixes
    ):
        raise DeliveryError("prompt file is protected by repository policy")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeliveryError(f"cannot read prompt file: {exc}") from exc
    missing = [field for field in REQUIRED_FIELDS if field not in text]
    if missing:
        raise DeliveryError("prompt is missing product-first fields: " + ", ".join(missing))
    return text


def build_command(*, binary: str, model: str, sandbox: str) -> tuple[str, ...]:
    """Build the only Codex execution command used by ordinary delivery."""

    if sandbox not in {"workspace-write", "read-only"}:
        raise DeliveryError(f"unsupported sandbox: {sandbox}")
    command: tuple[str, ...] = (
        binary,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--strict-config",
        "--enable",
        "hooks",
        "--dangerously-bypass-hook-trust",
        "--sandbox",
        sandbox,
        "--cd",
        str(ROOT),
        "--model",
        model,
    )
    if sandbox == "workspace-write":
        command += ("-c", "sandbox_workspace_write.network_access=false")
    return (*command, "-")


def _codex(
    binary_name: str,
    model: str,
    sandbox: str,
    prompt: str,
) -> subprocess.CompletedProcess[str]:
    binary = shutil.which(binary_name)
    if binary is None:
        raise DeliveryError(f"cannot resolve Codex CLI: {binary_name}")
    return subprocess.run(  # noqa: S603 - Codex executable is resolved by shutil.which.
        build_command(binary=binary, model=model, sandbox=sandbox),
        cwd=ROOT,
        input=prompt,
        check=False,
        capture_output=True,
        text=True,
        timeout=3600,
    )


def run_agent(arguments: argparse.Namespace) -> None:
    branch = _require_short_branch()
    _require_clean()
    start_head = _git("rev-parse", "HEAD")
    task = _task_text(arguments.prompt_file)
    prompt = f"""Implement the following task on the current short branch.
Read and obey every applicable AGENTS.md. Stay within the stated non-goals.
Do not commit, push, tag, merge, create another branch/worktree, or run the full make verify.
Run focused checks as appropriate and leave the implementation in the worktree.

Branch: {branch}

{task}
"""
    completed = _codex(arguments.codex_binary, arguments.model, "workspace-write", prompt)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise DeliveryError(f"Codex implementation exited with {completed.returncode}")
    if _git("rev-parse", "HEAD") != start_head:
        raise DeliveryError("implementation agent changed HEAD; commits remain caller-owned")


def review_agent(arguments: argparse.Namespace) -> None:
    _require_short_branch()
    _require_clean()
    target = _git("rev-parse", f"{arguments.target_commit}^{{commit}}")
    if target != _git("rev-parse", "HEAD"):
        raise DeliveryError("review target must equal the current final HEAD")
    base = _git("rev-parse", f"{arguments.base_ref}^{{commit}}")
    if not _git_succeeds("merge-base", "--is-ancestor", base, target):
        raise DeliveryError("review base must be an ancestor of the final HEAD")
    task = _task_text(arguments.prompt_file)
    prompt = f"""Independently review the immutable final package payload.
Read and obey every applicable AGENTS.md. Do not modify any file.
Inspect the cumulative diff with: git diff {base}..{target}
Check the task, focused evidence, scope, regressions, and retained safety boundaries.
The first non-empty output line must be exactly APPROVED or CHANGES_REQUESTED.
After that line, report only actionable findings.

{task}
"""
    completed = _codex(arguments.codex_binary, arguments.model, "read-only", prompt)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise DeliveryError(f"Codex review exited with {completed.returncode}")
    first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    if first_line not in {"APPROVED", "CHANGES_REQUESTED"}:
        raise DeliveryError("review must start with APPROVED or CHANGES_REQUESTED")
    if _git("rev-parse", "HEAD") != target or _git("status", "--porcelain"):
        raise DeliveryError("review changed the immutable final package state")
    if first_line == "CHANGES_REQUESTED":
        raise DeliveryError("independent review requested changes")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--prompt-file", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--codex-binary", default="codex")
    review = commands.add_parser("review")
    review.add_argument("--base-ref", required=True)
    review.add_argument("--target-commit", required=True)
    review.add_argument("--prompt-file", required=True)
    review.add_argument("--model", required=True)
    review.add_argument("--codex-binary", default="codex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "run":
            run_agent(arguments)
        else:
            review_agent(arguments)
    except (DeliveryError, subprocess.TimeoutExpired) as exc:
        print(f"Agent action failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
