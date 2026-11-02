"""Strongly typed normalized A-share documents produced after source ingestion."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import EntityRef, InstrumentRef, MoneyValue, OperationContext
from stock_assistant.contracts.events import EvidenceRef
from stock_assistant.contracts.ingestion import ContentTier, FactEligibility, SourceRecord


class NormalizedContentKind(StrEnum):
    EXCHANGE_ANNOUNCEMENT = "exchange_announcement"
    FINANCIAL_REPORT = "financial_report"
    BROKER_RESEARCH = "broker_research"
    FINANCIAL_NEWS = "financial_news"
    SOCIAL_SENTIMENT_AGGREGATE = "social_sentiment_aggregate"


class StatementScope(StrEnum):
    CONSOLIDATED = "consolidated"
    PARENT_COMPANY = "parent_company"
    NOT_APPLICABLE = "not_applicable"


class ReportingPeriodKind(StrEnum):
    FIRST_QUARTER = "first_quarter"
    HALF_YEAR = "half_year"
    THIRD_QUARTER = "third_quarter"
    ANNUAL = "annual"
    OTHER = "other"


class AuditOpinion(StrEnum):
    UNQUALIFIED = "unqualified"
    QUALIFIED = "qualified"
    ADVERSE = "adverse"
    DISCLAIMER = "disclaimer"
    UNAUDITED = "unaudited"
    UNKNOWN = "unknown"


class ResearchRating(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    OUTPERFORM = "outperform"
    NEUTRAL = "neutral"
    UNDERPERFORM = "underperform"
    SELL = "sell"
    NOT_RATED = "not_rated"
    UNKNOWN = "unknown"


class NormalizedContentBase(ContractModel):
    """Common bitemporal identity retained by every normalized content revision."""

    schema_version: Literal["1.0"] = "1.0"
    content_id: UUID = Field(default_factory=uuid4)
    source_record_id: UUID
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    external_id: str = Field(min_length=1, max_length=500)
    content_kind: NormalizedContentKind
    content_tier: ContentTier
    fact_eligibility: FactEligibility
    authority: float = Field(ge=0, le=1, allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=500)
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime
    revision: int = Field(ge=1)
    instruments: list[InstrumentRef] = Field(default_factory=list, max_length=1000)
    entities: list[EntityRef] = Field(default_factory=list, max_length=1000)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=100)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rights_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def knowledge_timeline_and_evidence_must_be_consistent(self) -> "NormalizedContentBase":
        if self.first_seen_at < self.published_at:
            raise ValueError("first_seen_at cannot be earlier than published_at")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("normalized content evidence IDs must be unique")
        if any(item.first_seen_at > self.revision_known_at for item in self.evidence):
            raise ValueError("content revision cannot use evidence learned later")
        return self


class ExchangeAnnouncement(NormalizedContentBase):
    content_kind: Literal[NormalizedContentKind.EXCHANGE_ANNOUNCEMENT]
    content_tier: Literal[ContentTier.STATUTORY_DISCLOSURE]
    fact_eligibility: Literal[FactEligibility.COMPANY_FACT]
    exchange_announcement_id: str = Field(min_length=1, max_length=300)
    exchange: str = Field(pattern=r"^[A-Z0-9_]{2,32}$")
    announcement_category: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    document_number: str | None = Field(default=None, max_length=200)
    report_period_end: date | None = None
    corrects_content_id: UUID | None = None


class FinancialMetric(ContractModel):
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    label: str = Field(min_length=1, max_length=200)
    value: DecimalString
    unit: str = Field(min_length=1, max_length=50)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period_start: date
    period_end: date
    comparative: bool = False
    restated: bool = False
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def metric_period_must_be_ordered(self) -> "FinancialMetric":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot be earlier than period_start")
        return self


class FinancialReport(NormalizedContentBase):
    content_kind: Literal[NormalizedContentKind.FINANCIAL_REPORT]
    content_tier: Literal[ContentTier.STATUTORY_DISCLOSURE]
    fact_eligibility: Literal[FactEligibility.COMPANY_FACT]
    company_id: UUID
    reporting_period: ReportingPeriodKind
    period_start: date
    period_end: date
    fiscal_year: int = Field(ge=1990, le=9999)
    statement_scope: StatementScope
    audit_opinion: AuditOpinion
    presentation_currency: str = Field(pattern=r"^[A-Z]{3}$")
    metrics: list[FinancialMetric] = Field(min_length=1, max_length=2000)
    restates_content_id: UUID | None = None

    @model_validator(mode="after")
    def report_period_and_metrics_must_be_consistent(self) -> "FinancialReport":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot be earlier than period_start")
        metric_ids = [(item.metric_id, item.period_start, item.period_end) for item in self.metrics]
        if len(set(metric_ids)) != len(metric_ids):
            raise ValueError("financial metrics must be unique per period")
        evidence_ids = {item.evidence_id for item in self.evidence}
        referenced = {item_id for metric in self.metrics for item_id in metric.evidence_ids}
        if not referenced.issubset(evidence_ids):
            raise ValueError("financial metrics reference unattached evidence")
        return self


class BrokerResearchReport(NormalizedContentBase):
    content_kind: Literal[NormalizedContentKind.BROKER_RESEARCH]
    content_tier: Literal[ContentTier.LICENSED_BROKER_RESEARCH]
    fact_eligibility: Literal[FactEligibility.CONTEXT_ONLY]
    institution_id: str = Field(min_length=1, max_length=200)
    institution_name: str = Field(min_length=1, max_length=300)
    analyst_names: list[str] = Field(default_factory=list, max_length=50)
    rating: ResearchRating
    original_rating: str | None = Field(default=None, max_length=100)
    previous_rating: ResearchRating | None = None
    target_price_low: MoneyValue | None = None
    target_price_high: MoneyValue | None = None
    forecast_horizon_end: date | None = None
    summary: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def target_price_range_must_be_consistent(self) -> "BrokerResearchReport":
        if (self.target_price_low is None) != (self.target_price_high is None):
            raise ValueError("target price bounds must be provided together")
        if self.target_price_low is not None and self.target_price_high is not None:
            if self.target_price_low.currency != self.target_price_high.currency:
                raise ValueError("target price bounds must use the same currency")
            if self.target_price_high.amount < self.target_price_low.amount:
                raise ValueError("target_price_high cannot be lower than target_price_low")
        return self


class FinancialNewsArticle(NormalizedContentBase):
    content_kind: Literal[NormalizedContentKind.FINANCIAL_NEWS]
    content_tier: Literal[ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA]
    fact_eligibility: Literal[FactEligibility.CONTEXT_ONLY]
    publisher_id: str = Field(min_length=1, max_length=200)
    publisher_name: str = Field(min_length=1, max_length=300)
    byline: str | None = Field(default=None, max_length=300)
    normalized_summary: str = Field(min_length=1, max_length=5000)
    topics: list[str] = Field(default_factory=list, max_length=100)
    canonical_cluster_key: str = Field(min_length=16, max_length=200)


class SocialSentimentAggregate(NormalizedContentBase):
    """Aggregate-only social signal; individual posts and identities are forbidden."""

    content_kind: Literal[NormalizedContentKind.SOCIAL_SENTIMENT_AGGREGATE]
    content_tier: Literal[ContentTier.SOCIAL_AGGREGATE]
    fact_eligibility: Literal[FactEligibility.SENTIMENT_ONLY]
    window_started_at: AwareDatetime
    window_ended_at: AwareDatetime
    sample_size: int = Field(ge=1)
    sentiment_score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    positive_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    negative_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    neutral_share: float = Field(ge=0, le=1, allow_inf_nan=False)
    aggregation_method: str = Field(min_length=1, max_length=200)
    aggregation_version: str = Field(min_length=1, max_length=200)
    contains_individual_content: Literal[False] = False
    contains_personal_identifiers: Literal[False] = False

    @model_validator(mode="after")
    def aggregate_window_and_shares_must_be_consistent(self) -> "SocialSentimentAggregate":
        if self.window_ended_at <= self.window_started_at:
            raise ValueError("window_ended_at must be later than window_started_at")
        if self.revision_known_at < self.window_ended_at:
            raise ValueError("sentiment cannot be known before its aggregation window ends")
        if abs(self.positive_share + self.negative_share + self.neutral_share - 1.0) > 1e-6:
            raise ValueError("sentiment shares must sum to one")
        return self


NormalizedContent = Annotated[
    ExchangeAnnouncement
    | FinancialReport
    | BrokerResearchReport
    | FinancialNewsArticle
    | SocialSentimentAggregate,
    Field(discriminator="content_kind"),
]


class ContentNormalizationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    source_record: SourceRecord

    @model_validator(mode="after")
    def record_must_be_visible_at_cutoff(self) -> "ContentNormalizationRequest":
        if self.source_record.first_seen_at > self.context.as_of:
            raise ValueError("source record cannot be first seen after context.as_of")
        return self


class ContentNormalizationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    source_record_id: UUID
    items: list[NormalizedContent] = Field(default_factory=list, max_length=20)
    normalized_at: AwareDatetime
    normalizer_version: str = Field(min_length=1, max_length=200)
    ignored_reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def result_shape_must_be_consistent(self) -> "ContentNormalizationResult":
        if not self.items and self.ignored_reason_code is None:
            raise ValueError("empty normalization results require ignored_reason_code")
        if self.items and self.ignored_reason_code is not None:
            raise ValueError("ignored_reason_code is only valid for empty results")
        if any(item.source_record_id != self.source_record_id for item in self.items):
            raise ValueError("normalized items must retain source_record_id")
        if any(item.revision_known_at > self.normalized_at for item in self.items):
            raise ValueError("normalized items cannot be known after normalized_at")
        return self
