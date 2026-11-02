"""Bounded subprocess runner for the R1 free-source allowlist."""

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

FREE_SOURCE_ENDPOINT_ALLOWLIST: Final = frozenset(
    {
        "baostock_history",
        "official_gov_policy",
        "official_miit_policy",
        "official_sse_disclosures",
        "official_sse_reports",
        "stock_financial_analysis_indicator_em",
        "stock_news_em",
        "stock_zh_a_disclosure_report_cninfo",
        "stock_zh_a_hist",
    }
)
FREE_SOURCE_ENDPOINT_DOMAINS: Final = {
    "baostock_history": "baostock.com",
    "official_gov_policy": "sousuo.www.gov.cn",
    "official_miit_policy": "www.miit.gov.cn",
    "official_sse_disclosures": "www.sse.com.cn",
    "official_sse_reports": "www.sse.com.cn",
    "stock_financial_analysis_indicator_em": "eastmoney.com",
    "stock_news_em": "eastmoney.com",
    "stock_zh_a_disclosure_report_cninfo": "cninfo.com.cn",
    "stock_zh_a_hist": "eastmoney.com",
}
FREE_SOURCE_SAFE_REASON_CODES: Final = frozenset(
    {
        "FREE_SOURCE_CIRCUIT_OPEN",
        "FREE_SOURCE_ENDPOINT_NOT_ALLOWED",
        "FREE_SOURCE_FORBIDDEN",
        "FREE_SOURCE_INVALID_JSON",
        "FREE_SOURCE_NOT_QUALIFIED",
        "FREE_SOURCE_PROCESS_FAILED",
        "FREE_SOURCE_RATE_LIMITED",
        "FREE_SOURCE_RESPONSE_TOO_LARGE",
        "FREE_SOURCE_REQUEST_INVALID",
        "FREE_SOURCE_ROW_LIMIT_EXCEEDED",
        "FREE_SOURCE_SCHEMA_DRIFT",
        "FREE_SOURCE_TIMEOUT",
        "FREE_SOURCE_TARGET_LOCKED",
        "FREE_SOURCE_UPSTREAM_ERROR",
        "FREE_SOURCE_UNAVAILABLE",
    }
)
_RETRYABLE = frozenset(
    {
        "FREE_SOURCE_CIRCUIT_OPEN",
        "FREE_SOURCE_PROCESS_FAILED",
        "FREE_SOURCE_TIMEOUT",
        "FREE_SOURCE_UNAVAILABLE",
    }
)


class FreeSourceRunnerError(RuntimeError):
    """Safe classified failure from a free-source child process."""

    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class FreeSourceProcessResult:
    returncode: int
    stdout: str
    stderr: str = ""
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class FreeSourceRunResult:
    """Bounded child result with optional official conditional-request state."""

    rows: list[dict[str, JsonValue]]
    response_etag: str | None = None
    response_last_modified: str | None = None
    not_modified: bool = False


