from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from scripts.workspace_runtime import (
    POSTGRES_TEST_PATHS,
    WorkspaceRuntime,
    WorkspaceRuntimeError,
    _build_parser,
    _wait_for_loopback_tcp,
    migration_project_name,
    runtime_environment,
    sanitize_workspace_name,
    validate_test_database_url,
    workspace_project_name,
)

ResponseFactory = Callable[[tuple[str, ...], int], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class RecordedCall:
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    check: bool


class FakeRunner:
    def __init__(self, response_factory: ResponseFactory | None = None) -> None:
        self.calls: list[RecordedCall] = []
        self._response_factory = response_factory or (lambda _args, _count: (0, ""))

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        self.calls.append(RecordedCall(args=command, cwd=cwd, env=env.copy(), check=check))
        returncode, stdout = self._response_factory(command, len(self.calls))
        result = subprocess.CompletedProcess(
            args=list(command),
            returncode=returncode,
            stdout=stdout,
            stderr="synthetic failure" if returncode else "",
        )
        if check and returncode:
            raise subprocess.CalledProcessError(
                returncode=returncode,
                cmd=list(command),
                output=stdout,
                stderr=result.stderr,
            )
        return result


def _make_workspace(root: Path, name: str = "Feature One") -> Path:
    workspace = root / name
    deploy = workspace / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    return workspace


def _ends_with(command: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    return command[-len(suffix) :] == suffix


def test_project_name_uses_sanitized_leaf_and_realpath_hash(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path, "Risk Model @ 01")

    expected_hash = hashlib.sha256(os.fsencode(workspace.resolve())).hexdigest()[:8]

    assert workspace_project_name(workspace) == f"sma-risk-model-01-{expected_hash}"
    assert sanitize_workspace_name("___") == "workspace"


def test_equal_worktree_leaf_names_still_get_distinct_projects(tmp_path: Path) -> None:
    first = _make_workspace(tmp_path / "one", "feature")
    second = _make_workspace(tmp_path / "two", "feature")

    assert workspace_project_name(first) != workspace_project_name(second)


def test_migration_project_is_distinct_and_database_url_is_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)

    assert migration_project_name(workspace).startswith("sma-migration-feature-one-")
    assert migration_project_name(workspace) != workspace_project_name(workspace)
    validate_test_database_url(
        "postgresql+asyncpg://test:test@127.0.0.1:5432/stock_assistant_r0p_test_slot"
    )
    with pytest.raises(WorkspaceRuntimeError, match="must start"):
        validate_test_database_url("postgresql+asyncpg://test:test@127.0.0.1:5432/stock_assistant")
    with pytest.raises(WorkspaceRuntimeError, match="local or CI-internal"):
        validate_test_database_url(
            "postgresql+asyncpg://test:test@database.example.com:5432/stock_assistant_r0p_test"
        )


def test_loopback_readiness_retries_then_closes_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connected:
        closed = False

        def close(self) -> None:
            self.closed = True

    connected = Connected()
    attempts = 0
    delays: list[float] = []

    def connect(_target: tuple[str, int], *, timeout: float) -> Connected:
        nonlocal attempts
        assert timeout == 0.25
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("synthetic port forwarding delay")
        return connected

    monkeypatch.setattr("scripts.workspace_runtime.socket.create_connection", connect)
    monkeypatch.setattr("scripts.workspace_runtime.time.sleep", delays.append)

    _wait_for_loopback_tcp(
        "127.0.0.1",
        5432,
        attempts=2,
        connect_timeout=0.25,
        retry_delay=0.05,
    )

    assert attempts == 2
    assert delays == [0.05]
    assert connected.closed is True


def test_loopback_readiness_fails_closed_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def refuse(_target: tuple[str, int], *, timeout: float) -> None:
        nonlocal attempts
        assert timeout == 0.25
        attempts += 1
        raise ConnectionRefusedError("synthetic unavailable port")

    monkeypatch.setattr("scripts.workspace_runtime.socket.create_connection", refuse)
    monkeypatch.setattr("scripts.workspace_runtime.time.sleep", lambda _delay: None)

    with pytest.raises(WorkspaceRuntimeError, match="loopback port is not ready"):
        _wait_for_loopback_tcp(
            "127.0.0.1",
            5432,
            attempts=2,
            connect_timeout=0.25,
            retry_delay=0.05,
        )

    assert attempts == 2


def test_migration_compose_uses_a_separate_explicit_project(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    override = workspace / "deploy" / "compose.migration-test.yaml"
    override.write_text("services: {}\n", encoding="utf-8")
    runner = FakeRunner()
    runtime = WorkspaceRuntime.create(workspace, runner)

    runtime._run_migration_compose(["config", "--quiet"])

    assert runner.calls[0].args == (
        "docker",
        "compose",
        "-p",
        migration_project_name(workspace),
        "-f",
        str(workspace / "deploy" / "compose.yaml"),
        "-f",
        str(override),
        "config",
        "--quiet",
    )
    assert runner.calls[0].env["COMPOSE_DISABLE_ENV_FILE"] == "1"


def test_postgres_suite_argument_is_allowlisted() -> None:
    parser = _build_parser()

    arguments = parser.parse_args(["migration-test", "--suite", "recovery"])

    assert arguments.suite == "recovery"
    assert POSTGRES_TEST_PATHS == {
        "migrations": "tests/migrations",
        "recovery": "tests/recovery",
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["migration-test", "--suite", "../../private"])


def test_doctor_always_uses_explicit_project_and_disables_dotenv(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    runner = FakeRunner(
        lambda command, _count: (
            0,
            (
                "Docker version 28.1.1\n"
                if command[-1] == "--version"
                else "Docker Compose version v2.35.1\n"
                if _ends_with(command, ("compose", "version"))
                else ""
            ),
        )
    )
    runtime = WorkspaceRuntime.create(workspace, runner)

    result = runtime.doctor()

    assert result["status"] == "ok"
    compose_commands = [
        call.args for call in runner.calls if call.args[:2] == ("docker", "compose")
    ]
    assert compose_commands
    assert all(
        command[2:6] == ("-p", runtime.project_name, "-f", str(runtime.compose_file))
        for command in compose_commands
    )
    assert all(call.env["COMPOSE_DISABLE_ENV_FILE"] == "1" for call in runner.calls)
    assert all("COMPOSE_PROJECT_NAME" not in call.env for call in runner.calls)


def test_foundation_up_records_only_dynamic_loopback_endpoints(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)

    def respond(command: tuple[str, ...], _count: int) -> tuple[int, str]:
        if _ends_with(command, ("port", "seaweedfs", "9333")):
            return 0, "127.0.0.1:49153\n"
        if _ends_with(command, ("port", "web", "3000")):
            return 0, ":0\n"
        if "port" in command:
            return 1, ""
        return 0, ""

    runner = FakeRunner(respond)
    runtime = WorkspaceRuntime.create(workspace, runner)

    manifest = runtime.up("foundation")

    assert manifest["scope"] == "foundation"
    assert manifest["endpoints"] == {"object_storage_console": "http://127.0.0.1:49153"}
    up_command = next(call.args for call in runner.calls if "up" in call.args)
    assert _ends_with(
        up_command,
        (
            "up",
            "--detach",
            "--wait",
            "--wait-timeout",
            "300",
            "postgres",
            "valkey",
            "seaweedfs",
        ),
    )
    assert "--build" not in up_command
    setup_command = next(call.args for call in runner.calls if "run" in call.args)
    assert _ends_with(setup_command, ("run", "--rm", "seaweedfs-configure"))
    assert runner.calls.index(next(call for call in runner.calls if call.args == up_command)) < (
        runner.calls.index(next(call for call in runner.calls if call.args == setup_command))
    )
    serialized = runtime.manifest_path.read_text(encoding="utf-8")
    assert ".env" not in serialized
    assert "password" not in serialized.casefold()
    assert json.loads(serialized)["project_name"] == runtime.project_name


def test_full_up_surfaces_bounded_redacted_compose_diagnostics(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)

    def fail_up(command: tuple[str, ...], _count: int) -> tuple[int, str]:
        if "up" in command:
            return (
                1,
                "token=private-value "
                "postgresql://runtime:private-value@postgres:5432/database compose failed",
            )
        return 0, ""

    runtime = WorkspaceRuntime.create(workspace, FakeRunner(fail_up))

    with pytest.raises(WorkspaceRuntimeError, match="Compose up failed") as captured:
        runtime.up("full")

    message = str(captured.value)
    assert "<redacted>" in message
    assert "private-value" not in message
    assert runtime.project_name in message


def test_status_refreshes_all_published_ports_from_compose(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)

    def respond(command: tuple[str, ...], _count: int) -> tuple[int, str]:
        if _ends_with(command, ("ps", "--services", "--status", "running")):
            return 0, "api\nweb\nseaweedfs\n"
        ports = {
            ("port", "web", "3000"): "127.0.0.1:49001\n",
            ("port", "api", "8000"): "127.0.0.1:49002\n",
            ("port", "seaweedfs", "9333"): "127.0.0.1:49003\n",
        }
        for suffix, output in ports.items():
            if _ends_with(command, suffix):
                return 0, output
        return 0, ""

    runtime = WorkspaceRuntime.create(workspace, FakeRunner(respond))
    runtime._write_manifest("full", {})

    status = runtime.status()

    assert status["running_services"] == ["api", "seaweedfs", "web"]
    assert status["endpoints"] == {
        "api": "http://127.0.0.1:49002",
        "object_storage_console": "http://127.0.0.1:49003",
        "web": "http://127.0.0.1:49001",
    }


def test_status_without_manifest_explains_the_safe_next_action(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    runtime = WorkspaceRuntime.create(workspace, FakeRunner())

    with pytest.raises(WorkspaceRuntimeError) as captured:
        runtime.status()

    message = str(captured.value)
    assert "make workspace-up" in message
    assert "make parallel-doctor" in message
    assert "No Docker resources were changed" in message
    assert runtime.runner.calls == []


def test_status_rejects_non_loopback_published_port(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)

    def respond(command: tuple[str, ...], _count: int) -> tuple[int, str]:
        if _ends_with(command, ("port", "web", "3000")):
            return 0, "0.0.0.0:49001\n"
        return 0, ""

    runtime = WorkspaceRuntime.create(workspace, FakeRunner(respond))
    runtime._write_manifest("full", {})

    with pytest.raises(WorkspaceRuntimeError, match="not loopback-only"):
        runtime.status()


def test_destroy_fails_closed_on_manifest_identity_mismatch(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    runner = FakeRunner()
    runtime = WorkspaceRuntime.create(workspace, runner)
    manifest = runtime._write_manifest("foundation", {})
    manifest["project_name"] = "sma-someone-else-00000000"
    runtime.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WorkspaceRuntimeError, match="identity mismatch"):
        runtime.destroy()

    assert runner.calls == []


def test_destroy_rejects_mislabeled_resources_before_compose_down(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)

    def respond(command: tuple[str, ...], _count: int) -> tuple[int, str]:
        if command[:2] == ("docker", "ps"):
            return 0, "container-1\tsma-unrelated-00000000\n"
        return 0, ""

    runner = FakeRunner(respond)
    runtime = WorkspaceRuntime.create(workspace, runner)
    runtime._write_manifest("foundation", {})

    with pytest.raises(WorkspaceRuntimeError, match="unexpected project label"):
        runtime.destroy()

    assert not any("down" in call.args for call in runner.calls)


def test_destroy_removes_only_the_exact_verified_compose_project(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    state = {"destroyed": False}

    def respond(command: tuple[str, ...], _count: int) -> tuple[int, str]:
        if _ends_with(
            command,
            ("down", "--volumes", "--remove-orphans", "--rmi", "local"),
        ):
            state["destroyed"] = True
            return 0, ""
        if command[:2] in {
            ("docker", "ps"),
            ("docker", "network"),
            ("docker", "volume"),
        }:
            if state["destroyed"]:
                return 0, ""
            identifier = command[1]
            return 0, f"{identifier}-1\t{runtime.project_name}\n"
        return 0, ""

    runner = FakeRunner(respond)
    runtime = WorkspaceRuntime.create(workspace, runner)
    runtime._write_manifest("foundation", {})

    result = runtime.destroy()

    assert result == {"status": "destroyed", "project_name": runtime.project_name}
    assert not runtime.manifest_path.exists()
    down_command = next(call.args for call in runner.calls if "down" in call.args)
    assert down_command[:6] == (
        "docker",
        "compose",
        "-p",
        runtime.project_name,
        "-f",
        str(runtime.compose_file),
    )
    assert _ends_with(
        down_command,
        ("down", "--volumes", "--remove-orphans", "--rmi", "local"),
    )
    assert not any("prune" in call.args for call in runner.calls)


def test_runtime_environment_never_selects_implicit_compose_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "unsafe-shared")
    monkeypatch.setenv("COMPOSE_FILE", "unexpected.yaml")
    monkeypatch.setenv("COMPOSE_PROFILES", "unexpected")

    environment = runtime_environment()

    assert environment["COMPOSE_DISABLE_ENV_FILE"] == "1"
    assert "COMPOSE_PROJECT_NAME" not in environment
    assert "COMPOSE_FILE" not in environment
    assert "COMPOSE_PROFILES" not in environment


def test_compose_targets_are_pinned_isolated_and_loopback_only() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    compose_path = repository_root / "deploy" / "compose.yaml"
    compose_text = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)

    assert "name" not in compose
    assert "env_file" not in compose["x-python-service"]
    assert not re.findall(r"(?<!\$)\$\{", compose_text)
    services = compose["services"]
    assert "redis" not in services
    assert "minio" not in services
    assert "@sha256:" in services["postgres"]["image"]
    assert "@sha256:" in services["valkey"]["image"]
    assert "@sha256:" in services["seaweedfs"]["image"]
    seaweed_command = services["seaweedfs"]["command"]
    assert "-master.volumeSizeLimitMB=256" in seaweed_command
    assert "-master.telemetry=false" in seaweed_command
    assert "-volume.max=16" in seaweed_command
    seaweed_configure = services["seaweedfs-configure"]
    assert "@sha256:" in seaweed_configure["image"]
    assert seaweed_configure["entrypoint"] == ["/bin/sh", "-ec"]
    assert "-volumeGrowthCount=1" in seaweed_configure["command"][0]
    assert "-locationPrefix=/buckets/" in seaweed_configure["command"][0]
    assert seaweed_configure["depends_on"]["seaweedfs"]["condition"] == "service_healthy"
    assert "ports" not in services["postgres"]
    assert "ports" not in services["valkey"]
    seaweed_healthcheck = services["seaweedfs"]["healthcheck"]["test"]
    assert "/usr/bin/weed shell" not in seaweed_healthcheck[1]
    for readiness_probe in (
        "nc -z 127.0.0.1 18888",
        "127.0.0.1:9333/cluster/status",
        "127.0.0.1:8888/",
        "127.0.0.1:8333/",
        "ListAllMyBucketsResult",
    ):
        assert readiness_probe in seaweed_healthcheck[1]
    assert services["seaweedfs"]["ports"] == [
        {
            "name": "object-console",
            "target": 9333,
            "published": "0",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]
    for service, target in (("api", 8000), ("web", 3000)):
        assert services[service]["ports"][0]["target"] == target
        assert services[service]["ports"][0]["published"] == "0"
        assert services[service]["ports"][0]["host_ip"] == "127.0.0.1"

    migration_override = yaml.safe_load(
        (repository_root / "deploy" / "compose.migration-test.yaml").read_text(encoding="utf-8")
    )
    migration_postgres = migration_override["services"]["postgres"]
    assert migration_postgres["environment"]["POSTGRES_DB"].startswith("stock_assistant_r0p_test")
    assert migration_postgres["ports"][0]["published"] == "0"
    assert migration_postgres["ports"][0]["host_ip"] == "127.0.0.1"


def test_ci_has_split_unskippable_jobs_and_success_aggregator() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    workflow_text = (repository_root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]
    required_jobs = {
        "governance",
        "python-quality",
        "web-quality",
        "migrations",
        "integration",
        "e2e",
        "parallel-isolation",
    }

    assert required_jobs.issubset(jobs)
    assert set(jobs["quality-gate"]["needs"]) == required_jobs
    assert jobs["quality-gate"]["if"] == "${{ always() }}"
    assert "pull_request:" not in workflow_text
    assert "merge_group:" not in workflow_text
    assert '      - "integration/**"' in workflow_text
    assert "      - main" in workflow_text
    governance_steps = jobs["governance"]["steps"]
    checkout_step = next(
        step for step in governance_steps if "actions/checkout@" in step.get("uses", "")
    )
    baseline_step = next(
        step
        for step in governance_steps
        if step.get("name") == "Resolve the true cross-commit baseline"
    )
    assert checkout_step["with"]["fetch-depth"] == 0
    assert checkout_step["with"]["persist-credentials"] is False
    assert 'CANDIDATE_BASE="origin/main"' in baseline_step["run"]
    assert "git fetch" not in baseline_step["run"]
    assert "paths:" not in workflow_text
    assert "fetch-depth: 0" in workflow_text
    assert 'SMA_RUN_DOCKER_INTEGRATION: "1"' in workflow_text
    assert 'SMA_RUN_DOCKER_E2E: "1"' in workflow_text
    assert 'SMA_RUN_DOCKER_PARALLEL_ISOLATION: "1"' in workflow_text
