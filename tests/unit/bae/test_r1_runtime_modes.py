import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apps.runtime import premarket as runtime_premarket
from apps.scheduler.requests import (
    build_demo_replay_ingest_requests,
    build_dev_live_ingest_requests,
    build_runtime_brief_request,
)
from apps.worker import tasks

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import (
    FetchBatch,
    SourceProvenanceV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.premarket_jobs import (
    PremarketIngestJobRequestV1_2,
    PremarketIngestJobResultV1_2,
    PremarketIngestTaskResultV1_2,
)
from stock_assistant.modules.ingestion.r1_sources import (
    DEMO_REPLAY_SOURCE_IDS,
    R1SourceBatch,
    ReplayScenario,
    build_demo_replay_batch_v1_2,
)
from stock_assistant.platform.sources.free_source_runner import FreeSourceRunnerError
from stock_assistant.platform.sources.r1_free_live import FREE_SOURCE_MATRIX

NOW = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)


class _Engine:
    async def dispose(self) -> None:
        return None


class _ActiveTarget:
    def __init__(self) -> None:
        self.load_contexts: list[object] = []
        self.initialization_contexts: list[object] = []
        self.found = False

    async def load(self, context: object) -> object:
        self.load_contexts.append(context)
        return SimpleNamespace(found=self.found)

    async def initialize(self, context: object) -> None:
        self.initialization_contexts.append(context)
        self.found = True
        return None


class _Repository:
    def __init__(self) -> None:
        self.persisted: list[object] = []
        self.failures: list[tuple[object, str]] = []

    async def acquire_source_lease(self, **_kwargs: object) -> object:
        return SimpleNamespace(provider_cursor=None)

    async def persist_source_batch(self, *, batch: object, **_kwargs: object) -> object:
        assert isinstance(batch, R1SourceBatch)
        self.persisted.append(batch)
        source_watermark = batch.coverages[0].progress.business_watermark
        return SimpleNamespace(
            reason_code=None,
            records_fetched=1,
            evidence_stored=1,
            event_revisions_stored=1,
            manifest_hash="a" * 64,
            source_watermark=source_watermark,
            idempotent_replay=False,
        )

    async def record_source_failure(
        self,
        *,
        batch: object,
        reason_code: str,
        **_kwargs: object,
    ) -> None:
        self.failures.append((batch, reason_code))


class _LeaseBusyRepository(_Repository):
    async def acquire_source_lease(self, **_kwargs: object) -> object:
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "R1_SOURCE_LEASE_BUSY",
            retryable=True,
        )


class _CursorConflictOnFailureRepository(_Repository):
    async def record_source_failure(self, **_kwargs: object) -> None:
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "R1_CURSOR_CAS_CONFLICT",
            retryable=True,
        )


class _CursorConflictOnPersistRepository(_Repository):
    async def persist_source_batch(self, **_kwargs: object) -> object:
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "R1_CURSOR_CAS_CONFLICT",
            retryable=True,
        )


class _BriefingRepository:
    def __init__(self) -> None:
        self.arguments: dict[str, object] | None = None

    async def generate_and_store_r1_briefing(self, **kwargs: object) -> object:
        self.arguments = kwargs
        return SimpleNamespace(
            briefing=SimpleNamespace(
                briefing_id="00000000-0000-0000-0000-000000000499",
                generated_at=kwargs["generated_at"],
            ),
            manifest_hash="b" * 64,
            event_count=1,
            evidence_count=1,
            idempotent_replay=False,
            reason_codes=(),
        )


def _runtime_settings(
    mode: SourceRuntimeModeV1_2,
    *,
    replay_scenario: ReplayScenario = ReplayScenario.SUCCESS,
    replay_capability: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        app_env="development",
        enable_r1_fixture=mode is SourceRuntimeModeV1_2.DEMO_REPLAY,
        enable_r1_dev_live=mode is SourceRuntimeModeV1_2.DEV_LIVE,
        r1_runtime_mode=mode,
        r1_demo_replay_scenario=replay_scenario.value,
        r1_demo_replay_capability=replay_capability,
        database_url=SimpleNamespace(get_secret_value=lambda: "unused"),
        assert_safe_production=lambda: None,
    )


