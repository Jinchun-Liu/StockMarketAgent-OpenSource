"""Ports for briefing generation, deterministic validation and persistence."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.briefing_workflows import (
    BriefingGenerationRequest,
    BriefingGenerationResult,
    BriefingPage,
    BriefingQuery,
    BriefingStorageRecord,
    BriefingStoreRequest,
    BriefingValidationRequest,
    BriefingValidationResult,
)


@runtime_checkable
class BriefingGeneratorPort(Protocol):
    async def generate(self, request: BriefingGenerationRequest) -> BriefingGenerationResult: ...


@runtime_checkable
class BriefingValidationPort(Protocol):
    def validate(self, request: BriefingValidationRequest) -> BriefingValidationResult: ...


@runtime_checkable
class BriefingRepositoryPort(Protocol):
    async def store(self, request: BriefingStoreRequest) -> BriefingStorageRecord: ...

    async def query(self, request: BriefingQuery) -> BriefingPage: ...
