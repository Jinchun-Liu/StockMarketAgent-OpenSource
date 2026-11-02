"""Scheduler for frozen R1 fixtures and the stock-agnostic R2 event catalog."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import time as wall_time
from typing import Final
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError
from celery import chord, group
from celery.canvas import Signature
from minio.error import S3Error
from sqlalchemy.ext.asyncio import create_async_engine

from apps.scheduler.requests import (
    build_demo_replay_ingest_requests,
    build_dev_live_ingest_requests,
    build_runtime_brief_request,
)
from apps.worker.celery_app import celery_app
from apps.worker.schedule_policy import schedule_doctor
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.config import get_settings
from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.global_events import PolicyRefreshRequest
from stock_assistant.contracts.ingestion import SourceRuntimeMode, SourceRuntimeModeV1_2
from stock_assistant.contracts.jobs import JobRequest
from stock_assistant.contracts.premarket_jobs import (
    PremarketBriefJobRequest,
    PremarketIngestJobRequest,
    PremarketIngestJobRequestV1_1,
    PremarketIngestJobRequestV1_2,
)
from stock_assistant.contracts.research import (
    ResearchGenerateResult,
    ResearchGenerateTaskRequest,
    ResearchSessionMutationRequest,
)
from stock_assistant.contracts.storage import ObjectDeleteRequest
from stock_assistant.platform.db.research import (
    RESEARCH_MAX_JOB_ATTEMPTS,
    PostgresResearchRepository,
)
from stock_assistant.platform.object_store.minio_store import MinioObjectStore
from stock_assistant.platform.object_store.s3_store import S3ObjectStore
from stock_assistant.platform.research.synthetic_global_events import (
    synthetic_global_event_manifest_sha256,
    synthetic_global_events,
)
from stock_assistant.platform.runtime import database_url_from_environment
from stock_assistant.platform.scheduling.celery_dispatcher import CeleryJobDispatcher
from stock_assistant.ports.infrastructure import ObjectStorePort

_SHANGHAI: Final = ZoneInfo("Asia/Shanghai")
_INGEST_AT: Final = wall_time(8, 55)
_BRIEF_AT: Final = wall_time(9, 5)
_POLL_SECONDS: Final = 15
_GLOBAL_EVENT_REFRESH_SECONDS: Final = 60 * 60
_GLOBAL_EVENT_REFRESH_NAMESPACE: Final = UUID("180a3032-8f0f-5b49-80f9-5ab1ff3140e2")
_LOGGER = logging.getLogger(__name__)

type PremarketRequest = (
    PremarketIngestJobRequest
    | PremarketIngestJobRequestV1_1
    | PremarketIngestJobRequestV1_2
    | PremarketBriefJobRequest
)


def _signature(request: PremarketRequest) -> Signature:
    return celery_app.signature(
        request.job_name,
        args=(request.model_dump(mode="json"),),
        immutable=True,
    )


def _require_fixture_scheduler() -> None:
    settings = get_settings()
    settings.assert_safe_production()
    if (
        not settings.enable_r1_fixture
        or settings.r1_runtime_mode is not SourceRuntimeMode.DEMO_REPLAY
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_DISABLED")


def _require_dev_live_once() -> None:
    settings = get_settings()
    settings.assert_safe_production()
    if (
        not settings.enable_r1_dev_live
        or settings.r1_runtime_mode is not SourceRuntimeMode.DEV_LIVE
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_DEV_LIVE_DISABLED")


def _research_store() -> ObjectStorePort:
    settings = get_settings()
    if settings.enable_r2_private_preview:
        import boto3
        from botocore.config import Config

        if settings.r2_artifact_bucket is None or settings.product_commit is None:
            raise ValueError("R2_PREVIEW_OBJECT_STORE_NOT_CONFIGURED")
        return S3ObjectStore(
            boto3.client(
                "s3",
                region_name=settings.aws_region,
                config=Config(
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"max_attempts": 3, "mode": "standard"},
                    s3={"addressing_style": "virtual"},
                ),
            ),
            bucket=settings.r2_artifact_bucket,
            product_sha=settings.product_commit,
        )

    from minio import Minio

    return MinioObjectStore(
        Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
        ),
        bucket=settings.minio_artifact_bucket,
    )


def build_global_event_refresh_request(run_at: datetime) -> PolicyRefreshRequest:
    """Build a stock-agnostic request identified by hour and fixture manifest."""

    if run_at.tzinfo is None:
        raise ValueError("run_at must be timezone-aware")
    canonical_run_at = run_at.astimezone(UTC)
    bucket = canonical_run_at.replace(minute=0, second=0, microsecond=0)
    manifest_sha256 = synthetic_global_event_manifest_sha256(synthetic_global_events())
    identity = f"{bucket.isoformat()}:{manifest_sha256}"
    request_id = uuid5(_GLOBAL_EVENT_REFRESH_NAMESPACE, f"request:{identity}")
    return PolicyRefreshRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            request_id=request_id,
            correlation_id=request_id,
            actor_id="global-event-scheduler",
            workspace_id="global-event-catalog",
            as_of=canonical_run_at,
            requested_at=canonical_run_at,
            idempotency_key=f"global-event-refresh-{identity}",
        ),
        scheduled_for=canonical_run_at,
        fixture_manifest_sha256=manifest_sha256,
    )


def dispatch_global_event_refresh(run_at: datetime) -> str | None:
    """Dispatch one global catalog refresh; never include a user stock identity."""

    settings = get_settings()
    settings.assert_safe_production()
    if settings.app_env == "production" or not settings.enable_r2_synthetic_research:
        return None
    request = build_global_event_refresh_request(run_at)
    result = celery_app.signature(
        "stock_assistant.policies.refresh.v1",
        args=(request.model_dump(mode="json"),),
        immutable=True,
    ).apply_async()
    return str(result.id)


def global_event_refresh_due(run_at: datetime, last_dispatched_at: datetime | None) -> bool:
    if run_at.tzinfo is None:
        raise ValueError("run_at must be timezone-aware")
    if last_dispatched_at is None:
        return True
    return (run_at - last_dispatched_at).total_seconds() >= _GLOBAL_EVENT_REFRESH_SECONDS


async def dispatch_research_outbox_once(run_at: datetime) -> int:
    """Relay transactional research outbox rows to Valkey-backed Celery."""

    settings = get_settings()
    if settings.app_env == "production" or not (
        settings.enable_r2_synthetic_research or settings.enable_r2_real_research
    ):
        return 0
    engine = create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    repository = PostgresResearchRepository(engine)
    dispatcher = CeleryJobDispatcher[ResearchGenerateTaskRequest, ResearchGenerateResult](
        celery_app,
        result_model=ResearchGenerateResult,
    )
    dispatched = 0
    try:
        for item in await repository.pending_jobs(limit=20):
            await dispatcher.enqueue(
                JobRequest[ResearchGenerateTaskRequest](
                    schema_version="1.0",
                    job_id=item.job_id,
                    job_name=item.task_name,
                    context=item.payload.context,
                    payload=item.payload,
                    queued_at=item.created_at,
                    attempt=item.attempt,
                    max_attempts=RESEARCH_MAX_JOB_ATTEMPTS,
                )
            )
            if await repository.mark_job_dispatched(
                job_id=item.job_id,
                dispatched_at=run_at,
            ):
                dispatched += 1
        return dispatched
    finally:
        await engine.dispose()


async def maintain_research_lifecycle_once(run_at: datetime) -> tuple[int, int, int]:
    """Expire seven-day sessions and purge inaccessible artifacts after 24 hours."""

    settings = get_settings()
    if settings.app_env == "production" or not (
        settings.enable_r2_synthetic_research
        or settings.enable_r2_real_research
        or settings.enable_r2_private_preview
    ):
        return 0, 0, 0
    engine = create_async_engine(
        database_url_from_environment(settings.database_url.get_secret_value())
    )
    repository = PostgresResearchRepository(engine)
    purged = 0
    try:
        stalled = await repository.mark_stalled_due(now=run_at)
        expired = await repository.expire_due(now=run_at)
        store = _research_store()
        for candidate in await repository.purge_candidates(now=run_at):
            context = MutationContext(
                schema_version="1.0",
                actor_id="research-lifecycle",
                workspace_id="on-demand-research",
                as_of=run_at,
                requested_at=run_at,
                idempotency_key=f"research-purge-{candidate.session_id}",
            )
            if candidate.reference is not None:
                reason_code = None
                try:
                    result = await store.delete(
                        ObjectDeleteRequest(
                            schema_version="1.0",
                            context=context,
                            reference=candidate.reference,
                        )
                    )
                    if not result.deleted:
                        reason_code = "RESEARCH_ARTIFACT_DELETE_NOT_CONFIRMED"
                except ValueError as error:
                    if str(error) not in {
                        "OBJECT_NAMESPACE_INVALID",
                        "OBJECT_KEY_INVALID",
                        "S3_VERSION_REQUIRED_FOR_DELETE",
                    }:
                        raise
                    reason_code = str(error)
                except (ClientError, BotoConnectionError, HTTPClientError, S3Error, OSError):
                    reason_code = "RESEARCH_ARTIFACT_DELETE_FAILED"
                if reason_code is not None:
                    _LOGGER.warning(
                        "research artifact purge deferred session_id=%s reason_code=%s",
                        candidate.session_id,
                        reason_code,
                    )
                    continue
            purged += await repository.purge_deleted(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=context,
                    session_id=candidate.session_id,
                )
            )
        return stalled, expired, purged
    finally:
        await engine.dispose()


def dispatch_fixture_cycle(
    run_at: datetime,
    *,
    include_brief: bool,
    replay_session_id: str | None = None,
    wait_for_completion: bool = False,
    timeout_seconds: int = 120,
) -> str:
    """Dispatch independent source ingests and an optional persisted-state brief."""

    _require_fixture_scheduler()
    ingest_requests = build_demo_replay_ingest_requests(
        run_at,
        replay_session_id=replay_session_id,
    )
    ingest_group = group(_signature(request) for request in ingest_requests)
    if include_brief:
        result = chord(
            ingest_group,
            _signature(
                build_runtime_brief_request(
                    run_at,
                    runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
                )
            ),
        ).apply_async()
    else:
        result = ingest_group.apply_async()
    if wait_for_completion:
        result.get(timeout=timeout_seconds, propagate=True)
    return str(result.id)


def dispatch_dev_live_cycle(
    run_at: datetime,
    *,
    wait_for_completion: bool = True,
    timeout_seconds: int = 120,
) -> str:
    """Dispatch the explicit one-shot development live cycle without fallback."""

    _require_dev_live_once()
    ingest_group = group(_signature(request) for request in build_dev_live_ingest_requests(run_at))
    result = chord(
        ingest_group,
        _signature(
            build_runtime_brief_request(
                run_at,
                runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            )
        ),
    ).apply_async()
    if wait_for_completion:
        result.get(timeout=timeout_seconds, propagate=True)
    return str(result.id)


def due_dispatches(run_at: datetime, dispatched: set[str]) -> tuple[tuple[str, bool], ...]:
    """Return daily dispatch keys without reading mutable external state."""

    if run_at.tzinfo is None:
        raise ValueError("run_at must be timezone-aware")
    local = run_at.astimezone(_SHANGHAI)
    if local.weekday() >= 5:
        return ()
    day = local.date().isoformat()
    brief_key = f"{day}:brief"
    ingest_key = f"{day}:ingest"
    if brief_key in dispatched:
        return ()
    if local.time() >= _BRIEF_AT and brief_key not in dispatched:
        # A late start runs both source tasks before the persisted-state brief callback.
        return ((brief_key, True),)
    if local.time() >= _INGEST_AT and ingest_key not in dispatched:
        return ((ingest_key, False),)
    return ()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-once", action="store_true")
    parser.add_argument("--dev-live-once", action="store_true")
    parser.add_argument("--replay-session-id")
    parser.add_argument("--at", type=datetime.fromisoformat)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    gaps = schedule_doctor()
    if gaps:
        raise RuntimeError("; ".join(gaps))
    if args.fixture_once:
        run_at = args.at or datetime.now(_SHANGHAI)
        print(
            dispatch_fixture_cycle(
                run_at,
                include_brief=True,
                replay_session_id=args.replay_session_id,
                wait_for_completion=True,
            ),
            flush=True,
        )
        return 0
    if args.dev_live_once:
        if args.replay_session_id is not None:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_REPLAY_SESSION_FOR_LIVE_FORBIDDEN",
            )
        run_at = args.at or datetime.now(_SHANGHAI)
        print(dispatch_dev_live_cycle(run_at), flush=True)
        return 0

    settings = get_settings()
    settings.assert_safe_production()
    if not settings.enable_r1_fixture and not (
        settings.enable_r2_synthetic_research
        or settings.enable_r2_real_research
        or settings.enable_r2_private_preview
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "SYNTHETIC_RUNTIME_DISABLED")
    dispatched: set[str] = set()
    last_global_event_refresh: datetime | None = None
    while True:
        run_at = datetime.now(_SHANGHAI)
        if settings.enable_r2_synthetic_research and global_event_refresh_due(
            run_at, last_global_event_refresh
        ):
            dispatch_global_event_refresh(run_at)
            last_global_event_refresh = run_at
        asyncio.run(dispatch_research_outbox_once(run_at))
        asyncio.run(maintain_research_lifecycle_once(run_at))
        if settings.enable_r1_fixture:
            for key, include_brief in due_dispatches(run_at, dispatched):
                dispatch_fixture_cycle(run_at, include_brief=include_brief)
                dispatched.add(key)
        time.sleep(_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_global_event_refresh_request",
    "dispatch_dev_live_cycle",
    "dispatch_fixture_cycle",
    "dispatch_global_event_refresh",
    "due_dispatches",
    "global_event_refresh_due",
    "main",
]
