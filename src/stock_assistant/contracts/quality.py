"""Transparent source-quality scoring and filtering contracts."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import EntityRef, OperationContext
from stock_assistant.contracts.content import NormalizedContent
from stock_assistant.contracts.ingestion import ContentTier, FactEligibility


class QualityFactorName(StrEnum):
    AUTHORITY = "authority"
    RELEVANCE = "relevance"
    FRESHNESS = "freshness"
    CORROBORATION = "corroboration"
    COMPLETENESS = "completeness"
    CONFLICT_PENALTY = "conflict_penalty"


class ContentDisposition(StrEnum):
    RETAIN_AND_NOTIFY = "retain_and_notify"
    RETAIN_ONLY = "retain_only"
    FILTER_LOW_QUALITY = "filter_low_quality"
    BLOCKED_RIGHTS = "blocked_rights"


class QualityFactor(ContractModel):
    name: QualityFactorName
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    weight: float = Field(ge=-1, le=1, allow_inf_nan=False)
    contribution: float = Field(ge=-1, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def contribution_must_match_score_and_weight(self) -> "QualityFactor":
        if abs(self.contribution - self.score * self.weight) > 1e-6:
            raise ValueError("contribution must equal score multiplied by weight")
        if self.name is QualityFactorName.CONFLICT_PENALTY and self.weight > 0:
            raise ValueError("conflict_penalty weight cannot be positive")
        if self.name is not QualityFactorName.CONFLICT_PENALTY and self.weight < 0:
            raise ValueError("only conflict_penalty may use a negative weight")
        return self


class QualityAssessmentRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    content: NormalizedContent
    target: EntityRef | None = None
    scoring_policy_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    scoring_policy_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def content_must_be_visible_at_cutoff(self) -> "QualityAssessmentRequest":
        if self.content.revision_known_at > self.context.as_of:
            raise ValueError("quality scoring cannot use content learned after context.as_of")
        return self


class QualityAssessment(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    assessment_id: UUID = Field(default_factory=uuid4)
    content_id: UUID
    target: EntityRef | None = None
    content_tier: ContentTier
    fact_eligibility: FactEligibility
    factors: list[QualityFactor] = Field(min_length=6, max_length=6)
    total_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    disposition: ContentDisposition
    fact_eligible: bool
    may_trigger_notification_alone: bool
    scoring_policy_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    scoring_policy_version: str = Field(min_length=1, max_length=100)
    assessed_at: AwareDatetime
    reason_codes: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def assessment_must_follow_source_safety_rules(self) -> "QualityAssessment":
        names = [item.name for item in self.factors]
        if set(names) != set(QualityFactorName) or len(set(names)) != len(names):
            raise ValueError("quality assessment requires each quality factor exactly once")
        expected_total = max(0.0, min(1.0, sum(item.contribution for item in self.factors)))
        if abs(self.total_score - expected_total) > 1e-6:
            raise ValueError("total_score must equal the clamped factor contribution sum")
        direct_fact = {
            FactEligibility.COMPANY_FACT,
            FactEligibility.POLICY_FACT,
            FactEligibility.MARKET_OBSERVATION,
        }
        if self.fact_eligible != (self.fact_eligibility in direct_fact):
            raise ValueError("fact_eligible must match the source fact eligibility")
        if self.content_tier is ContentTier.SOCIAL_AGGREGATE:
            if self.fact_eligible or self.may_trigger_notification_alone:
                raise ValueError("social aggregates cannot establish facts or trigger alone")
        authoritative_never_drop = {
            ContentTier.STATUTORY_DISCLOSURE,
            ContentTier.REGULATOR,
            ContentTier.ISSUER_OFFICIAL,
            ContentTier.OFFICIAL_POLICY,
        }
        if (
            self.content_tier in authoritative_never_drop
            and self.disposition is ContentDisposition.FILTER_LOW_QUALITY
        ):
            raise ValueError("authoritative facts cannot be dropped for a low quality score")
        return self


class QualityAssessmentResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    assessment: QualityAssessment
    as_of: AwareDatetime

    @model_validator(mode="after")
    def assessment_cannot_be_created_after_result_cutoff(self) -> "QualityAssessmentResult":
        if self.assessment.assessed_at > self.as_of:
            raise ValueError("assessment cannot be later than result as_of")
        return self
