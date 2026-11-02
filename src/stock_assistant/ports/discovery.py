"""Ports for controlled source discovery and receipt persistence."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from stock_assistant.contracts.discovery import (
    ResearchWebRetrievalReceipt,
    ResearchWebRetrievalReceiptWriteResult,
    SourceDiscoveryQuery,
    SourceDiscoveryResult,
)


@runtime_checkable
class SourceDiscoveryPort(Protocol):
    """Discover candidate public URLs without treating search output as evidence."""

    async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult: ...


@runtime_checkable
class ResearchWebRetrievalReceiptRepositoryPort(Protocol):
    """Persist only hashes and lifecycle metadata for model-assisted discovery."""

    async def save(
        self, receipt: ResearchWebRetrievalReceipt
    ) -> ResearchWebRetrievalReceiptWriteResult: ...

    async def purge_expired(self, *, now: datetime) -> int: ...


__all__ = ["ResearchWebRetrievalReceiptRepositoryPort", "SourceDiscoveryPort"]
