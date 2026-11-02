"""Ports for vendor-neutral point-in-time instrument master data."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.instruments import (
    InstrumentLookupRequest,
    InstrumentLookupResult,
    InstrumentSearchQuery,
    InstrumentSearchResult,
    InstrumentSearchResultV1_1,
    MasterDataQuery,
    MasterDataSnapshot,
)


@runtime_checkable
class InstrumentMasterDataPort(Protocol):
    """Resolve identifiers and query issuer metadata at the requested cutoff."""

    async def resolve(self, request: InstrumentLookupRequest) -> InstrumentLookupResult: ...

    async def search(self, request: InstrumentSearchQuery) -> InstrumentSearchResult: ...

    async def query(self, request: MasterDataQuery) -> MasterDataSnapshot: ...


@runtime_checkable
class InstrumentMasterDataV2Port(InstrumentMasterDataPort, Protocol):
    """Opt-in extension exposing explicit legal-name verification state."""

    async def search_v1_1(self, request: InstrumentSearchQuery) -> InstrumentSearchResultV1_1: ...
