"""Ports for authorized, incremental market-information ingestion."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.discovery import RobotsOriginStop
from stock_assistant.contracts.formal_disclosure import CandidateVerificationOutcome
from stock_assistant.contracts.ingestion import (
    FetchBatch,
    FetchRequest,
    RightsCheck,
    RightsDecision,
    SourceDescriptor,
    SourceRegistryLookup,
    SourceRegistryLookupResult,
    SourceRegistryQuery,
    SourceRegistryResult,
)


class GovernedWebSourceError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        robots_origin_stop: RobotsOriginStop | None = None,
        discovery_outcome: CandidateVerificationOutcome | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.robots_origin_stop = robots_origin_stop
        self.discovery_outcome = discovery_outcome


@runtime_checkable
class SourceConnectorPort(Protocol):
    """One authorized source adapter with opaque incremental cursors."""

    @property
    def descriptor(self) -> SourceDescriptor: ...

    async def fetch(self, request: FetchRequest) -> FetchBatch: ...


@runtime_checkable
class SourceRegistryPort(Protocol):
    """Discover configured sources without importing concrete adapters."""

    async def get_source(self, request: SourceRegistryLookup) -> SourceRegistryLookupResult: ...

    async def list_sources(self, request: SourceRegistryQuery) -> SourceRegistryResult: ...


@runtime_checkable
class RightsPolicyPort(Protocol):
    """Fail-closed authorization for fetch, retention, embedding and training."""

    async def authorize(self, check: RightsCheck) -> RightsDecision: ...
