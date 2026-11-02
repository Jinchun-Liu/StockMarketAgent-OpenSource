"""PostgreSQL persistence for R2 on-demand research sessions and reports."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Literal, cast
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.messaging import MessageEnvelope, MessagePublishRequest
from stock_assistant.contracts.research import (
    CoverageGap,
    ResearchAnalysisConfig,
    ResearchAvailableResult,
    ResearchCheckpoint,
    ResearchCheckpointLookupResult,
    ResearchCheckpointWriteRequest,
    ResearchCutoffMode,
    ResearchGenerateRequestV1_1,
    ResearchPendingJob,
    ResearchProgress,
    ResearchPurgeCandidate,
    ResearchRealSourceMode,
    ResearchReportLookupResult,
    ResearchReportReadyV1,
    ResearchReportWriteRequest,
    ResearchResultCompleteness,
    ResearchSessionAccepted,
    ResearchSessionCreateRequest,
    ResearchSessionListQuery,
    ResearchSessionLookupRequest,
    ResearchSessionLookupResult,
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
    ResearchSessionPage,
    ResearchSessionStatus,
    ResearchSessionView,
    ResearchSourceMode,
    ResearchStage,
    ResearchStageTransitionRequest,
    effective_research_cutoff_mode,
    parse_research_generate_request,
    validate_stored_research_report,
)
from stock_assistant.contracts.storage import RetentionClass, StoredObjectRef
from stock_assistant.platform.db.recovery import PostgresOutboxPublisher, PostgresUnitOfWork

RESEARCH_TASK_NAME: Final[Literal["stock_assistant.research.generate_report.v1"]] = (
    "stock_assistant.research.generate_report.v1"
)
RESEARCH_REPORT_READY_EVENT: Final = "com.stockassistant.research.report.ready.v1"
RESEARCH_MAX_JOB_ATTEMPTS: Final = 3
_EVENT_NAMESPACE: Final = UUID("eb5aa22d-f0d0-5b8a-b129-37c1f4bbc94b")
_IDEMPOTENCY_LOCK_NAMESPACE: Final = 24_187_001
_MUTATION_LOCK_NAMESPACE: Final = 24_187_002
_OWNER_QUOTA_LOCK_NAMESPACE: Final = 24_187_003
_PURGE_SCHEDULER_BUFFER: Final = timedelta(minutes=5)
_PURGE_DELAY: Final = timedelta(hours=24) - _PURGE_SCHEDULER_BUFFER
_PRIVATE_PRINCIPAL_ACTOR: Final = re.compile(r"^[a-f0-9]{64}$")
_MAX_ACTIVE_PRIVATE_SESSIONS: Final = 5
_MAX_DAILY_PRIVATE_SESSION_CREATES: Final = 20
_RESEARCH_TASK_ATTEMPT_WINDOW: Final = timedelta(minutes=30)
_REPORT_READ_TASK_TYPE: Final = "research_report_retained_read"
_LOGGER = logging.getLogger(__name__)


class _ReportCompatibilityFailureCategory(StrEnum):
    INTEGRITY_METADATA_MISMATCH = "integrity_metadata_mismatch"
    RETAINED_VALIDATION_FAILED = "retained_validation_failed"


def _log_report_compatibility_rejection(
    *,
    correlation_id: UUID,
    failure_category: _ReportCompatibilityFailureCategory,
) -> None:
    _LOGGER.warning(
        "research_report_compatibility_rejected "
        "task_type=research_report_retained_read correlation_id=%s failure_category=%s",
        correlation_id,
        failure_category.value,
        extra={
            "event": "research_report_compatibility_rejected",
            "task_type": _REPORT_READ_TASK_TYPE,
            "correlation_id": str(correlation_id),
            "failure_category": failure_category.value,
        },
    )


class ResearchRepositoryError(RuntimeError):
    """Stable repository failure without SQL or report-content leakage."""


class ResearchIdempotencyConflictError(ResearchRepositoryError):
    """An idempotency key was reused for a different session request."""


class ResearchTransitionConflictError(ResearchRepositoryError):
    """A state transition did not match the current session state."""


class ResearchQuotaExceededError(ResearchRepositoryError):
    """A private-preview principal exceeded a bounded research-session quota."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _owner_principal_sha256(actor_id: str) -> str:
    return hashlib.sha256(actor_id.encode("utf-8")).hexdigest()


def _job_payload(
    *,
    session_id: UUID,
    job_id: UUID,
    source_context: MutationContext,
    cutoff: datetime,
    cutoff_mode: ResearchCutoffMode,
) -> ResearchGenerateRequestV1_1:
    return ResearchGenerateRequestV1_1(
        schema_version="1.1",
        context=source_context.model_copy(
            update={
                "as_of": cutoff,
                "deadline_at": source_context.requested_at + _RESEARCH_TASK_ATTEMPT_WINDOW,
                "idempotency_key": f"research-generate-{job_id}",
            }
        ),
        session_id=session_id,
        job_id=job_id,
        cutoff_mode=cutoff_mode,
    )


