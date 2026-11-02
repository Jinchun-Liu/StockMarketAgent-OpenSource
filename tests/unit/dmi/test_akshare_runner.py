import json
import sys
from types import SimpleNamespace

import pytest

from stock_assistant.platform.sources.akshare_runner import (
    AKSHARE_SAFE_REASON_CODES,
    AkshareProcessResult,
    AkshareRunnerError,
    AkshareSubprocessRunner,
    akshare_reason_is_retryable,
    normalize_akshare_reason_code,
)
from stock_assistant.platform.sources.akshare_worker import _run


class FakeFrame:
    def __init__(self, rows: list[dict[str, object]], calls: list[str]) -> None:
        self._rows = rows
        self._calls = calls
        self.columns = list(rows[0]) if rows else []

    def head(self, limit: int) -> "FakeFrame":
        self._calls.append(f"head:{limit}")
        return FakeFrame(self._rows[:limit], self._calls)

    def __len__(self) -> int:
        return len(self._rows)

    def itertuples(self, *, index: bool, name: object):
        assert index is False
        assert name is None
        self._calls.append("itertuples")
        for row in self._rows:
            yield tuple(row[column] for column in self.columns)

    def to_dict(self, **_kwargs: object) -> object:
        raise AssertionError("worker must not materialize the full frame with to_dict")


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_runner_accepts_only_allowlisted_functions_and_bounded_rows() -> None:
    calls: list[tuple[str, float, int, int]] = []

    def execute(
        function: str,
        _arguments: object,
        timeout: float,
        max_rows: int,
        max_bytes: int,
    ):
        calls.append((function, timeout, max_rows, max_bytes))
        return AkshareProcessResult(
            returncode=0,
            stdout=json.dumps({"ok": True, "rows": [{"日期": "2026-07-30"}]}),
        )

    runner = AkshareSubprocessRunner(executor=execute, sleep=_no_sleep)

    rows = await runner.run("stock_zh_a_hist", {"symbol": "603986"})

    assert rows == [{"日期": "2026-07-30"}]
    assert calls == [("stock_zh_a_hist", 10.0, 1000, 2 * 1024 * 1024)]
    with pytest.raises(AkshareRunnerError, match="AKSHARE_FUNCTION_NOT_ALLOWED"):
        await runner.run("arbitrary_function", {})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_retries_once_with_fixed_backoff() -> None:
    calls = 0
    sleeps: list[float] = []

    def execute(
        _function: str, _arguments: object, _timeout: float, _max_rows: int, _max_bytes: int
    ):
        nonlocal calls
        calls += 1
        return AkshareProcessResult(returncode=-1, stdout="", timed_out=True)

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    runner = AkshareSubprocessRunner(executor=execute, sleep=sleep)

    with pytest.raises(AkshareRunnerError, match="AKSHARE_TIMEOUT"):
        await runner.run("stock_news_em", {"symbol": "603986"})

    assert calls == 2
    assert sleeps == [0.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason_code",
    (
        "AKSHARE_FORBIDDEN",
        "AKSHARE_CAPTCHA_REQUIRED",
        "AKSHARE_SCHEMA_DRIFT",
        "AKSHARE_HTML_ERROR_PAGE",
    ),
)
async def test_non_retryable_failures_are_never_retried(reason_code: str) -> None:
    calls = 0

    def execute(
        _function: str, _arguments: object, _timeout: float, _max_rows: int, _max_bytes: int
    ):
        nonlocal calls
        calls += 1
        return AkshareProcessResult(
            returncode=2,
            stdout=json.dumps({"ok": False, "error_code": reason_code}),
        )

    runner = AkshareSubprocessRunner(executor=execute, sleep=_no_sleep)

    with pytest.raises(AkshareRunnerError, match=reason_code):
        await runner.run("stock_news_em", {"symbol": "603986"})
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor_error", ("vendor private failure detail", 401))
async def test_unknown_or_non_string_child_error_is_normalized_and_not_retried(
    vendor_error: object,
) -> None:
    calls = 0

    def execute(
        _function: str,
        _arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ):
        nonlocal calls
        calls += 1
        return AkshareProcessResult(
            returncode=2,
            stdout=json.dumps({"ok": False, "error_code": vendor_error}),
        )

    runner = AkshareSubprocessRunner(executor=execute, sleep=_no_sleep)

    with pytest.raises(AkshareRunnerError) as raised:
        await runner.run("stock_news_em", {"symbol": "603986"})

    assert raised.value.reason_code == "AKSHARE_UPSTREAM_ERROR"
    assert str(raised.value) == "AKSHARE_UPSTREAM_ERROR"
    assert raised.value.retryable is False
    assert calls == 1


