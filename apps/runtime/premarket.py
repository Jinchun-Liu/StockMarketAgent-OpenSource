"""Shared R1 premarket use-case composition for Celery and one-shot ECS tasks."""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Final, Literal, cast
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.ingestion import (
    FetchBatch,
    FetchRequest,
    SourceProvenanceV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.premarket_jobs import (
    PremarketBriefJobRequest,
    PremarketBriefJobResult,
    PremarketBriefTaskResult,
    PremarketIngestJobRequestV1_2,
    PremarketIngestJobResultV1_2,
    PremarketIngestTaskResultV1_2,
)
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.modules.ingestion.r1_fixture import FIXTURE_RULESET_VERSION
from stock_assistant.modules.ingestion.r1_sources import (
    DEMO_REPLAY_ADAPTER_VERSION,
    DEMO_REPLAY_RIGHTS_HASH,
    DEMO_REPLAY_SOURCE_IDS,
    R1SourceBatch,
    ReplayScenario,
    build_demo_replay_batch_v1_2,
    r1_source_manifest_hash,
    source_batch_from_fetch,
)
from stock_assistant.modules.portfolio.active_target import (
    R1_INSTRUMENT,
    R1_WATCHLIST_ID,
    FixedActiveTargetService,
)
from stock_assistant.platform.db.portfolio import PostgresPortfolioRepository
from stock_assistant.platform.db.r1_premarket import (
    FixtureBriefingStorageResult,
    PostgresR1PremarketRepository,
    R1BriefingStorageResult,
    R1SourceLease,
)
from stock_assistant.platform.sources.free_source_runner import (
    FreeSourceRunnerError,
    FreeSourceSubprocessRunner,
)
from stock_assistant.platform.sources.qinghong_flash_runner import (
    QINGHONG_SOURCE_IDS,
    HttpxQinghongFlashRunner,
)
from stock_assistant.platform.sources.r1_dev_live import DEV_LIVE_SOURCE_IDS
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_ADAPTER_VERSION,
    FREE_SOURCE_CATALOG,
    FREE_SOURCE_LICENSE_ID,
    FREE_SOURCE_RIGHTS_HASH,
    FreeDevelopmentRightsPolicy,
    FreeLiveAdapter,
    RightsGatedFreeConnector,
    active_free_source_definitions,
    free_source_gap,
)
from stock_assistant.platform.sources.research_preview_runner import (
    PROMAX_BASE_URL,
    PROMAX_PROVIDER,
    QUANTDATA_BASE_URL,
    QUANTDATA_PROVIDER,
    RESEARCH_PREVIEW_CONNECT_TIMEOUT_SECONDS,
    RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS,
    RESEARCH_PREVIEW_SOURCE_IDS,
    AccountRateLimiter,
    HttpxResearchPreviewRunner,
    PostgresAccountRateLimiter,
)

_STABLE_REASON_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_QINGHONG_ACCOUNT_LIMITER: Final = AccountRateLimiter()
_R1_TARGET_BOOTSTRAP_AT: Final = datetime.fromisoformat("2026-07-25T00:55:00+00:00")
_R1_TARGET_BOOTSTRAP_REQUEST_ID: Final = UUID("9548ea44-c879-57de-9434-01780edb1a56")
_R1_TARGET_BOOTSTRAP_CORRELATION_ID: Final = UUID("b922bb7e-aa15-5d19-a8df-c24623e8e2e8")
_CURSOR_CAS_CONFLICT: Final = "R1_CURSOR_CAS_CONFLICT"


def _bootstrap_context() -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        request_id=_R1_TARGET_BOOTSTRAP_REQUEST_ID,
        correlation_id=_R1_TARGET_BOOTSTRAP_CORRELATION_ID,
        actor_id="r1-fixed-target-bootstrap",
        workspace_id="default",
        as_of=_R1_TARGET_BOOTSTRAP_AT,
        requested_at=_R1_TARGET_BOOTSTRAP_AT,
        idempotency_key="r1-fixed-target-bootstrap-v2",
        locale="zh-CN",
        timezone="Asia/Shanghai",
    )