def _encode_cursor(created_at: datetime, session_id: UUID) -> str:
    raw = _canonical([created_at.isoformat(), str(session_id)]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError
        created_at = datetime.fromisoformat(payload[0])
        session_id = UUID(payload[1])
        if created_at.tzinfo is None:
            raise ValueError
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ResearchRepositoryError("invalid research session cursor") from error
    return created_at, session_id


def _view(row: RowMapping) -> ResearchSessionView:
    return ResearchSessionView(
        schema_version="1.0",
        session_id=cast(UUID, row["session_id"]),
        job_id=cast(UUID, row["job_id"]),
        instrument_id=cast(str, row["instrument_id"]),
        instrument_name=cast(str, row["instrument_name"]),
        as_of=cast(datetime, row["as_of"]),
        analysis_config=ResearchAnalysisConfig.model_validate(row["analysis_config"]),
        source_mode=(
            ResearchRealSourceMode.DEVELOPMENT_ONLY
            if row["source_mode"] == "real_development_only"
            else ResearchSourceMode(cast(str, row["source_mode"]))
        ),
        status=ResearchSessionStatus(cast(str, row["status"])),
        progress=ResearchProgress(
            stage=ResearchStage(cast(str, row["stage"])),
            message=cast(str, row["progress_message"]),
            latest_progress_at=cast(datetime, row["heartbeat_at"]),
            checkpoint=cast("str | None", row["checkpoint"]),
        ),
        coverage_gaps=[CoverageGap.model_validate(item) for item in row["coverage_gaps"]],
        failure_reason_code=cast("str | None", row["failure_reason_code"]),
        retryable=bool(row["retryable"]),
        result_completeness=(
            ResearchResultCompleteness(row["result_completeness"])
            if row["result_completeness"] is not None
            and row["status"] not in {"expired", "deleted"}
            else None
        ),
        available_result=(
            ResearchAvailableResult.model_validate(row["available_result"])
            if row["available_result"] is not None and row["status"] not in {"expired", "deleted"}
            else None
        ),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        completed_at=cast("datetime | None", row["completed_at"]),
        expires_at=cast(datetime, row["expires_at"]),
        deleted_at=cast("datetime | None", row["deleted_at"]),
    )


_SESSION_COLUMNS: Final = """
    session_id, job_id, instrument_id, instrument_name, as_of, cutoff_mode,
    analysis_config, source_mode, status, stage, progress_message,
    heartbeat_at, checkpoint, coverage_gaps, failure_reason_code,
    retryable, created_at, updated_at, completed_at, expires_at, deleted_at,
    result_completeness, checkpoint_payload -> 'available_result' AS available_result
"""


class PostgresResearchRepository:
    """Durable session state, report publication, tombstones and lifecycle cleanup."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_checkpoint(
        self, request: ResearchSessionLookupRequest
    ) -> ResearchCheckpointLookupResult:
        async with self._engine.connect() as connection:
            payload = await connection.scalar(
                text("""
                    SELECT checkpoint_payload FROM research_sessions
                     WHERE session_id = :session_id
                       AND owner_principal_sha256 = :owner
                       AND deleted_at IS NULL AND expires_at > clock_timestamp()
                       AND status NOT IN ('expired', 'deleted')
                """),
                {
                    "session_id": request.session_id,
                    "owner": _owner_principal_sha256(request.context.actor_id),
                },
            )
        checkpoint = ResearchCheckpoint.model_validate(payload) if payload is not None else None
        return ResearchCheckpointLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=checkpoint is not None,
            checkpoint=checkpoint,
        )

    async def save_checkpoint(
        self, request: ResearchCheckpointWriteRequest
    ) -> ResearchCheckpointLookupResult:
        checkpoint = ResearchCheckpoint.model_validate(request.checkpoint.model_dump(mode="python"))
        packet = checkpoint.packet
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                    SELECT s.instrument_id, s.instrument_name, s.as_of, s.cutoff_mode,
                           s.source_mode, s.expires_at, o.payload,
                           clock_timestamp() AS database_now
                      FROM research_sessions s
                      JOIN research_job_outbox o ON o.job_id = s.job_id
                     WHERE s.session_id = :session_id AND s.job_id = :job_id
                       AND s.owner_principal_sha256 = :owner
                       AND s.status = 'running' AND s.deleted_at IS NULL
                       AND s.expires_at > clock_timestamp()
                       AND o.status IN ('pending', 'dispatched')
                     FOR UPDATE OF s
                """),
                        {
                            "session_id": request.session_id,
                            "job_id": request.job_id,
                            "owner": _owner_principal_sha256(request.context.actor_id),
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ResearchTransitionConflictError("checkpoint job is no longer active")
            task = parse_research_generate_request(row["payload"])
            deadline = task.context.deadline_at
            if (
                deadline is not None
                and max(row["database_now"], request.context.requested_at) >= deadline
            ):
                raise TimeoutError("research checkpoint deadline exceeded")
            if (
                request.context.deadline_at != deadline
                or packet.instrument_id != row["instrument_id"]
                or packet.instrument_name != row["instrument_name"]
                or packet.as_of > request.context.requested_at
                or (row["cutoff_mode"] == "historical_replay" and packet.as_of != row["as_of"])
                or (row["cutoff_mode"] == "current_run" and packet.as_of < row["as_of"])
                or (
                    checkpoint.available_result is not None
                    and checkpoint.available_result.source_mode.value != row["source_mode"]
                )
                or (
                    checkpoint.report is not None
                    and (
                        checkpoint.report.session_id != request.session_id
                        or checkpoint.report.instrument_id != packet.instrument_id
                        or checkpoint.report.as_of != packet.as_of
                        or checkpoint.report.source_mode.value != row["source_mode"]
                        or checkpoint.report.expires_at > row["expires_at"]
                    )
                )
            ):
                raise ResearchTransitionConflictError(
                    "checkpoint context or deadline does not match"
                )
            await connection.execute(
                text("""
                    UPDATE research_sessions SET checkpoint_payload = CAST(:payload AS jsonb),
                        heartbeat_at = :now, updated_at = :now, row_version = row_version + 1
                     WHERE session_id = :session_id
                """),
                {
                    "session_id": request.session_id,
                    "payload": _canonical(checkpoint.model_dump(mode="json")),
                    "now": request.context.requested_at,
                },
            )
        return ResearchCheckpointLookupResult(
            schema_version="1.0", session_id=request.session_id, found=True, checkpoint=checkpoint
        )

    @asynccontextmanager
    async def provider_permit(
        self, *, provider: Literal["deepseek", "tavily"], limit: int, deadline_at: datetime
    ) -> AsyncIterator[None]:
        """Bound in-flight calls across processes using the existing PostgreSQL lock service.

        These are deployment caps, not a claim about the provider's licensed quota.
        A disconnected worker loses its session lock automatically. Waiting releases
        the database connection so queued calls cannot exhaust the repository pool.
        """
        namespace = 24187004 if provider == "deepseek" else 24187005
        acquired: AsyncConnection | None = None
        try:
            while acquired is None:
                remaining = (deadline_at - datetime.now(UTC)).total_seconds()
                if remaining <= 0:
                    raise TimeoutError("provider queue deadline exceeded")
                async with asyncio.timeout(remaining):
                    connection = await self._engine.connect()
                    try:
                        for slot in range(limit):
                            if await connection.scalar(
                                text("SELECT pg_try_advisory_xact_lock(:namespace, :slot)"),
                                {"namespace": namespace, "slot": slot},
                            ):
                                acquired = connection
                                break
                    finally:
                        if acquired is None:
                            await connection.close()
                    if acquired is None:
                        await asyncio.sleep(min(0.1, remaining))
            yield
        finally:
            if acquired is not None:
                # A transaction-scoped permit also releases on timeout, cancellation,
                # exception and disconnected processes, without pooling session locks.
                await acquired.close()

    async def _mutation_replay(
        self,
        connection: AsyncConnection,
        request: ResearchSessionMutationRequest,
        *,
        operation: str,
    ) -> ResearchSessionMutationResult | None:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
            {
                "namespace": _MUTATION_LOCK_NAMESPACE,
                "key": request.context.idempotency_key,
            },
        )
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT session_id, operation, result
                          FROM research_session_mutation_receipts
                         WHERE idempotency_key = :idempotency_key
                        """
                    ),
                    {"idempotency_key": request.context.idempotency_key},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["session_id"] != request.session_id or row["operation"] != operation:
            raise ResearchIdempotencyConflictError(
                "mutation idempotency key was reused for a different request"
            )
        result = ResearchSessionMutationResult.model_validate(row["result"])
        return result.model_copy(update={"idempotent_replay": True})

    async def _record_mutation(
        self,
        connection: AsyncConnection,
        request: ResearchSessionMutationRequest,
        *,
        operation: str,
        result: ResearchSessionMutationResult,
    ) -> ResearchSessionMutationResult:
        await connection.execute(
            text(
                """
                INSERT INTO research_session_mutation_receipts (
                    idempotency_key, session_id, operation, result, created_at
                ) VALUES (
                    :idempotency_key, :session_id, :operation,
                    CAST(:result AS jsonb), :created_at
                )
                """
            ),
            {
                "idempotency_key": request.context.idempotency_key,
                "session_id": request.session_id,
                "operation": operation,
                "result": _canonical(result.model_dump(mode="json")),
                "created_at": request.context.requested_at,
            },
        )
        return result

    async def pending_jobs(self, *, limit: int = 20) -> list[ResearchPendingJob]:
        if not 1 <= limit <= 100:
            raise ValueError("research outbox limit must be between 1 and 100")
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT job_id, task_name, payload, attempt, created_at
                              FROM research_job_outbox
                             WHERE status = 'pending'
                             ORDER BY created_at, outbox_id
                             LIMIT :limit
                            """
                        ),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        return [
            ResearchPendingJob(
                schema_version="1.0",
                job_id=cast(UUID, row["job_id"]),
                task_name=RESEARCH_TASK_NAME,
                payload=parse_research_generate_request(row["payload"]),
                attempt=int(row["attempt"]),
                created_at=cast(datetime, row["created_at"]),
            )
            for row in rows
        ]

    async def mark_job_dispatched(self, *, job_id: UUID, dispatched_at: datetime) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE research_job_outbox
                       SET status = 'dispatched', dispatched_at = :dispatched_at
                     WHERE job_id = :job_id AND status = 'pending'
                    """
                ),
                {"job_id": job_id, "dispatched_at": dispatched_at},
            )
        return bool(result.rowcount)

    async def expire_due(self, *, now: datetime) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT session_id, status, stage, checkpoint
                          FROM research_sessions
                         WHERE expires_at <= :now
                           AND status NOT IN ('expired', 'deleted')
                         FOR UPDATE SKIP LOCKED
                    ), expired AS (
                        UPDATE research_sessions AS session
                           SET status = 'expired',
                               progress_message = '研究会话已超过七天保留期。',
                               failure_reason_code = 'REPORT_EXPIRED',
                               retryable = false,
                               updated_at = :now,
                               purge_due_at = :purge_due_at,
                               row_version = row_version + 1
                          FROM candidate
                         WHERE session.session_id = candidate.session_id
                        RETURNING session.session_id, session.stage, session.checkpoint,
                                  candidate.status AS from_status
                    )
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    )
                    SELECT session_id, from_status, 'expired', stage,
                           '研究会话已超过七天保留期。', checkpoint,
                           'REPORT_EXPIRED', :now, GREATEST(clock_timestamp(), :now)
                      FROM expired
                    """
                ),
                {"now": now, "purge_due_at": now + _PURGE_DELAY},
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_job_outbox AS job
                       SET status = 'cancelled', completed_at = :now
                      FROM research_sessions AS session
                     WHERE job.session_id = session.session_id
                       AND session.status = 'expired'
                       AND session.expires_at <= :now
                       AND job.status IN ('pending', 'dispatched')
                    """
                ),
                {"now": now},
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET inaccessible_at = COALESCE(inaccessible_at, :now)
                     WHERE expires_at <= :now AND inaccessible_at IS NULL
                    """
                ),
                {"now": now},
            )
        return int(result.rowcount or 0)

    async def purge_candidates(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[ResearchPurgeCandidate]:
        if not 1 <= limit <= 500:
            raise ValueError("research purge limit must be between 1 and 500")
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session.session_id, report.report_id,
                                   report.artifact_object_key,
                                   report.artifact_object_version_id, report.report,
                                   report.created_at, report.expires_at
                              FROM research_sessions AS session
                              LEFT JOIN research_reports AS report
                                ON report.session_id = session.session_id
                             WHERE session.status IN ('expired', 'deleted')
                               AND session.purge_due_at <= :now
                               AND session.purged_at IS NULL
                             ORDER BY session.purge_due_at, session.session_id
                             LIMIT :limit
                            """
                        ),
                        {"now": now, "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        candidates: list[ResearchPurgeCandidate] = []
        for row in rows:
            reference = None
            payload = row["report"]
            object_key = cast("str | None", row["artifact_object_key"])
            if object_key is not None:
                content_hash = (
                    payload.get("artifact_content_sha256") if isinstance(payload, dict) else None
                )
                content_length = (
                    payload.get("artifact_content_length") if isinstance(payload, dict) else None
                )
                report_id = row["report_id"]
                try:
                    if (
                        not isinstance(report_id, UUID)
                        or not isinstance(content_hash, str)
                        or not isinstance(content_length, int)
                    ):
                        raise ValueError("artifact metadata is incomplete")
                    reference = StoredObjectRef(
                        schema_version="1.0",
                        object_id=uuid5(_EVENT_NAMESPACE, f"artifact:{report_id}"),
                        namespace="research_reports",
                        object_key=object_key,
                        version_id=cast("str | None", row["artifact_object_version_id"]),
                        media_type="application/json",
                        content_length=content_length,
                        content_sha256=content_hash,
                        stored_at=cast(datetime, row["created_at"]),
                        retention_class=RetentionClass.SHORT_TERM,
                        retention_until=cast(datetime, row["expires_at"]),
                    )
                except ValueError:
                    _LOGGER.warning(
                        "research artifact metadata invalid; purge deferred for session_id=%s",
                        row["session_id"],
                    )
                    continue
            candidates.append(
                ResearchPurgeCandidate(
                    schema_version="1.0",
                    session_id=cast(UUID, row["session_id"]),
                    reference=reference,
                )
            )
        return candidates

    async def create(self, request: ResearchSessionCreateRequest) -> ResearchSessionAccepted:
        request_hash = request.request_sha256
        accepted_at = request.context.requested_at
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        expires_at = accepted_at + timedelta(days=request.analysis_config.report_retention_days)
        payload = _job_payload(
            session_id=request.session_id,
            job_id=request.job_id,
            source_context=request.context,
            cutoff=request.context.as_of,
            cutoff_mode=request.cutoff_mode,
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
                {
                    "namespace": _IDEMPOTENCY_LOCK_NAMESPACE,
                    "key": request.context.idempotency_key,
                },
            )
            replay = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session_id, job_id, request_sha256, created_at
                              FROM research_sessions
                             WHERE idempotency_key = :idempotency_key
                            """
                        ),
                        {"idempotency_key": request.context.idempotency_key},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["request_sha256"] != request_hash:
                    raise ResearchIdempotencyConflictError(
                        "idempotency key was reused for a different research request"
                    )
                return ResearchSessionAccepted(
                    schema_version="1.0",
                    session_id=cast(UUID, replay["session_id"]),
                    job_id=cast(UUID, replay["job_id"]),
                    accepted_at=cast(datetime, replay["created_at"]),
                    idempotent_replay=True,
                )
            if _PRIVATE_PRINCIPAL_ACTOR.fullmatch(request.context.actor_id):
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
                    {
                        "namespace": _OWNER_QUOTA_LOCK_NAMESPACE,
                        "key": owner_principal_sha256,
                    },
                )
                active_count = await connection.scalar(
                    text(
                        """
                        SELECT count(*)
                          FROM research_sessions
                         WHERE owner_principal_sha256 = :owner_principal_sha256
                           AND deleted_at IS NULL
                           AND expires_at > :now
                           AND status IN ('queued', 'running', 'stalled')
                        """
                    ),
                    {
                        "owner_principal_sha256": owner_principal_sha256,
                        "now": accepted_at,
                    },
                )
                if int(active_count or 0) >= _MAX_ACTIVE_PRIVATE_SESSIONS:
                    raise ResearchQuotaExceededError("RESEARCH_ACTIVE_SESSION_LIMIT")
                rolling_window_start = accepted_at.astimezone(UTC) - timedelta(days=1)
                daily_count = await connection.scalar(
                    text(
                        """
                        SELECT count(*)
                          FROM research_sessions
                         WHERE owner_principal_sha256 = :owner_principal_sha256
                           AND created_at >= :rolling_window_start
                           AND created_at <= :accepted_at
                        """
                    ),
                    {
                        "owner_principal_sha256": owner_principal_sha256,
                        "rolling_window_start": rolling_window_start,
                        "accepted_at": accepted_at,
                    },
                )
                if int(daily_count or 0) >= _MAX_DAILY_PRIVATE_SESSION_CREATES:
                    raise ResearchQuotaExceededError("RESEARCH_DAILY_CREATE_LIMIT")
            await connection.execute(
                text(
                    """
                    INSERT INTO research_sessions (
                        session_id, job_id, owner_principal_sha256,
                        instrument_id, instrument_name, as_of, cutoff_mode,
                        analysis_config, source_mode, idempotency_key, request_sha256,
                        status, stage, progress_message, heartbeat_at, checkpoint,
                        coverage_gaps, failure_reason_code, retryable, created_at,
                        updated_at, completed_at, expires_at, deleted_at, purge_due_at
                    ) VALUES (
                        :session_id, :job_id, :owner_principal_sha256,
                        :instrument_id, :instrument_name, :as_of, :cutoff_mode,
                        CAST(:analysis_config AS jsonb), :source_mode, :idempotency_key,
                        :request_sha256, 'queued', 'queued', :message, :created_at,
                        NULL, '[]'::jsonb, NULL, false, :created_at, :created_at,
                        NULL, :expires_at, NULL, NULL
                    )
                    """
                ),
                {
                    "session_id": request.session_id,
                    "job_id": request.job_id,
                    "owner_principal_sha256": owner_principal_sha256,
                    "instrument_id": request.instrument_id,
                    "instrument_name": request.instrument_name,
                    "as_of": request.context.as_of,
                    "cutoff_mode": request.cutoff_mode.value,
                    "analysis_config": _canonical(request.analysis_config.model_dump(mode="json")),
                    "source_mode": request.source_mode.value,
                    "idempotency_key": request.context.idempotency_key,
                    "request_sha256": request_hash,
                    "message": "研究任务已进入队列。",
                    "created_at": accepted_at,
                    "expires_at": expires_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    ) VALUES (
                        :session_id, NULL, 'queued', 'queued', :message,
                        NULL, NULL, :occurred_at,
                        GREATEST(clock_timestamp(), :occurred_at)
                    )
                    """
                ),
                {
                    "session_id": request.session_id,
                    "message": "研究任务已进入队列。",
                    "occurred_at": accepted_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_job_outbox (
                        outbox_id, session_id, job_id, task_name, payload,
                        idempotency_key, status, attempt, created_at
                    ) VALUES (
                        :outbox_id, :session_id, :job_id, :task_name,
                        CAST(:payload AS jsonb), :idempotency_key, 'pending', 1, :created_at
                    )
                    """
                ),
                {
                    "outbox_id": uuid4(),
                    "session_id": request.session_id,
                    "job_id": request.job_id,
                    "task_name": RESEARCH_TASK_NAME,
                    "payload": _canonical(payload.model_dump(mode="json")),
                    "idempotency_key": payload.context.idempotency_key,
                    "created_at": accepted_at,
                },
            )
        return ResearchSessionAccepted(
            schema_version="1.0",
            session_id=request.session_id,
            job_id=request.job_id,
            accepted_at=accepted_at,
        )

    async def _advance_automatic_state(
        self, connection: AsyncConnection, request: ResearchSessionLookupRequest
    ) -> None:
        now = request.context.as_of
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        stalled = (
            (
                await connection.execute(
                    text(
                        """
                        WITH candidate AS (
                            SELECT session_id, status
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND status IN ('queued', 'running')
                               AND heartbeat_at <= :stalled_before
                               AND deleted_at IS NULL
                             FOR UPDATE
                        )
                        UPDATE research_sessions AS session
                           SET status = 'stalled',
                               progress_message = '连续 30 分钟没有阶段进展, 可重新运行。',
                               failure_reason_code = 'RESEARCH_SESSION_STALLED',
                               retryable = true,
                               updated_at = :now,
                               row_version = row_version + 1
                          FROM candidate
                         WHERE session.session_id = candidate.session_id
                        RETURNING session.session_id, session.stage, session.checkpoint,
                                  candidate.status AS from_status
                        """
                    ),
                    {
                        "session_id": request.session_id,
                        "owner_principal_sha256": owner_principal_sha256,
                        "now": now,
                        "stalled_before": now - timedelta(minutes=30),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if stalled is not None:
            await connection.execute(
                text(
                    """
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    ) VALUES (
                        :session_id, :from_status, 'stalled', :stage, :message,
                        :checkpoint, 'RESEARCH_SESSION_STALLED', :occurred_at,
                        GREATEST(clock_timestamp(), :occurred_at)
                    )
                    """
                ),
                {
                    "session_id": request.session_id,
                    "from_status": stalled["from_status"],
                    "stage": stalled["stage"],
                    "message": "连续 30 分钟没有阶段进展, 可重新运行。",
                    "checkpoint": stalled["checkpoint"],
                    "occurred_at": now,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_job_outbox
                       SET status = 'cancelled', completed_at = :now
                     WHERE session_id = :session_id
                       AND status IN ('pending', 'dispatched')
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
        expired = (
            (
                await connection.execute(
                    text(
                        """
                        WITH candidate AS (
                            SELECT session_id, status
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND expires_at <= :now
                               AND status NOT IN ('expired', 'deleted')
                               AND deleted_at IS NULL
                             FOR UPDATE
                        )
                        UPDATE research_sessions AS session
                           SET status = 'expired',
                               progress_message = '研究会话已超过七天保留期。',
                               failure_reason_code = 'REPORT_EXPIRED',
                               retryable = false,
                               updated_at = :now,
                               purge_due_at = :purge_due_at,
                               row_version = row_version + 1
                          FROM candidate
                         WHERE session.session_id = candidate.session_id
                        RETURNING session.session_id, session.stage, session.checkpoint,
                                  candidate.status AS from_status
                        """
                    ),
                    {
                        "session_id": request.session_id,
                        "owner_principal_sha256": owner_principal_sha256,
                        "now": now,
                        "purge_due_at": now + _PURGE_DELAY,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if expired is not None:
            await connection.execute(
                text(
                    """
                    UPDATE research_job_outbox
                       SET status = 'cancelled', completed_at = :now
                     WHERE session_id = :session_id
                       AND status IN ('pending', 'dispatched')
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET inaccessible_at = COALESCE(inaccessible_at, :now)
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    ) VALUES (
                        :session_id, :from_status, 'expired', :stage, :message,
                        :checkpoint, 'REPORT_EXPIRED', :occurred_at,
                        GREATEST(clock_timestamp(), :occurred_at)
                    )
                    """
                ),
                {
                    "session_id": request.session_id,
                    "from_status": expired["from_status"],
                    "stage": expired["stage"],
                    "message": "研究会话已超过七天保留期。",
                    "checkpoint": expired["checkpoint"],
                    "occurred_at": now,
                },
            )

    async def get(self, request: ResearchSessionLookupRequest) -> ResearchSessionLookupResult:
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        async with self._engine.begin() as connection:
            await self._advance_automatic_state(connection, request)
            row = (
                (
                    await connection.execute(
                        text(
                            f"""
                            SELECT {_SESSION_COLUMNS}
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND deleted_at IS NULL
                            """  # noqa: S608 - fixed local column list
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        return ResearchSessionLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=row is not None,
            session=_view(row) if row is not None else None,
            cutoff_mode=(
                ResearchCutoffMode(cast(str, row["cutoff_mode"]))
                if row is not None
                else ResearchCutoffMode.HISTORICAL_REPLAY
            ),
            as_of=request.context.as_of,
        )

    async def list_page(self, query: ResearchSessionListQuery) -> ResearchSessionPage:
        owner_principal_sha256 = _owner_principal_sha256(query.context.actor_id)
        decoded = _decode_cursor(query.cursor)
        cursor_created_at, cursor_session_id = decoded if decoded is not None else (None, None)
        async with self._engine.connect() as connection:
            rows = list(
                (
                    await connection.execute(
                        text(
                            f"""
                                SELECT {_SESSION_COLUMNS}
                                  FROM research_sessions
                                 WHERE deleted_at IS NULL
                                   AND owner_principal_sha256 = :owner_principal_sha256
                                   AND expires_at > :as_of
                                   AND (
                                       CAST(:cursor_created_at AS timestamptz) IS NULL
                                       OR (created_at, session_id) < (
                                           :cursor_created_at, :cursor_session_id
                                       )
                                   )
                                 ORDER BY created_at DESC, session_id DESC
                                 LIMIT :fetch_limit
                                """  # noqa: S608 - fixed local column list
                        ),
                        {
                            "as_of": query.context.as_of,
                            "owner_principal_sha256": owner_principal_sha256,
                            "cursor_created_at": cursor_created_at,
                            "cursor_session_id": cursor_session_id,
                            "fetch_limit": query.limit + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
        has_more = len(rows) > query.limit
        visible = rows[: query.limit]
        next_cursor = None
        if has_more and visible:
            tail = visible[-1]
            next_cursor = _encode_cursor(
                cast(datetime, tail["created_at"]), cast(UUID, tail["session_id"])
            )
        return ResearchSessionPage(
            schema_version="1.0",
            items=[_view(row) for row in visible],
            next_cursor=next_cursor,
            has_more=has_more,
            as_of=query.context.as_of,
        )

    async def transition(
        self, request: ResearchStageTransitionRequest
    ) -> ResearchSessionLookupResult:
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        async with self._engine.begin() as connection:
            current = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT status, stage, checkpoint, job_id
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND deleted_at IS NULL
                             FOR UPDATE
                            """
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                return ResearchSessionLookupResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    found=False,
                    as_of=request.context.as_of,
                )
            if current["job_id"] != request.job_id:
                raise ResearchTransitionConflictError(
                    "research transition job does not own the current session"
                )
            current_status = ResearchSessionStatus(cast(str, current["status"]))
            if current_status not in request.expected_statuses:
                if current_status is request.status and current["stage"] == request.stage.value:
                    pass
                else:
                    raise ResearchTransitionConflictError(
                        "research session is not in an expected transition state"
                    )
            else:
                if (
                    request.status is ResearchSessionStatus.SUCCEEDED
                    and request.result_completeness is ResearchResultCompleteness.PARTIAL
                ):
                    # This completion has no report write. Check its original
                    # deadline under the same session lock as the terminal state.
                    job = (
                        (
                            await connection.execute(
                                text("""
                                    SELECT o.payload, s.checkpoint_payload, s.expires_at,
                                           clock_timestamp() AS database_now
                                      FROM research_job_outbox o
                                      JOIN research_sessions s ON s.job_id = o.job_id
                                     WHERE o.job_id = :job_id AND s.session_id = :session_id
                                       AND o.status IN ('pending', 'dispatched')
                                """),
                                {"job_id": request.job_id, "session_id": request.session_id},
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if job is None:
                        raise ResearchTransitionConflictError("limited-result job is inactive")
                    deadline = parse_research_generate_request(job["payload"]).context.deadline_at
                    if request.context.deadline_at != deadline:
                        raise ResearchTransitionConflictError("limited-result deadline mismatch")
                    now = max(job["database_now"], request.context.requested_at)
                    if now >= job["expires_at"] or (deadline is not None and now >= deadline):
                        raise TimeoutError("limited-result completion deadline exceeded")
                    saved = ResearchCheckpoint.model_validate(job["checkpoint_payload"])
                    if (
                        request.stage is not ResearchStage.COMPLETED
                        or saved.available_result is None
                        or saved.available_result.reader_edition is None
                        or saved.support_assessment is None
                        or saved.report is not None
                    ):
                        raise ResearchTransitionConflictError("limited result is not retained")
                completed_at = (
                    request.context.requested_at
                    if request.status
                    in {ResearchSessionStatus.SUCCEEDED, ResearchSessionStatus.FAILED}
                    else None
                )
                await connection.execute(
                    text(
                        """
                        UPDATE research_sessions
                           SET status = :status,
                               stage = :stage,
                               progress_message = :message,
                               heartbeat_at = :occurred_at,
                               checkpoint = :checkpoint,
                               failure_reason_code = :reason_code,
                               retryable = :retryable,
                               result_completeness = :result_completeness,
                               updated_at = :occurred_at,
                               completed_at = CASE WHEN :status = 'failed'
                                   THEN COALESCE(completed_at, :completed_at)
                                   ELSE COALESCE(:completed_at, completed_at) END,
                               row_version = row_version + 1
                         WHERE session_id = :session_id
                           AND owner_principal_sha256 = :owner_principal_sha256
                        """
                    ),
                    {
                        "session_id": request.session_id,
                        "owner_principal_sha256": owner_principal_sha256,
                        "status": request.status.value,
                        "stage": request.stage.value,
                        "message": request.message,
                        "occurred_at": request.context.requested_at,
                        "checkpoint": request.checkpoint,
                        "reason_code": request.reason_code,
                        "retryable": request.retryable,
                        "result_completeness": (
                            request.result_completeness.value
                            if request.result_completeness is not None
                            else None
                        ),
                        "completed_at": completed_at,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO research_session_transitions (
                            session_id, from_status, to_status, stage, message,
                            checkpoint, reason_code, occurred_at, recorded_at
                        ) VALUES (
                            :session_id, :from_status, :to_status, :stage, :message,
                            :checkpoint, :reason_code, :occurred_at,
                            GREATEST(clock_timestamp(), :occurred_at)
                        )
                        """
                    ),
                    {
                        "session_id": request.session_id,
                        "from_status": current_status.value,
                        "to_status": request.status.value,
                        "stage": request.stage.value,
                        "message": request.message,
                        "checkpoint": request.checkpoint,
                        "reason_code": request.reason_code,
                        "occurred_at": request.context.requested_at,
                    },
                )
            row = (
                (
                    await connection.execute(
                        text(
                            f"SELECT {_SESSION_COLUMNS} FROM research_sessions "  # noqa: S608
                            "WHERE session_id = :session_id "
                            "AND owner_principal_sha256 = :owner_principal_sha256"
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one()
            )
        return ResearchSessionLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=True,
            session=_view(row),
            cutoff_mode=ResearchCutoffMode(cast(str, row["cutoff_mode"])),
            as_of=request.context.as_of,
        )

    async def save_report(self, request: ResearchReportWriteRequest) -> ResearchReportLookupResult:
        report = request.report
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        report_payload = report.model_dump(mode="json")
        report_sha256 = _sha256(report_payload)
        unit_of_work = PostgresUnitOfWork(self._engine)
        async with unit_of_work:
            connection = unit_of_work.connection
            session = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT status, job_id, expires_at, as_of, cutoff_mode,
                                   clock_timestamp() AS database_now
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND deleted_at IS NULL
                             FOR UPDATE
                            """
                        ),
                        {
                            "session_id": report.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if session is None:
                raise ResearchRepositoryError("research session does not exist")
            status = ResearchSessionStatus(cast(str, session["status"]))
            cutoff_mode = ResearchCutoffMode(cast(str, session["cutoff_mode"]))
            initial_cutoff = cast(datetime, session["as_of"])
            session_expires_at = cast(datetime, session["expires_at"])
            job = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT task_name, payload, status
                              FROM research_job_outbox
                             WHERE session_id = :session_id
                               AND job_id = :job_id
                             FOR UPDATE
                            """
                        ),
                        {
                            "session_id": report.session_id,
                            "job_id": request.job_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            try:
                persisted_task = (
                    parse_research_generate_request(job["payload"]) if job is not None else None
                )
            except (TypeError, ValidationError, ValueError):
                persisted_task = None
            persisted_mode = (
                effective_research_cutoff_mode(persisted_task)
                if persisted_task is not None
                else None
            )
            persisted_deadline = (
                persisted_task.context.deadline_at if persisted_task is not None else None
            )
            accepted_citations = [
                citation
                for claim in report.claims
                for citation in (*claim.evidence, *claim.counter_evidence)
            ]
            web_citations = [
                citation
                for citation in accepted_citations
                if citation.acquisition_mode.value == "model_assisted_web"
            ]
            expected_as_of = max(
                [initial_cutoff, *(citation.first_seen_at for citation in accepted_citations)]
            )
            if (
                status is not ResearchSessionStatus.RUNNING
                or session["job_id"] != request.job_id
                or cutoff_mode != request.cutoff_mode
                or job is None
                or job["task_name"] != RESEARCH_TASK_NAME
                or job["status"] not in {"pending", "dispatched"}
                or persisted_task is None
                or persisted_task.session_id != report.session_id
                or persisted_task.job_id != request.job_id
                or persisted_task.context.as_of != initial_cutoff
                or persisted_mode != cutoff_mode
                or request.context.deadline_at != persisted_deadline
                or session_expires_at <= session["database_now"]
                or report.generated_at >= session_expires_at
                or report.expires_at > session_expires_at
                or report.as_of > report.generated_at
                or (
                    persisted_deadline is not None
                    and (
                        session["database_now"] > persisted_deadline
                        or report.generated_at > persisted_deadline
                    )
                )
                or (
                    cutoff_mode is ResearchCutoffMode.HISTORICAL_REPLAY
                    and (web_citations or report.as_of != initial_cutoff)
                )
                or (
                    cutoff_mode is ResearchCutoffMode.CURRENT_RUN
                    and (
                        persisted_deadline is None
                        or report.as_of != expected_as_of
                        or any(
                            citation.published_at > initial_cutoff
                            or citation.revision_known_at > initial_cutoff
                            for citation in web_citations
                        )
                    )
                )
            ):
                raise ResearchTransitionConflictError(
                    "only the current running job can publish within its original retention window"
                )
            existing = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT report_sha256, report
                              FROM research_reports
                             WHERE session_id = :session_id
                            """
                        ),
                        {"session_id": report.session_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and existing["report_sha256"] != report_sha256:
                raise ResearchIdempotencyConflictError(
                    "a different report already exists for this session"
                )
            if existing is None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO research_reports (
                            report_id, session_id, report, generator_version,
                            model_provider, model_name, model_version, validator_version,
                            rule_version, data_manifest_sha256, report_sha256,
                            artifact_object_key, artifact_object_version_id,
                            created_at, expires_at
                        ) VALUES (
                            :report_id, :session_id, CAST(:report AS jsonb), :generator_version,
                            :model_provider, :model_name, :model_version, :validator_version,
                            :rule_version, :data_manifest_sha256, :report_sha256,
                            :artifact_object_key, :artifact_object_version_id,
                            :created_at, :expires_at
                        )
                        """
                    ),
                    {
                        "report_id": report.report_id,
                        "session_id": report.session_id,
                        "report": _canonical(report_payload),
                        "generator_version": report.generator_version,
                        "model_provider": report.model_provider,
                        "model_name": report.model_name,
                        "model_version": report.model_version,
                        "validator_version": report.validator_version,
                        "rule_version": report.rule_version,
                        "data_manifest_sha256": report.data_manifest_sha256,
                        "report_sha256": report_sha256,
                        "artifact_object_key": report.artifact_object_key,
                        "artifact_object_version_id": request.artifact_object_version_id,
                        "created_at": report.generated_at,
                        "expires_at": report.expires_at,
                    },
                )
                for claim in report.claims:
                    evidence_ids = [
                        *(item.evidence_id for item in claim.evidence),
                        *(item.evidence_id for item in claim.counter_evidence),
                    ]
                    await connection.execute(
                        text(
                            """
                            INSERT INTO research_report_claims (
                                report_id, claim_id, claim_type, section, claim,
                                evidence_ids, publication_eligible, created_at
                            ) VALUES (
                                :report_id, :claim_id, :claim_type, :section,
                                CAST(:claim AS jsonb), :evidence_ids,
                                :publication_eligible, :created_at
                            )
                            """
                        ),
                        {
                            "report_id": report.report_id,
                            "claim_id": claim.claim_id,
                            "claim_type": claim.claim_type.value,
                            "section": claim.section.value,
                            "claim": _canonical(claim.model_dump(mode="json")),
                            "evidence_ids": evidence_ids,
                            "publication_eligible": claim.publication_eligible,
                            "created_at": report.generated_at,
                        },
                    )
                if report.artifact_object_key is not None:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO research_session_contexts (
                                context_id, session_id, kind, content, object_key,
                                content_sha256, created_at, expires_at
                            ) VALUES (
                                :context_id, :session_id, 'object_artifact', NULL,
                                :object_key, :content_sha256, :created_at, :expires_at
                            ) ON CONFLICT (context_id) DO NOTHING
                            """
                        ),
                        {
                            "context_id": uuid5(
                                _EVENT_NAMESPACE,
                                f"artifact:{report.report_id}",
                            ),
                            "session_id": report.session_id,
                            "object_key": report.artifact_object_key,
                            "content_sha256": report.artifact_content_sha256,
                            "created_at": report.generated_at,
                            "expires_at": report.expires_at,
                        },
                    )
                await connection.execute(
                    text(
                        """
                        UPDATE research_sessions
                           SET status = 'succeeded', stage = 'completed',
                               progress_message = '完整研究报告已通过发布校验。',
                               as_of = :report_as_of,
                               coverage_gaps = CAST(:coverage_gaps AS jsonb),
                               heartbeat_at = :completed_at, updated_at = :completed_at,
                               completed_at = :completed_at, retryable = false,
                               failure_reason_code = NULL, result_completeness = 'complete',
                               row_version = row_version + 1
                         WHERE session_id = :session_id
                           AND owner_principal_sha256 = :owner_principal_sha256
                        """
                    ),
                    {
                        "session_id": report.session_id,
                        "owner_principal_sha256": owner_principal_sha256,
                        "completed_at": report.generated_at,
                        "report_as_of": report.as_of,
                        "coverage_gaps": _canonical(
                            [item.model_dump(mode="json") for item in report.coverage_gaps]
                        ),
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO research_session_transitions (
                            session_id, from_status, to_status, stage, message,
                            checkpoint, reason_code, occurred_at, recorded_at
                        ) VALUES (
                            :session_id, :from_status, 'succeeded', 'completed',
                            '完整研究报告已通过发布校验。', 'report_published', NULL,
                            :completed_at, GREATEST(clock_timestamp(), :completed_at)
                        )
                        """
                    ),
                    {
                        "session_id": report.session_id,
                        "from_status": status.value,
                        "completed_at": report.generated_at,
                    },
                )
                await connection.execute(
                    text(
                        """
                        UPDATE research_job_outbox
                           SET status = 'completed', completed_at = :completed_at
                         WHERE job_id = :job_id
                        """
                    ),
                    {
                        "job_id": cast(UUID, session["job_id"]),
                        "completed_at": report.generated_at,
                    },
                )
            ready = ResearchReportReadyV1(
                schema_version="1.0",
                session_id=report.session_id,
                report_id=report.report_id,
                instrument_id=report.instrument_id,
                as_of=report.as_of,
                expires_at=report.expires_at,
                report_sha256=report_sha256,
            )
            event_idempotency = f"research-report-ready-{report.session_id}"
            event_context = request.context.model_copy(
                update={"idempotency_key": event_idempotency}
            )
            await PostgresOutboxPublisher[ResearchReportReadyV1](unit_of_work).publish(
                MessagePublishRequest(
                    schema_version="1.0",
                    context=event_context,
                    envelope=MessageEnvelope(
                        schema_version="1.0",
                        message_id=uuid5(_EVENT_NAMESPACE, event_idempotency),
                        event_name=RESEARCH_REPORT_READY_EVENT,
                        producer="research_worker",
                        occurred_at=report.generated_at,
                        produced_at=report.generated_at,
                        correlation_id=event_context.correlation_id,
                        causation_id=cast(UUID, session["job_id"]),
                        partition_key=str(report.session_id),
                        idempotency_key=event_idempotency,
                        payload=ready,
                    ),
                )
            )
            await unit_of_work.commit()
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=report.session_id,
            found=True,
            report=report,
            as_of=request.context.as_of,
        )

    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT session.status, session.deleted_at,
                                   session.expires_at AS session_expires_at,
                                   report.report, report.expires_at AS report_expires_at,
                                   report.validator_version AS report_validator_version,
                                   report.report_sha256,
                                   report.inaccessible_at, report.purged_at
                              FROM research_sessions AS session
                              LEFT JOIN research_reports AS report
                                ON report.session_id = session.session_id
                             WHERE session.session_id = :session_id
                               AND session.owner_principal_sha256 = :owner_principal_sha256
                            """
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["deleted_at"] is not None:
            return ResearchReportLookupResult(
                schema_version="1.0",
                session_id=request.session_id,
                found=False,
                reason_code="REPORT_NOT_FOUND",
                as_of=request.context.as_of,
            )
        session_expires_at = cast(datetime, row["session_expires_at"])
        report_expires_at = cast("datetime | None", row["report_expires_at"])
        if (
            session_expires_at <= request.context.as_of
            or (report_expires_at is not None and report_expires_at <= request.context.as_of)
            or row["inaccessible_at"] is not None
            or row["purged_at"] is not None
        ):
            return ResearchReportLookupResult(
                schema_version="1.0",
                session_id=request.session_id,
                found=False,
                reason_code="REPORT_EXPIRED",
                as_of=request.context.as_of,
            )
        if row["status"] != ResearchSessionStatus.SUCCEEDED.value or row["report"] is None:
            return ResearchReportLookupResult(
                schema_version="1.0",
                session_id=request.session_id,
                found=False,
                reason_code="REPORT_NOT_READY",
                as_of=request.context.as_of,
            )
        stored_report = row["report"]
        trusted_validator_version = row["report_validator_version"]
        stored_report_sha256 = row["report_sha256"]
        try:
            integrity_matches = (
                isinstance(stored_report, dict)
                and isinstance(trusted_validator_version, str)
                and stored_report.get("validator_version") == trusted_validator_version
                and isinstance(stored_report_sha256, str)
                and _sha256(stored_report) == stored_report_sha256
            )
        except (TypeError, ValueError):
            integrity_matches = False
        if not integrity_matches:
            _log_report_compatibility_rejection(
                correlation_id=request.context.correlation_id,
                failure_category=(_ReportCompatibilityFailureCategory.INTEGRITY_METADATA_MISMATCH),
            )
            return ResearchReportLookupResult(
                schema_version="1.0",
                session_id=request.session_id,
                found=False,
                reason_code="REPORT_COMPATIBILITY_PROJECTION_FAILED",
                as_of=request.context.as_of,
            )
        try:
            report = validate_stored_research_report(
                stored_report,
                trusted_validator_version=trusted_validator_version,
            )
        except (TypeError, ValidationError, ValueError):
            _log_report_compatibility_rejection(
                correlation_id=request.context.correlation_id,
                failure_category=(_ReportCompatibilityFailureCategory.RETAINED_VALIDATION_FAILED),
            )
            return ResearchReportLookupResult(
                schema_version="1.0",
                session_id=request.session_id,
                found=False,
                reason_code="REPORT_COMPATIBILITY_PROJECTION_FAILED",
                as_of=request.context.as_of,
            )
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=True,
            report=report,
            as_of=request.context.as_of,
        )

    async def cancel(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult:
        now = request.context.requested_at
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        async with self._engine.begin() as connection:
            replay = await self._mutation_replay(connection, request, operation="cancel")
            if replay is not None:
                return replay
            current = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT status, stage, job_id, checkpoint
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND deleted_at IS NULL
                             FOR UPDATE
                            """
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                return ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    status=ResearchSessionStatus.DELETED,
                    processed_at=now,
                    idempotent_replay=True,
                    reason_code="RESEARCH_SESSION_NOT_FOUND",
                )
            status = ResearchSessionStatus(cast(str, current["status"]))
            if status is ResearchSessionStatus.CANCELLED:
                result = ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=cast(UUID, current["job_id"]),
                    status=status,
                    processed_at=now,
                    idempotent_replay=True,
                )
                return await self._record_mutation(
                    connection, request, operation="cancel", result=result
                )
            if status not in {
                ResearchSessionStatus.QUEUED,
                ResearchSessionStatus.RUNNING,
                ResearchSessionStatus.STALLED,
            }:
                result = ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=cast(UUID, current["job_id"]),
                    status=status,
                    processed_at=now,
                    reason_code="RESEARCH_SESSION_NOT_CANCELLABLE",
                )
                return await self._record_mutation(
                    connection, request, operation="cancel", result=result
                )
            await connection.execute(
                text(
                    """
                    UPDATE research_sessions
                       SET status = 'cancelled', progress_message = '研究任务已取消。',
                           failure_reason_code = NULL, retryable = false,
                           heartbeat_at = :now, updated_at = :now,
                           completed_at = :now, row_version = row_version + 1
                     WHERE session_id = :session_id
                       AND owner_principal_sha256 = :owner_principal_sha256
                    """
                ),
                {
                    "session_id": request.session_id,
                    "owner_principal_sha256": owner_principal_sha256,
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_job_outbox
                       SET status = 'cancelled', completed_at = :now
                     WHERE job_id = :job_id AND status <> 'completed'
                    """
                ),
                {"job_id": current["job_id"], "now": now},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    ) VALUES (
                        :session_id, :from_status, 'cancelled', :stage,
                        '研究任务已取消。', :checkpoint, NULL, :now,
                        GREATEST(clock_timestamp(), :now)
                    )
                    """
                ),
                {
                    "session_id": request.session_id,
                    "from_status": status.value,
                    "stage": current["stage"],
                    "checkpoint": current["checkpoint"],
                    "now": now,
                },
            )
            result = ResearchSessionMutationResult(
                schema_version="1.0",
                session_id=request.session_id,
                job_id=cast(UUID, current["job_id"]),
                status=ResearchSessionStatus.CANCELLED,
                processed_at=now,
            )
            await self._record_mutation(connection, request, operation="cancel", result=result)
        return result

    async def retry(self, request: ResearchSessionMutationRequest) -> ResearchSessionMutationResult:
        now = request.context.requested_at
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        new_job_id = uuid4()
        async with self._engine.begin() as connection:
            replay = await self._mutation_replay(connection, request, operation="retry")
            if replay is not None:
                return replay
            current = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT status, retryable, job_id, as_of, cutoff_mode,
                                   COALESCE((SELECT max(attempt) FROM research_job_outbox
                                    WHERE session_id = research_sessions.session_id), 0) AS attempt
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                               AND deleted_at IS NULL
                             FOR UPDATE
                            """
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                return ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    status=ResearchSessionStatus.DELETED,
                    processed_at=now,
                    reason_code="RESEARCH_SESSION_NOT_FOUND",
                )
            status = ResearchSessionStatus(cast(str, current["status"]))
            retryable = status is ResearchSessionStatus.STALLED or (
                status is ResearchSessionStatus.FAILED and bool(current["retryable"])
            )
            if not retryable:
                result = ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=cast(UUID, current["job_id"]),
                    status=status,
                    processed_at=now,
                    reason_code="RESEARCH_SESSION_NOT_RETRYABLE",
                )
                return await self._record_mutation(
                    connection, request, operation="retry", result=result
                )
            if int(current["attempt"]) >= RESEARCH_MAX_JOB_ATTEMPTS:
                await connection.execute(
                    text(
                        """
                        UPDATE research_sessions
                           SET status = 'failed', retryable = false,
                               completed_at = COALESCE(completed_at, :now),
                               progress_message = '研究任务已达到最大执行次数。',
                               failure_reason_code = 'RESEARCH_RETRY_ATTEMPTS_EXHAUSTED',
                               updated_at = :now, row_version = row_version + 1
                         WHERE session_id = :session_id
                           AND owner_principal_sha256 = :owner_principal_sha256
                        """
                    ),
                    {
                        "session_id": request.session_id,
                        "owner_principal_sha256": owner_principal_sha256,
                        "now": now,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO research_session_transitions (
                            session_id, from_status, to_status, stage, message,
                            checkpoint, reason_code, occurred_at, recorded_at
                        ) VALUES (
                            :session_id, :from_status, 'failed', 'validating_report',
                            '研究任务已达到最大执行次数。', NULL,
                            'RESEARCH_RETRY_ATTEMPTS_EXHAUSTED', :now,
                            GREATEST(clock_timestamp(), :now)
                        )
                        """
                    ),
                    {
                        "session_id": request.session_id,
                        "from_status": status.value,
                        "now": now,
                    },
                )
                result = ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=cast(UUID, current["job_id"]),
                    status=ResearchSessionStatus.FAILED,
                    processed_at=now,
                    reason_code="RESEARCH_RETRY_ATTEMPTS_EXHAUSTED",
                )
                return await self._record_mutation(
                    connection, request, operation="retry", result=result
                )
            cutoff_mode = ResearchCutoffMode(cast(str, current["cutoff_mode"]))
            payload = _job_payload(
                session_id=request.session_id,
                job_id=new_job_id,
                source_context=request.context,
                cutoff=cast(datetime, current["as_of"]),
                cutoff_mode=cutoff_mode,
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_sessions
                       SET job_id = :job_id, status = 'queued', stage = 'queued',
                           progress_message = CASE WHEN checkpoint_payload IS NULL
                               THEN '研究任务已重新入队, 将从头执行。'
                               ELSE '研究任务已重新入队, 将继续已保存的进度。' END,
                           checkpoint = CASE WHEN checkpoint_payload IS NULL
                               THEN NULL ELSE checkpoint END,
                           result_completeness = NULL,
                           heartbeat_at = :now, updated_at = :now,
                           failure_reason_code = NULL, retryable = false,
                           coverage_gaps = '[]'::jsonb,
                           completed_at = NULL, row_version = row_version + 1
                     WHERE session_id = :session_id
                       AND owner_principal_sha256 = :owner_principal_sha256
                    """
                ),
                {
                    "session_id": request.session_id,
                    "owner_principal_sha256": owner_principal_sha256,
                    "job_id": new_job_id,
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_job_outbox (
                        outbox_id, session_id, job_id, task_name, payload,
                        idempotency_key, status, attempt, created_at
                    ) VALUES (
                        :outbox_id, :session_id, :job_id, :task_name,
                        CAST(:payload AS jsonb), :idempotency_key, 'pending', :attempt, :now
                    )
                    """
                ),
                {
                    "outbox_id": uuid4(),
                    "session_id": request.session_id,
                    "job_id": new_job_id,
                    "task_name": RESEARCH_TASK_NAME,
                    "payload": _canonical(payload.model_dump(mode="json")),
                    "idempotency_key": payload.context.idempotency_key,
                    "attempt": int(current["attempt"]) + 1,
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    ) VALUES (
                        :session_id, :from_status, 'queued', 'queued',
                        '研究任务已重新入队, 将从头执行。', NULL, NULL, :now,
                        GREATEST(clock_timestamp(), :now)
                    )
                    """
                ),
                {"session_id": request.session_id, "from_status": status.value, "now": now},
            )
            result = ResearchSessionMutationResult(
                schema_version="1.0",
                session_id=request.session_id,
                job_id=new_job_id,
                status=ResearchSessionStatus.QUEUED,
                processed_at=now,
            )
            await self._record_mutation(connection, request, operation="retry", result=result)
        return result

    async def delete(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult:
        now = request.context.requested_at
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        async with self._engine.begin() as connection:
            replay = await self._mutation_replay(connection, request, operation="delete")
            if replay is not None:
                return replay
            current = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT status, job_id, stage, checkpoint, deleted_at
                              FROM research_sessions
                             WHERE session_id = :session_id
                               AND owner_principal_sha256 = :owner_principal_sha256
                             FOR UPDATE
                            """
                        ),
                        {
                            "session_id": request.session_id,
                            "owner_principal_sha256": owner_principal_sha256,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                return ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    status=ResearchSessionStatus.DELETED,
                    processed_at=now,
                    idempotent_replay=True,
                    reason_code="RESEARCH_SESSION_NOT_FOUND",
                )
            if current["deleted_at"] is not None:
                result = ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    status=ResearchSessionStatus.DELETED,
                    processed_at=now,
                    idempotent_replay=True,
                )
                return await self._record_mutation(
                    connection, request, operation="delete", result=result
                )
            status = ResearchSessionStatus(cast(str, current["status"]))
            if status in {ResearchSessionStatus.QUEUED, ResearchSessionStatus.RUNNING}:
                result = ResearchSessionMutationResult(
                    schema_version="1.0",
                    session_id=request.session_id,
                    job_id=cast(UUID, current["job_id"]),
                    status=status,
                    processed_at=now,
                    reason_code="RESEARCH_SESSION_MUST_BE_CANCELLED",
                )
                return await self._record_mutation(
                    connection, request, operation="delete", result=result
                )
            await connection.execute(
                text(
                    """
                    UPDATE research_sessions
                       SET status = 'deleted', deleted_at = :now,
                           purge_due_at = :purge_due_at, updated_at = :now,
                           progress_message = '研究会话已删除并进入清理队列。',
                           row_version = row_version + 1
                     WHERE session_id = :session_id
                       AND owner_principal_sha256 = :owner_principal_sha256
                    """
                ),
                {
                    "session_id": request.session_id,
                    "owner_principal_sha256": owner_principal_sha256,
                    "now": now,
                    "purge_due_at": now + _PURGE_DELAY,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET inaccessible_at = COALESCE(inaccessible_at, :now)
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO research_session_transitions (
                        session_id, from_status, to_status, stage, message,
                        checkpoint, reason_code, occurred_at, recorded_at
                    ) VALUES (
                        :session_id, :from_status, 'deleted', :stage,
                        '研究会话已删除并进入清理队列。', :checkpoint, NULL, :now,
                        GREATEST(clock_timestamp(), :now)
                    )
                    """
                ),
                {
                    "session_id": request.session_id,
                    "from_status": status.value,
                    "stage": current["stage"],
                    "checkpoint": current["checkpoint"],
                    "now": now,
                },
            )
            result = ResearchSessionMutationResult(
                schema_version="1.0",
                session_id=request.session_id,
                job_id=cast(UUID, current["job_id"]),
                status=ResearchSessionStatus.DELETED,
                processed_at=now,
            )
            await self._record_mutation(connection, request, operation="delete", result=result)
        return result

    async def mark_stalled(self, request: ResearchSessionMutationRequest) -> int:
        owner_principal_sha256 = _owner_principal_sha256(request.context.actor_id)
        async with self._engine.begin() as connection:
            count = await connection.scalar(
                text(
                    """
                    WITH candidate AS (
                        SELECT session_id, status, stage, checkpoint
                          FROM research_sessions
                         WHERE session_id = :session_id
                           AND owner_principal_sha256 = :owner_principal_sha256
                           AND status IN ('queued', 'running')
                           AND heartbeat_at <= :stalled_before
                           AND deleted_at IS NULL
                         FOR UPDATE
                    ), stalled AS (
                        UPDATE research_sessions AS session
                           SET status = 'stalled',
                               progress_message = :message,
                               failure_reason_code = 'RESEARCH_SESSION_STALLED',
                               retryable = true, updated_at = :now,
                               row_version = row_version + 1
                          FROM candidate
                         WHERE session.session_id = candidate.session_id
                        RETURNING session.session_id, candidate.status AS from_status,
                                  candidate.stage, candidate.checkpoint
                    ), transition AS (
                        INSERT INTO research_session_transitions (
                            session_id, from_status, to_status, stage, message,
                            checkpoint, reason_code, occurred_at, recorded_at
                        )
                        SELECT session_id, from_status, 'stalled', stage, :message,
                               checkpoint, 'RESEARCH_SESSION_STALLED', :now,
                               GREATEST(clock_timestamp(), :now)
                          FROM stalled
                        RETURNING transition_id
                    ), cancel_jobs AS (
                        UPDATE research_job_outbox AS job
                           SET status = 'cancelled', completed_at = :now
                          FROM stalled
                         WHERE job.session_id = stalled.session_id
                           AND job.status IN ('pending', 'dispatched')
                        RETURNING job.outbox_id
                    )
                    SELECT count(*) FROM transition
                    """
                ),
                {
                    "session_id": request.session_id,
                    "owner_principal_sha256": owner_principal_sha256,
                    "now": request.context.requested_at,
                    "stalled_before": request.context.requested_at - timedelta(minutes=30),
                    "message": "连续 30 分钟没有阶段进展, 可重新运行。",
                },
            )
        return int(count or 0)

    async def mark_stalled_due(self, *, now: datetime) -> int:
        """Atomically stall every inactive session and cancel its active outbox receipt."""

        async with self._engine.begin() as connection:
            count = await connection.scalar(
                text(
                    """
                    WITH candidate AS (
                        SELECT session_id, status, stage, checkpoint
                          FROM research_sessions
                         WHERE status IN ('queued', 'running')
                           AND heartbeat_at <= :stalled_before
                           AND deleted_at IS NULL
                         FOR UPDATE SKIP LOCKED
                    ), stalled AS (
                        UPDATE research_sessions AS session
                           SET status = 'stalled',
                               progress_message = :message,
                               failure_reason_code = 'RESEARCH_SESSION_STALLED',
                               retryable = true, updated_at = :now,
                               row_version = row_version + 1
                          FROM candidate
                         WHERE session.session_id = candidate.session_id
                        RETURNING session.session_id, candidate.status AS from_status,
                                  candidate.stage, candidate.checkpoint
                    ), transition AS (
                        INSERT INTO research_session_transitions (
                            session_id, from_status, to_status, stage, message,
                            checkpoint, reason_code, occurred_at, recorded_at
                        )
                        SELECT session_id, from_status, 'stalled', stage, :message,
                               checkpoint, 'RESEARCH_SESSION_STALLED', :now,
                               GREATEST(clock_timestamp(), :now)
                          FROM stalled
                        RETURNING transition_id
                    ), cancel_jobs AS (
                        UPDATE research_job_outbox AS job
                           SET status = 'cancelled', completed_at = :now
                          FROM stalled
                         WHERE job.session_id = stalled.session_id
                           AND job.status IN ('pending', 'dispatched')
                        RETURNING job.outbox_id
                    )
                    SELECT count(*) FROM transition
                    """
                ),
                {
                    "now": now,
                    "stalled_before": now - timedelta(minutes=30),
                    "message": "连续 30 分钟没有阶段进展, 可重新运行。",
                },
            )
        return int(count or 0)

    async def purge_deleted(self, request: ResearchSessionMutationRequest) -> int:
        now = request.context.requested_at
        async with self._engine.begin() as connection:
            eligible = await connection.scalar(
                text(
                    """
                    SELECT 1 FROM research_sessions
                     WHERE session_id = :session_id
                       AND status IN ('expired', 'deleted')
                       AND purge_due_at <= :now
                       AND purged_at IS NULL
                     FOR UPDATE
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            if not eligible:
                return 0
            await connection.execute(
                text(
                    """
                    UPDATE research_session_contexts
                       SET content = NULL, object_key = NULL,
                           deleted_at = COALESCE(deleted_at, :now)
                     WHERE session_id = :session_id
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_web_retrieval_receipts
                       SET candidate_url_sha256 = ARRAY[]::char(64)[],
                           selected_source_id = NULL,
                           selected_url_sha256 = NULL,
                           purged_at = :now
                     WHERE session_id = :session_id
                       AND purged_at IS NULL
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_report_claims AS claim
                       SET claim = jsonb_build_object('purged', true)
                      FROM research_reports AS report
                     WHERE claim.report_id = report.report_id
                       AND report.session_id = :session_id
                    """
                ),
                {"session_id": request.session_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE research_reports
                       SET report = NULL, artifact_object_key = NULL,
                           artifact_object_version_id = NULL, purged_at = :now
                     WHERE session_id = :session_id AND purged_at IS NULL
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
            result = await connection.execute(
                text(
                    """
                    UPDATE research_sessions
                       SET purged_at = :now, checkpoint_payload = NULL,
                           row_version = row_version + 1
                     WHERE session_id = :session_id AND purged_at IS NULL
                    """
                ),
                {"session_id": request.session_id, "now": now},
            )
        return int(result.rowcount or 0)


__all__ = [
    "RESEARCH_MAX_JOB_ATTEMPTS",
    "RESEARCH_REPORT_READY_EVENT",
    "RESEARCH_TASK_NAME",
    "PostgresResearchRepository",
    "ResearchIdempotencyConflictError",
    "ResearchRepositoryError",
    "ResearchTransitionConflictError",
]
