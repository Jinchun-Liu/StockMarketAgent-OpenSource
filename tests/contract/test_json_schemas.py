import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from scripts.generate_contract_schemas import SCHEMA_TARGETS, find_schema_drift

from stock_assistant.contracts.briefings import Briefing, BriefingItem
from stock_assistant.contracts.notifications import Notification


@pytest.mark.contract
@pytest.mark.parametrize(
    "path",
    sorted(Path("schemas").rglob("*.schema.json")),
    ids=lambda path: str(path),
)
def test_schema_is_valid_draft_2020(path: Path) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


@pytest.mark.contract
def test_checked_in_schemas_match_pydantic_sources() -> None:
    assert find_schema_drift() == ()


@pytest.mark.contract
def test_only_exact_versioned_contract_schema_paths_are_published() -> None:
    actual = set(Path("schemas").rglob("*.schema.json"))
    expected = {target.relative_path for target in SCHEMA_TARGETS}
    assert actual == expected


@pytest.mark.contract
def test_research_generate_v1_1_is_additive_and_keeps_v1_0_immutable() -> None:
    legacy_path = Path("schemas/jobs/research-generate-request.v1.0.schema.json")
    current_path = Path("schemas/jobs/research-generate-request.v1.1.schema.json")
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))

    assert "cutoff_mode" not in legacy["properties"]
    assert legacy["properties"]["schema_version"]["const"] == "1.0"
    assert current["properties"]["schema_version"]["const"] == "1.1"
    assert "cutoff_mode" in current["required"]
    assert current["$defs"]["ResearchCutoffMode"]["enum"] == [
        "current_run",
        "historical_replay",
    ]
    assert current["x-runtime-invariants"] == [
        "context.deadline_at is required and must be an absolute timezone-aware timestamp",
        "schema version 1.0 is interpreted as historical_replay and remains separately published",
    ]

    registry = json.loads(Path("governance/interfaces.json").read_text(encoding="utf-8"))
    research_task = next(item for item in registry["tasks"] if item["key"] == "research_generate")
    assert research_task["version"] == "1.1"
    assert research_task["request_schema"] == ("schema:jobs.research-generate-request:v1.1")
    assert research_task["backward_compatible_request_schemas"] == [
        "schema:jobs.research-generate-request:v1.0"
    ]

    research_port = next(
        item
        for item in registry["ports"]
        if item["symbol"] == "stock_assistant.ports.research.ResearchRepositoryPort"
    )
    assert research_port["id"] == "port:research.research-repository:v1.2"
    assert research_port["version"] == "1.2"


@pytest.mark.contract
def test_pydantic_examples_match_checked_in_wire_schemas(event_factory) -> None:
    now = datetime(2026, 7, 21, 0, 20, tzinfo=UTC)
    event = event_factory()
    briefing = Briefing(
        schema_version="1.0",
        kind="premarket",
        generated_at=now,
        as_of=now,
        items=[
            BriefingItem(
                headline="Synthetic update",
                fact_summary="A deterministic test fact.",
                change_type="update",
                source_ids=["synthetic_exchange"],
                published_at=now,
                first_seen_at=now,
                event_ids=[event.event_id],
                evidence_ids=[event.evidence[0].evidence_id],
                confidence=0.9,
                is_material_change=True,
            )
        ],
        data_delay_seconds=30,
        model_version="test-model",
        prompt_hash="a" * 64,
        rule_version="test-rules",
    )
    notification = Notification(
        schema_version="1.0",
        event_cluster_id=event.cluster_id,
        event_revision=event.revision,
        change_type="new",
        severity="normal",
        title="Synthetic event",
        evidence_ids=[uuid4()],
        created_at=now,
        idempotency_key="b" * 32,
    )

    examples = {
        "schemas/events/market-event.v1.0.schema.json": event,
        "schemas/briefings/briefing.v1.0.schema.json": briefing,
        "schemas/notifications/notification.v1.0.schema.json": notification,
    }
    for path, model in examples.items():
        schema = json.loads(Path(path).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(model.model_dump(mode="json"))


@pytest.mark.contract
def test_report_v1_2_publishes_new_score_basis_without_rewriting_v1_1() -> None:
    previous = json.loads(Path("schemas/research/report.v1.1.schema.json").read_text())
    current = json.loads(Path("schemas/research/report.v1.2.schema.json").read_text())
    assert previous["properties"]["schema_version"]["const"] == "1.1"
    assert "scoring_basis" not in previous["$defs"]["ResearchScorecard"]["properties"]
    assert current["properties"]["schema_version"]["const"] == "1.2"
    assert current["$defs"]["ResearchScoreBasis"]["enum"] == [
        "real_evidence",
        "synthetic_scenario",
    ]
