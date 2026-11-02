from datetime import UTC, datetime

import pytest
from apps.scheduler.requests import (
    build_demo_replay_ingest_requests,
    build_dev_live_ingest_requests,
    build_fixture_brief_request,
    build_fixture_ingest_requests,
    build_runtime_brief_request,
    fixture_premarket_window,
)

from stock_assistant.contracts.ingestion import SourceRuntimeModeV1_2
from stock_assistant.modules.ingestion.r1_fixture import (
    CHOICE_SOURCE_ID,
    CNINFO_SOURCE_ID,
)
from stock_assistant.modules.ingestion.r1_sources import R1_CAPABILITIES
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_CATALOG,
    active_free_source_definitions,
)


def test_scheduler_builds_complete_typed_requests_for_fixed_target() -> None:
    run_at = datetime(2026, 7, 29, 1, 5, tzinfo=UTC)
    window_start, window_end = fixture_premarket_window(run_at)
    ingest = build_fixture_ingest_requests(run_at)
    brief = build_fixture_brief_request(run_at)

    assert [item.payload.source_id for item in ingest] == [
        CNINFO_SOURCE_ID,
        CHOICE_SOURCE_ID,
    ]
    assert all(
        item.payload.active_target.instrument.instrument_id == "XSHG:603986" for item in ingest
    )
    assert all(item.payload.window_start == window_start for item in ingest)
    assert all(item.payload.window_end == window_end for item in ingest)
    assert brief.payload.active_target.instrument.instrument_id == "XSHG:603986"
    assert brief.payload.source_ids == [CNINFO_SOURCE_ID, CHOICE_SOURCE_ID]
    assert brief.payload.window_start == window_start
    assert brief.payload.window_end == window_end


def test_scheduler_requests_are_stable_for_repeated_factory_calls() -> None:
    run_at = datetime(2026, 7, 29, 1, 5, tzinfo=UTC)

    assert build_fixture_ingest_requests(run_at) == build_fixture_ingest_requests(run_at)
    assert build_fixture_brief_request(run_at) == build_fixture_brief_request(run_at)


def test_v1_2_scheduler_requests_isolate_mode_provider_and_capability() -> None:
    run_at = datetime(2026, 7, 29, 1, 5, tzinfo=UTC)

    replay = build_demo_replay_ingest_requests(run_at)
    live = build_dev_live_ingest_requests(run_at)
    active_live = active_free_source_definitions(FREE_SOURCE_CATALOG)

    assert tuple(item.payload.capability for item in replay) == R1_CAPABILITIES
    assert tuple(item.payload.capability for item in live) == tuple(
        item.capability for item in active_live
    )
    assert [item.payload.source_id for item in live] == [item.source_id for item in active_live]
    assert len(live) == 5
    assert all(item.schema_version == "1.2" for item in (*replay, *live))
    assert all(item.payload.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY for item in replay)
    assert all(item.payload.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE for item in live)
    assert {item.payload.source_id for item in replay}.isdisjoint(
        item.payload.source_id for item in live
    )
    assert build_runtime_brief_request(
        run_at,
        runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
    ).payload.source_ids == [item.payload.source_id for item in replay]
    assert build_runtime_brief_request(
        run_at,
        runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
    ).payload.source_ids == [item.payload.source_id for item in live]


def test_functional_replay_session_is_bound_to_each_request_identity() -> None:
    run_at = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)

    requests = build_demo_replay_ingest_requests(
        run_at,
        replay_session_id="2026-07-31",
    )

    assert all(item.payload.replay_session_id == "2026-07-31" for item in requests)
    assert len({item.job_id for item in requests}) == len(R1_CAPABILITIES)
    assert len({item.context.idempotency_key for item in requests}) == len(R1_CAPABILITIES)


def test_runtime_brief_request_rejects_authorized_mode() -> None:
    run_at = datetime(2026, 7, 29, 1, 5, tzinfo=UTC)

    with pytest.raises(ValueError, match="AUTHORIZED runtime"):
        build_runtime_brief_request(run_at, runtime_mode=SourceRuntimeModeV1_2.AUTHORIZED)
