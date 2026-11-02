from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from apps.api.http import RequestContextMiddleware
from apps.api.routes.premarket import (
    _requested_provenance_version,
    _response,
    load_latest_briefing,
    router,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from scripts.generate_contract_schemas import SCHEMA_TARGETS
from starlette.requests import Request

from stock_assistant.contracts.behavior import (
    AttentionRiskBand,
    AttentionRiskCategory,
    AttentionRiskCategoryName,
    AttentionRiskIndex,
    BehaviorPressureSummary,
    EmpiricalRankMapping,
    HistoricalAnalogState,
    HistoricalAnalogSummary,
    IndexAvailability,
    InterpretationOrigin,
    InterpretationStatus,
    MarketStructureDimension,
    ModelInterpretation,
    PlainLanguageUpdate,
    PressureBand,
    PressureFacet,
    PressureFacetName,
    PressureMode,
    StructureAvailability,
    StructureDimensionName,
)
from stock_assistant.contracts.event_views import EventEvidenceViewV1_2
from stock_assistant.contracts.ingestion import (
    SourceAccessChannelV1_2,
    SourceEvidenceRole,
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceRecordV1_2,
    SourceReleaseTier,
    SourceRightsStatusV1_2,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.portfolio import (
    R1_FIXED_TARGET_INSTRUMENT,
    R1_FIXED_TARGET_WATCHLIST_ID,
    ActiveTargetRevisionRef,
)
from stock_assistant.contracts.premarket_jobs import PremarketIngestJobPayloadV1_2
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)
from stock_assistant.contracts.r1_http import (
    R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3,
    project_premarket_briefing_v1_2,
)

NOW = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)
FIXTURE_ROOT = Path("tests/contract/arc/fixtures/r1/valid")
CONTRACT_PACK = Path("tests/contract/pae/fixtures/r1-functional-contract-pack.v1.2.json")


def _provenance(
    mode: SourceRuntimeModeV1_2 = SourceRuntimeModeV1_2.DEMO_REPLAY,
) -> SourceProvenanceV1_2:
    if mode is SourceRuntimeModeV1_2.AUTHORIZED:
        channel = SourceAccessChannelV1_2.AUTHORIZED_API
        release_tier = SourceReleaseTier.AUTHORIZED
        rights_status = SourceRightsStatusV1_2.APPROVED
        quality_tier = SourceQualityTier.AUTHORIZED
    elif mode is SourceRuntimeModeV1_2.DEV_LIVE:
        channel = SourceAccessChannelV1_2.BAOSTOCK_SDK
        release_tier = SourceReleaseTier.DEV_ONLY
        rights_status = SourceRightsStatusV1_2.UNVERIFIED
        quality_tier = SourceQualityTier.FREE_STRUCTURED
    else:
        channel = SourceAccessChannelV1_2.DEMO_REPLAY
        release_tier = SourceReleaseTier.DEV_ONLY
        rights_status = SourceRightsStatusV1_2.NOT_APPLICABLE
        quality_tier = SourceQualityTier.DEMO_REPLAY
    return SourceProvenanceV1_2(
        schema_version="1.2",
        provider="stock_market_agent",
        access_channel=channel,
        adapter_name="r1_source_adapter",
        adapter_version="1.2.0",
        endpoint_name="fixed_target_records",
        source_identifier="XSHG:603986:statutory_announcements",
        runtime_mode=mode,
        release_tier=release_tier,
        rights_status=rights_status,
        quality_tier=quality_tier,
        evidence_role=SourceEvidenceRole.AUTHORITATIVE,
    )


def _active_target() -> ActiveTargetRevisionRef:
    return ActiveTargetRevisionRef(
        schema_version="1.0",
        watchlist_id=R1_FIXED_TARGET_WATCHLIST_ID,
        watchlist_revision=1,
        instrument=R1_FIXED_TARGET_INSTRUMENT,
        activated_at=NOW - timedelta(minutes=10),
    )


def _v1_1_mappable_provenance() -> SourceProvenanceV1_2:
    payload = _provenance(SourceRuntimeModeV1_2.DEV_LIVE).model_dump(mode="json")
    payload["access_channel"] = "akshare"
    payload["evidence_role"] = "structured_helper"
    return SourceProvenanceV1_2.model_validate(payload)


