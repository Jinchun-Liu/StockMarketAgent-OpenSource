import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from scripts.generate_contract_schemas import SCHEMA_TARGETS

from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.event_views import EventEvidenceViewV1_1
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    SourceAccessChannel,
    SourceProvenance,
    SourceRecord,
    SourceReleaseTier,
    SourceRightsStatus,
    SourceRuntimeMode,
    SourceTier,
)
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.portfolio import (
    R1_FIXED_TARGET_INSTRUMENT,
    R1_FIXED_TARGET_WATCHLIST_ID,
    ActiveTargetRevisionRef,
)
from stock_assistant.contracts.premarket_jobs import (
    PremarketIngestJobPayload,
    PremarketIngestJobPayloadV1_1,
    PremarketIngestJobRequestV1_1,
    PremarketIngestJobResultV1_1,
    PremarketIngestTaskResultV1_1,
)
from stock_assistant.contracts.premarket_views import PremarketBriefingViewV1_1

NOW = datetime(2026, 7, 30, 1, 5, tzinfo=UTC)
FIXTURE_ROOT = Path("tests/contract/arc/fixtures/r1/valid")
CONTRACT_PACK = Path("tests/contract/pae/fixtures/r1-dev-demo-contract-pack.v1.1.json")


def _provenance(
    *,
    mode: SourceRuntimeMode = SourceRuntimeMode.DEMO_REPLAY,
) -> SourceProvenance:
    return SourceProvenance(
        schema_version="1.1",
        provider="stock_market_agent",
        access_channel=(
            SourceAccessChannel.DEMO_REPLAY
            if mode is SourceRuntimeMode.DEMO_REPLAY
            else SourceAccessChannel.AKSHARE
        ),
        adapter_name="r1_demo_replay",
        adapter_version="1.0.0",
        endpoint_name="stock_zh_a_disclosure_report_cninfo",
        source_identifier="XSHG:603986:statutory_announcements",
        runtime_mode=mode,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatus.UNVERIFIED,
    )


def _active_target() -> ActiveTargetRevisionRef:
    return ActiveTargetRevisionRef(
        schema_version="1.0",
        watchlist_id=R1_FIXED_TARGET_WATCHLIST_ID,
        watchlist_revision=1,
        instrument=R1_FIXED_TARGET_INSTRUMENT,
        activated_at=NOW - timedelta(minutes=10),
    )


def _briefing_payload() -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / "premarket-briefing.v1.0.json").read_text())
    payload["schema_version"] = "1.1"
    payload["runtime_mode"] = "demo_replay"
    payload["release_tier"] = "dev_only"
    for coverage in payload["source_coverage"]:
        coverage["provenance"] = _provenance().model_dump(mode="json")
    payload["ingestion_progress"]["provenance"] = _provenance().model_dump(mode="json")
    return payload


def _evidence_payload() -> dict[str, object]:
    collection = json.loads((FIXTURE_ROOT / "event-evidence-scenarios.v1.0.json").read_text())
    payload = collection[0]["payload"]
    payload["schema_version"] = "1.1"
    payload["runtime_mode"] = "demo_replay"
    for evidence in payload["evidence"]:
        evidence["event_time"] = evidence["published_at"]
        evidence["provenance"] = _provenance().model_dump(mode="json")
    return payload


