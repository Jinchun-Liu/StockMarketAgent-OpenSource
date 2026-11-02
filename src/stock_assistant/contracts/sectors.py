"""Trading-calendar, quote, sector-heat and fair-rotation contracts."""

from datetime import date
from enum import StrEnum
from itertools import pairwise
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import (
    InstrumentRef,
    MoneyValue,
    MutationContext,
    OperationContext,
)


class TradingDayStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"
    UNSCHEDULED_CLOSURE = "unscheduled_closure"


class TradingPhase(StrEnum):
    PRE_OPEN = "pre_open"
    OPENING_AUCTION = "opening_auction"
    CONTINUOUS = "continuous"
    BREAK = "break"
    CLOSING_AUCTION = "closing_auction"
    POST_CLOSE = "post_close"


class QuoteStatus(StrEnum):
    NORMAL = "normal"
    HALTED = "halted"
    SUSPENDED = "suspended"
    DELAYED = "delayed"
    CLOSED = "closed"


class SectorKind(StrEnum):
    INDUSTRY = "industry"
    CONCEPT = "concept"
    REGION = "region"
    STYLE = "style"
    INDEX = "index"
    CUSTOM = "custom"


class HeatDirection(StrEnum):
    HEATING = "heating"
    COOLING = "cooling"
    STABLE = "stable"
    UNKNOWN = "unknown"


class HeatMetricName(StrEnum):
    PRICE_CHANGE = "price_change"
    TURNOVER = "turnover"
    VOLUME_RATIO = "volume_ratio"
    ADVANCE_BREADTH = "advance_breadth"
    NEWS_MATERIALITY = "news_materiality"
    POLICY_RELEVANCE = "policy_relevance"
    SENTIMENT = "sentiment"


class RotationOutcome(StrEnum):
    COMPLETED = "completed"
    DEGRADED = "degraded"
    SKIPPED = "skipped"
    FAILED = "failed"


