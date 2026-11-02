"""Ports for evidence-grounded event extraction, validation and revision."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.event_workflows import (
    EventExtractionRequest,
    EventExtractionResult,
    EventRevisionRequest,
    EventRevisionResult,
    EventValidationRequest,
    EventValidationResult,
)


@runtime_checkable
class EventExtractionPort(Protocol):
    async def extract(self, request: EventExtractionRequest) -> EventExtractionResult: ...


@runtime_checkable
class EventValidationPort(Protocol):
    async def validate(self, request: EventValidationRequest) -> EventValidationResult: ...


@runtime_checkable
class EventRevisionPort(Protocol):
    async def revise(self, request: EventRevisionRequest) -> EventRevisionResult: ...
