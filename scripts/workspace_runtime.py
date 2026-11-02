"""Run one Docker Compose stack per worktree without sharing mutable resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, Protocol, TypedDict, cast
from urllib.parse import unquote, urlsplit

MANIFEST_SCHEMA_VERSION: Final = "1.0"
PROJECT_PREFIX: Final = "sma"
COMPOSE_RELATIVE_PATH: Final = Path("deploy/compose.yaml")
MIGRATION_COMPOSE_RELATIVE_PATH: Final = Path("deploy/compose.migration-test.yaml")
MIGRATION_DATABASE_NAME: Final = "stock_assistant_r0p_test"
FOUNDATION_SERVICES: Final[tuple[str, ...]] = (
    "postgres",
    "valkey",
    "seaweedfs",
)
FOUNDATION_SETUP_SERVICE: Final = "seaweedfs-configure"
PUBLIC_ENDPOINTS: Final[tuple[tuple[str, str, int, str], ...]] = (
    ("web", "web", 3000, "http"),
    ("api", "api", 8000, "http"),
    ("object_storage_console", "seaweedfs", 9333, "http"),
)
RESOURCE_KINDS: Final[tuple[tuple[str, str], ...]] = (
    ("container", "ps"),
    ("network", "network"),
    ("volume", "volume"),
)
_SENSITIVE_DIAGNOSTIC_VALUE: Final = re.compile(
    r"(?i)(password|secret|token|api[_-]?key)(\s*[=:]\s*)([^\s,;]+)"
)
_URL_CREDENTIALS: Final = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")

RuntimeScope = Literal["foundation", "full"]
PostgresTestSuite = Literal["migrations", "recovery"]
POSTGRES_TEST_PATHS: Final[dict[PostgresTestSuite, str]] = {
    "migrations": "tests/migrations",
    "recovery": "tests/recovery",
}


class WorkspaceRuntimeError(RuntimeError):
    """Raised when a workspace runtime operation cannot be completed safely."""


def _bounded_process_detail(error: subprocess.CalledProcessError) -> str:
    parts = [
        value.strip()
        for value in (error.stdout, error.stderr)
        if isinstance(value, str) and value.strip()
    ]
    detail = "\n".join(parts) or "no captured process output"
    detail = _SENSITIVE_DIAGNOSTIC_VALUE.sub(r"\1\2<redacted>", detail)
    detail = _URL_CREDENTIALS.sub(r"\g<scheme><redacted>@", detail)
    return detail[-4000:]


class WorkspaceManifest(TypedDict):
    """Non-sensitive identity and endpoint state for one workspace."""

    schema_version: str
    project_name: str
    workspace_realpath: str
    compose_file: str
    scope: RuntimeScope
    updated_at: str
    endpoints: dict[str, str]


class ProcessRunner(Protocol):
    """Small injectable subprocess boundary used by the runtime wrapper."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    """Execute commands without a shell and capture bounded text output."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - arguments are structured and shell is disabled
            list(args),
            cwd=cwd,
            env=env,
            check=check,
            capture_output=True,
            text=True,
            timeout=900,
            shell=False,
        )


def sanitize_workspace_name(value: str) -> str:
    """Return a bounded Compose-safe name segment."""

    sanitized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not sanitized:
        sanitized = "workspace"
    return sanitized[:40].rstrip("-") or "workspace"


def workspace_project_name(workspace: Path) -> str:
    """Derive the auditable project name from a canonical workspace path."""

    realpath = workspace.resolve(strict=True)
    path_hash = hashlib.sha256(os.fsencode(realpath)).hexdigest()[:8]
    return f"{PROJECT_PREFIX}-{sanitize_workspace_name(realpath.name)}-{path_hash}"


def migration_project_name(workspace: Path) -> str:
    """Derive a separate deterministic project for destructive migration tests."""

    realpath = workspace.resolve(strict=True)
    path_hash = hashlib.sha256(os.fsencode(realpath)).hexdigest()[:8]
    workspace_name = sanitize_workspace_name(realpath.name)[:30].rstrip("-")
    return f"{PROJECT_PREFIX}-migration-{workspace_name}-{path_hash}"