async def _ensure_target(engine: AsyncEngine, observed_at: datetime) -> None:
    service = FixedActiveTargetService(PostgresPortfolioRepository(engine))
    state = await service.load(
        OperationContext(
            schema_version="1.0",
            request_id=_R1_TARGET_BOOTSTRAP_REQUEST_ID,
            correlation_id=_R1_TARGET_BOOTSTRAP_CORRELATION_ID,
            actor_id="r1-fixed-target-bootstrap",
            workspace_id="default",
            as_of=observed_at,
            requested_at=observed_at,
            locale="zh-CN",
            timezone="Asia/Shanghai",
        )
    )
    if not state.found:
        await service.initialize(_bootstrap_context())


def require_v1_2_runtime(request: PremarketIngestJobRequestV1_2) -> None:
    """Apply the same fail-closed runtime gate in Celery and ECS compositions."""

    payload = request.payload
    settings = get_settings()
    settings.assert_safe_production()
    target = payload.active_target
    if (
        target.instrument != R1_INSTRUMENT
        or target.watchlist_id != R1_WATCHLIST_ID
        or target.watchlist_revision != 1
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")
    if payload.ruleset_version != FIXTURE_RULESET_VERSION:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_RULESET_MISMATCH")
    if payload.runtime_mode is SourceRuntimeModeV1_2.AUTHORIZED:
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "AUTHORIZED_CONNECTORS_NOT_STARTED")
    if payload.runtime_mode is SourceRuntimeModeV1_2.PUBLIC_STATIC:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "PUBLIC_STATIC_INGEST_FORBIDDEN")
    if payload.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY:
        valid = (
            settings.enable_r1_fixture
            and settings.r1_runtime_mode.value == SourceRuntimeMode.DEMO_REPLAY.value
            and DEMO_REPLAY_SOURCE_IDS.get(payload.capability) == payload.source_id
            and payload.source_adapter_version == DEMO_REPLAY_ADAPTER_VERSION
            and payload.rights_snapshot_hash == DEMO_REPLAY_RIGHTS_HASH
        )
    else:
        enabled = {
            item.source_id: item for item in active_free_source_definitions(FREE_SOURCE_CATALOG)
        }
        definition = enabled.get(payload.source_id)
        valid = (
            settings.enable_r1_dev_live
            and settings.r1_runtime_mode.value == SourceRuntimeMode.DEV_LIVE.value
            and definition is not None
            and definition.capability == payload.capability
            and payload.source_adapter_version == FREE_SOURCE_ADAPTER_VERSION
            and payload.rights_snapshot_hash == FREE_SOURCE_RIGHTS_HASH
            and payload.replay_session_id is None
        )
    if not valid:
        reason = (
            "R1_FIXTURE_DISABLED"
            if payload.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY
            and not settings.enable_r1_fixture
            else "R1_DEV_LIVE_DISABLED"
            if payload.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
            and not settings.enable_r1_dev_live
            else "R1_SOURCE_PROFILE_MISMATCH"
        )
        code = (
            ErrorCode.PRECONDITION_FAILED
            if reason in {"R1_FIXTURE_DISABLED", "R1_DEV_LIVE_DISABLED"}
            else ErrorCode.RIGHTS_BLOCKED
        )
        raise ApplicationError(code, reason)


@lru_cache(maxsize=1)
def free_live_runner() -> FreeSourceSubprocessRunner:
    """Keep one endpoint-isolated breaker registry per worker/coordinator process."""

    return FreeSourceSubprocessRunner()


def reset_free_live_runner_cache() -> None:
    free_live_runner.cache_clear()


