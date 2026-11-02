"""Point-in-time security, issuer, industry and region master-data contracts."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import EntityRef, InstrumentRef, OperationContext


class ListingStatus(StrEnum):
    PRE_LISTING = "pre_listing"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class InstrumentType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    REIT = "reit"
    FUND = "fund"
    BOND = "bond"
    CONVERTIBLE_BOND = "convertible_bond"
    OTHER = "other"


class RegionLevel(StrEnum):
    COUNTRY = "country"
    PROVINCE = "province"
    PREFECTURE = "prefecture"
    COUNTY = "county"
    SPECIAL_ZONE = "special_zone"


class RegionRole(StrEnum):
    REGISTERED = "registered"
    HEADQUARTERS = "headquarters"
    PRIMARY_OPERATION = "primary_operation"
    MATERIAL_OPERATION = "material_operation"


class IndustryScheme(StrEnum):
    CSRC = "csrc"
    CITIC = "citic"
    SHENWAN = "shenwan"
    EXCHANGE = "exchange"
    CUSTOM = "custom"


class EntityKind(StrEnum):
    INSTRUMENT = "instrument"
    COMPANY = "company"
    INDUSTRY = "industry"
    REGION = "region"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class InstrumentSearchMatchType(StrEnum):
    EXACT_INSTRUMENT_ID = "exact_instrument_id"
    EXACT_CODE = "exact_code"
    EXACT_SHORT_NAME = "exact_short_name"
    EXACT_LEGAL_NAME = "exact_legal_name"
    PARTIAL_NAME = "partial_name"


class IndustryMembership(ContractModel):
    industry_id: UUID
    scheme: IndustryScheme
    is_primary: bool
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validity_must_be_ordered(self) -> "IndustryMembership":
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class RegionAssignment(ContractModel):
    region_id: UUID
    role: RegionRole
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validity_must_be_ordered(self) -> "RegionAssignment":
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class BusinessLine(ContractModel):
    business_line_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    revenue_share: DecimalString | None = Field(default=None, ge=0, le=1)
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validity_must_be_ordered(self) -> "BusinessLine":
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class IndustryNode(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    industry_id: UUID
    scheme: IndustryScheme
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    level: int = Field(ge=1, le=20)
    parent_industry_id: UUID | None = None
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def temporal_fields_must_be_consistent(self) -> "IndustryNode":
        if self.parent_industry_id == self.industry_id:
            raise ValueError("an industry cannot be its own parent")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class RegionNode(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    region_id: UUID
    standard_code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    level: RegionLevel
    parent_region_id: UUID | None = None
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def temporal_fields_must_be_consistent(self) -> "RegionNode":
        if self.parent_region_id == self.region_id:
            raise ValueError("a region cannot be its own parent")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class CompanyProfile(ContractModel):
    """Issuer facts visible at one historical cutoff."""

    schema_version: Literal["1.0"] = "1.0"
    company_id: UUID
    legal_name: str = Field(min_length=1, max_length=300)
    legal_name_verified: bool = False
    short_name: str = Field(min_length=1, max_length=100)
    unified_credit_code: str | None = Field(default=None, min_length=18, max_length=18)
    business_summary: str = Field(min_length=1, max_length=5000)
    business_lines: list[BusinessLine] = Field(default_factory=list, max_length=200)
    industries: list[IndustryMembership] = Field(default_factory=list, max_length=100)
    regions: list[RegionAssignment] = Field(default_factory=list, max_length=100)
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    revision: int = Field(ge=1)
    revision_known_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def company_profile_must_be_consistent(self) -> "CompanyProfile":
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        line_ids = [item.business_line_id for item in self.business_lines]
        if len(set(line_ids)) != len(line_ids):
            raise ValueError("business lines must be unique")
        nested_known_at = [
            *(item.first_seen_at for item in self.business_lines),
            *(item.first_seen_at for item in self.industries),
            *(item.first_seen_at for item in self.regions),
        ]
        if any(item > self.revision_known_at for item in nested_known_at):
            raise ValueError("company revision cannot contain facts learned later")
        return self


class InstrumentProfile(ContractModel):
    """Canonical listing metadata without vendor or ORM types."""

    schema_version: Literal["1.0"] = "1.0"
    instrument: InstrumentRef
    instrument_type: InstrumentType
    company_id: UUID | None = None
    issuer: EntityRef | None = None
    display_name: str = Field(min_length=1, max_length=200)
    listing_status: ListingStatus
    board: str = Field(min_length=1, max_length=100)
    lot_size: DecimalString = Field(gt=0)
    price_tick: DecimalString = Field(gt=0)
    listed_at: AwareDatetime | None = None
    delisted_at: AwareDatetime | None = None
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    revision: int = Field(ge=1)
    revision_known_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def instrument_profile_must_be_consistent(self) -> "InstrumentProfile":
        if self.instrument_type is InstrumentType.EQUITY:
            if self.company_id is None and self.issuer is None:
                raise ValueError("equity instruments require a company or issuer reference")
        if self.delisted_at is not None and self.listed_at is not None:
            if self.delisted_at <= self.listed_at:
                raise ValueError("delisted_at must be later than listed_at")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        return self


class InstrumentLookupRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument_id: str | None = Field(default=None, max_length=200)
    symbol: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,32}$")

    @model_validator(mode="after")
    def exactly_one_lookup_shape_is_required(self) -> "InstrumentLookupRequest":
        by_id = self.instrument_id is not None
        by_symbol = self.symbol is not None or self.exchange is not None
        if by_id == by_symbol:
            raise ValueError("provide either instrument_id or symbol with exchange")
        if by_symbol and (self.symbol is None or self.exchange is None):
            raise ValueError("symbol and exchange must be provided together")
        return self


class InstrumentLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    status: ResolutionStatus
    matches: list[InstrumentProfile] = Field(default_factory=list, max_length=100)
    resolved_at: AwareDatetime
    as_of: AwareDatetime

    @model_validator(mode="after")
    def status_must_match_result_count(self) -> "InstrumentLookupResult":
        match_count = len(self.matches)
        if self.status is ResolutionStatus.RESOLVED and match_count != 1:
            raise ValueError("resolved status requires exactly one match")
        if self.status is ResolutionStatus.NOT_FOUND and match_count != 0:
            raise ValueError("not_found status cannot contain matches")
        if self.status is ResolutionStatus.AMBIGUOUS and match_count < 2:
            raise ValueError("ambiguous status requires at least two matches")
        if any(item.revision_known_at > self.as_of for item in self.matches):
            raise ValueError("lookup cannot expose instrument revisions known after as_of")
        return self


class InstrumentSearchQuery(ContractModel):
    """Bounded A-share code/name search at one point-in-time cutoff."""

    schema_version: Literal["1.0"] = "1.0"
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    as_of: AwareDatetime

    @property
    def normalized_query(self) -> str:
        return " ".join(self.query.strip().split())


class InstrumentSearchMatch(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    exchange: Literal["XSHG", "XSHE", "XBSE"]
    short_name: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(min_length=1, max_length=300)
    listing_status: ListingStatus
    match_type: InstrumentSearchMatchType
    revision_known_at: AwareDatetime


class InstrumentSearchMatchV1_1(ContractModel):
    """Compatible search match with explicit legal-name verification semantics."""

    schema_version: Literal["1.1"] = "1.1"
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    exchange: Literal["XSHG", "XSHE", "XBSE"]
    short_name: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(min_length=1, max_length=300)
    legal_name_verified: bool
    listing_status: ListingStatus
    match_type: InstrumentSearchMatchType
    revision_known_at: AwareDatetime


class InstrumentSearchResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    query: str = Field(min_length=1, max_length=200)
    matches: list[InstrumentSearchMatch] = Field(default_factory=list, max_length=50)
    as_of: AwareDatetime
    searched_at: AwareDatetime

    @property
    def requires_confirmation(self) -> bool:
        if len(self.matches) != 1:
            return bool(self.matches)
        return self.matches[0].match_type is InstrumentSearchMatchType.PARTIAL_NAME

    @model_validator(mode="after")
    def matches_must_be_point_in_time_safe(self) -> "InstrumentSearchResult":
        if any(item.revision_known_at > self.as_of for item in self.matches):
            raise ValueError("search cannot expose an instrument revision known after as_of")
        identifiers = [item.instrument_id for item in self.matches]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("search matches must be unique")
        return self


class InstrumentSearchResultV1_1(ContractModel):
    """Search result 1.1; 1.0 remains immutable and readable."""

    schema_version: Literal["1.1"] = "1.1"
    query: str = Field(min_length=1, max_length=200)
    matches: list[InstrumentSearchMatchV1_1] = Field(default_factory=list, max_length=50)
    as_of: AwareDatetime
    searched_at: AwareDatetime

    @property
    def requires_confirmation(self) -> bool:
        if len(self.matches) != 1:
            return bool(self.matches)
        return self.matches[0].match_type is InstrumentSearchMatchType.PARTIAL_NAME

    @model_validator(mode="after")
    def matches_must_be_point_in_time_safe(self) -> "InstrumentSearchResultV1_1":
        if any(item.revision_known_at > self.as_of for item in self.matches):
            raise ValueError("search cannot expose an instrument revision known after as_of")
        identifiers = [item.instrument_id for item in self.matches]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("search matches must be unique")
        return self


class MasterDataQuery(ContractModel):
    """Query a globally ordered discriminated union with one opaque stable cursor."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    entity_kinds: list[EntityKind] = Field(default_factory=lambda: list(EntityKind), min_length=1)
    instrument_ids: list[str] = Field(default_factory=list, max_length=10_000)
    company_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    industry_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    region_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    include_inactive: bool = False
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)


