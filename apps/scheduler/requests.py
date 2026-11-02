"""Typed request factory for the fixture-driven R1 scheduler."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.ingestion import SourceRuntimeMode, SourceRuntimeModeV1_2
from stock_assistant.contracts.portfolio import ActiveTargetRevisionRef
from stock_assistant.contracts.premarket_jobs import (
    PREMARKET_BRIEF_JOB_NAME,
    PREMARKET_INGEST_JOB_NAME,
    PremarketBriefJobPayload,
    PremarketBriefJobRequest,
    PremarketIngestJobPayload,
    PremarketIngestJobPayloadV1_2,
    PremarketIngestJobRequest,
    PremarketIngestJobRequestV1_2,
)
from stock_assistant.contracts.sectors import TradingDay, TradingDayStatus
from stock_assistant.modules.ingestion.r1_fixture import (
    CHOICE_SOURCE_ID,
    CNINFO_SOURCE_ID,
    FIXTURE_ADAPTER_VERSION,
    FIXTURE_RIGHTS_HASH,
    FIXTURE_RULESET_VERSION,
)
from stock_assistant.modules.ingestion.r1_sources import (
    DEMO_REPLAY_ADAPTER_VERSION,
    DEMO_REPLAY_RIGHTS_HASH,
    DEMO_REPLAY_SOURCE_IDS,
    R1_CAPABILITIES,
)
from stock_assistant.modules.portfolio.active_target import R1_INSTRUMENT, R1_WATCHLIST_ID
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_ADAPTER_VERSION,
    FREE_SOURCE_CATALOG,
    FREE_SOURCE_RIGHTS_HASH,
    active_free_source_definitions,
)

_REQUEST_NAMESPACE = UUID("99c24f3c-f8e0-5ba3-8766-e1a19d2bf30a")
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def fixture_premarket_window(run_at: datetime) -> tuple[datetime, datetime]:
    """Return the synthetic weekday window ending at 08:55 Asia/Shanghai."""

    if run_at.tzinfo is None:
        raise ValueError("run_at must be timezone-aware")
    local_day = run_at.astimezone(_SHANGHAI).date()
    prior_day = _previous_weekday(local_day)
    window_start = datetime.combine(prior_day, time(15, 0), _SHANGHAI)
    window_end = datetime.combine(local_day, time(8, 55), _SHANGHAI)
    return window_start, window_end


def trading_premarket_window(
    run_at: datetime,
    trading_days: tuple[TradingDay, ...],
) -> tuple[datetime, datetime]:
    """Resolve the complete window from an as-of-safe XSHG calendar result."""

    if run_at.tzinfo is None or run_at.utcoffset() is None:
        raise ValueError("run_at must be timezone-aware")
    local_day = run_at.astimezone(_SHANGHAI).date()
    open_dates = sorted(
        item.trading_date
        for item in trading_days
        if item.exchange == "XSHG"
        and item.status in {TradingDayStatus.OPEN, TradingDayStatus.PARTIAL}
    )
    if local_day not in open_dates:
        raise ValueError("run_at is not an XSHG trading day")
    local_run_at = run_at.astimezone(_SHANGHAI)
    current_cutoff = datetime.combine(local_day, time(8, 55), _SHANGHAI)
    window_end_date = local_day
    if local_run_at < current_cutoff:
        completed = [value for value in open_dates if value < local_day]
        if not completed:
            raise ValueError("calendar result does not contain a complete XSHG window")
        window_end_date = completed[-1]
    previous = [value for value in open_dates if value < window_end_date]
    if not previous:
        raise ValueError("calendar result does not contain the prior XSHG trading day")
    return (
        datetime.combine(previous[-1], time(15, 0), _SHANGHAI),
        datetime.combine(window_end_date, time(8, 55), _SHANGHAI),
    )


def _resolve_window(
    run_at: datetime,
    window: tuple[datetime, datetime] | None,
) -> tuple[datetime, datetime]:
    resolved = fixture_premarket_window(run_at) if window is None else window
    if any(value.tzinfo is None or value.utcoffset() is None for value in resolved):
        raise ValueError("premarket window must be timezone-aware")
    if resolved[1] <= resolved[0]:
        raise ValueError("premarket window must be ordered")
    return resolved


def _target(window_start: datetime) -> ActiveTargetRevisionRef:
    return ActiveTargetRevisionRef(
        schema_version="1.0",
        watchlist_id=R1_WATCHLIST_ID,
        watchlist_revision=1,
        instrument=R1_INSTRUMENT,
        activated_at=window_start,
    )


def _context(*, key: str, run_at: datetime, as_of: datetime) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        request_id=uuid5(_REQUEST_NAMESPACE, f"request:{key}"),
        correlation_id=uuid5(_REQUEST_NAMESPACE, f"correlation:{as_of.isoformat()}"),
        actor_id="local-fixture-scheduler",
        workspace_id="default",
        as_of=as_of,
        requested_at=run_at,
        idempotency_key=f"r1-fixture-{key}",
    )


def build_fixture_ingest_requests(run_at: datetime) -> tuple[PremarketIngestJobRequest, ...]:
    """Build one immutable request for each frozen R1 source identity."""

    window_start, window_end = fixture_premarket_window(run_at)
    target = _target(window_start)
    requests: list[PremarketIngestJobRequest] = []
    for source_id in (CNINFO_SOURCE_ID, CHOICE_SOURCE_ID):
        key = f"ingest-{source_id}-{window_end.date().isoformat()}"
        requests.append(
            PremarketIngestJobRequest(
                schema_version="1.0",
                job_id=uuid5(_REQUEST_NAMESPACE, f"job:{key}"),
                job_name=PREMARKET_INGEST_JOB_NAME,
                context=_context(key=key, run_at=run_at, as_of=window_end),
                payload=PremarketIngestJobPayload(
                    schema_version="1.0",
                    active_target=target,
                    window_start=window_start,
                    window_end=window_end,
                    source_id=source_id,
                    source_adapter_version=FIXTURE_ADAPTER_VERSION,
                    rights_snapshot_hash=FIXTURE_RIGHTS_HASH,
                    ruleset_version=FIXTURE_RULESET_VERSION,
                ),
                queued_at=run_at,
                scheduled_for=window_end,
                attempt=1,
                max_attempts=3,
            )
        )
    return tuple(requests)


def build_fixture_brief_request(run_at: datetime) -> PremarketBriefJobRequest:
    """Build the deterministic briefing request for the same complete window."""

    window_start, window_end = fixture_premarket_window(run_at)
    key = f"brief-{window_end.date().isoformat()}"
    return PremarketBriefJobRequest(
        schema_version="1.0",
        job_id=uuid5(_REQUEST_NAMESPACE, f"job:{key}"),
        job_name=PREMARKET_BRIEF_JOB_NAME,
        context=_context(key=key, run_at=run_at, as_of=window_end),
        payload=PremarketBriefJobPayload(
            schema_version="1.0",
            active_target=_target(window_start),
            window_start=window_start,
            window_end=window_end,
            source_ids=[CNINFO_SOURCE_ID, CHOICE_SOURCE_ID],
            ruleset_version=FIXTURE_RULESET_VERSION,
        ),
        queued_at=run_at,
        scheduled_for=run_at,
        attempt=1,
        max_attempts=3,
    )


def build_demo_replay_ingest_requests(
    run_at: datetime,
    *,
    replay_session_id: str | None = None,
) -> tuple[PremarketIngestJobRequestV1_2, ...]:
    """Build five isolated deterministic replay capability requests."""

    routes = tuple(
        (DEMO_REPLAY_SOURCE_IDS[capability], capability) for capability in R1_CAPABILITIES
    )
    return _build_v1_2_ingest_requests(
        run_at=run_at,
        runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
        routes=routes,
        adapter_version=DEMO_REPLAY_ADAPTER_VERSION,
        rights_snapshot_hash=DEMO_REPLAY_RIGHTS_HASH,
        replay_session_id=replay_session_id,
    )


def build_dev_live_ingest_requests(
    run_at: datetime,
    *,
    window: tuple[datetime, datetime] | None = None,
    source_ids: tuple[str, ...] | None = None,
) -> tuple[PremarketIngestJobRequestV1_2, ...]:
    """Build one request per fixed free provider/capability route."""

    active = active_free_source_definitions(FREE_SOURCE_CATALOG)
    if source_ids is not None:
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("DEV_LIVE source_ids must be non-empty and unique")
        active_ids = {item.source_id for item in active}
        unknown = set(source_ids) - active_ids
        if unknown:
            raise ValueError("DEV_LIVE source_ids must identify active sources")
        selected_ids = set(source_ids)
        active = tuple(item for item in active if item.source_id in selected_ids)
    return _build_v1_2_ingest_requests(
        run_at=run_at,
        runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        routes=tuple((item.source_id, item.capability) for item in active),
        adapter_version=FREE_SOURCE_ADAPTER_VERSION,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        replay_session_id=None,
        window=window,
    )


def _build_v1_2_ingest_requests(
    *,
    run_at: datetime,
    runtime_mode: SourceRuntimeModeV1_2,
    routes: tuple[tuple[str, str], ...],
    adapter_version: str,
    rights_snapshot_hash: str,
    replay_session_id: str | None,
    window: tuple[datetime, datetime] | None = None,
) -> tuple[PremarketIngestJobRequestV1_2, ...]:
    window_start, window_end = _resolve_window(run_at, window)
    target = _target(window_start)
    requests: list[PremarketIngestJobRequestV1_2] = []
    for source_id, capability in routes:
        session_key = replay_session_id or "single"
        key = (
            f"ingest-{runtime_mode.value}-{source_id}-{capability}-"
            f"{window_end.date().isoformat()}-{session_key}"
        )
        requests.append(
            PremarketIngestJobRequestV1_2(
                schema_version="1.2",
                job_id=uuid5(_REQUEST_NAMESPACE, f"job:{key}"),
                job_name=PREMARKET_INGEST_JOB_NAME,
                context=_context(key=key, run_at=run_at, as_of=window_end).model_copy(
                    update={
                        "actor_id": f"local-{runtime_mode.value}-scheduler",
                        "idempotency_key": f"r1-{runtime_mode.value}-{key}",
                    }
                ),
                payload=PremarketIngestJobPayloadV1_2(
                    schema_version="1.2",
                    active_target=target,
                    window_start=window_start,
                    window_end=window_end,
                    source_id=source_id,
                    source_adapter_version=adapter_version,
                    rights_snapshot_hash=rights_snapshot_hash,
                    ruleset_version=FIXTURE_RULESET_VERSION,
                    capability=capability,
                    runtime_mode=runtime_mode,
                    replay_session_id=replay_session_id,
                    max_records=(1000 if runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE else 100),
                ),
                queued_at=run_at,
                scheduled_for=window_end,
                attempt=1,
                max_attempts=3,
            )
        )
    return tuple(requests)


def build_runtime_brief_request(
    run_at: datetime,
    *,
    runtime_mode: SourceRuntimeMode | SourceRuntimeModeV1_2,
    window: tuple[datetime, datetime] | None = None,
) -> PremarketBriefJobRequest:
    """Build the unchanged brief job intent for one explicit source mode."""

    if runtime_mode.value == SourceRuntimeMode.DEMO_REPLAY.value:
        source_ids = DEMO_REPLAY_SOURCE_IDS
        ordered_source_ids = [source_ids[item] for item in R1_CAPABILITIES]
    elif runtime_mode.value == SourceRuntimeMode.DEV_LIVE.value:
        ordered_source_ids = [
            item.source_id for item in active_free_source_definitions(FREE_SOURCE_CATALOG)
        ]
    else:
        raise ValueError("AUTHORIZED runtime has no R1 development briefing request")
    window_start, window_end = _resolve_window(run_at, window)
    key = f"brief-{runtime_mode.value}-{window_end.date().isoformat()}"
    return PremarketBriefJobRequest(
        schema_version="1.0",
        job_id=uuid5(_REQUEST_NAMESPACE, f"job:{key}"),
        job_name=PREMARKET_BRIEF_JOB_NAME,
        context=_context(key=key, run_at=run_at, as_of=window_end),
        payload=PremarketBriefJobPayload(
            schema_version="1.0",
            active_target=_target(window_start),
            window_start=window_start,
            window_end=window_end,
            source_ids=ordered_source_ids,
            ruleset_version=FIXTURE_RULESET_VERSION,
        ),
        queued_at=run_at,
        scheduled_for=run_at,
        attempt=1,
        max_attempts=3,
    )


__all__ = [
    "build_demo_replay_ingest_requests",
    "build_dev_live_ingest_requests",
    "build_fixture_brief_request",
    "build_fixture_ingest_requests",
    "build_runtime_brief_request",
    "fixture_premarket_window",
    "trading_premarket_window",
]
