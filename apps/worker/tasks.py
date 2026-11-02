"""Thin Celery adapters for the fixture-driven R1 task boundary."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Coroutine
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Final, Never, cast
from uuid import UUID

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from minio.error import S3Error
from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from apps.runtime.premarket import _fetch_free_live as _shared_fetch_free_live
from apps.runtime.premarket import (
    execute_premarket_brief as _shared_execute_premarket_brief_async,
)
from apps.runtime.premarket import (
    execute_premarket_ingest_v1_2 as _shared_execute_premarket_ingest_v1_2_async,
)
from apps.runtime.premarket import (
    require_v1_2_runtime as _shared_require_v1_2_runtime,
)
from apps.runtime.premarket import (
    runtime_mode_for_brief as _shared_runtime_mode_for_brief,
)
from apps.worker.celery_app import celery_app
from apps.worker.task_names import TASK_NAMES
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.config.settings import Settings
from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.behavior import (
    BehaviorComputeRequest,
    BehaviorPressureQuery,
    HistoricalAnalogCandidateQuery,
    HistoricalAnalogState,
    HistoricalAnalogSummary,
    PressureClusterName,
    PressureMode,
)
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.global_events import (
    GlobalEventCatalogSlice,
    GlobalEventCatalogStatus,
    PolicyRefreshRequest,
    PolicyRefreshResult,
)
from stock_assistant.contracts.ingestion import (
    FetchBatch,
    FetchRequest,
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.jobs import JobFailure, JobStatus
from stock_assistant.contracts.market_data import MarketDataRevisionQuery
from stock_assistant.contracts.persistence import MarketEventQueryV1_1
from stock_assistant.contracts.premarket_jobs import (
    PremarketBriefJobRequest,
    PremarketBriefJobResult,
    PremarketIngestJobRequest,
    PremarketIngestJobRequestV1_1,
    PremarketIngestJobRequestV1_2,
    PremarketIngestJobResult,
    PremarketIngestJobResultV1_1,
    PremarketIngestJobResultV1_2,
    PremarketIngestTaskResult,
    PremarketIngestTaskResultV1_1,
    PremarketIngestTaskResultV1_2,
)
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.contracts.research import (
    ResearchCheckpoint,
    ResearchCheckpointWriteRequest,
    ResearchCutoffMode,
    ResearchEvidencePacket,
    ResearchGenerateResult,
    ResearchGenerateTaskRequest,
    ResearchRealSourceMode,
    ResearchReport,
    ResearchReportWriteRequest,
    ResearchResultCompleteness,
    ResearchSessionLookupRequest,
    ResearchSessionLookupResult,
    ResearchSessionStatus,
    ResearchStage,
    ResearchStageTransitionRequest,
    effective_research_cutoff_mode,
    parse_research_generate_request,
)
from stock_assistant.contracts.storage import (
    ObjectAttribute,
    ObjectDeleteRequest,
    ObjectWriteRequest,
    RetentionClass,
    StoredObjectRef,
)
from stock_assistant.modules.behavior.analogs import (
    AnalogObservation,
    behavior_analog_vector,
    select_analogs,
)
from stock_assistant.modules.behavior.dev_reference import validated_dev_only_behavior_inputs
from stock_assistant.modules.behavior.scoring import (
    BehaviorScoreResult,
    compose_behavior_pressure,
)
from stock_assistant.modules.behavior.service import (
    BehaviorPressureApplicationService,
    materialize_snapshot,
)
from stock_assistant.modules.ingestion.r1_fixture import (
    FIXTURE_RULESET_VERSION,
    FixtureBatch,
    fixture_batch_for_request,
    require_fixture_brief_request,
)
from stock_assistant.modules.ingestion.r1_sources import (
    DEMO_REPLAY_ADAPTER_VERSION,
    DEMO_REPLAY_RIGHTS_HASH,
    DEMO_REPLAY_SOURCE_IDS,
    R1SourceBatch,
    ReplayScenario,
    build_demo_replay_batch,
    build_demo_replay_batch_v1_2,
    r1_source_manifest_hash,
    source_batch_from_fetch,
    terminal_gap_batch,
)
from stock_assistant.modules.portfolio.active_target import (
    R1_INSTRUMENT,
    R1_WATCHLIST_ID,
    FixedActiveTargetService,
)
from stock_assistant.modules.research.external_events import enrich_packet_with_global_events
from stock_assistant.modules.research.reporting import (
    GENERATOR_VERSION,
    VALIDATOR_VERSION,
    ResearchGenerationError,
    ResearchReportGenerator,
)
from stock_assistant.modules.research.web_fallback import (
    ResearchWebFallbackCoordinator,
    empty_real_evidence_packet,
    enabled_web_domains,
    enabled_web_rights_snapshots,
    mark_web_fallback_unavailable,
)
from stock_assistant.platform.db.behavior import (
    BehaviorRepositoryError,
    PersistedAnalogObservation,
    PostgresBehaviorOutboxPublisher,
    PostgresBehaviorPressureRepository,
    PostgresMarketDataRepository,
)
from stock_assistant.platform.db.market_events import (
    MarketEventRepositoryError,
    PostgresMarketEventRepository,
)
from stock_assistant.platform.db.portfolio import PostgresPortfolioRepository
from stock_assistant.platform.db.r1_premarket import (
    PostgresR1PremarketRepository,
)
from stock_assistant.platform.db.recovery import PostgresUnitOfWork
from stock_assistant.platform.db.research import (
    PostgresResearchRepository,
    ResearchRepositoryError,
    ResearchTransitionConflictError,
)
from stock_assistant.platform.db.research_web_retrieval import (
    PostgresResearchWebRetrievalReceiptRepository,
)
from stock_assistant.platform.discovery.cninfo import CninfoSourceDiscovery
from stock_assistant.platform.discovery.tavily import TavilyDiscoveryError, TavilySourceDiscovery
from stock_assistant.platform.llm.deepseek import DeepSeekProviderError, build_deepseek_provider
from stock_assistant.platform.llm.synthetic_research import SyntheticResearchLLMProvider
from stock_assistant.platform.object_store.minio_store import MinioObjectStore
from stock_assistant.platform.object_store.s3_store import S3ObjectStore
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet
from stock_assistant.platform.runtime import database_url_from_environment
from stock_assistant.platform.sources.akshare_runner import (
    AkshareRunnerError,
    AkshareSubprocessRunner,
    normalize_akshare_reason_code,
)
from stock_assistant.platform.sources.free_source_runner import (
    FreeSourceRunnerError,
    FreeSourceSubprocessRunner,
)
from stock_assistant.platform.sources.governed_web import (
    DevelopmentResearchWebRightsPolicy,
    GovernedWebSourceConnector,
    GovernedWebSourceError,
)
from stock_assistant.platform.sources.r1_dev_live import (
    DEV_LIVE_ADAPTER_VERSION,
    DEV_LIVE_LICENSE_ID,
    DEV_LIVE_RIGHTS_HASH,
    DEV_LIVE_SOURCE_IDS,
    AkshareDevLiveAdapter,
    DevelopmentMetadataRightsPolicy,
    RightsGatedAkshareConnector,
    dev_live_macro_gap,
)
from stock_assistant.platform.sources.r1_free_live import (
    FreeDevelopmentRightsPolicy,
    FreeLiveAdapter,
    RightsGatedFreeConnector,
    free_source_gap,
)
from stock_assistant.platform.sources.research_preview_runner import (
    RESEARCH_PREVIEW_SOURCE_IDS,
)
from stock_assistant.ports.infrastructure import ObjectStorePort
from stock_assistant.ports.intelligence import LLMProviderError, LLMProviderPort

WireRequest = dict[str, JsonValue]
WireResult = dict[str, JsonValue]
_STABLE_REASON_CODE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_R1_TARGET_BOOTSTRAP_AT: Final = datetime(2026, 7, 25, 0, 55, tzinfo=UTC)
_R1_TARGET_BOOTSTRAP_REQUEST_ID: Final = UUID("9548ea44-c879-57de-9434-01780edb1a56")
_R1_TARGET_BOOTSTRAP_CORRELATION_ID: Final = UUID("b922bb7e-aa15-5d19-a8df-c24623e8e2e8")
_LOGGER = logging.getLogger(__name__)


def _research_web_failure_details(error: Exception) -> tuple[str, bool]:
    if isinstance(error, (DeepSeekProviderError, TavilyDiscoveryError, GovernedWebSourceError)):
        return error.code, error.retryable
    if isinstance(error, httpx.ProxyError):
        return "RESEARCH_WEB_EGRESS_PROXY_REJECTED", False
    if isinstance(error, httpx.TransportError):
        return "RESEARCH_WEB_TRANSPORT_ERROR", True
    if isinstance(error, httpx.HTTPError):
        return "RESEARCH_WEB_HTTP_ERROR", False
    if isinstance(error, OSError):
        return "RESEARCH_WEB_TRANSPORT_ERROR", True
    return "RESEARCH_WEB_VALIDATION_FAILED", False


def _log_research_web_fallback_failure(*, correlation_id: UUID, error: Exception) -> None:
    failure_code, retryable = _research_web_failure_details(error)
    retryable_text = str(retryable).lower()
    _LOGGER.warning(
        "research_web_fallback_unavailable task_type=research_web_fallback "
        "correlation_id=%s failure_code=%s retryable=%s",
        correlation_id,
        failure_code,
        retryable_text,
        extra={
            "event": "research_web_fallback_unavailable",
            "task_type": "research_web_fallback",
            "correlation_id": str(correlation_id),
            "failure_code": failure_code,
            "retryable": retryable,
        },
    )


class TaskDisabledError(RuntimeError):
    """Stable fail-closed error for a registered task outside its delivery stage."""

    reason_code = "FEATURE_NOT_ENABLED_FOR_STAGE"


def _raise_disabled(operation: str, stage: str) -> Never:
    raise TaskDisabledError(
        f"{TaskDisabledError.reason_code}: {operation} remains disabled until {stage}"
    )


def _wire(result: ContractModel) -> WireResult:
    """Serialize an already validated result for Celery's JSON boundary."""

    return cast("WireResult", result.model_dump(mode="json"))


