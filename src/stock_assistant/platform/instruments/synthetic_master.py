"""Deterministic, offline A-share master data for the private R2 preview."""

from __future__ import annotations

import gzip
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Literal, cast
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.runtime_resources import runtime_resource_path
from stock_assistant.common.time import utc_now
from stock_assistant.contracts.common import EntityRef, InstrumentRef
from stock_assistant.contracts.instruments import (
    CompanyProfile,
    EntityKind,
    InstrumentLookupRequest,
    InstrumentLookupResult,
    InstrumentProfile,
    InstrumentSearchMatch,
    InstrumentSearchMatchType,
    InstrumentSearchMatchV1_1,
    InstrumentSearchQuery,
    InstrumentSearchResult,
    InstrumentSearchResultV1_1,
    InstrumentType,
    ListingStatus,
    MasterDataQuery,
    MasterDataSnapshot,
    ResolutionStatus,
)

_NAMESPACE = UUID("dc17775a-5bd7-5cbf-9cbb-8f20e9154b99")
_VALID_FROM = datetime(1990, 12, 1, tzinfo=UTC)
_CATALOG_RESOURCE = "instruments/ashare-catalog.dev.v1.json.gz"
_CATALOG_SOURCE_IDS = {
    "sse_official_instrument_directory",
    "szse_official_instrument_directory",
    "bse_official_instrument_directory",
    "szse_official_identity_change_disclosures",
    "sse_official_identity_change_disclosures",
}
_EXPECTED_CANONICAL_COUNTS = {"XSHG": 2_461, "XSHE": 3_084, "XBSE": 339}
_EXPECTED_LISTING_COUNTS = {ListingStatus.ACTIVE: 5_551, ListingStatus.DELISTED: 333}
_EXPECTED_SOURCE_RECORD_COUNTS = {
    "sse_official_instrument_directory": 2_474,
    "szse_official_instrument_directory": 3_105,
    "bse_official_instrument_directory": 339,
    "szse_official_identity_change_disclosures": 3,
    "sse_official_identity_change_disclosures": 1,
}


class _CatalogSourceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=100)
    source_url: str = Field(min_length=1, max_length=500)
    adapter: str = Field(min_length=1, max_length=200)
    retrieved_at: AwareDatetime
    record_count: int = Field(ge=1)
    normalized_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retention_until: AwareDatetime
    permitted_operations: tuple[Literal["FETCH"], Literal["RETAIN"]]
    permitted_use: Literal["NONCOMMERCIAL_PRIVATE_RESEARCH_PREVIEW"]
    rights_state: Literal["DEV_ONLY/UNVERIFIED"]


class _BundledInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    short_name: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(min_length=1, max_length=300)
    legal_name_verified: bool
    listing_status: ListingStatus
    board: str = Field(min_length=1, max_length=100)
    listed_at: date | None
    delisted_at: date | None
    revision_known_at: AwareDatetime
    source_id: str = Field(min_length=1, max_length=100)
    legacy_symbols: list[str] = Field(default_factory=list, max_length=10)
    legacy_names: list[str] = Field(default_factory=list, max_length=20)
    alias_evidence_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @property
    def exchange(self) -> Literal["XSHG", "XSHE", "XBSE"]:
        return cast(
            'Literal["XSHG", "XSHE", "XBSE"]',
            self.instrument_id.split(":", maxsplit=1)[0],
        )

    @property
    def symbol(self) -> str:
        return self.instrument_id.split(":", maxsplit=1)[1]


class _CatalogPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    catalog_version: str = Field(min_length=1, max_length=100)
    source_mode: Literal["DEV_ONLY/UNVERIFIED"]
    retrieved_at: AwareDatetime
    expires_at: AwareDatetime
    notice: str = Field(min_length=1, max_length=1_000)
    sources: list[_CatalogSourceReceipt] = Field(min_length=5, max_length=20)
    alias_evidence: list[_CatalogAliasEvidence] = Field(min_length=4, max_length=4)
    instruments: list[_BundledInstrument] = Field(min_length=5_750, max_length=10_000)


