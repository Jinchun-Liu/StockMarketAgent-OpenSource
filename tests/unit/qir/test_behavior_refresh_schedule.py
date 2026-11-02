from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from apps.refresh import main as refresh_main
from apps.refresh.main import (
    _behavior_last_session_window,
    _behavior_refresh_identity,
    _behavior_window,
    _dev_market_bars,
)
from apps.runtime.coordinator import RefreshResult

from stock_assistant.contracts.behavior import PressureMode
from stock_assistant.contracts.sectors import TradingDay, TradingDayStatus

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ((9, 39), None),
        ((9, 40), ((9, 30), (9, 40))),
        ((11, 30), ((11, 20), (11, 30))),
        ((11, 40), None),
        ((13, 0), None),
        ((13, 10), ((13, 0), (13, 10))),
        ((15, 0), ((14, 50), (15, 0))),
        ((15, 10), None),
    ],
)
def test_intraday_uses_only_complete_trading_windows(
    clock: tuple[int, int],
    expected: tuple[tuple[int, int], tuple[int, int]] | None,
) -> None:
    run_at = datetime(2026, 8, 7, *clock, tzinfo=SHANGHAI)

    window = _behavior_window(run_at, mode=PressureMode.INTRADAY_10M)

    if expected is None:
        assert window is None
        return
    assert window is not None
    assert (window[0].hour, window[0].minute) == expected[0]
    assert (window[1].hour, window[1].minute) == expected[1]


def test_post_close_opens_only_at_1510_and_uses_last_complete_window() -> None:
    before = datetime(2026, 8, 7, 15, 9, tzinfo=SHANGHAI)
    ready = datetime(2026, 8, 7, 15, 10, tzinfo=SHANGHAI)

    assert _behavior_window(before, mode=PressureMode.POST_CLOSE) is None
    window = _behavior_window(ready, mode=PressureMode.POST_CLOSE)

    assert window is not None
    assert (window[0].hour, window[0].minute) == (14, 50)
    assert (window[1].hour, window[1].minute) == (15, 0)


def test_development_market_window_is_explicit_and_deterministic() -> None:
    window_start = datetime(2026, 8, 7, 14, 50, tzinfo=SHANGHAI)
    window_end = datetime(2026, 8, 7, 15, 0, tzinfo=SHANGHAI)

    first = _dev_market_bars(window_start=window_start, window_end=window_end)
    second = _dev_market_bars(window_start=window_start, window_end=window_end)

    assert len(first) == 10
    assert first == second
    assert {item.instrument.instrument_id for item in first} == {"XSHG:603986"}
    assert {item.source_id for item in first} == {"dev_only_fixture"}
    assert first[-1].bar.closed_at == window_end


def test_weekend_backfill_uses_post_close_visible_to_latest_premarket_session() -> None:
    run_at = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    days = tuple(
        TradingDay(
            schema_version="1.0",
            exchange="XSHG",
            trading_date=trading_date,
            status=status,
            first_seen_at=run_at,
            calendar_version="weekend-backfill-test",
        )
        for trading_date, status in (
            (date(2026, 8, 6), TradingDayStatus.OPEN),
            (date(2026, 8, 7), TradingDayStatus.OPEN),
            (date(2026, 8, 8), TradingDayStatus.CLOSED),
            (date(2026, 8, 9), TradingDayStatus.CLOSED),
        )
    )

    window = _behavior_last_session_window(run_at, days)

    assert tuple(item.isoformat() for item in window) == (
        "2026-08-06T14:50:00+08:00",
        "2026-08-06T15:00:00+08:00",
    )


def test_weekend_backfill_requires_two_visible_xshg_sessions() -> None:
    run_at = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    only_one = (
        TradingDay(
            schema_version="1.0",
            exchange="XSHG",
            trading_date=date(2026, 8, 7),
            status=TradingDayStatus.OPEN,
            first_seen_at=run_at,
            calendar_version="weekend-backfill-test",
        ),
    )

    with pytest.raises(RuntimeError, match="RECENT_XSHG_BEHAVIOR_WINDOW_UNAVAILABLE"):
        _behavior_last_session_window(run_at, only_one)


