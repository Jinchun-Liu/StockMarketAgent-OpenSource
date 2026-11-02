"""PostgreSQL integration for R2 research state, reports and memory boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from uuid import UUID, uuid4

import pytest
from apps.scheduler import main as scheduler_main
from apps.worker import tasks as worker_tasks
from botocore.exceptions import ClientError
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.integration.test_active_target_repository import _database_url, _upgrade

from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.global_events import (
    GlobalEventCatalogSlice,
    GlobalEventCatalogStatus,
)
from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    GenerationResponse,
    ModelProviderCapability,
)
from stock_assistant.contracts.persistence import MarketEventQueryV1_1
from stock_assistant.contracts.research import (
    ResearchAnalysisConfig,
    ResearchCheckpoint,
    ResearchCheckpointLookupResult,
    ResearchCheckpointWriteRequest,
    ResearchCutoffMode,
    ResearchEvidenceAcquisitionMode,
    ResearchEvidencePacket,
    ResearchEvidenceSourceLevel,
    ResearchEvidenceVerificationStatus,
    ResearchGenerateRequest,
    ResearchGenerateRequestV1_1,
    ResearchGenerateResult,
    ResearchRealSourceMode,
    ResearchReport,
    ResearchReportWriteRequest,
    ResearchResultCompleteness,
    ResearchSessionCreateRequest,
    ResearchSessionListQuery,
    ResearchSessionLookupRequest,
    ResearchSessionLookupResult,
    ResearchSessionMutationRequest,
    ResearchSessionStatus,
    ResearchSessionView,
    ResearchSourceMode,
    ResearchStage,
    ResearchStageTransitionRequest,
)
from stock_assistant.contracts.storage import (
    ObjectDeleteRequest,
    ObjectDeleteResult,
    ObjectWriteRequest,
    StoredObjectRef,
)
from stock_assistant.modules.research.reporting import (
    ResearchGenerationError,
    ResearchReportGenerator,
    _patch_projection,
)
from stock_assistant.modules.research.web_fallback import (
    WEB_FALLBACK_RULE_VERSION,
    empty_real_evidence_packet,
)
from stock_assistant.platform.db.research import (
    PostgresResearchRepository,
    ResearchIdempotencyConflictError,
    ResearchQuotaExceededError,
    ResearchRepositoryError,
    ResearchTransitionConflictError,
)
from stock_assistant.platform.llm.synthetic_research import SyntheticResearchLLMProvider
from stock_assistant.platform.object_store.s3_store import S3ObjectStore
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet
from stock_assistant.ports.intelligence import LLMProviderPort

pytestmark = pytest.mark.integration


def _canonical_report_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _ResearchObjectStore:
    def __init__(self) -> None:
        self.last_write: ObjectWriteRequest | None = None
        self.writes: list[ObjectWriteRequest] = []
        self.references: list[StoredObjectRef] = []
        self.deletes: list[ObjectDeleteRequest] = []

    async def put(
        self,
        request: ObjectWriteRequest,
        chunks: AsyncIterator[bytes],
    ) -> StoredObjectRef:
        self.last_write = request
        self.writes.append(request)
        payload = b"".join([chunk async for chunk in chunks])
        assert hashlib.sha256(payload).hexdigest() == request.content_sha256
        assert request.suggested_key is not None
        reference = StoredObjectRef(
            schema_version="1.0",
            namespace=request.namespace,
            object_key=request.suggested_key,
            version_id="synthetic-version-1",
            media_type=request.media_type,
            content_length=len(payload),
            content_sha256=request.content_sha256,
            stored_at=request.context.requested_at,
            retention_class=request.retention_class,
            retention_until=request.retention_until,
        )
        self.references.append(reference)
        return reference

    async def delete(self, request: ObjectDeleteRequest) -> ObjectDeleteResult:
        self.deletes.append(request)
        return ObjectDeleteResult(
            schema_version="1.0",
            object_id=request.reference.object_id,
            deleted=True,
            processed_at=request.context.requested_at,
        )


class _PausedAfterPutResearchObjectStore(_ResearchObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self.uploaded = asyncio.Event()
        self.release = asyncio.Event()

    async def put(
        self,
        request: ObjectWriteRequest,
        chunks: AsyncIterator[bytes],
    ) -> StoredObjectRef:
        reference = await super().put(request, chunks)
        self.uploaded.set()
        await asyncio.wait_for(self.release.wait(), timeout=10)
        return reference


class _CoordinatedResearchLLMProvider:
    """Record model tasks and optionally hold narrative calls at an explicit barrier."""

    def __init__(self, *, expected_narratives: int = 0) -> None:
        self._delegate = SyntheticResearchLLMProvider()
        self.expected_narratives = expected_narratives
        self.narratives_started = 0
        self.task_types: list[str] = []
        self.all_narratives_started = asyncio.Event()
        self.release_narratives = asyncio.Event()
        if expected_narratives == 0:
            self.release_narratives.set()

    @property
    def provider_name(self) -> str:
        return self._delegate.provider_name

    @property
    def capability(self) -> ModelProviderCapability:
        return self._delegate.capability

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.task_types.append(request.task_type)
        if request.task_type == "research_report_narrative":
            self.narratives_started += 1
            if self.narratives_started >= self.expected_narratives:
                self.all_narratives_started.set()
            await self.release_narratives.wait()
        return await self._delegate.generate(request)

    async def wait_until_blocked(self) -> None:
        await asyncio.wait_for(self.all_narratives_started.wait(), timeout=10)


class _NoMatchEventRepository:
    def __init__(self, _engine: AsyncEngine) -> None:
        pass

    async def get_catalog_slice(
        self,
        request: MarketEventQueryV1_1,
    ) -> GlobalEventCatalogSlice:
        return GlobalEventCatalogSlice(
            schema_version="1.0",
            status=GlobalEventCatalogStatus.READY_NO_MATCHES,
            watermark=request.context.as_of,
            last_success_at=request.context.as_of,
        )


def _worker_settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_env="test",
        enable_r2_deepseek_research=False,
        enable_r2_synthetic_research=True,
        enable_r2_real_research=False,
        enable_r2_web_fallback=False,
        assert_safe_production=lambda: None,
    )


async def _dispatched_payload(
    repository: PostgresResearchRepository,
    request: ResearchSessionCreateRequest,
    *,
    dispatched_at: datetime,
) -> ResearchGenerateRequestV1_1:
    pending = await repository.pending_jobs()
    payload = next(item.payload for item in pending if item.job_id == request.job_id)
    assert isinstance(payload, ResearchGenerateRequestV1_1)
    assert payload.schema_version == "1.1"
    assert payload.cutoff_mode is request.cutoff_mode
    assert payload.context.as_of == request.context.as_of
    assert payload.context.deadline_at is not None
    assert payload.context.deadline_at > payload.context.requested_at
    assert await repository.mark_job_dispatched(
        job_id=request.job_id,
        dispatched_at=dispatched_at,
    )
    return payload


def _configure_worker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: _CoordinatedResearchLLMProvider,
    object_store: _ResearchObjectStore,
) -> None:
    monkeypatch.setattr(worker_tasks, "get_settings", _worker_settings)
    monkeypatch.setattr(
        worker_tasks, "_research_llm_provider", lambda _settings, _repository: provider
    )
    monkeypatch.setattr(
        worker_tasks,
        "_research_object_store",
        lambda _settings: object_store,
    )
    monkeypatch.setattr(
        worker_tasks,
        "PostgresMarketEventRepository",
        _NoMatchEventRepository,
    )


def _mutation(now: datetime, idempotency_key: str) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=now,
        requested_at=now,
        idempotency_key=idempotency_key,
    )


def _lookup(session_id: UUID, now: datetime) -> ResearchSessionLookupRequest:
    return ResearchSessionLookupRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            as_of=now,
            requested_at=now,
        ),
        session_id=session_id,
    )


def _create(
    now: datetime,
    idempotency_key: str,
    instrument_id: str = "XSHG:603986",
    cutoff_mode: ResearchCutoffMode = ResearchCutoffMode.HISTORICAL_REPLAY,
) -> ResearchSessionCreateRequest:
    return ResearchSessionCreateRequest(
        schema_version="1.0",
        context=_mutation(now, idempotency_key),
        instrument_id=instrument_id,
        instrument_name="兆易创新" if instrument_id == "XSHG:603986" else "宁德时代",
        request_sha256=hashlib.sha256(instrument_id.encode("utf-8")).hexdigest(),
        analysis_config=ResearchAnalysisConfig(schema_version="1.0"),
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        cutoff_mode=cutoff_mode,
    )


async def _build_running_report(
    repository: PostgresResearchRepository,
    request: ResearchSessionCreateRequest,
    now: datetime,
) -> ResearchReport:
    transitioned = await repository.transition(
        ResearchStageTransitionRequest(
            schema_version="1.0",
            context=_mutation(now, f"research-transition-{request.job_id}"),
            session_id=request.session_id,
            job_id=request.job_id,
            expected_statuses=[ResearchSessionStatus.QUEUED],
            status=ResearchSessionStatus.RUNNING,
            stage=ResearchStage.VALIDATING_REPORT,
            message="正在验证报告。",
            checkpoint="evidence-ready",
        )
    )
    assert transitioned.session is not None
    packet = synthetic_evidence_packet(
        instrument_id=request.instrument_id,
        instrument_name=request.instrument_name,
        as_of=request.context.as_of,
    )
    report = await ResearchReportGenerator(SyntheticResearchLLMProvider()).generate(
        session=transitioned.session,
        packet=packet,
    )
    return report.model_copy(update={"expires_at": transitioned.session.expires_at})


async def _publish_report(
    repository: PostgresResearchRepository,
    request: ResearchSessionCreateRequest,
    now: datetime,
    *,
    artifact_object_key: str | None = None,
    artifact_object_version_id: str | None = None,
    artifact_release_sha: str | None = None,
) -> None:
    report = await _build_running_report(repository, request, now)
    if artifact_release_sha is not None:
        artifact_object_key = (
            f"releases/{artifact_release_sha}/research/{report.session_id}/{report.report_id}.json"
        )
    pending = await repository.pending_jobs()
    payload = next(item.payload for item in pending if item.job_id == request.job_id)
    save_requested_at = now
    if payload.context.deadline_at is not None and save_requested_at >= payload.context.deadline_at:
        save_requested_at = payload.context.deadline_at - timedelta(microseconds=1)
    if artifact_object_key is not None:
        report = report.model_copy(
            update={
                "artifact_object_key": artifact_object_key,
                "artifact_content_sha256": "a" * 64,
                "artifact_content_length": 1,
            }
        )
    stored = await repository.save_report(
        ResearchReportWriteRequest(
            schema_version="1.0",
            context=_mutation(save_requested_at, f"research-save-{request.job_id}").model_copy(
                update={"deadline_at": payload.context.deadline_at}
            ),
            job_id=request.job_id,
            cutoff_mode=request.cutoff_mode,
            report=report,
            artifact_object_version_id=artifact_object_version_id,
        )
    )
    assert stored.found is True


@pytest.mark.asyncio
async def test_repository_reads_retained_report_without_republishing_under_new_rules(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-retained-validator-{uuid4()}")
    retained_summary = "一方面保留原始专业结论, 另一方面继续核对证据。"
    logger_name = "stock_assistant.platform.db.research"
    rejection_event = "research_report_compatibility_rejected"
    rejection_records: list[logging.LogRecord] = []
    try:
        await repository.create(request)
        await _publish_report(repository, request, now + timedelta(seconds=1))
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET report = jsonb_set(
                           jsonb_set(
                               report,
                               '{validator_version}',
                               to_jsonb(CAST(:validator_version AS text))
                           ),
                           '{sections,0,summary}',
                           to_jsonb(CAST(:summary AS text))
                       )
                     WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": request.session_id,
                    "summary": retained_summary,
                    "validator_version": "r2-publication-validator.v2.1",
                },
            )
            retained_payload = (
                await connection.execute(
                    text("SELECT report FROM research_reports WHERE session_id = :session_id"),
                    {"session_id": request.session_id},
                )
            ).scalar_one()
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET validator_version = :validator_version,
                           report_sha256 = :report_sha256
                     WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": request.session_id,
                    "validator_version": "r2-publication-validator.v2.1",
                    "report_sha256": _canonical_report_sha256(retained_payload),
                },
            )

        retained = await repository.get_report(
            _lookup(request.session_id, now + timedelta(seconds=2))
        )
        assert retained.found is True
        assert retained.report is not None
        assert retained.report.sections[0].summary == retained_summary

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET validator_version = 'r2-publication-validator.v2'
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id},
            )

        mismatched_version_lookup = _lookup(
            request.session_id,
            now + timedelta(seconds=3),
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            mismatched_version = await repository.get_report(mismatched_version_lookup)
        assert mismatched_version.found is False
        assert mismatched_version.reason_code == "REPORT_COMPATIBILITY_PROJECTION_FAILED"
        version_records = [
            record
            for record in caplog.records
            if record.name == logger_name and getattr(record, "event", None) == rejection_event
        ]
        assert len(version_records) == 1
        assert version_records[0].__dict__["task_type"] == "research_report_retained_read"
        assert version_records[0].__dict__["correlation_id"] == str(
            mismatched_version_lookup.context.correlation_id
        )
        assert version_records[0].__dict__["failure_category"] == ("integrity_metadata_mismatch")
        rejection_records.extend(version_records)

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET validator_version = 'r2-publication-validator.v2.1',
                           report_sha256 = :report_sha256
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id, "report_sha256": "0" * 64},
            )

        mismatched_hash_lookup = _lookup(
            request.session_id,
            now + timedelta(seconds=4),
        )
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            mismatched_hash = await repository.get_report(mismatched_hash_lookup)
        assert mismatched_hash.found is False
        assert mismatched_hash.reason_code == "REPORT_COMPATIBILITY_PROJECTION_FAILED"
        hash_records = [
            record
            for record in caplog.records
            if record.name == logger_name and getattr(record, "event", None) == rejection_event
        ]
        assert len(hash_records) == 1
        assert hash_records[0].__dict__["task_type"] == "research_report_retained_read"
        assert hash_records[0].__dict__["correlation_id"] == str(
            mismatched_hash_lookup.context.correlation_id
        )
        assert hash_records[0].__dict__["failure_category"] == ("integrity_metadata_mismatch")
        rejection_records.extend(hash_records)

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET report = report - 'sections'
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id},
            )
            malformed_payload = (
                await connection.execute(
                    text("SELECT report FROM research_reports WHERE session_id = :session_id"),
                    {"session_id": request.session_id},
                )
            ).scalar_one()
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET report_sha256 = :report_sha256
                     WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": request.session_id,
                    "report_sha256": _canonical_report_sha256(malformed_payload),
                },
            )

        malformed_lookup = _lookup(request.session_id, now + timedelta(seconds=5))
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            malformed = await repository.get_report(malformed_lookup)
        assert malformed.found is False
        assert malformed.report is None
        assert malformed.reason_code == "REPORT_COMPATIBILITY_PROJECTION_FAILED"
        validation_records = [
            record
            for record in caplog.records
            if record.name == logger_name and getattr(record, "event", None) == rejection_event
        ]
        assert len(validation_records) == 1
        assert validation_records[0].__dict__["task_type"] == "research_report_retained_read"
        assert validation_records[0].__dict__["correlation_id"] == str(
            malformed_lookup.context.correlation_id
        )
        assert validation_records[0].__dict__["failure_category"] == ("retained_validation_failed")
        rejection_records.extend(validation_records)
        safe_log_text = "\n".join(record.getMessage() for record in rejection_records)
        assert retained_summary not in safe_log_text
        assert "0" * 64 not in safe_log_text
        assert "stored report integrity metadata" not in safe_log_text
        assert all(record.exc_info is None for record in rejection_records)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_research_sessions_are_non_enumerable_across_private_owners() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    owner_a = "a" * 64
    owner_b = "b" * 64
    created = _create(now, f"owner-isolation-{uuid4()}")
    created = created.model_copy(
        update={
            "context": created.context.model_copy(update={"actor_id": owner_a}),
        }
    )
    try:
        await repository.create(created)
        owner_a_lookup = _lookup(created.session_id, now).model_copy(
            update={
                "context": _lookup(created.session_id, now).context.model_copy(
                    update={"actor_id": owner_a}
                )
            }
        )
        owner_b_lookup = _lookup(created.session_id, now).model_copy(
            update={
                "context": _lookup(created.session_id, now).context.model_copy(
                    update={"actor_id": owner_b}
                )
            }
        )

        assert (await repository.get(owner_a_lookup)).found is True
        assert (await repository.get(owner_b_lookup)).found is False
        hidden_page = await repository.list_page(
            ResearchSessionListQuery(
                schema_version="1.0",
                context=owner_b_lookup.context,
                limit=100,
            )
        )
        assert all(item.session_id != created.session_id for item in hidden_page.items)
        hidden_report = await repository.get_report(owner_b_lookup)
        assert hidden_report.found is False
        for operation in (repository.cancel, repository.retry):
            hidden_mutation = await operation(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(now, f"owner-b-hidden-{uuid4()}").model_copy(
                        update={"actor_id": owner_b}
                    ),
                    session_id=created.session_id,
                )
            )
            assert hidden_mutation.reason_code == "RESEARCH_SESSION_NOT_FOUND"
        hidden_delete = await repository.delete(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(now, f"owner-b-delete-{uuid4()}").model_copy(
                    update={"actor_id": owner_b}
                ),
                session_id=created.session_id,
            )
        )
        assert hidden_delete.reason_code == "RESEARCH_SESSION_NOT_FOUND"
        assert (await repository.get(owner_a_lookup)).found is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_private_owner_active_quota_is_atomic_under_concurrent_creates() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    owner = hashlib.sha256(str(uuid4()).encode("utf-8")).hexdigest()
    requests: list[ResearchSessionCreateRequest] = []
    for _ in range(6):
        request = _create(now, f"owner-quota-{uuid4()}")
        requests.append(
            request.model_copy(
                update={"context": request.context.model_copy(update={"actor_id": owner})}
            )
        )
    try:
        results = await asyncio.gather(
            *(repository.create(request) for request in requests),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, BaseException) for result in results) == 5
        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(failures) == 1
        assert isinstance(failures[0], ResearchQuotaExceededError)
        assert str(failures[0]) == "RESEARCH_ACTIVE_SESSION_LIMIT"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_private_owner_create_quota_uses_rolling_24_hour_window() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    utc_midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    before_midnight = utc_midnight - timedelta(minutes=1)
    after_midnight = utc_midnight + timedelta(minutes=1)
    owner = hashlib.sha256(str(uuid4()).encode("utf-8")).hexdigest()
    try:
        for _index in range(20):
            request = _create(before_midnight, f"owner-rolling-quota-{uuid4()}")
            request = request.model_copy(
                update={"context": request.context.model_copy(update={"actor_id": owner})}
            )
            await repository.create(request)
            cancelled = await repository.cancel(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(
                        before_midnight,
                        f"owner-rolling-quota-cancel-{uuid4()}",
                    ).model_copy(update={"actor_id": owner}),
                    session_id=request.session_id,
                )
            )
            assert cancelled.status is ResearchSessionStatus.CANCELLED

        blocked = _create(after_midnight, f"owner-rolling-quota-{uuid4()}")
        blocked = blocked.model_copy(
            update={"context": blocked.context.model_copy(update={"actor_id": owner})}
        )
        with pytest.raises(ResearchQuotaExceededError, match="RESEARCH_DAILY_CREATE_LIMIT"):
            await repository.create(blocked)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_report_publish_rejects_replaced_jobs_and_expired_sessions() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    replaced = _create(
        now - timedelta(hours=1),
        "research-create-replaced-job-0001",
    )
    expired = _create(
        now - timedelta(seconds=20),
        "research-create-expired-publish-0001",
        "XSHE:300750",
    )
    try:
        await repository.create(replaced)
        replaced_report = await _build_running_report(
            repository,
            replaced,
            now - timedelta(minutes=59),
        )
        assert await repository.mark_stalled_due(now=now) == 1
        retried = await repository.retry(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(now + timedelta(seconds=1), "research-replace-job-0001"),
                session_id=replaced.session_id,
            )
        )
        assert retried.job_id is not None
        assert retried.job_id != replaced.job_id
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_report(
                ResearchReportWriteRequest(
                    schema_version="1.0",
                    context=_mutation(
                        now + timedelta(seconds=2),
                        "research-old-job-save-0001",
                    ),
                    job_id=replaced.job_id,
                    report=replaced_report,
                )
            )
        with pytest.raises(ResearchTransitionConflictError):
            await repository.transition(
                ResearchStageTransitionRequest(
                    schema_version="1.0",
                    context=_mutation(
                        now + timedelta(seconds=2),
                        "research-old-job-fail-0001",
                    ),
                    session_id=replaced.session_id,
                    job_id=replaced.job_id,
                    expected_statuses=[ResearchSessionStatus.QUEUED],
                    status=ResearchSessionStatus.FAILED,
                    stage=ResearchStage.VALIDATING_REPORT,
                    message="旧任务不得覆盖新任务。",
                    reason_code="RESEARCH_DEPENDENCY_UNAVAILABLE",
                    retryable=True,
                )
            )

        await repository.create(expired)
        expired_report = await _build_running_report(
            repository,
            expired,
            now - timedelta(seconds=19),
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_sessions SET expires_at = :expires_at
                     WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": expired.session_id,
                    "expires_at": now - timedelta(seconds=15),
                },
            )
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_report(
                ResearchReportWriteRequest(
                    schema_version="1.0",
                    context=_mutation(
                        now + timedelta(seconds=3),
                        "research-expired-save-0001",
                    ),
                    job_id=expired.job_id,
                    report=expired_report,
                )
            )
        assert await repository.expire_due(now=now) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_report_publish_rejects_a_caller_extended_task_deadline() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    database_now = datetime.now(UTC).replace(microsecond=0)
    initial_cutoff = database_now - timedelta(minutes=31)
    request = _create(
        initial_cutoff,
        f"research-persisted-deadline-{uuid4()}",
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
    )
    try:
        await repository.create(request)
        report = await _build_running_report(
            repository,
            request,
            database_now - timedelta(seconds=1),
        )
        forged_context = _mutation(
            database_now,
            f"research-forged-deadline-{uuid4()}",
        ).model_copy(update={"deadline_at": database_now + timedelta(minutes=30)})

        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_report(
                ResearchReportWriteRequest(
                    schema_version="1.0",
                    context=forged_context,
                    job_id=request.job_id,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    report=report,
                )
            )

        async with engine.connect() as connection:
            report_count = await connection.scalar(
                text("SELECT count(*) FROM research_reports WHERE session_id = :session_id"),
                {"session_id": request.session_id},
            )
        assert report_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_report_publish_advances_as_of_for_accepted_builtin_citation() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    initial_cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(
        initial_cutoff,
        f"research-builtin-final-cutoff-{uuid4()}",
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
    )
    try:
        await repository.create(request)
        transitioned = await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(
                    initial_cutoff + timedelta(seconds=1),
                    f"research-builtin-running-{uuid4()}",
                ),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.VALIDATING_REPORT,
                message="正在验证报告。",
                checkpoint="evidence-ready",
            )
        )
        assert transitioned.session is not None
        first_seen_at = initial_cutoff + timedelta(seconds=2)
        packet = synthetic_evidence_packet(
            instrument_id=request.instrument_id,
            instrument_name=request.instrument_name,
            as_of=initial_cutoff,
        )
        builtin_citation = packet.citations[0].model_copy(
            update={
                "first_seen_at": first_seen_at,
                "revision_known_at": first_seen_at,
            }
        )
        packet = ResearchEvidencePacket.model_validate(
            {
                **packet.model_dump(mode="python"),
                "as_of": first_seen_at,
                "citations": [builtin_citation, *packet.citations[1:]],
                "data_manifest_sha256": hashlib.sha256(
                    f"{packet.data_manifest_sha256}:{first_seen_at.isoformat()}".encode()
                ).hexdigest(),
            }
        )
        report = await ResearchReportGenerator(SyntheticResearchLLMProvider()).generate(
            session=transitioned.session.model_copy(update={"as_of": first_seen_at}),
            packet=packet,
        )
        report = report.model_copy(update={"expires_at": transitioned.session.expires_at})
        assert any(
            citation.evidence_id == builtin_citation.evidence_id
            for claim in report.claims
            for citation in (*claim.evidence, *claim.counter_evidence)
        )

        stored = await repository.save_report(
            ResearchReportWriteRequest(
                schema_version="1.0",
                context=_mutation(
                    initial_cutoff + timedelta(seconds=3),
                    f"research-builtin-save-{uuid4()}",
                ).model_copy(update={"deadline_at": initial_cutoff + timedelta(minutes=30)}),
                job_id=request.job_id,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                report=report,
            )
        )

        assert stored.report is not None
        assert stored.report.as_of == first_seen_at
        lookup = await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        assert lookup.session is not None
        assert lookup.session.as_of == first_seen_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_current_report_publish_atomically_advances_as_of_and_session_gaps() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    initial_cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(
        initial_cutoff,
        f"research-atomic-final-cutoff-{uuid4()}",
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
    )
    try:
        await repository.create(request)
        transitioned = await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(
                    initial_cutoff + timedelta(seconds=1),
                    f"research-atomic-running-{uuid4()}",
                ),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.VALIDATING_REPORT,
                message="正在验证报告。",
                checkpoint="evidence-ready",
            )
        )
        assert transitioned.session is not None
        first_seen_at = initial_cutoff + timedelta(seconds=2)
        packet = synthetic_evidence_packet(
            instrument_id=request.instrument_id,
            instrument_name=request.instrument_name,
            as_of=initial_cutoff,
            include_builtin_api_gap=True,
            fixture_version="v3",
        )
        first_citation = packet.citations[0].model_copy(
            update={
                "published_at": initial_cutoff - timedelta(days=1),
                "first_seen_at": first_seen_at,
                "revision_known_at": initial_cutoff - timedelta(days=1),
                "acquisition_mode": ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB,
                "source_level": ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
                "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
                "content_sha256": "a" * 64,
                "rights_snapshot_sha256": "b" * 64,
            }
        )
        packet = ResearchEvidencePacket.model_validate(
            {
                **packet.model_dump(mode="python"),
                "as_of": first_seen_at,
                "citations": [first_citation, *packet.citations[1:]],
                "web_retrieval_receipt_ids": [uuid4()],
                "data_manifest_sha256": hashlib.sha256(
                    f"{packet.data_manifest_sha256}:{first_seen_at.isoformat()}".encode()
                ).hexdigest(),
            }
        )
        report = await ResearchReportGenerator(SyntheticResearchLLMProvider()).generate(
            session=transitioned.session.model_copy(update={"as_of": first_seen_at}),
            packet=packet,
        )
        report = report.model_copy(update={"expires_at": transitioned.session.expires_at})
        write_context = _mutation(
            initial_cutoff + timedelta(seconds=3),
            f"research-atomic-save-{uuid4()}",
        ).model_copy(update={"deadline_at": initial_cutoff + timedelta(minutes=30)})

        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_report(
                ResearchReportWriteRequest(
                    schema_version="1.0",
                    context=write_context,
                    job_id=request.job_id,
                    cutoff_mode=ResearchCutoffMode.HISTORICAL_REPLAY,
                    report=report,
                )
            )
        expired_context = _mutation(
            initial_cutoff - timedelta(minutes=2),
            f"research-expired-attempt-{uuid4()}",
        ).model_copy(update={"deadline_at": initial_cutoff - timedelta(minutes=1)})
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_report(
                ResearchReportWriteRequest(
                    schema_version="1.0",
                    context=expired_context,
                    job_id=request.job_id,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    report=report,
                )
            )
        async with engine.connect() as connection:
            before_publish = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT rs.status, rs.as_of, rs.coverage_gaps,
                                   count(report.report_id) AS report_count
                              FROM research_sessions AS rs
                              LEFT JOIN research_reports AS report
                                ON report.session_id = rs.session_id
                             WHERE rs.session_id = :session_id
                             GROUP BY rs.status, rs.as_of, rs.coverage_gaps
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert before_publish["status"] == "running"
        assert before_publish["as_of"] == initial_cutoff
        assert before_publish["coverage_gaps"] == []
        assert before_publish["report_count"] == 0

        stored = await repository.save_report(
            ResearchReportWriteRequest(
                schema_version="1.0",
                context=write_context,
                job_id=request.job_id,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                report=report,
            )
        )

        assert stored.report is not None
        assert stored.report.as_of == first_seen_at
        lookup = await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        assert lookup.session is not None
        assert lookup.session.as_of == first_seen_at
        assert lookup.session.coverage_gaps == stored.report.coverage_gaps
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT rs.as_of, rs.coverage_gaps,
                                   report.report
                              FROM research_sessions AS rs
                              JOIN research_reports AS report
                                ON report.session_id = rs.session_id
                             WHERE rs.session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert row["as_of"] == first_seen_at
        assert (
            datetime.fromisoformat(row["report"]["as_of"].replace("Z", "+00:00")) == first_seen_at
        )
        assert row["coverage_gaps"] == row["report"]["coverage_gaps"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_progress_checkpoints_follow_completed_stage_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(
        now,
        "research-worker-stage-order-create-0001",
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
    )
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        monkeypatch.setattr(
            worker_tasks,
            "get_settings",
            lambda: SimpleNamespace(
                app_env="test",
                enable_r2_deepseek_research=False,
                enable_r2_synthetic_research=True,
                enable_r2_real_research=False,
                enable_r2_web_fallback=True,
                r2_tavily_concurrency=1,
                tavily_api_key=SecretStr("synthetic-tavily-key"),
                assert_safe_production=lambda: None,
            ),
        )
        object_store = _ResearchObjectStore()
        monkeypatch.setattr(worker_tasks, "_research_object_store", lambda _settings: object_store)
        fallback_packets: list[ResearchEvidencePacket] = []

        class _WorkerFallback:
            def __init__(self, **_dependencies: object) -> None:
                pass

            async def complete(
                self,
                *,
                session: ResearchSessionView,
                packet: ResearchEvidencePacket,
                cutoff_mode: ResearchCutoffMode,
                task_deadline_at: datetime,
            ) -> ResearchEvidencePacket:
                del session
                fallback_packets.append(packet)
                assert cutoff_mode is ResearchCutoffMode.CURRENT_RUN
                assert task_deadline_at == payload.context.deadline_at
                assert any(
                    gap.reason_code == "REQUIRED_FIELD_MISSING" for gap in packet.coverage_gaps
                )
                return packet

        monkeypatch.setattr(worker_tasks, "ResearchWebFallbackCoordinator", _WorkerFallback)
        monkeypatch.setattr(
            worker_tasks,
            "synthetic_evidence_packet",
            lambda **kwargs: synthetic_evidence_packet(**kwargs, fixture_version="v3"),
        )

        class _NoMatchEventRepository:
            def __init__(self, _engine: AsyncEngine) -> None:
                pass

            async def get_catalog_slice(
                self,
                _event_request: MarketEventQueryV1_1,
            ) -> GlobalEventCatalogSlice:
                return GlobalEventCatalogSlice(
                    schema_version="1.0",
                    status=GlobalEventCatalogStatus.READY_NO_MATCHES,
                    watermark=now,
                    last_success_at=now,
                )

        monkeypatch.setattr(
            worker_tasks,
            "PostgresMarketEventRepository",
            _NoMatchEventRepository,
        )

        result = await worker_tasks._execute_research_generate(payload, engine=engine)

        assert result.status is ResearchSessionStatus.SUCCEEDED
        assert len(fallback_packets) == 1
        async with engine.connect() as connection:
            transitions = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT stage, checkpoint, occurred_at
                              FROM research_session_transitions
                             WHERE session_id = :session_id
                               AND to_status = 'running'
                             ORDER BY transition_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .all()
            )
            lifecycle = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT report.created_at, report.expires_at, report.report,
                                   report.artifact_object_version_id,
                                   session.completed_at, session.as_of, session.coverage_gaps,
                                   session.expires_at AS session_expires_at
                              FROM research_reports AS report
                              JOIN research_sessions AS session
                                ON session.session_id = report.session_id
                             WHERE report.session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert [(row["stage"], row["checkpoint"]) for row in transitions] == [
            ("resolving_instrument", None),
            ("collecting_history", "instrument-resolved"),
            ("analyzing_events", "history-collected"),
            ("debating_cases", "events-analyzed"),
            ("validating_report", "cases-debated"),
            ("validating_report", "report-validating"),
        ]
        assert lifecycle["completed_at"] == lifecycle["created_at"]
        assert lifecycle["expires_at"] == request.context.requested_at + timedelta(days=7)
        assert lifecycle["session_expires_at"] == lifecycle["expires_at"]
        assert lifecycle["artifact_object_version_id"] == "synthetic-version-1"
        assert object_store.last_write is not None
        assert object_store.last_write.retention_until == lifecycle["expires_at"]
        assert lifecycle["created_at"] >= transitions[-1]["occurred_at"]
        assert lifecycle["as_of"] == payload.context.as_of
        assert (
            datetime.fromisoformat(lifecycle["report"]["as_of"].replace("Z", "+00:00"))
            == payload.context.as_of
        )
        assert lifecycle["coverage_gaps"] == lifecycle["report"]["coverage_gaps"]
        deleted_at = lifecycle["created_at"] + timedelta(seconds=1)
        await repository.delete(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(deleted_at, "research-worker-artifact-delete-0001"),
                session_id=request.session_id,
            )
        )
        candidate = next(
            item
            for item in await repository.purge_candidates(now=deleted_at + timedelta(hours=24))
            if item.session_id == request.session_id
        )
        assert candidate.reference is not None
        assert candidate.reference.version_id == "synthetic-version-1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_historical_task_never_constructs_live_web_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-legacy-historical-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider()
    object_store = _ResearchObjectStore()
    monkeypatch.setattr(
        worker_tasks,
        "synthetic_evidence_packet",
        lambda **kwargs: synthetic_evidence_packet(**kwargs, fixture_version="v3"),
    )
    try:
        await repository.create(request)
        await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        legacy_payload = ResearchGenerateRequest(
            schema_version="1.0",
            context=request.context.model_copy(
                update={"idempotency_key": f"research-generate-{request.job_id}"}
            ),
            session_id=request.session_id,
            job_id=request.job_id,
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_job_outbox
                       SET payload = CAST(:payload AS jsonb)
                     WHERE session_id = :session_id
                       AND job_id = :job_id
                    """
                ),
                {
                    "session_id": request.session_id,
                    "job_id": request.job_id,
                    "payload": json.dumps(legacy_payload.model_dump(mode="json")),
                },
            )
        monkeypatch.setattr(
            worker_tasks,
            "get_settings",
            lambda: SimpleNamespace(
                app_env="test",
                enable_r2_deepseek_research=False,
                enable_r2_synthetic_research=True,
                enable_r2_real_research=False,
                enable_r2_web_fallback=True,
                assert_safe_production=lambda: None,
            ),
        )
        monkeypatch.setattr(
            worker_tasks, "_research_llm_provider", lambda _settings, _repository: provider
        )
        monkeypatch.setattr(
            worker_tasks,
            "_research_object_store",
            lambda _settings: object_store,
        )
        monkeypatch.setattr(
            worker_tasks,
            "PostgresMarketEventRepository",
            _NoMatchEventRepository,
        )

        class _UnexpectedLiveFallback:
            def __init__(self, **_dependencies: object) -> None:
                raise AssertionError("historical replay must not construct a live web fallback")

        monkeypatch.setattr(
            worker_tasks,
            "ResearchWebFallbackCoordinator",
            _UnexpectedLiveFallback,
        )

        result = await worker_tasks._execute_research_generate(legacy_payload, engine=engine)

        assert result.status is ResearchSessionStatus.SUCCEEDED
        stored = await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
        session = await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        assert stored.report is not None
        assert session.session is not None
        expected_areas = {
            "business.core_products",
            "business.customer_revenue_chain",
            "business.product_structure_change",
        }
        pit_gaps = {
            gap.area: gap.reason_code
            for gap in stored.report.coverage_gaps
            if gap.area in expected_areas
        }
        assert pit_gaps == {area: "WEB_PIT_NOT_ELIGIBLE" for area in expected_areas}
        assert session.session.as_of == request.context.as_of
        assert session.session.coverage_gaps == stored.report.coverage_gaps
        assert provider.task_types == [
            "research_report_narrative",
            "research_score_assessment",
            "research_support_assessment",
        ]
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("mode_mismatch", "RESEARCH_TASK_CONTEXT_MISMATCH"),
        ("expired_deadline", "RESEARCH_TASK_DEADLINE_EXCEEDED"),
    ],
)
@pytest.mark.asyncio
async def test_worker_fails_closed_for_mode_mismatch_or_expired_deadline(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_reason: str,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    stored_mode = (
        ResearchCutoffMode.HISTORICAL_REPLAY
        if case == "mode_mismatch"
        else ResearchCutoffMode.CURRENT_RUN
    )
    request = _create(
        now,
        f"research-task-guard-{case}-{uuid4()}",
        cutoff_mode=stored_mode,
    )
    monkeypatch.setattr(worker_tasks, "get_settings", _worker_settings)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        if case == "mode_mismatch":
            guarded_payload = payload.model_copy(
                update={"cutoff_mode": ResearchCutoffMode.CURRENT_RUN}
            )
        else:
            guarded_payload = payload.model_copy(
                update={
                    "context": payload.context.model_copy(
                        update={
                            "requested_at": now - timedelta(minutes=2),
                            "deadline_at": now - timedelta(minutes=1),
                        }
                    )
                }
            )

        result = await worker_tasks._execute_research_generate(guarded_payload, engine=engine)

        assert result.status is ResearchSessionStatus.FAILED
        assert result.reason_code == expected_reason
        current = await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        stored = await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
        assert current.session is not None
        assert current.session.status is ResearchSessionStatus.FAILED
        assert current.session.failure_reason_code == expected_reason
        assert current.session.completed_at is not None
        assert current.session.completed_at == current.session.updated_at
        assert stored.found is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_retry_recomputes_when_only_a_stage_label_was_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(
        now,
        "research-worker-final-checkpoint-create-0001",
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
    )
    try:
        await repository.create(request)
        original_payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(
                    now + timedelta(seconds=2),
                    "research-worker-final-checkpoint-running-0001",
                ),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.VALIDATING_REPORT,
                message="正在执行证据发布硬门。",
                checkpoint="report-validating",
            )
        )
        assert not (await repository.get_checkpoint(_lookup(request.session_id, now))).found
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(
                    now + timedelta(seconds=3),
                    "research-worker-final-checkpoint-failed-0001",
                ),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.RUNNING],
                status=ResearchSessionStatus.FAILED,
                stage=ResearchStage.VALIDATING_REPORT,
                message="模型暂时不可用。",
                checkpoint="report-validating",
                reason_code="MODEL_UNAVAILABLE",
                retryable=True,
            )
        )
        retried = await repository.retry(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(
                    now + timedelta(seconds=4),
                    "research-worker-final-checkpoint-retry-0001",
                ),
                session_id=request.session_id,
            )
        )
        assert retried.job_id is not None
        requeued = await repository.get(_lookup(request.session_id, now + timedelta(seconds=4)))
        assert requeued.session is not None
        assert requeued.session.progress.checkpoint is None
        assert requeued.session.progress.message == "研究任务已重新入队, 将从头执行。"
        pending = await repository.pending_jobs()
        payload = next(item.payload for item in pending if item.job_id == retried.job_id)
        assert isinstance(payload, ResearchGenerateRequestV1_1)
        assert payload.cutoff_mode is ResearchCutoffMode.CURRENT_RUN
        assert payload.context.as_of == request.context.as_of
        assert payload.context.deadline_at is not None
        assert original_payload.context.deadline_at is not None
        assert payload.context.deadline_at > original_payload.context.deadline_at

        monkeypatch.setattr(
            worker_tasks,
            "get_settings",
            _worker_settings,
        )
        provider = _CoordinatedResearchLLMProvider()
        monkeypatch.setattr(
            worker_tasks, "_research_llm_provider", lambda _settings, _repository: provider
        )
        object_store = _ResearchObjectStore()
        monkeypatch.setattr(
            worker_tasks,
            "_research_object_store",
            lambda _settings: object_store,
        )

        class _RightsBlockedEventRepository:
            def __init__(self, _engine: AsyncEngine) -> None:
                pass

            async def get_catalog_slice(
                self,
                event_request: MarketEventQueryV1_1,
            ) -> GlobalEventCatalogSlice:
                assert event_request.security_ids == [request.instrument_id]
                assert event_request.company_ids == [f"company:{request.instrument_id}"]
                assert event_request.industry_ids == ["industry:semiconductor"]
                assert event_request.region_ids == ["region:china"]
                return GlobalEventCatalogSlice(
                    schema_version="1.0",
                    status=GlobalEventCatalogStatus.RIGHTS_BLOCKED,
                    reason_code="EVENT_CATALOG_RIGHTS_BLOCKED",
                )

        monkeypatch.setattr(
            worker_tasks,
            "PostgresMarketEventRepository",
            _RightsBlockedEventRepository,
        )
        result = await worker_tasks._execute_research_generate(payload, engine=engine)

        assert result.status is ResearchSessionStatus.SUCCEEDED
        lookup = await repository.get(_lookup(request.session_id, now + timedelta(seconds=5)))
        assert lookup.session is not None
        assert lookup.session.status is ResearchSessionStatus.SUCCEEDED
        stored = await repository.get_report(
            _lookup(request.session_id, now + timedelta(seconds=5))
        )
        assert stored.found is True
        assert stored.report is not None
        assert "EVENT_CATALOG_RIGHTS_BLOCKED" in {
            gap.reason_code for gap in stored.report.coverage_gaps
        }
        assert provider.task_types == [
            "research_report_narrative",
            "research_score_assessment",
            "research_support_assessment",
        ]
        assert stored.report.scorecard is not None
        assert stored.report.scorecard.status.value == "scored"
        assert stored.report.scorecard.scoring_basis.value == "synthetic_scenario"
        assert stored.report.scorecard.overall_score is not None
        assert len(object_store.writes) == 1
        async with engine.connect() as connection:
            old_diagnostic = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*)
                          FROM research_session_transitions
                         WHERE session_id = :session_id
                           AND from_status = 'queued'
                           AND to_status = 'running'
                           AND checkpoint = 'report-validating'
                        """
                    ),
                    {"session_id": request.session_id},
                )
            ).scalar_one()
            first_restarted_stage = (
                (
                    await connection.execute(
                        text(
                            """
                            WITH retry_transition AS (
                                SELECT max(transition_id) AS transition_id
                                  FROM research_session_transitions
                                 WHERE session_id = :session_id
                                   AND to_status = 'queued'
                                   AND message = '研究任务已重新入队, 将从头执行。'
                            )
                            SELECT stage, checkpoint
                              FROM research_session_transitions, retry_transition
                             WHERE session_id = :session_id
                               AND research_session_transitions.transition_id
                                   > retry_transition.transition_id
                               AND to_status = 'running'
                             ORDER BY research_session_transitions.transition_id
                             LIMIT 1
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert old_diagnostic == 1
        assert dict(first_restarted_stage) == {
            "stage": "resolving_instrument",
            "checkpoint": None,
        }
    finally:
        await engine.dispose()


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.asyncio
async def test_same_research_job_two_workers_publish_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-two-workers-{seed}-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider(expected_narratives=1)
    object_store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )

        first_worker = asyncio.create_task(
            worker_tasks._execute_research_generate(payload, engine=engine)
        )
        await provider.wait_until_blocked()
        second_result = await asyncio.wait_for(
            worker_tasks._execute_research_generate(payload, engine=engine),
            timeout=10,
        )
        assert second_result.status is ResearchSessionStatus.RUNNING
        assert second_result.reason_code == "RESEARCH_JOB_ALREADY_RUNNING"

        provider.release_narratives.set()
        first_result = await asyncio.wait_for(first_worker, timeout=10)
        assert first_result.status is ResearchSessionStatus.SUCCEEDED
        assert provider.task_types == [
            "research_report_narrative",
            "research_score_assessment",
            "research_support_assessment",
        ]
        assert len(object_store.writes) == 1
        assert object_store.deletes == []
        async with engine.connect() as connection:
            report_count = await connection.scalar(
                text(
                    """
                    SELECT count(*) FROM research_reports
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id},
            )
        assert report_count == 1
    finally:
        provider.release_narratives.set()
        await engine.dispose()


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.asyncio
async def test_different_research_jobs_remain_isolated_while_models_overlap(
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    requests = (
        _create(now, f"research-overlap-{seed}-a-{uuid4()}"),
        _create(now, f"research-overlap-{seed}-b-{uuid4()}", "XSHE:300750"),
    )
    provider = _CoordinatedResearchLLMProvider(expected_narratives=2)
    object_store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)
    try:
        payloads: list[ResearchGenerateRequestV1_1] = []
        for offset, request in enumerate(requests, start=1):
            await repository.create(request)
            payloads.append(
                await _dispatched_payload(
                    repository,
                    request,
                    dispatched_at=now + timedelta(seconds=offset),
                )
            )

        workers = [
            asyncio.create_task(worker_tasks._execute_research_generate(payload, engine=engine))
            for payload in payloads
        ]
        await provider.wait_until_blocked()
        provider.release_narratives.set()
        results = await asyncio.wait_for(asyncio.gather(*workers), timeout=10)

        assert [result.status for result in results] == [
            ResearchSessionStatus.SUCCEEDED,
            ResearchSessionStatus.SUCCEEDED,
        ]
        assert provider.task_types.count("research_report_narrative") == 2
        assert provider.task_types.count("research_score_assessment") == 2
        assert provider.task_types.count("research_support_assessment") == 2
        assert len(object_store.writes) == 2
        assert len({write.suggested_key for write in object_store.writes}) == 2
        for request in requests:
            stored = await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
            assert stored.report is not None
            assert stored.report.instrument_id == request.instrument_id
            assert stored.report.scorecard is not None
            assert stored.report.scorecard.status.value == "scored"
            assert stored.report.scorecard.scoring_basis.value == "synthetic_scenario"
            assert stored.report.scorecard.overall_score is not None
            assert stored.report.scorecard.evidence_confidence == 0
    finally:
        provider.release_narratives.set()
        await engine.dispose()


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.asyncio
async def test_old_worker_cannot_publish_after_failure_is_retried(
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-old-worker-retry-{seed}-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider(expected_narratives=1)
    object_store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        old_worker = asyncio.create_task(
            worker_tasks._execute_research_generate(payload, engine=engine)
        )
        await provider.wait_until_blocked()
        saved = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert saved is not None
        assert saved.narrative is None and saved.report is None

        failed_at = datetime.now(UTC)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(failed_at, f"old-worker-failed-{uuid4()}"),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.RUNNING],
                status=ResearchSessionStatus.FAILED,
                stage=ResearchStage.DEBATING_CASES,
                message="模型暂时不可用。",
                checkpoint="events-analyzed",
                reason_code="MODEL_UNAVAILABLE",
                retryable=True,
            )
        )
        failed = await repository.get(_lookup(request.session_id, failed_at))
        assert failed.session is not None
        assert failed.session.completed_at == failed_at
        retried = await repository.retry(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(
                    failed_at + timedelta(microseconds=1),
                    f"old-worker-retry-{uuid4()}",
                ),
                session_id=request.session_id,
            )
        )
        assert retried.job_id is not None
        assert retried.job_id != request.job_id

        provider.release_narratives.set()
        old_result = await asyncio.wait_for(old_worker, timeout=10)
        assert old_result.status is ResearchSessionStatus.QUEUED
        assert old_result.reason_code == "RESEARCH_JOB_NO_LONGER_ACTIVE"
        current = await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        assert current.session is not None
        assert current.session.job_id == retried.job_id
        assert current.session.status is ResearchSessionStatus.QUEUED
        assert current.session.completed_at is None
        assert current.session.progress.checkpoint == "events-analyzed"
        assert (
            await repository.get_checkpoint(_lookup(request.session_id, datetime.now(UTC)))
        ).checkpoint == saved
        assert provider.task_types == ["research_report_narrative"]
        assert object_store.writes == []
        old_report = await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
        assert old_report.found is False
    finally:
        provider.release_narratives.set()
        await engine.dispose()


@pytest.mark.parametrize("terminal_action", ["cancel", "delete"])
@pytest.mark.parametrize("seed", range(10))
@pytest.mark.asyncio
async def test_cancel_or_delete_while_model_waits_never_publishes(
    monkeypatch: pytest.MonkeyPatch,
    terminal_action: str,
    seed: int,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-model-wait-{terminal_action}-{seed}-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider(expected_narratives=1)
    object_store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        worker = asyncio.create_task(
            worker_tasks._execute_research_generate(payload, engine=engine)
        )
        await provider.wait_until_blocked()

        action_at = datetime.now(UTC)
        cancelled = await repository.cancel(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(action_at, f"model-wait-cancel-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        assert cancelled.status is ResearchSessionStatus.CANCELLED
        cancelled_view = await repository.get(_lookup(request.session_id, action_at))
        assert cancelled_view.session is not None
        assert cancelled_view.session.completed_at == action_at
        if terminal_action == "delete":
            deleted = await repository.delete(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(
                        action_at + timedelta(microseconds=1),
                        f"model-wait-delete-{uuid4()}",
                    ),
                    session_id=request.session_id,
                )
            )
            assert deleted.status is ResearchSessionStatus.DELETED

        provider.release_narratives.set()
        result = await asyncio.wait_for(worker, timeout=10)
        assert result.reason_code == "RESEARCH_JOB_NO_LONGER_ACTIVE"
        assert provider.task_types == ["research_report_narrative"]
        assert object_store.writes == []
        stored_report = await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
        assert stored_report.found is False
        async with engine.connect() as connection:
            stored_status = await connection.scalar(
                text("SELECT status FROM research_sessions WHERE session_id = :session_id"),
                {"session_id": request.session_id},
            )
        assert stored_status == ("deleted" if terminal_action == "delete" else "cancelled")
        async with engine.connect() as connection:
            completed_at = await connection.scalar(
                text("SELECT completed_at FROM research_sessions WHERE session_id = :session_id"),
                {"session_id": request.session_id},
            )
        assert completed_at == action_at
    finally:
        provider.release_narratives.set()
        await engine.dispose()


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.asyncio
async def test_cancel_after_artifact_upload_compensates_before_return(
    monkeypatch: pytest.MonkeyPatch,
    seed: int,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-cancel-after-upload-{seed}-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider()
    object_store = _PausedAfterPutResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        worker = asyncio.create_task(
            worker_tasks._execute_research_generate(payload, engine=engine)
        )
        await asyncio.wait_for(object_store.uploaded.wait(), timeout=10)

        cancelled = await repository.cancel(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(datetime.now(UTC), f"cancel-after-upload-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        assert cancelled.status is ResearchSessionStatus.CANCELLED

        object_store.release.set()
        result = await asyncio.wait_for(worker, timeout=10)
        assert result.status is ResearchSessionStatus.CANCELLED
        assert result.reason_code == "RESEARCH_JOB_NO_LONGER_ACTIVE"
        assert len(object_store.writes) == 1
        assert len(object_store.references) == 1
        assert len(object_store.deletes) == 1
        assert object_store.deletes[0].reference == object_store.references[0]
        stored_report = await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
        assert stored_report.found is False
    finally:
        object_store.release.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_artifact_is_compensated_when_report_save_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(now, f"research-artifact-compensation-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider()
    object_store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)

    async def fail_report_save(
        _repository: PostgresResearchRepository,
        _request: ResearchReportWriteRequest,
    ) -> None:
        raise SQLAlchemyError("forced report save failure")

    monkeypatch.setattr(PostgresResearchRepository, "save_report", fail_report_save)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(
            repository,
            request,
            dispatched_at=now + timedelta(seconds=1),
        )
        result = await worker_tasks._execute_research_generate(payload, engine=engine)

        assert result.status is ResearchSessionStatus.FAILED
        assert result.reason_code == "RESEARCH_DEPENDENCY_UNAVAILABLE"
        assert len(object_store.writes) == 1
        assert len(object_store.references) == 1
        assert len(object_store.deletes) == 1
        assert object_store.deletes[0].reference == object_store.references[0]
        current = await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        assert current.session is not None
        assert current.session.status is ResearchSessionStatus.FAILED
        assert current.session.retryable is True
        async with engine.connect() as connection:
            report_count = await connection.scalar(
                text(
                    """
                    SELECT count(*) FROM research_reports
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id},
            )
        assert report_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_triggered_expiry_preserves_the_purge_deadline_buffer() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(base, "research-get-expiry-purge-buffer-create-0001")
    expiry_time = base + timedelta(seconds=2)
    purge_due_at = expiry_time + timedelta(hours=23, minutes=55)
    try:
        await repository.create(request)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_sessions
                       SET expires_at = :expires_at
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id, "expires_at": expiry_time},
            )

        expired = await repository.get(_lookup(request.session_id, expiry_time))
        assert expired.session is not None
        assert expired.session.status is ResearchSessionStatus.EXPIRED
        async with engine.connect() as connection:
            lifecycle = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT purge_due_at,
                                   (SELECT status FROM research_job_outbox
                                     WHERE session_id = :session_id) AS outbox_status
                              FROM research_sessions
                             WHERE session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert lifecycle["purge_due_at"] == purge_due_at
        assert lifecycle["outbox_status"] == "cancelled"
        before_due = await repository.purge_candidates(now=purge_due_at - timedelta(microseconds=1))
        assert request.session_id not in {item.session_id for item in before_due}
        due = await repository.purge_candidates(now=purge_due_at)
        assert request.session_id in {item.session_id for item in due}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_completion_time_survives_replay_and_job_replacement() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=30)
    request = _create(now, f"failed-time-create-{uuid4()}")
    failed_at = now + timedelta(seconds=2)
    failure = ResearchStageTransitionRequest(
        schema_version="1.0",
        context=_mutation(failed_at, f"failed-time-transition-{uuid4()}"),
        session_id=request.session_id,
        job_id=request.job_id,
        expected_statuses=[ResearchSessionStatus.QUEUED, ResearchSessionStatus.RUNNING],
        status=ResearchSessionStatus.FAILED,
        stage=ResearchStage.COLLECTING_HISTORY,
        message="固定资料失败。",
        reason_code="MODEL_UNAVAILABLE",
        retryable=True,
        result_completeness=ResearchResultCompleteness.EXECUTION_FAILED,
    )
    try:
        await repository.create(request)
        first = await repository.transition(failure)
        assert first.session is not None
        assert first.session.completed_at == failed_at

        repeated = failure.model_copy(
            update={
                "context": _mutation(
                    failed_at + timedelta(seconds=1), f"failed-time-replay-{uuid4()}"
                )
            }
        )
        for replay_request in (failure, repeated):
            replay = await repository.transition(replay_request)
            assert replay.session is not None
            assert replay.session.completed_at == failed_at
            assert replay.session.updated_at == failed_at
        async with engine.connect() as connection:
            transitions = (
                (
                    await connection.execute(
                        text(
                            "SELECT occurred_at FROM research_session_transitions "
                            "WHERE session_id = :session_id AND to_status = 'failed'"
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .scalars()
                .all()
            )
        assert transitions == [failed_at]

        retried = await repository.retry(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(failed_at + timedelta(seconds=2), f"failed-time-retry-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        assert retried.job_id is not None and retried.job_id != request.job_id
        current = await repository.get(_lookup(request.session_id, now))
        assert current.session is not None
        assert current.session.completed_at is None

        with pytest.raises(ResearchTransitionConflictError):
            await repository.transition(repeated)
        current = await repository.get(_lookup(request.session_id, now))
        assert current.session is not None
        assert current.session.job_id == retried.job_id
        assert current.session.status is ResearchSessionStatus.QUEUED
        assert current.session.completed_at is None

        second_at = failed_at + timedelta(seconds=3)
        second = await repository.transition(
            failure.model_copy(
                update={
                    "job_id": retried.job_id,
                    "context": _mutation(second_at, f"failed-time-second-{uuid4()}"),
                }
            )
        )
        assert second.session is not None
        assert second.session.completed_at == second_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_ceiling_prevents_a_fourth_outbox_job() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=30)
    request = _create(base, "research-retry-ceiling-create-0001")
    current_job_id = request.job_id
    try:
        await repository.create(request)
        for attempt in range(1, 4):
            assert await repository.mark_job_dispatched(
                job_id=current_job_id,
                dispatched_at=base + timedelta(seconds=attempt * 2 - 1),
            )
            await repository.transition(
                ResearchStageTransitionRequest(
                    schema_version="1.0",
                    context=_mutation(
                        base + timedelta(seconds=attempt * 2),
                        f"research-retry-ceiling-failure-{attempt:02d}",
                    ),
                    session_id=request.session_id,
                    job_id=current_job_id,
                    expected_statuses=[ResearchSessionStatus.QUEUED],
                    status=ResearchSessionStatus.FAILED,
                    stage=ResearchStage.VALIDATING_REPORT,
                    message="合成失败用于验证重试上限。",
                    reason_code="REPORT_VALIDATION_FAILED",
                    retryable=True,
                )
            )
            if attempt < 3:
                retried = await repository.retry(
                    ResearchSessionMutationRequest(
                        schema_version="1.0",
                        context=_mutation(
                            base + timedelta(seconds=attempt * 2 + 1),
                            f"research-retry-ceiling-retry-{attempt:02d}",
                        ),
                        session_id=request.session_id,
                    )
                )
                assert retried.job_id is not None
                current_job_id = retried.job_id

        exhausted_request = ResearchSessionMutationRequest(
            schema_version="1.0",
            context=_mutation(
                base + timedelta(seconds=8),
                "research-retry-ceiling-exhausted-0001",
            ),
            session_id=request.session_id,
        )
        exhausted = await repository.retry(exhausted_request)
        exhausted_replay = await repository.retry(exhausted_request)
        assert exhausted.status is ResearchSessionStatus.FAILED
        assert exhausted.job_id == current_job_id
        assert exhausted.reason_code == "RESEARCH_RETRY_ATTEMPTS_EXHAUSTED"
        assert exhausted_replay.idempotent_replay is True

        lookup = await repository.get(_lookup(request.session_id, base + timedelta(seconds=9)))
        assert lookup.session is not None
        assert lookup.session.status is ResearchSessionStatus.FAILED
        assert lookup.session.retryable is False
        assert lookup.session.failure_reason_code == "RESEARCH_RETRY_ATTEMPTS_EXHAUSTED"
        assert lookup.session.completed_at == base + timedelta(seconds=6)
        async with engine.connect() as connection:
            attempts = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT count(*) AS count, max(attempt) AS maximum
                              FROM research_job_outbox
                             WHERE session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert dict(attempts) == {"count": 3, "maximum": 3}

        follower = _create(
            base + timedelta(seconds=10),
            "research-retry-ceiling-follower-0001",
            "XSHE:300750",
        )
        await repository.create(follower)
        pending = await repository.pending_jobs()
        scoped_pending = [
            (item.payload.session_id, item.job_id, item.attempt)
            for item in pending
            if item.payload.session_id in {request.session_id, follower.session_id}
        ]
        assert scoped_pending == [(follower.session_id, follower.job_id, 1)]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stalled_retry_ceiling_records_first_terminal_time() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    base = datetime.now(UTC) - timedelta(hours=2)
    request = _create(base, f"stalled-ceiling-create-{uuid4()}")
    current_job_id = request.job_id
    try:
        await repository.create(request)
        for attempt in range(1, 4):
            stalled_at = base + timedelta(minutes=31 * attempt)
            stalled_request = ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(stalled_at, f"stalled-ceiling-mark-{uuid4()}"),
                session_id=request.session_id,
            )
            assert await repository.mark_stalled(stalled_request) == 1
            stalled = await repository.get(_lookup(request.session_id, stalled_at))
            assert stalled.session is not None
            assert stalled.session.status is ResearchSessionStatus.STALLED
            assert stalled.session.completed_at is None
            retried = await repository.retry(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(
                        stalled_at + timedelta(seconds=1), f"stalled-ceiling-retry-{uuid4()}"
                    ),
                    session_id=request.session_id,
                )
            )
            assert retried.job_id is not None
            if attempt < 3:
                assert retried.status is ResearchSessionStatus.QUEUED
                assert retried.job_id != current_job_id
                current_job_id = retried.job_id
            else:
                assert retried.status is ResearchSessionStatus.FAILED
                assert retried.job_id == current_job_id
                assert retried.reason_code == "RESEARCH_RETRY_ATTEMPTS_EXHAUSTED"

        terminal_at = base + timedelta(minutes=93, seconds=1)
        terminal = await repository.get(_lookup(request.session_id, terminal_at))
        assert terminal.session is not None
        assert terminal.session.completed_at == terminal_at
        async with engine.connect() as connection:
            attempts = await connection.scalar(
                text("SELECT count(*) FROM research_job_outbox WHERE session_id = :session_id"),
                {"session_id": request.session_id},
            )
            transitions = (
                (
                    await connection.execute(
                        text(
                            "SELECT occurred_at FROM research_session_transitions "
                            "WHERE session_id = :session_id AND to_status = 'failed'"
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .scalars()
                .all()
            )
        assert attempts == 3
        assert transitions == [terminal_at]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_research_repository_is_idempotent_recoverable_and_bounded() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    # Keep all explicit knowledge times behind PostgreSQL clock_timestamp(); the
    # database deliberately rejects future-dated transition history.
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    primary = _create(now, "research-create-primary-0001")
    try:
        accepted = await repository.create(primary)
        replay = await repository.create(primary)
        assert replay.session_id == accepted.session_id
        assert replay.job_id == accepted.job_id
        assert replay.idempotent_replay is True
        with pytest.raises(ResearchIdempotencyConflictError):
            await repository.create(
                primary.model_copy(
                    update={
                        "instrument_id": "XSHE:300750",
                        "request_sha256": hashlib.sha256(b"XSHE:300750").hexdigest(),
                    }
                )
            )

        await _publish_report(repository, primary, now + timedelta(seconds=1))
        stored = await repository.get_report(
            _lookup(primary.session_id, now + timedelta(seconds=2))
        )
        assert stored.found is True
        assert stored.report is not None
        assert len(stored.report.sections) == 10
        assert stored.report.expires_at == primary.context.requested_at + timedelta(days=7)

        cancellable = _create(
            now + timedelta(seconds=3),
            "research-create-cancel-0001",
            "XSHE:300750",
        )
        await repository.create(cancellable)
        cancel_request = ResearchSessionMutationRequest(
            schema_version="1.0",
            context=_mutation(
                now + timedelta(seconds=4),
                "research-cancel-receipt-0001",
            ),
            session_id=cancellable.session_id,
        )
        cancelled = await repository.cancel(cancel_request)
        cancel_replay = await repository.cancel(cancel_request)
        assert cancelled.status is ResearchSessionStatus.CANCELLED
        assert cancel_replay.job_id == cancelled.job_id
        assert cancel_replay.idempotent_replay is True

        delete_request = ResearchSessionMutationRequest(
            schema_version="1.0",
            context=_mutation(
                now + timedelta(seconds=5),
                "research-delete-receipt-0001",
            ),
            session_id=cancellable.session_id,
        )
        deleted = await repository.delete(delete_request)
        delete_replay = await repository.delete(delete_request)
        assert deleted.status is ResearchSessionStatus.DELETED
        assert delete_replay.idempotent_replay is True

        lookup_stalled_request = _create(
            now - timedelta(hours=1),
            "research-create-lookup-stalled-0001",
            "XSHE:300750",
        )
        await repository.create(lookup_stalled_request)
        lookup_stalled = await repository.get(_lookup(lookup_stalled_request.session_id, now))
        assert lookup_stalled.session is not None
        assert lookup_stalled.session.status is ResearchSessionStatus.STALLED
        async with engine.connect() as connection:
            lookup_stalled_outbox = await connection.scalar(
                text(
                    """
                    SELECT status FROM research_job_outbox
                     WHERE job_id = :job_id
                    """
                ),
                {"job_id": lookup_stalled_request.job_id},
            )
        assert lookup_stalled_outbox == "cancelled"

        stalled_request = _create(
            now - timedelta(hours=1),
            "research-create-stalled-0001",
            "XSHE:300750",
        )
        await repository.create(stalled_request)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(
                    now - timedelta(minutes=59),
                    "research-stalled-checkpoint-0001",
                ),
                session_id=stalled_request.session_id,
                job_id=stalled_request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.COLLECTING_HISTORY,
                message="历史证据检查点已保存。",
                checkpoint="history-collected",
            )
        )
        assert await repository.mark_stalled_due(now=now) == 1
        stalled = await repository.get(_lookup(stalled_request.session_id, now))
        assert stalled.session is not None
        assert stalled.session.status is ResearchSessionStatus.STALLED
        retry_request = ResearchSessionMutationRequest(
            schema_version="1.0",
            context=_mutation(
                now + timedelta(seconds=6),
                "research-retry-receipt-0001",
            ),
            session_id=stalled_request.session_id,
        )
        retried = await repository.retry(retry_request)
        retry_replay = await repository.retry(retry_request)
        assert retried.status is ResearchSessionStatus.QUEUED
        assert retry_replay.job_id == retried.job_id
        assert retry_replay.idempotent_replay is True
        resumed = await repository.get(_lookup(stalled_request.session_id, now))
        assert resumed.session is not None
        assert resumed.session.progress.checkpoint is None
        assert resumed.session.progress.message == "研究任务已重新入队, 将从头执行。"

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_sessions
                       SET expires_at = :expires_at
                     WHERE session_id = :session_id
                    """
                ),
                {
                    "session_id": primary.session_id,
                    "expires_at": stored.report.generated_at + timedelta(seconds=2),
                },
            )
        expiry_time = stored.report.generated_at + timedelta(seconds=3)
        expired_before_scheduler = await repository.get_report(
            _lookup(primary.session_id, expiry_time)
        )
        assert expired_before_scheduler.found is False
        assert expired_before_scheduler.reason_code == "REPORT_EXPIRED"
        assert await repository.expire_due(now=expiry_time) == 1
        expired_report = await repository.get_report(_lookup(primary.session_id, expiry_time))
        assert expired_report.found is False
        assert expired_report.reason_code == "REPORT_EXPIRED"
        candidates = await repository.purge_candidates(now=expiry_time + timedelta(hours=25))
        primary_candidate = next(
            item for item in candidates if item.session_id == primary.session_id
        )
        assert primary_candidate.reference is None
        receipt_id = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO research_web_retrieval_receipts (
                        receipt_id, session_id, query_sha256,
                        candidate_url_sha256, selected_source_id,
                        selected_url_sha256, status, credits_used,
                        rule_version, created_at, expires_at
                    ) VALUES (
                        :receipt_id, :session_id, :query_sha256,
                        CAST(:candidate_url_sha256 AS char(64)[]),
                        :selected_source_id, :selected_url_sha256, 'fetched', 1,
                        'web-fallback.v1', :created_at, :expires_at
                    )
                    """
                ),
                {
                    "receipt_id": receipt_id,
                    "session_id": primary.session_id,
                    "query_sha256": "a" * 64,
                    "candidate_url_sha256": ["b" * 64],
                    "selected_source_id": "registered-official-source",
                    "selected_url_sha256": "b" * 64,
                    "created_at": expiry_time - timedelta(days=1),
                    "expires_at": expiry_time,
                },
            )
        purged = await repository.purge_deleted(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(
                    expiry_time + timedelta(hours=25),
                    "research-purge-primary-0001",
                ),
                session_id=primary.session_id,
            )
        )
        assert purged == 1
        async with engine.connect() as connection:
            tombstone = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT report IS NULL AS body_removed,
                                   report_sha256 IS NOT NULL AS hash_retained,
                                   purged_at IS NOT NULL AS purged
                              FROM research_reports
                             WHERE session_id = :session_id
                            """
                        ),
                        {"session_id": primary.session_id},
                    )
                )
                .mappings()
                .one()
            )
            retrieval_tombstone = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT query_sha256 IS NOT NULL AS query_hash_retained,
                                   cardinality(candidate_url_sha256) = 0 AS candidates_removed,
                                   selected_source_id IS NULL AS source_removed,
                                   selected_url_sha256 IS NULL AS selected_url_removed,
                                   purged_at IS NOT NULL AS purged
                              FROM research_web_retrieval_receipts
                             WHERE receipt_id = :receipt_id
                            """
                        ),
                        {"receipt_id": receipt_id},
                    )
                )
                .mappings()
                .one()
            )
        assert dict(tombstone) == {
            "body_removed": True,
            "hash_retained": True,
            "purged": True,
        }
        assert dict(retrieval_tombstone) == {
            "query_hash_retained": True,
            "candidates_removed": True,
            "source_removed": True,
            "selected_url_removed": True,
            "purged": True,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_purge_batches_do_not_starve_report_cleanup() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    # Publish while the original task deadline is valid, then advance only the
    # lifecycle operation's time. Database publication time remains real.
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=10)
    no_report_session_ids: set[UUID] = set()
    try:
        for index in range(101):
            created_at = base + timedelta(seconds=index * 3)
            request = _create(
                created_at,
                f"research-purge-starvation-create-{index:03d}",
                "XSHE:300750",
            )
            await repository.create(request)
            await repository.cancel(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(
                        created_at + timedelta(seconds=1),
                        f"research-purge-starvation-cancel-{index:03d}",
                    ),
                    session_id=request.session_id,
                )
            )
            await repository.delete(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(
                        created_at + timedelta(seconds=2),
                        f"research-purge-starvation-delete-{index:03d}",
                    ),
                    session_id=request.session_id,
                )
            )
            no_report_session_ids.add(request.session_id)

        report_request = _create(
            base + timedelta(seconds=400),
            "research-purge-starvation-report-create",
        )
        await repository.create(report_request)
        await _publish_report(
            repository,
            report_request,
            base + timedelta(seconds=401),
        )
        deleted_at = datetime.now(UTC)
        await repository.delete(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(
                    deleted_at,
                    "research-purge-starvation-report-delete",
                ),
                session_id=report_request.session_id,
            )
        )

        now = deleted_at + timedelta(hours=25)
        first_batch = await repository.purge_candidates(now=now, limit=100)
        assert len(first_batch) == 100
        assert report_request.session_id not in {candidate.session_id for candidate in first_batch}
        assert all(candidate.session_id in no_report_session_ids for candidate in first_batch)
        for index, candidate in enumerate(first_batch):
            assert (
                await repository.purge_deleted(
                    ResearchSessionMutationRequest(
                        schema_version="1.0",
                        context=_mutation(
                            now,
                            f"research-purge-starvation-first-{index:03d}",
                        ),
                        session_id=candidate.session_id,
                    )
                )
                == 1
            )

        second_batch = await repository.purge_candidates(now=now, limit=100)
        second_ids = {candidate.session_id for candidate in second_batch}
        assert report_request.session_id in second_ids
        assert len(second_ids & no_report_session_ids) == 1
        for index, candidate in enumerate(second_batch):
            assert (
                await repository.purge_deleted(
                    ResearchSessionMutationRequest(
                        schema_version="1.0",
                        context=_mutation(
                            now,
                            f"research-purge-starvation-second-{index:03d}",
                        ),
                        session_id=candidate.session_id,
                    )
                )
                == 1
            )

        remaining = await repository.purge_candidates(now=now, limit=500)
        remaining_ids = {candidate.session_id for candidate in remaining}
        assert report_request.session_id not in remaining_ids
        assert no_report_session_ids.isdisjoint(remaining_ids)
        async with engine.connect() as connection:
            completion = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session.purged_at IS NOT NULL AS session_purged,
                                   report.purged_at IS NOT NULL AS report_purged,
                                   report.report IS NULL AS report_body_removed
                              FROM research_sessions AS session
                              JOIN research_reports AS report
                                ON report.session_id = session.session_id
                             WHERE session.session_id = :session_id
                            """
                        ),
                        {"session_id": report_request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert dict(completion) == {
            "session_purged": True,
            "report_purged": True,
            "report_body_removed": True,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lifecycle_entrypoint_purges_before_the_24_hour_deadline(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    created_at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)
    request = _create(
        created_at,
        "research-lifecycle-boundary-create-0001",
    )
    following = _create(created_at, "research-lifecycle-boundary-create-0002")

    class _DeleteClient:
        failure: Exception | None = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private-provider-message"}},
            "DeleteObject",
        )

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def delete_object(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            assert kwargs["Bucket"] == "synthetic-artifacts"
            assert kwargs["VersionId"] == "synthetic-version-1"
            assert str(kwargs["Key"]).startswith(f"releases/{'a' * 40}/research/")
            if str(request.session_id) in str(kwargs["Key"]) and self.failure is not None:
                raise self.failure
            return {}

    client = _DeleteClient()
    store = S3ObjectStore(client, bucket="synthetic-artifacts", product_sha="b" * 40)
    try:
        for item in (request, following):
            await repository.create(item)
            await _publish_report(
                repository,
                item,
                created_at + timedelta(seconds=1),
                artifact_release_sha="a" * 40,
                artifact_object_version_id="synthetic-version-1",
            )
        deleted_at = datetime.now(UTC)
        boundary = deleted_at + timedelta(hours=23, minutes=56)
        deleted = await repository.delete(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(
                    deleted_at,
                    "research-lifecycle-boundary-delete-0001",
                ),
                session_id=request.session_id,
            )
        )
        assert deleted.status is ResearchSessionStatus.DELETED

        monkeypatch.setattr(
            scheduler_main,
            "get_settings",
            lambda: SimpleNamespace(
                app_env="test",
                enable_r2_synthetic_research=True,
                enable_r2_real_research=False,
                database_url=SecretStr(database_url),
            ),
        )

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET report = report - 'artifact_content_sha256'
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id},
            )
        monkeypatch.setattr(scheduler_main, "_research_store", lambda: store)
        await scheduler_main.maintain_research_lifecycle_once(boundary)
        assert client.calls == []
        async with engine.connect() as connection:
            malformed_retained = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session.purged_at,
                                   report.report IS NOT NULL AS report_body_retained
                              FROM research_sessions AS session
                              JOIN research_reports AS report
                                ON report.session_id = session.session_id
                             WHERE session.session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert malformed_retained["purged_at"] is None
        assert malformed_retained["report_body_retained"] is True
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET report = jsonb_set(
                           report,
                           '{artifact_content_sha256}',
                           to_jsonb(CAST(:content_sha256 AS text))
                       )
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id, "content_sha256": "a" * 64},
            )

        await repository.delete(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(
                    deleted_at + timedelta(microseconds=1),
                    "research-lifecycle-boundary-delete-0002",
                ),
                session_id=following.session_id,
            )
        )
        _stalled, _expired, purged = await scheduler_main.maintain_research_lifecycle_once(boundary)
        assert purged == 1
        assert len(client.calls) == 2
        assert str(request.session_id) in str(client.calls[0]["Key"])
        assert str(following.session_id) in str(client.calls[1]["Key"])
        assert "RESEARCH_ARTIFACT_DELETE_FAILED" in caplog.text
        assert "private-provider-message" not in caplog.text
        async with engine.connect() as connection:
            retained = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session.purged_at,
                                   report.report IS NOT NULL AS report_body_retained
                              FROM research_sessions AS session
                              JOIN research_reports AS report
                                ON report.session_id = session.session_id
                             WHERE session.session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert retained["purged_at"] is None
        assert retained["report_body_retained"] is True

        for failure in (
            ValueError("unexpected-program-error"),
            RuntimeError("unexpected-program-error"),
        ):
            client.failure = failure
            with pytest.raises(type(failure), match="unexpected-program-error"):
                await scheduler_main.maintain_research_lifecycle_once(boundary)

        client.failure = None
        _stalled, _expired, purged = await scheduler_main.maintain_research_lifecycle_once(boundary)
        assert purged == 1

        async with engine.connect() as connection:
            completion = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session.purge_due_at, session.purged_at,
                                   report.report IS NULL AS report_body_removed
                              FROM research_sessions AS session
                              JOIN research_reports AS report
                                ON report.session_id = session.session_id
                             WHERE session.session_id = :session_id
                            """
                        ),
                        {"session_id": request.session_id},
                    )
                )
                .mappings()
                .one()
            )
        assert completion["purge_due_at"] == deleted_at + timedelta(hours=23, minutes=55)
        assert completion["purged_at"] <= deleted_at + timedelta(hours=24)
        assert completion["report_body_removed"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_macro_reference_memory_rejects_equity_current_and_model_scopes() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    now = datetime.now(UTC).replace(microsecond=0)
    statement = text(
        """
        INSERT INTO macro_reference_facts (
            fact_id, namespace, subject_scope, temporal_stability, origin_type,
            subject, predicate, object_value, fact_class,
            interpretation_rule_version, valid_from, valid_until,
            first_seen_at, revision_known_at, revision, evidence_ids,
            content_sha256
        ) VALUES (
            :fact_id, 'macro.reference.policy', :subject_scope, :temporal_stability,
            :origin_type, 'policy-rate', 'definition', CAST(:object_value AS jsonb),
            'definition', 'macro-reference.v1', :now, NULL, :now, :now, 1,
            ARRAY[:evidence_id]::uuid[], :content_sha256
        )
        """
    )

    async def insert_reference(
        *,
        subject_scope: str,
        temporal_stability: str,
        origin_type: str,
    ) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                statement,
                {
                    "fact_id": uuid4(),
                    "subject_scope": subject_scope,
                    "temporal_stability": temporal_stability,
                    "origin_type": origin_type,
                    "object_value": '{"meaning":"stable reference"}',
                    "now": now,
                    "evidence_id": uuid4(),
                    "content_sha256": uuid4().hex * 2,
                },
            )

    try:
        await insert_reference(
            subject_scope="macro",
            temporal_stability="stable",
            origin_type="deterministic_reference",
        )
        for invalid in (
            {
                "subject_scope": "equity",
                "temporal_stability": "stable",
                "origin_type": "deterministic_reference",
            },
            {
                "subject_scope": "macro",
                "temporal_stability": "current",
                "origin_type": "deterministic_reference",
            },
            {
                "subject_scope": "macro",
                "temporal_stability": "stable",
                "origin_type": "model_conclusion",
            },
        ):
            with pytest.raises(IntegrityError):
                await insert_reference(**invalid)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("real", "durable"), [(False, "legacy"), (True, "legacy"), (True, "document"), (False, "patch")]
)
async def test_checkpoint_survives_retry_and_rejects_old_job_owner_cancel_and_deadline(
    real: bool,
    durable: str,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=5)
    request = _create(now, f"checkpoint-create-{uuid4()}")
    if real:
        request = request.model_copy(
            update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
        )
    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(now, f"checkpoint-running-{uuid4()}"),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.AUDITING_DATA if real else ResearchStage.ANALYZING_EVENTS,
                message="正在核对资料。",
                checkpoint="events-analyzed",
            )
        )
        current = (await repository.get(_lookup(request.session_id, now))).session
        assert current is not None
        assert current.source_mode == request.source_mode
        assert payload.context.deadline_at is not None
        checkpoint = ResearchCheckpoint(
            schema_version="1.9" if durable == "document" else "1.3" if real else "1.0",
            acquisition=ResearchAcquisitionState(
                schema_version="1.3" if durable == "document" else "1.0",
                rule_version=WEB_FALLBACK_RULE_VERSION,
                instrument_id=request.instrument_id,
                requested_as_of=now,
                deadline_at=payload.context.deadline_at,
                input_sha256="a" * 64,
                attempts={"identity.company": 3},
            )
            if real
            else None,
            generator_version="checkpoint-test-v1",
            stage=ResearchStage.ANALYZING_EVENTS,
            packet=empty_real_evidence_packet(current)
            if real
            else synthetic_evidence_packet(
                instrument_id=request.instrument_id,
                instrument_name=request.instrument_name,
                as_of=now,
            ),
        )
        if durable == "document":
            from tests.unit.qmr.test_research_fallback import _document_fixture

            from stock_assistant.contracts.discovery import (
                ResearchAcquisitionCandidate,
                ResearchSourceSpan,
            )

            document, _registration = _document_fixture(
                "[PDF page 9]\n603986 兆易创新\n原文身份。", now, pdf=True
            )
            assert checkpoint.acquisition is not None
            span = ResearchSourceSpan(start=0, end=len(document.body))
            checkpoint.acquisition.documents = [document]
            checkpoint.acquisition.active_gap = "identity.company"
            checkpoint.acquisition.active_fact_keys = ["identity.company"]
            checkpoint.acquisition.candidates = [
                ResearchAcquisitionCandidate(
                    candidate=document.candidate,
                    content_sha256=document.content_sha256,
                    first_seen_at=document.first_seen_at,
                    revision_known_at=document.revision_known_at,
                    document_id=document.document_id,
                    context_spans=[span],
                    fact_spans={"identity.company": [span]},
                    requested_fact_keys=["identity.company"],
                    attempt_numbers={"identity.company": 3},
                    extraction_complete=False,
                    facts=[],
                    publication_basis=document.publication_basis,
                    discovery_request_sha256=document.discovery_request_sha256,
                )
            ]
            checkpoint = checkpoint.model_copy(update={"schema_version": "1.9"})
        elif durable == "patch":
            from tests.unit.qmr.test_research_report_generator import (
                _IndependentFeedbackRepairProvider,
            )

            pending: list[ResearchCheckpoint] = []

            async def stop_at_patch(value: ResearchCheckpoint) -> None:
                pending.append(value.model_copy(deep=True))
                if value.narrative_patch is not None:
                    raise asyncio.CancelledError

            with pytest.raises(asyncio.CancelledError):
                await ResearchReportGenerator(_IndependentFeedbackRepairProvider()).generate(
                    session=current,
                    packet=checkpoint.packet,
                    save_checkpoint=stop_at_patch,
                )
            checkpoint = ResearchCheckpoint.model_validate_json(pending[-1].model_dump_json())
            assert (
                checkpoint.narrative_repair_reserved and checkpoint.support_assessment is not None
            )
        write = ResearchCheckpointWriteRequest(
            schema_version="1.0",
            context=payload.context.model_copy(update={"requested_at": datetime.now(UTC)}),
            session_id=request.session_id,
            job_id=request.job_id,
            checkpoint=checkpoint,
        )
        await repository.save_checkpoint(write)
        persisted = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert persisted is not None
        assert persisted == checkpoint, [
            name
            for name in type(checkpoint).model_fields
            if getattr(persisted, name) != getattr(checkpoint, name)
        ]
        if durable != "legacy":
            assert (
                await _checkpoint_from_fresh_process(database_url, _lookup(request.session_id, now))
                == checkpoint
            )
        wrong_owner = _lookup(request.session_id, now).model_copy(
            update={
                "context": _lookup(request.session_id, now).context.model_copy(
                    update={"actor_id": "other-owner"}
                ),
            }
        )
        assert not (await repository.get_checkpoint(wrong_owner)).found
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_checkpoint(
                write.model_copy(
                    update={
                        "context": write.context.model_copy(update={"actor_id": "other-owner"}),
                    }
                )
            )
        with pytest.raises(TimeoutError):
            await repository.save_checkpoint(
                write.model_copy(
                    update={
                        "context": write.context.model_copy(
                            update={"requested_at": payload.context.deadline_at}
                        ),
                    }
                )
            )
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_checkpoint(
                write.model_copy(
                    update={
                        "checkpoint": checkpoint.model_copy(
                            update={
                                "packet": synthetic_evidence_packet(
                                    instrument_id="XSHE:300750",
                                    instrument_name="宁德时代",
                                    as_of=now,
                                ),
                            }
                        ),
                    }
                )
            )
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(datetime.now(UTC), f"checkpoint-failed-{uuid4()}"),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.RUNNING],
                status=ResearchSessionStatus.FAILED,
                stage=ResearchStage.DEBATING_CASES,
                message="模型暂时不可用。",
                checkpoint="events-analyzed",
                reason_code="MODEL_UNAVAILABLE",
                retryable=True,
                result_completeness=ResearchResultCompleteness.EXECUTION_FAILED,
            )
        )
        retried = await repository.retry(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(datetime.now(UTC), f"checkpoint-retry-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        view = (await repository.get(_lookup(request.session_id, datetime.now(UTC)))).session
        assert view is not None and view.progress.checkpoint == "events-analyzed"
        assert view.result_completeness is None
        assert (
            await repository.get_checkpoint(_lookup(request.session_id, now))
        ).checkpoint == checkpoint
        if durable != "legacy":
            assert (
                await _checkpoint_from_fresh_process(database_url, _lookup(request.session_id, now))
                == checkpoint
            )
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_checkpoint(write)
        assert retried.job_id is not None
        new_payload = next(
            item.payload
            for item in await repository.pending_jobs()
            if item.job_id == retried.job_id
        )
        assert new_payload.context.deadline_at != payload.context.deadline_at
        if real:
            assert checkpoint.acquisition is not None
            assert checkpoint.acquisition.attempts == {"identity.company": 3}
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(datetime.now(UTC), f"checkpoint-resumed-{uuid4()}"),
                session_id=request.session_id,
                job_id=retried.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.DEBATING_CASES,
                message="继续已保存研究。",
                checkpoint="events-analyzed",
            )
        )
        await repository.save_checkpoint(
            write.model_copy(
                update={
                    "job_id": retried.job_id,
                    "context": new_payload.context,
                }
            )
        )
        await repository.cancel(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(datetime.now(UTC), f"checkpoint-cancel-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        with pytest.raises(ResearchTransitionConflictError):
            await repository.save_checkpoint(
                write.model_copy(
                    update={
                        "job_id": retried.job_id,
                        "context": new_payload.context,
                    }
                )
            )
        deleted_at = datetime.now(UTC)
        await repository.delete(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(deleted_at, f"checkpoint-delete-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        assert not (await repository.get_checkpoint(_lookup(request.session_id, deleted_at))).found
        await repository.purge_deleted(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(deleted_at + timedelta(hours=24), f"checkpoint-purge-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        async with engine.connect() as connection:
            assert await connection.scalar(
                text(
                    "SELECT checkpoint_payload IS NULL FROM research_sessions WHERE session_id=:id"
                ),
                {"id": request.session_id},
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("execution_layer", ["generator", "worker"])
@pytest.mark.asyncio
async def test_generation_deadline_preserves_checkpoint_and_reaches_failed_terminal(
    monkeypatch: pytest.MonkeyPatch,
    execution_layer: str,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=10)
    request = _create(now, f"generation-deadline-{execution_layer}-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider(expected_narratives=1)
    object_store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=object_store)
    generation: asyncio.Task[ResearchReport] | asyncio.Task[ResearchGenerateResult]
    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        if execution_layer == "generator":
            await repository.transition(
                ResearchStageTransitionRequest(
                    schema_version="1.0",
                    context=_mutation(now, f"generation-deadline-running-{uuid4()}"),
                    session_id=request.session_id,
                    job_id=request.job_id,
                    expected_statuses=[ResearchSessionStatus.QUEUED],
                    status=ResearchSessionStatus.RUNNING,
                    stage=ResearchStage.DEBATING_CASES,
                    message="正在核对资料。",
                    checkpoint="events-analyzed",
                )
            )
        deadline_at = datetime.now(UTC) + timedelta(milliseconds=500)
        payload = payload.model_copy(
            update={"context": payload.context.model_copy(update={"deadline_at": deadline_at})}
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE research_job_outbox SET payload = CAST(:payload AS jsonb) "
                    "WHERE job_id = :job_id"
                ),
                {"payload": payload.model_dump_json(), "job_id": request.job_id},
            )

        if execution_layer == "generator":
            session = (await repository.get(_lookup(request.session_id, now))).session
            assert session is not None

            async def save_checkpoint(checkpoint: ResearchCheckpoint) -> None:
                await repository.save_checkpoint(
                    ResearchCheckpointWriteRequest(
                        schema_version="1.0",
                        context=payload.context,
                        session_id=request.session_id,
                        job_id=request.job_id,
                        checkpoint=checkpoint,
                    )
                )

            generation = asyncio.create_task(
                ResearchReportGenerator(provider).generate(
                    session=session,
                    packet=synthetic_evidence_packet(
                        instrument_id=request.instrument_id,
                        instrument_name=request.instrument_name,
                        as_of=now,
                    ),
                    save_checkpoint=save_checkpoint,
                    deadline_at=deadline_at,
                )
            )
        else:
            # Let the inner provider deadline fire first, including the real database
            # cleanup fence, instead of the worker's outer timer masking that path.
            monkeypatch.setattr(
                worker_tasks, "utc_now", lambda: datetime.now(UTC) - timedelta(milliseconds=300)
            )
            generation = asyncio.create_task(
                worker_tasks._execute_research_generate(payload, engine=engine)
            )

        await provider.wait_until_blocked()
        before = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert before is not None and before.available_result is not None
        assert before.available_result.claims
        assert before.report is None

        if execution_layer == "generator":
            with pytest.raises(ResearchGenerationError) as captured:
                await asyncio.wait_for(generation, timeout=5)
            assert captured.value.reason_code == "MODEL_TIMEOUT"
            assert captured.value.available_result is not None
            # Host and container wall clocks can differ by a few milliseconds.
            # Establish that this write is late according to the database itself.
            async with asyncio.timeout(2):
                async with engine.connect() as connection:
                    while True:
                        database_now = await connection.scalar(text("SELECT clock_timestamp()"))
                        assert isinstance(database_now, datetime)
                        if database_now >= deadline_at:
                            break
                        await asyncio.sleep(0.01)
            # The caller timestamp is still before the deadline. PostgreSQL time must
            # reject the late write without replacing the earlier durable snapshot.
            with pytest.raises(TimeoutError):
                await repository.save_checkpoint(
                    ResearchCheckpointWriteRequest(
                        schema_version="1.0",
                        context=payload.context,
                        session_id=request.session_id,
                        job_id=request.job_id,
                        checkpoint=before.model_copy(update={"provider_calls": 99}),
                    )
                )
        else:
            result = await asyncio.wait_for(generation, timeout=5)
            assert isinstance(result, ResearchGenerateResult)
            assert result.status is ResearchSessionStatus.FAILED
            assert result.reason_code == "MODEL_TIMEOUT"
            current = (await repository.get(_lookup(request.session_id, now))).session
            assert current is not None
            assert current.status is ResearchSessionStatus.FAILED
            assert current.result_completeness is ResearchResultCompleteness.EXECUTION_FAILED
            assert current.completed_at is not None
            assert current.completed_at == current.updated_at
            assert current.available_result is not None

        after = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert after == before
        assert provider.task_types == ["research_report_narrative"]
        assert object_store.writes == []
        assert not (await repository.get_report(_lookup(request.session_id, now))).found
    finally:
        provider.release_narratives.set()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["deepseek", "tavily"])
async def test_provider_permit_is_cross_connection_bounded_and_released_on_cancel(
    provider: Literal["deepseek", "tavily"],
) -> None:
    database_url = _database_url()
    first_engine = create_async_engine(database_url)
    second_engine = create_async_engine(database_url)
    first = PostgresResearchRepository(first_engine)
    second = PostgresResearchRepository(second_engine)
    entered = asyncio.Event()
    hold = asyncio.Event()
    deadline = datetime.now(UTC) + timedelta(seconds=5)

    async def occupy() -> None:
        async with first.provider_permit(provider=provider, limit=1, deadline_at=deadline):
            entered.set()
            await hold.wait()

    task = asyncio.create_task(occupy())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        with pytest.raises(TimeoutError):
            async with second.provider_permit(
                provider=provider,
                limit=1,
                deadline_at=datetime.now(UTC) + timedelta(milliseconds=150),
            ):
                pytest.fail("a second database connection exceeded the provider cap")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with second.provider_permit(provider=provider, limit=1, deadline_at=deadline):
            pass
    finally:
        hold.set()
        await asyncio.gather(task, return_exceptions=True)
        await first_engine.dispose()
        await second_engine.dispose()


@pytest.mark.asyncio
async def test_worker_artifact_retry_reuses_persisted_packet_and_complete_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=2)
    request = _create(now, f"worker-checkpoint-recovery-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider()
    generator_calls: list[ResearchCheckpoint | None] = []
    checkpoint_versions: list[str] = []
    original_save = PostgresResearchRepository.save_checkpoint

    async def capture_checkpoint(
        self: PostgresResearchRepository, request: ResearchCheckpointWriteRequest
    ) -> ResearchCheckpointLookupResult:
        checkpoint_versions.append(request.checkpoint.schema_version)
        return await original_save(self, request)

    monkeypatch.setattr(PostgresResearchRepository, "save_checkpoint", capture_checkpoint)

    class CheckpointConsumer:
        """Exercise the worker/repository boundary independently of narrative policy changes."""

        def __init__(self, active_provider: LLMProviderPort) -> None:
            self.delegate = ResearchReportGenerator(active_provider)

        async def generate(
            self,
            *,
            session: ResearchSessionView,
            packet: ResearchEvidencePacket,
            checkpoint: ResearchCheckpoint | None = None,
            save_checkpoint: Callable[[ResearchCheckpoint], Awaitable[None]] | None = None,
            deadline_at: datetime | None = None,
            progress: Callable[[ResearchStage, str], Awaitable[None]] | None = None,
        ) -> ResearchReport:
            generator_calls.append(checkpoint)
            if checkpoint is not None and checkpoint.report is not None:
                return checkpoint.report
            return await self.delegate.generate(
                session=session,
                packet=packet,
                save_checkpoint=save_checkpoint,
                deadline_at=deadline_at,
                progress=progress,
            )

    class FailsFirstUpload(_ResearchObjectStore):
        failed = False

        async def put(
            self, request: ObjectWriteRequest, chunks: AsyncIterator[bytes]
        ) -> StoredObjectRef:
            if not self.failed:
                self.failed = True
                raise OSError("synthetic object store interruption")
            return await super().put(request, chunks)

    store = FailsFirstUpload()
    _configure_worker(monkeypatch, provider=provider, object_store=store)
    monkeypatch.setattr(worker_tasks, "ResearchReportGenerator", CheckpointConsumer)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        first = await worker_tasks._execute_research_generate(payload, engine=engine)
        assert first.status is ResearchSessionStatus.FAILED
        assert first.reason_code == "RESEARCH_DEPENDENCY_UNAVAILABLE"
        snapshot = (
            await repository.get_checkpoint(_lookup(request.session_id, datetime.now(UTC)))
        ).checkpoint
        assert snapshot is not None and snapshot.report is not None
        assert checkpoint_versions[0] == "1.9"
        assert checkpoint_versions[1:] and set(checkpoint_versions[1:]) == {"1.9"}
        assert snapshot.schema_version == "1.9"
        assert not snapshot.narrative_repair_reserved
        assert snapshot.scorecard == snapshot.report.scorecard
        assert snapshot.scorecard is not None and snapshot.scorecard.overall_score is not None
        first_call_count = len(provider.task_types)
        assert first_call_count > 0
        failed_view = (await repository.get(_lookup(request.session_id, datetime.now(UTC)))).session
        assert failed_view is not None
        assert failed_view.result_completeness is ResearchResultCompleteness.EXECUTION_FAILED
        retried = await repository.retry(
            ResearchSessionMutationRequest(
                schema_version="1.0",
                context=_mutation(datetime.now(UTC), f"worker-resume-{uuid4()}"),
                session_id=request.session_id,
            )
        )
        retry_payload = next(
            item.payload
            for item in await repository.pending_jobs()
            if item.job_id == retried.job_id
        )

        def must_not_collect_again(**_arguments: object) -> ResearchEvidencePacket:
            pytest.fail("retry discarded its durable evidence checkpoint")

        monkeypatch.setattr(worker_tasks, "synthetic_evidence_packet", must_not_collect_again)
        second = await worker_tasks._execute_research_generate(retry_payload, engine=engine)
        assert second.status is ResearchSessionStatus.SUCCEEDED
        assert checkpoint_versions[0] == "1.9"
        assert set(checkpoint_versions[1:]) == {"1.9"}
        assert len(provider.task_types) == first_call_count
        assert len(generator_calls) == 2 and generator_calls[1] is not None
        assert generator_calls[1].report is not None
        complete_view = (
            await repository.get(_lookup(request.session_id, datetime.now(UTC)))
        ).session
        assert complete_view is not None
        assert complete_view.result_completeness is ResearchResultCompleteness.COMPLETE
        replay = await worker_tasks._execute_research_generate(retry_payload, engine=engine)
        assert replay.status is ResearchSessionStatus.SUCCEEDED
        assert len(store.writes) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_boundary", ["normal", "cancel", "deadline", "blocked", "sql_fault", "repo_fault"]
)
async def test_audited_limited_result_finishes_without_complete_report(
    monkeypatch: pytest.MonkeyPatch, terminal_boundary: str
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine: AsyncEngine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=10)
    request = _create(now, f"limited-result-{uuid4()}")
    provider = _CoordinatedResearchLLMProvider()
    provider._delegate = SyntheticResearchLLMProvider(failure_mode="unsupported_hypothesis")
    store = _ResearchObjectStore()
    _configure_worker(monkeypatch, provider=provider, object_store=store)
    original_transition = PostgresResearchRepository.transition
    blocked_transition_cancelled = False

    async def at_terminal_boundary(
        self: PostgresResearchRepository, transition: ResearchStageTransitionRequest
    ) -> ResearchSessionLookupResult:
        nonlocal blocked_transition_cancelled
        if (
            transition.status is ResearchSessionStatus.SUCCEEDED
            and transition.result_completeness is ResearchResultCompleteness.PARTIAL
        ):
            if terminal_boundary == "cancel":
                await self.cancel(
                    ResearchSessionMutationRequest(
                        schema_version="1.0",
                        context=_mutation(datetime.now(UTC), f"cancel-limited-{uuid4()}"),
                        session_id=request.session_id,
                    )
                )
            elif terminal_boundary == "deadline":
                assert transition.context.deadline_at is not None
                late = transition.context.deadline_at + timedelta(microseconds=1)
                transition = transition.model_copy(
                    update={"context": transition.context.model_copy(update={"requested_at": late})}
                )
            elif terminal_boundary == "blocked":
                try:
                    await asyncio.Event().wait()
                finally:
                    blocked_transition_cancelled = True
            elif terminal_boundary == "sql_fault":
                raise SQLAlchemyError("synthetic one-time terminal transition fault")
            elif terminal_boundary == "repo_fault":
                raise ResearchRepositoryError("synthetic one-time terminal transition fault")
        return await original_transition(self, transition)

    monkeypatch.setattr(PostgresResearchRepository, "transition", at_terminal_boundary)
    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        if terminal_boundary == "blocked":
            payload = payload.model_copy(
                update={
                    "context": payload.context.model_copy(
                        update={"deadline_at": datetime.now(UTC) + timedelta(seconds=2)}
                    )
                }
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE research_job_outbox SET payload = CAST(:payload AS jsonb) "
                        "WHERE job_id = :job_id"
                    ),
                    {"payload": payload.model_dump_json(), "job_id": payload.job_id},
                )
        result = await asyncio.wait_for(
            worker_tasks._execute_research_generate(payload, engine=engine), timeout=5
        )
        view = (await repository.get(_lookup(request.session_id, datetime.now(UTC)))).session
        assert view is not None
        assert result.report_id is None and not store.writes
        assert not (
            await repository.get_report(_lookup(request.session_id, datetime.now(UTC)))
        ).found
        assert view.available_result is not None
        assert "月球" not in view.available_result.model_dump_json()
        calls = list(provider.task_types)
        assert calls.count("research_report_narrative") == 2
        # This fixture returns the identical rejected hypothesis in its patch.
        # Unchanged content keeps its existing verdict rather than paying for a
        # second audit. Still prove the rejection and all terminal protections.
        assert calls.count("research_support_assessment") == 1
        saved = (
            await repository.get_checkpoint(_lookup(request.session_id, datetime.now(UTC)))
        ).checkpoint
        assert saved is not None and saved.narrative_candidate is not None
        assert saved.narrative_patch is not None and saved.support_assessment is not None
        assert _patch_projection(saved.narrative_candidate, saved.narrative_patch) == (
            saved.narrative_candidate
        )
        assert saved.support_rounds_reserved == 1 and not saved.support_pending_ids
        assert any(
            verdict.item_id == "hypothesis:0" and not verdict.supported
            for verdict in saved.support_assessment.verdicts
        )
        if terminal_boundary == "normal":
            assert result.status is ResearchSessionStatus.SUCCEEDED
            assert view.status is ResearchSessionStatus.SUCCEEDED
            assert view.result_completeness is ResearchResultCompleteness.PARTIAL
            assert view.progress.stage is ResearchStage.COMPLETED
            assert view.failure_reason_code is None and not view.retryable
            assert view.available_result.reader_edition is not None
            assert "分析受限" in view.progress.message
            replay = await worker_tasks._execute_research_generate(payload, engine=engine)
            assert replay.status is ResearchSessionStatus.SUCCEEDED and replay.report_id is None
            assert provider.task_types == calls
        elif terminal_boundary == "cancel":
            assert result.status is ResearchSessionStatus.CANCELLED
            assert view.status is ResearchSessionStatus.CANCELLED
        else:
            assert result.status is ResearchSessionStatus.FAILED
            assert result.reason_code == (
                "RESEARCH_DEPENDENCY_UNAVAILABLE"
                if terminal_boundary in {"sql_fault", "repo_fault"}
                else "RESEARCH_TASK_DEADLINE_EXCEEDED"
            )
            assert view.result_completeness is ResearchResultCompleteness.EXECUTION_FAILED
            assert view.retryable
            if terminal_boundary == "blocked":
                assert blocked_transition_cancelled
    finally:
        await engine.dispose()


async def _checkpoint_from_fresh_process(
    database_url: str, lookup: ResearchSessionLookupRequest
) -> ResearchCheckpoint | None:
    child = """
import asyncio, json, sys
from sqlalchemy.ext.asyncio import create_async_engine
from stock_assistant.contracts.research import ResearchSessionLookupRequest
from stock_assistant.platform.db.research import PostgresResearchRepository
async def main():
    data = json.loads(sys.stdin.read())
    engine = create_async_engine(data['database_url'])
    try:
        lookup = ResearchSessionLookupRequest.model_validate(data['lookup'])
        result = await PostgresResearchRepository(engine).get_checkpoint(lookup)
        print(result.model_dump_json())
    finally:
        await engine.dispose()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        child,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(
            process.communicate(
                json.dumps(
                    {
                        "database_url": database_url,
                        "lookup": lookup.model_dump(mode="json"),
                    }
                ).encode()
            ),
            timeout=30,
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, error.decode()
    return ResearchCheckpointLookupResult.model_validate_json(output).checkpoint
