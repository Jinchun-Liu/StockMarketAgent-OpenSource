from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from apps.worker import tasks
from pydantic import ValidationError

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.global_events import PolicyRefreshRequest, PolicyRefreshResult
from stock_assistant.contracts.ingestion import SourceRuntimeMode
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.portfolio import ActiveTargetRevisionRef
from stock_assistant.contracts.premarket_jobs import (
    PremarketBriefJobPayload,
    PremarketBriefJobRequest,
    PremarketBriefJobResult,
    PremarketIngestJobPayload,
    PremarketIngestJobRequest,
    PremarketIngestJobResult,
)
from stock_assistant.modules.ingestion.r1_fixture import (
    CHOICE_SOURCE_ID,
    CNINFO_SOURCE_ID,
    FIXTURE_ADAPTER_VERSION,
    FIXTURE_RIGHTS_HASH,
    FIXTURE_RULESET_VERSION,
    FixtureBatch,
)
from stock_assistant.modules.portfolio.active_target import R1_INSTRUMENT, R1_WATCHLIST_ID

NOW = datetime(2026, 7, 23, 1, 5, tzinfo=UTC)
WINDOW_START = datetime(2026, 7, 22, 7, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 7, 23, 0, 55, tzinfo=UTC)
INGEST_JOB_ID = UUID("00000000-0000-0000-0000-000000000401")
BRIEF_JOB_ID = UUID("00000000-0000-0000-0000-000000000402")


def _context() -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        request_id=UUID("00000000-0000-0000-0000-000000000403"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000404"),
        as_of=WINDOW_END,
        requested_at=NOW,
        idempotency_key="r0p-runtime-idempotency-0001",
    )


def _active_target() -> ActiveTargetRevisionRef:
    return ActiveTargetRevisionRef(
        schema_version="1.0",
        watchlist_id=R1_WATCHLIST_ID,
        watchlist_revision=1,
        instrument=R1_INSTRUMENT,
        activated_at=WINDOW_START,
    )


def _successful_ingest(
    request: PremarketIngestJobRequest,
    observed_at: datetime,
    _batch: FixtureBatch,
) -> PremarketIngestJobResult:
    return PremarketIngestJobResult(
        schema_version="1.0",
        job_id=request.job_id,
        status=JobStatus.SUCCEEDED,
        started_at=observed_at,
        completed_at=observed_at,
        attempt=request.attempt,
        result={
            "schema_version": "1.0",
            "active_target": request.payload.active_target,
            "source_id": request.payload.source_id,
            "window_start": request.payload.window_start,
            "window_end": request.payload.window_end,
            "records_fetched": 3,
            "evidence_stored": 4,
            "event_revisions_stored": 3,
            "data_manifest_hash": "b" * 64,
            "source_watermark": request.payload.window_end,
        },
    )


def _successful_brief(
    request: PremarketBriefJobRequest,
    observed_at: datetime,
) -> PremarketBriefJobResult:
    return PremarketBriefJobResult(
        schema_version="1.0",
        job_id=request.job_id,
        status=JobStatus.SUCCEEDED,
        started_at=observed_at,
        completed_at=observed_at,
        attempt=request.attempt,
        result={
            "schema_version": "1.0",
            "active_target": request.payload.active_target,
            "window_start": request.payload.window_start,
            "window_end": request.payload.window_end,
            "briefing_id": "00000000-0000-0000-0000-000000000499",
            "generated_at": observed_at,
            "event_count": 5,
            "evidence_count": 7,
            "data_manifest_hash": "c" * 64,
        },
    )


def _ingest_request() -> PremarketIngestJobRequest:
    return PremarketIngestJobRequest(
        schema_version="1.0",
        job_id=INGEST_JOB_ID,
        context=_context(),
        payload=PremarketIngestJobPayload(
            schema_version="1.0",
            active_target=_active_target(),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            source_id=CNINFO_SOURCE_ID,
            source_adapter_version=FIXTURE_ADAPTER_VERSION,
            rights_snapshot_hash=FIXTURE_RIGHTS_HASH,
            ruleset_version=FIXTURE_RULESET_VERSION,
        ),
        queued_at=NOW,
        attempt=2,
        max_attempts=3,
    )


