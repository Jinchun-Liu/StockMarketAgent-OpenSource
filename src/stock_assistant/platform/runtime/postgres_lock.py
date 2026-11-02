"""Session-scoped PostgreSQL advisory lock for one-shot coordinator exclusion."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class CoordinatorAlreadyRunning(RuntimeError):
    """Another task owns the bounded refresh window."""


@asynccontextmanager
async def postgres_advisory_lock(
    engine: AsyncEngine,
    *,
    lock_id: int,
) -> AsyncIterator[None]:
    """Hold one dedicated database session until the operation has terminated."""

    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        if acquired is not True:
            raise CoordinatorAlreadyRunning("REFRESH_COORDINATOR_ALREADY_RUNNING")
        try:
            yield
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )


__all__ = ["CoordinatorAlreadyRunning", "postgres_advisory_lock"]
