"""Ports for reproducible LoRA training and research-model governance."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.models import (
    DatasetQuery,
    DatasetRegistrationRequest,
    DatasetRegistryPage,
    DatasetRegistryRecord,
    ModelPromotionRequest,
    ModelPromotionResult,
    ModelQuery,
    ModelRegistrationRequest,
    ModelRegistryPage,
    ModelRegistryRecord,
    TrainingRunCancelRequest,
    TrainingRunLookup,
    TrainingRunLookupResult,
    TrainingRunRecord,
    TrainingRunRequest,
)


@runtime_checkable
class TrainingDatasetRegistryPort(Protocol):
    async def register(self, request: DatasetRegistrationRequest) -> DatasetRegistryRecord: ...

    async def query(self, request: DatasetQuery) -> DatasetRegistryPage: ...


@runtime_checkable
class LoRATrainingPort(Protocol):
    async def start(self, request: TrainingRunRequest) -> TrainingRunRecord: ...

    async def get(self, request: TrainingRunLookup) -> TrainingRunLookupResult: ...

    async def cancel(self, request: TrainingRunCancelRequest) -> TrainingRunRecord: ...


@runtime_checkable
class ModelRegistryPort(Protocol):
    async def register(self, request: ModelRegistrationRequest) -> ModelRegistryRecord: ...

    async def query(self, request: ModelQuery) -> ModelRegistryPage: ...

    async def promote(self, request: ModelPromotionRequest) -> ModelPromotionResult: ...