def _free_reason_code(error: Exception) -> str:
    if isinstance(error, FreeSourceRunnerError):
        return error.reason_code
    if isinstance(error, ApplicationError):
        if _STABLE_REASON_CODE.fullmatch(error.safe_message):
            return error.safe_message
        if _STABLE_REASON_CODE.fullmatch(error.code.value):
            return error.code.value
    return "R1_SOURCE_UNAVAILABLE"


def _free_gap_status(error: Exception) -> SourceCoverageStatus:
    if isinstance(error, ApplicationError):
        if error.code is ErrorCode.RIGHTS_BLOCKED:
            return SourceCoverageStatus.RIGHTS_BLOCKED
        if error.safe_message == "FREE_SOURCE_NOT_QUALIFIED":
            return SourceCoverageStatus.NOT_QUALIFIED
    if isinstance(error, FreeSourceRunnerError):
        if error.reason_code in {
            "FREE_SOURCE_RATE_LIMITED",
            "QINGHONG_RATE_LIMITED",
            "RESEARCH_DATA_RATE_LIMITED",
        }:
            return SourceCoverageStatus.DELAYED
        if error.reason_code in {
            "QINGHONG_PERMISSION_BLOCKED",
            "RESEARCH_DATA_PERMISSION_BLOCKED",
        }:
            return SourceCoverageStatus.RIGHTS_BLOCKED
    return SourceCoverageStatus.UNAVAILABLE


async def _fetch_free_live(
    request: PremarketIngestJobRequestV1_2,
    *,
    provider_cursor: str | None,
    engine: AsyncEngine | None = None,
) -> FetchBatch:
    payload = request.payload
    adapter = FreeLiveAdapter(payload.source_id)
    fetch_request = FetchRequest(
        schema_version="1.0",
        context=request.context,
        source_id=payload.source_id,
        capability=payload.capability,
        rights_snapshot_hash=payload.rights_snapshot_hash,
        cursor=provider_cursor,
        since=payload.window_start,
        until=payload.window_end,
        instrument_ids=[payload.active_target.instrument.instrument_id],
        limit=payload.max_records,
    )
    if payload.source_id in QINGHONG_SOURCE_IDS.values():
        settings = get_settings()
        api_key = settings.r1_qinghong_api_key
        if api_key is None:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "QINGHONG_CREDENTIAL_MISSING",
            )
        base_url = settings.r1_qinghong_api_base_url
        if base_url is None:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "QINGHONG_HTTPS_ENDPOINT_REQUIRED",
            )
        timeout = httpx.Timeout(10.0, connect=5.0)
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
        async with httpx.AsyncClient(
            base_url=str(base_url),
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        ) as client:
            connector = RightsGatedFreeConnector(
                adapter=adapter,
                rights_policy=FreeDevelopmentRightsPolicy(),
                runner=HttpxQinghongFlashRunner(
                    api_key=api_key,
                    client=client,
                    limiter=_QINGHONG_ACCOUNT_LIMITER,
                ),
            )
            return await connector.fetch(fetch_request)
    if payload.source_id in RESEARCH_PREVIEW_SOURCE_IDS.values():
        settings = get_settings()
        provider = cast(
            'Literal["quantdata888_tushare_compatible", "promax_tushare_compatible"]',
            adapter.definition.provider,
        )
        if provider == QUANTDATA_PROVIDER:
            credential = settings.r1_research_data_api_token
            research_base_url = QUANTDATA_BASE_URL
        elif provider == PROMAX_PROVIDER:
            credential = settings.r1_promax_api_key
            research_base_url = PROMAX_BASE_URL
        else:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "RESEARCH_DATA_PROVIDER_NOT_ALLOWED",
            )
        if credential is None:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "RESEARCH_DATA_CREDENTIAL_MISSING",
            )
        timeout = httpx.Timeout(
            RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS,
            connect=RESEARCH_PREVIEW_CONNECT_TIMEOUT_SECONDS,
        )
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
        async with httpx.AsyncClient(
            base_url=research_base_url,
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        ) as client:
            connector = RightsGatedFreeConnector(
                adapter=adapter,
                rights_policy=FreeDevelopmentRightsPolicy(),
                runner=HttpxResearchPreviewRunner(
                    token=credential,
                    client=client,
                    provider=provider,
                    limiter=(
                        PostgresAccountRateLimiter(engine)
                        if engine is not None
                        else AccountRateLimiter()
                    ),
                ),
            )
            return await connector.fetch(fetch_request)
    connector = RightsGatedFreeConnector(
        adapter=adapter,
        rights_policy=FreeDevelopmentRightsPolicy(),
        runner=free_live_runner(),
    )
    return await connector.fetch(fetch_request)


