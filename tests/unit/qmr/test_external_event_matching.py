"""Deterministic matching and report publication tests for unified R2."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from stock_assistant.contracts.global_events import (
    GlobalEventCatalogSlice,
    GlobalEventCatalogStatus,
    ResearchExposureDirectness,
    ResearchExternalImpactDirection,
)
from stock_assistant.contracts.research import (
    ClaimType,
    ResearchAnalysisConfig,
    ResearchProgress,
    ResearchSectionKind,
    ResearchSessionStatus,
    ResearchSessionView,
    ResearchSourceMode,
    ResearchStage,
)
from stock_assistant.modules.research import reporting
from stock_assistant.modules.research.external_events import (
    enrich_packet_with_global_events,
    match_external_events,
)
from stock_assistant.modules.research.reporting import (
    ResearchReportGenerator,
)
from stock_assistant.platform.llm.synthetic_research import SyntheticResearchLLMProvider
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet
from stock_assistant.platform.research.synthetic_global_events import synthetic_global_events

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_report_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting, "utc_now", lambda: NOW)


def _packet():
    return synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=NOW,
    )


def _catalog(*, events=None, status=GlobalEventCatalogStatus.READY_WITH_MATCHES):
    selected = list(synthetic_global_events()) if events is None else list(events)
    return GlobalEventCatalogSlice(
        schema_version="1.0",
        status=status,
        events=selected,
        watermark=NOW - timedelta(hours=1),
        last_success_at=NOW - timedelta(minutes=10),
    )


def _session():
    return ResearchSessionView(
        schema_version="1.0",
        session_id=UUID("00000000-0000-0000-0000-000000002001"),
        job_id=UUID("00000000-0000-0000-0000-000000002002"),
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=NOW,
        analysis_config=ResearchAnalysisConfig(schema_version="1.0"),
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        status=ResearchSessionStatus.RUNNING,
        progress=ResearchProgress(
            stage=ResearchStage.VALIDATING_REPORT,
            message="验证国际事件影响路径。",
            latest_progress_at=NOW,
        ),
        created_at=NOW - timedelta(minutes=2),
        updated_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )


def test_matcher_uses_structured_directness_and_rejects_text_co_mention() -> None:
    packet = _packet()
    base = synthetic_global_events()[2]
    official = base.model_copy(update={"security_ids": [packet.instrument_id]})
    company = base.model_copy(update={"company_ids": [packet.exposure_profile.company_id]})
    one_hop = match_external_events(packet, [base])
    official_match = match_external_events(packet, [official])
    company_match = match_external_events(packet, [company])

    assert one_hop[0].directness is ResearchExposureDirectness.ONE_HOP_EVIDENCE
    assert one_hop[0].hop_count == 1
    assert official_match[0].directness is ResearchExposureDirectness.OFFICIAL_DIRECT_NAMING
    assert company_match[0].directness is ResearchExposureDirectness.COMPANY_DISCLOSED

    unrelated = synthetic_global_events()[-1]
    mentioned_only = unrelated.model_copy(
        update={
            "evidence": [
                unrelated.evidence[0].model_copy(
                    update={"title": "合成公告正文提到兆易创新但无结构化暴露证据"}
                )
            ]
        }
    )
    assert match_external_events(packet, [mentioned_only]) == []


def test_matcher_excludes_future_revision_and_marks_industry_background() -> None:
    packet = _packet()
    event = synthetic_global_events()[2]
    profile = packet.exposure_profile.model_copy(update={"edges": []})
    background_packet = packet.model_copy(update={"exposure_profile": profile})
    background = match_external_events(background_packet, [event])
    future = event.model_copy(update={"revision_known_at": NOW + timedelta(seconds=1)})

    assert background[0].directness is ResearchExposureDirectness.INDUSTRY_BACKGROUND
    assert background[0].hop_count == 0
    assert match_external_events(packet, [future]) == []


def test_matcher_accepts_evidence_backed_one_hop_region_exposure() -> None:
    packet = _packet()
    event = synthetic_global_events()[2]
    region_edge = packet.exposure_profile.edges[0].model_copy(
        update={"to_entity_id": "region:china"}
    )
    profile = packet.exposure_profile.model_copy(update={"edges": [region_edge]})
    region_packet = packet.model_copy(update={"exposure_profile": profile})
    region_only_event = event.model_copy(
        update={
            "facts": [fact for fact in event.facts if fact.name != "industry_id"],
            "region_ids": ["region:china"],
        }
    )

    matches = match_external_events(region_packet, [region_only_event])

    assert len(matches) == 1
    assert matches[0].directness is ResearchExposureDirectness.ONE_HOP_EVIDENCE
    assert matches[0].hop_count == 1
    assert matches[0].path[1] == "region:china"
    assert region_edge.evidence_ids[0] in matches[0].supporting_evidence_ids


def test_matcher_rejects_unknown_channel_and_downgrades_unknown_direction() -> None:
    packet = _packet()
    event = synthetic_global_events()[2]
    without_profile = packet.model_copy(update={"exposure_profile": None})
    unknown_channel = event.model_copy(
        update={
            "facts": [
                fact.model_copy(update={"value": "not_a_channel"})
                if fact.name == "impact_channel"
                else fact
                for fact in event.facts
            ]
        }
    )
    unknown_direction = event.model_copy(
        update={
            "facts": [
                fact.model_copy(update={"value": "not_a_direction"})
                if fact.name == "impact_direction"
                else fact
                for fact in event.facts
            ]
        }
    )

    assert match_external_events(without_profile, [event]) == []
    assert match_external_events(packet, [unknown_channel]) == []
    matches = match_external_events(packet, [unknown_direction])
    assert matches[0].direction is ResearchExternalImpactDirection.UNKNOWN


def test_catalog_ready_without_match_is_distinct_from_unavailable() -> None:
    packet = _packet()
    no_match = enrich_packet_with_global_events(
        packet,
        GlobalEventCatalogSlice(
            schema_version="1.0",
            status=GlobalEventCatalogStatus.READY_NO_MATCHES,
            watermark=NOW - timedelta(hours=1),
            last_success_at=NOW - timedelta(minutes=10),
        ),
    )
    unavailable = enrich_packet_with_global_events(
        packet,
        GlobalEventCatalogSlice(
            schema_version="1.0",
            status=GlobalEventCatalogStatus.UNAVAILABLE,
            reason_code="EVENT_CATALOG_NOT_REFRESHED",
        ),
    )

    assert no_match.coverage_gaps[-1].reason_code == "NO_RELEVANT_GLOBAL_EVENTS"
    assert unavailable.coverage_gaps[-1].reason_code == "EVENT_CATALOG_UNAVAILABLE"
    assert no_match.external_event_catalog_status is GlobalEventCatalogStatus.READY_NO_MATCHES
    assert unavailable.external_event_catalog_status is GlobalEventCatalogStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_report_separates_event_facts_from_conditional_stock_impact() -> None:
    packet = enrich_packet_with_global_events(_packet(), _catalog())
    report = await ResearchReportGenerator(SyntheticResearchLLMProvider()).generate(
        session=_session(),
        packet=packet,
    )
    event_facts = [
        item
        for item in report.claims
        if item.claim_type is ClaimType.FACT
        and item.section is ResearchSectionKind.MAJOR_EVENTS_AND_MACRO
        and item.statement.startswith("国际官方事件")
    ]
    impact_claims = [
        item
        for item in report.claims
        if item.claim_type is ClaimType.SYSTEM_INFERENCE
        and item.section is ResearchSectionKind.MAJOR_EVENTS_AND_MACRO
    ]

    assert event_facts
    assert impact_claims
    assert all(item.calculation is not None for item in impact_claims)
    assert any(item.counter_evidence for item in impact_claims)
    exposure_fact = next(
        fact for fact in packet.facts if fact.model_dump()["key"] == "macro.exposure"
    )
    assert "半导体" in exposure_fact.statement
    assert packet.exposure_profile is not None
    assert packet.exposure_profile.edges[0].evidence_ids == [exposure_fact.evidence_id]
    assert all(
        exposure_fact.evidence_id in {reference.evidence_id for reference in claim.evidence}
        for claim in impact_claims
    )
    names = {
        calculation_input.name
        for item in impact_claims
        if item.calculation is not None
        for calculation_input in item.calculation.inputs
    }
    assert names == {
        "event_revision",
        "exposure_directness",
        "impact_channel",
        "impact_direction",
        "evidence_path",
    }


@pytest.mark.asyncio
async def test_report_preserves_distinct_event_support_and_counter_from_same_source() -> None:
    events = synthetic_global_events()
    supporting, counter = events[2:4]
    disguised_counter = counter.model_copy(
        update={
            "evidence": [
                counter.evidence[0].model_copy(
                    update={"source_id": supporting.evidence[0].source_id}
                )
            ]
        }
    )
    packet = enrich_packet_with_global_events(
        _packet(),
        _catalog(events=[supporting, disguised_counter]),
    )

    report = await ResearchReportGenerator(SyntheticResearchLLMProvider()).generate(
        session=_session(),
        packet=packet,
    )
    impacts = [claim for claim in report.claims if claim.calculation is not None]
    assert impacts
    assert any(claim.counter_evidence for claim in impacts)
    assert all(claim.calculation.cutoff == packet.as_of for claim in impacts)
