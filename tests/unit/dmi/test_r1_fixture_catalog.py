from datetime import UTC, datetime

from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.premarket_views import SourceDataMode
from stock_assistant.modules.ingestion.r1_fixture import (
    CHOICE_SOURCE_ID,
    CNINFO_SOURCE_ID,
    build_fixture_batches,
    fixture_manifest_hash,
)


def test_fixture_catalog_covers_two_sources_five_sections_and_revision_history() -> None:
    batches = build_fixture_batches(
        window_start=datetime(2026, 7, 28, 7, tzinfo=UTC),
        window_end=datetime(2026, 7, 29, 0, 55, tzinfo=UTC),
        instrument_id="XSHG:603986",
    )

    assert [batch.source_id for batch in batches] == [CNINFO_SOURCE_ID, CHOICE_SOURCE_ID]
    capabilities = {coverage.capability for batch in batches for coverage in batch.coverages}
    assert capabilities == {
        "statutory.announcements",
        "financial.changes",
        "market.snapshot",
        "company_industry.news",
        "macro.flashes",
    }
    assert all(
        coverage.progress.data_mode is SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY
        for batch in batches
        for coverage in batch.coverages
    )

    for batch in batches:
        evidence_sources = {
            evidence.source_id for event in batch.events for evidence in event.evidence
        }
        assert evidence_sources == {batch.source_id}
        assert all(
            set(fact.evidence_ids).issubset({evidence.evidence_id for evidence in event.evidence})
            for event in batch.events
            for fact in event.facts
        )

    events = [event for batch in batches for event in batch.events]
    assert {event.instrument_ids[0] for event in events} == {"XSHG:603986"}
    assert any(event.revision == 2 for event in events)
    assert not any(event.change_type is BriefingChangeType.CONTRADICTION for event in events)
    market_sources = {
        event.evidence[0].source_id
        for event in events
        if event.event_type == "source_observation.market_close"
    }
    assert market_sources == {
        CNINFO_SOURCE_ID,
        CHOICE_SOURCE_ID,
    }


def test_fixture_manifest_is_stable_and_source_specific() -> None:
    window = {
        "window_start": datetime(2026, 7, 28, 7, tzinfo=UTC),
        "window_end": datetime(2026, 7, 29, 0, 55, tzinfo=UTC),
        "instrument_id": "XSHG:603986",
    }
    first = build_fixture_batches(**window)
    second = build_fixture_batches(**window)

    assert [fixture_manifest_hash(item) for item in first] == [
        fixture_manifest_hash(item) for item in second
    ]
    assert fixture_manifest_hash(first[0]) != fixture_manifest_hash(first[1])
