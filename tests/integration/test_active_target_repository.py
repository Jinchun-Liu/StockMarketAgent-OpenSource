"""PostgreSQL contract checks for the fixed Active Target repository adapter."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from apps.worker import tasks
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.persistence import (
    ActiveTargetLookupRequest,
    ActiveTargetWriteRequest,
)
from stock_assistant.contracts.portfolio import ActiveTargetRevisionRef, Watchlist, WatchlistEntry
from stock_assistant.modules.portfolio.active_target import (
    R1_WATCHLIST_ID,
    FixedActiveTargetService,
)
from stock_assistant.platform.db.portfolio import (
    PortfolioIdempotencyConflictError,
    PortfolioRevisionConflictError,
    PostgresPortfolioRepository,
)

pytestmark = pytest.mark.integration


async def _reset_fixed_r1_target(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM active_target_write_receipts WHERE watchlist_id = :watchlist_id"),
            {"watchlist_id": R1_WATCHLIST_ID},
        )
        await connection.execute(
            text("DELETE FROM active_target_revisions WHERE watchlist_id = :watchlist_id"),
            {"watchlist_id": R1_WATCHLIST_ID},
        )


async def _fixed_r1_target_snapshot(
    engine: AsyncEngine,
) -> tuple[int, datetime | None, list[str]]:
    async with engine.connect() as connection:
        revision_row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) AS revision_count, max(activated_at) AS activated_at
                          FROM active_target_revisions
                         WHERE watchlist_id = :watchlist_id
                        """
                    ),
                    {"watchlist_id": R1_WATCHLIST_ID},
                )
            )
            .mappings()
            .one()
        )
        receipt_keys = list(
            (
                await connection.scalars(
                    text(
                        """
                        SELECT idempotency_key
                          FROM active_target_write_receipts
                         WHERE watchlist_id = :watchlist_id
                         ORDER BY idempotency_key
                        """
                    ),
                    {"watchlist_id": R1_WATCHLIST_ID},
                )
            ).all()
        )
    return (
        int(revision_row["revision_count"]),
        cast("datetime | None", revision_row["activated_at"]),
        [cast(str, item) for item in receipt_keys],
    )


def _database_url() -> str:
    url = os.environ.get("ACTIVE_TARGET_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("ACTIVE_TARGET_TEST_DATABASE_URL is required for PostgreSQL integration")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("active-target integration requires a postgresql+asyncpg URL")
    if parsed.host not in {"127.0.0.1", "localhost", "postgres"}:
        pytest.fail("active-target integration only accepts an isolated local/CI PostgreSQL host")
    if parsed.database is None or not parsed.database.startswith(
        "stock_assistant_active_target_test"
    ):
        pytest.fail("database name must start with stock_assistant_active_target_test")
    return url


def _upgrade_with_connection(connection: Connection, config: Config) -> None:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def _upgrade(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", "infra/migrations")
    config.set_main_option("prepend_sys_path", ". src")
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_upgrade_with_connection, config)
    finally:
        await engine.dispose()


def _request(
    *,
    revision: int,
    updated_at: datetime,
    idempotency_key: str,
    watchlist_id: UUID,
    instrument: InstrumentRef,
    expected_revision: int | None,
) -> ActiveTargetWriteRequest:
    return ActiveTargetWriteRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=updated_at + timedelta(seconds=1),
            requested_at=updated_at,
            idempotency_key=idempotency_key,
        ),
        watchlist=Watchlist(
            watchlist_id=watchlist_id,
            name="固定主动标的",
            revision=revision,
            updated_at=updated_at,
            entries=[
                WatchlistEntry(
                    instrument=instrument,
                    added_at=updated_at,
                    alert_enabled=True,
                )
            ],
        ),
        active_target=ActiveTargetRevisionRef(
            schema_version="1.0",
            watchlist_id=watchlist_id,
            watchlist_revision=revision,
            instrument=instrument,
            activated_at=updated_at,
        ),
        expected_revision=expected_revision,
    )


async def test_active_target_write_replay_conflict_and_point_in_time_lookup() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresPortfolioRepository(engine)
    watchlist_id = uuid4()
    run_key = watchlist_id.hex
    first_instrument = InstrumentRef(
        instrument_id="XSHG:603986",
        symbol="603986.SH",
        exchange="XSHG",
        market="CN",
    )
    second_instrument = InstrumentRef(
        instrument_id="SZSE:000001",
        symbol="000001",
        exchange="SZSE",
        market="CN",
    )
    second_time = datetime.now(UTC) - timedelta(minutes=1)
    first_time = second_time - timedelta(minutes=1)
    first = _request(
        revision=1,
        updated_at=first_time,
        idempotency_key=f"active-target-create-{run_key}",
        watchlist_id=watchlist_id,
        instrument=first_instrument,
        expected_revision=None,
    )
    second = _request(
        revision=2,
        updated_at=second_time,
        idempotency_key=f"active-target-update-{run_key}",
        watchlist_id=watchlist_id,
        instrument=second_instrument,
        expected_revision=1,
    )
    try:
        async with engine.connect() as connection:
            migrated_database = await connection.scalar(text("SELECT current_database()"))
        assert migrated_database == make_url(database_url).database

        created = await repository.save_active_target(first)
        assert created.created is True
        assert created.changed is True
        replays = [await repository.save_active_target(first) for _ in range(100)]
        assert all(replay.idempotent_replay is True for replay in replays)
        assert all(replay.stored_at == created.stored_at for replay in replays)

        async with engine.connect() as connection:
            historical_as_of = await connection.scalar(text("SELECT clock_timestamp()"))
        assert isinstance(historical_as_of, datetime)
        assert created.stored_at < historical_as_of

        conflicting_key = first.model_copy(
            update={
                "context": first.context.model_copy(
                    update={"idempotency_key": f"active-target-update-{run_key}"}
                )
            }
        )
        updated = await repository.save_active_target(second)
        assert historical_as_of <= updated.stored_at
        with pytest.raises(PortfolioIdempotencyConflictError):
            await repository.save_active_target(conflicting_key)

        historical = await repository.get_active_target(
            ActiveTargetLookupRequest(
                schema_version="1.0",
                context=OperationContext(
                    schema_version="1.0",
                    as_of=historical_as_of,
                    requested_at=historical_as_of - timedelta(seconds=1),
                ),
                watchlist_id=watchlist_id,
            )
        )
        assert historical.found is True
        assert historical.watchlist is not None
        assert historical.watchlist.revision == 1
        assert historical.active_target is not None
        assert historical.active_target.instrument == first_instrument

        latest = await repository.get_active_target(
            ActiveTargetLookupRequest(
                schema_version="1.0",
                context=OperationContext(
                    schema_version="1.0",
                    as_of=updated.stored_at,
                    requested_at=datetime.now(UTC),
                ),
                watchlist_id=watchlist_id,
            )
        )
        assert latest.watchlist is not None
        assert latest.watchlist.revision == 2

        stale = _request(
            revision=2,
            updated_at=second_time,
            idempotency_key=f"active-target-stale-{run_key}",
            watchlist_id=watchlist_id,
            instrument=second_instrument,
            expected_revision=1,
        )
        with pytest.raises(PortfolioRevisionConflictError):
            await repository.save_active_target(stale)
    finally:
        await engine.dispose()