class TradingSessionSegment(ContractModel):
    phase: TradingPhase
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "TradingSessionSegment":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class TradingDay(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    exchange: str = Field(pattern=r"^[A-Z0-9_]{2,32}$")
    trading_date: date
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    status: TradingDayStatus
    segments: list[TradingSessionSegment] = Field(default_factory=list, max_length=20)
    first_seen_at: AwareDatetime
    revised_at: AwareDatetime | None = None
    calendar_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def day_segments_must_be_consistent(self) -> "TradingDay":
        if self.status is TradingDayStatus.CLOSED and self.segments:
            raise ValueError("closed trading days cannot contain session segments")
        ordered = sorted(self.segments, key=lambda item: item.starts_at)
        for previous, current in pairwise(ordered):
            if current.starts_at < previous.ends_at:
                raise ValueError("trading session segments cannot overlap")
        if self.revised_at is not None and self.revised_at < self.first_seen_at:
            raise ValueError("revised_at cannot be earlier than first_seen_at")
        return self


class ExchangeCalendarQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    exchanges: list[str] = Field(min_length=1, max_length=100)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def date_range_must_be_ordered(self) -> "ExchangeCalendarQuery":
        if self.date_to < self.date_from:
            raise ValueError("date_to cannot be earlier than date_from")
        return self


class ExchangeCalendarResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    days: list[TradingDay] = Field(default_factory=list, max_length=10_000)
    as_of: AwareDatetime
    calendar_version: str = Field(min_length=1, max_length=200)
    degraded: bool = False
    source_gaps: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def result_must_be_point_in_time_safe(self) -> "ExchangeCalendarResult":
        if any(day.first_seen_at > self.as_of for day in self.days):
            raise ValueError("calendar result cannot contain days first seen after as_of")
        if any(day.revised_at is not None and day.revised_at > self.as_of for day in self.days):
            raise ValueError("calendar result cannot contain revisions known after as_of")
        keys = [(day.exchange, day.trading_date) for day in self.days]
        if len(set(keys)) != len(keys):
            raise ValueError("calendar result cannot contain duplicate exchange dates")
        return self


class MarketQuoteQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instruments: list[InstrumentRef] = Field(min_length=1, max_length=10_000)
    maximum_age_seconds: int = Field(default=60, ge=0, le=86_400)
    allow_delayed: bool = True


class MarketQuote(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    quote_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    quoted_at: AwareDatetime
    received_at: AwareDatetime
    status: QuoteStatus
    last_price: MoneyValue
    previous_close: MoneyValue | None = None
    open_price: MoneyValue | None = None
    high_price: MoneyValue | None = None
    low_price: MoneyValue | None = None
    volume: DecimalString = Field(ge=0)
    turnover: MoneyValue | None = None
    change_rate: DecimalString | None = None
    source_id: str = Field(min_length=1, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def quote_values_must_be_consistent(self) -> "MarketQuote":
        if self.received_at < self.quoted_at:
            raise ValueError("received_at cannot be earlier than quoted_at")
        prices = [
            self.last_price,
            self.previous_close,
            self.open_price,
            self.high_price,
            self.low_price,
            self.turnover,
        ]
        currencies = {item.currency for item in prices if item is not None}
        if currencies != {self.instrument.currency}:
            raise ValueError("all quote monetary values must use the instrument currency")
        price_values = [
            self.last_price,
            self.previous_close,
            self.open_price,
            self.high_price,
            self.low_price,
        ]
        if any(item is not None and item.amount <= 0 for item in price_values):
            raise ValueError("quote prices must be positive")
        if self.turnover is not None and self.turnover.amount < 0:
            raise ValueError("quote turnover cannot be negative")
        if self.high_price is not None and self.low_price is not None:
            if self.high_price.amount < self.low_price.amount:
                raise ValueError("high_price cannot be lower than low_price")
        return self


class MarketQuoteBatch(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    quotes: list[MarketQuote] = Field(default_factory=list, max_length=10_000)
    as_of: AwareDatetime
    source_watermark: AwareDatetime | None = None
    missing_instrument_ids: list[str] = Field(default_factory=list, max_length=10_000)
    degraded: bool = False

    @model_validator(mode="after")
    def quotes_must_be_unique_and_visible(self) -> "MarketQuoteBatch":
        ids = [item.instrument.instrument_id for item in self.quotes]
        if len(set(ids)) != len(ids):
            raise ValueError("quote batch must contain at most one quote per instrument")
        if any(item.received_at > self.as_of for item in self.quotes):
            raise ValueError("quote batch cannot contain data received after as_of")
        if self.source_watermark is not None and self.source_watermark > self.as_of:
            raise ValueError("source_watermark cannot be later than as_of")
        return self


class SectorConstituent(ContractModel):
    instrument: InstrumentRef
    weight: DecimalString | None = Field(default=None, ge=0, le=1)
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validity_must_be_ordered(self) -> "SectorConstituent":
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class SectorConstituentSnapshotRef(ContractModel):
    snapshot_id: UUID
    sector_id: UUID
    as_of: AwareDatetime
    revision_known_at: AwareDatetime
    constituent_count: int = Field(ge=0)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def revision_must_be_visible_at_snapshot_cutoff(self) -> "SectorConstituentSnapshotRef":
        if self.revision_known_at > self.as_of:
            raise ValueError("constituent revision cannot be known after snapshot as_of")
        return self


class SectorDefinition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    sector_id: UUID
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    kind: SectorKind
    taxonomy: str = Field(min_length=1, max_length=100)
    parent_sector_id: UUID | None = None
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    revision: int = Field(ge=1)
    revision_known_at: AwareDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def sector_definition_must_be_consistent(self) -> "SectorDefinition":
        if self.parent_sector_id == self.sector_id:
            raise ValueError("a sector cannot be its own parent")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        return self


class SectorCatalogQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    sector_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    kinds: list[SectorKind] = Field(default_factory=list, max_length=20)
    instrument_ids: list[str] = Field(default_factory=list, max_length=10_000)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)


class SectorCatalogResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    sectors: list[SectorDefinition] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False

    @model_validator(mode="after")
    def result_must_be_visible_at_cutoff(self) -> "SectorCatalogResult":
        if any(item.revision_known_at > self.as_of for item in self.sectors):
            raise ValueError("sector result cannot contain revisions known after as_of")
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        return self


class SectorConstituentQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    sector_id: UUID
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=500, ge=1, le=1000)


class SectorConstituentPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    sector_id: UUID
    snapshot: SectorConstituentSnapshotRef
    items: list[SectorConstituent] = Field(default_factory=list, max_length=1000)
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False

    @model_validator(mode="after")
    def constituents_must_be_unique_and_visible(self) -> "SectorConstituentPage":
        if self.snapshot.sector_id != self.sector_id:
            raise ValueError("constituent snapshot must match sector_id")
        if any(item.first_seen_at > self.snapshot.as_of for item in self.items):
            raise ValueError("constituent page cannot contain members learned after as_of")
        instrument_ids = [item.instrument.instrument_id for item in self.items]
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("constituent page cannot contain duplicate instruments")
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        return self


class HeatMetric(ContractModel):
    name: HeatMetricName
    raw_value: DecimalString
    unit: str = Field(min_length=1, max_length=50)
    normalized_score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    observed_at: AwareDatetime


class SectorHeatQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    sector_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    kinds: list[SectorKind] = Field(default_factory=list, max_length=20)
    lookback_minutes: int = Field(default=10, ge=1, le=10_080)
    metric_names: list[HeatMetricName] = Field(default_factory=lambda: list(HeatMetricName))
    limit: int = Field(default=100, ge=1, le=1000)


class SectorHeatSnapshot(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: UUID = Field(default_factory=uuid4)
    sector: SectorDefinition
    constituent_snapshot: SectorConstituentSnapshotRef
    as_of: AwareDatetime
    lookback_minutes: int = Field(ge=1, le=10_080)
    heat_score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    direction: HeatDirection
    rank: int | None = Field(default=None, ge=1)
    metrics: list[HeatMetric] = Field(min_length=1, max_length=100)
    leading_instruments: list[InstrumentRef] = Field(default_factory=list, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=1000)
    calculator_version: str = Field(min_length=1, max_length=200)
    data_watermark: AwareDatetime
    degraded: bool = False

    @model_validator(mode="after")
    def heat_snapshot_must_be_consistent(self) -> "SectorHeatSnapshot":
        if self.data_watermark > self.as_of:
            raise ValueError("data_watermark cannot be later than as_of")
        if self.sector.revision_known_at > self.as_of:
            raise ValueError("sector revision cannot be known after snapshot as_of")
        if self.constituent_snapshot.sector_id != self.sector.sector_id:
            raise ValueError("constituent snapshot must match the heat sector")
        if self.constituent_snapshot.as_of > self.as_of:
            raise ValueError("heat cannot use a future constituent snapshot")
        if any(item.observed_at > self.as_of for item in self.metrics):
            raise ValueError("heat metrics cannot be observed after snapshot as_of")
        metric_names = [item.name for item in self.metrics]
        if len(set(metric_names)) != len(metric_names):
            raise ValueError("heat metrics must be unique")
        if abs(sum(item.weight for item in self.metrics) - 1.0) > 1e-6:
            raise ValueError("heat metric weights must sum to one")
        return self


class SectorHeatResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshots: list[SectorHeatSnapshot] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime
    degraded: bool = False
    source_gaps: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def snapshots_must_share_cutoff_and_be_unique(self) -> "SectorHeatResult":
        if any(item.as_of != self.as_of for item in self.snapshots):
            raise ValueError("every heat snapshot must use the result as_of")
        sector_ids = [item.sector.sector_id for item in self.snapshots]
        if len(set(sector_ids)) != len(sector_ids):
            raise ValueError("heat result must contain at most one snapshot per sector")
        snapshot_ids = [item.snapshot_id for item in self.snapshots]
        if len(set(snapshot_ids)) != len(snapshot_ids):
            raise ValueError("heat result snapshot IDs must be unique")
        return self


class SectorVisitState(ContractModel):
    sector_id: UUID
    active_started_at: AwareDatetime | None = None
    last_completed_at: AwareDatetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    completed_visits: int = Field(default=0, ge=0)
    state_revision: int = Field(ge=1)
    revision_known_at: AwareDatetime

    @model_validator(mode="after")
    def visit_times_must_be_known_by_revision(self) -> "SectorVisitState":
        if self.active_started_at is not None and self.active_started_at > self.revision_known_at:
            raise ValueError("active_started_at cannot be later than revision_known_at")
        if self.last_completed_at is not None and self.last_completed_at > self.revision_known_at:
            raise ValueError("last_completed_at cannot be later than revision_known_at")
        return self


class RotationPolicy(ContractModel):
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    version: str = Field(min_length=1, max_length=100)
    interval_seconds: int = Field(default=600, ge=60, le=86_400)
    maximum_starvation_seconds: int = Field(default=3600, ge=60, le=604_800)
    heat_weight: float = Field(default=0.4, ge=0, le=1, allow_inf_nan=False)
    fairness_weight: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    maximum_slots: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def weights_must_sum_to_one(self) -> "RotationPolicy":
        if abs(self.heat_weight + self.fairness_weight - 1.0) > 1e-6:
            raise ValueError("heat_weight and fairness_weight must sum to one")
        return self


class SectorRotationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    trading_day: TradingDay
    candidates: list[SectorHeatSnapshot] = Field(min_length=1, max_length=10_000)
    visits: list[SectorVisitState] = Field(default_factory=list, max_length=10_000)
    policy: RotationPolicy
    schedule_from: AwareDatetime

    @model_validator(mode="after")
    def candidates_and_state_must_be_unique(self) -> "SectorRotationRequest":
        candidate_ids = [item.sector.sector_id for item in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("rotation candidates must be unique")
        visit_ids = [item.sector_id for item in self.visits]
        if len(set(visit_ids)) != len(visit_ids):
            raise ValueError("visit states must be unique")
        if self.trading_day.first_seen_at > self.context.as_of:
            raise ValueError("trading calendar cannot be first seen after context.as_of")
        if (
            self.trading_day.revised_at is not None
            and self.trading_day.revised_at > self.context.as_of
        ):
            raise ValueError("trading calendar revision cannot be known after context.as_of")
        if any(item.as_of > self.context.as_of for item in self.candidates):
            raise ValueError("rotation candidates cannot use a future cutoff")
        if any(item.revision_known_at > self.context.as_of for item in self.visits):
            raise ValueError("visit state revisions cannot be known after context.as_of")
        if self.schedule_from < self.context.as_of:
            raise ValueError("schedule_from cannot be earlier than context.as_of")
        return self


class SectorRotationSlot(ContractModel):
    sector_id: UUID
    scheduled_at: AwareDatetime
    priority_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    fairness_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    heat_score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class SectorRotationPlan(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: UUID = Field(default_factory=uuid4)
    generated_at: AwareDatetime
    valid_until: AwareDatetime
    slots: list[SectorRotationSlot] = Field(default_factory=list, max_length=1000)
    interval_seconds: int = Field(default=600, ge=60, le=86_400)
    policy_id: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=100)
    degraded: bool = False

    @model_validator(mode="after")
    def plan_must_be_ordered_and_non_repeating(self) -> "SectorRotationPlan":
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be later than generated_at")
        sector_ids = [item.sector_id for item in self.slots]
        if any(previous == current for previous, current in pairwise(sector_ids)):
            raise ValueError("adjacent rotation slots cannot repeat a sector")
        schedule = [item.scheduled_at for item in self.slots]
        if schedule != sorted(schedule):
            raise ValueError("rotation slots must be ordered by scheduled_at")
        for previous, current in pairwise(schedule):
            if (current - previous).total_seconds() < self.interval_seconds:
                raise ValueError("rotation slots must respect interval_seconds")
        if any(item < self.generated_at or item >= self.valid_until for item in schedule):
            raise ValueError("rotation slots must fall inside the plan validity window")
        return self


class SectorVisitWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    plan_id: UUID
    sector_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    outcome: RotationOutcome
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    expected_state_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def visit_timeline_must_be_consistent(self) -> "SectorVisitWriteRequest":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")
        if self.completed_at > self.context.as_of:
            raise ValueError("completed_at cannot be later than context.as_of")
        return self
