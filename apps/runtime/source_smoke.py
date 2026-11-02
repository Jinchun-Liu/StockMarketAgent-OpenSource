"""Operator-only, side-effect-free smoke for the five R1 research interfaces."""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import JsonValue

from stock_assistant.common.errors import ApplicationError
from stock_assistant.config.settings import Settings
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import FetchRequest
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_RIGHTS_HASH,
    FreeLiveAdapter,
)
from stock_assistant.platform.sources.research_preview_runner import (
    PROMAX_BASE_URL,
    PROMAX_PROVIDER,
    QUANTDATA_BASE_URL,
    QUANTDATA_PROVIDER,
    RESEARCH_PREVIEW_CONNECT_TIMEOUT_SECONDS,
    RESEARCH_PREVIEW_PROVIDER_BY_CAPABILITY,
    RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS,
    RESEARCH_PREVIEW_SOURCE_IDS,
    AccountRateLimiter,
    HttpxResearchPreviewRunner,
    ResearchPreviewRunnerError,
)

_QUANTDATA_TOKEN: Final = re.compile(r"^(?:[0-9a-f]{32}|tk_live_[A-Za-z0-9_-]{24,128})$")
_PROMAX_KEY: Final = re.compile(r"^tsr_[A-Za-z0-9_-]{24,128}$")


class _ResearchRunner(Protocol):
    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]: ...


@dataclass(frozen=True, slots=True)
class SourceSmokeEndpointResult:
    """One safe interface verdict without response content or credentials."""

    source_id: str
    capability: str
    endpoint_name: str
    status: Literal["available", "empty", "blocked"]
    rows_received: int
    records_accepted: int
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class SourceSmokeResult:
    """Bounded aggregate emitted by the operator task."""

    operation: Literal["source-smoke"]
    status: Literal["succeeded", "degraded"]
    credential_format_valid: bool
    interfaces: tuple[SourceSmokeEndpointResult, ...]


async def run_source_smoke(
    *,
    settings: Settings,
    now: datetime | None = None,
    runner: _ResearchRunner | None = None,
) -> SourceSmokeResult:
    """Validate credential shape and exercise every fixed R1 provider operation."""

    observed_at = now or datetime.now(UTC)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("source smoke time must be timezone-aware")
    quantdata_token = settings.r1_research_data_api_token
    quantdata_value = quantdata_token.get_secret_value() if quantdata_token is not None else ""
    promax_key = settings.r1_promax_api_key
    promax_value = promax_key.get_secret_value() if promax_key is not None else ""
    blocked_providers = {
        provider: reason
        for provider, reason in (
            (
                QUANTDATA_PROVIDER,
                _credential_reason(
                    quantdata_value,
                    pattern=_QUANTDATA_TOKEN,
                    missing="QUANTDATA_CREDENTIAL_MISSING",
                    invalid="QUANTDATA_CREDENTIAL_FORMAT_INVALID",
                ),
            ),
            (
                PROMAX_PROVIDER,
                _credential_reason(
                    promax_value,
                    pattern=_PROMAX_KEY,
                    missing="PROMAX_CREDENTIAL_MISSING",
                    invalid="PROMAX_CREDENTIAL_FORMAT_INVALID",
                ),
            ),
        )
        if reason is not None
    }
    credential_format_valid = not blocked_providers

    if runner is not None:
        return await _run_interfaces(
            runners={QUANTDATA_PROVIDER: runner, PROMAX_PROVIDER: runner},
            blocked_providers=blocked_providers,
            observed_at=observed_at,
            credential_format_valid=credential_format_valid,
        )

    timeout = httpx.Timeout(
        RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS,
        connect=RESEARCH_PREVIEW_CONNECT_TIMEOUT_SECONDS,
    )
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    runners: dict[str, _ResearchRunner] = {}
    async with AsyncExitStack() as stack:
        if quantdata_token is not None and QUANTDATA_PROVIDER not in blocked_providers:
            quantdata_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=QUANTDATA_BASE_URL,
                    timeout=timeout,
                    limits=limits,
                    follow_redirects=False,
                )
            )
            runners[QUANTDATA_PROVIDER] = HttpxResearchPreviewRunner(
                token=quantdata_token,
                client=quantdata_client,
                provider=QUANTDATA_PROVIDER,
                limiter=AccountRateLimiter(),
            )
        if promax_key is not None and PROMAX_PROVIDER not in blocked_providers:
            promax_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=PROMAX_BASE_URL,
                    timeout=timeout,
                    limits=limits,
                    follow_redirects=False,
                )
            )
            runners[PROMAX_PROVIDER] = HttpxResearchPreviewRunner(
                token=promax_key,
                client=promax_client,
                provider=PROMAX_PROVIDER,
                limiter=AccountRateLimiter(),
            )
        return await _run_interfaces(
            runners=runners,
            blocked_providers=blocked_providers,
            observed_at=observed_at,
            credential_format_valid=credential_format_valid,
        )