def _v1_1_mappable_briefing_payload() -> dict[str, object]:
    payload = _briefing_payload()
    payload["runtime_mode"] = "dev_live"
    payload["ingestion_progress"]["data_mode"] = "real"
    payload["ingestion_progress"]["provenance"] = _v1_1_mappable_provenance().model_dump(
        mode="json"
    )
    for coverage in payload["source_coverage"]:
        coverage["progress"]["data_mode"] = "real"
        coverage["provenance"] = _v1_1_mappable_provenance().model_dump(mode="json")
    return payload


def _briefing_payload() -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / "premarket-briefing.v1.0.json").read_text())
    payload["schema_version"] = "1.2"
    payload["runtime_mode"] = "demo_replay"
    payload["release_tier"] = "dev_only"
    for coverage in payload["source_coverage"]:
        coverage["provenance"] = _provenance().model_dump(mode="json")
    payload["ingestion_progress"]["provenance"] = _provenance().model_dump(mode="json")
    return payload


def _evidence_payload() -> dict[str, object]:
    scenarios = json.loads((FIXTURE_ROOT / "event-evidence-scenarios.v1.0.json").read_text())
    payload = scenarios[0]["payload"]
    payload["schema_version"] = "1.2"
    payload["runtime_mode"] = "demo_replay"
    for evidence in payload["evidence"]:
        evidence["event_time"] = evidence["published_at"]
        evidence["provenance"] = _provenance().model_dump(mode="json")
    return payload


@pytest.mark.contract
def test_v1_2_provenance_adds_public_static_quality_role_and_baostock() -> None:
    assert {item.value for item in SourceRuntimeModeV1_2} == {
        "demo_replay",
        "dev_live",
        "public_static",
        "authorized",
    }
    assert SourceRightsStatusV1_2.NOT_APPLICABLE.value == "not_applicable"
    assert SourceAccessChannelV1_2.BAOSTOCK_SDK.value == "baostock_sdk"
    assert {item.value for item in SourceEvidenceRole} == {
        "authoritative",
        "structured_helper",
        "discovery",
        "cross_check",
    }
    assert _provenance(SourceRuntimeModeV1_2.PUBLIC_STATIC).rights_status is (
        SourceRightsStatusV1_2.NOT_APPLICABLE
    )

    with pytest.raises(ValidationError, match="public static provenance"):
        SourceProvenanceV1_2.model_validate(
            {
                **_provenance(SourceRuntimeModeV1_2.PUBLIC_STATIC).model_dump(),
                "access_channel": SourceAccessChannelV1_2.BAOSTOCK_SDK,
            }
        )


@pytest.mark.contract
def test_v1_2_adds_typed_source_record_and_optional_replay_session() -> None:
    record = SourceRecordV1_2(
        source_id="cninfo_public",
        external_id="announcement-1",
        capability="statutory_announcements",
        record_type="announcement",
        source_tier="development_only",
        content_tier="development_only",
        fact_eligibility="ineligible",
        authority=0.0,
        title="Synthetic announcement",
        event_time=NOW - timedelta(minutes=5),
        first_seen_at=NOW - timedelta(minutes=4),
        fetched_at=NOW - timedelta(minutes=3),
        rights_snapshot_hash="a" * 64,
        provenance=_provenance(),
    )
    assert record.provenance.schema_version == "1.2"

    payload = PremarketIngestJobPayloadV1_2(
        schema_version="1.2",
        active_target=_active_target(),
        window_start=NOW - timedelta(hours=18),
        window_end=NOW,
        source_id="cninfo_public",
        source_adapter_version="1.2.0",
        rights_snapshot_hash="a" * 64,
        ruleset_version="r1-functional-v1",
        capability="statutory_announcements",
        runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
        replay_session_id="2026-07-31",
    )
    assert payload.replay_session_id == "2026-07-31"
    assert payload.model_copy(update={"replay_session_id": None}).replay_session_id is None


