"""Bounded HTTP runner for the R1-R6 Tushare-compatible research service."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from time import monotonic
from typing import Final, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.platform.sources.free_source_runner import FreeSourceRunnerError

QUANTDATA_BASE_URL: Final = "https://quantdata888.duckdns.org"
PROMAX_BASE_URL: Final = "https://pcd.mobcvb.cn/tushare/pro/"
QUANTDATA_PROVIDER: Final = "quantdata888_tushare_compatible"
PROMAX_PROVIDER: Final = "promax_tushare_compatible"
# Compatibility export for the market route and existing operator tooling.
RESEARCH_PREVIEW_BASE_URL: Final = QUANTDATA_BASE_URL
RESEARCH_PREVIEW_PROVIDER: Final = QUANTDATA_PROVIDER
RESEARCH_PREVIEW_CONNECT_TIMEOUT_SECONDS: Final = 5.0
RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS: Final = 30.0
RESEARCH_PREVIEW_TOTAL_TIMEOUT_SECONDS: Final = 90.0
RESEARCH_PREVIEW_SOURCE_IDS: Final = {
    "statutory.announcements": "research_preview_announcements",
    "financial.changes": "research_preview_financials",
    "market.snapshot": "research_preview_market",
    "company_industry.news": "research_preview_company_news",
    "macro.flashes": "research_preview_macro",
}
RESEARCH_PREVIEW_PROVIDER_BY_CAPABILITY: Final = {
    "statutory.announcements": PROMAX_PROVIDER,
    "financial.changes": PROMAX_PROVIDER,
    "market.snapshot": QUANTDATA_PROVIDER,
    "company_industry.news": PROMAX_PROVIDER,
    "macro.flashes": PROMAX_PROVIDER,
}

_ENDPOINTS: Final = {
    "anns_d": (
        "ann_date,ts_code,name,title,url,rec_time",
        frozenset({"ts_code", "start_date", "end_date"}),
    ),
    "income": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
        "basic_eps,diluted_eps,total_revenue,revenue,operate_profit,"
        "total_profit,n_income,n_income_attr_p",
        frozenset({"ts_code", "start_date", "end_date"}),
    ),
    "daily": (
        "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
        frozenset({"ts_code", "start_date", "end_date"}),
    ),
    "news": (
        "datetime,title,channels",
        frozenset({"src", "start_date", "end_date"}),
    ),
    "npr": (
        "pubtime,title,url,pcode,puborg,ptype",
        frozenset({"ptype", "start_date", "end_date"}),
    ),
}
_MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
_MAX_ROWS: Final = 2_000
_MAX_DISCOVERY_SEGMENTS: Final = 8
_DISCOVERY_DATETIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
_DISCOVERY_CHUNK_SPANS: Final = {
    "news": timedelta(hours=1),
    "npr": timedelta(days=5),
}
_ENDPOINT_PAGE_LIMITS: Final = {
    "anns_d": 2_000,
    "income": 100,
    "news": 1_500,
    "npr": 500,
}
_PROMAX_FIXED_LIMITS: Final = {
    "anns_d": "50",
    "news": "100",
    "npr": "100",
}
_PROMAX_FIELDS_ENDPOINTS: Final = frozenset({"income", "news", "npr"})
_ACCOUNT_QUOTA_LOCK_ID: Final = 1_916_037_986
_ACCOUNT_QUOTA_INTERVAL_SECONDS: Final = 2.0


class ResearchPreviewRunnerError(FreeSourceRunnerError):
    """Stable provider failure that never exposes upstream text or credentials."""


class _RateLimiter(Protocol):
    async def wait(self, sleep: Callable[[float], Awaitable[None]]) -> None: ...


class AccountRateLimiter:
    """Reserve request slots across all runner instances in one process."""

    def __init__(
        self,
        *,
        minimum_interval_seconds: float = 2.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        # One shared account slot every two seconds enforces the project's
        # conservative 30 requests/minute ceiling across all five capabilities.
        if minimum_interval_seconds < 2.0:
            raise ValueError("research preview interval must preserve the account quota")
        self._minimum_interval_seconds = minimum_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    def reserve_delay(self) -> float:
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_allowed_at)
            self._next_allowed_at = slot + self._minimum_interval_seconds
            return slot - now

    async def wait(self, sleep: Callable[[float], Awaitable[None]]) -> None:
        delay = self.reserve_delay()
        if delay > 0:
            await sleep(delay)


class PostgresAccountRateLimiter:
    """Serialize account request starts across worker processes through PostgreSQL."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        lock_id: int = _ACCOUNT_QUOTA_LOCK_ID,
        minimum_interval_seconds: float = _ACCOUNT_QUOTA_INTERVAL_SECONDS,
    ) -> None:
        if minimum_interval_seconds < 2.0:
            raise ValueError("research preview interval must preserve the account quota")
        self._engine = engine
        self._lock_id = lock_id
        self._minimum_interval_seconds = minimum_interval_seconds

    async def wait(self, _sleep: Callable[[float], Awaitable[None]]) -> None:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(
                    text("SELECT pg_advisory_lock(:lock_id)"),
                    {"lock_id": self._lock_id},
                )
                try:
                    await connection.execute(
                        text(
                            "SELECT pg_sleep(CAST(:minimum_interval_seconds AS double precision))"
                        ),
                        {"minimum_interval_seconds": self._minimum_interval_seconds},
                    )
                finally:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": self._lock_id},
                    )
        except SQLAlchemyError as error:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_RATE_LIMITED",
                retryable=True,
            ) from error