class MasterDataSnapshot(ContractModel):
    """Bounded union page; cursor order is stable across all four entity kinds."""

    schema_version: Literal["1.0"] = "1.0"
    as_of: AwareDatetime
    instruments: list[InstrumentProfile] = Field(default_factory=list, max_length=1000)
    companies: list[CompanyProfile] = Field(default_factory=list, max_length=1000)
    industries: list[IndustryNode] = Field(default_factory=list, max_length=1000)
    regions: list[RegionNode] = Field(default_factory=list, max_length=1000)
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False

    @model_validator(mode="after")
    def snapshot_must_be_point_in_time_safe(self) -> "MasterDataSnapshot":
        unseen_instruments = any(item.revision_known_at > self.as_of for item in self.instruments)
        unseen_companies = any(item.revision_known_at > self.as_of for item in self.companies)
        unseen_industries = any(item.first_seen_at > self.as_of for item in self.industries)
        unseen_regions = any(item.first_seen_at > self.as_of for item in self.regions)
        if unseen_instruments or unseen_companies or unseen_industries or unseen_regions:
            raise ValueError("snapshot cannot contain revisions or records known after as_of")
        nested_known_at = [
            *(line.first_seen_at for company in self.companies for line in company.business_lines),
            *(item.first_seen_at for company in self.companies for item in company.industries),
            *(item.first_seen_at for company in self.companies for item in company.regions),
        ]
        if any(item > self.as_of for item in nested_known_at):
            raise ValueError("snapshot cannot contain nested facts learned after as_of")
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        return self
