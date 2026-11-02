"""Contracts for point-in-time policy discovery and portfolio exposure matching."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import EntityRef, OperationContext
from stock_assistant.contracts.events import Direction, EvidenceRef, ImpactHorizon


class PolicyAuthorityLevel(StrEnum):
    NATIONAL = "national"
    MINISTRY = "ministry"
    PROVINCIAL = "provincial"
    PREFECTURAL = "prefectural"
    COUNTY = "county"
    REGULATOR = "regulator"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    EFFECTIVE = "effective"
    AMENDED = "amended"
    REPEALED = "repealed"
    EXPIRED = "expired"


class PolicyTargetKind(StrEnum):
    INSTRUMENT = "instrument"
    COMPANY = "company"
    INDUSTRY = "industry"
    REGION = "region"
    MACRO_THEME = "macro_theme"


class PolicyAuthority(ContractModel):
    authority_id: UUID
    name: str = Field(min_length=1, max_length=300)
    level: PolicyAuthorityLevel
    region_id: UUID | None = None


class PolicyTarget(ContractModel):
    target: EntityRef
    target_kind: PolicyTargetKind
    explicit: bool
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)


class PolicyMeasure(ContractModel):
    measure_id: UUID
    measure_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    summary: str = Field(min_length=1, max_length=2000)
    direction: Direction
    impact_horizon: ImpactHorizon
    targets: list[PolicyTarget] = Field(min_length=1, max_length=500)
    effective_from: AwareDatetime | None = None
    effective_until: AwareDatetime | None = None
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def effective_range_must_be_ordered(self) -> "PolicyMeasure":
        if self.effective_from is not None and self.effective_until is not None:
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be later than effective_from")
        return self


class PolicyDocument(ContractModel):
    """Normalized official policy metadata and evidence, excluding unrestricted full text."""

    schema_version: Literal["1.0"] = "1.0"
    policy_id: UUID
    document_number: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    authority: PolicyAuthority
    status: PolicyStatus
    summary: str = Field(min_length=1, max_length=5000)
    published_at: AwareDatetime
    effective_from: AwareDatetime | None = None
    effective_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    revised_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    revision_known_at: AwareDatetime
    topics: list[str] = Field(default_factory=list, max_length=100)
    measures: list[PolicyMeasure] = Field(default_factory=list, max_length=500)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=100)
    extractor_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def document_timeline_and_evidence_must_be_consistent(self) -> "PolicyDocument":
        if self.first_seen_at < self.published_at:
            raise ValueError("first_seen_at cannot be earlier than published_at")
        if self.effective_from is not None and self.effective_until is not None:
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be later than effective_from")
        if self.revised_at is not None and self.revised_at < self.published_at:
            raise ValueError("revised_at cannot be earlier than published_at")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        if self.revised_at is not None and self.revision_known_at < self.revised_at:
            raise ValueError("revision_known_at cannot be earlier than revised_at")
        if any(item.first_seen_at > self.revision_known_at for item in self.evidence):
            raise ValueError("policy revision cannot use evidence learned later")
        evidence_ids = {item.evidence_id for item in self.evidence}
        referenced = {
            evidence_id for measure in self.measures for evidence_id in measure.evidence_ids
        }
        referenced.update(
            evidence_id
            for measure in self.measures
            for target in measure.targets
            for evidence_id in target.evidence_ids
        )
        if not referenced.issubset(evidence_ids):
            raise ValueError("policy measures reference unattached evidence")
        return self


class PolicyQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    authority_levels: list[PolicyAuthorityLevel] = Field(default_factory=list, max_length=20)
    authority_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    region_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    industry_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    company_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    instrument_ids: list[str] = Field(default_factory=list, max_length=1000)
    topics: list[str] = Field(default_factory=list, max_length=100)
    published_since: AwareDatetime | None = None
    published_until: AwareDatetime | None = None
    statuses: list[PolicyStatus] = Field(default_factory=list, max_length=20)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def published_range_must_be_ordered(self) -> "PolicyQuery":
        if self.published_since is not None and self.published_until is not None:
            if self.published_until < self.published_since:
                raise ValueError("published_until cannot be earlier than published_since")
        if self.published_until is not None and self.published_until > self.context.as_of:
            raise ValueError("published_until cannot be later than context.as_of")
        return self


class PolicySearchResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    policies: list[PolicyDocument] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False
    source_gaps: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def result_must_be_point_in_time_safe(self) -> "PolicySearchResult":
        if any(item.revision_known_at > self.as_of for item in self.policies):
            raise ValueError("result cannot include policy revisions known after as_of")
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        return self


class PolicyExposureTarget(ContractModel):
    target: EntityRef
    target_kind: PolicyTargetKind
    weight: float = Field(default=1, ge=0, le=1)
    related_instrument_ids: list[str] = Field(default_factory=list, max_length=1000)


class PolicyMatchRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    targets: list[PolicyExposureTarget] = Field(min_length=1, max_length=10_000)
    policy_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    minimum_relevance: float = Field(default=0.4, ge=0, le=1)
    include_indirect_matches: bool = True
    limit: int = Field(default=100, ge=1, le=1000)


class PolicyMatch(ContractModel):
    policy_id: UUID
    target: PolicyExposureTarget
    matched_measure_ids: list[UUID] = Field(min_length=1, max_length=500)
    direction: Direction
    impact_horizon: ImpactHorizon
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=500)


class PolicyMatchResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    matches: list[PolicyMatch] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime
    matcher_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)
    degraded: bool = False
    source_gaps: list[str] = Field(default_factory=list, max_length=100)
