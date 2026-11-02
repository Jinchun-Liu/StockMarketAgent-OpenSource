"""Bounded one-shot composition for continuous-preview refresh operations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

from apps.runtime.premarket import (
    execute_premarket_brief,
    execute_premarket_ingest_v1_2,
)
from apps.runtime.research import ResearchBriefingEnricherPort, briefing_evidence_ids
from apps.scheduler.requests import (
    build_dev_live_ingest_requests,
    build_runtime_brief_request,
    trading_premarket_window,
)
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.event_views import (
    EventEvidenceRevisionSelector,
    EventEvidenceViewV1_2,
)
from stock_assistant.contracts.ingestion import SourceReleaseTier, SourceRuntimeModeV1_2
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)
from stock_assistant.contracts.public_snapshots import (
    PublicCapabilityRunState,
    PublicCapabilityRunStatus,
    PublicRunState,
)
from stock_assistant.contracts.sectors import ExchangeCalendarQuery, ExchangeCalendarResult
from stock_assistant.modules.briefings.degraded_preview import (
    DegradedPreviewDecision,
    DegradedPreviewPolicy,
    DegradedPreviewRequest,
)
from stock_assistant.platform.db.r1_premarket import PostgresR1PremarketRepository
from stock_assistant.platform.publication.public_smoke import smoke_snapshot_with_retry
from stock_assistant.platform.publication.s3_snapshots import (
    CurrentSnapshotMissing,
    S3SnapshotPublisher,
    SnapshotPublicationError,
    SnapshotPublicationResult,
)
from stock_assistant.platform.publication.snapshot_bundle import build_snapshot_documents
from stock_assistant.platform.runtime.postgres_lock import postgres_advisory_lock
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_CATALOG,
    active_free_source_definitions,
)
from stock_assistant.ports.sectors import TradingCalendarPort

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CONTEXT_NAMESPACE = UUID("e8991051-4651-5133-9f39-e4f1a466976b")
_RETRYABLE_PUBLIC_REASONS = frozenset({"R1_SOURCE_DELAYED", "R1_SOURCE_UNAVAILABLE"})
_HOLIDAY_NO_OP = "XSHG_HOLIDAY_NO_OP"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PublicSnapshotSmoke(Protocol):
    def __call__(
        self,
        *,
        base_url: str,
        expected_product_sha: str,
        expected_snapshot_id: str,
        attempts: int,
    ) -> dict[str, str]: ...


class RefreshCoordinatorError(RuntimeError):
    """A refresh cannot safely produce or switch a public snapshot."""


@dataclass(frozen=True, slots=True)
class RefreshResult:
    operation: str
    status: Literal["SUCCEEDED", "NO_OP"] = "SUCCEEDED"
    source_results: int = 0
    degraded_sources: int = 0
    snapshot_id: str | None = None
    pointer_switched: bool = False
    catch_up_action: str | None = None


class ContinuousPreviewCoordinator:
    """Coordinate one bounded task while PostgreSQL enforces cross-task exclusion."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        calendar: TradingCalendarPort,
        publisher: S3SnapshotPublisher,
        product_commit: str,
        advisory_lock_id: int,
        freshness_grace_minutes: int,
        public_base_url: str,
        public_smoke_attempts: int = 3,
        public_smoke: PublicSnapshotSmoke = smoke_snapshot_with_retry,
        research_enricher: ResearchBriefingEnricherPort | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._calendar = calendar
        self._publisher = publisher
        self._product_commit = product_commit
        self._advisory_lock_id = advisory_lock_id
        self._freshness_grace = timedelta(minutes=freshness_grace_minutes)
        self._public_base_url = public_base_url
        self._public_smoke_attempts = public_smoke_attempts
        self._public_smoke = public_smoke
        self._research_enricher = research_enricher
        self._clock = clock
        if not public_base_url:
            raise ValueError("public_base_url is required")
        if not 1 <= public_smoke_attempts <= 5:
            raise ValueError("public_smoke_attempts must be between one and five")

    async def ingest(self, *, run_at: datetime) -> RefreshResult:
        async with postgres_advisory_lock(self._engine, lock_id=self._advisory_lock_id):
            calendar_and_window = await self._calendar_and_window(run_at)
            if calendar_and_window[1] is None:
                return _holiday_result("ingest")
            return await self._ingest(run_at=run_at, calendar_and_window=calendar_and_window)

    async def publish(self, *, run_at: datetime) -> RefreshResult:
        async with postgres_advisory_lock(self._engine, lock_id=self._advisory_lock_id):
            calendar_and_window = await self._calendar_and_window(run_at)
            if calendar_and_window[1] is None:
                return _holiday_result("publish")
            return await self._publish(run_at=run_at, calendar_and_window=calendar_and_window)

    async def seed(self, *, run_at: datetime) -> RefreshResult:
        async with postgres_advisory_lock(self._engine, lock_id=self._advisory_lock_id):
            calendar_and_window = await self._calendar_and_window(run_at)
            if calendar_and_window[1] is None:
                return _holiday_result("seed")
            ingest = await self._ingest(run_at=run_at, calendar_and_window=calendar_and_window)
            published = await self._publish(
                run_at=run_at,
                calendar_and_window=calendar_and_window,
            )
            return RefreshResult(
                operation="seed",
                source_results=ingest.source_results,
                degraded_sources=ingest.degraded_sources,
                snapshot_id=published.snapshot_id,
                pointer_switched=published.pointer_switched,
            )

    async def seed_last_session(self, *, run_at: datetime) -> RefreshResult:
        """Operator-only seed using the latest complete XSHG session on a closed day."""

        async with postgres_advisory_lock(self._engine, lock_id=self._advisory_lock_id):
            calendar, window = await self._calendar_and_window(run_at)
            if window is None:
                window = self._latest_completed_session_window(
                    run_at=run_at,
                    calendar=calendar,
                )
            calendar_and_window = (calendar, window)
            ingest = await self._ingest(
                run_at=run_at,
                calendar_and_window=calendar_and_window,
            )
            published = await self._publish(
                run_at=run_at,
                calendar_and_window=calendar_and_window,
            )
            return RefreshResult(
                operation="seed-last-session",
                source_results=ingest.source_results,
                degraded_sources=ingest.degraded_sources,
                snapshot_id=published.snapshot_id,
                pointer_switched=published.pointer_switched,
            )

    async def catch_up(self, *, run_at: datetime) -> RefreshResult:
        async with postgres_advisory_lock(self._engine, lock_id=self._advisory_lock_id):
            calendar, window = await self._calendar_and_window(run_at)
            if window is None:
                return _holiday_result("catch_up")
            try:
                current = self._publisher.read_current()
            except (CurrentSnapshotMissing, SnapshotPublicationError, ValueError):
                ingest = await self._ingest(run_at=run_at, calendar_and_window=(calendar, window))
                published = await self._publish(
                    run_at=run_at,
                    calendar_and_window=(calendar, window),
                )
                return RefreshResult(
                    operation="catch_up",
                    source_results=ingest.source_results,
                    degraded_sources=ingest.degraded_sources,
                    snapshot_id=published.snapshot_id,
                    pointer_switched=published.pointer_switched,
                    catch_up_action="POINTER_MISSING_OR_INVALID",
                )
            retry_source_ids: tuple[str, ...] | None = None
            if current.validated.manifest.data_as_of < window[1]:
                action = "POINTER_BEHIND"
            elif retry_source_ids := self._active_retryable_source_ids(
                current.validated.run_status.capabilities
            ):
                action = "ACTIVE_SOURCE_RETRYABLE_FAILURE"
            else:
                return RefreshResult(
                    operation="catch_up",
                    snapshot_id=str(current.validated.manifest.snapshot_id),
                    catch_up_action="NO_RETRY_REQUIRED",
                )
            ingest = await self._ingest(
                run_at=run_at,
                calendar_and_window=(calendar, window),
                source_ids=retry_source_ids,
            )
            published = await self._publish(
                run_at=run_at,
                calendar_and_window=(calendar, window),
            )
            return RefreshResult(
                operation="catch_up",
                source_results=ingest.source_results,
                degraded_sources=ingest.degraded_sources,
                snapshot_id=published.snapshot_id,
                pointer_switched=published.pointer_switched,
                catch_up_action=action,
            )

    async def _ingest(
        self,
        *,
        run_at: datetime,
        calendar_and_window: tuple[
            ExchangeCalendarResult,
            tuple[datetime, datetime] | None,
        ]
        | None = None,
        source_ids: tuple[str, ...] | None = None,
    ) -> RefreshResult:
        _, window = calendar_and_window or await self._calendar_and_window(run_at)
        if window is None:
            return _holiday_result("ingest")
        results = []
        for request in build_dev_live_ingest_requests(
            run_at,
            window=window,
            source_ids=source_ids,
        ):
            results.append(
                await execute_premarket_ingest_v1_2(
                    request,
                    run_at,
                    engine=self._engine,
                )
            )
        return RefreshResult(
            operation="ingest",
            source_results=len(results),
            degraded_sources=sum(result.status.value == "degraded" for result in results),
        )

    async def _publish(
        self,
        *,
        run_at: datetime,
        calendar_and_window: tuple[
            ExchangeCalendarResult,
            tuple[datetime, datetime] | None,
        ]
        | None = None,
    ) -> RefreshResult:
        calendar, window = calendar_and_window or await self._calendar_and_window(run_at)
        if window is None:
            return _holiday_result("publish")
        run_started_at = self._clock()
        request = build_runtime_brief_request(
            run_at,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            window=window,
        )
        await execute_premarket_brief(request, run_at, engine=self._engine)
        evaluation_at = self._clock()
        evaluation_calendar = calendar.model_copy(update={"as_of": evaluation_at})
        repository = PostgresR1PremarketRepository(self._engine)
        current, lkg = await self._briefing_candidates(
            repository=repository,
            run_at=evaluation_at,
            window_start=window[0],
            window_end=window[1],
        )
        decision = DegradedPreviewPolicy().decide(
            DegradedPreviewRequest(
                as_of=evaluation_at,
                calendar=evaluation_calendar,
                current_briefing=current,
                last_known_good=lkg,
            )
        )
        if decision.briefing is None or decision.run_state is PublicRunState.FAILED:
            raise RefreshCoordinatorError("NO_USABLE_BRIEFING_POINTER_SWITCH_FORBIDDEN")
        evidence = await self._load_evidence(repository=repository, decision=decision)
        selected_briefing = decision.briefing
        assert selected_briefing is not None
        research_briefing = cast(PremarketBriefingViewV1_3, selected_briefing)
        if self._research_enricher is not None:
            research_briefing = await self._research_enricher.enrich(
                briefing=selected_briefing,
                context=request.context.model_copy(update={"as_of": selected_briefing.as_of}),
                evidence_ids=briefing_evidence_ids(selected_briefing),
            )
        previous_last_success = None
        try:
            previous_last_success = (
                self._publisher.read_current().validated.manifest.last_successful_run_at
            )
        except (CurrentSnapshotMissing, SnapshotPublicationError, ValueError):
            pass
        completed_at = self._clock()
        published_at = max(completed_at, decision.briefing.generated_at)
        fresh_until = self._fresh_until(calendar=calendar, published_at=published_at)
        snapshot = build_snapshot_documents(
            decision=decision,
            research_briefing=research_briefing,
            evidence_by_event_id=evidence,
            snapshot_id=uuid4(),
            run_id=uuid4(),
            product_commit=self._product_commit,
            run_started_at=run_started_at,
            run_completed_at=completed_at,
            published_at=published_at,
            fresh_until=fresh_until,
            previous_last_successful_run_at=previous_last_success,
        )
        result = self._publisher.publish(snapshot)
        await asyncio.to_thread(
            self._public_smoke,
            base_url=self._public_base_url,
            expected_product_sha=self._product_commit,
            expected_snapshot_id=result.snapshot_id,
            attempts=self._public_smoke_attempts,
        )
        return _publication_result(result)

    async def _calendar_and_window(
        self,
        run_at: datetime,
    ) -> tuple[ExchangeCalendarResult, tuple[datetime, datetime] | None]:
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        local_date = run_at.astimezone(_SHANGHAI).date()
        context = OperationContext(
            schema_version="1.0",
            request_id=uuid5(_CONTEXT_NAMESPACE, f"calendar:{run_at.isoformat()}"),
            correlation_id=uuid5(_CONTEXT_NAMESPACE, f"refresh:{local_date.isoformat()}"),
            actor_id="r1-preview-coordinator",
            workspace_id="default",
            as_of=run_at,
            requested_at=run_at,
            locale="zh-CN",
            timezone="Asia/Shanghai",
        )
        calendar = await self._calendar.get_calendar(
            ExchangeCalendarQuery(
                schema_version="1.0",
                context=context,
                exchanges=["XSHG"],
                date_from=local_date - timedelta(days=14),
                date_to=local_date + timedelta(days=45),
            )
        )
        current = next(
            (
                item
                for item in calendar.days
                if item.exchange == "XSHG" and item.trading_date == local_date
            ),
            None,
        )
        if current is None:
            raise RefreshCoordinatorError("CURRENT_XSHG_CALENDAR_DAY_MISSING")
        if current.status.value not in {"open", "partial"}:
            return calendar, None
        return calendar, trading_premarket_window(run_at, tuple(calendar.days))

    @staticmethod
    def _latest_completed_session_window(
        *,
        run_at: datetime,
        calendar: ExchangeCalendarResult,
    ) -> tuple[datetime, datetime]:
        local_date = run_at.astimezone(_SHANGHAI).date()
        completed_sessions = sorted(
            day.trading_date
            for day in calendar.days
            if day.exchange == "XSHG"
            and day.status.value in {"open", "partial"}
            and day.trading_date <= local_date
        )
        if not completed_sessions:
            raise RefreshCoordinatorError("RECENT_XSHG_SESSION_WINDOW_UNAVAILABLE")
        session_anchor = datetime.combine(completed_sessions[-1], time(9, 5), _SHANGHAI)
        try:
            return trading_premarket_window(session_anchor, tuple(calendar.days))
        except ValueError as error:
            raise RefreshCoordinatorError("RECENT_XSHG_SESSION_WINDOW_UNAVAILABLE") from error

    async def _briefing_candidates(
        self,
        *,
        repository: PostgresR1PremarketRepository,
        run_at: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[PremarketBriefingViewV1_2 | None, PremarketBriefingViewV1_2 | None]:
        latest = await repository.get_latest_briefing(
            as_of=run_at,
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        current = (
            latest
            if isinstance(latest, PremarketBriefingViewV1_2)
            and latest.comparison_since == window_start
            and window_end <= latest.as_of <= run_at
            and latest.generated_at <= run_at
            else None
        )
        previous = await repository.get_latest_briefing(
            as_of=window_start,
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        return current, previous if isinstance(previous, PremarketBriefingViewV1_2) else None

    async def _load_evidence(
        self,
        *,
        repository: PostgresR1PremarketRepository,
        decision: DegradedPreviewDecision,
    ) -> dict[UUID, EventEvidenceViewV1_2]:
        briefing = decision.briefing
        if briefing is None:
            return {}
        evidence: dict[UUID, EventEvidenceViewV1_2] = {}
        for section in decision.sections_in_order:
            for item in section.items:
                selected = await repository.get_event_evidence(
                    event_id=item.event_id,
                    selector=EventEvidenceRevisionSelector(
                        schema_version="1.0",
                        as_of=briefing.as_of,
                        revision=item.event_revision,
                    ),
                    expected_runtime_mode=briefing.runtime_mode,
                    expected_release_tier=briefing.release_tier,
                )
                if not isinstance(selected, EventEvidenceViewV1_2):
                    raise RefreshCoordinatorError("PUBLIC_EVIDENCE_REVISION_MISSING")
                evidence[item.event_id] = selected
        return evidence

    def _fresh_until(
        self,
        *,
        calendar: ExchangeCalendarResult,
        published_at: datetime,
    ) -> datetime:
        published_date = published_at.astimezone(_SHANGHAI).date()
        future_sessions = sorted(
            day.trading_date
            for day in calendar.days
            if day.exchange == "XSHG"
            and day.status.value in {"open", "partial"}
            and day.trading_date > published_date
        )
        if not future_sessions:
            raise RefreshCoordinatorError("NEXT_XSHG_REFRESH_CUTOFF_UNAVAILABLE")
        catch_up_cutoff = datetime.combine(future_sessions[0], time(9, 20), _SHANGHAI)
        fresh_until = catch_up_cutoff + self._freshness_grace
        if fresh_until <= published_at:
            raise RefreshCoordinatorError("SNAPSHOT_FRESHNESS_WINDOW_INVALID")
        return fresh_until

    @staticmethod
    def _active_retryable_source_ids(
        capabilities: Sequence[PublicCapabilityRunStatus],
    ) -> tuple[str, ...]:
        active_ids = {
            item.source_id for item in active_free_source_definitions(FREE_SOURCE_CATALOG)
        }
        return tuple(
            sorted(
                {
                    value.source_id
                    for value in capabilities
                    if value.source_id in active_ids
                    and value.state
                    in {
                        PublicCapabilityRunState.GAP,
                        PublicCapabilityRunState.FAILED,
                    }
                    and value.safe_reason_code in _RETRYABLE_PUBLIC_REASONS
                }
            )
        )


def _publication_result(result: SnapshotPublicationResult) -> RefreshResult:
    return RefreshResult(
        operation="publish",
        snapshot_id=result.snapshot_id,
        pointer_switched=result.pointer_switched,
    )


def _holiday_result(operation: str) -> RefreshResult:
    return RefreshResult(
        operation=operation,
        status="NO_OP",
        catch_up_action=_HOLIDAY_NO_OP,
    )


__all__ = [
    "ContinuousPreviewCoordinator",
    "RefreshCoordinatorError",
    "RefreshResult",
]
