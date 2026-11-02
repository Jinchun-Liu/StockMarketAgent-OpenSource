from datetime import UTC, datetime, timedelta
from uuid import UUID

from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import FetchRequest, SourceRuntimeMode
from stock_assistant.contracts.premarket_views import (
    SourceAdvanceState,
    SourceCoverageStatus,
)
from stock_assistant.modules.ingestion.r1_sources import (
    source_batch_from_fetch,
    terminal_gap_batch,
)
from stock_assistant.platform.sources.r1_dev_live import (
    DEV_LIVE_LICENSE_ID,
    DEV_LIVE_RIGHTS_HASH,
    DEV_LIVE_SOURCE_IDS,
    AkshareDevLiveAdapter,
    dev_live_macro_gap,
)

NOW = datetime(2026, 7, 31, 0, 55, tzinfo=UTC)


def _request(capability: str) -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            request_id=UUID(int=1),
            correlation_id=UUID(int=2),
            as_of=NOW,
            requested_at=NOW,
        ),
        source_id=DEV_LIVE_SOURCE_IDS[capability],
        capability=capability,
        rights_snapshot_hash=DEV_LIVE_RIGHTS_HASH,
        since=NOW - timedelta(days=30),
        until=NOW,
        instrument_ids=["XSHG:603986"],
    )


def test_dev_live_fetch_batch_normalizes_to_deterministic_r1_event() -> None:
    adapter = AkshareDevLiveAdapter("market.snapshot")
    fetch_batch = adapter.normalize(
        _request("market.snapshot"),
        [{"日期": "2026-07-30", "开盘": 91, "收盘": 93, "最高": 94, "最低": 90}],
    )

    first = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability="market.snapshot",
        provenance=adapter.provenance,
        license_id=DEV_LIVE_LICENSE_ID,
    )
    second = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability="market.snapshot",
        provenance=adapter.provenance,
        license_id=DEV_LIVE_LICENSE_ID,
    )

    assert first.runtime_mode is SourceRuntimeMode.DEV_LIVE
    assert first.coverages[0].status is SourceCoverageStatus.OK
    assert first.coverages[0].progress.advance_state is SourceAdvanceState.ADVANCED
    assert first.events[0].event_id == second.events[0].event_id
    assert first.events[0].evidence[0].evidence_id == second.events[0].evidence[0].evidence_id
    assert first.events[0].evidence[0].provenance == adapter.provenance


def test_macro_gap_is_terminal_and_never_advances() -> None:
    reason, status, provenance = dev_live_macro_gap()

    batch = terminal_gap_batch(
        source_id=DEV_LIVE_SOURCE_IDS["macro.flashes"],
        capability="macro.flashes",
        runtime_mode=SourceRuntimeMode.DEV_LIVE,
        provenance=provenance,
        rights_snapshot_hash=DEV_LIVE_RIGHTS_HASH,
        license_id=DEV_LIVE_LICENSE_ID,
        observed_at=NOW,
        status=status,
        reason_code=reason,
    )

    assert batch.events == ()
    assert batch.coverages[0].status is SourceCoverageStatus.NOT_QUALIFIED
    assert batch.coverages[0].progress.advance_state is SourceAdvanceState.NOT_ADVANCED
    assert batch.coverages[0].progress.business_watermark is None


def test_empty_fetch_advances_successful_source_progress() -> None:
    adapter = AkshareDevLiveAdapter("market.snapshot")
    request = _request("market.snapshot")
    execution_time = NOW + timedelta(minutes=10)
    fetch_batch = adapter.normalize(
        request,
        [],
        execution_started_at=execution_time,
        execution_completed_at=execution_time,
    )

    batch = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability="market.snapshot",
        provenance=adapter.provenance,
        license_id=DEV_LIVE_LICENSE_ID,
    )

    coverage = batch.coverages[0]
    assert batch.events == ()
    assert coverage.status is SourceCoverageStatus.OK
    assert coverage.gap_reason is None
    assert coverage.progress.advance_state is SourceAdvanceState.ADVANCED
    assert coverage.progress.business_watermark == fetch_batch.source_watermark == request.until
    assert coverage.last_success_at == fetch_batch.completed_at == execution_time
    assert coverage.observed_at == fetch_batch.completed_at == execution_time
    assert batch.next_cursor == fetch_batch.next_cursor == request.until.isoformat()