@pytest.mark.contract
def test_source_provenance_modes_are_explicit_and_fail_closed() -> None:
    assert {item.value for item in SourceRuntimeMode} == {
        "demo_replay",
        "dev_live",
        "authorized",
    }
    assert {item.value for item in SourceAccessChannel} == {
        "demo_replay",
        "akshare",
        "official_http",
        "authorized_api",
    }
    assert _provenance().release_tier is SourceReleaseTier.DEV_ONLY

    with pytest.raises(ValidationError, match="development-only"):
        _provenance().model_copy(
            update={"release_tier": SourceReleaseTier.AUTHORIZED}
        ).__class__.model_validate(
            {
                **_provenance().model_dump(),
                "release_tier": SourceReleaseTier.AUTHORIZED,
            }
        )

    authorized = {
        **_provenance().model_dump(),
        "runtime_mode": SourceRuntimeMode.AUTHORIZED,
        "access_channel": SourceAccessChannel.AUTHORIZED_API,
        "release_tier": SourceReleaseTier.AUTHORIZED,
        "rights_status": SourceRightsStatus.APPROVED,
    }
    assert SourceProvenance.model_validate(authorized).rights_status is (
        SourceRightsStatus.APPROVED
    )
    for key, value in (
        ("access_channel", SourceAccessChannel.OFFICIAL_HTTP),
        ("release_tier", SourceReleaseTier.DEV_ONLY),
        ("rights_status", SourceRightsStatus.UNVERIFIED),
        ("rights_status", SourceRightsStatus.BLOCKED),
    ):
        with pytest.raises(ValidationError, match="authorized runtime provenance"):
            SourceProvenance.model_validate({**authorized, key: value})

    schema = json.loads(Path("schemas/ingestion/source-provenance.v1.1.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(authorized)
    assert list(
        validator.iter_errors({**authorized, "rights_status": SourceRightsStatus.UNVERIFIED})
    )


@pytest.mark.contract
def test_source_record_requires_event_time_and_provenance() -> None:
    payload = {
        "source_id": "cninfo_demo_replay",
        "external_id": "announcement-1",
        "capability": "statutory_announcements",
        "record_type": "announcement",
        "source_tier": SourceTier.DEVELOPMENT_ONLY,
        "content_tier": ContentTier.DEVELOPMENT_ONLY,
        "fact_eligibility": FactEligibility.INELIGIBLE,
        "authority": 0.0,
        "title": "Synthetic announcement",
        "event_time": NOW - timedelta(minutes=5),
        "first_seen_at": NOW - timedelta(minutes=4),
        "fetched_at": NOW - timedelta(minutes=3),
        "payload": {},
        "rights_snapshot_hash": "a" * 64,
        "provenance": _provenance(),
    }
    assert SourceRecord.model_validate(payload).event_time < NOW
    payload.pop("provenance")
    with pytest.raises(ValidationError, match="supplied together"):
        SourceRecord.model_validate(payload)


@pytest.mark.contract
def test_legacy_source_record_remains_readable_without_v1_1_lineage() -> None:
    record = SourceRecord.model_validate(
        {
            "source_id": "legacy_source",
            "external_id": "legacy-1",
            "capability": "statutory_announcements",
            "record_type": "announcement",
            "source_tier": SourceTier.STATUTORY_DISCLOSURE,
            "content_tier": ContentTier.STATUTORY_DISCLOSURE,
            "fact_eligibility": FactEligibility.COMPANY_FACT,
            "authority": 1.0,
            "first_seen_at": NOW,
            "fetched_at": NOW,
            "payload": {},
            "rights_snapshot_hash": "b" * 64,
        }
    )
    assert record.event_time is None
    assert record.provenance is None


@pytest.mark.contract
def test_job_payload_1_0_remains_readable_and_1_1_is_capability_scoped() -> None:
    old = PremarketIngestJobPayload(
        schema_version="1.0",
        active_target=_active_target(),
        window_start=NOW - timedelta(hours=18),
        window_end=NOW,
        source_id="cninfo_data_service",
        source_adapter_version="1.0.0",
        rights_snapshot_hash="a" * 64,
        ruleset_version="r1-v1",
    )
    assert old.schema_version == "1.0"

    payload = PremarketIngestJobPayloadV1_1(
        schema_version="1.1",
        active_target=_active_target(),
        window_start=NOW - timedelta(hours=18),
        window_end=NOW,
        source_id="cninfo_demo_replay",
        source_adapter_version="1.0.0",
        rights_snapshot_hash="a" * 64,
        ruleset_version="r1-v1",
        capability="statutory.announcements",
        runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
    )
    request = PremarketIngestJobRequestV1_1(
        schema_version="1.1",
        job_id=UUID("00000000-0000-0000-0000-000000000211"),
        context=MutationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW,
            idempotency_key="r1-demo-replay-announcements",
        ),
        payload=payload,
        queued_at=NOW,
    )
    assert request.payload.capability == "statutory.announcements"

    task_result = PremarketIngestTaskResultV1_1(
        schema_version="1.1",
        active_target=_active_target(),
        source_id="cninfo_demo_replay",
        window_start=payload.window_start,
        window_end=payload.window_end,
        records_fetched=1,
        evidence_stored=1,
        event_revisions_stored=1,
        data_manifest_hash="b" * 64,
        source_watermark=NOW,
        capability=payload.capability,
        runtime_mode=payload.runtime_mode,
        provenance=_provenance(),
    )
    result = PremarketIngestJobResultV1_1(
        schema_version="1.1",
        job_id=request.job_id,
        status=JobStatus.SUCCEEDED,
        started_at=NOW,
        completed_at=NOW,
        result=task_result,
        attempt=1,
    )
    assert result.result is not None
    assert result.result.provenance.runtime_mode is SourceRuntimeMode.DEMO_REPLAY
    assert result.result.provenance.endpoint_name == "stock_zh_a_disclosure_report_cninfo"
    assert result.result.provenance.endpoint_name != result.result.capability

    with pytest.raises(ValidationError, match="runtime_mode must match"):
        PremarketIngestTaskResultV1_1.model_validate(
            {
                **task_result.model_dump(),
                "provenance": _provenance(mode=SourceRuntimeMode.DEV_LIVE),
            }
        )


@pytest.mark.contract
def test_public_v1_1_views_require_matching_provenance() -> None:
    briefing = PremarketBriefingViewV1_1.model_validate(_briefing_payload())
    assert briefing.runtime_mode is SourceRuntimeMode.DEMO_REPLAY
    assert all(item.provenance for item in briefing.source_coverage)

    evidence = EventEvidenceViewV1_1.model_validate(_evidence_payload())
    assert evidence.runtime_mode is SourceRuntimeMode.DEMO_REPLAY
    assert all(item.event_time <= item.first_seen_at for item in evidence.evidence)


@pytest.mark.contract
def test_v1_1_schema_targets_are_registered_without_rewriting_v1_0() -> None:
    paths = {target.relative_path.as_posix() for target in SCHEMA_TARGETS}
    assert {
        "schemas/ingestion/source-provenance.v1.1.schema.json",
        "schemas/jobs/premarket-ingest-request.v1.1.schema.json",
        "schemas/jobs/premarket-ingest-result.v1.1.schema.json",
        "schemas/briefings/premarket-view.v1.1.schema.json",
        "schemas/events/event-evidence-view.v1.1.schema.json",
    } <= paths
    assert "schemas/jobs/premarket-ingest-request.v1.0.schema.json" in paths


@pytest.mark.contract
def test_contract_pack_freezes_mode_mapping_without_fallback() -> None:
    pack = json.loads(CONTRACT_PACK.read_text())
    assert pack["fixed_instrument_id"] == "XSHG:603986"
    assert pack["data_mode_mapping"] == {
        "authorized": "real",
        "demo_replay": "synthetic_development_only",
        "dev_live": "real",
    }
    assert pack["no_fallback"] == [
        "authorized_to_dev_live",
        "dev_live_to_demo_replay",
    ]
