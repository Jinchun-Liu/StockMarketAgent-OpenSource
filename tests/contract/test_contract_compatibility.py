import json
import shutil
import subprocess
from pathlib import Path

from scripts.check_contract_compatibility import validate

GIT_BINARY = shutil.which("git")


def _run_git(root: Path, *args: str) -> None:
    assert GIT_BINARY is not None
    subprocess.run(  # noqa: S603 - fixed Git executable operates only in tmp_path
        [GIT_BINARY, *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _registry(*, frozen: bool = False, port_version: str = "1.0") -> dict[str, object]:
    port: dict[str, object] = {
        "id": f"port:persistence.example:v{port_version}",
        "version": port_version,
        "symbol": "stock_assistant.ports.persistence.ExamplePort",
        "file": "src/stock_assistant/ports/persistence.py",
        "status": "declared",
    }
    if frozen:
        port["signature_status"] = "frozen"
    return {
        "schemas": [
            {
                "id": "schema:events.example:v1.0",
                "version": "1.0",
                "path": "schemas/events/example.v1.0.schema.json",
                "model": "stock_assistant.contracts.events.Example",
                "status": "published",
            }
        ],
        "ports": [port],
    }


def _write_repository(
    root: Path,
    *,
    frozen: bool = False,
    include_delivery: bool = True,
) -> None:
    (root / "governance").mkdir(parents=True)
    (root / "governance/interfaces.json").write_text(
        json.dumps(_registry(frozen=frozen), indent=2),
        encoding="utf-8",
    )
    if include_delivery:
        (root / "governance/delivery.json").write_text(
            json.dumps({"active_execution": None}),
            encoding="utf-8",
        )
    (root / "schemas/events").mkdir(parents=True)
    (root / "schemas/events/example.v1.0.schema.json").write_text(
        '{"type":"object"}\n',
        encoding="utf-8",
    )
    (root / "infra/migrations/versions").mkdir(parents=True)
    (root / "infra/migrations/versions/0001_initial.py").write_text(
        'revision = "0001"\n',
        encoding="utf-8",
    )
    (root / "src/stock_assistant/ports").mkdir(parents=True)
    (root / "src/stock_assistant/ports/persistence.py").write_text(
        (
            "from typing import Protocol\n\n"
            "class ExamplePort(Protocol):\n"
            "    async def get(self, request: LookupRequest) -> LookupResult: ...\n"
        ),
        encoding="utf-8",
    )


def _commit_baseline(root: Path, *, frozen: bool = False) -> None:
    _run_git(root, "init", "-q")
    _write_repository(root, frozen=frozen)
    _run_git(root, "add", ".")
    _run_git(
        root,
        "-c",
        "user.name=Contract Test",
        "-c",
        "user.email=contract@example.invalid",
        "commit",
        "-qm",
        "baseline",
    )


def test_compatibility_rejects_changes_to_published_schema_and_migration(
    tmp_path: Path,
) -> None:
    _commit_baseline(tmp_path)
    (tmp_path / "schemas/events/example.v1.0.schema.json").write_text(
        '{"type":"string"}\n',
        encoding="utf-8",
    )
    (tmp_path / "infra/migrations/versions/0001_initial.py").write_text(
        'revision = "rewritten"\n',
        encoding="utf-8",
    )

    errors = validate(tmp_path, "HEAD")

    assert any("published schema bytes changed" in error for error in errors)
    assert any("published migration bytes changed" in error for error in errors)


def test_compatibility_rejects_changes_to_a_frozen_port_signature(tmp_path: Path) -> None:
    _commit_baseline(tmp_path, frozen=True)
    port = tmp_path / "src/stock_assistant/ports/persistence.py"
    port.write_text(
        port.read_text(encoding="utf-8").replace(
            "LookupRequest",
            "ChangedLookupRequest",
        ),
        encoding="utf-8",
    )

    errors = validate(tmp_path, "HEAD")

    assert any("frozen Port signature changed" in error for error in errors)


def test_compatibility_allows_registered_first_freeze_without_active_execution(
    tmp_path: Path,
) -> None:
    _commit_baseline(tmp_path)
    port = tmp_path / "src/stock_assistant/ports/persistence.py"
    port.write_text(
        (
            port.read_text(encoding="utf-8")
            + "    async def save(self, request: WriteRequest) -> WriteResult: ...\n"
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "governance/interfaces.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["ports"][0]["id"] = "port:persistence.example:v1.1"
    registry["ports"][0]["version"] = "1.1"
    registry["ports"][0]["signature_status"] = "frozen"
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    assert validate(tmp_path, "HEAD") == []


def test_compatibility_rejects_first_freeze_during_active_execution(tmp_path: Path) -> None:
    _commit_baseline(tmp_path)
    registry_path = tmp_path / "governance/interfaces.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["ports"][0]["version"] = "1.1"
    registry["ports"][0]["id"] = "port:persistence.example:v1.1"
    registry["ports"][0]["signature_status"] = "frozen"
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    (tmp_path / "governance/delivery.json").write_text(
        json.dumps({"active_execution": {"id": "unsafe"}}),
        encoding="utf-8",
    )

    errors = validate(tmp_path, "HEAD")

    assert any("first freeze requires active_execution to be null" in error for error in errors)


def test_compatibility_allows_first_freeze_handed_off_by_contract_steward(
    tmp_path: Path,
) -> None:
    _commit_baseline(tmp_path)
    registry_path = tmp_path / "governance/interfaces.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["ports"][0]["version"] = "1.1"
    registry["ports"][0]["id"] = "port:persistence.example:v1.1"
    registry["ports"][0]["signature_status"] = "frozen"
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    (tmp_path / "governance/delivery.json").write_text(
        json.dumps(
            {
                "active_execution": {
                    "id": "r0p-foundation",
                    "status": "integrating",
                    "lanes": [
                        {
                            "role": "contract_steward",
                            "status": "ready_for_integration",
                            "owns_single_writer_zones": ["contract_boundary"],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    assert validate(tmp_path, "HEAD") == []


def test_compatibility_allows_first_delivery_registry_publication(tmp_path: Path) -> None:
    _run_git(tmp_path, "init", "-q")
    _write_repository(tmp_path, include_delivery=False)
    _run_git(tmp_path, "add", ".")
    _run_git(
        tmp_path,
        "-c",
        "user.name=Contract Test",
        "-c",
        "user.email=contract@example.invalid",
        "commit",
        "-qm",
        "baseline without delivery registry",
    )
    (tmp_path / "governance/delivery.json").write_text(
        json.dumps(
            {
                "active_execution": {
                    "id": "r0p-foundation",
                    "status": "implementing",
                    "lanes": [
                        {
                            "role": "contract_steward",
                            "status": "implementing",
                            "owns_single_writer_zones": ["contract_boundary"],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    assert validate(tmp_path, "HEAD") == []
