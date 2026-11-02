"""Bounded fresh-connection health check for startup-injected DB credentials."""

import asyncio
import importlib
from typing import Final, cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from stock_assistant.contracts.common import ComponentHealth, HealthStatus, OperationContext

_TIMEOUT_SECONDS: Final = 5.0
# The installed asyncpg exception classes have no typing package. Keep that
# vendor boundary local; callers consume only ComponentHealth.
_ASYNCPG = importlib.import_module("asyncpg")
_POSTGRES_ERROR = cast(type[Exception], _ASYNCPG.PostgresError)
_INTERFACE_ERROR = cast(type[Exception], _ASYNCPG.InterfaceError)


class PostgresHealthCheck:
    """Implement HealthCheckPort without trusting previously authenticated pools."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def check_health(self, context: OperationContext) -> ComponentHealth:
        del context
        engine = create_async_engine(
            self._database_url,
            poolclass=NullPool,
            connect_args={"timeout": 3, "command_timeout": 3},
        )
        status = HealthStatus.HEALTHY
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                try:
                    async with engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                finally:
                    await engine.dispose()
        except (SQLAlchemyError, _POSTGRES_ERROR, _INTERFACE_ERROR, OSError, TimeoutError):
            status = HealthStatus.UNAVAILABLE
        return ComponentHealth(
            component="postgresql",
            status=status,
            reason_code="DATABASE_UNAVAILABLE" if status is HealthStatus.UNAVAILABLE else None,
        )
