from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts.check_openapi_compatibility import OASDIFF_IMAGE, validate
from scripts.generate_openapi import (
    OUTPUT_PATH,
    OpenApiGenerationError,
    _deny_network_audit,
    render_openapi,
    validate_local_only,
)

GIT_BINARY = shutil.which("git")
NODE_BINARY = shutil.which("node")


def _run_git(root: Path, *args: str) -> None:
    assert GIT_BINARY is not None
    subprocess.run(  # noqa: S603 - fixed Git executable operates only in tmp_path
        [GIT_BINARY, *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


class FakeCompatibilityRunner:
    def __init__(self, *, breaking_returncode: int) -> None:
        self.breaking_returncode = breaking_returncode
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        recorded = tuple(command)
        self.commands.append(recorded)
        if "docker" in Path(recorded[0]).name:
            returncode = self.breaking_returncode if "breaking" in recorded else 0
            return subprocess.CompletedProcess(recorded, returncode, "", "synthetic break")
        return subprocess.run(  # noqa: S603 - command is built by the production checker
            list(recorded),
            check=False,
            capture_output=True,
            text=True,
        )


def _openapi_document() -> dict[str, object]:
    value: object = json.loads(render_openapi())
    assert isinstance(value, dict)
    return value


def _initialize_repository(root: Path, *, publish_snapshot: bool) -> None:
    _run_git(root, "init", "-q")
    (root / "README.md").write_text("synthetic repository\n", encoding="utf-8")
    if publish_snapshot:
        target = root / "schemas/http/openapi.v1.json"
        target.parent.mkdir(parents=True)
        target.write_text(render_openapi(), encoding="utf-8")
    _run_git(root, "add", ".")
    _run_git(
        root,
        "-c",
        "user.name=OpenAPI Test",
        "-c",
        "user.email=openapi@example.invalid",
        "commit",
        "-qm",
        "baseline",
    )
    if not publish_snapshot:
        target = root / "schemas/http/openapi.v1.json"
        target.parent.mkdir(parents=True)
        target.write_text(render_openapi(), encoding="utf-8")


def test_openapi_snapshot_is_deterministic_and_exposes_only_implemented_http() -> None:
    first = render_openapi()
    second = render_openapi()
    document = _openapi_document()

    assert first == second
    assert first == OUTPUT_PATH.read_text(encoding="utf-8")
    assert document["openapi"] == "3.1.0"
    assert set(document["paths"]) == {
        "/api/v1/active-target",
        "/api/v1/briefings/premarket/latest",
        "/api/v1/events/{event_id}/evidence",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/instruments/{instrument_id}/behavior-pressure-snapshots",
        "/api/v1/instruments/search",
        "/api/v1/meta/capabilities",
        "/api/v1/research-sessions",
        "/api/v1/research-sessions/{session_id}",
        "/api/v1/research-sessions/{session_id}/cancel",
        "/api/v1/research-sessions/{session_id}/report",
        "/api/v1/research-sessions/{session_id}/retry",
        "/api/v1/today",
        "/api/v1/today/stream",
        "/api/v2/instruments/search",
        "/api/v2/instruments/directory-search",
        "/api/v2/research-skills",
        "/api/v2/research-sessions",
        "/api/v2/research-sessions-v2.0",
        "/api/v2/research-sessions/{session_id}",
        "/api/v2/research-sessions/{session_id}/view-v2.0",
        "/api/v2/research-sessions/{session_id}/cancel",
        "/api/v2/research-sessions/{session_id}/report",
        "/api/v2/research-sessions/{session_id}/report-v1.1",
        "/api/v2/research-sessions/{session_id}/report-v1.2",
        "/api/v2/research-sessions/{session_id}/report-v2.0",
        "/api/v2/research-sessions/{session_id}/report-v2.1",
        "/api/v2/research-sessions/{session_id}/report-v2.2",
        "/api/v2/research-sessions/{session_id}/retry",
    }
    assert set(document["paths"]["/api/v1/active-target"]) == {"get"}
    assert set(document["paths"]["/api/v1/briefings/premarket/latest"]) == {"get"}
    assert set(document["paths"]["/api/v1/events/{event_id}/evidence"]) == {"get"}
    assert set(document["paths"]["/api/v1/today"]) == {"get"}
    assert set(document["paths"]["/api/v1/today/stream"]) == {"get"}

    vendor_media_type_v1_1 = "application/vnd.stockassistant.r1-source-provenance.v1+json"
    vendor_media_type_v1_2 = "application/vnd.stockassistant.r1-source-provenance.v1.2+json"
    research_media_type_v1_3 = "application/vnd.stockassistant.r1-research-product.v1.3+json"
    for path in (
        "/api/v1/briefings/premarket/latest",
        "/api/v1/events/{event_id}/evidence",
    ):
        content = document["paths"][path]["get"]["responses"]["200"]["content"]
        expected_content = {
            "application/json",
            vendor_media_type_v1_1,
            vendor_media_type_v1_2,
        }
        if path == "/api/v1/briefings/premarket/latest":
            expected_content.add(research_media_type_v1_3)
        assert set(content) == expected_content
        assert content["application/json"]["schema"]["properties"]["data"]["$ref"].endswith("View")
        vendor_schema_v1_1 = content[vendor_media_type_v1_1]["schema"]
        vendor_schema_v1_2 = content[vendor_media_type_v1_2]["schema"]
        assert "V1_1" in vendor_schema_v1_1["properties"]["data"]["$ref"]
        assert "V1_2" in vendor_schema_v1_2["$ref"]
        assert "Union" not in vendor_schema_v1_2["$ref"]
        if path == "/api/v1/briefings/premarket/latest":
            assert (
                "V1_3" in content[research_media_type_v1_3]["schema"]["properties"]["data"]["$ref"]
            )
        responses = document["paths"][path]["get"]["responses"]
        assert responses["200"]["headers"]["Vary"]["schema"] == {
            "type": "string",
            "const": "Accept",
        }
        assert responses["412"]["headers"]["Vary"]["schema"] == {
            "type": "string",
            "const": "Accept",
        }


def test_openapi_rendering_does_not_inherit_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "must-not-leak-into-contract")
    monkeypatch.setenv("WEB_ORIGIN", "https://private-host.invalid")

    document = _openapi_document()

    assert document["info"]["title"] == "StockMarketAgent"
    assert "private-host" not in render_openapi()


def test_first_publication_runs_offline_validation_and_a_breaking_negative_probe(
    tmp_path: Path,
) -> None:
    _initialize_repository(tmp_path, publish_snapshot=False)
    runner = FakeCompatibilityRunner(breaking_returncode=1)

    assert validate(tmp_path, "HEAD", runner=runner) == []

    docker_commands = [command for command in runner.commands if "docker" in Path(command[0]).name]
    assert len(docker_commands) == 2
    for command in docker_commands:
        assert "--pull=never" in command
        assert "--network=none" in command
        assert "--allow-external-refs=false" in command
        assert OASDIFF_IMAGE in command
    assert "validate" in docker_commands[0]
    assert "breaking" in docker_commands[1]


@pytest.mark.parametrize("runtime_returncode", [0, 2, 102, 125])
def test_first_publication_rejects_unexpected_negative_probe_exit_codes(
    tmp_path: Path,
    runtime_returncode: int,
) -> None:
    _initialize_repository(tmp_path, publish_snapshot=False)
    runner = FakeCompatibilityRunner(breaking_returncode=runtime_returncode)

    errors = validate(tmp_path, "HEAD", runner=runner)

    assert len(errors) == 1
    assert f"unexpected exit code {runtime_returncode}" in errors[0]


def test_published_snapshot_reports_a_breaking_oasdiff_result(tmp_path: Path) -> None:
    _initialize_repository(tmp_path, publish_snapshot=True)
    runner = FakeCompatibilityRunner(breaking_returncode=1)

    errors = validate(tmp_path, "HEAD", runner=runner)

    assert errors == ["breaking OpenAPI change detected: synthetic break"]


def test_compatibility_fails_closed_when_base_tree_cannot_be_inspected(
    tmp_path: Path,
) -> None:
    _initialize_repository(tmp_path, publish_snapshot=False)

    class FailingTreeRunner(FakeCompatibilityRunner):
        def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            recorded = tuple(command)
            if "ls-tree" in recorded:
                return subprocess.CompletedProcess(recorded, 128, "", "synthetic tree failure")
            return super().__call__(command)

    errors = validate(
        tmp_path,
        "HEAD",
        runner=FailingTreeRunner(breaking_returncode=1),
    )

    assert errors == ["cannot inspect the base OpenAPI snapshot: synthetic tree failure"]


def test_remote_openapi_references_are_rejected(tmp_path: Path) -> None:
    snapshot = tmp_path / "openapi.json"
    snapshot.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "remote ref probe", "version": "1.0.0"},
                "paths": {},
                "components": {
                    "schemas": {"Remote": {"$ref": "https://contracts.invalid/schema.json"}}
                },
            }
        ),
        encoding="utf-8",
    )

    errors = validate_local_only(snapshot)

    assert len(errors) == 1
    assert "must be an internal '#...' reference" in errors[0]