def _install_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch,
    repository: _Repository,
    *,
    mode: SourceRuntimeModeV1_2,
    replay_scenario: ReplayScenario = ReplayScenario.SUCCESS,
    replay_capability: str | None = None,
) -> _ActiveTarget:
    monkeypatch.setattr(
        runtime_premarket,
        "get_settings",
        lambda: _runtime_settings(
            mode,
            replay_scenario=replay_scenario,
            replay_capability=replay_capability,
        ),
    )
    monkeypatch.setattr(runtime_premarket, "create_async_engine", lambda _url: _Engine())
    monkeypatch.setattr(
        runtime_premarket,
        "PostgresR1PremarketRepository",
        lambda _engine: repository,
    )
    monkeypatch.setattr(runtime_premarket, "PostgresPortfolioRepository", lambda _engine: object())
    active_target = _ActiveTarget()
    monkeypatch.setattr(
        runtime_premarket,
        "FixedActiveTargetService",
        lambda _repository: active_target,
    )
    monkeypatch.setattr(runtime_premarket, "utc_now", lambda: NOW)
    return active_target


def _success(
    request: PremarketIngestJobRequestV1_2,
    observed_at: datetime,
) -> PremarketIngestJobResultV1_2:
    batch = build_demo_replay_batch_v1_2(
        capability=request.payload.capability,
        scenario=ReplayScenario.SUCCESS,
        window_start=request.payload.window_start,
        window_end=request.payload.window_end,
        instrument_id="XSHG:603986",
    )
    return PremarketIngestJobResultV1_2(
        schema_version="1.2",
        job_id=request.job_id,
        status=JobStatus.SUCCEEDED,
        started_at=observed_at,
        completed_at=observed_at,
        attempt=request.attempt,
        result=PremarketIngestTaskResultV1_2(
            schema_version="1.2",
            active_target=request.payload.active_target,
            source_id=request.payload.source_id,
            capability=request.payload.capability,
            runtime_mode=request.payload.runtime_mode,
            replay_session_id=request.payload.replay_session_id,
            provenance=SourceProvenanceV1_2.model_validate(batch.provenance),
            window_start=request.payload.window_start,
            window_end=request.payload.window_end,
            records_fetched=1,
            evidence_stored=1,
            event_revisions_stored=1,
            data_manifest_hash="a" * 64,
            source_watermark=request.payload.window_end,
        ),
    )


def test_worker_round_trips_v1_2_request_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_demo_replay_ingest_requests(NOW)[0]
    monkeypatch.setattr(tasks, "utc_now", lambda: NOW)
    monkeypatch.setattr(
        runtime_premarket,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            enable_r1_fixture=True,
            enable_r1_dev_live=False,
            r1_runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
            assert_safe_production=lambda: None,
        ),
    )
    monkeypatch.setattr(tasks, "_execute_premarket_ingest_v1_2", _success)

    result = PremarketIngestJobResultV1_2.model_validate(
        tasks.premarket_ingest(request.model_dump(mode="json"))
    )

    assert result.schema_version == "1.2"
    assert result.status is JobStatus.SUCCEEDED
    assert result.result is not None
    assert result.result.capability == request.payload.capability
    assert result.result.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY
    assert result.result.provenance.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY


def test_brief_runtime_mode_keeps_ambiguous_replay_sources_schema_neutral() -> None:
    replay_source_ids = tuple(DEMO_REPLAY_SOURCE_IDS.values())
    free_source_ids = tuple(item.source_id for item in FREE_SOURCE_MATRIX)

    assert tasks._runtime_mode_for_brief(replay_source_ids) is SourceRuntimeMode.DEMO_REPLAY
    assert tasks._runtime_mode_for_brief(free_source_ids) is SourceRuntimeModeV1_2.DEV_LIVE


