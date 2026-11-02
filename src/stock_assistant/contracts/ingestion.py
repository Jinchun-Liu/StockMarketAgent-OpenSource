"""Vendor-neutral contracts for authorized incremental source ingestion."""

import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.formal_disclosure import (
    CandidateVerificationOutcome,
    FormalDocumentVerification,
)

CapabilityName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")]

_EXTENSION_MAX_KEYS = 128
_EXTENSION_MAX_BYTES = 1024 * 1024


def _validate_extension(
    value: dict[str, JsonValue],
    *,
    namespace: str | None,
    owner: str | None,
    label: str,
) -> None:
    if bool(value) != (namespace is not None and owner is not None):
        raise ValueError(f"{label} namespace and owner are required exactly when values exist")
    if len(value) > _EXTENSION_MAX_KEYS:
        raise ValueError(f"{label} cannot contain more than {_EXTENSION_MAX_KEYS} keys")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > _EXTENSION_MAX_BYTES:
        raise ValueError(f"{label} exceeds the {_EXTENSION_MAX_BYTES}-byte limit")


class RightsOperation(StrEnum):
    """Actions that must be independently authorized and fail closed."""

    FETCH = "fetch"
    DERIVE = "derive"
    CACHE_METADATA = "cache_metadata"
    CACHE_FULLTEXT = "cache_fulltext"
    EMBED = "embed"
    TRAIN = "train"
    EXPORT = "export"
    REDISTRIBUTE = "redistribute"
    RETAIN = "retain"
    DELETE = "delete"


class RightsContentCategory(StrEnum):
    """Stable R1 content categories evaluated independently by the rights policy."""

    STATUTORY_ANNOUNCEMENTS = "statutory_announcements"
    FINANCIAL_CHANGES = "financial_changes"
    MARKET_SNAPSHOT = "market_snapshot"
    COMPANY_INDUSTRY_NEWS = "company_industry_news"
    MACRO_FLASHES = "macro_flashes"


class RightsPermittedUse(StrEnum):
    """Contract purposes kept separate from the operation performed on source data."""

    DISPLAY = "display"
    SUMMARY = "summary"
    NON_DISPLAY = "non_display"
    AI_USE = "ai_use"


class SourceRuntimeMode(StrEnum):
    """Mutually exclusive R1 source execution modes."""

    DEMO_REPLAY = "demo_replay"
    DEV_LIVE = "dev_live"
    AUTHORIZED = "authorized"


class SourceReleaseTier(StrEnum):
    """Whether a source result may leave development-only surfaces."""

    DEV_ONLY = "dev_only"
    AUTHORIZED = "authorized"


class SourceRightsStatus(StrEnum):
    """Publicly safe rights state without exposing contract evidence."""

    UNVERIFIED = "unverified"
    APPROVED = "approved"
    BLOCKED = "blocked"


class SourceAccessChannel(StrEnum):
    """Auditable access channel used to obtain one source record."""

    DEMO_REPLAY = "demo_replay"
    AKSHARE = "akshare"
    OFFICIAL_HTTP = "official_http"
    AUTHORIZED_API = "authorized_api"


class SourceRuntimeModeV1_2(StrEnum):
    """R1 1.2 execution modes, including the detached public snapshot."""

    DEMO_REPLAY = "demo_replay"
    DEV_LIVE = "dev_live"
    PUBLIC_STATIC = "public_static"
    AUTHORIZED = "authorized"


class SourceRightsStatusV1_2(StrEnum):
    """R1 1.2 public rights state, including data with no source-rights claim."""

    UNVERIFIED = "unverified"
    APPROVED = "approved"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class SourceAccessChannelV1_2(StrEnum):
    """R1 1.2 access channels used by replay, free and authorized adapters."""

    DEMO_REPLAY = "demo_replay"
    AKSHARE = "akshare"
    OFFICIAL_HTTP = "official_http"
    BAOSTOCK_SDK = "baostock_sdk"
    AUTHORIZED_API = "authorized_api"


class SourceQualityTier(StrEnum):
    """Coarse quality origin kept separate from evidence authority and rights."""

    DEMO_REPLAY = "demo_replay"
    FREE_OFFICIAL = "free_official"
    FREE_STRUCTURED = "free_structured"
    DEV_WEB = "dev_web"
    AUTHORIZED = "authorized"


