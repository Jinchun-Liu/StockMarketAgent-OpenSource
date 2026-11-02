"""Ports for the three-layer memory architecture and change comparison."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.memory import (
    EpisodicMemoryWriteRequest,
    MemoryClearNamespaceRequest,
    MemoryClearNamespaceResult,
    MemoryCompareRequest,
    MemoryComparison,
    MemoryDeleteRequest,
    MemoryDeleteResult,
    MemoryLookupRequest,
    MemoryLookupResult,
    MemoryQuery,
    MemoryQueryResult,
    MemoryRecord,
    SemanticRuleMatchRequest,
    SemanticRuleMatchResult,
    SemanticRuleQuery,
    SemanticRuleQueryResult,
    ShortTermMemoryWriteRequest,
)


@runtime_checkable
class ShortTermMemoryPort(Protocol):
    """Session-scoped state and temporary deduplication cache."""

    async def put(self, request: ShortTermMemoryWriteRequest) -> MemoryRecord: ...

    async def get(self, request: MemoryLookupRequest) -> MemoryLookupResult: ...

    async def clear_namespace(
        self, request: MemoryClearNamespaceRequest
    ) -> MemoryClearNamespaceResult: ...


@runtime_checkable
class EpisodicMemoryPort(Protocol):
    """Durable recent events, predictions and historical recommendations."""

    async def put(self, request: EpisodicMemoryWriteRequest) -> MemoryRecord: ...

    async def query(self, query: MemoryQuery) -> MemoryQueryResult: ...

    async def delete(self, request: MemoryDeleteRequest) -> MemoryDeleteResult: ...


@runtime_checkable
class SemanticMemoryPort(Protocol):
    """Versioned rules and their explainable matches."""

    async def list_rules(self, query: SemanticRuleQuery) -> SemanticRuleQueryResult: ...

    async def match(self, request: SemanticRuleMatchRequest) -> SemanticRuleMatchResult: ...


@runtime_checkable
class MemoryComparisonPort(Protocol):
    """Classify new, changed, corrected and duplicate information."""

    async def compare(self, request: MemoryCompareRequest) -> MemoryComparison: ...
