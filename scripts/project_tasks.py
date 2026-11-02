"""Dependency-light developer tasks used by the Makefile."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

HELP = """StockMarketAgent tasks

  make doctor         diagnose the local toolchain without changing it
  make install        install API, data and development dependencies
  make install-ml     install optional CUDA/ML dependencies
  make demo           print a deterministic synthetic premarket briefing
  make delivery-registry validate the small current-stage delivery record
  make agent-run       run one task from its four-field product definition
  make package-check   validate Git scope and run explicit focused tests
  make agent-review    independently review one immutable final package HEAD
  make integration-merge merge one reviewed HEAD into the integration candidate
  make contract-compat validate published migrations, schemas and frozen Ports against a base
  make parallel-doctor diagnose this worktree's isolated runtime identity
  make workspace-up     start only this worktree's isolated Compose project
  make workspace-status show this worktree's local URLs and service state
  make workspace-down   stop this worktree without deleting its data
  make workspace-destroy precisely delete only this worktree's Compose resources
  make migration-test    validate empty and previous-head disposable PostgreSQL upgrades
  make integration-test  exercise PostgreSQL, Valkey, SeaweedFS and recovery foundations
  make e2e-test           build and smoke-test this worktree's complete local runtime
  make parallel-isolation-test prove four real Git worktrees remain isolated
  make lint           run Python and Web static checks
  make test           run Python tests with coverage
  make test-contract  validate stable contracts
  make verify-fast    run governance and Python checks
  make verify         run every hard gate, including real Docker acceptance tests
  make compose-up     alias for the isolated workspace-up command
  make compose-down   alias for the isolated workspace-down command
  make migrate        apply PostgreSQL migrations
