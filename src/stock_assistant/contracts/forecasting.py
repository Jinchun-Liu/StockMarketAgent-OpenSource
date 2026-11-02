"""Contracts for quantitative, text-signal and fused paper forecasts."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import (
    InstrumentRef,
    MoneyValue,
    MutationContext,
    OperationContext,
)
from stock_assistant.contracts.content import NormalizedContent


class SignalDirection(StrEnum):
    STRONGLY_NEGATIVE = "strongly_negative"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    STRONGLY_POSITIVE = "strongly_positive"


class OutcomeStatus(StrEnum):
    OBSERVED = "observed"
    NOT_YET_AVAILABLE = "not_yet_available"
    INVALIDATED = "invalidated"


class MarketBar(ContractModel):
    """Adjusted or raw OHLCV bar with exact decimal numeric values."""

    instrument: InstrumentRef
    interval: str = Field(pattern=r"^[1-9][0-9]*(?:s|min|h|d|w)$")
    opened_at: AwareDatetime
    closed_at: AwareDatetime
    received_at: AwareDatetime
    open_price: DecimalString = Field(gt=0)
    high_price: DecimalString = Field(gt=0)
    low_price: DecimalString = Field(gt=0)
    close_price: DecimalString = Field(gt=0)
    volume: DecimalString = Field(ge=0)
    turnover: MoneyValue | None = None
    adjusted: bool
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    data_version: str = Field(min_length=1, max_length=200)
    adjustment_version: str = Field(min_length=1, max_length=200)
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def bar_values_must_be_consistent(self) -> "MarketBar":
        if self.closed_at <= self.opened_at:
            raise ValueError("closed_at must be later than opened_at")
        if self.received_at < self.closed_at:
            raise ValueError("received_at cannot be earlier than closed_at")
        if self.high_price < max(self.open_price, self.close_price, self.low_price):
            raise ValueError("high_price must be the greatest OHLC value")
        if self.low_price > min(self.open_price, self.close_price, self.high_price):
            raise ValueError("low_price must be the least OHLC value")
        if self.turnover is not None and self.turnover.currency != self.instrument.currency:
            raise ValueError("turnover currency must match the instrument currency")
        return self


class FeatureObservation(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: DecimalString
    observed_at: AwareDatetime
    available_at: AwareDatetime
    feature_version: str = Field(min_length=1, max_length=100)
    data_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def availability_must_follow_observation(self) -> "FeatureObservation":
        if self.available_at < self.observed_at:
            raise ValueError("available_at cannot be earlier than observed_at")
        return self


class ForecastRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument: InstrumentRef
    horizon_minutes: int = Field(ge=1, le=525_600)
    bars: list[MarketBar] = Field(min_length=1, max_length=100_000)
    features: list[FeatureObservation] = Field(default_factory=list, max_length=10_000)
    event_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    data_snapshot_id: UUID
    data_manifest_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    feature_set_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def bars_must_match_instrument_and_cutoff(self) -> "ForecastRequest":
        requested_id = self.instrument.instrument_id
        if any(bar.instrument.instrument_id != requested_id for bar in self.bars):
            raise ValueError("all bars must belong to the requested instrument")
        if any(bar.received_at > self.context.as_of for bar in self.bars):
            raise ValueError("forecast input cannot contain bars received after context.as_of")
        if any(feature.available_at > self.context.as_of for feature in self.features):
            raise ValueError("forecast input cannot contain features after context.as_of")
        bar_keys = [(bar.interval, bar.opened_at, bar.closed_at) for bar in self.bars]
        if len(set(bar_keys)) != len(bar_keys):
            raise ValueError("forecast input cannot contain duplicate market bars")
        feature_keys = [(feature.name, feature.observed_at) for feature in self.features]
        if len(set(feature_keys)) != len(feature_keys):
            raise ValueError("forecast input cannot contain duplicate feature observations")
        if any(feature.feature_version != self.feature_set_version for feature in self.features):
            raise ValueError("all feature observations must match feature_set_version")
        return self


class ReturnQuantiles(ContractModel):
    """Decimal return fractions, where 0.01 represents one percent."""

    q10: DecimalString
    q50: DecimalString
    q90: DecimalString

    @model_validator(mode="after")
    def quantiles_must_be_ordered(self) -> "ReturnQuantiles":
        if not self.q10 <= self.q50 <= self.q90:
            raise ValueError("return quantiles must satisfy q10 <= q50 <= q90")
        return self


class QuantitativeForecast(ContractModel):
    prediction_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    as_of: AwareDatetime
    horizon_minutes: int = Field(ge=1, le=525_600)
    probability_up: float = Field(ge=0, le=1)
    returns: ReturnQuantiles
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    feature_set_version: str = Field(min_length=1, max_length=200)
    data_snapshot_id: UUID
    training_cutoff: AwareDatetime
    generated_at: AwareDatetime
    calibration_version: str = Field(min_length=1, max_length=200)
    code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    latency_ms: int = Field(ge=0)
    is_paper_only: Literal[True] = True

    @model_validator(mode="after")
    def training_must_precede_prediction(self) -> "QuantitativeForecast":
        if self.training_cutoff > self.as_of:
            raise ValueError("training_cutoff cannot be later than as_of")
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        return self


class TextSignal(ContractModel):
    signal_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    as_of: AwareDatetime
    direction: SignalDirection
    signed_score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    materiality: float = Field(ge=0, le=1)
    content_ids: list[UUID] = Field(min_length=1, max_length=1000)
    event_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=1000)
    model_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)
    generated_at: AwareDatetime
    is_paper_only: Literal[True] = True

    @model_validator(mode="after")
    def generation_must_follow_cutoff(self) -> "TextSignal":
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        if self.direction in {SignalDirection.POSITIVE, SignalDirection.STRONGLY_POSITIVE}:
            if self.signed_score <= 0:
                raise ValueError("positive directions require a positive signed_score")
        if self.direction in {SignalDirection.NEGATIVE, SignalDirection.STRONGLY_NEGATIVE}:
            if self.signed_score >= 0:
                raise ValueError("negative directions require a negative signed_score")
        if self.direction is SignalDirection.NEUTRAL and self.signed_score != 0:
            raise ValueError("neutral direction requires a zero signed_score")
        return self


class TextSignalRequest(ContractModel):
    """Explicit PIT evidence pack; analyzers never perform hidden repository reads."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument: InstrumentRef
    contents: list[NormalizedContent] = Field(min_length=1, max_length=1000)
    horizon_minutes: int = Field(ge=1, le=525_600)
    rule_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def contents_must_be_visible_and_relevant(self) -> "TextSignalRequest":
        if any(item.revision_known_at > self.context.as_of for item in self.contents):
            raise ValueError("text-signal content cannot be known after context.as_of")
        content_ids = [item.content_id for item in self.contents]
        if len(set(content_ids)) != len(content_ids):
            raise ValueError("text-signal content IDs must be unique")
        requested = self.instrument.instrument_id
        if any(
            item.instruments
            and requested not in {instrument.instrument_id for instrument in item.instruments}
            for item in self.contents
        ):
            raise ValueError("explicitly instrument-scoped content must match the request")
        return self