@pytest.mark.contract
def test_v1_2_read_models_require_v1_2_provenance() -> None:
    briefing = PremarketBriefingViewV1_2.model_validate(_briefing_payload())
    evidence = EventEvidenceViewV1_2.model_validate(_evidence_payload())

    assert briefing.schema_version == "1.2"
    assert evidence.schema_version == "1.2"
    assert all(item.provenance.schema_version == "1.2" for item in briefing.source_coverage)
    assert all(item.provenance.schema_version == "1.2" for item in evidence.evidence)

    invalid = _briefing_payload()
    invalid["source_coverage"][0]["provenance"] = SourceProvenance.model_validate(
        {
            "schema_version": "1.1",
            "provider": "stock_market_agent",
            "access_channel": "demo_replay",
            "adapter_name": "r1_demo_replay",
            "adapter_version": "1.0.0",
            "endpoint_name": "fixed_target_records",
            "source_identifier": "XSHG:603986:statutory_announcements",
            "runtime_mode": "demo_replay",
            "release_tier": "dev_only",
            "rights_status": "unverified",
        }
    ).model_dump(mode="json")
    with pytest.raises(ValidationError):
        PremarketBriefingViewV1_2.model_validate(invalid)


@pytest.mark.contract
def test_v1_2_schema_targets_are_additive() -> None:
    paths = {target.relative_path.as_posix() for target in SCHEMA_TARGETS}
    assert {
        "schemas/ingestion/source-provenance.v1.2.schema.json",
        "schemas/jobs/premarket-ingest-request.v1.2.schema.json",
        "schemas/jobs/premarket-ingest-result.v1.2.schema.json",
        "schemas/briefings/premarket-view.v1.2.schema.json",
        "schemas/events/event-evidence-view.v1.2.schema.json",
    } <= paths
    assert "schemas/ingestion/source-provenance.v1.1.schema.json" in paths


@pytest.mark.contract
def test_v1_2_generated_schema_encodes_runtime_invariants() -> None:
    schema = SourceProvenanceV1_2.model_json_schema()
    Draft202012Validator.check_schema(schema)
    assert len(schema["allOf"]) == 4
    validator = Draft202012Validator(schema)
    validator.validate(_provenance(SourceRuntimeModeV1_2.PUBLIC_STATIC).model_dump(mode="json"))
    invalid = _provenance(SourceRuntimeModeV1_2.PUBLIC_STATIC).model_dump(mode="json")
    invalid["access_channel"] = "baostock_sdk"
    assert list(validator.iter_errors(invalid))


@pytest.mark.contract
def test_http_negotiation_prefers_v1_2_then_v1_1_and_defaults_to_v1_0() -> None:
    def request(accept: str) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(b"accept", accept.encode())],
                "method": "GET",
                "path": "/api/v1/briefings/premarket/latest",
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("testclient", 123),
                "scheme": "http",
            }
        )

    assert (
        _requested_provenance_version(
            request(
                "application/vnd.stockassistant.r1-source-provenance.v1+json, "
                "application/vnd.stockassistant.r1-source-provenance.v1.2+json"
            )
        )
        == "1.2"
    )
    assert (
        _requested_provenance_version(
            request("application/vnd.stockassistant.r1-source-provenance.v1+json")
        )
        == "1.1"
    )
    assert _requested_provenance_version(request("application/json")) == "1.0"
    assert _requested_provenance_version(request(R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3)) == "1.3"
    assert (
        _requested_provenance_version(
            request(
                "application/vnd.stockassistant.r1-source-provenance.v1.2+json;q=0, "
                "application/json"
            )
        )
        == "1.0"
    )
    assert (
        _requested_provenance_version(
            request(
                "application/vnd.stockassistant.r1-source-provenance.v1.2+json;q=0.4, "
                "application/vnd.stockassistant.r1-source-provenance.v1+json;q=0.8"
            )
        )
        == "1.1"
    )
    assert (
        _requested_provenance_version(
            request("application/vnd.stockassistant.r1-source-provenance.v1.2+json;q=0")
        )
        is None
    )
    assert _requested_provenance_version(request("application/json;q=0, */*;q=1")) == "1.3"

    pack = json.loads(CONTRACT_PACK.read_text())
    assert pack["preferred_http_media_types"] == [
        "application/vnd.stockassistant.r1-source-provenance.v1.2+json",
        "application/vnd.stockassistant.r1-source-provenance.v1+json",
        "application/json",
    ]


