"""Port for deterministic normalization into typed A-share content."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.content import (
    ContentNormalizationRequest,
    ContentNormalizationResult,
)


@runtime_checkable
class ContentNormalizationPort(Protocol):
    async def normalize(
        self, request: ContentNormalizationRequest
    ) -> ContentNormalizationResult: ...
