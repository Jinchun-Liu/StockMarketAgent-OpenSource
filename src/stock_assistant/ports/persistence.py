"""Persistence ports; database implementations remain in platform adapters."""

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from stock_assistant.contracts.behavior import (
    BehaviorPressureAssessmentWriteRequest,
    BehaviorPressureAssessmentWriteResult,
    BehaviorPressurePage,
    BehaviorPressureQuery,
    BehaviorTransitionStateLookup,
    BehaviorTransitionStateLookupResult,
    BehaviorTransitionStateWriteRequest,
    BehaviorTransitionStateWriteResult,
    HistoricalAnalogCandidatePage,
    HistoricalAnalogCandidateQuery,
)
from stock_assistant.contracts.common import CursorPage
from stock_assistant.contracts.events import MarketEvent
from stock_assistant.contracts.global_events import GlobalEventCatalogSlice
from stock_assistant.contracts.market_data import (
    MarketDataRevisionAppendRequest,
    MarketDataRevisionAppendResult,
    MarketDataRevisionPage,
    MarketDataRevisionQuery,
)
from stock_assistant.contracts.persistence import (
    ActiveTargetLookupRequest,
    ActiveTargetLookupResult,
    ActiveTargetWriteRequest,
    ActiveTargetWriteResult,
    MarketEventLookupRequest,
    MarketEventLookupResult,
    MarketEventQuery,
    MarketEventQueryV1_1,
    MarketEventUpsertRequest,
    MarketEventUpsertResult,
    PortfolioLookupRequest,
    PortfolioLookupResult,
    PortfolioSnapshotWriteRequest,
    PortfolioSnapshotWriteResult,
)


@runtime_checkable
class UnitOfWorkPort(Protocol):
    """Async transaction boundary owned by an application use case."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@runtime_checkable
class MarketEventRepositoryPort(Protocol):
    async def get_event(self, request: MarketEventLookupRequest) -> MarketEventLookupResult: ...

    async def upsert_event(self, request: MarketEventUpsertRequest) -> MarketEventUpsertResult: ...

    async def list_events(self, request: MarketEventQuery) -> CursorPage[MarketEvent]: ...

    async def list_events_by_industry(
        self, request: MarketEventQueryV1_1
    ) -> CursorPage[MarketEvent]: ...

    async def get_catalog_slice(self, request: MarketEventQueryV1_1) -> GlobalEventCatalogSlice: ...


@runtime_checkable
class MarketDataRepositoryPort(Protocol):
    """Append-only PIT market data; adapters never leak vendor or ORM types."""

    async def append_revisions(
        self, request: MarketDataRevisionAppendRequest
    ) -> MarketDataRevisionAppendResult: ...

    async def query_revisions(self, request: MarketDataRevisionQuery) -> MarketDataRevisionPage: ...


@runtime_checkable
class BehaviorPressureRepositoryPort(Protocol):
    """Persist and query immutable behavior-pressure research assessments."""

    async def save_assessment(
        self, request: BehaviorPressureAssessmentWriteRequest
    ) -> BehaviorPressureAssessmentWriteResult: ...

    async def query_assessments(self, request: BehaviorPressureQuery) -> BehaviorPressurePage: ...

    async def query_analog_candidates(
        self, request: HistoricalAnalogCandidateQuery
    ) -> HistoricalAnalogCandidatePage: ...

    async def get_transition_state(
        self, request: BehaviorTransitionStateLookup
    ) -> BehaviorTransitionStateLookupResult: ...

    async def save_transition_state(
        self, request: BehaviorTransitionStateWriteRequest
    ) -> BehaviorTransitionStateWriteResult: ...


@runtime_checkable
class PortfolioRepositoryPort(Protocol):
    async def get_latest(self, request: PortfolioLookupRequest) -> PortfolioLookupResult: ...

    async def save_snapshot(
        self, request: PortfolioSnapshotWriteRequest
    ) -> PortfolioSnapshotWriteResult: ...

    async def get_active_target(
        self, request: ActiveTargetLookupRequest
    ) -> ActiveTargetLookupResult: ...

    async def save_active_target(
        self, request: ActiveTargetWriteRequest
    ) -> ActiveTargetWriteResult: ...