def validate_test_database_url(database_url: str) -> None:
    """Fail closed unless a provided database is explicitly disposable and local."""

    parsed = urlsplit(database_url)
    if parsed.scheme != "postgresql+asyncpg":
        raise WorkspaceRuntimeError("Migration test URL must use the postgresql+asyncpg scheme")
    if parsed.hostname not in {"127.0.0.1", "localhost", "postgres"}:
        raise WorkspaceRuntimeError("Migration test database must be local or CI-internal")
    database_name = unquote(parsed.path.lstrip("/"))
    if not database_name.startswith(MIGRATION_DATABASE_NAME):
        raise WorkspaceRuntimeError(
            f"Migration test database must start with {MIGRATION_DATABASE_NAME}"
        )


def _wait_for_loopback_tcp(
    host: str,
    port: int,
    *,
    attempts: int = 30,
    connect_timeout: float = 1.0,
    retry_delay: float = 0.1,
) -> None:
    """Wait for Docker's published loopback port after container health succeeds."""

    if host != "127.0.0.1" or not 1 <= port <= 65535 or attempts < 1:
        raise WorkspaceRuntimeError("Loopback readiness target is invalid")
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            connection = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
            continue
        connection.close()
        return
    raise WorkspaceRuntimeError("Disposable PostgreSQL loopback port is not ready") from last_error


def runtime_environment() -> dict[str, str]:
    """Disable implicit Compose dotenv loading while preserving Docker connectivity."""

    environment = os.environ.copy()
    environment["COMPOSE_DISABLE_ENV_FILE"] = "1"
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
        environment.pop(key, None)
    return environment