class SourceEvidenceRole(StrEnum):
    """How one source may support a fact without changing its source identity."""

    AUTHORITATIVE = "authoritative"
    STRUCTURED_HELPER = "structured_helper"
    DISCOVERY = "discovery"
    CROSS_CHECK = "cross_check"


class SourceProvenance(ContractModel):
    """Stable source lineage shared by ingestion, storage and public read models."""

    schema_version: Literal["1.1"] = "1.1"
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    access_channel: SourceAccessChannel
    adapter_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    adapter_version: str = Field(min_length=1, max_length=100)
    endpoint_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,199}$")
    source_identifier: str = Field(min_length=1, max_length=500)
    runtime_mode: SourceRuntimeMode
    release_tier: SourceReleaseTier
    rights_status: SourceRightsStatus
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"runtime_mode": {"const": "demo_replay"}}},
                    "then": {
                        "properties": {
                            "access_channel": {"const": "demo_replay"},
                            "release_tier": {"const": "dev_only"},
                            "rights_status": {"not": {"const": "approved"}},
                        }
                    },
                },
                {
                    "if": {"properties": {"runtime_mode": {"const": "dev_live"}}},
                    "then": {
                        "properties": {
                            "access_channel": {"const": "akshare"},
                            "release_tier": {"const": "dev_only"},
                            "rights_status": {"const": "unverified"},
                        }
                    },
                },
                {
                    "if": {"properties": {"runtime_mode": {"const": "authorized"}}},
                    "then": {
                        "properties": {
                            "access_channel": {"const": "authorized_api"},
                            "release_tier": {"const": "authorized"},
                            "rights_status": {"const": "approved"},
                        }
                    },
                },
            ],
            "x-runtime-invariants": [
                "runtime_mode determines access_channel, release_tier, and rights_status"
            ],
        }
    )

    @model_validator(mode="after")
    def mode_release_and_rights_must_be_consistent(self) -> "SourceProvenance":
        if self.runtime_mode is SourceRuntimeMode.DEMO_REPLAY:
            if self.access_channel is not SourceAccessChannel.DEMO_REPLAY:
                raise ValueError("demo replay provenance requires the demo replay channel")
            if self.release_tier is not SourceReleaseTier.DEV_ONLY:
                raise ValueError("demo replay provenance must remain development-only")
            if self.rights_status is SourceRightsStatus.APPROVED:
                raise ValueError("demo replay provenance cannot claim approved source rights")
        elif self.runtime_mode is SourceRuntimeMode.DEV_LIVE:
            if self.access_channel is not SourceAccessChannel.AKSHARE:
                raise ValueError("dev live provenance requires the allowlisted AKShare channel")
            if self.release_tier is not SourceReleaseTier.DEV_ONLY:
                raise ValueError("dev live provenance must remain development-only")
            if self.rights_status is not SourceRightsStatus.UNVERIFIED:
                raise ValueError("dev live provenance must expose unverified public rights")
        else:
            if self.access_channel is not SourceAccessChannel.AUTHORIZED_API:
                raise ValueError(
                    "authorized runtime provenance requires the authorized API channel"
                )
            if self.release_tier is not SourceReleaseTier.AUTHORIZED:
                raise ValueError(
                    "authorized runtime provenance requires the authorized release tier"
                )
            if self.rights_status is not SourceRightsStatus.APPROVED:
                raise ValueError("authorized runtime provenance requires approved source rights")
        return self