class _CatalogAliasEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: UUID
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    legacy_symbol: str = Field(pattern=r"^[0-9]{6}$")
    legacy_name: str = Field(min_length=1, max_length=100)
    source_id: Literal[
        "szse_official_identity_change_disclosures",
        "sse_official_identity_change_disclosures",
    ]
    source_url: str = Field(min_length=1, max_length=500)
    retrieved_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retention_until: AwareDatetime


@lru_cache(maxsize=1)
def _catalog_payload() -> _CatalogPayload:
    payload = _CatalogPayload.model_validate_json(
        gzip.decompress(runtime_resource_path(_CATALOG_RESOURCE).read_bytes())
    )
    identifiers = [item.instrument_id for item in payload.instruments]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("bundled A-share catalog contains duplicate identifiers")
    if {item.source_id for item in payload.sources} != _CATALOG_SOURCE_IDS:
        raise ValueError("bundled A-share catalog source receipts are incomplete")
    if any(item.retention_until != payload.expires_at for item in payload.sources):
        raise ValueError("bundled A-share catalog retention receipts disagree")
    if any(item.retention_until != payload.expires_at for item in payload.alias_evidence):
        raise ValueError("bundled A-share alias evidence retention receipts disagree")
    if payload.expires_at <= payload.retrieved_at:
        raise ValueError("bundled A-share catalog retention window is invalid")
    source_receipts = {item.source_id: item for item in payload.sources}
    alias_evidence_ids = [item.evidence_id for item in payload.alias_evidence]
    instrument_alias_evidence_ids = [
        evidence_id for item in payload.instruments for evidence_id in item.alias_evidence_ids
    ]
    if len(alias_evidence_ids) != len(set(alias_evidence_ids)):
        raise ValueError("bundled A-share alias evidence IDs are not unique")
    if set(instrument_alias_evidence_ids) != set(alias_evidence_ids):
        raise ValueError("bundled A-share alias evidence is not linked to canonical instruments")
    for alias in payload.alias_evidence:
        receipt = source_receipts[alias.source_id]
        if alias.rights_snapshot_sha256 != receipt.rights_snapshot_sha256:
            raise ValueError("bundled A-share alias rights snapshot does not match source receipt")
        target = next(
            (item for item in payload.instruments if item.instrument_id == alias.instrument_id),
            None,
        )
        if (
            target is None
            or alias.legacy_symbol not in target.legacy_symbols
            or alias.legacy_name not in target.legacy_names
            or alias.evidence_id not in target.alias_evidence_ids
        ):
            raise ValueError("bundled A-share alias evidence does not match its canonical identity")
    exchange_counts = {
        exchange: sum(item.exchange == exchange for item in payload.instruments)
        for exchange in _EXPECTED_CANONICAL_COUNTS
    }
    listing_counts = {
        status: sum(item.listing_status is status for item in payload.instruments)
        for status in _EXPECTED_LISTING_COUNTS
    }
    source_record_counts = {item.source_id: item.record_count for item in payload.sources}
    if (
        exchange_counts != _EXPECTED_CANONICAL_COUNTS
        or listing_counts != _EXPECTED_LISTING_COUNTS
        or source_record_counts != _EXPECTED_SOURCE_RECORD_COUNTS
    ):
        raise ValueError("bundled A-share catalog counts differ from its versioned manifest")
    return payload


def _catalog(now: datetime) -> tuple[_BundledInstrument, ...]:
    payload = _catalog_payload()
    if now >= payload.expires_at:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "INSTRUMENT_CATALOG_EXPIRED",
            retryable=True,
        )
    return tuple(payload.instruments)


_EXCHANGE_SUFFIX = {"SH": "XSHG", "SS": "XSHG", "SZ": "XSHE", "BJ": "XBSE"}