class TextSignalBatch(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    signals: list[TextSignal] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime
    generated_at: AwareDatetime
    analyzer_version: str = Field(min_length=1, max_length=200)
    degraded: bool = False
    source_gaps: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def signals_must_match_batch_cutoff(self) -> "TextSignalBatch":
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        if any(item.as_of != self.as_of for item in self.signals):
            raise ValueError("text signals must match the batch as_of")
        signal_ids = [item.signal_id for item in self.signals]
        if len(set(signal_ids)) != len(signal_ids):
            raise ValueError("text signal IDs must be unique")
        return self


class FusionRequest(ContractModel):
    context: OperationContext
    quantitative: QuantitativeForecast
    text_signals: list[TextSignal] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def all_signals_must_match_forecast(self) -> "FusionRequest":
        if self.quantitative.as_of > self.context.as_of:
            raise ValueError("quantitative forecast cannot be later than context.as_of")
        expected = self.quantitative.instrument.instrument_id
        if any(signal.instrument.instrument_id != expected for signal in self.text_signals):
            raise ValueError("all text signals must match the quantitative forecast instrument")
        if any(signal.as_of > self.context.as_of for signal in self.text_signals):
            raise ValueError("text signals cannot be later than context.as_of")
        return self


class ForecastContribution(ContractModel):
    source: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    weight: float = Field(ge=0, le=1)
    signed_adjustment: float = Field(ge=-1, le=1)
    rationale: str = Field(min_length=1, max_length=2000)


class FusedForecast(ContractModel):
    """Research-only fused output; it cannot be interpreted as an order."""

    schema_version: Literal["1.0"] = "1.0"
    prediction_id: UUID = Field(default_factory=uuid4)
    quantitative_prediction_id: UUID
    instrument: InstrumentRef
    as_of: AwareDatetime
    horizon_minutes: int = Field(ge=1, le=525_600)
    probability_up: float = Field(ge=0, le=1)
    returns: ReturnQuantiles
    confidence: float = Field(ge=0, le=1)
    contributions: list[ForecastContribution] = Field(min_length=1, max_length=100)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    fusion_model_version: str = Field(min_length=1, max_length=200)
    generated_at: AwareDatetime
    is_paper_only: Literal[True] = True

    @model_validator(mode="after")
    def contribution_weights_must_sum_to_one(self) -> "FusedForecast":
        total = sum(item.weight for item in self.contributions)
        if abs(total - 1.0) > 1e-6:
            raise ValueError("forecast contribution weights must sum to one")
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        return self


class ForecastOutcome(ContractModel):
    """Point-in-time label recorded after a paper forecast horizon closes."""

    schema_version: Literal["1.0"] = "1.0"
    prediction_id: UUID
    instrument: InstrumentRef
    prediction_as_of: AwareDatetime
    horizon_ends_at: AwareDatetime
    outcome_observed_at: AwareDatetime
    label_available_at: AwareDatetime
    benchmark_id: str = Field(min_length=1, max_length=200)
    realized_return: DecimalString
    benchmark_return: DecimalString
    excess_return: DecimalString
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    data_version: str = Field(min_length=1, max_length=200)
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def outcome_timeline_and_value_must_be_consistent(self) -> "ForecastOutcome":
        if self.horizon_ends_at <= self.prediction_as_of:
            raise ValueError("horizon_ends_at must be later than prediction_as_of")
        if self.outcome_observed_at < self.horizon_ends_at:
            raise ValueError("outcome_observed_at cannot be earlier than horizon_ends_at")
        if self.label_available_at < self.outcome_observed_at:
            raise ValueError("label_available_at cannot be earlier than outcome_observed_at")
        if self.excess_return != self.realized_return - self.benchmark_return:
            raise ValueError("excess_return must equal realized_return minus benchmark_return")
        return self


class ForecastOutcomeObservationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    prediction_id: UUID
    instrument: InstrumentRef
    prediction_as_of: AwareDatetime
    horizon_ends_at: AwareDatetime
    benchmark_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    expected_data_version: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def requested_horizon_must_follow_prediction(self) -> "ForecastOutcomeObservationRequest":
        if self.horizon_ends_at <= self.prediction_as_of:
            raise ValueError("horizon_ends_at must be later than prediction_as_of")
        return self


class ForecastOutcomeObservationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    prediction_id: UUID
    status: OutcomeStatus
    outcome: ForecastOutcome | None = None
    as_of: AwareDatetime
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    settlement_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def status_and_outcome_must_be_consistent(self) -> "ForecastOutcomeObservationResult":
        if self.status is OutcomeStatus.OBSERVED and self.outcome is None:
            raise ValueError("observed status requires an outcome")
        if self.status is not OutcomeStatus.OBSERVED and self.outcome is not None:
            raise ValueError("only observed status may contain an outcome")
        if self.outcome is not None:
            if self.outcome.prediction_id != self.prediction_id:
                raise ValueError("outcome must match prediction_id")
            if self.outcome.label_available_at > self.as_of:
                raise ValueError("outcome label cannot be available after result as_of")
        return self
