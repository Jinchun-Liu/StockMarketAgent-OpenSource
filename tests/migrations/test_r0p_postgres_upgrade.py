from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from stock_assistant.config import get_settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INITIAL_REVISION = "20260721_0001"
PREVIOUS_HEAD_REVISION = "20260830_0018"
HEAD_REVISION = "20260908_0021"

CLUSTER_ID = UUID("00000000-0000-0000-0000-000000000101")
FIRST_REVISION_ID = UUID("00000000-0000-0000-0000-000000000111")
SECOND_REVISION_ID = UUID("00000000-0000-0000-0000-000000000112")


def _database_url() -> str:
    url = os.environ.get("R0P_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("R0P_TEST_DATABASE_URL is required for the disposable PostgreSQL upgrade test")
    parsed = make_url(url)
    allowed_hosts = {"127.0.0.1", "localhost", "postgres"}
    if parsed.host not in allowed_hosts:
        pytest.fail("R0P migration tests only accept an isolated local/CI PostgreSQL host")
    if parsed.database is None or not parsed.database.startswith("stock_assistant_r0p_test"):
        pytest.fail("R0P migration database name must start with stock_assistant_r0p_test")
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("R0P migration tests require a postgresql+asyncpg URL")
    return url


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "infra" / "migrations"))
    config.set_main_option("prepend_sys_path", f"{REPOSITORY_ROOT} {REPOSITORY_ROOT / 'src'}")
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def _fetch_scalar(database_url: str, sql: str) -> object:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(sql))
    finally:
        await engine.dispose()


async def _execute(database_url: str, statements: Sequence[str]) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


