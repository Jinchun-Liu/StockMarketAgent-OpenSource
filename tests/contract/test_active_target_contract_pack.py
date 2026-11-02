from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_assistant.contracts.active_targets import ActiveTargetState
from stock_assistant.contracts.api import ApiResponse, ProblemDetails

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "tests/contract/packs/active-target"
OPENAPI = ROOT / "schemas/http/openapi.v1.json"
GENERATED_TYPES = ROOT / "apps/web/lib/api/openapi.generated.ts"


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _local_refs(value: object) -> list[str]:
    if isinstance(value, dict):
        refs = [ref for key, ref in value.items() if key == "$ref" and isinstance(ref, str)]
        return refs + [
            ref for key, child in value.items() if key != "$ref" for ref in _local_refs(child)
        ]
    if isinstance(value, list):
        return [ref for child in value for ref in _local_refs(child)]
    return []


def _resolve_local_ref(document: object, ref: str) -> object:
    assert ref.startswith("#/")
    current = document
    for encoded_token in ref.removeprefix("#/").split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        assert isinstance(current, dict)
        assert token in current, f"dangling OpenAPI reference: {ref}"
        current = current[token]
    return current


@pytest.mark.contract
@pytest.mark.parametrize("name", ["found.v1.json", "uninitialized.v1.json"])
def test_success_examples_validate_against_the_frozen_envelope(name: str) -> None:
    example = ApiResponse[ActiveTargetState].model_validate(_load(PACK / name))

    assert example.data.found is (name == "found.v1.json")
    assert (example.data.active_target is not None) is example.data.found


@pytest.mark.contract
def test_dependency_failure_example_is_a_retryable_503_problem() -> None:
    example = ProblemDetails.model_validate(_load(PACK / "dependency-unavailable.v1.json"))

    assert example.status == 503
    assert example.code == "DEPENDENCY_UNAVAILABLE"
    assert example.retryable is True


@pytest.mark.contract
def test_openapi_freezes_only_the_read_only_active_target_surface() -> None:
    document = _load(OPENAPI)
    assert isinstance(document, dict)
    paths = document["paths"]
    assert isinstance(paths, dict)
    operation = paths["/api/v1/active-target"]
    assert set(operation) == {"get"}
    assert operation["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiResponse_ActiveTargetState_"
    }
    unavailable_content = operation["get"]["responses"]["503"]["content"]
    assert set(unavailable_content) == {"application/problem+json"}
    assert unavailable_content["application/problem+json"]["schema"]["title"] == "ProblemDetails"


@pytest.mark.contract
def test_openapi_contains_no_dangling_local_references() -> None:
    document = _load(OPENAPI)

    refs = _local_refs(document)
    assert refs
    for ref in refs:
        _resolve_local_ref(document, ref)


@pytest.mark.contract
def test_generated_types_include_the_same_active_target_operation() -> None:
    generated = GENERATED_TYPES.read_text(encoding="utf-8")

    assert '"/api/v1/active-target": {' in generated
    assert "get_active_target_api_v1_active_target_get" in generated
