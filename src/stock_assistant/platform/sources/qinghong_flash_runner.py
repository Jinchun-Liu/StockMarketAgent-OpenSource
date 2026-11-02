"""Bounded metadata-only runner for the R1 Qinghong finance flash service."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue, SecretStr

from stock_assistant.platform.sources.free_source_runner import FreeSourceRunnerError

QINGHONG_PROVIDER: Final = "qinghong_finance_flash"
QINGHONG_DOCUMENTATION_URL: Final = "https://my.feishu.cn/wiki/T7XWwCxtIiOcLIkkXQbc5I1Tntc"
QINGHONG_REQUIRED_HOST: Final = "124.223.180.129"
QINGHONG_REQUIRED_PORT: Final = 3220
QINGHONG_SOURCE_IDS: Final = {
    "statutory.announcements": "qinghong_cninfo_flash",
    "company_industry.news": "qinghong_cls_flash",
    "macro.flashes": "qinghong_jin10_flash",
}
QINGHONG_ENDPOINTS: Final = {
    "qinghong_cninfo_flash": "cninfo-latest",
    "qinghong_cls_flash": "cls-telegraph",
    "qinghong_jin10_flash": "jin10",
}

_ENDPOINT_ARGUMENTS: Final = {
    "cninfo-latest": frozenset({"stock"}),
    "cls-telegraph": frozenset(),
    "jin10": frozenset(),
}
_MAX_RESPONSE_BYTES: Final = 512 * 1024
_MAX_ITEMS: Final = 100
_MIN_TIMESTAMP: Final = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_TIMESTAMP: Final = datetime(2100, 1, 1, tzinfo=UTC)


class QinghongFlashRunnerError(FreeSourceRunnerError):
    """Stable provider failure that never exposes upstream text or credentials."""


class _RateLimiter(Protocol):
    async def wait(self, sleep: Callable[[float], Awaitable[None]]) -> None: ...


class HttpxQinghongFlashRunner:
    """Call three fixed single-source feeds and retain only allowlisted metadata."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        client: httpx.AsyncClient,
        limiter: _RateLimiter,
        retry_delays_seconds: tuple[float, ...] = (1.0,),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if len(retry_delays_seconds) > 1 or any(delay < 0 for delay in retry_delays_seconds):
            raise ValueError("Qinghong retry budget is invalid")
        if client.follow_redirects:
            raise ValueError("Qinghong client must reject redirects")
        _validate_secure_base_url(client.base_url)
        self._api_key = api_key
        self._client = client
        self._limiter = limiter
        self._retry_delays_seconds = retry_delays_seconds
        self._sleep = sleep

    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        allowed_arguments = _endpoint_arguments(endpoint_name)
        if set(arguments) != allowed_arguments:
            raise QinghongFlashRunnerError("QINGHONG_REQUEST_INVALID", retryable=False)
        if endpoint_name == "cninfo-latest" and arguments.get("stock") != "603986":
            raise QinghongFlashRunnerError("QINGHONG_TARGET_LOCKED", retryable=False)
        if any(
            not isinstance(value, str)
            or not value
            or len(value) > 32
            or "\r" in value
            or "\n" in value
            for value in arguments.values()
        ):
            raise QinghongFlashRunnerError("QINGHONG_REQUEST_INVALID", retryable=False)
        api_key = self._api_key.get_secret_value()
        if not api_key:
            raise QinghongFlashRunnerError("QINGHONG_AUTH_FAILED", retryable=False)
        params = {"id": endpoint_name}
        for key, value in arguments.items():
            if not isinstance(value, str):
                raise AssertionError("validated Qinghong arguments must be strings")
            params[key] = value
        attempts = len(self._retry_delays_seconds) + 1
        for attempt in range(attempts):
            await self._limiter.wait(self._sleep)
            try:
                async with self._client.stream(
                    "GET",
                    "api/s",
                    params=params,
                    headers={"X-API-Key": api_key, "Accept": "application/json"},
                ) as response:
                    _validate_response_status(response.status_code)
                    body = await _read_bounded_body(response)
                    return _rows_from_body(body, expected_endpoint=endpoint_name)
            except httpx.TimeoutException as error:
                failure = QinghongFlashRunnerError("QINGHONG_TIMEOUT", retryable=True)
                if attempt + 1 == attempts:
                    raise failure from error
            except httpx.TransportError as error:
                failure = QinghongFlashRunnerError(
                    "QINGHONG_UPSTREAM_UNAVAILABLE",
                    retryable=True,
                )
                if attempt + 1 == attempts:
                    raise failure from error
            except QinghongFlashRunnerError as error:
                if not error.retryable or attempt + 1 == attempts:
                    raise
            await self._sleep(self._retry_delays_seconds[attempt])
        raise AssertionError("bounded Qinghong attempts must terminate")


