"""XSHG calendar adapter backed by the pinned ``exchange_calendars`` release."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Final, Protocol, cast
from zoneinfo import ZoneInfo

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.sectors import (
    ExchangeCalendarQuery,
    ExchangeCalendarResult,
    TradingDay,
    TradingDayStatus,
    TradingPhase,
    TradingSessionSegment,
)

_EXCHANGE: Final = "XSHG"
_TIMEZONE: Final = ZoneInfo("Asia/Shanghai")
_CALENDAR_VERSION: Final = "exchange-calendars-4.13.2:XSHG"
_REGULAR_TRADING_MINUTES: Final = 240


class _TimestampLike(Protocol):
    def to_pydatetime(self) -> datetime: ...


class _ExchangeCalendar(Protocol):
    def sessions_in_range(self, start: object, end: object) -> Iterable[object]: ...

    def session_open(self, session: object) -> object: ...

    def session_break_start(self, session: object) -> object: ...

    def session_break_end(self, session: object) -> object: ...

    def session_close(self, session: object) -> object: ...


type CalendarFactory = Callable[[], _ExchangeCalendar]


def _default_calendar_factory() -> _ExchangeCalendar:
    import exchange_calendars

    return cast("_ExchangeCalendar", exchange_calendars.get_calendar(_EXCHANGE))


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif hasattr(value, "to_pydatetime"):
        parsed = cast("_TimestampLike", value).to_pydatetime()
    else:
        raise TypeError("exchange calendar returned an unsupported timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_TIMEZONE)


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return _as_datetime(value).date()


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        return _as_datetime(value)
    except (TypeError, ValueError):
        # pandas represents missing break times as NaT; it must not cross this boundary.
        return None


class ExchangeCalendarsXshgAdapter:
    """Implement ``TradingCalendarPort`` and emit every natural date in the query."""

    def __init__(self, calendar_factory: CalendarFactory = _default_calendar_factory) -> None:
        self._calendar_factory = calendar_factory

    async def get_calendar(self, request: ExchangeCalendarQuery) -> ExchangeCalendarResult:
        if request.exchanges != [_EXCHANGE]:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "XSHG_CALENDAR_ONLY",
            )
        calendar = self._calendar_factory()
        sessions = {
            _as_date(session): session
            for session in calendar.sessions_in_range(request.date_from, request.date_to)
        }
        days: list[TradingDay] = []
        value = request.date_from
        while value <= request.date_to:
            session = sessions.get(value)
            if session is None:
                days.append(
                    TradingDay(
                        schema_version="1.0",
                        exchange=_EXCHANGE,
                        trading_date=value,
                        timezone=str(_TIMEZONE),
                        status=TradingDayStatus.CLOSED,
                        first_seen_at=request.context.as_of,
                        calendar_version=_CALENDAR_VERSION,
                    )
                )
            else:
                segments = self._segments(calendar, session)
                minutes = sum(
                    int((item.ends_at - item.starts_at).total_seconds() // 60)
                    for item in segments
                    if item.phase is TradingPhase.CONTINUOUS
                )
                status = (
                    TradingDayStatus.PARTIAL
                    if minutes < _REGULAR_TRADING_MINUTES
                    else TradingDayStatus.OPEN
                )
                days.append(
                    TradingDay(
                        schema_version="1.0",
                        exchange=_EXCHANGE,
                        trading_date=value,
                        timezone=str(_TIMEZONE),
                        status=status,
                        segments=segments,
                        first_seen_at=request.context.as_of,
                        calendar_version=_CALENDAR_VERSION,
                    )
                )
            value += timedelta(days=1)
        return ExchangeCalendarResult(
            schema_version="1.0",
            days=days,
            as_of=request.context.as_of,
            calendar_version=_CALENDAR_VERSION,
        )

    @staticmethod
    def _segments(
        calendar: _ExchangeCalendar,
        session: object,
    ) -> list[TradingSessionSegment]:
        opens_at = _as_datetime(calendar.session_open(session))
        closes_at = _as_datetime(calendar.session_close(session))
        break_start = _optional_datetime(calendar.session_break_start(session))
        break_end = _optional_datetime(calendar.session_break_end(session))
        if break_start is None or break_end is None:
            return [
                TradingSessionSegment(
                    phase=TradingPhase.CONTINUOUS,
                    starts_at=opens_at,
                    ends_at=closes_at,
                )
            ]
        return [
            TradingSessionSegment(
                phase=TradingPhase.CONTINUOUS,
                starts_at=opens_at,
                ends_at=break_start,
            ),
            TradingSessionSegment(
                phase=TradingPhase.BREAK,
                starts_at=break_start,
                ends_at=break_end,
            ),
            TradingSessionSegment(
                phase=TradingPhase.CONTINUOUS,
                starts_at=break_end,
                ends_at=closes_at,
            ),
        ]


__all__ = ["ExchangeCalendarsXshgAdapter"]