def _identity_batch(request: PremarketIngestJobRequestV1_2) -> R1SourceBatch:
    payload = request.payload
    if payload.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY:
        settings = get_settings()
        configured_capability = settings.r1_demo_replay_capability
        scenario = (
            ReplayScenario(settings.r1_demo_replay_scenario)
            if payload.replay_session_id is None and configured_capability == payload.capability
            else ReplayScenario.SUCCESS
        )
        return build_demo_replay_batch_v1_2(
            capability=payload.capability,
            scenario=scenario,
            window_start=payload.window_start,
            window_end=payload.window_end,
            instrument_id=payload.active_target.instrument.instrument_id,
            replay_session_id=payload.replay_session_id,
        )
    return free_source_gap(
        source_id=payload.source_id,
        observed_at=payload.window_end,
        status=SourceCoverageStatus.TIMELINESS_UNVERIFIED,
        reason_code="R1_FREE_SOURCE_PENDING",
    )


def _degraded_result(
    *,
    request: PremarketIngestJobRequestV1_2,
    observed_at: datetime,
    batch: R1SourceBatch,
    reason_code: str,
) -> PremarketIngestJobResultV1_2:
    return PremarketIngestJobResultV1_2(
        schema_version="1.2",
        job_id=request.job_id,
        status=JobStatus.DEGRADED,
        started_at=observed_at,
        completed_at=max(observed_at, utc_now()),
        attempt=request.attempt,
        result=PremarketIngestTaskResultV1_2(
            schema_version="1.2",
            active_target=request.payload.active_target,
            source_id=request.payload.source_id,
            capability=request.payload.capability,
            runtime_mode=request.payload.runtime_mode,
            replay_session_id=request.payload.replay_session_id,
            provenance=cast("SourceProvenanceV1_2", batch.provenance),
            window_start=request.payload.window_start,
            window_end=request.payload.window_end,
            records_fetched=0,
            evidence_stored=0,
            event_revisions_stored=0,
            data_manifest_hash=r1_source_manifest_hash(batch),
            next_cursor=None,
            source_watermark=None,
            reason_codes=[reason_code],
            idempotent_replay=False,
        ),
    )


