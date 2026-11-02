#!/usr/bin/env python3
"""Check real Git scope and run explicit focused pytest targets."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "/usr/bin/git"


class PackageCheckError(ValueError):
    """The focused package check failed."""


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - Git executable is resolved locally.
        [GIT, *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PackageCheckError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _paths(output: str) -> set[str]:
    return {line.strip() for line in output.splitlines() if line.strip()}


def changed_paths(base_ref: str) -> tuple[str, ...]:
    _git("rev-parse", "--verify", f"{base_ref}^{{commit}}")
    paths = _paths(_git("diff", "--name-only", f"{base_ref}..HEAD"))
    paths.update(_paths(_git("diff", "--name-only", "--cached")))
    paths.update(_paths(_git("diff", "--name-only")))
    paths.update(_paths(_git("ls-files", "--others", "--exclude-standard")))
    return tuple(sorted(paths))


def _normalize_scope(raw: str) -> str:
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or raw in {"", "."}:
        raise PackageCheckError(f"invalid write scope: {raw}")
    return path.as_posix().rstrip("/")


def _within(path: str, scope: str) -> bool:
    return path == scope or path.startswith(f"{scope}/")


def validate_scope(paths: tuple[str, ...], write_scopes: tuple[str, ...]) -> None:
    scopes = tuple(_normalize_scope(scope) for scope in write_scopes)
    outside = [path for path in paths if not any(_within(path, scope) for scope in scopes)]
    if outside:
        raise PackageCheckError("changed paths exceed WRITE_SCOPES: " + ", ".join(outside))


def _test_target(raw: str) -> str:
    if any(character.isspace() for character in raw) or "\\" in raw:
        raise PackageCheckError(f"focused test must be one tests/ pytest target: {raw}")
    path_text = raw.split("::", maxsplit=1)[0]
    path = PurePosixPath(path_text)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "tests":
        raise PackageCheckError(f"focused test must be one tests/ pytest target: {raw}")
    resolved = (ROOT / path).resolve()
    try:
        resolved.relative_to((ROOT / "tests").resolve())
    except ValueError as exc:
        raise PackageCheckError(f"focused test escapes tests/: {raw}") from exc
    if resolved.suffix != ".py" or not resolved.is_file():
        raise PackageCheckError(f"focused test file does not exist: {path_text}")
    return raw


def run_tests(targets: tuple[str, ...]) -> None:
    validated = [_test_target(target) for target in targets]
    result = subprocess.run(  # noqa: S603 - sys.executable and targets are bounded.
        [sys.executable, "-m", "pytest", "-q", *validated],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise PackageCheckError(f"focused pytest failed with exit code {result.returncode}")


def check(base_ref: str, write_scopes: tuple[str, ...], tests: tuple[str, ...]) -> None:
    branch = _git("branch", "--show-current").strip()
    if not branch or branch == "main" or branch.startswith("integration/"):
        raise PackageCheckError("package-check requires a short implementation branch")
    before = changed_paths(base_ref)
    validate_scope(before, write_scopes)
    if not before:
        raise PackageCheckError("package-check found no changes")
    run_tests(tests)
    validate_scope(changed_paths(base_ref), write_scopes)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--write-scope", action="append", required=True)
    parser.add_argument("--test", action="append", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        check(args.base_ref, tuple(args.write_scope), tuple(args.test))
    except PackageCheckError as exc:
        print(f"Package check failed: {exc}", file=sys.stderr)
        return 1
    print("Package check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
