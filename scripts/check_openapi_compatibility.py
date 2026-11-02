#!/usr/bin/env python3
"""Validate OpenAPI and reject breaking changes relative to a Git base."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
OPENAPI_PATH: Final = Path("schemas/http/openapi.v1.json")
OASDIFF_IMAGE: Final = (
    "tufin/oasdiff:v1.25.1@sha256:3a0556fff855d1e1c91ebeedf4800772090cbdb4f319e0b4ee3be549bd5b5072"
)
COMMAND_TIMEOUT_SECONDS: Final = 120
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class OpenApiCompatibilityError(RuntimeError):
    """Raised when compatibility cannot be checked safely."""


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - internally built Git/Docker commands only
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenApiCompatibilityError(
            f"compatibility command exceeded {COMMAND_TIMEOUT_SECONDS} seconds"
        ) from exc


def _git(
    root: Path,
    *args: str,
    runner: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        raise OpenApiCompatibilityError("git executable is required")
    return runner((git, "-C", str(root), *args))


def _docker_command(spec_dir: Path, *oasdiff_args: str) -> tuple[str, ...]:
    docker = shutil.which("docker") or "docker"
    mount = f"type=bind,src={spec_dir.resolve()},dst=/specs,readonly"
    return (
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--mount",
        mount,
        OASDIFF_IMAGE,
        *oasdiff_args,
    )


def _oasdiff(
    spec_dir: Path,
    *args: str,
    runner: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    return runner(_docker_command(spec_dir, *args))


def _diagnostic(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr.strip() or result.stdout.strip() or "no diagnostic").replace("\n", " ")


def _write_probe_documents(spec_dir: Path) -> None:
    base = {
        "openapi": "3.1.0",
        "info": {"title": "compatibility probe", "version": "1.0.0"},
        "paths": {
            "/probe": {
                "get": {
                    "operationId": "probe",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    head = {
        "openapi": "3.1.0",
        "info": {"title": "compatibility probe", "version": "1.0.0"},
        "paths": {},
    }
    (spec_dir / "probe-base.json").write_text(
        json.dumps(base, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (spec_dir / "probe-head.json").write_text(
        json.dumps(head, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _oasdiff_options() -> tuple[str, ...]:
    return "--fail-on", "ERR", "--allow-external-refs=false", "--color", "never"


def validate(
    root: Path,
    base_ref: str,
    *,
    runner: CommandRunner = _run,
) -> list[str]:
    """Return fail-closed OpenAPI validation or compatibility violations."""

    resolved_root = root.resolve()
    current_path = resolved_root / OPENAPI_PATH
    if not current_path.is_file():
        return [f"current OpenAPI snapshot is missing: {OPENAPI_PATH.as_posix()}"]

    try:
        base_commit = _git(
            resolved_root,
            "rev-parse",
            "--verify",
            f"{base_ref}^{{commit}}",
            runner=runner,
        )
    except OpenApiCompatibilityError as exc:
        return [str(exc)]
    if base_commit.returncode != 0:
        return [f"OpenAPI base ref is not a commit: {base_ref}: {_diagnostic(base_commit)}"]

    try:
        with tempfile.TemporaryDirectory(prefix="sma-openapi-compat-") as temporary:
            spec_dir = Path(temporary)
            (spec_dir / "head.json").write_bytes(current_path.read_bytes())
            validate_head = _oasdiff(
                spec_dir,
                "validate",
                "/specs/head.json",
                *_oasdiff_options(),
                runner=runner,
            )
            if validate_head.returncode != 0:
                return [f"current OpenAPI is invalid: {_diagnostic(validate_head)}"]

            base_listing = _git(
                resolved_root,
                "ls-tree",
                "--name-only",
                base_ref,
                "--",
                OPENAPI_PATH.as_posix(),
                runner=runner,
            )
            if base_listing.returncode != 0:
                return [f"cannot inspect the base OpenAPI snapshot: {_diagnostic(base_listing)}"]
            base_paths = {item for item in base_listing.stdout.splitlines() if item}
            if OPENAPI_PATH.as_posix() not in base_paths:
                _write_probe_documents(spec_dir)
                negative_probe = _oasdiff(
                    spec_dir,
                    "breaking",
                    "/specs/probe-base.json",
                    "/specs/probe-head.json",
                    *_oasdiff_options(),
                    runner=runner,
                )
                if negative_probe.returncode != 1:
                    return [
                        "oasdiff synthetic breaking-change probe returned an unexpected "
                        f"exit code {negative_probe.returncode}: {_diagnostic(negative_probe)}"
                    ]
                print("OpenAPI first publication: no base snapshot; negative probe passed")
                return []

            base_document = _git(
                resolved_root,
                "show",
                f"{base_ref}:{OPENAPI_PATH.as_posix()}",
                runner=runner,
            )
            if base_document.returncode != 0:
                return [f"cannot read base OpenAPI snapshot: {_diagnostic(base_document)}"]
            (spec_dir / "base.json").write_text(base_document.stdout, encoding="utf-8")
            validate_base = _oasdiff(
                spec_dir,
                "validate",
                "/specs/base.json",
                *_oasdiff_options(),
                runner=runner,
            )
            if validate_base.returncode != 0:
                return [f"base OpenAPI is invalid: {_diagnostic(validate_base)}"]
            breaking = _oasdiff(
                spec_dir,
                "breaking",
                "/specs/base.json",
                "/specs/head.json",
                *_oasdiff_options(),
                runner=runner,
            )
            if breaking.returncode != 0:
                return [f"breaking OpenAPI change detected: {_diagnostic(breaking)}"]
            return []
    except (OSError, OpenApiCompatibilityError) as exc:
        return [f"OpenAPI compatibility check could not run: {exc}"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        required=True,
        help="Git commit/ref used as compatibility base",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate(PROJECT_ROOT, args.base_ref)
    if not errors:
        print(f"OpenAPI compatibility passed against {args.base_ref}")
        return 0
    print("OpenAPI compatibility failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