class SourceProvenanceV1_2(ContractModel):
    """R1 1.2 lineage with explicit quality and evidence-role dimensions."""

    schema_version: Literal["1.2"] = "1.2"
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    access_channel: SourceAccessChannelV1_2
    adapter_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    adapter_version: str = Field(min_length=1, max_length=100)
    endpoint_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,199}$")
    source_identifier: str = Field(min_length=1, max_length=500)
    runtime_mode: SourceRuntimeModeV1_2
    release_tier: SourceReleaseTier
    rights_status: SourceRightsStatusV1_2
    quality_tier: SourceQualityTier
    evidence_role: SourceEvidenceRole
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"runtime_mode": {"const": "demo_replay"}}},
                    "then": {
                        "properties": {
                            "access_channel": {"const": "demo_replay"},
                            "release_tier": {"const": "dev_only"},
                            "rights_status": {"const": "not_applicable"},
                            "quality_tier": {"const": "demo_replay"},
                        }
                    },
                },
                {
                    "if": {"properties": {"runtime_mode": {"const": "public_static"}}},
                    "then": {
                        "properties": {
                            "access_channel": {"const": "demo_replay"},
                            "release_tier": {"const": "dev_only"},
                            "rights_status": {"const": "not_applicable"},
                            "quality_tier": {"const": "demo_replay"},
                        }
                    },
                },
                {
                    "if": {"properties": {"runtime_mode": {"const": "dev_live"}}},
                    "then": {
                        "properties": {
                            "access_channel": {
                                "enum": ["akshare", "official_http", "baostock_sdk"]
                            },
                            "release_tier": {"const": "dev_only"},
                            "rights_status": {"const": "unverified"},
                            "quality_tier": {
                                "enum": ["free_official", "free_structured", "dev_web"]
                            },
                        }
                    },
                },
                {
                    "if": {"properties": {"runtime_mode": {"const": "authorized"}}},
                    "then": {
                        "properties": {
                            "access_channel": {"const": "authorized_api"},
                            "release_tier": {"const": "authorized"},
                            "rights_status": {"const": "approved"},
                            "quality_tier": {"const": "authorized"},
                        }
                    },
                },
            ],
            "x-runtime-invariants": [
                "runtime_mode constrains channel, release tier, rights status, and quality",
                "quality_tier and evidence_role remain independent dimensions",
            ],
        }
    )

    @model_validator(mode="after")
    def mode_release_rights_and_quality_must_be_consistent(self) -> "SourceProvenanceV1_2":
        if self.runtime_mode is SourceRuntimeModeV1_2.DEMO_REPLAY:
            if self.access_channel is not SourceAccessChannelV1_2.DEMO_REPLAY:
                raise ValueError("demo replay provenance requires the demo replay channel")
            if self.release_tier is not SourceReleaseTier.DEV_ONLY:
                raise ValueError("demo replay provenance must remain development-only")
            if self.rights_status is not SourceRightsStatusV1_2.NOT_APPLICABLE:
                raise ValueError("demo replay provenance requires not-applicable source rights")
            if self.quality_tier is not SourceQualityTier.DEMO_REPLAY:
                raise ValueError("demo replay provenance requires the demo replay quality tier")
        elif self.runtime_mode is SourceRuntimeModeV1_2.PUBLIC_STATIC:
            if self.access_channel is not SourceAccessChannelV1_2.DEMO_REPLAY:
                raise ValueError("public static provenance requires the demo replay channel")
            if self.release_tier is not SourceReleaseTier.DEV_ONLY:
                raise ValueError("public static provenance must remain development-only")
            if self.rights_status is not SourceRightsStatusV1_2.NOT_APPLICABLE:
                raise ValueError("public static provenance requires not-applicable source rights")
            if self.quality_tier is not SourceQualityTier.DEMO_REPLAY:
                raise ValueError("public static provenance requires the demo replay quality tier")
        elif self.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE:
            if self.access_channel not in {
                SourceAccessChannelV1_2.AKSHARE,
                SourceAccessChannelV1_2.OFFICIAL_HTTP,
                SourceAccessChannelV1_2.BAOSTOCK_SDK,
            }:
                raise ValueError("dev live provenance requires an allowlisted free channel")
            if self.release_tier is not SourceReleaseTier.DEV_ONLY:
                raise ValueError("dev live provenance must remain development-only")
            if self.rights_status is not SourceRightsStatusV1_2.UNVERIFIED:
                raise ValueError("dev live provenance must expose unverified public rights")
            if self.quality_tier not in {
                SourceQualityTier.FREE_OFFICIAL,
                SourceQualityTier.FREE_STRUCTURED,
                SourceQualityTier.DEV_WEB,
            }:
                raise ValueError("dev live provenance requires a free-development quality tier")
        else:
            if self.access_channel is not SourceAccessChannelV1_2.AUTHORIZED_API:
                raise ValueError(
                    "authorized runtime provenance requires the authorized API channel"
                )
            if self.release_tier is not SourceReleaseTier.AUTHORIZED:
                raise ValueError(
                    "authorized runtime provenance requires the authorized release tier"
                )
            if self.rights_status is not SourceRightsStatusV1_2.APPROVED:
                raise ValueError("authorized runtime provenance requires approved source rights")
            if self.quality_tier is not SourceQualityTier.AUTHORIZED:
                raise ValueError("authorized runtime provenance requires authorized quality")
        return self


