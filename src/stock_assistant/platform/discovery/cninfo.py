"""Code-bound public CNINFO annual-report discovery; index rows are never facts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final, cast
from uuid import uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from stock_assistant.contracts.discovery import (
    SourceDiscoveryCandidate,
    SourceDiscoveryDiagnostics,
    SourceDiscoveryQuery,
    SourceDiscoveryResult,
    SourceDiscoveryStatus,
)
from stock_assistant.contracts.formal_disclosure import (
    FormalDocumentIdentity,
    FormalDocumentLocation,
    OfficialDocumentIndexBinding,
    PublicationDateEvidence,
)
from stock_assistant.platform.sources.governed_web import (
    AddressResolver,
    Clock,
    GovernedWebSourceConnector,
    GovernedWebSourceError,
    _default_resolver,
    _utc_now,
)

CNINFO_DISCOVERY_VERSION: Final = "cninfo-annual-discovery.v1.0"
_DOMAINS: Final = frozenset({"www.cninfo.com.cn", "static.cninfo.com.cn"})
_BASE: Final = "https://www.cninfo.com.cn"
_LOGGER = logging.getLogger(__name__)


class _Stock(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    code: str = Field(pattern=r"^\d{6}$")
    orgId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    zwjc: str = Field(min_length=1, max_length=100)


class _Announcement(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    secCode: str
    secName: str
    announcementTitle: str = Field(min_length=1, max_length=400)
    adjunctUrl: str = Field(pattern=r"^finalpage/\d{4}-\d{2}-\d{2}/\d{1,30}\.[Pp][Dd][Ff]$")
    announcementTime: int = Field(ge=0, le=253402300799000)


def _rows(response: httpx.Response, key: str) -> list[object]:
    document: object = json.loads(response.content)
    if not isinstance(document, dict):
        raise ValueError("invalid public index")
    rows = document.get(key)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("invalid public index rows")
    return cast(list[object], rows)


class CninfoSourceDiscovery(GovernedWebSourceConnector):
    """Reuse the governed transport with the session's fixed public company identity."""

    def __init__(
        self,
        *,
        instrument_id: str,
        instrument_name: str,
        client: httpx.AsyncClient,
        allowed_domains: set[str],
        resolver: AddressResolver = _default_resolver,
        clock: Clock = _utc_now,
    ) -> None:
        super().__init__(
            client=client, allowed_domains=allowed_domains, resolver=resolver, clock=clock
        )
        self._instrument_id = instrument_id
        self._instrument_name = instrument_name

    async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
        now = self._clock()
        result = SourceDiscoveryResult(
            schema_version="1.3",
            provider="cninfo_annual_index",
            status=SourceDiscoveryStatus.NO_RESULTS,
            searched_at=now,
            request_sha256=hashlib.sha256(
                json.dumps(
                    {
                        "adapter": CNINFO_DISCOVERY_VERSION,
                        "instrument_id": self._instrument_id,
                        "instrument_name": self._instrument_name,
                        "query": query.model_dump(mode="json"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
        if (
            not _DOMAINS.issubset(query.include_domains)
            or not _DOMAINS.issubset(self._allowed_domains)
            or re.fullmatch(r"(?:XSHG|XSHE|XBSE):\d{6}", self._instrument_id) is None
            or query.end_date > now
            or query.end_date > query.context.as_of
        ):
            return result
        seconds = 45.0
        if query.context.deadline_at is not None:
            seconds = min(seconds, (query.context.deadline_at - now).total_seconds())
        if seconds <= 0:
            return result.model_copy(update={"status": SourceDiscoveryStatus.FAILED})
        try:
            async with asyncio.timeout(seconds):
                candidates, diagnostics = await self._discover(query)
        except (
            GovernedWebSourceError,
            httpx.RequestError,
            ValueError,
            OverflowError,
            TimeoutError,
        ) as error:
            _LOGGER.info(
                "cninfo_discovery_failed correlation_id=%s failure_kind=%s code=%s",
                query.context.correlation_id,
                type(error).__name__,
                error.code
                if isinstance(error, GovernedWebSourceError)
                else "CNINFO_INDEX_UNAVAILABLE",
            )
            return result.model_copy(
                update={
                    "status": SourceDiscoveryStatus.FAILED,
                    "retryable": isinstance(error, (httpx.TimeoutException, TimeoutError)),
                    "robots_origin_stop": error.robots_origin_stop
                    if isinstance(error, GovernedWebSourceError)
                    else None,
                }
            )
        return result.model_copy(
            update={
                "status": SourceDiscoveryStatus.SUCCEEDED
                if candidates
                else SourceDiscoveryStatus.NO_RESULTS,
                "candidates": candidates,
                "diagnostics": diagnostics,
            }
        )

    async def _discover(
        self, query: SourceDiscoveryQuery
    ) -> tuple[list[SourceDiscoveryCandidate], SourceDiscoveryDiagnostics]:
        code = self._instrument_id.split(":", 1)[1]
        listing = await self._public_response(
            _BASE + "/new/data/szse_stock.json",
            correlation_id=query.context.correlation_id,
            accept="application/json",
            limit=2 * 1024 * 1024,
        )
        matches: list[_Stock] = []
        for row in _rows(listing, "stockList"):
            if not isinstance(row, dict) or row.get("code") != code:
                continue
            matches.append(_Stock.model_validate(row))
        if len(matches) != 1 or matches[0].zwjc != self._instrument_name:
            return [], SourceDiscoveryDiagnostics(raw_count=0)
        start = query.start_date or datetime(max(1970, query.end_date.year - 2), 1, 1, tzinfo=UTC)
        response = await self._public_response(
            _BASE + "/new/hisAnnouncement/query",
            correlation_id=query.context.correlation_id,
            accept="application/json",
            limit=1024 * 1024,
            form={
                "pageNum": "1",
                "pageSize": str(query.max_results),
                "column": "szse",
                "tabName": "fulltext",
                "plate": "",
                "stock": code + "," + matches[0].orgId,
                "searchkey": "",
                "secid": "",
                "category": "category_ndbg_szsh",
                "trade": "",
                "seDate": start.strftime("%Y-%m-%d") + "~" + query.end_date.strftime("%Y-%m-%d"),
                "sortName": "time",
                "sortType": "desc",
                "isHLtitle": "false",
            },
        )
        rows = _rows(response, "announcements")
        diagnostics = SourceDiscoveryDiagnostics(raw_count=len(rows))
        candidates: list[SourceDiscoveryCandidate] = []
        seen: set[str] = set()
        for raw in rows[: query.max_results]:
            try:
                row = _Announcement.model_validate(raw)
            except ValidationError:
                continue
            if row.secCode != code or row.secName != self._instrument_name:
                continue
            published = datetime.fromtimestamp(row.announcementTime / 1000, UTC)
            if not start <= published <= query.end_date:
                diagnostics.date_filtered += 1
                continue
            url = "https://static.cninfo.com.cn/" + row.adjunctUrl
            if url in seen:
                continue
            seen.add(url)
            row_hash = hashlib.sha256(row.model_dump_json().encode()).hexdigest()
            receipt_id = uuid5(query.context.request_id, row_hash)
            binding = OfficialDocumentIndexBinding(
                identity=FormalDocumentIdentity(
                    document_kind="annual",
                    issuer_name=self._instrument_name,
                    instrument_id=self._instrument_id,
                    title=row.announcementTitle,
                    native_document_id=row.adjunctUrl.rsplit("/", 1)[-1],
                    canonical_url=url,
                    binding_basis="official_index",
                    identity_anchors=[FormalDocumentLocation(field="secCode/secName")],
                ),
                publication=PublicationDateEvidence(
                    kind="official_index",
                    raw_value=str(row.announcementTime),
                    published_at=published,
                    precision="instant",
                    timezone="UTC",
                    canonical_url=url,
                    location=FormalDocumentLocation(field="announcementTime"),
                    observation_sha256=row_hash,
                    receipt_id=receipt_id,
                ),
                index_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
                row_id=row.adjunctUrl.rsplit("/", 1)[-1],
                source_id="cninfo",
            )
            candidates.append(
                SourceDiscoveryCandidate.model_validate(
                    {
                        "schema_version": "1.1",
                        "url": url,
                        "title": row.secName + " " + row.announcementTitle,
                        "domain": "static.cninfo.com.cn",
                        "published_at": published,
                        "discovery_score": Decimal("1"),
                        "official_index": binding,
                    }
                )
            )
        diagnostics.selected_count = len(candidates)
        return candidates, diagnostics