async def _run_interfaces(
    *,
    runners: Mapping[str, _ResearchRunner],
    blocked_providers: Mapping[str, str],
    observed_at: datetime,
    credential_format_valid: bool,
) -> SourceSmokeResult:
    results: list[SourceSmokeEndpointResult] = []
    for capability, source_id in RESEARCH_PREVIEW_SOURCE_IDS.items():
        provider = RESEARCH_PREVIEW_PROVIDER_BY_CAPABILITY[capability]
        blocked_reason = blocked_providers.get(provider)
        if blocked_reason is not None:
            results.append(_blocked_result(capability=capability, reason_code=blocked_reason))
            continue
        adapter = FreeLiveAdapter(source_id)
        request = _request(
            source_id=source_id,
            capability=capability,
            observed_at=observed_at,
        )
        try:
            runner = runners[provider]
            rows = await runner.run(adapter.endpoint_name, adapter.function_arguments(request))
            batch = adapter.normalize(
                request,
                rows,
                execution_started_at=observed_at,
                execution_completed_at=observed_at,
            )
        except ResearchPreviewRunnerError as error:
            results.append(
                _blocked_result(
                    capability=capability,
                    reason_code=error.reason_code,
                )
            )
            continue
        except ApplicationError:
            results.append(
                _blocked_result(
                    capability=capability,
                    reason_code="RESEARCH_DATA_SCHEMA_INVALID",
                )
            )
            continue
        status: Literal["available", "empty"] = "available" if rows else "empty"
        results.append(
            SourceSmokeEndpointResult(
                source_id=source_id,
                capability=capability,
                endpoint_name=adapter.endpoint_name,
                status=status,
                rows_received=len(rows),
                records_accepted=len(batch.records),
            )
        )
    interfaces = tuple(results)
    return SourceSmokeResult(
        operation="source-smoke",
        status=(
            "succeeded"
            if all(item.status in {"available", "empty"} for item in interfaces)
            else "degraded"
        ),
        credential_format_valid=credential_format_valid,
        interfaces=interfaces,
    )


def _credential_reason(
    value: str,
    *,
    pattern: re.Pattern[str],
    missing: str,
    invalid: str,
) -> str | None:
    if not value:
        return missing
    if pattern.fullmatch(value) is None:
        return invalid
    return None


def _request(
    *,
    source_id: str,
    capability: str,
    observed_at: datetime,
) -> FetchRequest:
    identity = f"source-smoke:{source_id}:{observed_at.isoformat()}"
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            request_id=uuid5(NAMESPACE_URL, f"request:{identity}"),
            correlation_id=uuid5(NAMESPACE_URL, identity),
            actor_id="continuous-preview-source-smoke",
            workspace_id="default",
            as_of=observed_at,
            requested_at=observed_at,
            locale="zh-CN",
            timezone="Asia/Shanghai",
        ),
        source_id=source_id,
        capability=capability,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        since=observed_at - timedelta(days=30),
        until=observed_at,
        instrument_ids=["XSHG:603986"],
        limit=100,
    )


def _blocked_result(*, capability: str, reason_code: str) -> SourceSmokeEndpointResult:
    source_id = RESEARCH_PREVIEW_SOURCE_IDS[capability]
    adapter = FreeLiveAdapter(source_id)
    return SourceSmokeEndpointResult(
        source_id=source_id,
        capability=capability,
        endpoint_name=adapter.endpoint_name,
        status="blocked",
        rows_received=0,
        records_accepted=0,
        reason_code=reason_code,
    )


__all__ = [
    "SourceSmokeEndpointResult",
    "SourceSmokeResult",
    "run_source_smoke",
]