@dataclass(frozen=True, slots=True)
class WorkspaceRuntime:
    """Safe lifecycle wrapper for one worktree-scoped Compose project."""

    workspace: Path
    runner: ProcessRunner

    @classmethod
    def create(
        cls,
        workspace: Path,
        runner: ProcessRunner | None = None,
    ) -> WorkspaceRuntime:
        realpath = workspace.resolve(strict=True)
        compose_file = realpath / COMPOSE_RELATIVE_PATH
        if not compose_file.is_file():
            raise WorkspaceRuntimeError(f"Compose file is missing: {compose_file}")
        return cls(workspace=realpath, runner=runner or SubprocessRunner())

    @property
    def project_name(self) -> str:
        return workspace_project_name(self.workspace)

    @property
    def compose_file(self) -> Path:
        return self.workspace / COMPOSE_RELATIVE_PATH

    @property
    def manifest_path(self) -> Path:
        return self.workspace / ".runtime" / "workspaces" / f"{self.project_name}.json"

    def _base_command(self) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            self.project_name,
            "-f",
            str(self.compose_file),
        ]

    def _run_compose(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            [*self._base_command(), *arguments],
            cwd=self.workspace,
            env=runtime_environment(),
            check=check,
        )

    def _run_docker(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            ["docker", *arguments],
            cwd=self.workspace,
            env=runtime_environment(),
            check=check,
        )

    def _write_manifest(
        self,
        scope: RuntimeScope,
        endpoints: dict[str, str],
    ) -> WorkspaceManifest:
        manifest = WorkspaceManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            project_name=self.project_name,
            workspace_realpath=str(self.workspace),
            compose_file=str(self.compose_file),
            scope=scope,
            updated_at=datetime.now(UTC).isoformat(),
            endpoints=endpoints,
        )
        manifest_path = self.manifest_path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
        temporary_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, manifest_path)
        return manifest

    def _load_manifest(self) -> WorkspaceManifest:
        if not self.manifest_path.is_file():
            raise WorkspaceRuntimeError(
                "Workspace manifest is missing, so no exact Docker target can be proven. "
                "Run 'make workspace-up' to create this worktree's manifest, or "
                "'make parallel-doctor' to diagnose a lost manifest. "
                "No Docker resources were changed"
            )
        try:
            raw: object = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceRuntimeError("Workspace manifest is unreadable") from exc
        if not isinstance(raw, dict):
            raise WorkspaceRuntimeError("Workspace manifest must be a JSON object")
        mapping = cast(dict[object, object], raw)
        required_strings = (
            "schema_version",
            "project_name",
            "workspace_realpath",
            "compose_file",
            "scope",
            "updated_at",
        )
        values: dict[str, str] = {}
        for key in required_strings:
            value = mapping.get(key)
            if not isinstance(value, str):
                raise WorkspaceRuntimeError(f"Workspace manifest field {key} is invalid")
            values[key] = value
        raw_endpoints = mapping.get("endpoints")
        if not isinstance(raw_endpoints, dict):
            raise WorkspaceRuntimeError("Workspace manifest endpoints are invalid")
        endpoint_mapping = cast(dict[object, object], raw_endpoints)
        endpoints: dict[str, str] = {}
        for endpoint_key, endpoint_value in endpoint_mapping.items():
            if not isinstance(endpoint_key, str) or not isinstance(endpoint_value, str):
                raise WorkspaceRuntimeError("Workspace manifest endpoint is invalid")
            endpoints[endpoint_key] = endpoint_value
        raw_scope = values["scope"]
        if raw_scope not in {"foundation", "full"}:
            raise WorkspaceRuntimeError("Workspace manifest scope is invalid")
        scope = cast(RuntimeScope, raw_scope)
        return WorkspaceManifest(
            schema_version=values["schema_version"],
            project_name=values["project_name"],
            workspace_realpath=values["workspace_realpath"],
            compose_file=values["compose_file"],
            scope=scope,
            updated_at=values["updated_at"],
            endpoints=endpoints,
        )

    def _validate_manifest_identity(self, manifest: WorkspaceManifest) -> None:
        expected = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "project_name": self.project_name,
            "workspace_realpath": str(self.workspace),
            "compose_file": str(self.compose_file),
        }
        actual = {
            "schema_version": manifest["schema_version"],
            "project_name": manifest["project_name"],
            "workspace_realpath": manifest["workspace_realpath"],
            "compose_file": manifest["compose_file"],
        }
        for key, expected_value in expected.items():
            if actual[key] != expected_value:
                raise WorkspaceRuntimeError(f"Workspace manifest identity mismatch for {key}")

    def _labeled_resources(self, docker_kind: str) -> list[tuple[str, str]]:
        return self._labeled_resources_for_project(self.project_name, docker_kind)

    def _labeled_resources_for_project(
        self,
        project_name: str,
        docker_kind: str,
    ) -> list[tuple[str, str]]:
        label_filter = f"label=com.docker.compose.project={project_name}"
        if docker_kind == "ps":
            arguments = [
                "ps",
                "--all",
                "--filter",
                label_filter,
                "--format",
                '{{.ID}}\t{{.Label "com.docker.compose.project"}}',
            ]
        elif docker_kind == "network":
            arguments = [
                "network",
                "ls",
                "--filter",
                label_filter,
                "--format",
                '{{.ID}}\t{{.Label "com.docker.compose.project"}}',
            ]
        elif docker_kind == "volume":
            arguments = [
                "volume",
                "ls",
                "--filter",
                label_filter,
                "--format",
                '{{.Name}}\t{{.Label "com.docker.compose.project"}}',
            ]
        else:
            raise WorkspaceRuntimeError(f"Unsupported Docker resource kind: {docker_kind}")
        result = self._run_docker(arguments)
        resources: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            identifier, separator, label = line.partition("\t")
            if not separator or not identifier or not label:
                raise WorkspaceRuntimeError("Docker returned an unparseable resource label")
            resources.append((identifier, label))
        return resources

    def _validate_resource_labels(self, project_name: str | None = None) -> None:
        expected_project = project_name or self.project_name
        for display_name, docker_kind in RESOURCE_KINDS:
            for identifier, label in self._labeled_resources_for_project(
                expected_project, docker_kind
            ):
                if label != expected_project:
                    raise WorkspaceRuntimeError(
                        f"{display_name} {identifier} has an unexpected project label"
                    )

    def _published_endpoint(
        self,
        service: str,
        container_port: int,
        scheme: str,
    ) -> str | None:
        result = self._run_compose(
            ["port", service, str(container_port)],
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        address = result.stdout.strip().splitlines()[0]
        if address in {":0", "0.0.0.0:0", "127.0.0.1:0"}:
            return None
        host, separator, port = address.rpartition(":")
        if not separator or host not in {"127.0.0.1", "localhost"} or not port.isdigit():
            raise WorkspaceRuntimeError(f"Published port for {service} is not loopback-only")
        return f"{scheme}://127.0.0.1:{port}"

    def _discover_endpoints(self) -> dict[str, str]:
        endpoints: dict[str, str] = {}
        for endpoint_name, service, port, scheme in PUBLIC_ENDPOINTS:
            endpoint = self._published_endpoint(service, port, scheme)
            if endpoint is not None:
                endpoints[endpoint_name] = endpoint
        return endpoints

    def doctor(self) -> dict[str, object]:
        """Check Docker, Compose and this workspace's rendered configuration."""

        docker_version = self._run_docker(["--version"]).stdout.strip()
        compose_version = self._run_compose(["version"]).stdout.strip()
        self._run_docker(["info", "--format", "{{.ServerVersion}}"])
        self._run_compose(["config", "--quiet"])
        return {
            "status": "ok",
            "workspace_realpath": str(self.workspace),
            "project_name": self.project_name,
            "compose_file": str(self.compose_file),
            "docker": docker_version,
            "compose": compose_version,
            "implicit_dotenv": "disabled",
        }

    def up(self, scope: RuntimeScope) -> WorkspaceManifest:
        """Start either target infrastructure or the complete local stack."""

        self._write_manifest(scope, {})
        arguments = ["up", "--detach", "--wait", "--wait-timeout", "300"]
        if scope == "full":
            arguments.append("--build")
        else:
            arguments.extend(FOUNDATION_SERVICES)
        try:
            self._run_compose(arguments)
            if scope == "foundation":
                # Compose `up --wait` treats a successful one-shot service as a
                # failed long-running dependency when it is selected explicitly.
                # Run it only after the persistent foundation is healthy and
                # remove its transient container on success.
                self._run_compose(["run", "--rm", FOUNDATION_SETUP_SERVICE])
        except subprocess.CalledProcessError as error:
            raise WorkspaceRuntimeError(
                f"Compose up failed for project {self.project_name}: "
                f"{_bounded_process_detail(error)}"
            ) from error
        return self._write_manifest(scope, self._discover_endpoints())

    def status(self) -> dict[str, object]:
        """Return actual service state and dynamically assigned loopback URLs."""

        manifest = self._load_manifest()
        self._validate_manifest_identity(manifest)
        self._validate_resource_labels()
        running = self._run_compose(["ps", "--services", "--status", "running"]).stdout.splitlines()
        endpoints = self._discover_endpoints()
        if endpoints != manifest["endpoints"]:
            manifest = self._write_manifest(manifest["scope"], endpoints)
        return {
            "status": "running" if running else "stopped",
            "project_name": self.project_name,
            "workspace_realpath": str(self.workspace),
            "scope": manifest["scope"],
            "running_services": sorted(service for service in running if service),
            "endpoints": endpoints,
        }

    def down(self) -> dict[str, str]:
        """Stop this exact project while retaining its named data volumes."""

        manifest = self._load_manifest()
        self._validate_manifest_identity(manifest)
        self._validate_resource_labels()
        self._run_compose(["down", "--remove-orphans"])
        return {"status": "stopped", "project_name": self.project_name}

    def destroy(self) -> dict[str, str]:
        """Remove only resources carrying this verified Compose project identity."""

        manifest = self._load_manifest()
        self._validate_manifest_identity(manifest)
        self._validate_resource_labels()
        self._run_compose(["down", "--volumes", "--remove-orphans", "--rmi", "local"])
        self._validate_resource_labels()
        remaining = [
            identifier
            for _, docker_kind in RESOURCE_KINDS
            for identifier, _ in self._labeled_resources(docker_kind)
        ]
        if remaining:
            raise WorkspaceRuntimeError("Verified project resources remain after Compose destroy")
        self.manifest_path.unlink()
        return {"status": "destroyed", "project_name": self.project_name}

    def _run_migration_compose(
        self,
        arguments: Sequence[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        migration_compose = self.workspace / MIGRATION_COMPOSE_RELATIVE_PATH
        if not migration_compose.is_file():
            raise WorkspaceRuntimeError(
                f"Migration Compose override is missing: {migration_compose}"
            )
        return self.runner.run(
            [
                "docker",
                "compose",
                "-p",
                migration_project_name(self.workspace),
                "-f",
                str(self.compose_file),
                "-f",
                str(migration_compose),
                *arguments,
            ],
            cwd=self.workspace,
            env=runtime_environment(),
            check=check,
        )

    def _run_postgres_pytest(
        self,
        database_url: str,
        suite: PostgresTestSuite,
    ) -> None:
        environment = runtime_environment()
        environment["R0P_TEST_DATABASE_URL"] = database_url
        subprocess.run(  # noqa: S603 - suite is a Literal resolved through an allowlist.
            [sys.executable, "-m", "pytest", POSTGRES_TEST_PATHS[suite]],
            cwd=self.workspace,
            env=environment,
            check=True,
            timeout=900,
            shell=False,
        )

    def migration_test(
        self,
        suite: PostgresTestSuite = "migrations",
    ) -> dict[str, str]:
        """Run one allowlisted PostgreSQL acceptance suite on a disposable database."""

        provided_url = os.environ.get("R0P_TEST_DATABASE_URL")
        if provided_url is not None:
            validate_test_database_url(provided_url)
            self._run_postgres_pytest(provided_url, suite)
            return {"status": "passed", "database": "provided", "suite": suite}

        project_name = migration_project_name(self.workspace)
        existing_resources = [
            identifier
            for _, docker_kind in RESOURCE_KINDS
            for identifier, _ in self._labeled_resources_for_project(project_name, docker_kind)
        ]
        if existing_resources:
            raise WorkspaceRuntimeError(
                "Disposable migration project already has resources; refusing reuse"
            )
        try:
            self._run_migration_compose(
                [
                    "up",
                    "--detach",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    "postgres",
                ]
            )
            binding_result = self._run_migration_compose(["port", "postgres", "5432"])
            binding = binding_result.stdout.strip()
            host, separator, port = binding.rpartition(":")
            if not separator or host != "127.0.0.1" or not port.isdigit() or port == "0":
                raise WorkspaceRuntimeError("Disposable migration PostgreSQL is not loopback-only")
            _wait_for_loopback_tcp(host, int(port))
            database_url = (
                "postgresql+asyncpg://stock_assistant:synthetic-local-only@"
                f"127.0.0.1:{port}/{MIGRATION_DATABASE_NAME}"
            )
            self._run_postgres_pytest(database_url, suite)
        finally:
            self._validate_resource_labels(project_name)
            self._run_migration_compose(
                ["down", "--volumes", "--remove-orphans"],
            )
            remaining_resources = [
                identifier
                for _, docker_kind in RESOURCE_KINDS
                for identifier, _ in self._labeled_resources_for_project(project_name, docker_kind)
            ]
            if remaining_resources:
                raise WorkspaceRuntimeError(
                    "Disposable migration project resources remain after cleanup"
                )
        return {"status": "passed", "database": "disposable", "suite": suite}


def _default_workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operate an isolated StockMarketAgent workspace stack"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=_default_workspace(),
        help="workspace root; defaults to the repository containing this script",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="validate Docker and rendered Compose config")
    up_parser = subparsers.add_parser("up", help="start an isolated stack")
    up_parser.add_argument(
        "--scope",
        choices=("foundation", "full"),
        default="full",
        help="foundation starts only PostgreSQL, Valkey and SeaweedFS",
    )
    subparsers.add_parser("status", help="show services and dynamic endpoint URLs")
    subparsers.add_parser("down", help="stop the exact stack and retain data volumes")
    subparsers.add_parser(
        "destroy",
        help="remove the exact stack and its verified named volumes",
    )
    migration_parser = subparsers.add_parser(
        "migration-test",
        help="run an allowlisted PostgreSQL suite on a provided or disposable database",
    )
    migration_parser.add_argument(
        "--suite",
        choices=tuple(POSTGRES_TEST_PATHS),
        default="migrations",
        help="migrations validates upgrades; recovery validates Outbox/Inbox/Job recovery",
    )
    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        runtime = WorkspaceRuntime.create(arguments.workspace)
        result: object
        if arguments.command == "doctor":
            result = runtime.doctor()
        elif arguments.command == "up":
            result = runtime.up(cast(RuntimeScope, arguments.scope))
        elif arguments.command == "status":
            result = runtime.status()
        elif arguments.command == "down":
            result = runtime.down()
        elif arguments.command == "destroy":
            result = runtime.destroy()
        elif arguments.command == "migration-test":
            result = runtime.migration_test(cast(PostgresTestSuite, arguments.suite))
        else:
            parser.error("unknown command")
        _print_json(result)
        return 0
    except (WorkspaceRuntimeError, subprocess.CalledProcessError) as exc:
        error_name = (
            "docker_command_failed"
            if isinstance(exc, subprocess.CalledProcessError)
            else "workspace_runtime_error"
        )
        _print_json({"status": "error", "code": error_name, "message": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