def _brief_request() -> PremarketBriefJobRequest:
    return PremarketBriefJobRequest(
        schema_version="1.0",
        job_id=BRIEF_JOB_ID,
        context=_context(),
        payload=PremarketBriefJobPayload(
            schema_version="1.0",
            active_target=_active_target(),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            source_ids=[CNINFO_SOURCE_ID, CHOICE_SOURCE_ID],
            ruleset_version=FIXTURE_RULESET_VERSION,
        ),
        queued_at=NOW,
        attempt=3,
        max_attempts=3,
    )


def test_r1_tasks_reject_zero_argument_calls() -> None:
    with pytest.raises(TypeError):
        tasks.premarket_ingest()
    with pytest.raises(TypeError):
        tasks.premarket_brief()


def test_premarket_ingest_validates_wire_request_and_preserves_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_fixture=True,
            app_env="test",
            r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        ),
    )
    monkeypatch.setattr(tasks, "_execute_premarket_ingest", _successful_ingest)
    request = _ingest_request()
    wire_request = request.model_dump(mode="json")

    result = PremarketIngestJobResult.model_validate(tasks.premarket_ingest(wire_request))

    assert result.job_id == request.job_id
    assert result.attempt == request.attempt
    assert result.started_at == NOW
    assert result.completed_at == NOW
    assert result.status is JobStatus.SUCCEEDED
    assert result.result is not None
    assert result.result.active_target == request.payload.active_target
    assert result.result.window_start == request.payload.window_start
    assert result.result.window_end == request.payload.window_end
    assert result.result.reason_codes == []
    assert result.result.records_fetched == 3
    assert result.result.evidence_stored == 4
    assert result.result.event_revisions_stored == 3
    assert result.result.data_manifest_hash == "b" * 64
    assert result.result.next_cursor is None


def test_premarket_brief_validates_wire_request_and_preserves_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_fixture=True,
            app_env="test",
            r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        ),
    )
    monkeypatch.setattr(tasks, "_execute_premarket_brief", _successful_brief)
    request = _brief_request()
    wire_request = request.model_dump(mode="json")

    result = PremarketBriefJobResult.model_validate(tasks.premarket_brief(wire_request))

    assert result.job_id == request.job_id
    assert result.attempt == request.attempt
    assert result.started_at == NOW
    assert result.completed_at == NOW
    assert result.status is JobStatus.SUCCEEDED
    assert result.result is not None
    assert result.result.active_target == request.payload.active_target
    assert result.result.window_start == request.payload.window_start
    assert result.result.window_end == request.payload.window_end
    assert result.result.reason_codes == []
    assert result.result.event_count == 5
    assert result.result.evidence_count == 7
    assert result.result.briefing_id == UUID("00000000-0000-0000-0000-000000000499")
    assert result.result.data_manifest_hash == "c" * 64


@pytest.mark.parametrize("task", [tasks.premarket_ingest, tasks.premarket_brief])
def test_r1_tasks_reject_naive_wire_timestamps(task: object) -> None:
    request = _ingest_request() if task is tasks.premarket_ingest else _brief_request()
    wire_request = request.model_dump(mode="python")
    payload = wire_request["payload"]
    assert isinstance(payload, dict)
    payload["window_start"] = WINDOW_START.replace(tzinfo=None)
    wire_task = cast("Callable[[object], object]", task)

    with pytest.raises(ValidationError, match="timezone info"):
        wire_task(wire_request)


@pytest.mark.parametrize("task", [tasks.premarket_ingest, tasks.premarket_brief])
def test_r1_tasks_reject_windows_after_point_in_time_cutoff(task: object) -> None:
    request = _ingest_request() if task is tasks.premarket_ingest else _brief_request()
    wire_request = request.model_dump(mode="python")
    payload = wire_request["payload"]
    assert isinstance(payload, dict)
    payload["window_end"] = request.context.as_of + timedelta(seconds=1)
    wire_task = cast("Callable[[object], object]", task)

    with pytest.raises(ValidationError, match="window_end cannot be later"):
        wire_task(wire_request)