def _validate_secure_base_url(value: httpx.URL) -> None:
    try:
        parsed = urlsplit(str(value))
        port = parsed.port
    except ValueError as error:
        raise ValueError("Qinghong API base URL is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != QINGHONG_REQUIRED_HOST
        or port != QINGHONG_REQUIRED_PORT
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Qinghong API requires the fixed HTTPS origin")


def _endpoint_arguments(endpoint_name: str) -> frozenset[str]:
    try:
        return _ENDPOINT_ARGUMENTS[endpoint_name]
    except KeyError as error:
        raise QinghongFlashRunnerError(
            "QINGHONG_ENDPOINT_NOT_ALLOWED",
            retryable=False,
        ) from error


def _validate_response_status(status_code: int) -> None:
    if status_code in {401, 403}:
        raise QinghongFlashRunnerError("QINGHONG_AUTH_FAILED", retryable=False)
    if status_code == 429:
        raise QinghongFlashRunnerError("QINGHONG_RATE_LIMITED", retryable=True)
    if 500 <= status_code < 600:
        raise QinghongFlashRunnerError("QINGHONG_UPSTREAM_UNAVAILABLE", retryable=True)
    if status_code != 200:
        raise QinghongFlashRunnerError("QINGHONG_REQUEST_REJECTED", retryable=False)


async def _read_bounded_body(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise QinghongFlashRunnerError(
                "QINGHONG_RESPONSE_TOO_LARGE",
                retryable=False,
            )
        body.extend(chunk)
    return bytes(body)


def _rows_from_body(
    body: bytes,
    *,
    expected_endpoint: str,
) -> list[dict[str, JsonValue]]:
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, ValueError) as error:
        raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False) from error
    if not isinstance(document, Mapping):
        raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False)
    status = document.get("status")
    updated_time = document.get("updatedTime")
    items = document.get("items")
    if (
        status not in {"success", "cache"}
        or document.get("id") != expected_endpoint
        or not isinstance(items, list)
        or len(items) > _MAX_ITEMS
    ):
        raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False)
    _timestamp_to_iso(updated_time)
    rows: list[dict[str, JsonValue]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False)
        item_id = item.get("id")
        title = item.get("title")
        url = item.get("url")
        if (
            not isinstance(item_id, str)
            or not item_id
            or len(item_id) > 500
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 2_000
            or not isinstance(url, str)
            or len(url) > 2_000
        ):
            raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False)
        published_at = _timestamp_to_iso(item.get("pubDate", updated_time))
        rows.append(
            {
                "platform_id": expected_endpoint,
                "item_id": item_id,
                "title": title.strip(),
                "published_at": published_at,
            }
        )
    return rows


def _timestamp_to_iso(value: object) -> str:
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            raw_timestamp = float(value)
            if abs(raw_timestamp) >= 100_000_000_000:
                raw_timestamp /= 1_000
            parsed = datetime.fromtimestamp(raw_timestamp, tz=UTC)
        elif isinstance(value, str) and value.strip():
            stripped = value.strip()
            if stripped.isdigit():
                return _timestamp_to_iso(int(stripped))
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timestamp must include timezone")
            parsed = parsed.astimezone(UTC)
        else:
            raise ValueError("timestamp is missing")
    except (OSError, OverflowError, ValueError) as error:
        raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False) from error
    if not _MIN_TIMESTAMP <= parsed < _MAX_TIMESTAMP:
        raise QinghongFlashRunnerError("QINGHONG_SCHEMA_CHANGED", retryable=False)
    return parsed.isoformat()


__all__ = [
    "QINGHONG_DOCUMENTATION_URL",
    "QINGHONG_ENDPOINTS",
    "QINGHONG_PROVIDER",
    "QINGHONG_REQUIRED_HOST",
    "QINGHONG_REQUIRED_PORT",
    "QINGHONG_SOURCE_IDS",
    "HttpxQinghongFlashRunner",
    "QinghongFlashRunnerError",
]