async def _event_revisions(database_url: str) -> list[tuple[UUID, UUID, int, UUID | None]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT id, logical_event_id, revision, supersedes_revision_id
                          FROM market_events
                         WHERE cluster_id = '00000000-0000-0000-0000-000000000101'
                         ORDER BY revision
                        """
                    )
                )
            ).all()
            return [
                (row.id, row.logical_event_id, row.revision, row.supersedes_revision_id)
                for row in rows
            ]
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_empty_and_previous_head_upgrade_on_disposable_postgres(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_url = _database_url()
    existing_table_count = asyncio.run(
        _fetch_scalar(
            database_url,
            """
            SELECT count(*)
              FROM pg_catalog.pg_tables
             WHERE schemaname = 'public'
               AND tablename NOT LIKE 'spatial_ref_sys'
            """,
        )
    )
    assert existing_table_count == 0, "the R0P PostgreSQL database must be disposable and empty"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = _alembic_config(database_url)

    command.upgrade(config, INITIAL_REVISION)
    asyncio.run(
        _execute(
            database_url,
            (
                """
                INSERT INTO event_clusters (
                    id,
                    canonical_event_type,
                    opened_at,
                    last_changed_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000101',
                    'synthetic_test',
                    '2026-07-22T01:00:00+00:00',
                    '2026-07-22T02:00:00+00:00'
                ),
                (
                    '00000000-0000-0000-0000-000000000121',
                    'synthetic_gap_test',
                    '2026-07-22T03:00:00+00:00',
                    '2026-07-22T03:00:00+00:00'
                )
                """,
                """
                INSERT INTO market_events (
                    id,
                    cluster_id,
                    revision,
                    event_type,
                    event_status,
                    direction,
                    materiality,
                    impact_horizon,
                    confidence,
                    published_at,
                    first_seen_at,
                    valid_from,
                    extractor_version,
                    rule_version,
                    payload,
                    created_at
                )
                VALUES
                (
                    '00000000-0000-0000-0000-000000000111',
                    '00000000-0000-0000-0000-000000000101',
                    1,
                    'synthetic_test',
                    'announced',
                    'neutral',
                    0.5,
                    'days',
                    0.8,
                    '2026-07-22T01:00:00+00:00',
                    '2026-07-22T01:05:00+00:00',
                    '2026-07-22T01:05:00+00:00',
                    'fixture-1',
                    'rule-1',
                    '{}'::jsonb,
                    '2026-07-22T01:06:00+00:00'
                ),
                (
                    '00000000-0000-0000-0000-000000000112',
                    '00000000-0000-0000-0000-000000000101',
                    2,
                    'synthetic_test',
                    'corrected',
                    'neutral',
                    0.5,
                    'days',
                    0.8,
                    '2026-07-22T01:00:00+00:00',
                    '2026-07-22T02:00:00+00:00',
                    '2026-07-22T02:00:00+00:00',
                    'fixture-1',
                    'rule-1',
                    '{}'::jsonb,
                    '2026-07-22T02:01:00+00:00'
                ),
                (
                    '00000000-0000-0000-0000-000000000122',
                    '00000000-0000-0000-0000-000000000121',
                    2,
                    'synthetic_gap_test',
                    'corrected',
                    'neutral',
                    0.5,
                    'days',
                    0.8,
                    '2026-07-22T03:00:00+00:00',
                    '2026-07-22T03:05:00+00:00',
                    '2026-07-22T03:05:00+00:00',
                    'fixture-1',
                    'rule-1',
                    '{}'::jsonb,
                    '2026-07-22T03:06:00+00:00'
                )
                """,
            ),
        )
    )

    command.upgrade(config, "20260723_0003")
    assert asyncio.run(_fetch_scalar(database_url, "SELECT version_num FROM alembic_version")) == (
        "20260723_0003"
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM migration_repair_queue
                 WHERE entity_table = 'market_events'
                   AND entity_key = '00000000-0000-0000-0000-000000000121'
                   AND reason_code = 'MARKET_EVENT_REVISION_CHAIN_INVALID'
                   AND resolved_at IS NULL
                """,
            )
        )
        == 1
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM market_events
                 WHERE id = '00000000-0000-0000-0000-000000000122'
                   AND supersedes_revision_id IS NULL
                """,
            )
        )
        == 1
    )
    with pytest.raises(DBAPIError, match="unresolved repair item"):
        command.upgrade(config, "20260723_0004")
    assert asyncio.run(_fetch_scalar(database_url, "SELECT version_num FROM alembic_version")) == (
        "20260723_0003"
    )

    asyncio.run(
        _execute(
            database_url,
            (
                """
                INSERT INTO market_events (
                    id,
                    cluster_id,
                    revision,
                    event_type,
                    event_status,
                    direction,
                    materiality,
                    impact_horizon,
                    confidence,
                    published_at,
                    first_seen_at,
                    valid_from,
                    extractor_version,
                    rule_version,
                    payload,
                    created_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000123',
                    '00000000-0000-0000-0000-000000000121',
                    1,
                    'synthetic_gap_test',
                    'announced',
                    'neutral',
                    0.5,
                    'days',
                    0.8,
                    '2026-07-22T02:50:00+00:00',
                    '2026-07-22T02:55:00+00:00',
                    '2026-07-22T02:55:00+00:00',
                    'fixture-1',
                    'rule-1',
                    '{}'::jsonb,
                    '2026-07-22T02:56:00+00:00'
                )
                """,
                """
                UPDATE market_events
                   SET logical_event_id =
                           '00000000-0000-0000-0000-000000000123',
                       supersedes_revision_id =
                           '00000000-0000-0000-0000-000000000123'
                 WHERE id = '00000000-0000-0000-0000-000000000122'
                """,
                """
                UPDATE migration_repair_queue
                   SET resolved_at = '2026-07-22T04:00:00+00:00',
                       resolution_note =
                           'Synthetic predecessor restored from fixture evidence'
                 WHERE entity_table = 'market_events'
                   AND entity_key = '00000000-0000-0000-0000-000000000121'
                   AND reason_code = 'MARKET_EVENT_REVISION_CHAIN_INVALID'
                   AND resolved_at IS NULL
                """,
            ),
        )
    )

    command.upgrade(config, "20260723_0005")

    assert asyncio.run(_fetch_scalar(database_url, "SELECT version_num FROM alembic_version")) == (
        "20260723_0005"
    )
    assert asyncio.run(_event_revisions(database_url)) == [
        (FIRST_REVISION_ID, FIRST_REVISION_ID, 1, None),
        (SECOND_REVISION_ID, FIRST_REVISION_ID, 2, FIRST_REVISION_ID),
    ]
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM migration_repair_queue
                 WHERE resolved_at IS NULL
                """,
            )
        )
        == 0
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM market_events
                 WHERE revision_known_at <= '2026-07-22T01:30:00+00:00'
                """,
            )
        )
        == 1
    )

    asyncio.run(
        _execute(
            database_url,
            (
                """
                INSERT INTO job_runs (
                    id,
                    task_name,
                    idempotency_key,
                    status,
                    scheduled_for,
                    attempt,
                    metrics,
                    request_schema_name,
                    request_schema_version,
                    request_schema_sha256,
                    result_schema_name,
                    result_schema_version,
                    result_schema_sha256,
                    request_id,
                    correlation_id,
                    actor_id,
                    workspace_id,
                    as_of,
                    requested_at,
                    queued_at,
                    queue_name,
                    max_attempts,
                    request_payload
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000201',
                    'stock_assistant.ingestion.premarket_ingest.v1',
                    'synthetic-job-0001',
                    'queued',
                    '2026-07-22T08:55:00+00:00',
                    1,
                    '{}'::jsonb,
                    'premarket.ingest.request',
                    '1.0',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'premarket.ingest.result',
                    '1.0',
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    '00000000-0000-0000-0000-000000000202',
                    '00000000-0000-0000-0000-000000000203',
                    'migration-test',
                    'test',
                    '2026-07-22T08:50:00+00:00',
                    '2026-07-22T08:51:00+00:00',
                    '2026-07-22T08:52:00+00:00',
                    'r0p-test',
                    3,
                    '{}'::jsonb
                )
                """,
                """
                UPDATE job_runs
                   SET status = 'running',
                       started_at = '2026-07-22T08:56:00+00:00'
                 WHERE id = '00000000-0000-0000-0000-000000000201'
                """,
            ),
        )
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM job_run_transitions
                 WHERE job_run_id = '00000000-0000-0000-0000-000000000201'
                """,
            )
        )
        == 2
    )
    with pytest.raises(DBAPIError, match="append-only"):
        asyncio.run(
            _execute(
                database_url,
                (
                    """
                    UPDATE job_run_transitions
                       SET reason_code = 'MUTATION_MUST_FAIL'
                     WHERE job_run_id = '00000000-0000-0000-0000-000000000201'
                    """,
                ),
            )
        )

    command.upgrade(config, PREVIOUS_HEAD_REVISION)
    asyncio.run(
        _execute(
            database_url,
            (
                """
                INSERT INTO research_sessions (
                    session_id,
                    job_id,
                    instrument_id,
                    instrument_name,
                    as_of,
                    analysis_config,
                    source_mode,
                    idempotency_key,
                    request_sha256,
                    status,
                    stage,
                    progress_message,
                    heartbeat_at,
                    created_at,
                    updated_at,
                    expires_at,
                    owner_principal_sha256
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000401',
                    '00000000-0000-0000-0000-000000000402',
                    'XSHG:603986',
                    'Synthetic migration fixture',
                    '2026-07-22T09:00:00+00:00',
                    '{}'::jsonb,
                    'synthetic_development_only',
                    'synthetic-research-migration-0001',
                    'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                    'queued',
                    'queued',
                    'Synthetic migration fixture queued',
                    '2026-07-22T09:00:00+00:00',
                    '2026-07-22T09:00:00+00:00',
                    '2026-07-22T09:00:00+00:00',
                    '2026-07-29T09:00:00+00:00',
                    '0dc852dd1d1b1710a3f66271adc4d80e2fb7791c1418292fb4efc35dee6541e7'
                )
                """,
            ),
        )
    )

    command.upgrade(config, "20260904_0019")
    command.upgrade(config, "head")
    assert asyncio.run(_fetch_scalar(database_url, "SELECT version_num FROM alembic_version")) == (
        HEAD_REVISION
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                "SELECT checkpoint_payload IS NULL AND result_completeness IS NULL "
                "FROM research_sessions "
                "WHERE session_id = '00000000-0000-0000-0000-000000000401'",
            )
        )
        is True
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT cutoff_mode
                  FROM research_sessions
                 WHERE session_id = '00000000-0000-0000-0000-000000000401'
                """,
            )
        )
        == "historical_replay"
    )
    with pytest.raises(IntegrityError):
        asyncio.run(
            _execute(
                database_url,
                (
                    """
                    UPDATE research_sessions
                       SET cutoff_mode = 'unbounded_live'
                     WHERE session_id = '00000000-0000-0000-0000-000000000401'
                    """,
                ),
            )
        )
    with pytest.raises(IntegrityError):
        asyncio.run(
            _execute(
                database_url,
                (
                    """
                    UPDATE research_sessions
                       SET cutoff_mode = NULL
                     WHERE session_id = '00000000-0000-0000-0000-000000000401'
                    """,
                ),
            )
        )
    asyncio.run(
        _execute(
            database_url,
            (
                """
                UPDATE research_sessions
                   SET cutoff_mode = 'current_run'
                 WHERE session_id = '00000000-0000-0000-0000-000000000401'
                """,
            ),
        )
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT cutoff_mode
                  FROM research_sessions
                 WHERE session_id = '00000000-0000-0000-0000-000000000401'
                """,
            )
        )
        == "current_run"
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT to_regclass('public.notification_inbox_items') IS NOT NULL
                """,
            )
        )
        is True
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM information_schema.columns
                     WHERE table_schema = 'public'
                       AND table_name = 'source_cursors'
                       AND column_name = 'business_watermark'
                )
                """,
            )
        )
        is True
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT constraint_record.convalidated
                  FROM pg_catalog.pg_constraint AS constraint_record
                  JOIN pg_catalog.pg_class AS relation
                    ON relation.oid = constraint_record.conrelid
                  JOIN pg_catalog.pg_namespace AS namespace
                    ON namespace.oid = relation.relnamespace
                 WHERE namespace.nspname = 'public'
                   AND relation.relname = 'source_cursors'
                   AND constraint_record.conname = 'source_cursors_refresh_state_ck'
                """,
            )
        )
        is True
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'raw_documents'
                   AND column_name IN ('event_time', 'provenance')
                """,
            )
        )
        == 2
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM job_run_transitions
                 WHERE job_run_id = '00000000-0000-0000-0000-000000000201'
                   AND recorded_at IS NOT NULL
                   AND recorded_at >= occurred_at
                """,
            )
        )
        == 2
    )
    asyncio.run(
        _execute(
            database_url,
            (
                """
                INSERT INTO job_run_transitions (
                    job_run_id,
                    sequence_number,
                    from_status,
                    to_status,
                    attempt,
                    occurred_at,
                    recorded_at,
                    request_id,
                    correlation_id,
                    reason_code,
                    safe_metadata
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000201',
                    3,
                    'running',
                    'running',
                    1,
                    '2026-07-22T08:57:00+00:00',
                    '2000-01-01T00:00:00+00:00',
                    '00000000-0000-0000-0000-000000000202',
                    '00000000-0000-0000-0000-000000000203',
                    'SYNTHETIC_HEARTBEAT',
                    '{}'::jsonb
                )
                """,
            ),
        )
    )
    assert (
        asyncio.run(
            _fetch_scalar(
                database_url,
                """
                SELECT count(*)
                  FROM job_run_transitions
                 WHERE job_run_id = '00000000-0000-0000-0000-000000000201'
                   AND sequence_number = 3
                   AND recorded_at > '2000-01-01T00:00:00+00:00'
                   AND recorded_at >= occurred_at
                """,
            )
        )
        == 1
    )
    with pytest.raises(DBAPIError, match="append-only"):
        asyncio.run(
            _execute(
                database_url,
                (
                    """
                    UPDATE job_run_transitions
                       SET recorded_at = '2000-01-01T00:00:00+00:00'
                     WHERE job_run_id = '00000000-0000-0000-0000-000000000201'
                       AND sequence_number = 3
                    """,
                ),
            )
        )

    asyncio.run(
        _execute(
            database_url,
            (
                """
                INSERT INTO outbox_events (
                    id,
                    topic,
                    aggregate_type,
                    aggregate_id,
                    payload,
                    created_at,
                    attempt,
                    message_id,
                    event_name,
                    envelope_schema_version,
                    payload_schema_name,
                    payload_schema_version,
                    payload_schema_sha256,
                    producer,
                    occurred_at,
                    produced_at,
                    correlation_id,
                    partition_key,
                    idempotency_key,
                    available_at
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000301',
                    'com.stockassistant.test.synthetic.v1',
                    'synthetic',
                    'one',
                    '{}'::jsonb,
                    '2026-07-22T09:00:00+00:00',
                    1,
                    '00000000-0000-0000-0000-000000000302',
                    'com.stockassistant.test.synthetic.v1',
                    '1.0',
                    'test.synthetic',
                    '1.0',
                    'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    'migration-test',
                    '2026-07-22T08:59:00+00:00',
                    '2026-07-22T09:00:00+00:00',
                    '00000000-0000-0000-0000-000000000303',
                    'synthetic:one',
                    'synthetic-message-0001',
                    '2026-07-22T09:00:00+00:00'
                )
                """,
            ),
        )
    )
    with pytest.raises(IntegrityError):
        asyncio.run(
            _execute(
                database_url,
                (
                    """
                    INSERT INTO outbox_events (
                        id,
                        topic,
                        aggregate_type,
                        aggregate_id,
                        payload,
                        created_at,
                        attempt,
                        message_id,
                        event_name,
                        envelope_schema_version,
                        payload_schema_name,
                        payload_schema_version,
                        payload_schema_sha256,
                        producer,
                        occurred_at,
                        produced_at,
                        correlation_id,
                        partition_key,
                        idempotency_key,
                        available_at
                    )
                    VALUES (
                        '00000000-0000-0000-0000-000000000304',
                        'com.stockassistant.test.synthetic.v1',
                        'synthetic',
                        'one',
                        '{}'::jsonb,
                        '2026-07-22T09:01:00+00:00',
                        1,
                        '00000000-0000-0000-0000-000000000305',
                        'com.stockassistant.test.synthetic.v1',
                        '1.0',
                        'test.synthetic',
                        '1.0',
                        'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                        'migration-test',
                        '2026-07-22T08:59:00+00:00',
                        '2026-07-22T09:01:00+00:00',
                        '00000000-0000-0000-0000-000000000306',
                        'synthetic:one',
                        'synthetic-message-0001',
                        '2026-07-22T09:01:00+00:00'
                    )
                    """,
                ),
            )
        )
    get_settings.cache_clear()
