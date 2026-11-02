"""Recovery exposes useful evidence without labelling a failed run complete."""

from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from stock_assistant.contracts.discovery import ResearchAcquisitionState, SourceDiscoveryResult
from stock_assistant.contracts.research import (
    ResearchAvailableResult,
    ResearchEvidencePacket,
    ResearchExecutionSourceMode,
    ResearchRealSourceMode,
    ResearchResultCompleteness,
    ResearchSessionStatus,
    ResearchSessionView,
    ResearchSourceMode,
)
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet


def test_legacy_acquisition_and_discovery_default_to_unknown_diagnostics() -> None:
    now = datetime(2026, 9, 5, tzinfo=UTC)
    state = ResearchAcquisitionState(
        schema_version="1.0",
        rule_version="legacy",
        instrument_id="XSHG:688307",
        requested_as_of=now,
        deadline_at=now,
        input_sha256="a" * 64,
    )
    assert state.attempted_queries == state.attempted_contents == state.discovery_diagnostics == {}
    result = SourceDiscoveryResult(
        schema_version="1.0",
        provider="legacy",
        status="no_results",
        request_sha256="b" * 64,
        searched_at=now,
    )
    assert result.diagnostics.raw_count is None
    assert result.diagnostics.selected_count == 0
    assert Draft202012Validator(SourceDiscoveryResult.model_json_schema()).is_valid(
        result.model_dump(mode="json")
    )
    restored = ResearchAcquisitionState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_execution_source_mode_schema_matches_runtime_and_preserves_legacy_enum() -> None:
    adapter = TypeAdapter(ResearchExecutionSourceMode)
    schema = adapter.json_schema()
    assert schema["type"] == "string"
    validator = Draft202012Validator(schema)
    for mode in (*ResearchSourceMode, *ResearchRealSourceMode):
        assert validator.is_valid(mode.value)
        assert adapter.validate_json(adapter.dump_json(mode)) is mode
    for invalid in (None, 1, {}, [], "unregistered_source"):
        assert not validator.is_valid(invalid)
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
    assert TypeAdapter(ResearchSourceMode).json_schema()["enum"] == [
        "synthetic_development_only",
        "production_authorized",
    ]


def test_sparse_packet_keeps_evidence_integrity_without_a_source_count_gate() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="合成公司",
        as_of=datetime(2026, 9, 5, tzinfo=UTC),
    )
    payload = packet.model_dump(mode="python")
    payload["facts"] = packet.facts[:1]
    sparse = ResearchEvidencePacket.model_validate(payload)
    assert sparse.facts == packet.facts[:1]
    payload["citations"] = []
    with pytest.raises(ValidationError, match="every fact must resolve"):
        ResearchEvidencePacket.model_validate(payload)


def test_failed_session_cannot_advertise_complete_report() -> None:
    now = datetime(2026, 9, 5, tzinfo=UTC)
    payload = {
        "schema_version": "1.0",
        "session_id": "00000000-0000-0000-0000-000000000001",
        "job_id": "00000000-0000-0000-0000-000000000002",
        "instrument_id": "XSHG:603986",
        "instrument_name": "合成公司",
        "as_of": now,
        "analysis_config": {"schema_version": "1.0"},
        "source_mode": ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        "status": ResearchSessionStatus.FAILED,
        "progress": {
            "stage": "debating_cases",
            "message": "模型暂不可用",
            "latest_progress_at": now,
        },
        "failure_reason_code": "MODEL_PROVIDER_UNAVAILABLE",
        "created_at": now,
        "updated_at": now,
        "expires_at": datetime(2026, 9, 12, tzinfo=UTC),
        "result_completeness": ResearchResultCompleteness.EXECUTION_FAILED,
        "available_result": ResearchAvailableResult(
            schema_version="1.0",
            as_of=now,
            source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
            non_production_notice="合成资料, 仅验证流程。",
        ),
    }
    session = ResearchSessionView.model_validate(payload)
    assert session.status is ResearchSessionStatus.FAILED
    assert session.model_dump(mode="json")["schema_version"] == "1.0"
    payload["result_completeness"] = ResearchResultCompleteness.COMPLETE
    with pytest.raises(ValidationError, match="only succeeded"):
        ResearchSessionView.model_validate(payload)