ProcessExecutor = Callable[
    [str, Mapping[str, JsonValue], float, int, int],
    FreeSourceProcessResult,
]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class FreeSourceSubprocessRunner:
    """Execute only fixed BaoStock/official endpoints with bounded retries."""

    def __init__(
        self,
        *,
        executor: ProcessExecutor | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        timeout_seconds: float = 10.0,
        max_attempts: int = 2,
        backoff_seconds: float = 0.5,
        minimum_interval_seconds: float = 5.0,
        failure_threshold: int = 3,
        reset_seconds: float = 300.0,
        max_rows: int = 1000,
        max_bytes: int = 2 * 1024 * 1024,
        rate_clock: Clock = time.monotonic,
    ) -> None:
        if (
            min(
                timeout_seconds,
                max_attempts,
                minimum_interval_seconds,
                failure_threshold,
                reset_seconds,
                max_rows,
                max_bytes,
            )
            <= 0
        ):
            raise ValueError("free-source runner bounds must be positive")
        self._executor = executor or _execute_subprocess
        self._sleep = sleep
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._minimum_interval_seconds = minimum_interval_seconds
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._rate_clock = rate_clock
        self._consecutive_retryable_failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._domain_locks = {
            domain: asyncio.Lock() for domain in set(FREE_SOURCE_ENDPOINT_DOMAINS.values())
        }
        self._domain_last_request_at: dict[str, float] = {}

    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> FreeSourceRunResult:
        if endpoint_name not in FREE_SOURCE_ENDPOINT_ALLOWLIST:
            raise FreeSourceRunnerError(
                "FREE_SOURCE_ENDPOINT_NOT_ALLOWED",
                retryable=False,
            )
        validate_free_source_arguments(endpoint_name, arguments)
        if self._clock() < self._open_until.get(endpoint_name, 0.0):
            raise FreeSourceRunnerError("FREE_SOURCE_CIRCUIT_OPEN", retryable=True)

        domain = FREE_SOURCE_ENDPOINT_DOMAINS[endpoint_name]
        lock = self._domain_locks[domain]
        async with lock:
            for attempt in range(self._max_attempts):
                await self._respect_domain_interval(domain)
                result = await asyncio.to_thread(
                    self._executor,
                    endpoint_name,
                    arguments,
                    self._timeout_seconds,
                    self._max_rows,
                    self._max_bytes,
                )
                try:
                    decoded = self._decode(result)
                except FreeSourceRunnerError as error:
                    if not error.retryable:
                        self._consecutive_retryable_failures.pop(endpoint_name, None)
                        self._open_until.pop(endpoint_name, None)
                        raise
                    failures = self._consecutive_retryable_failures.get(endpoint_name, 0) + 1
                    self._consecutive_retryable_failures[endpoint_name] = failures
                    if failures >= self._failure_threshold:
                        self._open_until[endpoint_name] = self._clock() + self._reset_seconds
                        raise
                    if attempt + 1 < self._max_attempts:
                        await self._sleep(self._backoff_seconds)
                        continue
                    raise
                self._consecutive_retryable_failures.pop(endpoint_name, None)
                self._open_until.pop(endpoint_name, None)
                return decoded
            raise AssertionError("bounded free-source attempts must terminate")

    async def _respect_domain_interval(self, domain: str) -> None:
        now = self._rate_clock()
        last_request_at = self._domain_last_request_at.get(domain)
        if last_request_at is not None:
            remaining = self._minimum_interval_seconds - (now - last_request_at)
            if remaining > 0:
                await self._sleep(remaining)
                now = self._rate_clock()
        self._domain_last_request_at[domain] = now

    def _decode(self, result: FreeSourceProcessResult) -> FreeSourceRunResult:
        if result.timed_out:
            raise FreeSourceRunnerError("FREE_SOURCE_TIMEOUT", retryable=True)
        if result.returncode != 0 and not result.stdout.strip():
            raise FreeSourceRunnerError("FREE_SOURCE_PROCESS_FAILED", retryable=True)
        if len(result.stdout.encode("utf-8")) > self._max_bytes:
            raise FreeSourceRunnerError("FREE_SOURCE_RESPONSE_TOO_LARGE", retryable=False)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise FreeSourceRunnerError("FREE_SOURCE_INVALID_JSON", retryable=False) from error
        if not isinstance(payload, dict):
            raise FreeSourceRunnerError("FREE_SOURCE_SCHEMA_DRIFT", retryable=False)
        if result.returncode != 0 and payload.get("ok") is True:
            raise FreeSourceRunnerError("FREE_SOURCE_PROCESS_FAILED", retryable=True)
        if payload.get("ok") is not True:
            reason = payload.get("error_code")
            reason_code = (
                reason
                if isinstance(reason, str) and reason in FREE_SOURCE_SAFE_REASON_CODES
                else "FREE_SOURCE_UPSTREAM_ERROR"
            )
            raise FreeSourceRunnerError(reason_code, retryable=reason_code in _RETRYABLE)
        rows = payload.get("rows")
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise FreeSourceRunnerError("FREE_SOURCE_SCHEMA_DRIFT", retryable=False)
        if len(rows) > self._max_rows:
            raise FreeSourceRunnerError("FREE_SOURCE_ROW_LIMIT_EXCEEDED", retryable=False)
        response = payload.get("response")
        if response is None:
            response = {}
        if not isinstance(response, dict) or set(response) - {
            "etag",
            "last_modified",
            "not_modified",
        }:
            raise FreeSourceRunnerError("FREE_SOURCE_SCHEMA_DRIFT", retryable=False)
        etag = response.get("etag")
        last_modified = response.get("last_modified")
        not_modified = response.get("not_modified", False)
        if (
            (
                etag is not None
                and (not isinstance(etag, str) or len(etag) > 500 or "\r" in etag or "\n" in etag)
            )
            or (
                last_modified is not None
                and (
                    not isinstance(last_modified, str)
                    or len(last_modified) > 500
                    or "\r" in last_modified
                    or "\n" in last_modified
                )
            )
            or not isinstance(not_modified, bool)
        ):
            raise FreeSourceRunnerError("FREE_SOURCE_SCHEMA_DRIFT", retryable=False)
        return FreeSourceRunResult(
            rows=cast(list[dict[str, JsonValue]], rows),
            response_etag=etag,
            response_last_modified=last_modified,
            not_modified=not_modified,
        )