def _r1_target_initialization_context() -> MutationContext:
    """Return the v2 canonical write identity used only for an empty repository."""

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


async def _ensure_r1_target_initialized(
    service: FixedActiveTargetService,
    *,
    observed_at: datetime,
) -> None:
    """Reuse any visible legacy target before attempting the canonical v2 creation."""

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
    if state.found:
        return
    await service.initialize(_r1_target_initialization_context())


def _run[ResultT](coroutine: Coroutine[object, object, ResultT]) -> ResultT:
    """Run one bounded async adapter call in a synchronous Celery worker slot."""

    return asyncio.run(coroutine)


def _require_v1_1_runtime(request: PremarketIngestJobRequestV1_1) -> None:
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
    if payload.runtime_mode is SourceRuntimeMode.AUTHORIZED:
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "AUTHORIZED_CONNECTORS_NOT_STARTED")
    if payload.runtime_mode is SourceRuntimeMode.DEMO_REPLAY:
        if not settings.enable_r1_fixture:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_DISABLED")
        expected_source = DEMO_REPLAY_SOURCE_IDS.get(payload.capability)
        valid = (
            settings.r1_runtime_mode is SourceRuntimeMode.DEMO_REPLAY
            and expected_source == payload.source_id
            and payload.source_adapter_version == DEMO_REPLAY_ADAPTER_VERSION
            and payload.rights_snapshot_hash == DEMO_REPLAY_RIGHTS_HASH
        )
    else:
        if not settings.enable_r1_dev_live:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_DEV_LIVE_DISABLED")
        expected_source = DEV_LIVE_SOURCE_IDS.get(payload.capability)
        valid = (
            settings.r1_runtime_mode is SourceRuntimeMode.DEV_LIVE
            and expected_source == payload.source_id
            and payload.source_adapter_version == DEV_LIVE_ADAPTER_VERSION
            and payload.rights_snapshot_hash == DEV_LIVE_RIGHTS_HASH
        )
    if not valid:
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "R1_SOURCE_PROFILE_MISMATCH")


def _require_v1_2_runtime(request: PremarketIngestJobRequestV1_2) -> None:
    _shared_require_v1_2_runtime(request)


def _gap_status(error: Exception) -> SourceCoverageStatus:
    if isinstance(error, ApplicationError) and error.code is ErrorCode.RIGHTS_BLOCKED:
        return SourceCoverageStatus.RIGHTS_BLOCKED
    if isinstance(error, AkshareRunnerError) and error.reason_code == "AKSHARE_RATE_LIMITED":
        return SourceCoverageStatus.DELAYED
    return SourceCoverageStatus.UNAVAILABLE


def _reason_code(error: Exception) -> str:
    if isinstance(error, AkshareRunnerError):
        return normalize_akshare_reason_code(error.reason_code)
    if isinstance(error, ApplicationError):
        if _STABLE_REASON_CODE.fullmatch(error.safe_message):
            return error.safe_message
        fallback = error.code.value
        return fallback if _STABLE_REASON_CODE.fullmatch(fallback) else "R1_SOURCE_UNAVAILABLE"
    return "R1_SOURCE_UNAVAILABLE"


def _free_reason_code(error: Exception) -> str:
    if isinstance(error, FreeSourceRunnerError):
        return error.reason_code
    return _reason_code(error)


def _free_gap_status(error: Exception) -> SourceCoverageStatus:
    if isinstance(error, ApplicationError):
        if error.code is ErrorCode.RIGHTS_BLOCKED:
            return SourceCoverageStatus.RIGHTS_BLOCKED
        if error.safe_message == "FREE_SOURCE_NOT_QUALIFIED":
            return SourceCoverageStatus.NOT_QUALIFIED
    if isinstance(error, FreeSourceRunnerError) and error.reason_code == "FREE_SOURCE_RATE_LIMITED":
        return SourceCoverageStatus.DELAYED
    return SourceCoverageStatus.UNAVAILABLE


@lru_cache(maxsize=1)
def _dev_live_runner() -> AkshareSubprocessRunner:
    """Return the breaker-bearing runner local to this Celery worker process."""

    return AkshareSubprocessRunner()


def _reset_dev_live_runner_cache() -> None:
    """Reset process-local runner state after worker restart/config change or in tests."""

    _dev_live_runner.cache_clear()


@lru_cache(maxsize=1)
def _free_live_runner() -> FreeSourceSubprocessRunner:
    return FreeSourceSubprocessRunner()


def _reset_free_live_runner_cache() -> None:
    _free_live_runner.cache_clear()


async def _fetch_dev_live(request: PremarketIngestJobRequestV1_1) -> FetchBatch:
    payload = request.payload
    adapter = AkshareDevLiveAdapter(payload.capability)
    connector = RightsGatedAkshareConnector(
        adapter=adapter,
        rights_policy=DevelopmentMetadataRightsPolicy(),
        runner=_dev_live_runner(),
    )
    return await connector.fetch(
        FetchRequest(
            schema_version="1.0",
            context=request.context,
            source_id=payload.source_id,
            capability=payload.capability,
            rights_snapshot_hash=payload.rights_snapshot_hash,
            since=payload.window_start,
            until=payload.window_end,
            instrument_ids=[payload.active_target.instrument.instrument_id],
            limit=payload.max_records,
        )
    )