def test_dev_live_brief_uses_stable_request_generation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_runtime_brief_request(NOW, runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE)
    observed_at = NOW + timedelta(seconds=30)
    repository = _BriefingRepository()
    monkeypatch.setattr(
        runtime_premarket,
        "get_settings",
        lambda: SimpleNamespace(database_url=SimpleNamespace(get_secret_value=lambda: "unused")),
    )
    monkeypatch.setattr(runtime_premarket, "create_async_engine", lambda _url: _Engine())
    monkeypatch.setattr(
        runtime_premarket,
        "PostgresR1PremarketRepository",
        lambda _engine: repository,
    )
    monkeypatch.setattr(runtime_premarket, "utc_now", lambda: observed_at)

    result = asyncio.run(tasks._execute_premarket_brief_async(request, observed_at))
    first_arguments = dict(repository.arguments or {})
    retried = asyncio.run(
        tasks._execute_premarket_brief_async(
            request,
            observed_at + timedelta(minutes=1),
        )
    )

    assert result.result is not None
    assert retried.result is not None
    assert repository.arguments is not None
    assert repository.arguments["window_end"] == request.payload.window_end
    assert "visibility_cutoff" not in repository.arguments
    assert repository.arguments["generated_at"] == request.scheduled_for
    assert first_arguments["generated_at"] == repository.arguments["generated_at"]


def test_dev_live_request_never_falls_back_to_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_dev_live_ingest_requests(NOW)[0]
    monkeypatch.setattr(
        runtime_premarket,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            enable_r1_fixture=True,
            enable_r1_dev_live=False,
            r1_runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
            assert_safe_production=lambda: None,
        ),
    )
    executed = False

    def unexpected(*_args: object) -> object:
        nonlocal executed
        executed = True
        raise AssertionError("disabled DEV_LIVE must not execute or replay")

    monkeypatch.setattr(tasks, "_execute_premarket_ingest_v1_2", unexpected)

    with pytest.raises(ApplicationError) as exc_info:
        tasks.premarket_ingest(request.model_dump(mode="json"))

    assert exc_info.value.code is ErrorCode.PRECONDITION_FAILED
    assert exc_info.value.safe_message == "R1_DEV_LIVE_DISABLED"
    assert executed is False


def test_authorized_mode_fails_closed_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_dev_live_ingest_requests(NOW)[0].model_copy(deep=True)
    request.payload.runtime_mode = SourceRuntimeModeV1_2.AUTHORIZED
    monkeypatch.setattr(
        runtime_premarket,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            enable_r1_fixture=True,
            enable_r1_dev_live=True,
            r1_runtime_mode=SourceRuntimeModeV1_2.AUTHORIZED,
            assert_safe_production=lambda: None,
        ),
    )

    with pytest.raises(ApplicationError) as exc_info:
        tasks.premarket_ingest(request.model_dump(mode="json"))

    assert exc_info.value.code is ErrorCode.RIGHTS_BLOCKED
    assert exc_info.value.safe_message == "AUTHORIZED_CONNECTORS_NOT_STARTED"


def test_demo_replay_execution_never_calls_dev_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_demo_replay_ingest_requests(NOW)[0]
    repository = _Repository()
    _install_runtime_fakes(monkeypatch, repository, mode=SourceRuntimeModeV1_2.DEMO_REPLAY)

    async def unexpected(_request: object, **_kwargs: object) -> object:
        raise AssertionError("DEMO_REPLAY must not call DEV_LIVE")

    monkeypatch.setattr(runtime_premarket, "_fetch_free_live", unexpected)
    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.SUCCEEDED
    assert len(repository.persisted) == 1
    assert repository.failures == []