def validate_free_source_arguments(
    endpoint_name: str,
    arguments: Mapping[str, JsonValue],
) -> None:
    """Fail closed unless one fixed endpoint is bound to the fixed R1 instrument."""

    if endpoint_name not in FREE_SOURCE_ENDPOINT_ALLOWLIST:
        raise FreeSourceRunnerError("FREE_SOURCE_ENDPOINT_NOT_ALLOWED", retryable=False)
    expected = {
        "baostock_history": ("code", "sh.603986"),
        "stock_zh_a_disclosure_report_cninfo": ("symbol", "603986"),
        "stock_financial_analysis_indicator_em": ("symbol", "603986.SH"),
        "stock_zh_a_hist": ("symbol", "603986"),
        "stock_news_em": ("symbol", "603986"),
        "official_sse_disclosures": ("instrument_id", "XSHG:603986"),
        "official_sse_reports": ("instrument_id", "XSHG:603986"),
        "official_gov_policy": ("instrument_id", "XSHG:603986"),
        "official_miit_policy": ("instrument_id", "XSHG:603986"),
    }[endpoint_name]
    if arguments.get(expected[0]) != expected[1]:
        raise FreeSourceRunnerError("FREE_SOURCE_TARGET_LOCKED", retryable=False)
    allowed_keys = {
        "baostock_history": {"code", "fields", "start_date", "end_date"},
        "stock_zh_a_disclosure_report_cninfo": {
            "symbol",
            "market",
            "category",
            "start_date",
            "end_date",
        },
        "stock_financial_analysis_indicator_em": {"symbol", "start_year"},
        "stock_zh_a_hist": {
            "symbol",
            "period",
            "start_date",
            "end_date",
            "adjust",
            "timeout",
        },
        "stock_news_em": {"symbol"},
        "official_sse_disclosures": {"instrument_id", "since", "until"},
        "official_sse_reports": {"instrument_id", "since", "until"},
        "official_gov_policy": {
            "instrument_id",
            "since",
            "until",
            "if_none_match",
            "if_modified_since",
        },
        "official_miit_policy": {
            "instrument_id",
            "since",
            "until",
            "if_none_match",
            "if_modified_since",
        },
    }[endpoint_name]
    if set(arguments) - allowed_keys:
        raise FreeSourceRunnerError("FREE_SOURCE_REQUEST_INVALID", retryable=False)
    for key in ("if_none_match", "if_modified_since"):
        value = arguments.get(key)
        if value is not None and (
            not isinstance(value, str) or len(value) > 500 or "\r" in value or "\n" in value
        ):
            raise FreeSourceRunnerError("FREE_SOURCE_REQUEST_INVALID", retryable=False)


def _execute_subprocess(
    endpoint_name: str,
    arguments: Mapping[str, JsonValue],
    timeout_seconds: float,
    max_rows: int,
    max_bytes: int,
) -> FreeSourceProcessResult:
    request = json.dumps(
        {
            "endpoint": endpoint_name,
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
            [sys.executable, "-m", "stock_assistant.platform.sources.free_source_worker"],
            input=request,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return FreeSourceProcessResult(returncode=-1, stdout="", timed_out=True)
    stdout = completed.stdout
    if len(stdout.encode("utf-8")) > max_bytes:
        stdout = json.dumps({"ok": False, "error_code": "FREE_SOURCE_RESPONSE_TOO_LARGE"})
    return FreeSourceProcessResult(
        returncode=completed.returncode,
        stdout=stdout,
        stderr=completed.stderr[-1000:],
    )


__all__ = [
    "FREE_SOURCE_ENDPOINT_ALLOWLIST",
    "FREE_SOURCE_ENDPOINT_DOMAINS",
    "FREE_SOURCE_SAFE_REASON_CODES",
    "FreeSourceProcessResult",
    "FreeSourceRunResult",
    "FreeSourceRunnerError",
    "FreeSourceSubprocessRunner",
    "validate_free_source_arguments",
]