@pytest.mark.contract
def test_http_v1_2_representation_is_exact_and_default_remains_v1_0() -> None:
    def request(accept: str) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(b"accept", accept.encode())],
                "method": "GET",
                "path": "/api/v1/briefings/premarket/latest",
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("testclient", 123),
                "scheme": "http",
            }
        )

    briefing = PremarketBriefingViewV1_2.model_validate(_briefing_payload())
    v1_2 = _response(
        request=request("application/vnd.stockassistant.r1-source-provenance.v1.2+json"),
        data=briefing,
        as_of=briefing.as_of,
    )
    legacy = _response(request=request("application/json"), data=briefing, as_of=briefing.as_of)
    unavailable = _response(
        request=request("application/vnd.stockassistant.r1-source-provenance.v1+json"),
        data=briefing,
        as_of=briefing.as_of,
    )
    wildcard_without_json = _response(
        request=request("application/json;q=0, */*;q=1"),
        data=briefing,
        as_of=briefing.as_of,
    )

    assert v1_2.media_type == "application/vnd.stockassistant.r1-source-provenance.v1.2+json"
    assert json.loads(bytes(v1_2.body))["data"]["schema_version"] == "1.2"
    assert legacy.media_type == "application/json"
    assert json.loads(bytes(legacy.body))["data"]["schema_version"] == "1.0"
    assert unavailable.status_code == 412
    assert wildcard_without_json.media_type == (
        "application/vnd.stockassistant.r1-source-provenance.v1.2+json"
    )
    assert json.loads(bytes(wildcard_without_json.body))["data"]["schema_version"] == "1.2"


@pytest.mark.contract
def test_v1_2_projects_to_v1_1_only_when_added_semantics_are_reconstructable() -> None:
    def request() -> Request:
        return Request(
            {
                "type": "http",
                "headers": [
                    (
                        b"accept",
                        b"application/vnd.stockassistant.r1-source-provenance.v1+json",
                    )
                ],
                "method": "GET",
                "path": "/api/v1/briefings/premarket/latest",
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("testclient", 123),
                "scheme": "http",
            }
        )

    mappable = PremarketBriefingViewV1_2.model_validate(_v1_1_mappable_briefing_payload())
    response = _response(request=request(), data=mappable, as_of=mappable.as_of)
    assert response.status_code == 200
    assert response.media_type == "application/vnd.stockassistant.r1-source-provenance.v1+json"
    assert json.loads(bytes(response.body))["data"]["schema_version"] == "1.1"

    for unrepresentable in (
        PremarketBriefingViewV1_2.model_validate(_briefing_payload()),
        PremarketBriefingViewV1_2.model_validate(
            {
                **_v1_1_mappable_briefing_payload(),
                "ingestion_progress": {
                    **_v1_1_mappable_briefing_payload()["ingestion_progress"],
                    "provenance": _provenance(SourceRuntimeModeV1_2.DEV_LIVE).model_dump(
                        mode="json"
                    ),
                },
                "source_coverage": [
                    {
                        **item,
                        "provenance": _provenance(SourceRuntimeModeV1_2.DEV_LIVE).model_dump(
                            mode="json"
                        ),
                    }
                    for item in _v1_1_mappable_briefing_payload()["source_coverage"]
                ],
            }
        ),
    ):
        response = _response(request=request(), data=unrepresentable, as_of=unrepresentable.as_of)
        assert response.status_code == 412