_SHARED_LIMITER = AccountRateLimiter()


class HttpxResearchPreviewRunner:
    """Call only five fixed provider operations with bounded retries and output."""

    def __init__(
        self,
        *,
        token: SecretStr,
        client: httpx.AsyncClient,
        provider: Literal[
            "quantdata888_tushare_compatible",
            "promax_tushare_compatible",
        ] = QUANTDATA_PROVIDER,
        limiter: _RateLimiter = _SHARED_LIMITER,
        retry_delays_seconds: tuple[float, ...] = (1.0,),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        total_timeout_seconds: float = RESEARCH_PREVIEW_TOTAL_TIMEOUT_SECONDS,
    ) -> None:
        if len(retry_delays_seconds) > 1 or any(delay < 0 for delay in retry_delays_seconds):
            raise ValueError("research preview retry budget is invalid")
        if not 0 < total_timeout_seconds <= 240:
            raise ValueError("research preview total timeout is invalid")
        expected_base_url = (
            QUANTDATA_BASE_URL if provider == QUANTDATA_PROVIDER else PROMAX_BASE_URL
        )
        if client.follow_redirects or not _is_exact_base_url(
            str(client.base_url),
            expected=expected_base_url,
        ):
            raise ValueError("research preview client must use the fixed provider origin")
        self._token = token
        self._client = client
        self._provider = provider
        self._limiter = limiter
        self._retry_delays_seconds = retry_delays_seconds
        self._sleep = sleep
        self._total_timeout_seconds = total_timeout_seconds

    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        fields, allowed_arguments = _endpoint_contract(endpoint_name)
        if set(arguments) != allowed_arguments:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_REQUEST_INVALID",
                retryable=False,
            )
        _validate_fixed_arguments(endpoint_name, arguments)
        token = self._token.get_secret_value()
        if not token:
            raise ResearchPreviewRunnerError("RESEARCH_DATA_AUTH_FAILED", retryable=False)
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                request_batches = _request_argument_batches(endpoint_name, arguments)
                maximum_rows = len(request_batches) * _ENDPOINT_PAGE_LIMITS.get(
                    endpoint_name,
                    _MAX_ROWS,
                )
                rows: list[dict[str, JsonValue]] = []
                seen_rows: set[str] = set()
                for request_arguments in request_batches:
                    for row in await self._run_request(
                        endpoint_name=endpoint_name,
                        fields=fields,
                        arguments=request_arguments,
                        token=token,
                    ):
                        identity = json.dumps(
                            row,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        if identity in seen_rows:
                            continue
                        seen_rows.add(identity)
                        rows.append(row)
                        if len(rows) > maximum_rows:
                            raise ResearchPreviewRunnerError(
                                "RESEARCH_DATA_RESULT_TRUNCATED",
                                retryable=False,
                            )
                return rows
        except TimeoutError as error:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_TIMEOUT",
                retryable=True,
            ) from error

    async def _run_request(
        self,
        *,
        endpoint_name: str,
        fields: str,
        arguments: Mapping[str, JsonValue],
        token: str,
    ) -> list[dict[str, JsonValue]]:
        attempts = len(self._retry_delays_seconds) + 1
        for attempt in range(attempts):
            await self._limiter.wait(self._sleep)
            try:
                async with self._request_stream(
                    endpoint_name=endpoint_name,
                    fields=fields,
                    arguments=arguments,
                    credential=token,
                ) as response:
                    _validate_response_status(response.status_code)
                    body = await _read_bounded_body(response)
                    return _rows_from_body(
                        body,
                        endpoint_name=endpoint_name,
                        page_limit=_effective_page_limit(
                            endpoint_name=endpoint_name,
                            provider=self._provider,
                        ),
                    )
            except httpx.TimeoutException as error:
                failure = ResearchPreviewRunnerError(
                    "RESEARCH_DATA_TIMEOUT",
                    retryable=True,
                )
                if attempt + 1 == attempts:
                    raise failure from error
            except httpx.TransportError as error:
                failure = ResearchPreviewRunnerError(
                    "RESEARCH_DATA_UPSTREAM_UNAVAILABLE",
                    retryable=True,
                )
                if attempt + 1 == attempts:
                    raise failure from error
            except ResearchPreviewRunnerError as error:
                if not error.retryable or attempt + 1 == attempts:
                    raise
            await self._sleep(self._retry_delays_seconds[attempt])
        raise AssertionError("bounded research preview attempts must terminate")

    @asynccontextmanager
    async def _request_stream(
        self,
        *,
        endpoint_name: str,
        fields: str,
        arguments: Mapping[str, JsonValue],
        credential: str,
    ) -> AsyncIterator[httpx.Response]:
        if self._provider == QUANTDATA_PROVIDER:
            stream = self._client.stream(
                "POST",
                "",
                json={
                    "api_name": endpoint_name,
                    "token": credential,
                    "params": dict(arguments),
                    "fields": fields,
                },
                headers={"Accept-Encoding": "gzip"},
            )
        else:
            # ProMax exposes one generic GET relay, but its upstream operations do
            # not share one request shape. Announcements require an explicit
            # bounded page size, while financial/news/policy operations need the
            # Tushare fields projection to keep their response schema and payload
            # bounded. These transport-only controls never widen business input.
            query_params: dict[str, str] = {}
            for key, value in arguments.items():
                if not isinstance(value, str):
                    raise ResearchPreviewRunnerError(
                        "RESEARCH_DATA_REQUEST_INVALID",
                        retryable=False,
                    )
                query_params[key] = value
            fixed_limit = _PROMAX_FIXED_LIMITS.get(endpoint_name)
            if fixed_limit is not None:
                query_params["limit"] = fixed_limit
            if endpoint_name in _PROMAX_FIELDS_ENDPOINTS:
                query_params["fields"] = fields
            stream = self._client.stream(
                "GET",
                endpoint_name,
                params=query_params,
                headers={
                    "Accept-Encoding": "gzip",
                    "X-API-Key": credential,
                },
            )
        async with stream as response:
            yield response


