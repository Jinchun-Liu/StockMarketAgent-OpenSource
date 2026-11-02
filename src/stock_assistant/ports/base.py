"""Small cross-cutting ports shared by every adapter family."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.common import ComponentHealth, OperationContext
from stock_assistant.contracts.system import CapabilityDescriptor


@runtime_checkable
class LifecyclePort(Protocol):
    """Explicit lifecycle for adapters that own network or process resources."""

    async def start(self) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class HealthCheckPort(Protocol):
    """Safe health check that never exposes credentials or raw exceptions."""

    async def check_health(self, context: OperationContext) -> ComponentHealth: ...


@runtime_checkable
class CapabilityDiscoveryPort(Protocol):
    """Runtime feature discovery for graceful degradation."""

    async def list_capabilities(self, context: OperationContext) -> list[CapabilityDescriptor]: ...