async def test_active_target_concurrent_replay_writes_one_revision_and_receipt() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresPortfolioRepository(engine)
    watchlist_id = uuid4()
    idempotency_key = f"active-target-concurrent-{watchlist_id.hex}"
    instrument = InstrumentRef(
        instrument_id="XSHG:603986",
        symbol="603986.SH",
        exchange="XSHG",
        market="CN",
    )
    request = _request(
        revision=1,
        updated_at=datetime.now(UTC),
        idempotency_key=idempotency_key,
        watchlist_id=watchlist_id,
        instrument=instrument,
        expected_revision=None,
    )
    try:
        results = await asyncio.gather(
            *(repository.save_active_target(request) for _ in range(100))
        )

        assert sum(not result.idempotent_replay for result in results) == 1
        assert sum(result.idempotent_replay for result in results) == 99
        assert all(result.watchlist.revision == 1 for result in results)
        assert all(result.active_target.instrument == instrument for result in results)
        assert len({result.stored_at for result in results}) == 1

        async with engine.connect() as connection:
            revision_count = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM active_target_revisions
                     WHERE watchlist_id = :watchlist_id
                    """
                ),
                {"watchlist_id": watchlist_id},
            )
            receipt_count = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM active_target_write_receipts
                     WHERE idempotency_key = :idempotency_key
                    """
                ),
                {"idempotency_key": idempotency_key},
            )

        assert revision_count == 1
        assert receipt_count == 1
    finally:
        await engine.dispose()


async def test_worker_bootstrap_reuses_legacy_v1_target_without_receipt_conflict() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresPortfolioRepository(engine)
    service = FixedActiveTargetService(repository)
    legacy_at = datetime.now(UTC) - timedelta(minutes=5)
    legacy_context = MutationContext(
        schema_version="1.0",
        request_id=UUID("bd9dcd14-66a8-5ead-921a-c9c880165dac"),
        correlation_id=UUID("f729c4d8-5433-531d-a57c-8e6ca399248b"),
        actor_id="legacy-r1-bootstrap",
        workspace_id="default",
        as_of=legacy_at,
        requested_at=legacy_at,
        idempotency_key="r1-fixed-target-bootstrap-v1",
    )
    try:
        await _reset_fixed_r1_target(engine)
        legacy = await service.initialize(legacy_context)
        assert legacy.state.active_target is not None
        legacy_activated_at = legacy.state.active_target.activated_at

        observed_at = datetime.now(UTC) + timedelta(seconds=1)
        await tasks._ensure_r1_target_initialized(service, observed_at=observed_at)
        await tasks._ensure_r1_target_initialized(
            service,
            observed_at=observed_at + timedelta(days=1),
        )

        revision_count, activated_at, receipt_keys = await _fixed_r1_target_snapshot(engine)
        assert revision_count == 1
        assert activated_at == legacy_activated_at == legacy_at
        assert receipt_keys == ["r1-fixed-target-bootstrap-v1"]
    finally:
        await _reset_fixed_r1_target(engine)
        await engine.dispose()


async def test_worker_bootstrap_empty_database_converges_on_one_v2_revision() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresPortfolioRepository(engine)
    try:
        await _reset_fixed_r1_target(engine)
        observed_before_write = datetime.now(UTC) - timedelta(seconds=1)
        services = [FixedActiveTargetService(repository) for _ in range(20)]
        await asyncio.gather(
            *(
                tasks._ensure_r1_target_initialized(
                    service,
                    observed_at=observed_before_write,
                )
                for service in services
            )
        )

        revision_count, activated_at, receipt_keys = await _fixed_r1_target_snapshot(engine)
        assert revision_count == 1
        assert activated_at == tasks._r1_target_initialization_context().as_of
        assert receipt_keys == ["r1-fixed-target-bootstrap-v2"]

        await tasks._ensure_r1_target_initialized(
            FixedActiveTargetService(repository),
            observed_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        assert await _fixed_r1_target_snapshot(engine) == (
            revision_count,
            activated_at,
            receipt_keys,
        )
    finally:
        await _reset_fixed_r1_target(engine)
        await engine.dispose()