async def _fetch_free_live(
    request: PremarketIngestJobRequestV1_2,
    *,
    provider_cursor: str | None = None,
) -> FetchBatch:
    """Compatibility test seam; production V1.2 execution lives in ``apps.runtime``."""

    payload = request.payload
    if payload.source_id in RESEARCH_PREVIEW_SOURCE_IDS.values():
        return await _shared_fetch_free_live(request, provider_cursor=provider_cursor)
    adapter = FreeLiveAdapter(payload.source_id)
    connector = RightsGatedFreeConnector(
        adapter=adapter,
        rights_policy=FreeDevelopmentRightsPolicy(),
        runner=_free_live_runner(),
    )
    return await connector.fetch(
        FetchRequest(
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
    )


def _identity_batch(request: PremarketIngestJobRequestV1_1) -> R1SourceBatch:
    payload = request.payload
    if payload.runtime_mode is SourceRuntimeMode.DEMO_REPLAY:
        settings = get_settings()
        configured_capability = getattr(settings, "r1_demo_replay_capability", None)
        scenario = (
            ReplayScenario(getattr(settings, "r1_demo_replay_scenario", "success"))
            if configured_capability == payload.capability
            else ReplayScenario.SUCCESS
        )
        return build_demo_replay_batch(
            capability=payload.capability,
            scenario=scenario,
            window_start=payload.window_start,
            window_end=payload.window_end,
            instrument_id=payload.active_target.instrument.instrument_id,
        )
    if payload.capability == "macro.flashes":
        reason, status, provenance = dev_live_macro_gap()
    else:
        provenance = AkshareDevLiveAdapter(payload.capability).provenance
        reason = "R1_DEV_LIVE_PENDING"
        status = SourceCoverageStatus.TIMELINESS_UNVERIFIED
    return terminal_gap_batch(
        source_id=payload.source_id,
        capability=payload.capability,
        runtime_mode=payload.runtime_mode,
        provenance=provenance,
        rights_snapshot_hash=payload.rights_snapshot_hash,
        license_id=DEV_LIVE_LICENSE_ID,
        observed_at=payload.window_end,
        status=status,
        reason_code=reason,
    )


async def _execute_premarket_ingest_async(
    request: PremarketIngestJobRequest,
    observed_at: datetime,
    batch: FixtureBatch,
) -> PremarketIngestJobResult:
    payload = request.payload
    settings = get_settings()
    engine = create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    try:
        repository = PostgresR1PremarketRepository(engine)
        lease = await repository.acquire_fixture_lease(
            batch=batch,
            owner=f"{request.job_id}:{request.attempt}",
        )
        active_target_service = FixedActiveTargetService(PostgresPortfolioRepository(engine))
        await _ensure_r1_target_initialized(
            active_target_service,
            observed_at=observed_at,
        )
        failure_source = getattr(settings, "r1_fixture_fail_source", None)
        if failure_source == batch.source_id:
            reason_code = "R1_FIXTURE_SOURCE_UNAVAILABLE"
            await repository.record_fixture_failure(
                batch=batch,
                window_start=payload.window_start,
                window_end=payload.window_end,
                lease=lease,
                reason_code=reason_code,
            )
            completed_at = max(observed_at, utc_now())
            return PremarketIngestJobResult(
                schema_version="1.0",
                job_id=request.job_id,
                status=JobStatus.FAILED,
                started_at=observed_at,
                completed_at=completed_at,
                attempt=request.attempt,
                failure=JobFailure(
                    reason_code=reason_code,
                    safe_message="R1 synthetic source is intentionally unavailable",
                    retryable=False,
                    failed_at=completed_at,
                ),
            )
        stored = await repository.persist_fixture_batch(
            batch=batch,
            window_start=payload.window_start,
            window_end=payload.window_end,
            lease=lease,
        )
    finally:
        await engine.dispose()
    status = JobStatus.SKIPPED if stored.reason_code is not None else JobStatus.SUCCEEDED
    return PremarketIngestJobResult(
        schema_version="1.0",
        job_id=request.job_id,
        status=status,
        started_at=observed_at,
        completed_at=max(observed_at, utc_now()),
        attempt=request.attempt,
        result=PremarketIngestTaskResult(
            schema_version="1.0",
            active_target=payload.active_target,
            source_id=payload.source_id,
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


def _execute_premarket_ingest(
    request: PremarketIngestJobRequest,
    observed_at: datetime,
    batch: FixtureBatch,
) -> PremarketIngestJobResult:
    return _run(_execute_premarket_ingest_async(request, observed_at, batch))


async def _execute_premarket_ingest_v1_1_async(
    request: PremarketIngestJobRequestV1_1,
    observed_at: datetime,
) -> PremarketIngestJobResultV1_1:
    payload = request.payload
    identity_batch = _identity_batch(request)
    settings = get_settings()
    engine = create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    try:
        repository = PostgresR1PremarketRepository(engine)
        lease = await repository.acquire_source_lease(
            batch=identity_batch,
            owner=f"{request.job_id}:{request.attempt}",
        )
        await _ensure_r1_target_initialized(
            FixedActiveTargetService(PostgresPortfolioRepository(engine)),
            observed_at=observed_at,
        )
        if payload.runtime_mode is SourceRuntimeMode.DEMO_REPLAY:
            batch = identity_batch
            coverage = batch.coverages[0]
            if coverage.status is not SourceCoverageStatus.OK:
                reason_code = coverage.gap_reason or "R1_REPLAY_SOURCE_FAILURE"
                await repository.record_source_failure(
                    batch=batch,
                    window_end=payload.window_end,
                    lease=lease,
                    reason_code=reason_code,
                )
                return _degraded_v1_1_result(
                    request=request,
                    observed_at=observed_at,
                    batch=batch,
                    reason_code=reason_code,
                )
        elif payload.capability == "macro.flashes":
            reason_code = identity_batch.coverages[0].gap_reason or "DEV_LIVE_MACRO_NOT_QUALIFIED"
            await repository.record_source_failure(
                batch=identity_batch,
                window_end=payload.window_end,
                lease=lease,
                reason_code=reason_code,
            )
            return _degraded_v1_1_result(
                request=request,
                observed_at=observed_at,
                batch=identity_batch,
                reason_code=reason_code,
            )
        else:
            try:
                fetched = await _fetch_dev_live(request)
                batch = source_batch_from_fetch(
                    fetch_batch=fetched,
                    capability=payload.capability,
                    provenance=AkshareDevLiveAdapter(payload.capability).provenance,
                    license_id=DEV_LIVE_LICENSE_ID,
                )
            except (ApplicationError, AkshareRunnerError) as error:
                adapter = AkshareDevLiveAdapter(payload.capability)
                reason_code = _reason_code(error)
                gap = terminal_gap_batch(
                    source_id=payload.source_id,
                    capability=payload.capability,
                    runtime_mode=payload.runtime_mode,
                    provenance=adapter.provenance,
                    rights_snapshot_hash=payload.rights_snapshot_hash,
                    license_id=DEV_LIVE_LICENSE_ID,
                    observed_at=payload.window_end,
                    status=_gap_status(error),
                    reason_code=reason_code,
                )
                await repository.record_source_failure(
                    batch=gap,
                    window_end=payload.window_end,
                    lease=lease,
                    reason_code=reason_code,
                )
                return _degraded_v1_1_result(
                    request=request,
                    observed_at=observed_at,
                    batch=gap,
                    reason_code=reason_code,
                )
        stored = await repository.persist_source_batch(
            batch=batch,
            window_start=payload.window_start,
            window_end=payload.window_end,
            lease=lease,
        )
    finally:
        await engine.dispose()
    status = JobStatus.SKIPPED if stored.reason_code is not None else JobStatus.SUCCEEDED
    return PremarketIngestJobResultV1_1(
        schema_version="1.1",
        job_id=request.job_id,
        status=status,
        started_at=observed_at,
        completed_at=max(observed_at, utc_now()),
        attempt=request.attempt,
        result=PremarketIngestTaskResultV1_1(
            schema_version="1.1",
            active_target=payload.active_target,
            source_id=payload.source_id,
            capability=payload.capability,
            runtime_mode=payload.runtime_mode,
            provenance=cast("SourceProvenance", batch.provenance),
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


def _degraded_v1_1_result(
    *,
    request: PremarketIngestJobRequestV1_1,
    observed_at: datetime,
    batch: R1SourceBatch,
    reason_code: str,
) -> PremarketIngestJobResultV1_1:
    return PremarketIngestJobResultV1_1(
        schema_version="1.1",
        job_id=request.job_id,
        status=JobStatus.DEGRADED,
        started_at=observed_at,
        completed_at=max(observed_at, utc_now()),
        attempt=request.attempt,
        result=PremarketIngestTaskResultV1_1(
            schema_version="1.1",
            active_target=request.payload.active_target,
            source_id=request.payload.source_id,
            capability=request.payload.capability,
            runtime_mode=request.payload.runtime_mode,
            provenance=cast("SourceProvenance", batch.provenance),
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


def _execute_premarket_ingest_v1_1(
    request: PremarketIngestJobRequestV1_1,
    observed_at: datetime,
) -> PremarketIngestJobResultV1_1:
    return _run(_execute_premarket_ingest_v1_1_async(request, observed_at))


def _identity_batch_v1_2(request: PremarketIngestJobRequestV1_2) -> R1SourceBatch:
    payload = request.payload
    if payload.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY:
        settings = get_settings()
        configured_capability = getattr(settings, "r1_demo_replay_capability", None)
        scenario = (
            ReplayScenario(getattr(settings, "r1_demo_replay_scenario", "success"))
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


def _degraded_v1_2_result(
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


async def _execute_premarket_ingest_v1_2_async(
    request: PremarketIngestJobRequestV1_2,
    observed_at: datetime,
) -> PremarketIngestJobResultV1_2:
    return await _shared_execute_premarket_ingest_v1_2_async(request, observed_at)


def _execute_premarket_ingest_v1_2(
    request: PremarketIngestJobRequestV1_2,
    observed_at: datetime,
) -> PremarketIngestJobResultV1_2:
    return _run(_execute_premarket_ingest_v1_2_async(request, observed_at))


async def _execute_premarket_brief_async(
    request: PremarketBriefJobRequest,
    observed_at: datetime,
) -> PremarketBriefJobResult:
    return await _shared_execute_premarket_brief_async(request, observed_at)


def _execute_premarket_brief(
    request: PremarketBriefJobRequest,
    observed_at: datetime,
) -> PremarketBriefJobResult:
    return _run(_execute_premarket_brief_async(request, observed_at))


def _runtime_mode_for_brief(
    source_ids: tuple[str, ...],
) -> SourceRuntimeMode | SourceRuntimeModeV1_2 | None:
    return _shared_runtime_mode_for_brief(source_ids)


def _analog_observation(item: PersistedAnalogObservation) -> AnalogObservation:
    return AnalogObservation(
        assessment_id=item.assessment_id,
        instrument_id=item.instrument_id,
        trading_day_index=item.trading_day_index,
        as_of=item.as_of,
        assessment_revision_known_at=item.assessment_revision_known_at,
        label_available_at=item.label_available_at,
        vector=item.vector,
        outcomes=item.outcomes,
    )


async def _load_post_close_analogs(
    *,
    repository: PostgresBehaviorPressureRepository,
    request: BehaviorComputeRequest,
    score_result: BehaviorScoreResult,
) -> HistoricalAnalogSummary:
    vector = behavior_analog_vector(score_result)
    try:
        candidates = await repository.query_analog_candidates(
            HistoricalAnalogCandidateQuery(
                schema_version="1.0",
                context=request.context,
                selection_as_of=request.context.as_of,
                vector=list(vector),
                limit=400,
            )
        )
        persisted = await repository.load_analog_observations(
            assessment_ids=[item.assessment_id for item in candidates.items],
            selection_as_of=request.context.as_of,
        )
        return select_analogs(
            query_vector=vector,
            observations=[_analog_observation(item) for item in persisted],
            selection_as_of=request.context.as_of,
            selection_semantics_hash=request.diagnostic_semantics_hash,
        ).summary
    except (BehaviorRepositoryError, SQLAlchemyError, ValueError):
        return HistoricalAnalogSummary(
            state=HistoricalAnalogState.FAILED,
            sample_count=0,
            reason_code="ANALOG_QUERY_FAILED",
            reason="历史对照暂时无法完成。",
            selection_as_of=request.context.as_of,
            selection_semantics_hash=request.diagnostic_semantics_hash,
        )


def _behavior_market_revision_query(
    request: BehaviorComputeRequest,
) -> MarketDataRevisionQuery:
    """Read close-stamped bars from the half-open behavior window ``(start, end]``."""

    return MarketDataRevisionQuery(
        schema_version="1.0",
        context=request.context,
        instrument_ids=[request.instrument_id],
        effective_from=request.window_start + timedelta(microseconds=1),
        effective_until=request.window_end,
        limit=10_000,
    )


async def _execute_behavior_compute_async(
    request: BehaviorComputeRequest,
    *,
    engine: AsyncEngine | None = None,
    maintain_transition_state: bool = True,
) -> ContractModel:
    """Materialize a truthful score or an explicit abstention from visible PIT inputs."""

    settings = get_settings()
    if settings.app_env == "production" or not (
        settings.enable_r1_fixture or settings.enable_r1_dev_live
    ):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "DEV_ONLY_BEHAVIOR_DISABLED",
        )
    owns_engine = engine is None
    active_engine = engine or create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    try:
        market_repository = PostgresMarketDataRepository(active_engine)
        page = await market_repository.query_revisions(_behavior_market_revision_query(request))
        # R1 demonstration scoring is allowed only when an explicit DEV_ONLY market
        # revision set matches the task's manifest and authorization lineage.
        dev_inputs = validated_dev_only_behavior_inputs(request=request, page=page)
        inherited = (
            frozenset(
                {
                    PressureClusterName.REFERENCE_COST_EXTENSION,
                    PressureClusterName.SHORT_PATH_EXTENSION,
                    PressureClusterName.PRICE_IMPACT,
                    PressureClusterName.SPREAD_PROXY,
                    PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY,
                }
            )
            if request.mode is PressureMode.INTRADAY_10M
            else frozenset()
        )
        behavior_repository = PostgresBehaviorPressureRepository(active_engine)
        cluster_scores: dict[PressureClusterName, int | None] = (
            dict(dev_inputs.cluster_scores)
            if dev_inputs is not None
            else {name: None for name in PressureClusterName}
        )
        if request.mode is PressureMode.INTRADAY_10M and dev_inputs is not None:
            previous = await behavior_repository.query_assessments(
                BehaviorPressureQuery(
                    schema_version="1.0",
                    context=request.context,
                    mode=PressureMode.POST_CLOSE,
                    limit=1,
                )
            )
            compatible = next(
                (
                    item
                    for item in previous.items
                    if item.pit_lineage.comparison_semantics_hash
                    == request.diagnostic_semantics_hash
                ),
                None,
            )
            previous_scores = (
                {item.cluster: item.score for item in compatible.clusters}
                if compatible is not None
                else {}
            )
            for name in inherited:
                cluster_scores[name] = previous_scores.get(name)
        score_result = (
            compose_behavior_pressure(
                cluster_scores=cluster_scores,
                reference_raw_totals=dev_inputs.reference_raw_totals,
                reference_population_hash=dev_inputs.reference_population_hash,
                inherited_clusters=inherited,
            )
            if dev_inputs is not None
            else compose_behavior_pressure(
                cluster_scores=cluster_scores,
                reference_raw_totals=(),
                reference_population_hash=request.data_manifest_hash,
                inherited_clusters=inherited,
            )
        )
        max_known_at = max(
            (item.revision_known_at for item in page.items),
            default=request.window_end,
        )
        historical_analogs = (
            await _load_post_close_analogs(
                repository=behavior_repository,
                request=request,
                score_result=score_result,
            )
            if request.mode is PressureMode.POST_CLOSE and score_result.score is not None
            else None
        )
        snapshot = materialize_snapshot(
            request=request,
            score_result=score_result,
            max_input_revision_known_at=max_known_at,
            historical_analogs=historical_analogs,
        )
        unit_of_work = PostgresUnitOfWork(active_engine)
        async with unit_of_work:
            service = BehaviorPressureApplicationService(
                PostgresBehaviorPressureRepository(unit_of_work.connection),
                PostgresBehaviorOutboxPublisher(unit_of_work),
            )
            stored = await service.persist(
                request=request,
                snapshot=snapshot,
                trading_date=request.window_end.date(),
                maintain_transition_state=maintain_transition_state,
            )
            await unit_of_work.commit()
        return stored
    finally:
        if owns_engine:
            await active_engine.dispose()


async def execute_behavior_compute_runtime(
    request: BehaviorComputeRequest,
    *,
    engine: AsyncEngine,
    maintain_transition_state: bool = True,
) -> ContractModel:
    """Run the behavior use case inside an already configured runtime process."""

    return await _execute_behavior_compute_async(
        request,
        engine=engine,
        maintain_transition_state=maintain_transition_state,
    )


def _research_mutation(
    request: ResearchGenerateTaskRequest,
    *,
    now: datetime,
    key_suffix: str,
    preserve_deadline: bool = False,
) -> MutationContext:
    return request.context.model_copy(
        update={
            "as_of": now,
            "requested_at": now,
            "deadline_at": request.context.deadline_at if preserve_deadline else None,
            "idempotency_key": f"research-{request.job_id}-{key_suffix}",
        }
    )


async def _research_transition(
    repository: PostgresResearchRepository,
    request: ResearchGenerateTaskRequest,
    *,
    status: ResearchSessionStatus,
    stage: ResearchStage,
    message: str,
    checkpoint: str | None,
    reason_code: str | None = None,
    retryable: bool = False,
    result_completeness: ResearchResultCompleteness | None = None,
) -> ResearchSessionLookupResult:
    now = utc_now()
    result = await repository.transition(
        ResearchStageTransitionRequest(
            schema_version="1.0",
            context=_research_mutation(
                request,
                now=now,
                key_suffix=stage.value,
                preserve_deadline=status is ResearchSessionStatus.SUCCEEDED,
            ),
            session_id=request.session_id,
            job_id=request.job_id,
            expected_statuses=[ResearchSessionStatus.QUEUED, ResearchSessionStatus.RUNNING],
            status=status,
            stage=stage,
            message=message,
            checkpoint=checkpoint,
            reason_code=reason_code,
            retryable=retryable,
            result_completeness=result_completeness,
        )
    )
    if not result.found or result.session is None:
        raise ResearchTransitionConflictError("research job is no longer visible or active")
    return result


async def _research_no_longer_active_result(
    repository: PostgresResearchRepository,
    request: ResearchGenerateTaskRequest,
    *,
    completed_at: datetime,
) -> ResearchGenerateResult:
    lookup = await repository.get(
        ResearchSessionLookupRequest(
            schema_version="1.0",
            context=OperationContext(
                schema_version="1.0",
                request_id=request.context.request_id,
                correlation_id=request.context.correlation_id,
                actor_id=request.context.actor_id,
                workspace_id=request.context.workspace_id,
                as_of=completed_at,
                requested_at=completed_at,
            ),
            session_id=request.session_id,
        )
    )
    status = (
        lookup.session.status
        if lookup.found and lookup.session is not None
        else ResearchSessionStatus.FAILED
    )
    return ResearchGenerateResult(
        schema_version="1.0",
        session_id=request.session_id,
        job_id=request.job_id,
        status=status,
        completed_at=completed_at,
        reason_code="RESEARCH_JOB_NO_LONGER_ACTIVE",
    )


async def _single_chunk(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


async def _execute_policy_refresh(
    request: PolicyRefreshRequest,
    *,
    engine: AsyncEngine | None = None,
) -> PolicyRefreshResult:
    """Refresh only the stock-agnostic synthetic global event catalog."""

    settings = get_settings()
    settings.assert_safe_production()
    if settings.app_env == "production" or not settings.enable_r2_synthetic_research:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R2_GLOBAL_EVENT_SYNTHETIC_REFRESH_DISABLED",
        )
    owns_engine = engine is None
    active_engine = engine or create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    try:
        return await PostgresMarketEventRepository(active_engine).refresh_synthetic_catalog(request)
    finally:
        if owns_engine:
            await active_engine.dispose()


def _research_object_store(settings: Settings) -> ObjectStorePort:
    if settings.enable_r2_private_preview:
        import boto3
        from botocore.config import Config

        if settings.r2_artifact_bucket is None or settings.product_commit is None:
            raise ValueError("R2_PREVIEW_OBJECT_STORE_NOT_CONFIGURED")
        client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 3, "mode": "standard"},
                s3={"addressing_style": "virtual"},
            ),
        )
        return S3ObjectStore(
            client,
            bucket=settings.r2_artifact_bucket,
            product_sha=settings.product_commit,
        )
    from minio import Minio

    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_secure,
    )
    return MinioObjectStore(client, bucket=settings.minio_artifact_bucket)


def _research_llm_provider(
    settings: Settings, repository: PostgresResearchRepository
) -> LLMProviderPort:
    if settings.enable_r2_deepseek_research:
        if settings.deepseek_api_key is None:
            raise ResearchGenerationError(
                "MODEL_CONFIGURATION_INVALID",
                failure_stage=ResearchStage.DEBATING_CASES,
            )
        try:
            return build_deepseek_provider(
                api_key=settings.deepseek_api_key,
                model=settings.deepseek_pro_model,
                base_url=settings.deepseek_base_url,
                permit=lambda seconds: repository.provider_permit(
                    provider="deepseek",
                    limit=settings.r2_deepseek_concurrency,
                    deadline_at=utc_now() + timedelta(seconds=seconds),
                ),
            )
        except LLMProviderError as error:
            raise ResearchGenerationError(
                "MODEL_CONFIGURATION_INVALID",
                failure_stage=ResearchStage.DEBATING_CASES,
            ) from error
    return SyntheticResearchLLMProvider()


async def _compensate_research_artifact(
    *,
    object_store: ObjectStorePort | None,
    artifact_ref: StoredObjectRef | None,
    request: ResearchGenerateTaskRequest,
    completed_at: datetime,
) -> None:
    if artifact_ref is None or object_store is None:
        return
    try:
        await object_store.delete(
            ObjectDeleteRequest(
                schema_version="1.0",
                context=_research_mutation(
                    request,
                    now=completed_at,
                    key_suffix="artifact-compensation",
                ),
                reference=artifact_ref,
            )
        )
    except (BotoCoreError, ClientError, OSError, S3Error, ValueError):
        _LOGGER.warning(
            "research artifact compensation failed for session_id=%s",
            request.session_id,
        )


def _research_failure_message(reason_code: str) -> str:
    if reason_code == "INSUFFICIENT_EVIDENCE":
        return "证据不足, 暂不能完成研究; 已核实内容仍可查看。"
    if reason_code == "NARRATIVE_SUPPORT_INSUFFICIENT":
        return "尚未形成证据支持的完整多空论证; 已核实内容与具体缺口仍可查看。"
    if "AUTH" in reason_code or "CONFIGURATION" in reason_code:
        return "研究来源或模型授权配置不可用, 请检查配置后重试。"
    if any(
        kind in reason_code
        for kind in ("DEADLINE", "UNAVAILABLE", "TRANSPORT", "TIMEOUT", "RATE_LIMITED")
    ):
        return "研究执行暂时中断, 可以从已保存的有效进度继续。"
    if reason_code.startswith("MODEL_") or reason_code == "REPORT_VALIDATION_FAILED":
        return "模型输出未能通过内容校验, 已保存的有效内容仍可查看。"
    return "研究执行失败, 已保存的有效进度仍保留。"


async def _execute_research_generate(
    request: ResearchGenerateTaskRequest,
    *,
    engine: AsyncEngine | None = None,
) -> ResearchGenerateResult:
    settings = get_settings()
    settings.assert_safe_production()
    if settings.enable_r2_real_research and not settings.enable_r2_deepseek_research:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REAL_RESEARCH_MODEL_NOT_CONFIGURED")
    if settings.app_env == "production" or not (
        settings.enable_r2_real_research
        or (settings.enable_r2_synthetic_research and settings.app_env != "preview")
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R2_SYNTHETIC_RESEARCH_DISABLED")
    owns_engine = engine is None
    active_engine = engine or create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    repository = PostgresResearchRepository(active_engine)
    lock_connection: AsyncConnection | None = None
    lock_acquired = False
    artifact_ref: StoredObjectRef | None = None
    object_store: ObjectStorePort | None = None
    last_checkpoint: str | None = None
    active_stage = ResearchStage.RESOLVING_INSTRUMENT
    attempt_budget_seconds = (
        max(0.0, (request.context.deadline_at - utc_now()).total_seconds())
        if request.context.deadline_at is not None
        else 1800.0
    )
    try:
        async with asyncio.timeout(attempt_budget_seconds):
            lock_connection = await active_engine.connect()
            lock_acquired = bool(
                await lock_connection.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": f"research-job:{request.job_id}"},
                )
            )
            if not lock_acquired:
                return ResearchGenerateResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=request.job_id,
                    status=ResearchSessionStatus.RUNNING,
                    completed_at=utc_now(),
                    reason_code="RESEARCH_JOB_ALREADY_RUNNING",
                )
            now = utc_now()
            lookup = await repository.get(
                ResearchSessionLookupRequest(
                    schema_version="1.0",
                    context=OperationContext(
                        schema_version="1.0",
                        request_id=request.context.request_id,
                        correlation_id=request.context.correlation_id,
                        actor_id=request.context.actor_id,
                        workspace_id=request.context.workspace_id,
                        as_of=now,
                        requested_at=now,
                    ),
                    session_id=request.session_id,
                )
            )
            if not lookup.found or lookup.session is None:
                return ResearchGenerateResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=request.job_id,
                    status=ResearchSessionStatus.FAILED,
                    completed_at=now,
                    reason_code="RESEARCH_SESSION_NOT_FOUND",
                )
            session = lookup.session
            if settings.enable_r2_real_research != (
                session.source_mode is ResearchRealSourceMode.DEVELOPMENT_ONLY
            ):
                raise ResearchGenerationError("RESEARCH_SOURCE_MODE_MISMATCH")
            if session.job_id != request.job_id or session.status in {
                ResearchSessionStatus.CANCELLED,
                ResearchSessionStatus.DELETED,
                ResearchSessionStatus.EXPIRED,
            }:
                return ResearchGenerateResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=request.job_id,
                    status=session.status,
                    completed_at=now,
                    reason_code="RESEARCH_JOB_NO_LONGER_ACTIVE",
                )
            cutoff_mode = effective_research_cutoff_mode(request)
            if cutoff_mode != lookup.cutoff_mode:
                raise ResearchGenerationError("RESEARCH_TASK_CONTEXT_MISMATCH")
            if request.context.deadline_at is not None and now > request.context.deadline_at:
                raise ResearchGenerationError("RESEARCH_TASK_DEADLINE_EXCEEDED")
            if session.status is ResearchSessionStatus.SUCCEEDED:
                stored = await repository.get_report(
                    ResearchSessionLookupRequest(
                        schema_version="1.0",
                        context=OperationContext(
                            schema_version="1.0",
                            request_id=request.context.request_id,
                            correlation_id=request.context.correlation_id,
                            actor_id=request.context.actor_id,
                            workspace_id=request.context.workspace_id,
                            as_of=now,
                            requested_at=now,
                        ),
                        session_id=request.session_id,
                    )
                )
                return ResearchGenerateResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=request.job_id,
                    status=session.status,
                    report_id=stored.report.report_id if stored.report is not None else None,
                    completed_at=now,
                )
            provider = _research_llm_provider(settings, repository)
            saved = await repository.get_checkpoint(
                ResearchSessionLookupRequest(
                    schema_version="1.0", context=request.context, session_id=request.session_id
                )
            )
            checkpoint = saved.checkpoint
            if checkpoint is not None and (
                checkpoint.schema_version != "1.9"
                or checkpoint.generator_version != GENERATOR_VERSION
                or checkpoint.validator_version not in {None, VALIDATOR_VERSION}
            ):
                active_stage = session.progress.stage
                last_checkpoint = session.progress.checkpoint
                raise ResearchGenerationError("RESEARCH_CHECKPOINT_INCOMPATIBLE")

            async def save_checkpoint(snapshot: ResearchCheckpoint) -> None:
                nonlocal checkpoint
                await repository.save_checkpoint(
                    ResearchCheckpointWriteRequest(
                        schema_version="1.0",
                        context=_research_mutation(
                            request, now=utc_now(), key_suffix="checkpoint", preserve_deadline=True
                        ),
                        session_id=request.session_id,
                        job_id=request.job_id,
                        checkpoint=snapshot,
                    )
                )
                checkpoint = snapshot

            stages = (
                (ResearchStage.RESOLVING_INSTRUMENT, "正在确认证券身份。", "instrument-resolved"),
                (ResearchStage.COLLECTING_HISTORY, "正在回溯历史证据。", "history-collected"),
                (
                    ResearchStage.ANALYZING_EVENTS,
                    "正在匹配重大事件、国际事件与宏观暴露。",
                    "events-analyzed",
                ),
                (
                    ResearchStage.DEBATING_CASES,
                    "正在核对看多、看空与综合判断, 并完成六维评分。",
                    "cases-debated",
                ),
                (
                    ResearchStage.VALIDATING_REPORT,
                    "正在核对报告的证据与内容。",
                    "report-validating",
                ),
            )
            current = lookup

            async def enter_stage(index: int) -> None:
                nonlocal active_stage, current
                stage, message, _ = stages[index]
                active_stage = stage
                current = await _research_transition(
                    repository,
                    request,
                    status=ResearchSessionStatus.RUNNING,
                    stage=stage,
                    message=message,
                    checkpoint=last_checkpoint,
                )

            if (
                checkpoint is not None
                and session.source_mode is not ResearchRealSourceMode.DEVELOPMENT_ONLY
            ):
                packet = checkpoint.packet
                last_checkpoint = "events-analyzed"
            else:
                await enter_stage(0)
                if current.session is None:
                    raise ResearchRepositoryError("research session disappeared during execution")
                if (
                    current.session.instrument_id != session.instrument_id
                    or current.session.instrument_name != session.instrument_name
                ):
                    raise ResearchGenerationError("REPORT_VALIDATION_FAILED")
                last_checkpoint = "instrument-resolved"

                await enter_stage(1)
                if current.session is None:
                    raise ResearchRepositoryError("research session disappeared during execution")
                packet = (
                    empty_real_evidence_packet(current.session)
                    if session.source_mode is ResearchRealSourceMode.DEVELOPMENT_ONLY
                    else synthetic_evidence_packet(
                        instrument_id=current.session.instrument_id,
                        instrument_name=current.session.instrument_name,
                        as_of=current.session.as_of,
                        include_builtin_api_gap=settings.enable_r2_web_fallback,
                    )
                )
                if session.source_mode is ResearchRealSourceMode.DEVELOPMENT_ONLY:
                    if (
                        settings.tavily_api_key is None
                        or settings.deepseek_api_key is None
                        or request.context.deadline_at is None
                    ):
                        raise ResearchGenerationError("MODEL_CONFIGURATION_INVALID")
                    acquisition_deadline = request.context.deadline_at
                    flash = build_deepseek_provider(
                        api_key=settings.deepseek_api_key,
                        model=settings.deepseek_flash_model,
                        base_url=settings.deepseek_base_url,
                        permit=lambda seconds: repository.provider_permit(
                            provider="deepseek",
                            limit=settings.r2_deepseek_concurrency,
                            deadline_at=min(
                                acquisition_deadline, utc_now() + timedelta(seconds=seconds)
                            ),
                        ),
                    )

                    async def acquisition_progress(stage: ResearchStage, message: str) -> None:
                        nonlocal active_stage, current
                        active_stage = stage
                        current = await _research_transition(
                            repository,
                            request,
                            status=ResearchSessionStatus.RUNNING,
                            stage=stage,
                            message=message,
                            checkpoint=last_checkpoint,
                        )

                    async def acquisition_save(
                        state: ResearchAcquisitionState, source_packet: ResearchEvidencePacket
                    ) -> None:
                        await save_checkpoint(
                            ResearchCheckpoint(
                                schema_version="1.9",
                                generator_version=GENERATOR_VERSION,
                                stage=active_stage,
                                packet=source_packet,
                                acquisition=state,
                                available_result=ResearchReportGenerator.acquisition_result(
                                    session, source_packet
                                ),
                            )
                        )

                    resume_state = checkpoint.acquisition if checkpoint is not None else None
                    if checkpoint is not None and resume_state is not None:
                        packet = checkpoint.packet
                    async with httpx.AsyncClient() as web_client:
                        coordinator = ResearchWebFallbackCoordinator(
                            provider=flash,
                            audit_provider=provider,
                            discovery=TavilySourceDiscovery(
                                api_key=settings.tavily_api_key,
                                client=web_client,
                                permit=lambda: repository.provider_permit(
                                    provider="tavily",
                                    limit=settings.r2_tavily_concurrency,
                                    deadline_at=acquisition_deadline,
                                ),
                            ),
                            disclosure_discovery=CninfoSourceDiscovery(
                                instrument_id=current.session.instrument_id,
                                instrument_name=current.session.instrument_name,
                                client=web_client,
                                allowed_domains=enabled_web_domains(),
                            ),
                            connector=GovernedWebSourceConnector(
                                client=web_client, allowed_domains=enabled_web_domains()
                            ),
                            rights=DevelopmentResearchWebRightsPolicy(
                                enabled_web_rights_snapshots()
                            ),
                            receipts=PostgresResearchWebRetrievalReceiptRepository(active_engine),
                        )
                        try:
                            packet = await coordinator.collect_real(
                                session=current.session,
                                packet=packet,
                                cutoff_mode=cutoff_mode,
                                task_deadline_at=request.context.deadline_at,
                                state=resume_state,
                                save=acquisition_save,
                                progress=acquisition_progress,
                            )
                        except (LLMProviderError, GovernedWebSourceError) as error:
                            raise ResearchGenerationError(
                                error.code,
                                retryable=error.retryable,
                                failure_stage=active_stage,
                            ) from error
                    if not {"identity.company", "business.core_products"}.issubset(
                        {fact.key for fact in packet.facts}
                    ):
                        raise ResearchGenerationError(
                            "INSUFFICIENT_EVIDENCE", failure_stage=active_stage
                        )
                elif settings.enable_r2_web_fallback:
                    if cutoff_mode is ResearchCutoffMode.HISTORICAL_REPLAY:
                        packet = mark_web_fallback_unavailable(
                            packet,
                            reason_code="WEB_PIT_NOT_ELIGIBLE",
                        )
                    elif settings.tavily_api_key is None:
                        packet = mark_web_fallback_unavailable(
                            packet,
                            reason_code="WEB_DISCOVERY_UNAVAILABLE",
                        )
                    else:
                        if request.context.deadline_at is None:
                            raise ResearchGenerationError("RESEARCH_TASK_CONTEXT_MISMATCH")
                        try:
                            async with httpx.AsyncClient() as web_client:
                                fallback = ResearchWebFallbackCoordinator(
                                    provider=provider,
                                    discovery=TavilySourceDiscovery(
                                        api_key=settings.tavily_api_key,
                                        client=web_client,
                                    ),
                                    connector=GovernedWebSourceConnector(
                                        client=web_client,
                                        allowed_domains=enabled_web_domains(),
                                    ),
                                    rights=DevelopmentResearchWebRightsPolicy(
                                        enabled_web_rights_snapshots()
                                    ),
                                    receipts=PostgresResearchWebRetrievalReceiptRepository(
                                        active_engine
                                    ),
                                )
                                async with repository.provider_permit(
                                    provider="tavily",
                                    limit=settings.r2_tavily_concurrency,
                                    deadline_at=request.context.deadline_at,
                                ):
                                    packet = await fallback.complete(
                                        session=current.session,
                                        packet=packet,
                                        cutoff_mode=cutoff_mode,
                                        task_deadline_at=request.context.deadline_at,
                                    )
                        except TavilyDiscoveryError as error:
                            _log_research_web_fallback_failure(
                                correlation_id=current.session.job_id,
                                error=error,
                            )
                            packet = mark_web_fallback_unavailable(
                                packet,
                                reason_code=(
                                    "WEB_DISCOVERY_LIMITED"
                                    if error.code == "TAVILY_QUOTA_EXHAUSTED"
                                    else "WEB_DISCOVERY_UNAVAILABLE"
                                ),
                            )
                last_checkpoint = "history-collected"

                if session.source_mode is not ResearchRealSourceMode.DEVELOPMENT_ONLY:
                    await enter_stage(2)
                profile = packet.exposure_profile
                event_repository = PostgresMarketEventRepository(active_engine)
                event_request_at = utc_now()
                try:
                    if session.source_mode is ResearchRealSourceMode.DEVELOPMENT_ONLY:
                        catalog = GlobalEventCatalogSlice(
                            schema_version="1.0",
                            status=GlobalEventCatalogStatus.UNAVAILABLE,
                            reason_code="REAL_EVENT_DATA_NOT_COLLECTED",
                        )
                    else:
                        catalog = await event_repository.get_catalog_slice(
                            MarketEventQueryV1_1(
                                schema_version="1.1",
                                context=OperationContext(
                                    schema_version="1.0",
                                    request_id=request.context.request_id,
                                    correlation_id=request.context.correlation_id,
                                    actor_id="research-worker",
                                    workspace_id="on-demand-research",
                                    as_of=current.session.as_of,
                                    requested_at=event_request_at,
                                ),
                                security_ids=[] if profile is None else [profile.instrument_id],
                                company_ids=(
                                    []
                                    if profile is None or profile.company_id is None
                                    else [profile.company_id]
                                ),
                                region_ids=[] if profile is None else profile.region_ids,
                                industry_ids=[] if profile is None else profile.industry_ids,
                                limit=1000,
                            )
                        )
                except (SQLAlchemyError, MarketEventRepositoryError):
                    catalog = GlobalEventCatalogSlice(
                        schema_version="1.0",
                        status=GlobalEventCatalogStatus.UNAVAILABLE,
                        reason_code="EVENT_CATALOG_DEPENDENCY_UNAVAILABLE",
                    )
                if session.source_mode is not ResearchRealSourceMode.DEVELOPMENT_ONLY:
                    packet = enrich_packet_with_global_events(packet, catalog)
                last_checkpoint = "events-analyzed"

                if session.source_mode is not ResearchRealSourceMode.DEVELOPMENT_ONLY:
                    await save_checkpoint(
                        ResearchCheckpoint(
                            schema_version="1.9",
                            acquisition=checkpoint.acquisition if checkpoint is not None else None,
                            generator_version=GENERATOR_VERSION,
                            stage=ResearchStage.ANALYZING_EVENTS,
                            packet=packet,
                        )
                    )

            if session.source_mode is not ResearchRealSourceMode.DEVELOPMENT_ONLY:
                await enter_stage(3)
            if current.session is None:
                raise ResearchRepositoryError("research session disappeared during execution")
            generation_session = current.session.model_copy(update={"as_of": packet.as_of})
            try:
                report = await ResearchReportGenerator(provider).generate(
                    session=generation_session,
                    packet=packet,
                    checkpoint=checkpoint,
                    save_checkpoint=save_checkpoint,
                    deadline_at=request.context.deadline_at,
                    progress=acquisition_progress
                    if session.source_mode is ResearchRealSourceMode.DEVELOPMENT_ONLY
                    else None,
                )
            except ResearchGenerationError as error:
                if not (
                    error.reason_code == "NARRATIVE_SUPPORT_INSUFFICIENT"
                    and error.result_completeness is ResearchResultCompleteness.PARTIAL
                    and error.available_result is not None
                    and error.available_result.reader_edition is not None
                ):
                    raise
                # A normally audited but incomplete analysis uses the same
                # execution timeout and outer failure/cancellation handling.
                # Only the complete path below publishes a formal report.
                active_stage = ResearchStage.VALIDATING_REPORT
                completed = await _research_transition(
                    repository,
                    request,
                    status=ResearchSessionStatus.SUCCEEDED,
                    stage=ResearchStage.COMPLETED,
                    message="研究流程已结束\uff0c分析受限\uff1b仅保留已审核内容\uff0c尚未形成完整报告。",
                    checkpoint=last_checkpoint,
                    result_completeness=ResearchResultCompleteness.PARTIAL,
                )
                assert completed.session is not None
                return ResearchGenerateResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=request.job_id,
                    status=ResearchSessionStatus.SUCCEEDED,
                    completed_at=completed.session.updated_at,
                )
            report = ResearchReport.model_validate(
                {
                    **report.model_dump(mode="python"),
                    "expires_at": current.session.expires_at,
                }
            )
            if checkpoint is None:
                raise ResearchGenerationError("RESEARCH_CHECKPOINT_MISSING")
            await save_checkpoint(
                checkpoint.model_copy(
                    update={
                        "stage": ResearchStage.VALIDATING_REPORT,
                        "packet": packet,
                        "report": report,
                        "scorecard": report.scorecard,
                    }
                )
            )
            last_checkpoint = "cases-debated"

            await enter_stage(4)
            report = ResearchReport.model_validate(report.model_dump(mode="python"))
            last_checkpoint = "report-validating"
            current = await _research_transition(
                repository,
                request,
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.VALIDATING_REPORT,
                message="报告证据与内容已核对, 正在保存报告及评分结果。",
                checkpoint=last_checkpoint,
            )
            if current.session is None:
                raise ResearchRepositoryError("research session disappeared before publication")
            session_expires_at = current.session.expires_at
            published_at = utc_now()
            if published_at >= session_expires_at:
                raise ResearchGenerationError("REPORT_EXPIRED")
            if (
                request.context.deadline_at is not None
                and published_at > request.context.deadline_at
            ):
                raise ResearchGenerationError("RESEARCH_TASK_DEADLINE_EXCEEDED")
            report = ResearchReport.model_validate(
                {
                    **report.model_dump(mode="python"),
                    "generated_at": published_at,
                    "expires_at": session_expires_at,
                }
            )
            payload = json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            object_store = _research_object_store(settings)
            artifact_ref = await object_store.put(
                ObjectWriteRequest(
                    schema_version="1.0",
                    context=_research_mutation(
                        request, now=report.generated_at, key_suffix="artifact"
                    ),
                    namespace="research_reports",
                    suggested_key=f"research/{report.session_id}/{report.report_id}.json",
                    media_type="application/json",
                    content_length=len(payload),
                    content_sha256=hashlib.sha256(payload).hexdigest(),
                    retention_class=RetentionClass.SHORT_TERM,
                    retention_until=report.expires_at,
                    attributes=[
                        ObjectAttribute(name="source_mode", value=report.source_mode.value)
                    ],
                ),
                _single_chunk(payload),
            )
            report = ResearchReport.model_validate(
                {
                    **report.model_dump(mode="python"),
                    "artifact_object_key": artifact_ref.object_key,
                    "artifact_content_sha256": artifact_ref.content_sha256,
                    "artifact_content_length": artifact_ref.content_length,
                }
            )
            await repository.save_report(
                ResearchReportWriteRequest(
                    schema_version="1.0",
                    context=_research_mutation(
                        request,
                        now=report.generated_at,
                        key_suffix="save-report",
                        preserve_deadline=True,
                    ),
                    job_id=request.job_id,
                    cutoff_mode=cutoff_mode,
                    report=report,
                    artifact_object_version_id=artifact_ref.version_id,
                )
            )
            return ResearchGenerateResult(
                schema_version="1.0",
                session_id=request.session_id,
                job_id=request.job_id,
                status=ResearchSessionStatus.SUCCEEDED,
                report_id=report.report_id,
                completed_at=report.generated_at,
            )
    except asyncio.CancelledError:
        await _compensate_research_artifact(
            object_store=object_store,
            artifact_ref=artifact_ref,
            request=request,
            completed_at=utc_now(),
        )
        raise
    except ResearchTransitionConflictError:
        completed_at = utc_now()
        await _compensate_research_artifact(
            object_store=object_store,
            artifact_ref=artifact_ref,
            request=request,
            completed_at=completed_at,
        )
        return await _research_no_longer_active_result(
            repository,
            request,
            completed_at=completed_at,
        )
    except ResearchGenerationError as error:
        _LOGGER.warning(
            "research_generation_failed session_id=%s job_id=%s stage=%s "
            "reason_code=%s retryable=%s",
            request.session_id,
            request.job_id,
            (error.failure_stage or active_stage).value,
            error.reason_code,
            error.retryable,
        )
        failed_at = utc_now()
        try:
            await _research_transition(
                repository,
                request,
                status=ResearchSessionStatus.FAILED,
                stage=error.failure_stage or active_stage,
                message=_research_failure_message(error.reason_code),
                checkpoint=last_checkpoint,
                reason_code=error.reason_code,
                retryable=error.retryable,
                result_completeness=error.result_completeness,
            )
        except ResearchTransitionConflictError:
            return await _research_no_longer_active_result(
                repository,
                request,
                completed_at=failed_at,
            )
        return ResearchGenerateResult(
            schema_version="1.0",
            session_id=request.session_id,
            job_id=request.job_id,
            status=ResearchSessionStatus.FAILED,
            completed_at=failed_at,
            reason_code=error.reason_code,
        )
    except (
        BotoCoreError,
        ClientError,
        OSError,
        S3Error,
        SQLAlchemyError,
        ResearchRepositoryError,
        ValueError,
        TimeoutError,
    ) as error:
        _LOGGER.warning(
            "research dependency failed for session_id=%s dependency_type=%s",
            request.session_id,
            type(error).__name__,
        )
        failed_at = utc_now()
        reason_code = (
            "RESEARCH_INTERNAL_ERROR"
            if isinstance(error, ValueError)
            else "RESEARCH_TASK_DEADLINE_EXCEEDED"
            if isinstance(error, TimeoutError)
            else "RESEARCH_DEPENDENCY_UNAVAILABLE"
        )
        await _compensate_research_artifact(
            object_store=object_store,
            artifact_ref=artifact_ref,
            request=request,
            completed_at=failed_at,
        )
        try:
            await _research_transition(
                repository,
                request,
                status=ResearchSessionStatus.FAILED,
                stage=active_stage,
                message="研究执行暂时中断, 已保存的有效进度可以继续。",
                checkpoint=last_checkpoint,
                reason_code=reason_code,
                retryable=not isinstance(error, ValueError),
                result_completeness=ResearchResultCompleteness.EXECUTION_FAILED,
            )
        except ResearchTransitionConflictError:
            return await _research_no_longer_active_result(
                repository,
                request,
                completed_at=failed_at,
            )
        return ResearchGenerateResult(
            schema_version="1.0",
            session_id=request.session_id,
            job_id=request.job_id,
            status=ResearchSessionStatus.FAILED,
            completed_at=failed_at,
            reason_code=reason_code,
        )
    finally:
        if lock_connection is not None:
            if lock_acquired:
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": f"research-job:{request.job_id}"},
                )
            await lock_connection.close()
        if owns_engine:
            await active_engine.dispose()


