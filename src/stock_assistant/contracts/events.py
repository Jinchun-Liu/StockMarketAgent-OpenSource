"""Canonical event contract used from ingestion through notification."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    is_fact_eligibility_compatible,
)


class EventStatus(StrEnum):
    RUMOR = "rumor"
    ANNOUNCED = "announced"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    CORRECTED = "corrected"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ImpactHorizon(StrEnum):
    INTRADAY = "intraday"
    DAYS = "days"
    WEEKS = "weeks"
    LONG_TERM = "long_term"
    UNKNOWN = "unknown"


class EvidenceRef(ContractModel):
    """Pointer to immutable source material stored outside prompts."""

    evidence_id: UUID = Field(default_factory=uuid4)
    source_id: str = Field(min_length=1, max_length=100)
    source_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=500)
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    object_key: str | None = Field(default=None, max_length=1024)
    license_id: str = Field(min_length=1, max_length=100)
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    content_tier: ContentTier
    fact_eligibility: FactEligibility

    @model_validator(mode="after")
    def eligibility_must_match_content_tier(self) -> "EvidenceRef":
        if self.first_seen_at < self.published_at:
            raise ValueError("first_seen_at cannot be earlier than published_at")
        if not is_fact_eligibility_compatible(self.content_tier, self.fact_eligibility):
            raise ValueError("fact eligibility is incompatible with the content tier")
        return self


class EventFact(ContractModel):
    """A normalized claim with a required evidence pointer."""

    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=50)
    period: str | None = Field(default=None, max_length=100)
    evidence_id: UUID
    evidence_span: str = Field(min_length=1, max_length=500)


class MarketEvent(ContractModel):
    """Versioned market event shared by memory, briefing and forecasting."""

    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID = Field(default_factory=uuid4)
    cluster_id: UUID = Field(default_factory=uuid4)
    revision: int = Field(default=1, ge=1)
    security_ids: list[str] = Field(default_factory=list, max_length=100)
    company_ids: list[str] = Field(default_factory=list, max_length=100)
    region_ids: list[str] = Field(default_factory=list, max_length=100)
    event_type: str = Field(min_length=1, max_length=100)
    event_status: EventStatus
    direction: Direction
    materiality: float = Field(ge=0, le=1)
    impact_horizon: ImpactHorizon
    confidence: float = Field(ge=0, le=1)
    event_time: AwareDatetime | None = None
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime
    facts: list[EventFact] = Field(default_factory=list, max_length=100)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=50)
    extractor_version: str = Field(min_length=1, max_length=100)
    rule_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def facts_must_reference_attached_evidence(self) -> "MarketEvent":
        evidence_ids = {item.evidence_id for item in self.evidence}
        missing = {fact.evidence_id for fact in self.facts} - evidence_ids
        if missing:
            raise ValueError(f"facts reference missing evidence: {sorted(map(str, missing))}")
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        fact_eligible = {
            FactEligibility.COMPANY_FACT,
            FactEligibility.POLICY_FACT,
            FactEligibility.MARKET_OBSERVATION,
        }
        if any(
            evidence_by_id[fact.evidence_id].fact_eligibility not in fact_eligible
            for fact in self.facts
        ):
            raise ValueError("event facts require fact-eligible evidence")
        if self.first_seen_at < self.published_at:
            # Clock skew and delayed publisher metadata occur; future publication does not.
            raise ValueError("first_seen_at cannot be earlier than published_at")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        if any(item.first_seen_at > self.revision_known_at for item in self.evidence):
            raise ValueError("event revision cannot use evidence learned after revision_known_at")
        return self