def test_fixture_tasks_fail_before_execution_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_fixture=False,
            app_env="development",
            r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        ),
    )
    executed = False

    def unexpected_execution(*_args: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("database execution must not start")

    monkeypatch.setattr(tasks, "_execute_premarket_ingest", unexpected_execution)

    with pytest.raises(ApplicationError) as exc_info:
        tasks.premarket_ingest(_ingest_request().model_dump(mode="json"))

    assert exc_info.value.code is ErrorCode.PRECONDITION_FAILED
    assert exc_info.value.safe_message == "R1_FIXTURE_DISABLED"
    assert executed is False


@pytest.mark.parametrize(
    ("task", "request_factory", "executor_name"),
    [
        (tasks.premarket_ingest, _ingest_request, "_execute_premarket_ingest"),
        (tasks.premarket_brief, _brief_request, "_execute_premarket_brief"),
    ],
)
def test_fixture_tasks_reject_non_fixed_target_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    task: object,
    request_factory: Callable[[], PremarketIngestJobRequest | PremarketBriefJobRequest],
    executor_name: str,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_fixture=True,
            app_env="development",
            r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        ),
    )
    request = request_factory()
    request.payload.active_target.instrument = request.payload.active_target.instrument.model_copy(
        update={"instrument_id": "XSHG:000001"}
    )
    executed = False

    def unexpected_execution(*_args: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("database execution must not start")

    monkeypatch.setattr(tasks, executor_name, unexpected_execution)
    wire_task = cast("Callable[[object], object]", task)

    with pytest.raises(ApplicationError) as exc_info:
        wire_task(request.model_dump(mode="json"))

    assert exc_info.value.code is ErrorCode.PRECONDITION_FAILED
    assert exc_info.value.safe_message == "R1_TARGET_LOCKED"
    assert executed is False


def test_real_adapter_shape_is_rights_blocked_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_fixture=True,
            app_env="development",
            r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        ),
    )
    request = _ingest_request().model_copy(deep=True)
    request.payload.source_adapter_version = "cninfo-production-v1"

    with pytest.raises(ApplicationError) as exc_info:
        tasks.premarket_ingest(request.model_dump(mode="json"))

    assert exc_info.value.code is ErrorCode.RIGHTS_BLOCKED
    assert exc_info.value.safe_message == "REAL_SOURCE_GRANT_REQUIRED"


def test_legacy_fixture_ingest_is_blocked_in_dev_live_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_fixture=True,
            app_env="development",
            r1_runtime_mode=SourceRuntimeMode.DEV_LIVE,
        ),
    )
    executed = False

    def unexpected_execution(*_args: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("legacy fixture must not execute in DEV_LIVE mode")

    monkeypatch.setattr(tasks, "_execute_premarket_ingest", unexpected_execution)

    with pytest.raises(ApplicationError) as exc_info:
        tasks.premarket_ingest(_ingest_request().model_dump(mode="json"))

    assert exc_info.value.code is ErrorCode.PRECONDITION_FAILED
    assert exc_info.value.safe_message == "R1_FIXTURE_DISABLED"
    assert executed is False


def test_policy_refresh_validates_stock_agnostic_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = PolicyRefreshRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW,
            idempotency_key="global-event-refresh-task-0001",
        ),
        scheduled_for=NOW,
        fixture_manifest_sha256="d" * 64,
    )

    async def successful_refresh(validated: PolicyRefreshRequest) -> PolicyRefreshResult:
        assert validated == request
        return PolicyRefreshResult(
            schema_version="1.0",
            scheduled_for=validated.scheduled_for,
            completed_at=NOW,
            events_seen=5,
            event_revisions_created=5,
            evidence_created=5,
            watermark=NOW,
            manifest_sha256="d" * 64,
            cursor_advanced=True,
        )

    monkeypatch.setattr(tasks, "_execute_policy_refresh", successful_refresh)
    result = PolicyRefreshResult.model_validate(
        tasks.policy_refresh(request.model_dump(mode="json"))
    )

    assert result.events_seen == 5
    assert result.cursor_advanced is True


@pytest.mark.parametrize(
    "task",
    [
        tasks.quote_snapshot,
        tasks.sector_rotation_tick,
        tasks.postmarket_outcome,
    ],
)
def test_deferred_tasks_fail_closed_without_side_effects(task: object) -> None:
    disabled_task = cast("Callable[[], object]", task)

    with pytest.raises(tasks.TaskDisabledError, match="FEATURE_NOT_ENABLED_FOR_STAGE"):
        disabled_task()
