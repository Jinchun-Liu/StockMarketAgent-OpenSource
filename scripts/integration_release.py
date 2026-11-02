#!/usr/bin/env python3
"""Merge one independently reviewed package HEAD into the integration candidate."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "/usr/bin/git"
INTEGRATION_BRANCH = re.compile(r"^integration/[A-Za-z0-9][A-Za-z0-9._/-]*$")
SOURCE_BRANCH = re.compile(r"^codex/[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class IntegrationError(ValueError):
    """The requested merge violates the serial integration boundary."""


def _git(*arguments: str, check: bool = True, capture: bool = True) -> str:
    completed = subprocess.run(  # noqa: S603 - Git executable is resolved locally.
        (GIT, *arguments),
        cwd=ROOT,
        check=False,
        capture_output=capture,
        text=True,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() if completed.stderr else ""
        raise IntegrationError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip() if completed.stdout else ""


def _git_succeeds(*arguments: str) -> bool:
    completed = subprocess.run(  # noqa: S603 - Git executable is resolved locally.
        (GIT, *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def merge(source_branch: str, reviewed_commit: str) -> str:
    current = _git("branch", "--show-current")
    if INTEGRATION_BRANCH.fullmatch(current) is None:
        raise IntegrationError("integration merge must run on integration/**")
    if SOURCE_BRANCH.fullmatch(source_branch) is None or source_branch == current:
        raise IntegrationError("source branch must be one local codex/** implementation branch")
    if _git("status", "--porcelain"):
        raise IntegrationError("integration candidate must be clean")
    if SHA.fullmatch(reviewed_commit) is None:
        raise IntegrationError("reviewed commit must be a full Git SHA")
    resolved_review = _git("rev-parse", f"{reviewed_commit}^{{commit}}")
    source_tip = _git("rev-parse", f"refs/heads/{source_branch}^{{commit}}")
    if resolved_review != reviewed_commit or source_tip != reviewed_commit:
        raise IntegrationError("source tip must equal the independently reviewed commit")
    if _git_succeeds("merge-base", "--is-ancestor", reviewed_commit, "HEAD"):
        raise IntegrationError("reviewed commit is already contained in the candidate")
    before = _git("rev-parse", "HEAD")
    _git("merge", "--ff", "--no-edit", reviewed_commit, capture=False)
    merged = _git("rev-parse", "HEAD")
    if merged == reviewed_commit:
        return merged
    parents = _git("rev-list", "--parents", "-n", "1", merged).split()
    if len(parents) != 3 or parents[1] != before or parents[2] != reviewed_commit:
        raise IntegrationError("integration did not preserve the exact reviewed history")
    return merged


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    merge_parser = commands.add_parser("merge")
    merge_parser.add_argument("--source-branch", required=True)
    merge_parser.add_argument("--reviewed-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        merged = merge(arguments.source_branch, arguments.reviewed_commit)
    except IntegrationError as exc:
        print(f"Integration merge failed: {exc}", file=sys.stderr)
        return 1
    print(f"Integrated reviewed commit as {merged}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
