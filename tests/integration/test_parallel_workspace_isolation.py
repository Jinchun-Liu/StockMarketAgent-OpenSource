"""Four-slot Compose isolation and exact teardown acceptance test."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from scripts.workspace_runtime import WorkspaceRuntime, runtime_environment
from tests.integration.runtime_support import REPOSITORY_ROOT, run_compose

pytestmark = pytest.mark.integration

_SEAWEEDFS_PROBE_ATTEMPTS = 10
_SEAWEEDFS_PROBE_RETRY_DELAY_SECONDS = 1.0


def _require_parallel_isolation() -> None:
    if os.environ.get("SMA_RUN_DOCKER_PARALLEL_ISOLATION") != "1":
        pytest.skip("set SMA_RUN_DOCKER_PARALLEL_ISOLATION=1 to run four Docker slots")


def _project_resource_names(runtime: WorkspaceRuntime, kind: str) -> set[str]:
    if kind == "container":
        arguments = ["ps", "--all"]
        format_value = "{{.ID}}"
    elif kind == "network":
        arguments = ["network", "ls"]
        format_value = "{{.ID}}"
    elif kind == "volume":
        arguments = ["volume", "ls"]
        format_value = "{{.Name}}"
    else:
        raise AssertionError(f"unsupported resource kind: {kind}")
    docker_executable = shutil.which("docker")
    assert docker_executable is not None
    result = subprocess.run(  # noqa: S603 - fixed Docker CLI and structured args
        [
            docker_executable,
            *arguments,
            "--filter",
            f"label=com.docker.compose.project={runtime.project_name}",
            "--format",
            format_value,
        ],
        cwd=runtime.workspace,
        env=runtime_environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    return {line for line in result.stdout.splitlines() if line}


def _run_git(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    git_executable = shutil.which("git")
    assert git_executable is not None
    return subprocess.run(  # noqa: S603 - resolved Git CLI and structured arguments
        [git_executable, *arguments],
        cwd=REPOSITORY_ROOT,
        env=runtime_environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
        shell=False,
    )


def _valkey_round_trip(runtime: WorkspaceRuntime, value: str) -> str:
    set_result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "valkey",
            "valkey-cli",
            "SET",
            "synthetic:isolation-slot",
            value,
        ],
    )
    assert set_result.stdout.strip() == "OK"
    get_result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "valkey",
            "valkey-cli",
            "GET",
            "synthetic:isolation-slot",
        ],
    )
    return get_result.stdout.strip()


def _postgres_round_trip(runtime: WorkspaceRuntime, value: str) -> str:
    result = run_compose(
        runtime,
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "stock_assistant",
            "-d",
            "stock_assistant",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"marker={value}",
            "-At",
        ],
        input_text=(
            "CREATE TABLE IF NOT EXISTS synthetic_isolation_marker "
            "(value text NOT NULL);\n"
            "INSERT INTO synthetic_isolation_marker VALUES (:'marker');\n"
            "SELECT value FROM synthetic_isolation_marker ORDER BY value DESC LIMIT 1;\n"
        ),
    )
    return result.stdout.strip().splitlines()[-1]


def _seaweed_bucket_round_trip(
    runtime: WorkspaceRuntime,
    value: str,
) -> str:
    bucket = f"synthetic-{value}"
    preload_bucket = f"synthetic-preload-{value}"
    script = (
        "import io\n"
        "from minio import Minio\n"
        "client = Minio(\n"
        "    'seaweedfs:8333',\n"
        "    'synthetic-local-access',\n"
        "    'synthetic-local-secret',\n"
        "    secure=False,\n"
        ")\n"
        f"preload_bucket = {preload_bucket!r}\n"
        "if not client.bucket_exists(preload_bucket):\n"
        "    client.make_bucket(preload_bucket)\n"
        "preload_payload = b'preload'\n"
        "client.put_object(\n"
        "    preload_bucket,\n"
        "    'preload.txt',\n"
        "    io.BytesIO(preload_payload),\n"
        "    len(preload_payload),\n"
        ")\n"
        f"bucket = {bucket!r}\n"
        "if not client.bucket_exists(bucket):\n"
        "    client.make_bucket(bucket)\n"
        f"payload = {value.encode()!r}\n"
        "client.put_object(bucket, 'marker.txt', io.BytesIO(payload), len(payload))\n"
        "response = client.get_object(bucket, 'marker.txt')\n"
        "try:\n"
        "    print(response.read().decode('utf-8'))\n"
        "finally:\n"
        "    response.close()\n"
        "    response.release_conn()\n"
    )
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(_SEAWEEDFS_PROBE_ATTEMPTS):
        last_result = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                script,
            ],
            check=False,
        )
        if last_result.returncode == 0 and last_result.stdout.strip() == value:
            return bucket
        if attempt + 1 < _SEAWEEDFS_PROBE_ATTEMPTS:
            time.sleep(_SEAWEEDFS_PROBE_RETRY_DELAY_SECONDS)

    assert last_result is not None
    detail = "\n".join(
        output.strip() for output in (last_result.stdout, last_result.stderr) if output.strip()
    )
    server_logs = run_compose(
        runtime,
        ["logs", "--no-color", "--tail", "200", "seaweedfs"],
        check=False,
    )
    server_detail = "\n".join(
        output.strip() for output in (server_logs.stdout, server_logs.stderr) if output.strip()
    )
    pytest.fail(
        "SeaweedFS signed object round trip did not become ready after "
        f"{_SEAWEEDFS_PROBE_ATTEMPTS} attempts; returncode={last_result.returncode}; "
        f"detail={(detail or 'no captured process output')[-2000:]}; "
        f"server_detail={(server_detail or 'no captured server logs')[-4000:]}"
    )


def test_four_workspace_stacks_are_isolated_and_one_can_be_destroyed(
    tmp_path: Path,
) -> None:
    _require_parallel_isolation()
    worktree_paths = [tmp_path / f"parallel-worktree-{slot}" for slot in range(4)]
    runtimes: list[WorkspaceRuntime] = []

    try:
        for worktree_path in worktree_paths:
            _run_git(
                [
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree_path),
                    "HEAD",
                ]
            )
            runtimes.append(WorkspaceRuntime.create(worktree_path))
        assert len({runtime.project_name for runtime in runtimes}) == 4

        # Keep all four stacks running together, but avoid coupling isolation proof
        # to four simultaneous multi-stage image builds on one Docker daemon.
        for runtime in runtimes:
            runtime.up("full")
        endpoint_urls: dict[str, set[str]] = {
            "web": set(),
            "api": set(),
            "object_storage_console": set(),
        }
        resource_sets: dict[str, list[set[str]]] = {
            "container": [],
            "network": [],
            "volume": [],
        }
        for slot, runtime in enumerate(runtimes):
            status = runtime.status()
            endpoints = status["endpoints"]
            assert isinstance(endpoints, dict)
            assert set(endpoints) == set(endpoint_urls)
            for endpoint_name, urls in endpoint_urls.items():
                urls.add(endpoints[endpoint_name])
            assert _valkey_round_trip(runtime, f"slot-{slot}") == f"slot-{slot}"
            assert _postgres_round_trip(runtime, f"slot-{slot}") == f"slot-{slot}"
            assert _seaweed_bucket_round_trip(runtime, f"slot-{slot}") == (f"synthetic-slot-{slot}")
            for kind in resource_sets:
                resources = _project_resource_names(runtime, kind)
                assert resources
                resource_sets[kind].append(resources)
        assert all(len(urls) == 4 for urls in endpoint_urls.values())
        for kind, per_slot_resources in resource_sets.items():
            for index, resources in enumerate(per_slot_resources):
                others = set().union(
                    *(
                        candidate
                        for candidate_index, candidate in enumerate(per_slot_resources)
                        if candidate_index != index
                    )
                )
                assert resources.isdisjoint(others), kind

        runtimes[0].destroy()
        assert not _project_resource_names(runtimes[0], "container")
        assert not _project_resource_names(runtimes[0], "network")
        assert not _project_resource_names(runtimes[0], "volume")

        for slot, runtime in enumerate(runtimes[1:], start=1):
            status = runtime.status()
            assert status["running_services"] == [
                "api",
                "postgres",
                "scheduler",
                "seaweedfs",
                "valkey",
                "web",
                "worker",
            ]
            endpoints = status["endpoints"]
            assert isinstance(endpoints, dict)
            assert set(endpoints) == set(endpoint_urls)
            assert all(
                endpoints[endpoint_name] in urls for endpoint_name, urls in endpoint_urls.items()
            )
            assert _valkey_round_trip(runtime, f"slot-{slot}") == f"slot-{slot}"
            assert _postgres_round_trip(runtime, f"slot-{slot}") == f"slot-{slot}"
            assert _seaweed_bucket_round_trip(runtime, f"slot-{slot}") == (f"synthetic-slot-{slot}")
    finally:
        for runtime in runtimes:
            if runtime.manifest_path.exists():
                runtime.destroy()
        for worktree_path in worktree_paths:
            if worktree_path.exists():
                assert not _run_git(
                    ["-C", str(worktree_path), "status", "--porcelain"]
                ).stdout.strip()
                _run_git(["worktree", "remove", str(worktree_path)])
