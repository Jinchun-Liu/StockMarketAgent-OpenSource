"""Frozen R1 contracts for deterministic attention and behavior research indices."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext

ATTENTION_RISK_FORMULA = "attention-risk.v1"
BEHAVIOR_PRESSURE_FORMULA = "behavior-pressure-research.v1"
BEHAVIOR_COMPUTE_TASK = "stock_assistant.behavior.compute.v1"
BEHAVIOR_BAND_TRANSITION_EVENT = "com.stockassistant.behavior.band_transition.v1"
R1_INSTRUMENT_ID = "XSHG:603986"

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Score = Annotated[int, Field(ge=0, le=100)]


def _is_r1_instrument(instrument: InstrumentRef) -> bool:
    return (
        instrument.instrument_id,
        instrument.symbol,
        instrument.exchange,
        instrument.market,
        instrument.asset_class,
        instrument.currency,
    ) == (R1_INSTRUMENT_ID, "603986", "XSHG", "CN", "equity", "CNY")


def _attention_band(score: int) -> AttentionRiskBand:
    if score <= 19:
        return AttentionRiskBand.CALM
    if score <= 39:
        return AttentionRiskBand.NOTICE
    if score <= 59:
        return AttentionRiskBand.WARMING
    if score <= 79:
        return AttentionRiskBand.TENSE
    return AttentionRiskBand.HIGH_ALERT


def _pressure_band(score: int) -> PressureBand:
    if score <= 39:
        return PressureBand.STABLE
    if score <= 59:
        return PressureBand.WARMING
    if score <= 74:
        return PressureBand.ATTENTION
    if score <= 89:
        return PressureBand.CROWDED
    return PressureBand.FRAGILE


class IndexAvailability(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    ABSTAINED = "abstained"


class AttentionRiskBand(StrEnum):
    CALM = "calm"
    NOTICE = "notice"
    WARMING = "warming"
    TENSE = "tense"
    HIGH_ALERT = "high_alert"


class AttentionRiskCategoryName(StrEnum):
    ANNOUNCEMENTS = "announcements"
    FINANCIAL = "financial"
    MARKET = "market"
    COMPANY_INDUSTRY = "company_industry"
    MACRO = "macro"


_ATTENTION_WEIGHTS = {
    AttentionRiskCategoryName.ANNOUNCEMENTS: Decimal("0.25"),
    AttentionRiskCategoryName.FINANCIAL: Decimal("0.25"),
    AttentionRiskCategoryName.MARKET: Decimal("0.20"),
    AttentionRiskCategoryName.COMPANY_INDUSTRY: Decimal("0.20"),
    AttentionRiskCategoryName.MACRO: Decimal("0.10"),
}


class AttentionRiskCategory(ContractModel):
    category: AttentionRiskCategoryName
    score: Score | None
    new_count: int = Field(default=0, ge=0)
    update_count: int = Field(default=0, ge=0)
    correction_count: int = Field(default=0, ge=0)
    contradiction_count: int = Field(default=0, ge=0)
    available: bool

    @model_validator(mode="after")
    def availability_must_match_score(self) -> AttentionRiskCategory:
        if self.available != (self.score is not None):
            raise ValueError("available must match whether category score exists")
        expected = min(
            100,
            10 * self.new_count
            + 15 * self.update_count
            + 25 * self.correction_count
            + 35 * self.contradiction_count,
        )
        if self.available and self.score != expected:
            raise ValueError("attention category score must match attention-risk.v1")
        return self


class IndexExplanation(ContractModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    text: str = Field(min_length=1, max_length=240)


class AttentionRiskIndex(ContractModel):
    formula_version: Literal["attention-risk.v1"] = "attention-risk.v1"
    score: Score | None
    band: AttentionRiskBand | None
    availability: IndexAvailability
    as_of: AwareDatetime
    categories: list[AttentionRiskCategory] = Field(min_length=5, max_length=5)
    drivers: list[IndexExplanation] = Field(default_factory=list, max_length=3)
    mitigators: list[IndexExplanation] = Field(default_factory=list, max_length=2)
    available_category_count: int = Field(ge=0, le=5)
    completeness_percent: Score
    disclaimer: str = "越高代表越需要核实信息，不代表上涨或下跌。"  # noqa: RUF001

    @model_validator(mode="after")
    def score_and_categories_must_be_consistent(self) -> AttentionRiskIndex:
        names = [item.category for item in self.categories]
        if len(set(names)) != 5 or set(names) != set(AttentionRiskCategoryName):
            raise ValueError("attention risk requires each of the five categories exactly once")
        actual_available = sum(item.available for item in self.categories)
        if actual_available != self.available_category_count:
            raise ValueError("available_category_count must match categories")
        if (self.score is None) != (self.band is None):
            raise ValueError("attention score and band must both exist or both be empty")
        if actual_available < 3 and (self.score is not None or self.band is not None):
            raise ValueError("attention risk abstains with fewer than three categories")
        if self.availability is IndexAvailability.ABSTAINED:
            if self.score is not None:
                raise ValueError("abstained attention risk cannot carry a score")
        elif self.score is None:
            raise ValueError("ready or degraded attention risk requires a score")
        if self.score is not None and self.band is not _attention_band(self.score):
            raise ValueError("attention risk band must match the frozen thresholds")
        if self.score is not None:
            available_items = [item for item in self.categories if item.score is not None]
            weight_total = sum(
                (_ATTENTION_WEIGHTS[item.category] for item in available_items),
                start=Decimal(0),
            )
            weighted_total = sum(
                (
                    Decimal(item.score) * _ATTENTION_WEIGHTS[item.category]
                    for item in available_items
                    if item.score is not None
                ),
                start=Decimal(0),
            )
            expected_score = int(
                (weighted_total / weight_total).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            if self.score != expected_score:
                raise ValueError("attention total must match available weighted categories")
        if self.completeness_percent != actual_available * 20:
            raise ValueError("attention completeness must match available categories")
        return self


class PressureMode(StrEnum):
    POST_CLOSE = "post_close"
    INTRADAY_10M = "intraday_10m"


class PressureBand(StrEnum):
    STABLE = "stable"
    WARMING = "warming"
    ATTENTION = "attention"
    CROWDED = "crowded"
    FRAGILE = "fragile"


def behavior_transition_idempotency_key(
    *,
    instrument_id: str,
    mode: PressureMode,
    trading_date: date,
    target_band: PressureBand,
) -> str:
    """Return the stable business key, independent of delivery attempts and message IDs."""

    canonical = "\n".join(
        (
            BEHAVIOR_BAND_TRANSITION_EVENT,
            instrument_id,
            mode.value,
            trading_date.isoformat(),
            target_band.value,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PressureFacetName(StrEnum):
    ACTIVITY_DIVERGENCE = "activity_divergence"
    EXTENSION_CROWDING = "extension_crowding"
    LIQUIDITY_FRAGILITY = "liquidity_fragility"
    VOLATILITY_TAIL = "volatility_tail"
    MARKET_INDUSTRY_DIVERGENCE = "market_industry_divergence"


class PressureClusterName(StrEnum):
    ACTIVITY_ANOMALY = "activity_anomaly"
    CLOSE_PATH_REJECTION = "close_path_rejection"
    REFERENCE_COST_EXTENSION = "reference_cost_extension"
    SHORT_PATH_EXTENSION = "short_path_extension"
    PRICE_IMPACT = "price_impact"
    SPREAD_PROXY = "spread_proxy"
    VOLATILITY_EXPANSION = "volatility_expansion"
    DOWNSIDE_TAIL_ASYMMETRY = "downside_tail_asymmetry"
    BREADTH_BACKGROUND = "breadth_background"
    IDIOSYNCRATIC_DIVERGENCE = "idiosyncratic_divergence"


class StructureDimensionName(StrEnum):
    TREND_ORGANIZATION = "trend_organization"
    POSITION_RECOVERY = "position_recovery"
    TRADING_PARTICIPATION = "trading_participation"
    VOLATILITY_ENVIRONMENT = "volatility_environment"
    INTRADAY_ACCEPTANCE = "intraday_acceptance"
    RELATIVE_ENVIRONMENT = "relative_environment"


class StructureAvailability(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    ABSTAINED = "abstained"
    NOT_APPLICABLE = "not_applicable"


class PressureComponent(ContractModel):
    score: Score | None
    availability: IndexAvailability
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def component_state_must_be_consistent(self) -> PressureComponent:
        if self.availability is IndexAvailability.ABSTAINED:
            if self.score is not None or self.reason_code is None:
                raise ValueError("abstained component requires no score and a reason code")
        elif self.score is None:
            raise ValueError("ready or degraded component requires a score")
        return self


class PressureFacet(PressureComponent):
    facet: PressureFacetName


class PressureCluster(PressureComponent):
    cluster: PressureClusterName
    inherited_from_post_close: bool = False


class MarketStructureDimension(ContractModel):
    dimension: StructureDimensionName
    availability: StructureAvailability
    state: str | None = Field(default=None, min_length=1, max_length=80)
    explanation: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def state_must_match_availability(self) -> MarketStructureDimension:
        state_required = self.availability in {
            StructureAvailability.READY,
            StructureAvailability.DEGRADED,
        }
        if state_required != (self.state is not None):
            raise ValueError("structure state must match availability")
        return self


class HistoricalOutcomeRange(ContractModel):
    metric: Literal["return_1d", "return_3d", "industry_excess", "mae", "mfe"]
    q1: DecimalString
    median: DecimalString
    q3: DecimalString

    @model_validator(mode="after")
    def quartiles_must_be_ordered(self) -> HistoricalOutcomeRange:
        if not self.q1 <= self.median <= self.q3:
            raise ValueError("historical quartiles must be ordered")
        return self


class HistoricalAnalogState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    INSUFFICIENT = "insufficient"
    MATURITY_PENDING = "maturity_pending"
    INCOMPARABLE = "incomparable"
    FAILED = "failed"


class HistoricalAnalogSummary(ContractModel):
    state: HistoricalAnalogState
    sample_count: int = Field(ge=0, le=200)
    ranges: list[HistoricalOutcomeRange] = Field(default_factory=list, max_length=5)
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    reason: str | None = Field(default=None, min_length=1, max_length=240)
    selection_as_of: AwareDatetime
    feature_set_version: Literal["behavior-analog-14d.v1"] = "behavior-analog-14d.v1"
    selection_semantics_hash: Sha256

    @model_validator(mode="after")
    def range_display_requires_fifty_samples(self) -> HistoricalAnalogSummary:
        metrics = [item.metric for item in self.ranges]
        ready = self.state is HistoricalAnalogState.READY
        if ready and (
            self.sample_count < 50
            or set(metrics)
            != {
                "return_1d",
                "return_3d",
                "industry_excess",
                "mae",
                "mfe",
            }
        ):
            raise ValueError("ready historical ranges require fifty samples and five metrics")
        if len(metrics) != len(set(metrics)):
            raise ValueError("historical range metrics must be unique")
        if not ready and self.ranges:
            raise ValueError("unavailable historical analogs cannot expose ranges")
        if not ready and (self.reason_code is None or self.reason is None):
            raise ValueError("non-ready historical analogs require a safe reason")
        return self


class PitLineage(ContractModel):
    data_manifest_hash: Sha256
    rights_snapshot_hash: Sha256
    comparison_semantics_hash: Sha256
    max_input_revision_known_at: AwareDatetime
    assessment_revision_known_at: AwareDatetime
    comparable_instrument_day_count: int = Field(ge=0, le=10_000)


class EmpiricalRankMapping(ContractModel):
    """Auditable raw-composite to empirical mid-rank mapping for the research score."""

    raw_total: Score
    population_count: int = Field(ge=1000, le=10_000)
    less_count: int = Field(ge=0)
    equal_count: int = Field(ge=1)
    mapped_score: Score
    reference_mapping_version: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    reference_population_hash: Sha256

    @model_validator(mode="after")
    def mapped_score_must_match_mid_rank(self) -> EmpiricalRankMapping:
        if self.less_count + self.equal_count > self.population_count:
            raise ValueError("mid-rank counts cannot exceed the reference population")
        percentile = (
            Decimal(100)
            * (Decimal(self.less_count) + Decimal("0.5") * Decimal(self.equal_count))
            / Decimal(self.population_count)
        )
        expected = int(percentile.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if self.mapped_score != expected:
            raise ValueError("mapped score must match the frozen empirical mid-rank formula")
        return self


class BehaviorPressureSnapshotV1_0(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    assessment_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    research_index: Literal[True] = True
    formula_version: Literal["behavior-pressure-research.v1"] = "behavior-pressure-research.v1"
    mode: PressureMode
    score: Score | None
    band: PressureBand | None
    availability: IndexAvailability
    score_mapping: EmpiricalRankMapping | None = None
    window_start: AwareDatetime
    window_end: AwareDatetime
    as_of: AwareDatetime
    generated_at: AwareDatetime
    facets: list[PressureFacet] = Field(min_length=5, max_length=5)
    clusters: list[PressureCluster] = Field(min_length=10, max_length=10)
    structure_dimensions: list[MarketStructureDimension] = Field(min_length=6, max_length=6)
    drivers: list[IndexExplanation] = Field(default_factory=list, max_length=3)
    mitigators: list[IndexExplanation] = Field(default_factory=list, max_length=2)
    historical_analogs: HistoricalAnalogSummary | None = None
    completeness_percent: Score
    pit_lineage: PitLineage
    disclaimer: str = "研究指数，不是下跌概率、买卖建议或目标价。"  # noqa: RUF001

    @model_validator(mode="after")
    def snapshot_must_match_frozen_research_semantics(self) -> BehaviorPressureSnapshotV1_0:
        if not _is_r1_instrument(self.instrument):
            raise ValueError("R1 behavior snapshot requires the canonical XSHG:603986 identity")
        if not self.window_start < self.window_end <= self.as_of <= self.generated_at:
            raise ValueError("behavior snapshot timeline is invalid")
        if self.pit_lineage.max_input_revision_known_at > self.as_of:
            raise ValueError("behavior inputs cannot be known after as_of")
        if self.pit_lineage.assessment_revision_known_at < self.generated_at:
            raise ValueError("assessment knowledge time cannot precede generation")
        if {item.facet for item in self.facets} != set(PressureFacetName):
            raise ValueError("behavior snapshot requires each facet exactly once")
        if {item.cluster for item in self.clusters} != set(PressureClusterName):
            raise ValueError("behavior snapshot requires each cluster exactly once")
        if {item.dimension for item in self.structure_dimensions} != set(StructureDimensionName):
            raise ValueError("behavior snapshot requires each structure dimension exactly once")
        if (self.score is None) != (self.band is None):
            raise ValueError("behavior score and band must both exist or both be empty")
        scored_clusters = sum(item.score is not None for item in self.clusters)
        expected_completeness = scored_clusters * 10
        if self.completeness_percent != expected_completeness:
            raise ValueError("behavior completeness must match available clusters")
        if self.availability is IndexAvailability.ABSTAINED:
            if self.score is not None or self.score_mapping is not None:
                raise ValueError("abstained behavior pressure cannot carry a score mapping")
        elif self.score is None or scored_clusters != 10:
            raise ValueError("scored behavior pressure requires all ten core clusters")
        elif self.score_mapping is None:
            raise ValueError("scored behavior pressure requires an empirical rank mapping")
        if self.score is not None and self.band is not _pressure_band(self.score):
            raise ValueError("behavior pressure band must match the frozen thresholds")
        if self.score is not None and self.score_mapping is not None:
            cluster_scores = [item.score for item in self.clusters]
            if any(value is None for value in cluster_scores):
                raise ValueError("ranked behavior pressure requires complete cluster scores")
            raw_total = int(
                (
                    sum((Decimal(value) for value in cluster_scores if value is not None))
                    / Decimal(10)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            if self.score_mapping.raw_total != raw_total:
                raise ValueError("raw pressure total must equal the ten-cluster mean")
            if self.score_mapping.mapped_score != self.score:
                raise ValueError("behavior score must equal the empirical mapped score")
            if (
                self.score_mapping.population_count
                != self.pit_lineage.comparable_instrument_day_count
            ):
                raise ValueError("rank population must match PIT comparable instrument-days")
        if self.pit_lineage.comparable_instrument_day_count < 1000 and self.score is not None:
            raise ValueError("behavior score requires at least 1,000 comparable instrument-days")
        intraday = next(
            item
            for item in self.structure_dimensions
            if item.dimension is StructureDimensionName.INTRADAY_ACCEPTANCE
        )
        if self.mode is PressureMode.POST_CLOSE:
            if intraday.availability is not StructureAvailability.NOT_APPLICABLE:
                raise ValueError("post-close intraday acceptance must be not_applicable")
            if self.historical_analogs is None:
                raise ValueError("post-close snapshot requires historical analog state")
            if self.historical_analogs.selection_as_of > self.as_of:
                raise ValueError("historical analog selection cannot be later than snapshot as_of")
            if any(item.inherited_from_post_close for item in self.clusters):
                raise ValueError("post-close clusters cannot be inherited")
        elif self.historical_analogs is not None:
            raise ValueError("intraday snapshots cannot carry historical analogs")
        else:
            expected_inherited = {
                PressureClusterName.REFERENCE_COST_EXTENSION,
                PressureClusterName.SHORT_PATH_EXTENSION,
                PressureClusterName.PRICE_IMPACT,
                PressureClusterName.SPREAD_PROXY,
                PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY,
            }
            actual_inherited = {
                item.cluster for item in self.clusters if item.inherited_from_post_close
            }
            if actual_inherited != expected_inherited:
                raise ValueError("intraday mode requires the frozen five inherited clusters")
            if self.window_end - self.window_start != timedelta(minutes=10):
                raise ValueError("intraday assessments require one complete ten-minute window")
            if intraday.availability is StructureAvailability.NOT_APPLICABLE:
                raise ValueError("intraday acceptance must be applicable during intraday mode")
        return self


class BehaviorPressureSummary(ContractModel):
    research_index: Literal[True] = True
    mode: PressureMode
    score: Score | None
    band: PressureBand | None
    availability: IndexAvailability
    score_mapping: EmpiricalRankMapping | None = None
    as_of: AwareDatetime
    facets: list[PressureFacet] = Field(min_length=5, max_length=5)
    structure_dimensions: list[MarketStructureDimension] = Field(min_length=6, max_length=6)
    drivers: list[IndexExplanation] = Field(default_factory=list, max_length=3)
    mitigators: list[IndexExplanation] = Field(default_factory=list, max_length=2)
    historical_analogs: HistoricalAnalogSummary | None = None
    completeness_percent: Score
    disclaimer: str = "研究指数，不是下跌概率、买卖建议或目标价。"  # noqa: RUF001

    @model_validator(mode="after")
    def summary_must_match_snapshot_semantics(self) -> BehaviorPressureSummary:
        if {item.facet for item in self.facets} != set(PressureFacetName):
            raise ValueError("behavior summary requires each facet exactly once")
        if {item.dimension for item in self.structure_dimensions} != set(StructureDimensionName):
            raise ValueError("behavior summary requires each structure dimension exactly once")
        if (self.score is None) != (self.band is None):
            raise ValueError("behavior summary score and band must both exist or both be empty")
        if self.availability is IndexAvailability.ABSTAINED:
            if self.score is not None or self.score_mapping is not None:
                raise ValueError("abstained behavior summary cannot carry a score mapping")
        elif self.score is None:
            raise ValueError("ready or degraded behavior summary requires a score")
        elif self.score_mapping is None:
            raise ValueError("scored behavior summary requires an empirical rank mapping")
        if self.score is not None and self.band is not _pressure_band(self.score):
            raise ValueError("behavior summary band must match the frozen thresholds")
        if self.score is not None and self.score_mapping is not None:
            facet_scores = [item.score for item in self.facets]
            if any(value is None for value in facet_scores):
                raise ValueError("ranked behavior summary requires complete facet scores")
            raw_total = int(
                (
                    sum((Decimal(value) for value in facet_scores if value is not None))
                    / Decimal(5)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            if self.score_mapping.raw_total != raw_total:
                raise ValueError("summary raw total must equal the five-facet mean")
            if self.score_mapping.mapped_score != self.score:
                raise ValueError("summary score must equal the empirical mapped score")
        available_facets = sum(item.score is not None for item in self.facets)
        if self.completeness_percent != available_facets * 20:
            raise ValueError("behavior summary completeness must match available facets")
        if self.mode is PressureMode.INTRADAY_10M and self.historical_analogs is not None:
            raise ValueError("intraday summary cannot carry historical analogs")
        if (
            self.historical_analogs is not None
            and self.historical_analogs.selection_as_of > self.as_of
        ):
            raise ValueError("historical analog selection cannot be later than summary as_of")
        return self


class PlainLanguageUpdate(ContractModel):
    checked_count: int = Field(ge=0)
    succeeded_count: int = Field(ge=0)
    no_change_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    last_success_at: AwareDatetime | None = None
    summary: str = Field(min_length=1, max_length=500)
    reasons: list[IndexExplanation] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def counts_must_reconcile(self) -> PlainLanguageUpdate:
        completed = self.succeeded_count + self.no_change_count + self.unavailable_count
        if completed > self.checked_count:
            raise ValueError("plain-language update counts cannot exceed checked_count")
        return self


class InterpretationOrigin(StrEnum):
    RULES = "rules"
    DEEPSEEK_FLASH = "deepseek_flash"
    DEEPSEEK_PRO = "deepseek_pro"


class InterpretationStatus(StrEnum):
    GENERATED = "generated"
    FALLBACK = "fallback"


class ModelInterpretation(ContractModel):
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=800)
    key_points: list[str] = Field(default_factory=list, max_length=5)
    label: Literal["智能解读"] = "智能解读"
    origin: InterpretationOrigin
    status: InterpretationStatus

    @model_validator(mode="after")
    def origin_and_status_must_be_truthful(self) -> ModelInterpretation:
        if self.origin is InterpretationOrigin.RULES:
            if self.status is not InterpretationStatus.FALLBACK:
                raise ValueError("rules interpretation must be marked as fallback")
        elif self.status is not InterpretationStatus.GENERATED:
            raise ValueError("model interpretation must be marked as generated")
        return self


class BehaviorPressureQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument_id: Literal["XSHG:603986"] = "XSHG:603986"
    mode: PressureMode
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)


class BehaviorPressurePage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[BehaviorPressureSnapshotV1_0] = Field(default_factory=list, max_length=1000)
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False
    as_of: AwareDatetime

    @model_validator(mode="after")
    def page_must_be_resumable_and_point_in_time_safe(self) -> BehaviorPressurePage:
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        if any(item.generated_at > self.as_of for item in self.items):
            raise ValueError("behavior page cannot expose future assessments")
        if any(item.pit_lineage.assessment_revision_known_at > self.as_of for item in self.items):
            raise ValueError("behavior page cannot expose future assessment revisions")
        return self


class HistoricalAnalogCandidateQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument_id: Literal["XSHG:603986"] = "XSHG:603986"
    selection_as_of: AwareDatetime
    feature_set_version: Literal["behavior-analog-14d.v1"] = "behavior-analog-14d.v1"
    vector: list[float] = Field(min_length=14, max_length=14)
    limit: int = Field(default=400, ge=50, le=1000)

    @model_validator(mode="after")
    def selection_cutoff_must_be_visible(self) -> HistoricalAnalogCandidateQuery:
        if self.selection_as_of > self.context.as_of:
            raise ValueError("analog selection cannot be later than context.as_of")
        return self


class HistoricalAnalogCandidate(ContractModel):
    assessment_id: UUID
    as_of: AwareDatetime
    assessment_revision_known_at: AwareDatetime
    vector: list[float] = Field(min_length=14, max_length=14)
    approximate_distance: float = Field(ge=0)


class HistoricalAnalogCandidatePage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[HistoricalAnalogCandidate] = Field(default_factory=list, max_length=1000)
    selection_as_of: AwareDatetime

    @model_validator(mode="after")
    def candidates_must_be_visible_at_selection(self) -> HistoricalAnalogCandidatePage:
        if any(item.as_of > self.selection_as_of for item in self.items):
            raise ValueError("analog candidate cannot occur after selection cutoff")
        if any(item.assessment_revision_known_at > self.selection_as_of for item in self.items):
            raise ValueError("analog candidate cannot be known after selection cutoff")
        return self


class BehaviorTransitionStateLookup(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument_id: Literal["XSHG:603986"] = "XSHG:603986"
    mode: PressureMode
    trading_date: date


class BehaviorTransitionState(ContractModel):
    instrument_id: Literal["XSHG:603986"] = "XSHG:603986"
    mode: PressureMode
    trading_date: date
    current_band: PressureBand | None = None
    candidate_band: PressureBand | None = None
    consecutive_complete_windows: int = Field(default=0, ge=0)
    last_assessment_id: UUID | None = None
    notified_band: PressureBand | None = None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime


class BehaviorTransitionStateLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    found: bool
    state: BehaviorTransitionState | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_must_match_state(self) -> BehaviorTransitionStateLookupResult:
        if self.found != (self.state is not None):
            raise ValueError("found must reflect whether transition state exists")
        return self


class BehaviorTransitionStateWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    state: BehaviorTransitionState
    expected_revision: int | None = Field(default=None, ge=1)


class BehaviorTransitionStateWriteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    state: BehaviorTransitionState
    created: bool
    idempotent_replay: bool = False
    stored_at: AwareDatetime


class BehaviorPressureAssessmentWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    snapshot: BehaviorPressureSnapshotV1_0


class BehaviorPressureAssessmentWriteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot: BehaviorPressureSnapshotV1_0
    created: bool
    idempotent_replay: bool = False
    stored_at: AwareDatetime


class BehaviorComputeRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    instrument_id: Literal["XSHG:603986"] = "XSHG:603986"
    mode: PressureMode
    window_start: AwareDatetime
    window_end: AwareDatetime
    formula_version: Literal["behavior-pressure-research.v1"] = "behavior-pressure-research.v1"
    diagnostic_semantics_hash: Sha256
    data_manifest_hash: Sha256
    rights_snapshot_hash: Sha256

    @model_validator(mode="after")
    def compute_window_must_match_mode(self) -> BehaviorComputeRequest:
        if self.window_end > self.context.as_of or self.window_start >= self.window_end:
            raise ValueError("behavior compute window must be complete at context.as_of")
        if (
            self.mode is PressureMode.INTRADAY_10M
            and self.window_end - self.window_start != timedelta(minutes=10)
        ):
            raise ValueError("intraday compute requires one complete ten-minute window")
        return self


class BandTransitionDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


class BehaviorAssessmentLineageRef(ContractModel):
    """Immutable market-data manifest lineage for one side of a band transition."""

    assessment_id: UUID
    data_manifest_hash: Sha256
    rights_snapshot_hash: Sha256
    max_input_revision_known_at: AwareDatetime


class BehaviorBandTransitionV1(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    event_name: Literal["com.stockassistant.behavior.band_transition.v1"] = (
        "com.stockassistant.behavior.band_transition.v1"
    )
    event_id: UUID = Field(default_factory=uuid4)
    instrument_id: Literal["XSHG:603986"] = "XSHG:603986"
    mode: PressureMode
    previous_band: PressureBand
    current_band: PressureBand
    previous_assessment_id: UUID
    current_assessment_id: UUID
    direction: BandTransitionDirection
    inference_kind: Literal["system_inference"] = "system_inference"
    formula_version: Literal["behavior-pressure-research.v1"] = "behavior-pressure-research.v1"
    comparison_semantics_hash: Sha256
    market_data_lineage_refs: list[BehaviorAssessmentLineageRef] = Field(
        min_length=2,
        max_length=2,
    )
    effective_at: AwareDatetime
    consecutive_complete_windows: int = Field(ge=1)
    trading_date: date
    idempotency_key: Sha256

    @model_validator(mode="after")
    def transition_must_match_mode(self) -> BehaviorBandTransitionV1:
        if self.previous_band is self.current_band:
            raise ValueError("band transition must change band")
        if self.previous_assessment_id == self.current_assessment_id:
            raise ValueError("band transition requires distinct assessments")
        if {item.assessment_id for item in self.market_data_lineage_refs} != {
            self.previous_assessment_id,
            self.current_assessment_id,
        }:
            raise ValueError("market-data lineage must cover both transition assessments")
        order = list(PressureBand)
        expected_direction = (
            BandTransitionDirection.HIGHER
            if order.index(self.current_band) > order.index(self.previous_band)
            else BandTransitionDirection.LOWER
        )
        if self.direction is not expected_direction:
            raise ValueError("transition direction must match previous and current band")
        if self.mode is PressureMode.INTRADAY_10M:
            if self.consecutive_complete_windows < 2:
                raise ValueError("intraday transition requires two complete windows")
        elif self.consecutive_complete_windows != 1:
            raise ValueError("post-close transition uses one confirmed assessment")
        expected_key = behavior_transition_idempotency_key(
            instrument_id=self.instrument_id,
            mode=self.mode,
            trading_date=self.trading_date,
            target_band=self.current_band,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("transition idempotency key must match the frozen business key")
        return self