def _canonical_code_query(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value).upper()
    if re.fullmatch(r"(XSHG|XSHE|XBSE):[0-9]{6}", compact):
        return compact
    match = re.fullmatch(r"([0-9]{6})\.(SH|SS|SZ|BJ)", compact)
    if match is not None:
        return f"{_EXCHANGE_SUFFIX[match.group(2)]}:{match.group(1)}"
    match = re.fullmatch(r"(SH|SS|SZ|BJ)[.:]?([0-9]{6})", compact)
    if match is not None:
        return f"{_EXCHANGE_SUFFIX[match.group(1)]}:{match.group(2)}"
    return None


def _instrument_ref(item: _BundledInstrument) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=item.instrument_id,
        symbol=item.symbol,
        exchange=item.exchange,
        market="CN",
        asset_class="equity",
        currency="CNY",
    )


def _company_id(item: _BundledInstrument) -> UUID:
    return uuid5(_NAMESPACE, f"company:{item.instrument_id}")


def _evidence_id(item: _BundledInstrument) -> UUID:
    return uuid5(_NAMESPACE, f"catalog-evidence:{item.source_id}:{item.instrument_id}")


def _evidence_ids(item: _BundledInstrument) -> list[UUID]:
    return [_evidence_id(item), *item.alias_evidence_ids]