@pytest.mark.contract
def test_v1_3_projects_to_byte_equivalent_v1_2_semantics() -> None:
    base_payload = _briefing_payload()
    base_payload["instrument"] = {
        "instrument_id": "XSHG:603986",
        "symbol": "603986",
        "exchange": "XSHG",
        "market": "CN",
        "asset_class": "equity",
        "currency": "CNY",
    }
    base = PremarketBriefingViewV1_2.model_validate(base_payload)
    categories = [
        AttentionRiskCategory(
            category=name,
            score=0,
            available=True,
        )
        for name in AttentionRiskCategoryName
    ]
    facets = [
        PressureFacet(facet=name, score=0, availability=IndexAvailability.READY)
        for name in PressureFacetName
    ]
    dimensions = [
        MarketStructureDimension(
            dimension=name,
            availability=(
                StructureAvailability.NOT_APPLICABLE
                if name is StructureDimensionName.INTRADAY_ACCEPTANCE
                else StructureAvailability.READY
            ),
            state=None if name is StructureDimensionName.INTRADAY_ACCEPTANCE else "状态正常",
            explanation="按冻结规则确定性计算。",
        )
        for name in StructureDimensionName
    ]
    value = PremarketBriefingViewV1_3.model_validate(
        {
            **base.model_dump(mode="json"),
            "schema_version": "1.3",
            "attention_risk_index": AttentionRiskIndex(
                score=0,
                band=AttentionRiskBand.CALM,
                availability=IndexAvailability.READY,
                as_of=base.as_of,
                categories=categories,
                available_category_count=5,
                completeness_percent=100,
            ).model_dump(mode="json"),
            "behavior_pressure_summary": BehaviorPressureSummary(
                mode=PressureMode.POST_CLOSE,
                score=0,
                band=PressureBand.STABLE,
                availability=IndexAvailability.READY,
                score_mapping=EmpiricalRankMapping(
                    raw_total=0,
                    population_count=1000,
                    less_count=0,
                    equal_count=1,
                    mapped_score=0,
                    reference_mapping_version="behavior-mid-rank.v1",
                    reference_population_hash="a" * 64,
                ),
                as_of=base.as_of,
                facets=facets,
                structure_dimensions=dimensions,
                historical_analogs=HistoricalAnalogSummary(
                    state=HistoricalAnalogState.INSUFFICIENT,
                    sample_count=0,
                    reason_code="INSUFFICIENT_SAMPLES",
                    reason="样本不足，暂不展示区间。",  # noqa: RUF001
                    selection_as_of=base.as_of,
                    selection_semantics_hash="a" * 64,
                ),
                completeness_percent=100,
            ).model_dump(mode="json"),
            "plain_language_update": PlainLanguageUpdate(
                checked_count=5,
                succeeded_count=5,
                no_change_count=0,
                unavailable_count=0,
                last_success_at=base.as_of,
                summary="本次更新已完成。",
            ).model_dump(mode="json"),
            "flash_interpretation": ModelInterpretation(
                title="规则解读",
                summary="当前使用确定性规则解释。",
                origin=InterpretationOrigin.RULES,
                status=InterpretationStatus.FALLBACK,
            ).model_dump(mode="json"),
            "pro_interpretation": None,
        }
    )

    projected = project_premarket_briefing_v1_2(value)
    assert projected == base

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router, prefix="/api/v1")

    async def briefing_loader() -> PremarketBriefingViewV1_3:
        return value

    app.dependency_overrides[load_latest_briefing] = briefing_loader
    client = TestClient(app)
    v1_3_response = client.get(
        "/api/v1/briefings/premarket/latest",
        headers={"Accept": R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3},
    )
    v1_2_response = client.get(
        "/api/v1/briefings/premarket/latest",
        headers={"Accept": "application/vnd.stockassistant.r1-source-provenance.v1.2+json"},
    )
    legacy_response = client.get("/api/v1/briefings/premarket/latest")

    assert v1_3_response.status_code == 200
    assert v1_3_response.headers["content-type"] == R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3
    assert v1_3_response.json()["data"]["schema_version"] == "1.3"
    assert v1_2_response.status_code == 200
    assert v1_2_response.json()["data"] == base.model_dump(mode="json")
    assert legacy_response.status_code == 200
    assert legacy_response.json()["data"]["schema_version"] == "1.0"


@pytest.mark.contract
def test_v1_2_task_schemas_are_published_and_have_a_runtime_consumer() -> None:
    registry = json.loads(Path("governance/interfaces.json").read_text(encoding="utf-8"))
    task = next(item for item in registry["tasks"] if item["key"] == "premarket_ingest")
    assert task["request_schema"] == "schema:jobs.premarket-ingest-request:v1.2"
    assert task["result_schema"] == "schema:jobs.premarket-ingest-result:v1.2"
    assert task["backward_compatible_request_schemas"] == [
        "schema:jobs.premarket-ingest-request:v1.0",
        "schema:jobs.premarket-ingest-request:v1.1",
    ]
    assert task["backward_compatible_result_schemas"] == [
        "schema:jobs.premarket-ingest-result:v1.0",
        "schema:jobs.premarket-ingest-result:v1.1",
    ]
    schemas = {item["id"]: item for item in registry["schemas"]}
    for schema_id in (
        "schema:jobs.premarket-ingest-request:v1.2",
        "schema:jobs.premarket-ingest-result:v1.2",
    ):
        assert schemas[schema_id]["status"] == "published"
        assert schemas[schema_id]["runtime_consumer_status"] == "implemented"
        assert schemas[schema_id]["runtime_consumer"] == "apps.worker.tasks.premarket_ingest"