def test_implicit_redocly_configuration_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "repository"
    generation_cwd = project_root / "apps/web"
    generation_cwd.mkdir(parents=True)
    snapshot = project_root / "schemas/http/openapi.v1.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(render_openapi(), encoding="utf-8")
    (project_root / "redocly.yaml").write_text("apis: {}\n", encoding="utf-8")

    errors = validate_local_only(
        snapshot,
        project_root=project_root,
        type_generation_cwd=generation_cwd,
    )

    assert errors == [
        "implicit Redocly configuration is forbidden for deterministic generation: redocly.yaml"
    ]


@pytest.mark.parametrize(
    "event",
    [
        "socket.__new__",
        "socket.bind",
        "socket.connect",
        "socket.connect_ex",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
    ],
)
def test_python_openapi_renderer_rejects_network_audit_events(event: str) -> None:
    with pytest.raises(OpenApiGenerationError, match="forbidden"):
        _deny_network_audit(event, ())


def test_installed_python_openapi_audit_hook_blocks_udp() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import socket,sys;"
                "from scripts.generate_openapi import _deny_network_audit;"
                "sys.addaudithook(_deny_network_audit);"
                "socket.socket(socket.AF_INET,socket.SOCK_DGRAM)"
                ".sendto(b'probe',('127.0.0.1',9))"
            ),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "forbidden during OpenAPI generation" in result.stderr


def test_node_openapi_type_generator_preload_blocks_network() -> None:
    assert NODE_BINARY is not None
    result = subprocess.run(  # noqa: S603 - fixed local Node safety probe
        [
            NODE_BINARY,
            f"--require={Path('scripts/deny_node_network.cjs').resolve()}",
            "-e",
            "require('node:http').get('http://127.0.0.1:9')",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "forbidden during OpenAPI type generation" in result.stderr
