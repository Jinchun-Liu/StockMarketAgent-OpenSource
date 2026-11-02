"""Pure degraded-preview and last-known-good selection for the R1 briefing."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from stock_assistant.contracts.portfolio import R1_FIXED_TARGET_INSTRUMENT_ID
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingSection,
    PremarketBriefingSections,
    PremarketBriefingViewV1_2,
    SourceCoverageStatus,
    SourceCoverageV1_2,
)
from stock_assistant.contracts.public_snapshots import (
    PublicRunState,
    PublicSnapshotCompleteness,
)
from stock_assistant.contracts.sectors import (
    ExchangeCalendarResult,
    TradingDayStatus,
)

_XSHG = "XSHG"
_XSHG_TIMEZONE = ZoneInfo("Asia/Shanghai")
_MAX_LKG_SESSIONS = 3
_SESSION_STATUSES = frozenset({TradingDayStatus.OPEN, TradingDayStatus.PARTIAL})
_UNAVAILABLE_MESSAGE = "暂无可用数据"


class DegradedPreviewState(StrEnum):
    """Application-level availability state for one public preview decision."""

    FRESH = "fresh"
    PARTIAL = "partial"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class DegradedPreviewReason(StrEnum):
    """Stable, publish-safe explanation for non-complete decisions."""

    CURRENT_SOURCE_GAPS = "CURRENT_SOURCE_GAPS"
    CURRENT_ALL_SOURCES_UNHEALTHY = "CURRENT_ALL_SOURCES_UNHEALTHY"
    LKG_WITHIN_THREE_SESSIONS = "LKG_WITHIN_THREE_SESSIONS"
    LKG_EXPIRED = "LKG_EXPIRED"
    NO_USABLE_BRIEFING = "NO_USABLE_BRIEFING"
    CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DegradedPreviewRequest:
    """Typed inputs available after repository and trading-calendar reads."""

    as_of: datetime
    calendar: ExchangeCalendarResult
    current_briefing: PremarketBriefingViewV1_2 | None = None
    last_known_good: PremarketBriefingViewV1_2 | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        if self.calendar.as_of != self.as_of:
            raise ValueError("calendar.as_of must equal the preview as_of")

        candidates = tuple(
            item for item in (self.current_briefing, self.last_known_good) if item is not None
        )
        for candidate in candidates:
            if candidate.as_of > self.as_of or candidate.generated_at > self.as_of:
                raise ValueError("preview candidates cannot contain future information")
            if candidate.instrument.instrument_id != R1_FIXED_TARGET_INSTRUMENT_ID:
                raise ValueError("preview candidates must use the R1 fixed target")

        if self.current_briefing is None or self.last_known_good is None:
            return
        current = self.current_briefing
        previous = self.last_known_good
        if previous.as_of > current.as_of:
            raise ValueError("last_known_good cannot be newer than current_briefing")
        if previous.instrument != current.instrument:
            raise ValueError("fallback candidates must identify the same instrument")
        if previous.runtime_mode is not current.runtime_mode:
            raise ValueError("fallback candidates must use the same runtime mode")
        if previous.release_tier is not current.release_tier:
            raise ValueError("fallback candidates must use the same release tier")


@dataclass(frozen=True, slots=True)
class DegradedPreviewDecision:
    """Strongly typed output consumed by the snapshot publishing composition root."""

    state: DegradedPreviewState
    completeness: PublicSnapshotCompleteness
    run_state: PublicRunState
    briefing: PremarketBriefingViewV1_2 | None
    trading_sessions_old: int | None
    reason: DegradedPreviewReason | None
    source_gaps: tuple[SourceCoverageV1_2, ...] = ()

    def __post_init__(self) -> None:
        expected_public_state = {
            DegradedPreviewState.FRESH: (
                PublicSnapshotCompleteness.COMPLETE,
                PublicRunState.SUCCEEDED,
            ),
            DegradedPreviewState.PARTIAL: (
                PublicSnapshotCompleteness.PARTIAL,
                PublicRunState.DEGRADED,
            ),
            DegradedPreviewState.STALE: (
                PublicSnapshotCompleteness.PARTIAL,
                PublicRunState.DEGRADED,
            ),
            DegradedPreviewState.UNAVAILABLE: (
                PublicSnapshotCompleteness.UNAVAILABLE,
                PublicRunState.FAILED,
            ),
        }[self.state]
        if (self.completeness, self.run_state) != expected_public_state:
            raise ValueError("preview state must match public completeness and run state")
        if self.state is DegradedPreviewState.UNAVAILABLE:
            if self.briefing is not None:
                raise ValueError("unavailable previews cannot expose a briefing")
        elif self.briefing is None:
            raise ValueError("available previews require a selected briefing")

        if self.state is DegradedPreviewState.FRESH:
            if self.trading_sessions_old != 0 or self.reason is not None:
                raise ValueError("fresh previews require zero session age and no reason")
        elif self.reason is None:
            raise ValueError("non-fresh previews require a stable reason")
        if self.state is DegradedPreviewState.STALE and (
            self.trading_sessions_old is None
            or not 0 <= self.trading_sessions_old <= _MAX_LKG_SESSIONS
        ):
            raise ValueError("stale previews must be zero to three XSHG sessions old")

    @property
    def sections(self) -> PremarketBriefingSections:
        """Return all five sections, empty only when no usable briefing exists."""

        if self.briefing is not None:
            return self.briefing.sections
        return PremarketBriefingSections(
            statutory_announcements=PremarketBriefingSection(),
            financial_changes=PremarketBriefingSection(),
            market_snapshot=PremarketBriefingSection(),
            company_industry_news=PremarketBriefingSection(),
            macro_flashes=PremarketBriefingSection(),
        )

    @property
    def sections_in_order(self) -> tuple[PremarketBriefingSection, ...]:
        """Expose a deterministic order without a public untyped mapping."""

        sections = self.sections
        return (
            sections.statutory_announcements,
            sections.financial_changes,
            sections.market_snapshot,
            sections.company_industry_news,
            sections.macro_flashes,
        )

    @property
    def display_message(self) -> str | None:
        """Provide the exact non-fabricated empty-state copy for callers."""

        if self.state is DegradedPreviewState.UNAVAILABLE:
            return _UNAVAILABLE_MESSAGE
        return None


class DegradedPreviewPolicy:
    """Select current or bounded LKG data without fabricating missing values."""

    def decide(self, request: DegradedPreviewRequest) -> DegradedPreviewDecision:
        """Evaluate current coverage first, then use at most three XSHG LKG sessions."""

        current_gaps = _source_gaps(request.current_briefing)
        current_has_healthy_source = _has_healthy_source(request.current_briefing)
        current_can_publish_degraded = (
            request.current_briefing is not None
            and bool(request.current_briefing.source_coverage)
            and not current_has_healthy_source
        )
        fallback_due_to_unhealthy_current = (
            request.current_briefing is not None and not current_has_healthy_source
        )

        using_last_known_good = not current_has_healthy_source and _has_healthy_source(
            request.last_known_good
        )
        selected = request.last_known_good if using_last_known_good else request.current_briefing
        selected_gaps = _source_gaps(selected)
        all_gaps = _merge_source_gaps(current_gaps, selected_gaps)
        current_all_sources_unhealthy = (
            selected is request.current_briefing and current_can_publish_degraded
        )
        if selected is None or (
            not _has_healthy_source(selected) and not current_all_sources_unhealthy
        ):
            reason = (
                DegradedPreviewReason.CURRENT_ALL_SOURCES_UNHEALTHY
                if fallback_due_to_unhealthy_current
                else DegradedPreviewReason.NO_USABLE_BRIEFING
            )
            return _unavailable(reason=reason, source_gaps=all_gaps)

        selected_date = selected.as_of.astimezone(_XSHG_TIMEZONE).date()
        target_date = request.as_of.astimezone(_XSHG_TIMEZONE).date()
        if selected_date > target_date:
            raise ValueError("preview candidates cannot be later than the XSHG target date")

        needs_calendar = selected_date < target_date or using_last_known_good
        calendar_is_incomplete = needs_calendar and not _has_complete_xshg_date_range(
            calendar=request.calendar,
            date_from=selected_date,
            date_to=target_date,
        )
        if needs_calendar and (
            request.calendar.degraded or request.calendar.source_gaps or calendar_is_incomplete
        ):
            return _unavailable(
                reason=DegradedPreviewReason.CALENDAR_UNAVAILABLE,
                source_gaps=all_gaps,
            )

        session_age = _count_xshg_sessions(
            calendar=request.calendar,
            after=selected_date,
            through=target_date,
        )
        if session_age > _MAX_LKG_SESSIONS:
            if current_can_publish_degraded:
                current = request.current_briefing
                assert current is not None
                return _degraded_current(current, source_gaps=all_gaps)
            return _unavailable(
                reason=DegradedPreviewReason.LKG_EXPIRED,
                source_gaps=all_gaps,
                trading_sessions_old=session_age,
            )
        if session_age > 0:
            return DegradedPreviewDecision(
                state=DegradedPreviewState.STALE,
                completeness=PublicSnapshotCompleteness.PARTIAL,
                run_state=PublicRunState.DEGRADED,
                briefing=selected,
                trading_sessions_old=session_age,
                reason=DegradedPreviewReason.LKG_WITHIN_THREE_SESSIONS,
                source_gaps=all_gaps,
            )
        if using_last_known_good:
            return DegradedPreviewDecision(
                state=DegradedPreviewState.STALE,
                completeness=PublicSnapshotCompleteness.PARTIAL,
                run_state=PublicRunState.DEGRADED,
                briefing=selected,
                trading_sessions_old=0,
                reason=DegradedPreviewReason.LKG_WITHIN_THREE_SESSIONS,
                source_gaps=all_gaps,
            )
        if current_all_sources_unhealthy:
            assert selected is not None
            return _degraded_current(selected, source_gaps=all_gaps)
        if selected_gaps:
            return DegradedPreviewDecision(
                state=DegradedPreviewState.PARTIAL,
                completeness=PublicSnapshotCompleteness.PARTIAL,
                run_state=PublicRunState.DEGRADED,
                briefing=selected,
                trading_sessions_old=0,
                reason=DegradedPreviewReason.CURRENT_SOURCE_GAPS,
                source_gaps=all_gaps,
            )
        return DegradedPreviewDecision(
            state=DegradedPreviewState.FRESH,
            completeness=PublicSnapshotCompleteness.COMPLETE,
            run_state=PublicRunState.SUCCEEDED,
            briefing=selected,
            trading_sessions_old=0,
            reason=None,
        )


def _source_gaps(
    briefing: PremarketBriefingViewV1_2 | None,
) -> tuple[SourceCoverageV1_2, ...]:
    if briefing is None:
        return ()
    return tuple(
        sorted(
            (
                coverage
                for coverage in briefing.source_coverage
                if coverage.status is not SourceCoverageStatus.OK
            ),
            key=lambda item: (item.source_id, item.capability),
        )
    )


def _has_healthy_source(briefing: PremarketBriefingViewV1_2 | None) -> bool:
    return briefing is not None and any(
        coverage.status is SourceCoverageStatus.OK for coverage in briefing.source_coverage
    )


def _merge_source_gaps(
    *groups: tuple[SourceCoverageV1_2, ...],
) -> tuple[SourceCoverageV1_2, ...]:
    merged: dict[tuple[str, str], SourceCoverageV1_2] = {}
    for group in groups:
        for coverage in group:
            merged[(coverage.source_id, coverage.capability)] = coverage
    return tuple(merged[key] for key in sorted(merged))


def _count_xshg_sessions(
    *,
    calendar: ExchangeCalendarResult,
    after: date,
    through: date,
) -> int:
    session_dates = {
        day.trading_date
        for day in calendar.days
        if (
            day.exchange == _XSHG
            and day.status in _SESSION_STATUSES
            and after < day.trading_date <= through
        )
    }
    return len(session_dates)


def _has_complete_xshg_date_range(
    *,
    calendar: ExchangeCalendarResult,
    date_from: date,
    date_to: date,
) -> bool:
    required_dates = {
        date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)
    }
    actual_dates = {
        day.trading_date
        for day in calendar.days
        if day.exchange == _XSHG and date_from <= day.trading_date <= date_to
    }
    return actual_dates == required_dates


def _unavailable(
    *,
    reason: DegradedPreviewReason,
    source_gaps: tuple[SourceCoverageV1_2, ...],
    trading_sessions_old: int | None = None,
) -> DegradedPreviewDecision:
    return DegradedPreviewDecision(
        state=DegradedPreviewState.UNAVAILABLE,
        completeness=PublicSnapshotCompleteness.UNAVAILABLE,
        run_state=PublicRunState.FAILED,
        briefing=None,
        trading_sessions_old=trading_sessions_old,
        reason=reason,
        source_gaps=source_gaps,
    )


def _degraded_current(
    briefing: PremarketBriefingViewV1_2,
    *,
    source_gaps: tuple[SourceCoverageV1_2, ...],
) -> DegradedPreviewDecision:
    return DegradedPreviewDecision(
        state=DegradedPreviewState.PARTIAL,
        completeness=PublicSnapshotCompleteness.PARTIAL,
        run_state=PublicRunState.DEGRADED,
        briefing=briefing,
        trading_sessions_old=0,
        reason=DegradedPreviewReason.CURRENT_ALL_SOURCES_UNHEALTHY,
        source_gaps=source_gaps,
    )


__all__ = [
    "DegradedPreviewDecision",
    "DegradedPreviewPolicy",
    "DegradedPreviewReason",
    "DegradedPreviewRequest",
    "DegradedPreviewState",
]