"""

CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    output: str


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str


CommandRunner = Callable[[tuple[str, ...]], CommandResult]
ExecutableResolver = Callable[[str], str | None]


def parse_major_version(output: str) -> int | None:
    """Extract the first numeric major version from common version output."""

    match = re.search(r"(?<!\d)(\d+)(?:\.\d+)?", output)
    return int(match.group(1)) if match else None


def _resolve_executable(name: str) -> str | None:
    return shutil.which(name)


def _run_command(command: tuple[str, ...]) -> CommandResult:
    try:
        completed = subprocess.run(  # noqa: S603 - command comes from fixed tool definitions.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(returncode=1, output=str(exc))
    output = (completed.stdout or completed.stderr).strip()
    return CommandResult(returncode=completed.returncode, output=output)


def _check_tool(
    *,
    name: str,
    executable: str,
    arguments: tuple[str, ...],
    fix: str,
    which: ExecutableResolver,
    runner: CommandRunner,
    minimum_major: int | None = None,
    fallback: Path | None = None,
) -> DoctorCheck:
    resolved = which(executable)
    if resolved is None and fallback is not None and fallback.is_file():
        resolved = str(fallback)
    if resolved is None:
        return DoctorCheck(name, "FAIL", f"command not found. Fix: {fix}")

    result = runner((resolved, *arguments))
    if result.returncode != 0:
        observed = result.output or f"exit code {result.returncode}"
        return DoctorCheck(name, "FAIL", f"{observed}. Fix: {fix}")

    if minimum_major is not None:
        observed_major = parse_major_version(result.output)
        if observed_major is None or observed_major < minimum_major:
            observed = result.output or "version could not be parsed"
            return DoctorCheck(
                name,
                "FAIL",
                f"observed {observed}; requires major {minimum_major}. Fix: {fix}",
            )
    return DoctorCheck(name, "PASS", f"{result.output} ({resolved})")


def _python_check(root: Path, runner: CommandRunner) -> DoctorCheck:
    repository_python = root / ".venv" / "bin" / "python"
    if repository_python.is_file():
        result = runner((str(repository_python), "--version"))
        observed = result.output or f"exit code {result.returncode}"
        match = re.search(r"Python\s+(\d+)\.(\d+)", observed)
        supported = result.returncode == 0 and match is not None and match.groups() == ("3", "12")
        if supported:
            return DoctorCheck("Python", "PASS", f"{observed} ({repository_python})")
        return DoctorCheck(
            "Python",
            "FAIL",
            f"repository environment reported {observed}; requires Python 3.12. "
            "Fix: recreate .venv with Python 3.12",
        )

    current = platform.python_version()
    if sys.version_info[:2] == (3, 12):
        return DoctorCheck("Python", "PASS", f"Python {current} ({sys.executable})")
    return DoctorCheck(
        "Python",
        "FAIL",
        f"observed Python {current}; requires Python 3.12. "
        "Fix: install Python 3.12 and recreate the virtual environment",
    )


def _repository_check(root: Path) -> DoctorCheck:
    required = (
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path("apps/web/package-lock.json"),
        Path("deploy/compose.yaml"),
    )
    missing = [str(path) for path in required if not (root / path).is_file()]
    if missing:
        return DoctorCheck(
            "Repository files",
            "FAIL",
            f"missing {', '.join(missing)}. "
            "Fix: run the command from a complete repository checkout",
        )
    return DoctorCheck("Repository files", "PASS", "lock files and Compose definition found")


def _schedule_policy_check() -> DoctorCheck:
    try:
        from apps.worker.schedule_policy import schedule_doctor

        gaps = schedule_doctor()
    except (ImportError, OSError, ValueError) as exc:
        return DoctorCheck(
            "R1 schedule policy",
            "FAIL",
            f"{exc}. Fix: restore configs/schedules.yaml and rerun make doctor",
        )
    if gaps:
        return DoctorCheck(
            "R1 schedule policy",
            "WARN",
            f"dispatch remains fail-closed: {', '.join(gaps)}. "
            "Fix in R1: compose the scheduler-side request factory, then rerun make doctor",
        )
    return DoctorCheck("R1 schedule policy", "PASS", "all enabled jobs are dispatchable")


def _hardware_check(which: ExecutableResolver, runner: CommandRunner) -> DoctorCheck:
    system = platform.system()
    machine = platform.machine()
    if system == "Darwin" and machine == "arm64":
        return DoctorCheck(
            "ML hardware",
            "WARN",
            "Apple Silicon detected; local inference and MPS/LoRA support require the documented "
            "hardware spike before being claimed as supported",
        )
    if system == "Linux":
        nvidia_smi = which("nvidia-smi")
        if nvidia_smi is not None:
            result = runner((nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader"))
            if result.returncode == 0:
                return DoctorCheck("ML hardware", "PASS", result.output)
        return DoctorCheck(
            "ML hardware",
            "WARN",
            "no verified NVIDIA runtime detected; core development works, "
            "ML training stays disabled",
        )
    return DoctorCheck(
        "ML hardware",
        "WARN",
        f"{system or 'unknown'} {machine or 'unknown'} is not in the verified ML matrix",
    )


def collect_doctor_checks(
    *,
    root: Path | None = None,
    which: ExecutableResolver = _resolve_executable,
    runner: CommandRunner = _run_command,
) -> list[DoctorCheck]:
    """Collect read-only environment checks without installing or starting services."""

    repository_root = root or Path(__file__).resolve().parents[1]
    return [
        _python_check(repository_root, runner),
        _check_tool(
            name="Node.js",
            executable="node",
            arguments=("--version",),
            minimum_major=22,
            fix="install Node.js 22 LTS",
            which=which,
            runner=runner,
        ),
        _check_tool(
            name="npm",
            executable="npm",
            arguments=("--version",),
            fix="install npm through Node.js 22 LTS",
            which=which,
            runner=runner,
        ),
        _check_tool(
            name="Docker Compose",
            executable="docker",
            arguments=("compose", "version"),
            fix="install and start Docker Desktop or Docker Engine with Compose v2",
            which=which,
            runner=runner,
        ),
        _check_tool(
            name="uv",
            executable="uv",
            arguments=("--version",),
            fix="install uv or restore the repository .venv",
            which=which,
            runner=runner,
            fallback=repository_root / ".venv" / "bin" / "uv",
        ),
        _repository_check(repository_root),
        _schedule_policy_check(),
        _hardware_check(which, runner),
    ]


def run_doctor() -> int:
    checks = collect_doctor_checks()
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.detail}")
    counts = {
        status: sum(check.status == status for check in checks)
        for status in ("PASS", "WARN", "FAIL")
    }
    print(f"Summary: {counts['PASS']} passed, {counts['WARN']} warning(s), {counts['FAIL']} failed")
    return 1 if counts["FAIL"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else "help"
    if command == "help":
        print(HELP)
        return 0
    if command == "doctor":
        return run_doctor()
    if command == "demo":
        from apps.demo_briefing import build_synthetic_premarket_demo

        print(build_synthetic_premarket_demo().model_dump_json(indent=2))
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