def _is_exact_base_url(value: str, *, expected: str) -> bool:
    parsed = urlsplit(value)
    expected_parsed = urlsplit(expected)
    return (
        parsed.scheme == "https"
        and parsed.hostname == expected_parsed.hostname
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path.rstrip("/") == expected_parsed.path.rstrip("/")
    )


def _endpoint_contract(endpoint_name: str) -> tuple[str, frozenset[str]]:
    try:
        return _ENDPOINTS[endpoint_name]
    except KeyError as error:
        raise ResearchPreviewRunnerError(
            "RESEARCH_DATA_ENDPOINT_NOT_ALLOWED",
            retryable=False,
        ) from error


def _validate_fixed_arguments(
    endpoint_name: str,
    arguments: Mapping[str, JsonValue],
) -> None:
    if endpoint_name in {"anns_d", "income", "daily"}:
        if arguments.get("ts_code") != "603986.SH":
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_TARGET_LOCKED",
                retryable=False,
            )
    elif endpoint_name == "news":
        if arguments.get("src") != "eastmoney":
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_REQUEST_INVALID",
                retryable=False,
            )
    elif endpoint_name == "npr" and arguments.get("ptype") != "科技":
        raise ResearchPreviewRunnerError(
            "RESEARCH_DATA_REQUEST_INVALID",
            retryable=False,
        )
    for key, value in arguments.items():
        if not isinstance(value, str) or not value or len(value) > 100:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_REQUEST_INVALID",
                retryable=False,
            )
        if "\r" in value or "\n" in value or key not in _ENDPOINTS[endpoint_name][1]:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_REQUEST_INVALID",
                retryable=False,
            )


def _request_argument_batches(
    endpoint_name: str,
    arguments: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], ...]:
    chunk_span = _DISCOVERY_CHUNK_SPANS.get(endpoint_name)
    if chunk_span is None:
        return (dict(arguments),)
    raw_start = arguments.get("start_date")
    raw_end = arguments.get("end_date")
    if not isinstance(raw_start, str) or not isinstance(raw_end, str):
        raise ResearchPreviewRunnerError("RESEARCH_DATA_REQUEST_INVALID", retryable=False)
    try:
        start = datetime.strptime(raw_start, _DISCOVERY_DATETIME_FORMAT)
        end = datetime.strptime(raw_end, _DISCOVERY_DATETIME_FORMAT)
    except ValueError as error:
        raise ResearchPreviewRunnerError(
            "RESEARCH_DATA_REQUEST_INVALID",
            retryable=False,
        ) from error
    if start > end:
        raise ResearchPreviewRunnerError("RESEARCH_DATA_REQUEST_INVALID", retryable=False)

    batches: list[dict[str, JsonValue]] = []
    segment_start = start
    while True:
        segment_end = min(segment_start + chunk_span, end)
        segment = dict(arguments)
        segment["start_date"] = segment_start.strftime(_DISCOVERY_DATETIME_FORMAT)
        segment["end_date"] = segment_end.strftime(_DISCOVERY_DATETIME_FORMAT)
        batches.append(segment)
        if segment_end == end:
            return tuple(batches)
        if len(batches) >= _MAX_DISCOVERY_SEGMENTS:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_REQUEST_INVALID",
                retryable=False,
            )
        segment_start = segment_end