@celery_app.task(name=TASK_NAMES["policy_refresh"])
def policy_refresh(request: PolicyRefreshRequest | WireRequest) -> WireResult:
    """Refresh the global synthetic event catalog without a stock parameter."""

    validated = PolicyRefreshRequest.model_validate(request)
    return _wire(_run(_execute_policy_refresh(validated)))


@celery_app.task(name=TASK_NAMES["premarket_ingest"])
def premarket_ingest(request: PremarketIngestJobRequest | WireRequest) -> WireResult:
    """Validate and execute one compatible R1 ingestion request."""

    raw = request if isinstance(request, dict) else request.model_dump(mode="python")
    if raw.get("schema_version") == "1.2":
        validated_v1_2 = PremarketIngestJobRequestV1_2.model_validate(raw)
        _require_v1_2_runtime(validated_v1_2)
        return _wire(_execute_premarket_ingest_v1_2(validated_v1_2, utc_now()))
    if raw.get("schema_version") == "1.1":
        validated_v1_1 = PremarketIngestJobRequestV1_1.model_validate(raw)
        _require_v1_1_runtime(validated_v1_1)
        return _wire(_execute_premarket_ingest_v1_1(validated_v1_1, utc_now()))
    validated = PremarketIngestJobRequest.model_validate(raw)
    settings = get_settings()
    if settings.r1_runtime_mode.value != SourceRuntimeMode.DEMO_REPLAY.value:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_DISABLED")
    batch = fixture_batch_for_request(
        validated,
        enabled=settings.enable_r1_fixture,
        app_env=settings.app_env,
    )
    return _wire(_execute_premarket_ingest(validated, utc_now(), batch))