async def execute_premarket_ingest_v1_2(
    request: PremarketIngestJobRequestV1_2,
    observed_at: datetime,
    *,
    engine: AsyncEngine | None = None,
) -> PremarketIngestJobResultV1_2:
    """Acquire lease, inject its opaque cursor, fetch, and persist one source."""

    require_v1_2_runtime(request)
    owns_engine = engine is None
    active_engine = engine or create_async_engine(get_settings().database_url.get_secret_value())
    payload = request.payload
    identity_batch = _identity_batch(request)
    try:
        repository = PostgresR1PremarketRepository(active_engine)

        async def record_failure_or_cursor_conflict(
            *,
            batch: R1SourceBatch,
            reason_code: str,
            lease: R1SourceLease,
        ) -> str:
            try:
                await repository.record_source_failure(
                    batch=batch,
                    window_end=payload.window_end,
                    lease=lease,
                    reason_code=reason_code,
                )
            except ApplicationError as error:
                if (
                    payload.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
                    and error.safe_message == _CURSOR_CAS_CONFLICT
                ):
                    return _CURSOR_CAS_CONFLICT
                raise
            return reason_code

        try:
            lease = await repository.acquire_source_lease(
                batch=identity_batch,
                owner=f"{request.job_id}:{request.attempt}",
            )
        except ApplicationError as error:
            if payload.runtime_mode is not SourceRuntimeModeV1_2.DEV_LIVE:
                raise
            reason_code = _free_reason_code(error)
            gap = free_source_gap(
                source_id=payload.source_id,
                observed_at=payload.window_end,
                status=_free_gap_status(error),
                reason_code=reason_code,
            )
            return _degraded_result(
                request=request,
                observed_at=observed_at,
                batch=gap,
                reason_code=reason_code,
            )
        await _ensure_target(active_engine, observed_at)
        if payload.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY:
            batch = identity_batch
        else:
            adapter = FreeLiveAdapter(payload.source_id)
            try:
                fetched = await _fetch_free_live(
                    request,
                    provider_cursor=lease.provider_cursor,
                    engine=active_engine,
                )
                batch = source_batch_from_fetch(
                    fetch_batch=fetched,
                    capability=payload.capability,
                    provenance=adapter.provenance,
                    license_id=FREE_SOURCE_LICENSE_ID,
                )
            except (ApplicationError, FreeSourceRunnerError) as error:
                reason_code = _free_reason_code(error)
                gap = free_source_gap(
                    source_id=payload.source_id,
                    observed_at=payload.window_end,
                    status=_free_gap_status(error),
                    reason_code=reason_code,
                )
                reason_code = await record_failure_or_cursor_conflict(
                    batch=gap,
                    lease=lease,
                    reason_code=reason_code,
                )
                return _degraded_result(
                    request=request,
                    observed_at=observed_at,
                    batch=gap,
                    reason_code=reason_code,
                )
            except (KeyError, TypeError, ValueError):
                reason_code = "R1_FREE_SOURCE_SCHEMA_INVALID"
                gap = free_source_gap(
                    source_id=payload.source_id,
                    observed_at=payload.window_end,
                    status=SourceCoverageStatus.UNAVAILABLE,
                    reason_code=reason_code,
                )
                reason_code = await record_failure_or_cursor_conflict(
                    batch=gap,
                    lease=lease,
                    reason_code=reason_code,
                )
                return _degraded_result(
                    request=request,
                    observed_at=observed_at,
                    batch=gap,
                    reason_code=reason_code,
                )

        coverage = batch.coverages[0]
        if coverage.status is not SourceCoverageStatus.OK:
            reason_code = coverage.gap_reason or "R1_SOURCE_UNAVAILABLE"
            reason_code = await record_failure_or_cursor_conflict(
                batch=batch,
                lease=lease,
                reason_code=reason_code,
            )
            return _degraded_result(
                request=request,
                observed_at=observed_at,
                batch=batch,
                reason_code=reason_code,
            )
        try:
            stored = await repository.persist_source_batch(
                batch=batch,
                window_start=payload.window_start,
                window_end=payload.window_end,
                lease=lease,
            )
        except ApplicationError as error:
            if (
                payload.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
                and error.safe_message == _CURSOR_CAS_CONFLICT
            ):
                return _degraded_result(
                    request=request,
                    observed_at=observed_at,
                    batch=batch,
                    reason_code=_CURSOR_CAS_CONFLICT,
                )
            raise
    finally:
        if owns_engine:
            await active_engine.dispose()
    status = JobStatus.SKIPPED if stored.reason_code is not None else JobStatus.SUCCEEDED
    return PremarketIngestJobResultV1_2(
        schema_version="1.2",
        job_id=request.job_id,
        status=status,
        started_at=observed_at,
        completed_at=max(observed_at, utc_now()),
        attempt=request.attempt,
        result=PremarketIngestTaskResultV1_2(
            schema_version="1.2",
            active_target=payload.active_target,
            source_id=payload.source_id,
            capability=payload.capability,
            runtime_mode=payload.runtime_mode,
            replay_session_id=payload.replay_session_id,
            provenance=cast("SourceProvenanceV1_2", batch.provenance),
            window_start=payload.window_start,
            window_end=payload.window_end,
            records_fetched=stored.records_fetched,
            evidence_stored=stored.evidence_stored,
            event_revisions_stored=stored.event_revisions_stored,
            data_manifest_hash=(None if stored.reason_code is not None else stored.manifest_hash),
            source_watermark=(None if stored.reason_code is not None else stored.source_watermark),
            reason_codes=([] if stored.reason_code is None else [stored.reason_code]),
            idempotent_replay=stored.idempotent_replay,
        ),
    )


