"""Real PostgreSQL, Valkey and SeaweedFS smoke checks using synthetic state."""

from __future__ import annotations

import http.client
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from scripts.workspace_runtime import WorkspaceRuntime
from tests.integration.runtime_support import (
    create_foundation_workspace,
    run_compose,
)

pytestmark = pytest.mark.integration

_UNPUBLISHED_PORT_BINDINGS = frozenset(
    {
        "",
        ":0",
        "0.0.0.0:0",
        "127.0.0.1:0",
        "invalid IP:0",
    }
)


def _port_is_unpublished(*, returncode: int, binding: str) -> bool:
    return returncode != 0 or binding in _UNPUBLISHED_PORT_BINDINGS


def _require_docker_integration() -> None:
    if os.environ.get("SMA_RUN_DOCKER_INTEGRATION") != "1":
        pytest.skip("set SMA_RUN_DOCKER_INTEGRATION=1 to run real service integration")


def _http_status(url: str, path: str) -> int:
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("GET", path)
        return connection.getresponse().status
    finally:
        connection.close()


def test_unpublished_port_binding_allowlist_is_narrow() -> None:
    assert _port_is_unpublished(returncode=0, binding="invalid IP:0")
    assert _port_is_unpublished(returncode=1, binding="unexpected compose error")
    assert not _port_is_unpublished(returncode=0, binding="0.0.0.0:5432")
    assert not _port_is_unpublished(returncode=0, binding="127.0.0.1:5432")


def test_target_foundation_services_are_healthy_and_not_host_exposed(
    tmp_path: Path,
) -> None:
    _require_docker_integration()
    workspace = create_foundation_workspace(tmp_path / "foundation-integration")
    runtime = WorkspaceRuntime.create(workspace)
    try:
        runtime.up("foundation")
        status = runtime.status()
        assert status["running_services"] == ["postgres", "seaweedfs", "valkey"]
        endpoints = status["endpoints"]
        assert isinstance(endpoints, dict)
        console_url = endpoints["object_storage_console"]
        assert _http_status(console_url, "/cluster/status") == 200

        postgres_result = run_compose(
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
                "-Atc",
                "SELECT 1",
            ],
        )
        assert postgres_result.stdout.strip() == "1"

        set_result = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "valkey",
                "valkey-cli",
                "SET",
                "synthetic:integration",
                "isolated",
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
                "synthetic:integration",
            ],
        )
        assert get_result.stdout.strip() == "isolated"

        bucket_result = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "seaweedfs",
                "/usr/bin/curl",
                "-fsS",
                "-X",
                "PUT",
                "http://127.0.0.1:8333/synthetic-integration",
            ],
        )
        assert bucket_result.returncode == 0
        marker_result = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "seaweedfs",
                "/usr/bin/curl",
                "-fsS",
                "-X",
                "PUT",
                "--data-binary",
                "@-",
                "http://127.0.0.1:8333/synthetic-integration/marker.txt",
            ],
            input_text="isolated",
        )
        assert marker_result.returncode == 0
        stored_marker = run_compose(
            runtime,
            [
                "exec",
                "-T",
                "seaweedfs",
                "/usr/bin/curl",
                "-fsS",
                "http://127.0.0.1:8333/synthetic-integration/marker.txt",
            ],
        )
        assert stored_marker.stdout == "isolated"

        for service, port in (
            ("postgres", "5432"),
            ("valkey", "6379"),
            ("seaweedfs", "8333"),
        ):
            unpublished = run_compose(
                runtime,
                ["port", service, port],
                check=False,
            )
            binding = unpublished.stdout.strip()
            assert _port_is_unpublished(
                returncode=unpublished.returncode,
                binding=binding,
            )
    finally:
        if runtime.manifest_path.exists():
            runtime.destroy()