@celery_app.task(name=TASK_NAMES["premarket_brief"])
def premarket_brief(request: PremarketBriefJobRequest | WireRequest) -> WireResult:
    """Validate and execute one deterministic R1 briefing request."""

    validated = PremarketBriefJobRequest.model_validate(request)
    settings = get_settings()
    runtime_mode = _runtime_mode_for_brief(tuple(validated.payload.source_ids))
    if runtime_mode is None:
        if settings.r1_runtime_mode.value != SourceRuntimeMode.DEMO_REPLAY.value:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_DISABLED")
        require_fixture_brief_request(
            validated,
            enabled=settings.enable_r1_fixture,
            app_env=settings.app_env,
        )
    elif runtime_mode.value == SourceRuntimeMode.DEMO_REPLAY.value:
        if (
            settings.app_env == "production"
            or not settings.enable_r1_fixture
            or settings.r1_runtime_mode.value != SourceRuntimeMode.DEMO_REPLAY.value
        ):
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_DISABLED")
    elif (
        settings.app_env == "production"
        or not settings.enable_r1_dev_live
        or settings.r1_runtime_mode.value != SourceRuntimeMode.DEV_LIVE.value
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_DEV_LIVE_DISABLED")
    return _wire(_execute_premarket_brief(validated, utc_now()))


@celery_app.task(name=TASK_NAMES["behavior_compute"])
def behavior_compute(request: BehaviorComputeRequest | WireRequest) -> WireResult:
    """Compute and persist one deterministic behavior-pressure research assessment."""

    validated = BehaviorComputeRequest.model_validate(request)
    return _wire(_run(_execute_behavior_compute_async(validated)))


@celery_app.task(name=TASK_NAMES["research_generate"])
def research_generate_report(request: ResearchGenerateTaskRequest | WireRequest) -> WireResult:
    """Generate and publish one complete R2 report or a stable terminal failure."""

    validated = parse_research_generate_request(request)
    return _wire(_run(_execute_research_generate(validated)))


@celery_app.task(name=TASK_NAMES["sector_rotation"])
def sector_rotation_tick() -> WireResult:
    return _wire(_raise_disabled("sector_rotation", "R4"))


@celery_app.task(name=TASK_NAMES["quote_snapshot"])
def quote_snapshot() -> WireResult:
    return _wire(_raise_disabled("quote_snapshot", "R4"))


@celery_app.task(name=TASK_NAMES["postmarket_outcome"])
def postmarket_outcome() -> WireResult:
    return _wire(_raise_disabled("postmarket_outcome", "R6"))