def runtime_mode_for_brief(
    source_ids: tuple[str, ...],
) -> SourceRuntimeMode | SourceRuntimeModeV1_2 | None:
    source_set = set(source_ids)
    if source_set == set(DEMO_REPLAY_SOURCE_IDS.values()):
        return SourceRuntimeMode.DEMO_REPLAY
    enabled_ids = {item.source_id for item in active_free_source_definitions(FREE_SOURCE_CATALOG)}
    if source_set == enabled_ids:
        return SourceRuntimeModeV1_2.DEV_LIVE
    if source_set == set(DEV_LIVE_SOURCE_IDS.values()):
        return SourceRuntimeMode.DEV_LIVE
    return None


async def execute_premarket_brief(
    request: PremarketBriefJobRequest,
    observed_at: datetime,
    *,
    engine: AsyncEngine | None = None,
) -> PremarketBriefJobResult:
    """Generate one persisted briefing through the same repository use case."""

    owns_engine = engine is None
    active_engine = engine or create_async_engine(get_settings().database_url.get_secret_value())
    payload = request.payload
    try:
        repository = PostgresR1PremarketRepository(active_engine)
        runtime_mode = runtime_mode_for_brief(tuple(payload.source_ids))
        stored: FixtureBriefingStorageResult | R1BriefingStorageResult
        if runtime_mode is None:
            stored = await repository.generate_and_store_briefing(
                window_start=payload.window_start,
                window_end=payload.window_end,
                generated_at=max(observed_at, payload.window_end),
                source_ids=tuple(payload.source_ids),
            )
        else:
            stored = await repository.generate_and_store_r1_briefing(
                window_start=payload.window_start,
                window_end=payload.window_end,
                generated_at=max(request.scheduled_for or request.queued_at, payload.window_end),
                source_ids=tuple(payload.source_ids),
                runtime_mode=runtime_mode,
            )
    finally:
        if owns_engine:
            await active_engine.dispose()
    return PremarketBriefJobResult(
        schema_version="1.0",
        job_id=request.job_id,
        status=JobStatus.DEGRADED if stored.reason_codes else JobStatus.SUCCEEDED,
        started_at=observed_at,
        completed_at=max(observed_at, utc_now()),
        attempt=request.attempt,
        result=PremarketBriefTaskResult(
            schema_version="1.0",
            active_target=payload.active_target,
            window_start=payload.window_start,
            window_end=payload.window_end,
            briefing_id=stored.briefing.briefing_id,
            generated_at=stored.briefing.generated_at,
            event_count=stored.event_count,
            evidence_count=stored.evidence_count,
            data_manifest_hash=stored.manifest_hash,
            reason_codes=list(stored.reason_codes),
            idempotent_replay=stored.idempotent_replay,
        ),
    )


__all__ = [
    "execute_premarket_brief",
    "execute_premarket_ingest_v1_2",
    "free_live_runner",
    "require_v1_2_runtime",
    "reset_free_live_runner_cache",
    "runtime_mode_for_brief",
]
