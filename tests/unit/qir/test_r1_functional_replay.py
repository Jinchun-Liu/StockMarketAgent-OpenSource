from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from apps.scheduler.requests import build_demo_replay_ingest_requests

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.modules.ingestion.r1_sources import (
    R1_CAPABILITIES,
    R1_FUNCTIONAL_REPLAY_SESSIONS,
    ReplayScenario,
    build_demo_replay_batch_v1_2,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _batches(session_id: str):
    run_at = datetime.fromisoformat(session_id).replace(
        hour=9,
        minute=5,
        tzinfo=_SHANGHAI,
    )
    requests = build_demo_replay_ingest_requests(
        run_at,
        replay_session_id=session_id,
    )
    return tuple(
        build_demo_replay_batch_v1_2(
            capability=request.payload.capability,
            scenario=ReplayScenario.SUCCESS,
            window_start=request.payload.window_start,
            window_end=request.payload.window_end,
            instrument_id=request.payload.active_target.instrument.instrument_id,
            replay_session_id=session_id,
        )
        for request in requests
    )


def test_five_session_replay_has_one_terminal_per_capability_and_fixed_outcomes() -> None:
    by_session = {session_id: _batches(session_id) for session_id in R1_FUNCTIONAL_REPLAY_SESSIONS}

    assert all(
        tuple(batch.capability for batch in batches) == R1_CAPABILITIES
        for batches in by_session.values()
    )
    assert all(len(batch.coverages) == 1 for batches in by_session.values() for batch in batches)

    first = by_session["2026-07-27"]
    assert all(batch.coverages[0].status is SourceCoverageStatus.OK for batch in first)
    assert [event.change_type for batch in first for event in batch.events] == [
        BriefingChangeType.NEW,
    ] * 5

    second = by_session["2026-07-28"]
    assert [event.change_type for batch in second for event in batch.events] == [
        BriefingChangeType.UPDATE
    ]

    third = by_session["2026-07-29"]
    assert [event.change_type for batch in third for event in batch.events] == [
        BriefingChangeType.CONTRADICTION
    ]

    fourth = by_session["2026-07-30"]
    assert [event.change_type for batch in fourth for event in batch.events] == [
        BriefingChangeType.CORRECTION
    ]

    fifth = by_session["2026-07-31"]
    macro = next(batch for batch in fifth if batch.capability == "macro.flashes")
    assert macro.coverages[0].status is SourceCoverageStatus.UNAVAILABLE
    assert macro.coverages[0].gap_reason == "R1_REPLAY_SOURCE_FAILURE"
    assert macro.next_cursor is None


def test_functional_replay_rejects_unknown_or_mismatched_session() -> None:
    request = build_demo_replay_ingest_requests(
        datetime(2026, 7, 31, 9, 5, tzinfo=_SHANGHAI),
        replay_session_id="2026-07-31",
    )[0]

    with pytest.raises(ApplicationError, match="R1_REPLAY_SESSION_NOT_ALLOWED"):
        build_demo_replay_batch_v1_2(
            capability=request.payload.capability,
            scenario=ReplayScenario.SUCCESS,
            window_start=request.payload.window_start,
            window_end=request.payload.window_end,
            instrument_id="XSHG:603986",
            replay_session_id="2026-08-01",
        )

    with pytest.raises(ApplicationError, match="R1_REPLAY_SESSION_WINDOW_MISMATCH"):
        build_demo_replay_batch_v1_2(
            capability=request.payload.capability,
            scenario=ReplayScenario.SUCCESS,
            window_start=request.payload.window_start,
            window_end=request.payload.window_end,
            instrument_id="XSHG:603986",
            replay_session_id="2026-07-30",
        )
