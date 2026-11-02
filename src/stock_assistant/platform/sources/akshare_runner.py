"""Bounded subprocess runner for the development-only AKShare allowlist."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, cast

from pydantic import JsonValue

AKSHARE_FUNCTION_ALLOWLIST: Final = frozenset(
    {
        "stock_zh_a_disclosure_report_cninfo",
        "stock_financial_analysis_indicator_em",
        "stock_news_em",
        "stock_individual_info_em",
        "stock_bid_ask_em",
        "stock_zh_a_hist",
    }
)
AKSHARE_SAFE_REASON_CODES: Final = frozenset(
    {
        "AKSHARE_CAPTCHA_REQUIRED",
        "AKSHARE_CIRCUIT_OPEN",
        "AKSHARE_FORBIDDEN",
        "AKSHARE_FUNCTION_NOT_ALLOWED",
        "AKSHARE_HTML_ERROR_PAGE",
        "AKSHARE_INVALID_JSON",
        "AKSHARE_PROCESS_FAILED",
        "AKSHARE_RATE_LIMITED",
        "AKSHARE_REQUEST_INVALID",
        "AKSHARE_RESPONSE_TOO_LARGE",
        "AKSHARE_ROW_LIMIT_EXCEEDED",
        "AKSHARE_SCHEMA_DRIFT",
        "AKSHARE_TIMEOUT",
        "AKSHARE_UPSTREAM_ERROR",
        "AKSHARE_UPSTREAM_UNAVAILABLE",
    }
)
_AKSHARE_RETRYABLE_REASON_CODES: Final = frozenset(
    {
        "AKSHARE_CIRCUIT_OPEN",
        "AKSHARE_PROCESS_FAILED",
        "AKSHARE_RATE_LIMITED",
        "AKSHARE_TIMEOUT",
        "AKSHARE_UPSTREAM_UNAVAILABLE",
    }
)


def normalize_akshare_reason_code(value: object) -> str:
    """Map arbitrary child/provider error values onto the public safe vocabulary."""

    return (
        value
        if isinstance(value, str) and value in AKSHARE_SAFE_REASON_CODES
        else ("AKSHARE_UPSTREAM_ERROR")
    )


def akshare_reason_is_retryable(value: object) -> bool:
    """Return retryability only after normalizing to the controlled vocabulary."""

    return normalize_akshare_reason_code(value) in _AKSHARE_RETRYABLE_REASON_CODES


class AkshareRunnerError(RuntimeError):
    """Safe, classified error from the bounded development runner."""

    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AkshareProcessResult:
    returncode: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False


ProcessExecutor = Callable[
    [str, Mapping[str, JsonValue], float, int, int],
    AkshareProcessResult,
]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class AkshareSubprocessRunner:
    """Run only registered AKShare functions with timeout, retry, and a circuit breaker."""

    def __init__(
        self,
        *,
        executor: ProcessExecutor | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        timeout_seconds: float = 10.0,
        max_attempts: int = 2,
        backoff_seconds: float = 0.5,
        failure_threshold: int = 3,
        reset_seconds: float = 300.0,
        max_rows: int = 1000,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if (
            min(
                timeout_seconds, max_attempts, failure_threshold, reset_seconds, max_rows, max_bytes
            )
            <= 0
        ):
            raise ValueError("AKShare runner bounds must be positive")
        self._executor = executor or _execute_subprocess
        self._sleep = sleep
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._consecutive_retryable_failures = 0
        self._open_until = 0.0

    async def run(
        self,
        function_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        """Return bounded normalized rows or a stable safe error."""

        if function_name not in AKSHARE_FUNCTION_ALLOWLIST:
            raise AkshareRunnerError("AKSHARE_FUNCTION_NOT_ALLOWED", retryable=False)
        now = self._clock()
        if now < self._open_until:
            raise AkshareRunnerError("AKSHARE_CIRCUIT_OPEN", retryable=True)

        last_error: AkshareRunnerError | None = None
        for attempt in range(self._max_attempts):
            result = await asyncio.to_thread(
                self._executor,
                function_name,
                arguments,
                self._timeout_seconds,
                self._max_rows,
                self._max_bytes,
            )
            try:
                rows = self._decode_result(result)
            except AkshareRunnerError as error:
                last_error = error
                if not error.retryable:
                    self._consecutive_retryable_failures = 0
                    raise
                self._consecutive_retryable_failures += 1
                if self._consecutive_retryable_failures >= self._failure_threshold:
                    self._open_until = self._clock() + self._reset_seconds
                    raise
                if attempt + 1 < self._max_attempts:
                    await self._sleep(self._backoff_seconds)
                    continue
                raise
            self._consecutive_retryable_failures = 0
            self._open_until = 0.0
            return rows
        assert last_error is not None
        raise last_error

    def _decode_result(self, result: AkshareProcessResult) -> list[dict[str, JsonValue]]:
        if result.timed_out:
            raise AkshareRunnerError("AKSHARE_TIMEOUT", retryable=True)
        if result.returncode != 0 and not result.stdout.strip():
            raise AkshareRunnerError("AKSHARE_PROCESS_FAILED", retryable=True)
        if len(result.stdout.encode("utf-8")) > self._max_bytes:
            raise AkshareRunnerError("AKSHARE_RESPONSE_TOO_LARGE", retryable=False)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AkshareRunnerError("AKSHARE_INVALID_JSON", retryable=False) from error
        if not isinstance(payload, dict):
            raise AkshareRunnerError("AKSHARE_SCHEMA_DRIFT", retryable=False)
        if payload.get("ok") is not True:
            reason_code = normalize_akshare_reason_code(payload.get("error_code"))
            raise AkshareRunnerError(
                reason_code,
                retryable=akshare_reason_is_retryable(reason_code),
            )
        rows = payload.get("rows")
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise AkshareRunnerError("AKSHARE_SCHEMA_DRIFT", retryable=False)
        if len(rows) > self._max_rows:
            raise AkshareRunnerError("AKSHARE_ROW_LIMIT_EXCEEDED", retryable=False)
        return cast(list[dict[str, JsonValue]], rows)


def _execute_subprocess(
    function_name: str,
    arguments: Mapping[str, JsonValue],
    timeout_seconds: float,
    max_rows: int,
    max_bytes: int,
) -> AkshareProcessResult:
    request = json.dumps(
        {
            "function": function_name,
            "arguments": arguments,
            "max_rows": max_rows,
            "max_bytes": max_bytes,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "stock_assistant.platform.sources.akshare_worker"],
            input=request,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return AkshareProcessResult(returncode=-1, stdout="", timed_out=True)
    stdout = completed.stdout
    if len(stdout.encode("utf-8")) > max_bytes:
        return AkshareProcessResult(
            returncode=completed.returncode,
            stdout=json.dumps({"ok": False, "error_code": "AKSHARE_RESPONSE_TOO_LARGE"}),
            stderr="",
        )
    return AkshareProcessResult(
        returncode=completed.returncode,
        stdout=stdout,
        stderr=completed.stderr[-1000:],
    )


__all__ = [
    "AKSHARE_FUNCTION_ALLOWLIST",
    "AKSHARE_SAFE_REASON_CODES",
    "AkshareProcessResult",
    "AkshareRunnerError",
    "AkshareSubprocessRunner",
    "akshare_reason_is_retryable",
    "normalize_akshare_reason_code",
]
