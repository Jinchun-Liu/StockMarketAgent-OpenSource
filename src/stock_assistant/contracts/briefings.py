"""Briefing output contract."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel


class BriefingKind(StrEnum):
    PREMARKET = "premarket"
    SECTOR_ROTATION = "sector_rotation"
    POLICY = "policy"
    ON_DEMAND = "on_demand"


class BriefingChangeType(StrEnum):
    NEW = "new"
    UPDATE = "update"
    CORRECTION = "correction"
    CONTRADICTION = "contradiction"


class SourceHealthStatus(StrEnum):
    OK = "ok"
    DELAYED = "delayed"
    UNAVAILABLE = "unavailable"
    BLOCKED_RIGHTS = "blocked_rights"


class SourceHealth(ContractModel):
    source_id: str = Field(min_length=1, max_length=100)
    status: SourceHealthStatus
    last_success_at: AwareDatetime | None = None
    delay_seconds: int = Field(default=0, ge=0)
    gap_reason: str | None = Field(default=None, max_length=500)


class ForecastSummary(ContractModel):
    prediction_id: UUID
    probability_up: float = Field(ge=0, le=1)
    return_q10: float
    return_q50: float
    return_q90: float
    model_version: str = Field(min_length=1, max_length=200)
    as_of: AwareDatetime
    is_paper_only: Literal[True] = True

    @model_validator(mode="after")
    def quantiles_must_be_ordered(self) -> "ForecastSummary":
        if not self.return_q10 <= self.return_q50 <= self.return_q90:
            raise ValueError("forecast quantiles must satisfy q10 <= q50 <= q90")
        return self


class BriefingItem(ContractModel):
    headline: str = Field(min_length=1, max_length=300)
    fact_summary: str = Field(min_length=1, max_length=2000)
    inference: str | None = Field(default=None, max_length=2000)
    change_type: BriefingChangeType
    source_ids: list[str] = Field(min_length=1, max_length=50)
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    event_ids: list[UUID] = Field(min_length=1, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    is_material_change: bool
    forecast: ForecastSummary | None = None


class Briefing(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    briefing_id: UUID = Field(default_factory=uuid4)
    kind: BriefingKind
    generated_at: AwareDatetime
    as_of: AwareDatetime
    items: list[BriefingItem] = Field(default_factory=list, max_length=200)
    source_health: list[SourceHealth] = Field(default_factory=list, max_length=100)
    omitted_duplicate_count: int = Field(default=0, ge=0)
    data_delay_seconds: int = Field(ge=0)
    model_version: str
    prompt_hash: str
    rule_version: str
    disclaimer: str = "本系统仅供个人信息整理与研究，不构成证券投资建议、收益承诺或交易指令。"

    @model_validator(mode="after")
    def briefing_must_be_point_in_time_safe(self) -> "Briefing":
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        if any(item.first_seen_at > self.as_of for item in self.items):
            raise ValueError("briefing cannot contain items first seen after as_of")
        if any(
            item.forecast is not None and item.forecast.as_of > self.as_of for item in self.items
        ):
            raise ValueError("briefing cannot contain forecasts made after as_of")
        if any(
            item.last_success_at is not None and item.last_success_at > self.generated_at
            for item in self.source_health
        ):
            raise ValueError("source health cannot report a future success")
        return self