class SourceTier(StrEnum):
    """Connector tiers supported by source configuration and scoring priors."""

    STATUTORY_DISCLOSURE = "statutory_disclosure"
    REGULATOR = "regulator"
    ISSUER_OFFICIAL = "issuer_official"
    OFFICIAL_POLICY = "official_policy"
    MARKET_DATA = "market_data"
    LICENSED_BROKER_RESEARCH = "licensed_broker_research"
    AUTHORITATIVE_FINANCIAL_MEDIA = "authoritative_financial_media"
    LICENSED_AGGREGATOR = "licensed_aggregator"
    SOCIAL_AGGREGATE = "social_aggregate"
    DEVELOPMENT_ONLY = "development_only"


class ContentTier(StrEnum):
    """Per-record authority tier after resolving capability-specific overrides."""

    STATUTORY_DISCLOSURE = "statutory_disclosure"
    REGULATOR = "regulator"
    ISSUER_OFFICIAL = "issuer_official"
    OFFICIAL_POLICY = "official_policy"
    MARKET_DATA = "market_data"
    LICENSED_BROKER_RESEARCH = "licensed_broker_research"
    AUTHORITATIVE_FINANCIAL_MEDIA = "authoritative_financial_media"
    LICENSED_AGGREGATOR = "licensed_aggregator"
    SOCIAL_AGGREGATE = "social_aggregate"
    DEVELOPMENT_ONLY = "development_only"


class FactEligibility(StrEnum):
    """Permitted downstream use; consumers fail closed on incompatible purposes."""

    COMPANY_FACT = "company_fact"
    POLICY_FACT = "policy_fact"
    MARKET_OBSERVATION = "market_observation"
    CONTEXT_ONLY = "context_only"
    SENTIMENT_ONLY = "sentiment_only"
    INELIGIBLE = "ineligible"


def is_fact_eligibility_compatible(
    content_tier: ContentTier,
    fact_eligibility: FactEligibility,
) -> bool:
    """Return whether a content tier may be used for the declared downstream purpose."""

    if content_tier is ContentTier.SOCIAL_AGGREGATE:
        return fact_eligibility is FactEligibility.SENTIMENT_ONLY
    if content_tier is ContentTier.DEVELOPMENT_ONLY:
        return fact_eligibility is FactEligibility.INELIGIBLE
    if fact_eligibility is FactEligibility.COMPANY_FACT:
        return content_tier in {
            ContentTier.STATUTORY_DISCLOSURE,
            ContentTier.REGULATOR,
            ContentTier.ISSUER_OFFICIAL,
        }
    if fact_eligibility is FactEligibility.POLICY_FACT:
        return content_tier is ContentTier.OFFICIAL_POLICY
    if fact_eligibility is FactEligibility.MARKET_OBSERVATION:
        return content_tier is ContentTier.MARKET_DATA
    if fact_eligibility is FactEligibility.SENTIMENT_ONLY:
        return False
    return True


def direct_source_content_tier(source_tier: SourceTier) -> ContentTier | None:
    """Return the required content tier for non-aggregator connectors."""

    if source_tier is SourceTier.LICENSED_AGGREGATOR:
        return None
    return ContentTier(source_tier.value)


class SourceCapability(ContractModel):
    capability: CapabilityName
    content_tier: ContentTier
    fact_eligibility: FactEligibility

    @model_validator(mode="after")
    def eligibility_must_match_content_tier(self) -> "SourceCapability":
        if not is_fact_eligibility_compatible(self.content_tier, self.fact_eligibility):
            raise ValueError("fact eligibility is incompatible with the content tier")
        return self


class SourceDescriptor(ContractModel):
    """Static connector identity and capability advertisement."""

    schema_version: Literal["1.0"] = "1.0"
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    display_name: str = Field(min_length=1, max_length=200)
    adapter_version: str = Field(min_length=1, max_length=100)
    source_tier: SourceTier
    enabled: bool
    capabilities: list[SourceCapability] = Field(min_length=1, max_length=100)
    authority: float = Field(ge=0, le=1, allow_inf_nan=False)
    rights_registry_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def capabilities_must_be_unique_and_eligible(self) -> "SourceDescriptor":
        names = [item.capability for item in self.capabilities]
        if len(set(names)) != len(names):
            raise ValueError("source capabilities must be unique")
        expected_tier = direct_source_content_tier(self.source_tier)
        if expected_tier is not None:
            if any(item.content_tier is not expected_tier for item in self.capabilities):
                raise ValueError("direct source capabilities must retain the source tier")
        return self


