"""Tavily URL-discovery adapter; Tavily output is never treated as evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import HttpUrl, JsonValue, SecretStr, TypeAdapter, ValidationError

from stock_assistant.contracts.discovery import (
    SourceDiscoveryCandidate,
    SourceDiscoveryDiagnostics,
    SourceDiscoveryQuery,
    SourceDiscoveryResult,
    SourceDiscoveryStatus,
)

TAVILY_SEARCH_URL: Final = "https://api.tavily.com/search"
TAVILY_ADAPTER_VERSION: Final = "tavily-url-discovery.v1.4"
_MAX_BODY_BYTES: Final = 1024 * 1024
_MAX_API_KEYS: Final = 10
_REQUEST_DEADLINE_SECONDS: Final = 15.0
_HTTP_URL = TypeAdapter(HttpUrl)
_LOGGER = logging.getLogger(__name__)


class TavilyDiscoveryError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _published_at(value: object) -> datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _api_keys(value: SecretStr) -> tuple[SecretStr, ...]:
    raw = value.get_secret_value().strip()
    if not raw:
        raise TavilyDiscoveryError("TAVILY_API_KEY_MISSING")
    if not raw.startswith("{"):
        return (SecretStr(raw),)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TavilyDiscoveryError("TAVILY_API_KEY_INVALID") from exc
    if not isinstance(document, dict):
        raise TavilyDiscoveryError("TAVILY_API_KEY_INVALID")
    candidates = document.get("api_keys")
    if not isinstance(candidates, list):
        raise TavilyDiscoveryError("TAVILY_API_KEY_INVALID")
    unique: list[SecretStr] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            raise TavilyDiscoveryError("TAVILY_API_KEY_INVALID")
        normalized = candidate.strip()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(SecretStr(normalized))
            if len(unique) > _MAX_API_KEYS:
                raise TavilyDiscoveryError("TAVILY_API_KEY_POOL_TOO_LARGE")
    if not unique:
        raise TavilyDiscoveryError("TAVILY_API_KEY_INVALID")
    return tuple(unique)


class TavilySourceDiscovery:
    """Discover at most five allowlisted URLs with search answers and raw text disabled."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        client: httpx.AsyncClient,
        permit: Callable[[], AbstractAsyncContextManager[None]] | None = None,
    ) -> None:
        self._api_keys = _api_keys(api_key)
        self._client = client
        self._permit = permit

    async def _post_with_key_rotation(
        self,
        *,
        payload: dict[str, JsonValue],
        correlation_id: object,
    ) -> httpx.Response:
        saw_quota_failure = False
        try:
            async with (
                asyncio.timeout(_REQUEST_DEADLINE_SECONDS),
                self._permit() if self._permit is not None else nullcontext(),
            ):
                for slot_index, api_key in enumerate(self._api_keys, start=1):
                    try:
                        response = await self._client.post(
                            TAVILY_SEARCH_URL,
                            headers={
                                "Authorization": f"Bearer {api_key.get_secret_value()}",
                                "Content-Type": "application/json",
                            },
                            json=payload,
                            timeout=None,
                            follow_redirects=False,
                        )
                    except httpx.ProxyError as exc:
                        raise TavilyDiscoveryError("TAVILY_EGRESS_PROXY_REJECTED") from exc
                    except httpx.TransportError as exc:
                        raise TavilyDiscoveryError(
                            "TAVILY_TEMPORARILY_UNAVAILABLE", retryable=True
                        ) from exc

                    failure_code: str | None = None
                    if response.status_code in {429, 432}:
                        saw_quota_failure = True
                        failure_code = "TAVILY_QUOTA_EXHAUSTED"
                    elif response.status_code in {401, 403}:
                        failure_code = "TAVILY_AUTHENTICATION_FAILED"
                    if failure_code is None:
                        return response
                    if slot_index < len(self._api_keys):
                        _LOGGER.warning(
                            "tavily_api_key_rotated correlation_id=%s key_slot=%s failure_code=%s",
                            correlation_id,
                            slot_index,
                            failure_code,
                            extra={
                                "event": "tavily_api_key_rotated",
                                "task_type": "research_web_gap_discovery",
                                "correlation_id": str(correlation_id),
                                "key_slot": slot_index,
                                "failure_code": failure_code,
                            },
                        )
        except TimeoutError as exc:
            raise TavilyDiscoveryError("TAVILY_TEMPORARILY_UNAVAILABLE", retryable=True) from exc

        # Quota wins for a mixed exhausted pool: at least one authenticated slot may
        # become usable after its provider quota window resets.
        if saw_quota_failure:
            raise TavilyDiscoveryError("TAVILY_QUOTA_EXHAUSTED", retryable=True)
        raise TavilyDiscoveryError("TAVILY_AUTHENTICATION_FAILED")

    async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
        payload: dict[str, JsonValue] = {
            "query": query.query,
            "search_depth": "advanced",
            "max_results": query.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_domains": cast(list[JsonValue], query.include_domains),
            "end_date": query.end_date.date().isoformat(),
        }
        if query.start_date is not None:
            payload["start_date"] = query.start_date.date().isoformat()
        request_sha256 = hashlib.sha256(_canonical(payload)).hexdigest()
        response = await self._post_with_key_rotation(
            payload=payload,
            correlation_id=query.context.correlation_id,
        )
        if response.status_code >= 500:
            raise TavilyDiscoveryError("TAVILY_TEMPORARILY_UNAVAILABLE", retryable=True)
        if response.status_code != 200:
            raise TavilyDiscoveryError("TAVILY_REQUEST_REJECTED")
        if len(response.content) > _MAX_BODY_BYTES:
            raise TavilyDiscoveryError("TAVILY_RESPONSE_TOO_LARGE")
        try:
            document = response.json()
        except json.JSONDecodeError as exc:
            raise TavilyDiscoveryError("TAVILY_RESPONSE_INVALID") from exc
        if not isinstance(document, dict) or not isinstance(document.get("results"), list):
            raise TavilyDiscoveryError("TAVILY_RESPONSE_INVALID")
        candidates: list[SourceDiscoveryCandidate] = []
        diagnostics = SourceDiscoveryDiagnostics(raw_count=len(document["results"]))
        allowed_domains = set(query.include_domains)
        for raw in cast(list[object], document["results"]):
            if not isinstance(raw, dict):
                raise TavilyDiscoveryError("TAVILY_RESPONSE_INVALID")
            url = raw.get("url")
            title = raw.get("title")
            score = raw.get("score")
            if not isinstance(url, str) or not isinstance(title, str):
                raise TavilyDiscoveryError("TAVILY_RESPONSE_INVALID")
            try:
                validated_url = _HTTP_URL.validate_python(url)
                parsed = urlsplit(str(validated_url))
                domain = (parsed.hostname or "").casefold()
                if (
                    parsed.scheme == "http"
                    and domain in allowed_domains
                    and parsed.port in {None, 80}
                    and parsed.username is None
                    and parsed.password is None
                ):
                    # A search URL is only a hint. Request the HTTPS resource
                    # through the governed connector; never fetch the HTTP URL
                    # or assume the two schemes serve identical content.
                    validated_url = _HTTP_URL.validate_python(
                        urlunsplit(("https", domain, parsed.path, parsed.query, parsed.fragment))
                    )
                    parsed = urlsplit(str(validated_url))
                if parsed.scheme != "https":
                    diagnostics.protocol_filtered += 1
                    continue
                if domain not in allowed_domains:
                    diagnostics.domain_filtered += 1
                    continue
                if isinstance(score, bool) or not isinstance(score, int | float | str):
                    raise TavilyDiscoveryError("TAVILY_RESPONSE_INVALID")
                published_at = _published_at(raw.get("published_date"))
                if published_at is not None and published_at > query.context.as_of:
                    diagnostics.date_filtered += 1
                    continue
                candidate = SourceDiscoveryCandidate(
                    schema_version="1.1",
                    url=validated_url,
                    title=title,
                    domain=domain,
                    published_at=published_at,
                    discovery_score=Decimal(str(score)),
                )
            except (InvalidOperation, ValidationError, ValueError) as exc:
                raise TavilyDiscoveryError("TAVILY_RESPONSE_INVALID") from exc
            if len(candidates) < query.max_results:
                candidates.append(candidate)
        diagnostics.selected_count = len(candidates)
        result = SourceDiscoveryResult(
            schema_version="1.3",
            provider="tavily",
            status=(
                SourceDiscoveryStatus.SUCCEEDED if candidates else SourceDiscoveryStatus.NO_RESULTS
            ),
            request_sha256=request_sha256,
            candidates=candidates,
            searched_at=datetime.now(UTC),
            credits_used=2,
            diagnostics=diagnostics,
        )
        _LOGGER.info(
            "research_web_discovery_completed task_type=research_web_gap_discovery "
            "correlation_id=%s status=%s candidate_count=%s credits_used=%s "
            "raw_count=%s protocol_filtered=%s domain_filtered=%s date_filtered=%s",
            query.context.correlation_id,
            result.status.value,
            len(result.candidates),
            result.credits_used,
            diagnostics.raw_count,
            diagnostics.protocol_filtered,
            diagnostics.domain_filtered,
            diagnostics.date_filtered,
            extra={
                "event": "research_web_discovery",
                "task_type": "research_web_gap_discovery",
                "correlation_id": str(query.context.correlation_id),
                "provider": result.provider,
                "adapter_version": TAVILY_ADAPTER_VERSION,
                "status": result.status.value,
                "candidate_count": len(result.candidates),
                "credits_used": result.credits_used,
                **diagnostics.model_dump(),
            },
        )
        return result


__all__ = [
    "TAVILY_ADAPTER_VERSION",
    "TAVILY_SEARCH_URL",
    "TavilyDiscoveryError",
    "TavilySourceDiscovery",
]