def test_active_target_bootstrap_context_is_stable_across_market_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_request = build_demo_replay_ingest_requests(NOW)[0]
    second_request = build_demo_replay_ingest_requests(NOW + timedelta(days=1))[0]
    assert first_request.payload.window_end != second_request.payload.window_end
    repository = _Repository()
    active_target = _install_runtime_fakes(
        monkeypatch,
        repository,
        mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
    )

    asyncio.run(tasks._execute_premarket_ingest_v1_2_async(first_request, NOW))
    asyncio.run(
        tasks._execute_premarket_ingest_v1_2_async(
            second_request,
            NOW + timedelta(days=1),
        )
    )

    assert len(active_target.load_contexts) == 2
    first_load, second_load = active_target.load_contexts
    assert isinstance(first_load, OperationContext)
    assert isinstance(second_load, OperationContext)
    assert first_load.as_of == NOW
    assert second_load.as_of == NOW + timedelta(days=1)
    assert active_target.initialization_contexts == [tasks._r1_target_initialization_context()]
    context = tasks._r1_target_initialization_context()
    assert context.as_of == context.requested_at
    assert context.as_of == datetime(2026, 7, 25, 0, 55, tzinfo=UTC)
    assert context.idempotency_key == "r1-fixed-target-bootstrap-v2"


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        (ReplayScenario.SOURCE_FAILURE, "R1_REPLAY_SOURCE_FAILURE"),
        (ReplayScenario.RIGHTS_BLOCKED, "R1_REPLAY_RIGHTS_BLOCKED"),
    ],
)
def test_demo_replay_failure_records_terminal_gap_without_cursor_advance(
    monkeypatch: pytest.MonkeyPatch,
    scenario: ReplayScenario,
    expected_reason: str,
) -> None:
    request = next(
        item
        for item in build_demo_replay_ingest_requests(NOW)
        if item.payload.capability == "financial.changes"
    )
    repository = _Repository()
    _install_runtime_fakes(
        monkeypatch,
        repository,
        mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
        replay_scenario=scenario,
        replay_capability="financial.changes",
    )

    async def unexpected(_request: object, **_kwargs: object) -> object:
        raise AssertionError("DEMO_REPLAY failure must not call DEV_LIVE")

    monkeypatch.setattr(runtime_premarket, "_fetch_free_live", unexpected)
    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.DEGRADED
    assert result.result is not None
    assert result.result.reason_codes == [expected_reason]
    assert result.result.source_watermark is None
    assert repository.persisted == []
    assert len(repository.failures) == 1
    failed_batch, failure_reason = repository.failures[0]
    assert isinstance(failed_batch, R1SourceBatch)
    assert failed_batch.capability == "financial.changes"
    assert failed_batch.coverages[0].progress.business_watermark is None
    assert failure_reason == expected_reason


def test_dev_live_macro_records_terminal_gap_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = next(
        item
        for item in build_dev_live_ingest_requests(NOW)
        if item.payload.capability == "macro.flashes"
    )
    repository = _Repository()
    _install_runtime_fakes(monkeypatch, repository, mode=SourceRuntimeModeV1_2.DEV_LIVE)

    async def not_qualified(_request: object, **_kwargs: object) -> object:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "FREE_SOURCE_NOT_QUALIFIED")

    monkeypatch.setattr(runtime_premarket, "_fetch_free_live", not_qualified)
    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.DEGRADED
    assert result.result is not None
    assert result.result.reason_codes == ["FREE_SOURCE_NOT_QUALIFIED"]
    assert repository.persisted == []
    assert len(repository.failures) == 1


