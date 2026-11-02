"""Ports that keep quantitative, text-signal and fusion models replaceable."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.forecasting import (
    ForecastOutcomeObservationRequest,
    ForecastOutcomeObservationResult,
    ForecastRequest,
    FusedForecast,
    FusionRequest,
    QuantitativeForecast,
    TextSignalBatch,
    TextSignalRequest,
)


@runtime_checkable
class QuantitativeForecastPort(Protocol):
    @property
    def model_version(self) -> str: ...

    async def predict(self, request: ForecastRequest) -> QuantitativeForecast: ...


@runtime_checkable
class TextSignalPort(Protocol):
    @property
    def model_version(self) -> str: ...

    async def analyze(self, request: TextSignalRequest) -> TextSignalBatch: ...


@runtime_checkable
class ForecastFusionPort(Protocol):
    @property
    def model_version(self) -> str: ...

    async def fuse(self, request: FusionRequest) -> FusedForecast: ...


@runtime_checkable
class ForecastOutcomePort(Protocol):
    async def observe(
        self, request: ForecastOutcomeObservationRequest
    ) -> ForecastOutcomeObservationResult: ...
