"""Portfolio and watchlist contracts with exact quantities and costs."""

from enum import StrEnum
from typing import Final, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import (
    InstrumentRef,
    MoneyValue,
    MutationContext,
    OperationContext,
)

R1_FIXED_TARGET_INSTRUMENT_ID: Final = "XSHG:603986"
R1_FIXED_TARGET_SYMBOL: Final = "603986.SH"
R1_FIXED_TARGET_EXCHANGE: Final = "XSHG"
R1_FIXED_TARGET_MARKET: Final = "CN"
R1_FIXED_TARGET_ASSET_CLASS: Final = "equity"
R1_FIXED_TARGET_CURRENCY: Final = "CNY"
R1_FIXED_TARGET_WATCHLIST_ID: Final = UUID("7840dcd4-3ffc-56c7-a562-4f9a0cbe7eef")
R1_FIXED_TARGET_WATCHLIST_REVISION: Final = 1
R1_FIXED_TARGET_INSTRUMENT: Final = InstrumentRef(
    instrument_id=R1_FIXED_TARGET_INSTRUMENT_ID,
    symbol=R1_FIXED_TARGET_SYMBOL,
    exchange=R1_FIXED_TARGET_EXCHANGE,
    market=R1_FIXED_TARGET_MARKET,
    asset_class=R1_FIXED_TARGET_ASSET_CLASS,
    currency=R1_FIXED_TARGET_CURRENCY,
)


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class PortfolioPosition(ContractModel):
    position_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    side: PositionSide = PositionSide.LONG
    quantity: DecimalString = Field(gt=0)
    available_quantity: DecimalString = Field(ge=0)
    average_cost: MoneyValue
    current_price: MoneyValue | None = None
    opened_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    tags: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def position_values_must_be_consistent(self) -> "PortfolioPosition":
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity cannot exceed quantity")
        amounts = [self.average_cost, self.current_price]
        currencies = {item.currency for item in amounts if item is not None}
        if currencies != {self.instrument.currency}:
            raise ValueError("position prices must use the instrument currency")
        if self.opened_at is not None and self.updated_at < self.opened_at:
            raise ValueError("updated_at cannot be earlier than opened_at")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("position tags must be unique")
        return self


class PortfolioSnapshot(ContractModel):
    """Point-in-time holdings input used by briefings and policy matching."""

    schema_version: Literal["1.0"] = "1.0"
    portfolio_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    as_of: AwareDatetime
    base_currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    positions: list[PortfolioPosition] = Field(default_factory=list, max_length=10_000)
    cash_balances: list[MoneyValue] = Field(default_factory=list, max_length=100)
    revision: int = Field(default=1, ge=1)
    source: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def snapshot_members_must_be_unique_and_current(self) -> "PortfolioSnapshot":
        instrument_ids = [item.instrument.instrument_id for item in self.positions]
        if len(set(instrument_ids)) != len(instrument_ids):
            raise ValueError("portfolio positions must have unique instruments")
        currencies = [item.currency for item in self.cash_balances]
        if len(set(currencies)) != len(currencies):
            raise ValueError("cash balances must have unique currencies")
        if any(item.updated_at > self.as_of for item in self.positions):
            raise ValueError("position updated_at cannot be later than snapshot as_of")
        return self


class PortfolioWriteRequest(ContractModel):
    context: MutationContext
    snapshot: PortfolioSnapshot
    expected_revision: int | None = Field(default=None, ge=1)


class PortfolioQuery(ContractModel):
    context: OperationContext
    portfolio_id: UUID | None = None
    instrument_ids: list[str] = Field(default_factory=list, max_length=10_000)
    include_closed: bool = False


class WatchlistEntry(ContractModel):
    instrument: InstrumentRef
    added_at: AwareDatetime
    note: str | None = Field(default=None, max_length=1000)
    alert_enabled: bool = True
    tags: list[str] = Field(default_factory=list, max_length=50)


class Watchlist(ContractModel):
    watchlist_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    entries: list[WatchlistEntry] = Field(default_factory=list, max_length=10_000)
    revision: int = Field(default=1, ge=1)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def entries_must_be_unique(self) -> "Watchlist":
        ids = [entry.instrument.instrument_id for entry in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("watchlist entries must have unique instruments")
        return self


class ActiveTargetRevisionRef(ContractModel):
    """Immutable reference captured by a job before it leaves the scheduler process."""

    schema_version: Literal["1.0"] = "1.0"
    watchlist_id: UUID
    watchlist_revision: int = Field(ge=1)
    instrument: InstrumentRef
    activated_at: AwareDatetime
