"""Regression coverage for the published R1 rights schema generation boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.generate_contract_schemas import SCHEMA_TARGETS, SchemaTarget, render_schema

from stock_assistant.contracts.ingestion import RightsCheckV1_1, RightsDecisionV1_1

RIGHTS_TARGETS = (
    (
        RightsCheckV1_1,
        Path("schemas/ingestion/rights-check.v1.1.schema.json"),
        "schema:ingestion.rights-check:v1.1",
    ),
    (
        RightsDecisionV1_1,
        Path("schemas/ingestion/rights-decision.v1.1.schema.json"),
        "schema:ingestion.rights-decision:v1.1",
    ),
)


def _target_for(model: type[RightsCheckV1_1] | type[RightsDecisionV1_1]) -> SchemaTarget:
    matches = [target for target in SCHEMA_TARGETS if target.model is model]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(("model", "path", "registry_id"), RIGHTS_TARGETS)
def test_rights_schema_target_matches_published_registry_entry(
    model: type[RightsCheckV1_1] | type[RightsDecisionV1_1],
    path: Path,
    registry_id: str,
) -> None:
    target = _target_for(model)
    registry = json.loads(Path("governance/interfaces.json").read_text(encoding="utf-8"))
    entries = [item for item in registry["schemas"] if item["id"] == registry_id]

    assert len(entries) == 1
    assert target.version == "1.1"
    assert target.relative_path == path
    assert entries[0]["version"] == target.version
    assert entries[0]["path"] == target.relative_path.as_posix()
    assert entries[0]["model"] == target.qualified_model_name


@pytest.mark.parametrize(("model", "path", "_registry_id"), RIGHTS_TARGETS)
def test_rights_schema_rendering_is_deterministic_and_checked_in(
    model: type[RightsCheckV1_1] | type[RightsDecisionV1_1],
    path: Path,
    _registry_id: str,
) -> None:
    target = _target_for(model)
    first_render = render_schema(target)

    assert render_schema(target) == first_render
    assert path.read_text(encoding="utf-8") == first_render


def test_r1_source_provenance_http_artifacts_survive_additive_r2_generation() -> None:
    openapi = json.loads(Path("schemas/http/openapi.v1.json").read_text(encoding="utf-8"))
    generated_types = Path("apps/web/lib/api/openapi.generated.ts").read_text(encoding="utf-8")

    assert "/api/v1/briefings/premarket/latest" in openapi["paths"]
    assert "/api/v1/events/{event_id}/evidence" in openapi["paths"]
    assert "PremarketBriefingViewV1_3: {" in generated_types
    assert "EventEvidenceViewV1_2: {" in generated_types
    assert "SourceProvenanceV1_2: {" in generated_types
