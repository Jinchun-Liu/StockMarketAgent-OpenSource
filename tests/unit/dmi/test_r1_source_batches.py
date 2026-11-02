import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.ingestion import SourceRuntimeMode, SourceRuntimeModeV1_2
from stock_assistant.contracts.premarket_views import (
    SourceAdvanceState,
    SourceCoverageStatus,
)
from stock_assistant.modules.ingestion.r1_sources import (
    DEMO_REPLAY_SOURCE_IDS,
    R1_CAPABILITIES,
    ReplayScenario,
    build_demo_replay_batch,
    build_demo_replay_batch_v1_2,
    build_demo_replay_batches,
    r1_source_manifest_hash,
)
from stock_assistant.platform.db.r1_premarket import _register_r1_source

WINDOW_START = datetime(2026, 7, 30, 7, tzinfo=UTC)
WINDOW_END = datetime(2026, 7, 31, 0, 55, tzinfo=UTC)


def _batch(capability: str, scenario: ReplayScenario):
    return build_demo_replay_batch(
        capability=capability,
        scenario=scenario,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        instrument_id="XSHG:603986",
    )


def test_demo_replay_has_five_independent_capability_streams_and_provenance() -> None:
    batches = build_demo_replay_batches(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        instrument_id="XSHG:603986",
    )

    assert tuple(item.capability for item in batches) == R1_CAPABILITIES
    assert {item.source_id for item in batches} == set(DEMO_REPLAY_SOURCE_IDS.values())
    assert len({item.stream for item in batches}) == 5
    assert all(item.runtime_mode is SourceRuntimeMode.DEMO_REPLAY for item in batches)
    assert all(item.provenance.source_identifier == item.source_id for item in batches)
    assert all(item.coverages[0].provenance == item.provenance for item in batches)
    assert all(
        evidence.provenance == batch.provenance and evidence.event_time <= evidence.first_seen_at
        for batch in batches
        for event in batch.events
        for evidence in event.evidence
    )


def test_demo_replay_exercises_update_and_contradiction_without_cross_capability_evidence() -> None:
    batches = build_demo_replay_batches(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        instrument_id="XSHG:603986",
    )
    events = [event for batch in batches for event in batch.events]

    assert {event.change_type for event in events} >= {
        BriefingChangeType.UPDATE,
        BriefingChangeType.CORRECTION,
        BriefingChangeType.CONTRADICTION,
    }
    for batch in batches:
        assert all(
            {evidence.source_id for evidence in event.evidence} == {batch.source_id}
            for event in batch.events
        )


@pytest.mark.parametrize(
    ("capability", "scenario", "status", "reason"),
    (
        (
            "statutory.announcements",
            ReplayScenario.SOURCE_FAILURE,
            SourceCoverageStatus.UNAVAILABLE,
            "R1_REPLAY_SOURCE_FAILURE",
        ),
        (
            "financial.changes",
            ReplayScenario.RIGHTS_BLOCKED,
            SourceCoverageStatus.RIGHTS_BLOCKED,
            "R1_REPLAY_RIGHTS_BLOCKED",
        ),
        (
            "company_industry.news",
            ReplayScenario.MEDIA_UNCONFIRMED,
            SourceCoverageStatus.NOT_QUALIFIED,
            "R1_MEDIA_UNCONFIRMED",
        ),
        (
            "macro.flashes",
            ReplayScenario.MACRO_INSUFFICIENT,
            SourceCoverageStatus.NOT_QUALIFIED,
            "R1_MACRO_RELATION_INSUFFICIENT",
        ),
    ),
)
def test_demo_replay_gap_scenarios_are_explicit_and_do_not_advance(
    capability: str,
    scenario: ReplayScenario,
    status: SourceCoverageStatus,
    reason: str,
) -> None:
    batch = _batch(capability, scenario)
    coverage = batch.coverages[0]

    assert coverage.status is status
    assert coverage.gap_reason == reason
    if scenario in {ReplayScenario.SOURCE_FAILURE, ReplayScenario.RIGHTS_BLOCKED}:
        assert batch.events == ()
        assert coverage.progress.advance_state is SourceAdvanceState.NOT_ADVANCED
        assert coverage.progress.business_watermark is None


def test_no_change_is_successful_and_replay_manifest_is_deterministic() -> None:
    first = _batch("market.snapshot", ReplayScenario.NO_CHANGE)
    second = _batch("market.snapshot", ReplayScenario.NO_CHANGE)

    assert first.events == ()
    assert first.coverages[0].status is SourceCoverageStatus.OK
    assert first.coverages[0].progress.advance_state is SourceAdvanceState.ADVANCED
    assert r1_source_manifest_hash(first) == r1_source_manifest_hash(second)


def test_v1_2_replay_registration_remains_synthetic() -> None:
    class CaptureConnection:
        parameters: dict[str, object] | None = None

        async def execute(self, _statement: object, parameters: dict[str, object]) -> None:
            self.parameters = parameters

    batch = build_demo_replay_batch_v1_2(
        capability="market.snapshot",
        scenario=ReplayScenario.SUCCESS,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        instrument_id="XSHG:603986",
    )
    assert batch.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY
    connection = CaptureConnection()

    asyncio.run(_register_r1_source(cast(AsyncConnection, connection), batch))

    assert connection.parameters is not None
    rights_record = json.loads(cast(str, connection.parameters["rights_record"]))
    assert rights_record["data_mode"] == "synthetic_development_only"