def test_reason_code_normalization_is_closed_over_exported_vocabulary() -> None:
    assert "AKSHARE_UPSTREAM_ERROR" in AKSHARE_SAFE_REASON_CODES
    assert normalize_akshare_reason_code("AKSHARE_TIMEOUT") == "AKSHARE_TIMEOUT"
    assert normalize_akshare_reason_code("vendor secret") == "AKSHARE_UPSTREAM_ERROR"
    assert normalize_akshare_reason_code(429) == "AKSHARE_UPSTREAM_ERROR"
    assert akshare_reason_is_retryable("AKSHARE_TIMEOUT") is True
    assert akshare_reason_is_retryable("vendor secret") is False


@pytest.mark.asyncio
async def test_three_consecutive_retryable_failures_open_circuit_for_five_minutes() -> None:
    calls = 0
    now = 100.0

    def execute(
        _function: str, _arguments: object, _timeout: float, _max_rows: int, _max_bytes: int
    ):
        nonlocal calls
        calls += 1
        return AkshareProcessResult(
            returncode=2,
            stdout=json.dumps({"ok": False, "error_code": "AKSHARE_RATE_LIMITED"}),
        )

    runner = AkshareSubprocessRunner(
        executor=execute,
        sleep=_no_sleep,
        clock=lambda: now,
    )

    with pytest.raises(AkshareRunnerError, match="AKSHARE_RATE_LIMITED"):
        await runner.run("stock_news_em", {"symbol": "603986"})
    with pytest.raises(AkshareRunnerError, match="AKSHARE_RATE_LIMITED"):
        await runner.run("stock_news_em", {"symbol": "603986"})
    calls_before_open_check = calls
    with pytest.raises(AkshareRunnerError, match="AKSHARE_CIRCUIT_OPEN"):
        await runner.run("stock_news_em", {"symbol": "603986"})

    assert calls_before_open_check == 3
    assert calls == calls_before_open_check


@pytest.mark.asyncio
async def test_empty_nonzero_process_failure_is_retryable_and_bounded() -> None:
    calls = 0

    def execute(
        _function: str, _arguments: object, _timeout: float, _max_rows: int, _max_bytes: int
    ):
        nonlocal calls
        calls += 1
        return AkshareProcessResult(returncode=1, stdout="", stderr="private stderr")

    runner = AkshareSubprocessRunner(executor=execute, sleep=_no_sleep)

    with pytest.raises(AkshareRunnerError, match="AKSHARE_PROCESS_FAILED"):
        await runner.run("stock_news_em", {"symbol": "603986"})
    assert calls == 2


@pytest.mark.asyncio
async def test_row_and_response_size_limits_fail_closed() -> None:
    oversized_rows = AkshareSubprocessRunner(
        executor=lambda *_args: AkshareProcessResult(
            returncode=0,
            stdout=json.dumps({"ok": True, "rows": [{"x": 1}, {"x": 2}]}),
        ),
        max_rows=1,
    )
    with pytest.raises(AkshareRunnerError, match="AKSHARE_ROW_LIMIT_EXCEEDED"):
        await oversized_rows.run("stock_news_em", {})

    oversized_bytes = AkshareSubprocessRunner(
        executor=lambda *_args: AkshareProcessResult(
            returncode=0,
            stdout=json.dumps({"ok": True, "rows": [{"x": "too-large"}]}),
        ),
        max_bytes=10,
    )
    with pytest.raises(AkshareRunnerError, match="AKSHARE_RESPONSE_TOO_LARGE"):
        await oversized_bytes.run("stock_news_em", {})


def test_worker_rejects_row_limit_before_materializing_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    frame = FakeFrame([{"row": index} for index in range(4)], calls)
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_news_em=lambda **_arguments: frame),
    )

    result = _run(
        {
            "function": "stock_news_em",
            "arguments": {"symbol": "603986"},
            "max_rows": 2,
            "max_bytes": 1024,
        }
    )

    assert result == {"ok": False, "error_code": "AKSHARE_ROW_LIMIT_EXCEEDED"}
    assert calls == ["head:3"]


def test_worker_rejects_large_cell_without_full_frame_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    frame = FakeFrame([{"title": "x" * 1000}], calls)
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_news_em=lambda **_arguments: frame),
    )

    result = _run(
        {
            "function": "stock_news_em",
            "arguments": {"symbol": "603986"},
            "max_rows": 2,
            "max_bytes": 64,
        }
    )

    assert result == {"ok": False, "error_code": "AKSHARE_RESPONSE_TOO_LARGE"}
    assert calls == ["head:3", "itertuples"]
