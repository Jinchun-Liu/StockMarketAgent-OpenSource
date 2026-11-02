"""Ports for calendars, quotes, sector heat and fair polling rotation."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.sectors import (
    ExchangeCalendarQuery,
    ExchangeCalendarResult,
    MarketQuoteBatch,
    MarketQuoteQuery,
    SectorCatalogQuery,
    SectorCatalogResult,
    SectorConstituentPage,
    SectorConstituentQuery,
    SectorHeatQuery,
    SectorHeatResult,
    SectorRotationPlan,
    SectorRotationRequest,
    SectorVisitState,
    SectorVisitWriteRequest,
)


@runtime_checkable
class TradingCalendarPort(Protocol):
    async def get_calendar(self, request: ExchangeCalendarQuery) -> ExchangeCalendarResult: ...


@runtime_checkable
class MarketQuotePort(Protocol):
    async def get_quotes(self, request: MarketQuoteQuery) -> MarketQuoteBatch: ...


@runtime_checkable
class SectorCatalogPort(Protocol):
    async def query(self, request: SectorCatalogQuery) -> SectorCatalogResult: ...

    async def query_constituents(
        self, request: SectorConstituentQuery
    ) -> SectorConstituentPage: ...


@runtime_checkable
class SectorHeatPort(Protocol):
    async def calculate(self, request: SectorHeatQuery) -> SectorHeatResult: ...


@runtime_checkable
class FairSectorRotationPort(Protocol):
    """Create deterministic schedules balancing heat with starvation prevention."""

    def plan(self, request: SectorRotationRequest) -> SectorRotationPlan: ...


@runtime_checkable
class SectorVisitStatePort(Protocol):
    async def record(self, request: SectorVisitWriteRequest) -> SectorVisitState: ...