class SourceRegistryLookup(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")


class SourceRegistryLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    source: SourceDescriptor | None = None
    found: bool
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_must_match_source(self) -> "SourceRegistryLookupResult":
        if self.found != (self.source is not None):
            raise ValueError("found must reflect whether source is present")
        return self


class SourceRegistryQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    source_tiers: list[SourceTier] = Field(default_factory=list, max_length=20)
    capabilities: list[CapabilityName] = Field(default_factory=list, max_length=100)
    include_disabled: bool = False


class SourceRegistryResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    sources: list[SourceDescriptor] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime

    @model_validator(mode="after")
    def sources_must_be_unique(self) -> "SourceRegistryResult":
        source_ids = [item.source_id for item in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source registry result cannot contain duplicate IDs")
        return self


class FetchRequest(ContractModel):
    """Bounded incremental query; cursors remain opaque to callers."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    capability: CapabilityName
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    cursor: str | None = Field(default=None, max_length=4096)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    instrument_ids: list[str] = Field(default_factory=list, max_length=1000)
    region_ids: list[str] = Field(default_factory=list, max_length=500)
    limit: int = Field(default=100, ge=1, le=1000)
    options: dict[str, JsonValue] = Field(default_factory=dict, max_length=_EXTENSION_MAX_KEYS)
    options_namespace: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    options_owner: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{1,99}$")

    @model_validator(mode="after")
    def time_range_must_be_ordered(self) -> "FetchRequest":
        if self.since is not None and self.until is not None and self.until < self.since:
            raise ValueError("until cannot be earlier than since")
        if self.until is not None and self.until > self.context.as_of:
            raise ValueError("until cannot be later than context.as_of")
        _validate_extension(
            self.options,
            namespace=self.options_namespace,
            owner=self.options_owner,
            label="options",
        )
        return self


class SourceRecord(ContractModel):
    """Normalized source item that contains references, not vendor SDK objects."""

    record_id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    external_id: str = Field(min_length=1, max_length=500)
    capability: CapabilityName
    record_type: CapabilityName
    source_tier: SourceTier
    content_tier: ContentTier
    fact_eligibility: FactEligibility
    authority: float = Field(ge=0, le=1, allow_inf_nan=False)
    title: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = Field(default=None, max_length=2048)
    event_time: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    fetched_at: AwareDatetime
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    object_key: str | None = Field(default=None, max_length=1024)
    media_type: str = Field(default="application/json", min_length=1, max_length=200)
    payload: dict[str, JsonValue] = Field(default_factory=dict, max_length=_EXTENSION_MAX_KEYS)
    payload_namespace: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    payload_owner: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    provenance: SourceProvenance | None = None
    formal_document: FormalDocumentVerification | None = None

    @model_validator(mode="after")
    def timeline_and_fact_eligibility_must_be_safe(self) -> "SourceRecord":
        if self.fetched_at < self.first_seen_at:
            raise ValueError("fetched_at cannot be earlier than first_seen_at")
        if self.event_time is not None and self.event_time > self.first_seen_at:
            raise ValueError("event_time cannot be later than first_seen_at")
        if self.published_at is not None and self.published_at > self.first_seen_at:
            raise ValueError("published_at cannot be later than first_seen_at")
        if (self.event_time is None) != (self.provenance is None):
            raise ValueError("event_time and provenance must be supplied together")
        expected_tier = direct_source_content_tier(self.source_tier)
        if expected_tier is not None and self.content_tier is not expected_tier:
            raise ValueError("direct source records must retain the source tier")
        if not is_fact_eligibility_compatible(self.content_tier, self.fact_eligibility):
            raise ValueError("fact eligibility is incompatible with the content tier")
        _validate_extension(
            self.payload,
            namespace=self.payload_namespace,
            owner=self.payload_owner,
            label="payload",
        )
        return self


class SourceRecordV1_2(SourceRecord):
    """Normalized record emitted by new R1 writers with complete 1.2 lineage."""

    event_time: AwareDatetime
    provenance: SourceProvenanceV1_2  # type: ignore[assignment]


class FetchBatch(ContractModel):
    """One retry-safe connector response."""

    schema_version: Literal["1.0", "1.1"] = "1.0"
    batch_id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    records: list[SourceRecord] = Field(default_factory=list, max_length=1000)
    next_cursor: str | None = Field(default=None, max_length=4096)
    exhausted: bool
    started_at: AwareDatetime
    completed_at: AwareDatetime
    source_watermark: AwareDatetime | None = None
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    warnings: list[str] = Field(default_factory=list, max_length=100)
    discovery_outcome: CandidateVerificationOutcome | None = None

    @model_validator(mode="after")
    def batch_times_must_be_ordered(self) -> "FetchBatch":
        if self.schema_version == "1.0" and (
            self.discovery_outcome is not None
            or any(item.formal_document is not None for item in self.records)
        ):
            raise ValueError("formal document provenance requires fetch batch 1.1")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")
        if any(item.source_id != self.source_id for item in self.records):
            raise ValueError("every record must match the batch source_id")
        if any(item.rights_snapshot_hash != self.rights_snapshot_hash for item in self.records):
            raise ValueError("every record must match the batch rights snapshot")
        return self


class SourceIngestionReadState(ContractModel):
    """Internal source-local cursor state; never expose it on public boundaries."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    stream: str = Field(min_length=1, max_length=200)
    cursor_version: int = Field(ge=0)
    provider_cursor: str | None = Field(default=None, max_length=4096, repr=False)
    business_watermark: AwareDatetime | None = Field(default=None, repr=False)
    last_success_at: AwareDatetime | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def successful_progress_must_be_ordered(self) -> "SourceIngestionReadState":
        if self.business_watermark is not None and self.last_success_at is None:
            raise ValueError("business_watermark requires last_success_at")
        if (
            self.business_watermark is not None
            and self.last_success_at is not None
            and self.business_watermark > self.last_success_at
        ):
            raise ValueError("business_watermark cannot be later than last_success_at")
        return self


class SourceIngestionLease(ContractModel):
    """Internal lease compatible with the existing R1 source-lease call shape."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    stream: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=200, repr=False)
    expected_cursor_version: int = Field(ge=0)
    lease_expires_at: AwareDatetime
    provider_cursor: str | None = Field(default=None, max_length=4096, repr=False)
    business_watermark: AwareDatetime | None = Field(default=None, repr=False)
    last_success_at: AwareDatetime | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def successful_progress_must_be_ordered(self) -> "SourceIngestionLease":
        SourceIngestionReadState(
            source_id=self.source_id,
            stream=self.stream,
            cursor_version=self.expected_cursor_version,
            provider_cursor=self.provider_cursor,
            business_watermark=self.business_watermark,
            last_success_at=self.last_success_at,
        )
        return self

    def as_read_state(self) -> SourceIngestionReadState:
        """Project the acquired lease to its immutable source read state."""

        return SourceIngestionReadState(
            source_id=self.source_id,
            stream=self.stream,
            cursor_version=self.expected_cursor_version,
            provider_cursor=self.provider_cursor,
            business_watermark=self.business_watermark,
            last_success_at=self.last_success_at,
        )


class RightsCheck(ContractModel):
    """In-process authorization request kept stable for RightsPolicyPort callers."""

    context: OperationContext
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    operation: RightsOperation
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    content_category: str = Field(min_length=1, max_length=100)
    permitted_use: RightsPermittedUse = RightsPermittedUse.NON_DISPLAY


class RightsDecision(ContractModel):
    """In-process fail-closed rights decision with a stable reason code."""

    allowed: bool
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    decided_at: AwareDatetime
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    retention_until: AwareDatetime | None = None


class RightsCheckV1_1(RightsCheck):
    """Versioned 1.1 wire contract for one source-operation rights check."""

    schema_version: Literal["1.1"] = "1.1"
    content_category: RightsContentCategory
    permitted_use: RightsPermittedUse


class RightsDecisionV1_1(RightsDecision):
    """Versioned 1.1 wire contract for a fail-closed rights decision."""

    schema_version: Literal["1.1"] = "1.1"
