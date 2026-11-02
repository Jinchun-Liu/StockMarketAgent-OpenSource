"""Ports for official-policy discovery and exposure matching."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.policies import (
    PolicyMatchRequest,
    PolicyMatchResult,
    PolicyQuery,
    PolicySearchResult,
)


@runtime_checkable
class PolicySearchPort(Protocol):
    async def search(self, request: PolicyQuery) -> PolicySearchResult: ...


@runtime_checkable
class PolicyMatchingPort(Protocol):
    async def match(self, request: PolicyMatchRequest) -> PolicyMatchResult: ...