def _aware_date(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _profile(item: _BundledInstrument) -> InstrumentProfile:
    company_id = _company_id(item)
    listed_at = _aware_date(item.listed_at)
    delisted_at = _aware_date(item.delisted_at)
    known_at = item.revision_known_at
    return InstrumentProfile(
        schema_version="1.0",
        instrument=_instrument_ref(item),
        instrument_type=InstrumentType.EQUITY,
        company_id=company_id,
        issuer=EntityRef(entity_type="company", entity_id=str(company_id)),
        display_name=item.short_name,
        listing_status=item.listing_status,
        board=item.board,
        lot_size=Decimal("100"),
        price_tick=Decimal("0.01"),
        listed_at=listed_at,
        delisted_at=delisted_at,
        valid_from=listed_at or _VALID_FROM,
        valid_until=None,
        first_seen_at=known_at,
        revision=1,
        revision_known_at=known_at,
        evidence_ids=_evidence_ids(item),
    )


def _company(item: _BundledInstrument) -> CompanyProfile:
    known_at = item.revision_known_at
    return CompanyProfile(
        schema_version="1.0",
        company_id=_company_id(item),
        legal_name=item.legal_name,
        legal_name_verified=item.legal_name_verified,
        short_name=item.short_name,
        business_summary=(
            "证券目录身份元数据, 仅用于私有 R2 研究预览; 业务事实须由研究证据包另行核验。"
        ),
        valid_from=_aware_date(item.listed_at) or _VALID_FROM,
        first_seen_at=known_at,
        revision=1,
        revision_known_at=known_at,
        evidence_ids=_evidence_ids(item),
    )


def _search_match(
    item: _BundledInstrument, match_type: InstrumentSearchMatchType
) -> InstrumentSearchMatchV1_1:
    return InstrumentSearchMatchV1_1(
        schema_version="1.1",
        instrument_id=item.instrument_id,
        symbol=item.symbol,
        exchange=item.exchange,
        short_name=item.short_name,
        legal_name=item.legal_name,
        legal_name_verified=item.legal_name_verified,
        listing_status=item.listing_status,
        match_type=match_type,
        revision_known_at=item.revision_known_at,
    )


def _legacy_search_result(result: InstrumentSearchResultV1_1) -> InstrumentSearchResult:
    return InstrumentSearchResult(
        schema_version="1.0",
        query=result.query,
        matches=[
            InstrumentSearchMatch.model_validate(
                {
                    **item.model_dump(mode="python", exclude={"legal_name_verified"}),
                    "schema_version": "1.0",
                },
            )
            for item in result.matches
        ],
        as_of=result.as_of,
        searched_at=result.searched_at,
    )


def _matches_identity(item: _BundledInstrument, instrument_id: str) -> bool:
    if item.instrument_id == instrument_id:
        return True
    exchange, symbol = instrument_id.split(":", maxsplit=1)
    return exchange == item.exchange and symbol in item.legacy_symbols


class BundledAshareInstrumentMasterData:
    """Offline search over one immutable, development-only A-share directory snapshot."""

    def __init__(self, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._clock = clock

    async def resolve(self, request: InstrumentLookupRequest) -> InstrumentLookupResult:
        requested_id = request.instrument_id
        if requested_id is None:
            exchange = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "XBSE"}.get(
                request.exchange or "", request.exchange or ""
            )
            requested_id = f"{exchange}:{request.symbol}"
        matches = [
            _profile(item)
            for item in _catalog(self._clock())
            if _matches_identity(item, requested_id)
            and item.revision_known_at <= request.context.as_of
        ]
        return InstrumentLookupResult(
            schema_version="1.0",
            status=ResolutionStatus.RESOLVED if matches else ResolutionStatus.NOT_FOUND,
            matches=matches,
            resolved_at=request.context.requested_at,
            as_of=request.context.as_of,
        )

    async def search(self, request: InstrumentSearchQuery) -> InstrumentSearchResult:
        return _legacy_search_result(await self.search_v1_1(request))

    async def search_v1_1(self, request: InstrumentSearchQuery) -> InstrumentSearchResultV1_1:
        normalized = request.normalized_query
        canonical = _canonical_code_query(normalized)
        matches: list[InstrumentSearchMatchV1_1] = []
        for item in _catalog(self._clock()):
            if item.revision_known_at > request.as_of:
                continue
            match_type: InstrumentSearchMatchType | None = None
            if canonical is not None and _matches_identity(item, canonical):
                match_type = InstrumentSearchMatchType.EXACT_INSTRUMENT_ID
            elif re.fullmatch(r"[0-9]{6}", normalized) and (
                normalized == item.symbol or normalized in item.legacy_symbols
            ):
                match_type = InstrumentSearchMatchType.EXACT_CODE
            elif normalized.casefold() == item.short_name.casefold():
                match_type = InstrumentSearchMatchType.EXACT_SHORT_NAME
            elif normalized.casefold() == item.legal_name.casefold():
                match_type = InstrumentSearchMatchType.EXACT_LEGAL_NAME
            elif normalized.casefold() in {name.casefold() for name in item.legacy_names}:
                match_type = InstrumentSearchMatchType.EXACT_LEGAL_NAME
            elif (
                normalized.casefold() in item.short_name.casefold()
                or normalized.casefold() in item.legal_name.casefold()
                or any(normalized.casefold() in name.casefold() for name in item.legacy_names)
            ):
                match_type = InstrumentSearchMatchType.PARTIAL_NAME
            if match_type is not None:
                matches.append(_search_match(item, match_type))
        matches.sort(
            key=lambda item: (
                list(InstrumentSearchMatchType).index(item.match_type),
                item.instrument_id,
            )
        )
        return InstrumentSearchResultV1_1(
            schema_version="1.1",
            query=normalized,
            matches=matches[: request.limit],
            as_of=request.as_of,
            searched_at=request.as_of,
        )

    async def query(self, request: MasterDataQuery) -> MasterDataSnapshot:
        visible = [
            item
            for item in _catalog(self._clock())
            if item.revision_known_at <= request.context.as_of
        ]
        if request.instrument_ids:
            expected = set(request.instrument_ids)
            visible = [
                item
                for item in visible
                if any(_matches_identity(item, instrument_id) for instrument_id in expected)
            ]
        if not request.include_inactive:
            visible = [item for item in visible if item.listing_status is ListingStatus.ACTIVE]
        visible = visible[: request.limit]
        include = set(request.entity_kinds)
        return MasterDataSnapshot(
            schema_version="1.0",
            as_of=request.context.as_of,
            instruments=(
                [_profile(item) for item in visible] if EntityKind.INSTRUMENT in include else []
            ),
            companies=(
                [_company(item) for item in visible] if EntityKind.COMPANY in include else []
            ),
            industries=[],
            regions=[],
            next_cursor=None,
            has_more=False,
        )


SyntheticAshareInstrumentMasterData = BundledAshareInstrumentMasterData

__all__ = ["BundledAshareInstrumentMasterData", "SyntheticAshareInstrumentMasterData"]