def test_research_source_without_runtime_credential_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = next(
        item
        for item in build_dev_live_ingest_requests(NOW)
        if item.payload.source_id == "research_preview_market"
    )
    monkeypatch.setattr(
        runtime_premarket,
        "get_settings",
        lambda: SimpleNamespace(r1_research_data_api_token=None),
    )

    with pytest.raises(ApplicationError) as exc_info:
        asyncio.run(runtime_premarket._fetch_free_live(request, provider_cursor=None))

    assert exc_info.value.safe_message == "RESEARCH_DATA_CREDENTIAL_MISSING"


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (FreeSourceRunnerError("FREE_SOURCE_TIMEOUT", retryable=True), "FREE_SOURCE_TIMEOUT"),
        (
            FreeSourceRunnerError("FREE_SOURCE_UPSTREAM_ERROR", retryable=False),
            "FREE_SOURCE_UPSTREAM_ERROR",
        ),
        (
            ApplicationError(ErrorCode.RIGHTS_BLOCKED, "vendor cookie and account detail"),
            "RIGHTS_BLOCKED",
        ),
        (
            ApplicationError(ErrorCode.RIGHTS_BLOCKED, "DEV_RIGHTS_BLOCKED"),
            "DEV_RIGHTS_BLOCKED",
        ),
        (
            ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_SCHEMA_INVALID",
            ),
            "R1_SOURCE_SCHEMA_INVALID",
        ),
        (
            ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_CLOCK_INVALID",
            ),
            "R1_SOURCE_CLOCK_INVALID",
        ),
        (ValueError("vendor timestamp is malformed"), "R1_FREE_SOURCE_SCHEMA_INVALID"),
    ],
)
def test_dev_live_source_error_is_a_stable_degraded_result(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_reason: str,
) -> None:
    request = next(
        item
        for item in build_dev_live_ingest_requests(NOW)
        if item.payload.source_id == "research_preview_market"
    )
    repository = _Repository()
    _install_runtime_fakes(monkeypatch, repository, mode=SourceRuntimeModeV1_2.DEV_LIVE)

    async def fail(_request: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(runtime_premarket, "_fetch_free_live", fail)
    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.DEGRADED
    assert result.failure is None
    assert result.result is not None
    assert result.result.reason_codes == [expected_reason]
    assert repository.persisted == []
    assert repository.failures[0][1] == expected_reason


def test_dev_live_busy_source_lease_is_a_stable_degraded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = next(
        item
        for item in build_dev_live_ingest_requests(NOW)
        if item.payload.source_id == "research_preview_market"
    )
    repository = _LeaseBusyRepository()
    _install_runtime_fakes(monkeypatch, repository, mode=SourceRuntimeModeV1_2.DEV_LIVE)

    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.DEGRADED
    assert result.failure is None
    assert result.result is not None
    assert result.result.reason_codes == ["R1_SOURCE_LEASE_BUSY"]
    assert repository.persisted == []
    assert repository.failures == []


def test_dev_live_retry_cursor_conflict_is_a_stable_degraded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = next(
        item
        for item in build_dev_live_ingest_requests(NOW)
        if item.payload.source_id == "research_preview_company_news"
    )
    repository = _CursorConflictOnFailureRepository()
    _install_runtime_fakes(monkeypatch, repository, mode=SourceRuntimeModeV1_2.DEV_LIVE)

    async def unavailable(_request: object, **_kwargs: object) -> object:
        raise FreeSourceRunnerError("RESEARCH_DATA_TIMEOUT", retryable=True)

    monkeypatch.setattr(runtime_premarket, "_fetch_free_live", unavailable)
    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.DEGRADED
    assert result.failure is None
    assert result.result is not None
    assert result.result.reason_codes == ["R1_CURSOR_CAS_CONFLICT"]
    assert repository.persisted == []
    assert repository.failures == []


def test_dev_live_success_cursor_conflict_is_a_stable_degraded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = next(
        item
        for item in build_dev_live_ingest_requests(NOW)
        if item.payload.source_id == "research_preview_financials"
    )
    repository = _CursorConflictOnPersistRepository()
    _install_runtime_fakes(monkeypatch, repository, mode=SourceRuntimeModeV1_2.DEV_LIVE)

    async def available(_request: object, **_kwargs: object) -> FetchBatch:
        return FetchBatch(
            schema_version="1.0",
            source_id=request.payload.source_id,
            records=[],
            exhausted=True,
            started_at=NOW,
            completed_at=NOW,
            source_watermark=request.payload.window_end,
            rights_snapshot_hash=request.payload.rights_snapshot_hash,
        )

    monkeypatch.setattr(runtime_premarket, "_fetch_free_live", available)
    result = asyncio.run(tasks._execute_premarket_ingest_v1_2_async(request, NOW))

    assert result.status is JobStatus.DEGRADED
    assert result.failure is None
    assert result.result is not None
    assert result.result.reason_codes == ["R1_CURSOR_CAS_CONFLICT"]
    assert repository.persisted == []
    assert repository.failures == []