def _validate_response_status(status_code: int) -> None:
    if status_code in {401, 403}:
        raise ResearchPreviewRunnerError("RESEARCH_DATA_AUTH_FAILED", retryable=False)
    if status_code == 429:
        raise ResearchPreviewRunnerError("RESEARCH_DATA_RATE_LIMITED", retryable=True)
    if 500 <= status_code < 600:
        raise ResearchPreviewRunnerError(
            "RESEARCH_DATA_UPSTREAM_UNAVAILABLE",
            retryable=True,
        )
    if status_code != 200:
        raise ResearchPreviewRunnerError("RESEARCH_DATA_REQUEST_REJECTED", retryable=False)


async def _read_bounded_body(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_RESPONSE_TOO_LARGE",
                retryable=False,
            )
        body.extend(chunk)
    return bytes(body)


def _rows_from_body(
    body: bytes,
    *,
    endpoint_name: str,
    page_limit: int,
) -> list[dict[str, JsonValue]]:
    try:
        document = json.loads(body, parse_float=str)
    except (UnicodeDecodeError, ValueError) as error:
        raise ResearchPreviewRunnerError("RESEARCH_DATA_SCHEMA_CHANGED", retryable=False) from error
    if not isinstance(document, Mapping):
        raise ResearchPreviewRunnerError("RESEARCH_DATA_SCHEMA_CHANGED", retryable=False)
    code = document.get("code")
    if code != 0:
        reason = (
            "RESEARCH_DATA_PERMISSION_BLOCKED" if code == 2002 else "RESEARCH_DATA_REQUEST_REJECTED"
        )
        raise ResearchPreviewRunnerError(reason, retryable=False)
    data = document.get("data")
    if not isinstance(data, Mapping):
        raise ResearchPreviewRunnerError("RESEARCH_DATA_SCHEMA_CHANGED", retryable=False)
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not isinstance(items, list):
        raise ResearchPreviewRunnerError("RESEARCH_DATA_SCHEMA_CHANGED", retryable=False)
    if not fields and not items:
        return []
    if (
        not fields
        or len(fields) > 100
        or any(not isinstance(field, str) or not field or len(field) > 100 for field in fields)
        or len(set(fields)) != len(fields)
        or len(items) >= page_limit
    ):
        reason = (
            "RESEARCH_DATA_RESULT_TRUNCATED"
            if isinstance(items, list) and len(items) >= page_limit
            else "RESEARCH_DATA_SCHEMA_CHANGED"
        )
        raise ResearchPreviewRunnerError(reason, retryable=False)
    rows: list[dict[str, JsonValue]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise ResearchPreviewRunnerError("RESEARCH_DATA_SCHEMA_CHANGED", retryable=False)
        rows.append(
            {field: _safe_json_value(value) for field, value in zip(fields, item, strict=True)}
        )
    return rows


def _effective_page_limit(
    *,
    endpoint_name: str,
    provider: Literal[
        "quantdata888_tushare_compatible",
        "promax_tushare_compatible",
    ],
) -> int:
    if provider == PROMAX_PROVIDER:
        fixed_limit = _PROMAX_FIXED_LIMITS.get(endpoint_name)
        if fixed_limit is not None:
            return int(fixed_limit)
    return _ENDPOINT_PAGE_LIMITS.get(endpoint_name, _MAX_ROWS)


def _safe_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ResearchPreviewRunnerError("RESEARCH_DATA_SCHEMA_CHANGED", retryable=False)


__all__ = [
    "PROMAX_BASE_URL",
    "PROMAX_PROVIDER",
    "QUANTDATA_BASE_URL",
    "QUANTDATA_PROVIDER",
    "RESEARCH_PREVIEW_BASE_URL",
    "RESEARCH_PREVIEW_CONNECT_TIMEOUT_SECONDS",
    "RESEARCH_PREVIEW_PROVIDER",
    "RESEARCH_PREVIEW_PROVIDER_BY_CAPABILITY",
    "RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS",
    "RESEARCH_PREVIEW_SOURCE_IDS",
    "RESEARCH_PREVIEW_TOTAL_TIMEOUT_SECONDS",
    "AccountRateLimiter",
    "HttpxResearchPreviewRunner",
    "PostgresAccountRateLimiter",
    "ResearchPreviewRunnerError",
]