def test_weekend_backfill_keeps_actual_request_time_but_freezes_pit_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = (
        datetime(2026, 8, 6, 14, 50, tzinfo=SHANGHAI),
        datetime(2026, 8, 6, 15, 0, tzinfo=SHANGHAI),
    )
    monkeypatch.setattr(
        refresh_main,
        "_latest_premarket_behavior_window",
        AsyncMock(return_value=window),
    )
    execute = AsyncMock(
        return_value=RefreshResult(operation="behavior-last-session"),
    )
    monkeypatch.setattr(refresh_main, "_execute_behavior_refresh", execute)

    result = __import__("asyncio").run(refresh_main._run_behavior_last_session())

    assert result.operation == "behavior-last-session"
    assert execute.await_args.kwargs["mode"] is PressureMode.POST_CLOSE
    assert execute.await_args.kwargs["window"] == window
    assert execute.await_args.kwargs["context_as_of"] == window[1]
    assert execute.await_args.kwargs["run_at"] > window[1]


def test_operator_seed_backfills_behavior_before_source_seed_and_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def behavior() -> RefreshResult:
        calls.append("behavior-last-session")
        return RefreshResult(operation="behavior-last-session")

    async def refresh(operation: str) -> RefreshResult:
        calls.append(operation)
        return RefreshResult(operation=operation, pointer_switched=True)

    monkeypatch.setattr(refresh_main, "_run_behavior_last_session", behavior)
    monkeypatch.setattr(refresh_main, "_run_refresh", refresh)

    result = __import__("asyncio").run(refresh_main._run_seed_last_session())

    assert calls == ["behavior-last-session", "seed-last-session"]
    assert result == RefreshResult(operation="seed-last-session", pointer_switched=True)


def test_weekend_backfill_has_a_disjoint_idempotency_identity() -> None:
    window_end = datetime(2026, 8, 20, 15, 0, tzinfo=SHANGHAI)

    scheduled = _behavior_refresh_identity(
        operation="behavior-post-close",
        mode=PressureMode.POST_CLOSE,
        window_end=window_end,
    )
    backfill = _behavior_refresh_identity(
        operation="behavior-last-session",
        mode=PressureMode.POST_CLOSE,
        window_end=window_end,
    )

    assert scheduled == ("post_close:close-stamped-open-left-v2:2026-08-20T15:00:00+08:00")
    assert backfill == ("backfill:post_close:close-stamped-open-left-v2:2026-08-20T15:00:00+08:00")
    assert backfill != scheduled


@pytest.mark.parametrize(
    ("operation", "maintain_transition_state"),
    [
        ("behavior-last-session", False),
        ("behavior-post-close", True),
    ],
)
def test_operator_backfill_does_not_rewrite_live_transition_state(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    maintain_transition_state: bool,
) -> None:
    run_at = datetime(2026, 8, 25, 9, 30, tzinfo=UTC)
    window = (
        datetime(2026, 8, 24, 14, 50, tzinfo=SHANGHAI),
        datetime(2026, 8, 24, 15, 0, tzinfo=SHANGHAI),
    )

    class FakeEngine:
        async def dispose(self) -> None:
            return None

    class FakeMarketRepository:
        def __init__(self, _engine: FakeEngine) -> None:
            pass

        async def append_revisions(self, _request: object) -> object:
            class Manifest:
                content_hash = "e" * 64

            class Appended:
                manifest = Manifest()

            return Appended()

    execute = AsyncMock(return_value=object())
    monkeypatch.setattr(refresh_main, "_database_url", lambda *, master: "postgresql://test")
    monkeypatch.setattr(refresh_main, "create_async_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr(refresh_main, "PostgresMarketDataRepository", FakeMarketRepository)
    monkeypatch.setattr(refresh_main, "execute_behavior_compute_runtime", execute)

    result = __import__("asyncio").run(
        refresh_main._execute_behavior_refresh(
            operation=operation,
            run_at=run_at,
            mode=PressureMode.POST_CLOSE,
            window=window,
            context_as_of=window[1],
        )
    )

    assert result.status == "SUCCEEDED"
    assert execute.await_args.kwargs["maintain_transition_state"] is maintain_transition_state
