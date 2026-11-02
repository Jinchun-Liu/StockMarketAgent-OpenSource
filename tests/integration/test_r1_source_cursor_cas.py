"""Real PostgreSQL acceptance coverage for the R1 cursor lease/CAS protocol."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from apps.runtime import premarket as runtime_premarket
from apps.scheduler.requests import build_runtime_brief_request
from apps.worker import tasks as worker_tasks
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.event_views import EventEvidenceRevisionSelector
from stock_assistant.contracts.ingestion import (
    FetchRequest,
    SourceAccessChannel,
    SourceAccessChannelV1_2,
    SourceEvidenceRole,
    SourceProvenance,
    SourceQualityTier,
    SourceReleaseTier,
    SourceRightsStatus,
    SourceRightsStatusV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_1,
    PremarketBriefingViewV1_2,
    SourceAdvanceState,
    SourceCoverageStatus,
    source_data_mode_for_runtime,
)
from stock_assistant.modules.briefings.projection import (
    validate_briefing_evidence_projection,
)
from stock_assistant.modules.ingestion.r1_fixture import (
    CHOICE_SOURCE_ID,
    CNINFO_SOURCE_ID,
    FixtureBatch,
    build_fixture_batches,
)
from stock_assistant.modules.ingestion.r1_sources import (
    R1SourceBatch,
    ReplayScenario,
    build_demo_replay_batch,
    build_demo_replay_batches,
    source_batch_from_fetch,
    terminal_gap_batch,
)
from stock_assistant.platform.db.r1_premarket import (
    FixtureBriefingStorageResult,
    PostgresR1PremarketRepository,
)
from stock_assistant.platform.sources.r1_dev_live import (
    DEV_LIVE_LICENSE_ID,
    DEV_LIVE_RIGHTS_HASH,
    DEV_LIVE_SOURCE_IDS,
    AkshareDevLiveAdapter,
)
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_LICENSE_ID,
    FREE_SOURCE_RIGHTS_HASH,
    FreeLiveAdapter,
    free_source_gap,
)
from stock_assistant.platform.sources.research_preview_runner import (
    PostgresAccountRateLimiter,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _dev_live_fetch_request(*, capability: str, as_of: datetime) -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            request_id=uuid4(),
            correlation_id=uuid4(),
            actor_id="r1-pit-integration",
            as_of=as_of,
            requested_at=as_of,
        ),
        source_id=DEV_LIVE_SOURCE_IDS[capability],
        capability=capability,
        rights_snapshot_hash=DEV_LIVE_RIGHTS_HASH,
        since=as_of - timedelta(days=30),
        until=as_of,
        instrument_ids=["XSHG:603986"],
        limit=100,
    )


def _free_fetch_request(*, adapter: FreeLiveAdapter, as_of: datetime) -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            request_id=uuid4(),
            correlation_id=uuid4(),
            actor_id="r1-free-source-integration",
            as_of=as_of,
            requested_at=as_of,
        ),
        source_id=adapter.source_id,
        capability=adapter.capability,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        since=as_of - timedelta(days=30),
        until=as_of,
        instrument_ids=["XSHG:603986"],
        limit=100,
    )


def _database_url() -> str:
    url = os.getenv("R1_CURSOR_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("R1_CURSOR_TEST_DATABASE_URL is required for the real PostgreSQL CAS test")
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost", "postgres"}:
        pytest.fail("R1 cursor tests only accept an isolated local/CI PostgreSQL host")
    if parsed.database is None or not parsed.database.startswith("stock_assistant_r0p_test"):
        pytest.fail("R1 cursor test database must use the disposable migration-test prefix")
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("R1 cursor tests require a postgresql+asyncpg URL")
    return url


def _upgrade(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "infra/migrations"))
    config.set_main_option("prepend_sys_path", f"{REPOSITORY_ROOT} {REPOSITORY_ROOT / 'src'}")
    config.set_main_option("path_separator", "os")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


async def _create_isolated_database(base_database_url: str, suffix: str) -> str:
    base_url = make_url(base_database_url)
    base_name = base_url.database
    assert base_name is not None
    database_name = f"{base_name}_{suffix}"
    assert database_name.replace("_", "").isalnum()
    admin_engine = create_async_engine(
        base_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin_engine.connect() as connection:
            statement = await connection.scalar(
                text("SELECT format('CREATE DATABASE %I', CAST(:database_name AS text))"),
                {"database_name": database_name},
            )
            assert isinstance(statement, str)
            await connection.exec_driver_sql(statement)
    finally:
        await admin_engine.dispose()
    return base_url.set(database=database_name).render_as_string(hide_password=False)


async def _scalar(engine: AsyncEngine, sql: str, **parameters: object) -> object:
    async with engine.connect() as connection:
        return await connection.scalar(text(sql), parameters)


async def _table_count(database_url: str) -> object:
    engine = create_async_engine(database_url)
    try:
        return await _scalar(
            engine,
            """
            SELECT count(*)
              FROM pg_catalog.pg_tables
             WHERE schemaname = 'public'
               AND tablename NOT LIKE 'spatial_ref_sys'
            """,
        )
    finally:
        await engine.dispose()


async def _cursor_state(engine: AsyncEngine, source_id: str) -> tuple[int, datetime | None]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT cursor_version, last_success_at
                          FROM source_cursors
                         WHERE source_id = :source_id
                           AND stream = 'r1_fixture_premarket'
                        """
                    ),
                    {"source_id": source_id},
                )
            )
            .mappings()
            .one()
        )
    return row["cursor_version"], row["last_success_at"]


async def _capability_cursor_state(
    engine: AsyncEngine,
    *,
    source_id: str,
    stream: str,
) -> tuple[int, datetime | None]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT cursor_version, last_success_at
                          FROM source_cursors
                         WHERE source_id = :source_id AND stream = :stream
                        """
                    ),
                    {"source_id": source_id, "stream": stream},
                )
            )
            .mappings()
            .one()
        )
    return row["cursor_version"], row["last_success_at"]


async def _capability_refresh_state(
    engine: AsyncEngine,
    *,
    source_id: str,
    stream: str,
) -> tuple[int, datetime | None, datetime | None, str | None]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT cursor_version, business_watermark, last_success_at,
                               cursor_payload->>'provider_cursor' AS provider_cursor
                          FROM source_cursors
                         WHERE source_id = :source_id AND stream = :stream
                        """
                    ),
                    {"source_id": source_id, "stream": stream},
                )
            )
            .mappings()
            .one()
        )
    return (
        row["cursor_version"],
        row["business_watermark"],
        row["last_success_at"],
        row["provider_cursor"],
    )


async def _expire_lease(engine: AsyncEngine, source_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE source_cursors
                   SET lease_expires_at = clock_timestamp() - interval '1 second'
                 WHERE source_id = :source_id
                   AND stream = 'r1_fixture_premarket'
                """
            ),
            {"source_id": source_id},
        )


async def _persist_fixture_batches(
    repository: PostgresR1PremarketRepository,
    batches: tuple[FixtureBatch, ...],
    *,
    window_start: datetime,
    window_end: datetime,
    owner_prefix: str,
) -> None:
    for batch in batches:
        lease = await repository.acquire_fixture_lease(
            batch=batch,
            owner=f"{owner_prefix}-{batch.source_id}",
        )
        await repository.persist_fixture_batch(
            batch=batch,
            window_start=window_start,
            window_end=window_end,
            lease=lease,
        )


async def _briefing_storage_counts(engine: AsyncEngine) -> tuple[int, ...]:
    statements = (
        "SELECT count(*) FROM briefings WHERE model_version = 'deterministic-fixture'",
        "SELECT count(*) FROM market_events",
        "SELECT count(*) FROM event_documents",
        "SELECT count(*) FROM event_claims",
        "SELECT count(*) FROM raw_documents",
    )
    counts: list[int] = []
    for statement in statements:
        count = await _scalar(engine, statement)
        assert isinstance(count, int)
        counts.append(count)
    return tuple(counts)


async def _exercise_cursor_protocol(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    window_start = datetime(2026, 7, 28, 7, tzinfo=UTC)
    window_end = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
    batches = build_fixture_batches(
        window_start=window_start,
        window_end=window_end,
        instrument_id="XSHG:603986",
    )
    cninfo, choice = batches
    try:
        mixed = replace(choice, events=(cninfo.events[0], *choice.events))
        with pytest.raises(ApplicationError, match="R1_FIXTURE_SOURCE_MIXED"):
            await repository.acquire_fixture_lease(batch=mixed, owner="mixed-owner")
        assert await _scalar(engine, "SELECT count(*) FROM raw_documents") == 0
        assert await _scalar(engine, "SELECT count(*) FROM source_cursors") == 0

        stale_lease = await repository.acquire_fixture_lease(
            batch=cninfo,
            owner="cninfo-stale-owner",
        )
        with pytest.raises(ApplicationError, match="R1_SOURCE_LEASE_BUSY"):
            await repository.acquire_fixture_lease(
                batch=cninfo,
                owner="cninfo-blocked-owner",
            )
        await _expire_lease(engine, CNINFO_SOURCE_ID)
        recovery_lease = await repository.acquire_fixture_lease(
            batch=cninfo,
            owner="cninfo-recovery-owner",
        )
        assert recovery_lease.expected_cursor_version == stale_lease.expected_cursor_version == 0

        outcomes = await asyncio.gather(
            repository.persist_fixture_batch(
                batch=cninfo,
                window_start=window_start,
                window_end=window_end,
                lease=stale_lease,
            ),
            repository.persist_fixture_batch(
                batch=cninfo,
                window_start=window_start,
                window_end=window_end,
                lease=recovery_lease,
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
        conflicts = [item for item in outcomes if isinstance(item, ApplicationError)]
        assert len(conflicts) == 1
        assert conflicts[0].safe_message == "R1_CURSOR_CAS_CONFLICT"
        expected_cninfo_evidence = {
            evidence.evidence_id for event in cninfo.events for evidence in event.evidence
        }
        assert await _scalar(
            engine,
            "SELECT count(*) FROM raw_documents WHERE source_id = :source_id",
            source_id=CNINFO_SOURCE_ID,
        ) == len(expected_cninfo_evidence)

        choice_lease = await repository.acquire_fixture_lease(
            batch=choice,
            owner="choice-first-owner",
        )
        await repository.persist_fixture_batch(
            batch=choice,
            window_start=window_start,
            window_end=window_end,
            lease=choice_lease,
        )
        coexisting_v1_1 = build_demo_replay_batch(
            capability="company_industry.news",
            scenario=ReplayScenario.SUCCESS,
            window_start=window_start,
            window_end=window_end,
            instrument_id="XSHG:603986",
        )
        coexisting_lease = await repository.acquire_source_lease(
            batch=coexisting_v1_1,
            owner="coexisting-v1-1",
        )
        await repository.persist_source_batch(
            batch=coexisting_v1_1,
            window_start=window_start,
            window_end=window_end,
            lease=coexisting_lease,
        )
        full_brief = await repository.generate_and_store_briefing(
            window_start=window_start,
            window_end=window_end,
            generated_at=window_end + timedelta(minutes=10),
            source_ids=(CNINFO_SOURCE_ID, CHOICE_SOURCE_ID),
        )
        assert full_brief.reason_codes == ()
        included_event_ids = {
            item.event_id
            for section in (
                full_brief.briefing.sections.statutory_announcements,
                full_brief.briefing.sections.financial_changes,
                full_brief.briefing.sections.market_snapshot,
                full_brief.briefing.sections.company_industry_news,
                full_brief.briefing.sections.macro_flashes,
            )
            for item in section.items
        }
        assert included_event_ids.isdisjoint({event.event_id for event in coexisting_v1_1.events})
        contradiction = full_brief.briefing.sections.market_snapshot.items[0]
        assert contradiction.change_type is BriefingChangeType.CONTRADICTION
        contradiction_sources = await _scalar(
            engine,
            """
            SELECT count(DISTINCT source_id)
              FROM raw_documents
             WHERE id = ANY(:evidence_ids)
            """,
            evidence_ids=list(contradiction.evidence_ids),
        )
        assert contradiction_sources == 2
        assert (
            await _scalar(
                engine,
                """
            SELECT count(*)
              FROM raw_documents
             WHERE source_id = 'cninfo_data_service'
               AND id IN (
                   SELECT document_id
                     FROM event_documents
                    WHERE event_id IN (
                        SELECT id FROM market_events
                         WHERE event_type = 'source_observation.market_close'
                           AND payload->'evidence'->0->>'source_id' = 'choice_quant_api'
                    )
               )
            """,
            )
            == 0
        )

        stable_counts = {
            "documents": await _scalar(engine, "SELECT count(*) FROM raw_documents"),
            "events": await _scalar(
                engine,
                "SELECT count(*) FROM market_events WHERE rule_version = 'r1-fixture-rules-v1'",
            ),
            "briefings": await _scalar(
                engine,
                "SELECT count(*) FROM briefings WHERE model_version = 'deterministic-fixture'",
            ),
        }
        stable_cursor_states = {
            source_id: await _cursor_state(engine, source_id)
            for source_id in (CNINFO_SOURCE_ID, CHOICE_SOURCE_ID)
        }
        for replay in range(100):
            for batch in batches:
                lease = await repository.acquire_fixture_lease(
                    batch=batch,
                    owner=f"replay-{replay}-{batch.source_id}",
                )
                result = await repository.persist_fixture_batch(
                    batch=batch,
                    window_start=window_start,
                    window_end=window_end,
                    lease=lease,
                )
                assert result.idempotent_replay is True
            replayed_brief = await repository.generate_and_store_briefing(
                window_start=window_start,
                window_end=window_end,
                generated_at=window_end + timedelta(minutes=10),
                source_ids=(CNINFO_SOURCE_ID, CHOICE_SOURCE_ID),
            )
            assert replayed_brief.briefing.briefing_id == full_brief.briefing.briefing_id
        assert {
            "documents": await _scalar(engine, "SELECT count(*) FROM raw_documents"),
            "events": await _scalar(
                engine,
                "SELECT count(*) FROM market_events WHERE rule_version = 'r1-fixture-rules-v1'",
            ),
            "briefings": await _scalar(
                engine,
                "SELECT count(*) FROM briefings WHERE model_version = 'deterministic-fixture'",
            ),
        } == stable_counts
        assert {
            source_id: await _cursor_state(engine, source_id)
            for source_id in (CNINFO_SOURCE_ID, CHOICE_SOURCE_ID)
        } == stable_cursor_states

        newer_start = window_start + timedelta(days=2)
        newer_end = window_end + timedelta(days=2)
        newer_batches = build_fixture_batches(
            window_start=newer_start,
            window_end=newer_end,
            instrument_id="XSHG:603986",
        )
        for batch in newer_batches:
            lease = await repository.acquire_fixture_lease(
                batch=batch,
                owner=f"newer-{batch.source_id}",
            )
            await repository.persist_fixture_batch(
                batch=batch,
                window_start=newer_start,
                window_end=newer_end,
                lease=lease,
            )
        newer_states = {
            source_id: await _cursor_state(engine, source_id)
            for source_id in (CNINFO_SOURCE_ID, CHOICE_SOURCE_ID)
        }

        late_start = window_start + timedelta(days=1)
        late_end = window_end + timedelta(days=1)
        late_cninfo = build_fixture_batches(
            window_start=late_start,
            window_end=late_end,
            instrument_id="XSHG:603986",
        )[0]
        late_lease = await repository.acquire_fixture_lease(
            batch=late_cninfo,
            owner="late-cninfo-owner",
        )
        late_result = await repository.persist_fixture_batch(
            batch=late_cninfo,
            window_start=late_start,
            window_end=late_end,
            lease=late_lease,
        )
        assert late_result.reason_code == "R1_CURSOR_WINDOW_NOT_NEWER"
        assert await _cursor_state(engine, CNINFO_SOURCE_ID) == newer_states[CNINFO_SOURCE_ID]

        partial_start = window_start + timedelta(days=3)
        partial_end = window_end + timedelta(days=3)
        partial_cninfo, partial_choice = build_fixture_batches(
            window_start=partial_start,
            window_end=partial_end,
            instrument_id="XSHG:603986",
        )
        cninfo_lease = await repository.acquire_fixture_lease(
            batch=partial_cninfo,
            owner="partial-cninfo-owner",
        )
        await repository.persist_fixture_batch(
            batch=partial_cninfo,
            window_start=partial_start,
            window_end=partial_end,
            lease=cninfo_lease,
        )
        choice_before_failure = await _cursor_state(engine, CHOICE_SOURCE_ID)
        failed_lease = await repository.acquire_fixture_lease(
            batch=partial_choice,
            owner="partial-choice-owner",
        )
        await repository.record_fixture_failure(
            batch=partial_choice,
            window_start=partial_start,
            window_end=partial_end,
            lease=failed_lease,
            reason_code="R1_FIXTURE_SOURCE_UNAVAILABLE",
        )
        assert await _cursor_state(engine, CHOICE_SOURCE_ID) == choice_before_failure
        partial_brief = await repository.generate_and_store_briefing(
            window_start=partial_start,
            window_end=partial_end,
            generated_at=partial_end + timedelta(minutes=10),
            source_ids=(CNINFO_SOURCE_ID, CHOICE_SOURCE_ID),
        )
        assert partial_brief.reason_codes == ("R1_SOURCE_UNAVAILABLE",)
        coverage = {
            item.source_id: item.status.value for item in partial_brief.briefing.source_coverage
        }
        assert coverage[CNINFO_SOURCE_ID] == "ok"
        assert coverage[CHOICE_SOURCE_ID] == "unavailable"
        assert len(partial_brief.briefing.sections.market_snapshot.items) == 1
        assert (
            partial_brief.briefing.sections.market_snapshot.items[0].change_type
            is BriefingChangeType.NEW
        )
    finally:
        await engine.dispose()


async def _exercise_briefing_concurrency(database_url: str) -> None:
    window_start = datetime(2026, 7, 28, 7, tzinfo=UTC)
    window_end = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
    generated_at = window_end + timedelta(minutes=10)
    batches = build_fixture_batches(
        window_start=window_start,
        window_end=window_end,
        instrument_id="XSHG:603986",
    )
    seed_engine = create_async_engine(database_url)
    try:
        await _persist_fixture_batches(
            PostgresR1PremarketRepository(seed_engine),
            batches,
            window_start=window_start,
            window_end=window_end,
            owner_prefix="briefing-concurrency-seed",
        )
        pre_concurrency_counts = await _briefing_storage_counts(seed_engine)
    finally:
        await seed_engine.dispose()

    first_engine = create_async_engine(database_url)
    second_engine = create_async_engine(database_url)
    first_repository = PostgresR1PremarketRepository(first_engine)
    second_repository = PostgresR1PremarketRepository(second_engine)
    barrier = asyncio.Barrier(2)

    async def generate(
        repository: PostgresR1PremarketRepository,
    ) -> FixtureBriefingStorageResult:
        await barrier.wait()
        return await repository.generate_and_store_briefing(
            window_start=window_start,
            window_end=window_end,
            generated_at=generated_at,
            source_ids=(CNINFO_SOURCE_ID, CHOICE_SOURCE_ID),
        )

    try:
        first_result, second_result = await asyncio.gather(
            generate(first_repository),
            generate(second_repository),
        )
        assert first_result.briefing.briefing_id == second_result.briefing.briefing_id
        assert sorted((first_result.idempotent_replay, second_result.idempotent_replay)) == [
            False,
            True,
        ]
        assert first_result.reason_codes == second_result.reason_codes == ()

        briefing_id = first_result.briefing.briefing_id
        briefing_items = [
            item
            for section in (
                first_result.briefing.sections.statutory_announcements,
                first_result.briefing.sections.financial_changes,
                first_result.briefing.sections.market_snapshot,
                first_result.briefing.sections.company_industry_news,
                first_result.briefing.sections.macro_flashes,
            )
            for item in section.items
        ]
        expected_evidence_links = {
            (item.event_id, item.event_revision, evidence_id)
            for item in briefing_items
            for evidence_id in item.evidence_ids
        }
        evidence_ids = {link[2] for link in expected_evidence_links}
        async with first_engine.connect() as connection:
            briefing_identity = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT count(*) AS total, count(DISTINCT id) AS distinct_ids
                              FROM briefings
                             WHERE kind = 'premarket'
                               AND as_of = :as_of
                               AND model_version = 'deterministic-fixture'
                               AND payload->'instrument'->>'instrument_id' = :instrument_id
                            """
                        ),
                        {"as_of": window_end, "instrument_id": "XSHG:603986"},
                    )
                )
                .mappings()
                .one()
            )
            linked_evidence = await connection.scalar(
                text(
                    """
                    SELECT count(DISTINCT document_id)
                      FROM event_documents
                     WHERE document_id = ANY(:evidence_ids)
                    """
                ),
                {"evidence_ids": list(evidence_ids)},
            )
            persisted_evidence_links = {
                (
                    row["logical_event_id"],
                    row["revision"],
                    row["document_id"],
                )
                for row in (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT event.logical_event_id,
                                       event.revision,
                                       link.document_id
                                  FROM market_events AS event
                                  JOIN event_documents AS link
                                    ON link.event_id = event.id
                                 WHERE event.logical_event_id = ANY(:event_ids)
                                """
                            ),
                            {"event_ids": [item.event_id for item in briefing_items]},
                        )
                    )
                    .mappings()
                    .all()
                )
            }
            duplicate_revisions = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM (
                        SELECT logical_event_id, revision
                          FROM market_events
                         GROUP BY logical_event_id, revision
                        HAVING count(*) > 1
                      ) AS duplicates
                    """
                )
            )
            ambiguous_current_revisions = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                      FROM (
                        SELECT latest.logical_event_id
                          FROM (
                            SELECT logical_event_id, max(revision) AS revision
                              FROM market_events
                             WHERE first_seen_at <= :as_of
                               AND revision_known_at <= :as_of
                             GROUP BY logical_event_id
                          ) AS latest
                          JOIN market_events AS event
                            ON event.logical_event_id = latest.logical_event_id
                           AND event.revision = latest.revision
                         GROUP BY latest.logical_event_id
                        HAVING count(*) <> 1
                      ) AS ambiguous
                    """
                ),
                {"as_of": window_end},
            )
            duplicate_evidence_links = await connection.scalar(
                text(
                    """
                    SELECT count(*) - count(DISTINCT (event_id, document_id))
                      FROM event_documents
                    """
                )
            )
        assert briefing_identity["total"] == briefing_identity["distinct_ids"] == 1
        assert linked_evidence == len(evidence_ids)
        assert expected_evidence_links <= persisted_evidence_links
        assert duplicate_revisions == 0
        assert ambiguous_current_revisions == 0
        assert duplicate_evidence_links == 0

        stable_counts = await _briefing_storage_counts(first_engine)
        assert stable_counts[0] == pre_concurrency_counts[0] + 1
        assert stable_counts[4] == pre_concurrency_counts[4]
        replayed = await first_repository.generate_and_store_briefing(
            window_start=window_start,
            window_end=window_end,
            generated_at=generated_at,
            source_ids=(CNINFO_SOURCE_ID, CHOICE_SOURCE_ID),
        )
        assert replayed.idempotent_replay is True
        assert replayed.briefing.briefing_id == briefing_id
        assert await _briefing_storage_counts(first_engine) == stable_counts

        latest = await second_repository.get_latest_briefing(
            as_of=window_end,
            expected_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert latest is not None
        assert latest.briefing_id == briefing_id
    finally:
        await first_engine.dispose()
        await second_engine.dispose()


async def _exercise_provider_neutral_replay(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    window_start = datetime(2026, 7, 28, 7, tzinfo=UTC)
    window_end = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
    statutory = build_demo_replay_batch(
        capability="statutory.announcements",
        scenario=ReplayScenario.SUCCESS,
        window_start=window_start,
        window_end=window_end,
        instrument_id="XSHG:603986",
    )
    failure = build_demo_replay_batch(
        capability="financial.changes",
        scenario=ReplayScenario.SOURCE_FAILURE,
        window_start=window_start,
        window_end=window_end,
        instrument_id="XSHG:603986",
    )
    try:
        lease = await repository.acquire_source_lease(batch=statutory, owner="replay-ok")
        stored = await repository.persist_source_batch(
            batch=statutory,
            window_start=window_start,
            window_end=window_end,
            lease=lease,
        )
        assert stored.idempotent_replay is False
        assert stored.evidence_stored > 0
        assert await _scalar(
            engine,
            """
            SELECT count(*)
              FROM raw_documents
             WHERE source_id = :source_id
               AND event_time IS NOT NULL
               AND provenance->>'runtime_mode' = 'demo_replay'
               AND provenance->>'release_tier' = 'dev_only'
            """,
            source_id=statutory.source_id,
        ) == len({item.evidence_id for event in statutory.events for item in event.evidence})
        assert await _capability_cursor_state(
            engine,
            source_id=statutory.source_id,
            stream=statutory.stream,
        ) == (1, statutory.coverages[0].progress.business_watermark)

        replay_lease = await repository.acquire_source_lease(
            batch=statutory,
            owner="replay-idempotent",
        )
        replayed = await repository.persist_source_batch(
            batch=statutory,
            window_start=window_start,
            window_end=window_end,
            lease=replay_lease,
        )
        assert replayed.idempotent_replay is True
        assert await _capability_cursor_state(
            engine,
            source_id=statutory.source_id,
            stream=statutory.stream,
        ) == (1, statutory.coverages[0].progress.business_watermark)

        visible_event = statutory.events[-1]
        persisted = await repository.get_event_evidence(
            event_id=visible_event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=window_end,
                revision=visible_event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert persisted is not None
        assert persisted.schema_version == "1.1"
        assert persisted.revision == visible_event.revision

        failure_lease = await repository.acquire_source_lease(
            batch=failure,
            owner="replay-failure",
        )
        before_failure = await _capability_cursor_state(
            engine,
            source_id=failure.source_id,
            stream=failure.stream,
        )
        await repository.record_source_failure(
            batch=failure,
            window_end=window_end,
            lease=failure_lease,
            reason_code="R1_REPLAY_SOURCE_FAILURE",
        )
        assert (
            await _capability_cursor_state(
                engine,
                source_id=failure.source_id,
                stream=failure.stream,
            )
            == before_failure
            == (0, None)
        )

        clock_failure = terminal_gap_batch(
            source_id=DEV_LIVE_SOURCE_IDS["market.snapshot"],
            capability="market.snapshot",
            runtime_mode=SourceRuntimeMode.DEV_LIVE,
            provenance=AkshareDevLiveAdapter("market.snapshot").provenance,
            rights_snapshot_hash=DEV_LIVE_RIGHTS_HASH,
            license_id=DEV_LIVE_LICENSE_ID,
            observed_at=window_end,
            status=SourceCoverageStatus.UNAVAILABLE,
            reason_code="R1_SOURCE_CLOCK_INVALID",
        )
        clock_lease = await repository.acquire_source_lease(
            batch=clock_failure,
            owner="dev-live-clock-failure",
        )
        cursor_before_clock_failure = await _capability_cursor_state(
            engine,
            source_id=clock_failure.source_id,
            stream=clock_failure.stream,
        )
        await repository.record_source_failure(
            batch=clock_failure,
            window_end=window_end,
            lease=clock_lease,
            reason_code="R1_SOURCE_CLOCK_INVALID",
        )
        assert (
            await _capability_cursor_state(
                engine,
                source_id=clock_failure.source_id,
                stream=clock_failure.stream,
            )
            == cursor_before_clock_failure
            == (0, None)
        )
        assert (
            await _scalar(
                engine,
                """
            SELECT count(*)
              FROM source_runs
             WHERE source_id = :source_id
               AND stream = :stream
               AND status = 'failed'
               AND safe_error_code = 'R1_SOURCE_CLOCK_INVALID'
            """,
                source_id=clock_failure.source_id,
                stream=clock_failure.stream,
            )
            == 1
        )
    finally:
        await engine.dispose()


async def _exercise_dev_live_knowledge_time(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    cutoff = datetime(2026, 7, 31, 0, 55, tzinfo=UTC)
    execution_started = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)
    execution_completed = execution_started + timedelta(seconds=2)
    adapter = AkshareDevLiveAdapter("market.snapshot")
    fetch_batch = adapter.normalize(
        _dev_live_fetch_request(capability="market.snapshot", as_of=cutoff),
        [{"日期": "2026-07-30", "收盘": 93}],
        execution_started_at=execution_started,
        execution_completed_at=execution_completed,
    )
    batch = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability="market.snapshot",
        provenance=adapter.provenance,
        license_id=DEV_LIVE_LICENSE_ID,
    )
    try:
        lease = await repository.acquire_source_lease(batch=batch, owner="pit-knowledge-time")
        await repository.persist_source_batch(
            batch=batch,
            window_start=cutoff - timedelta(days=1),
            window_end=cutoff,
            lease=lease,
        )
        event = batch.events[0]
        hidden = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=cutoff,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        visible = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=execution_completed,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )

        wrong_scope = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=execution_completed,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.AUTHORIZED,
            expected_release_tier=SourceReleaseTier.AUTHORIZED,
        )

        out_of_scope_known_at = execution_completed + timedelta(seconds=1)
        out_of_scope_payload = event.model_dump(mode="json")
        out_of_scope_payload.update(
            {
                "revision": event.revision + 1,
                "change_type": BriefingChangeType.UPDATE.value,
                "supersedes_revision": event.revision,
                "revision_known_at": out_of_scope_known_at.isoformat(),
                "as_of": out_of_scope_known_at.isoformat(),
                "runtime_mode": SourceRuntimeMode.AUTHORIZED.value,
            }
        )
        authorized_provenance = SourceProvenance(
            schema_version="1.1",
            provider="authorized_test_provider",
            access_channel=SourceAccessChannel.AUTHORIZED_API,
            adapter_name="authorized_test_adapter",
            adapter_version="1.0.0",
            endpoint_name="authorized.test",
            source_identifier="authorized:test:market.snapshot",
            runtime_mode=SourceRuntimeMode.AUTHORIZED,
            release_tier=SourceReleaseTier.AUTHORIZED,
            rights_status=SourceRightsStatus.APPROVED,
        ).model_dump(mode="json")
        for evidence in out_of_scope_payload["evidence"]:
            evidence["provenance"] = authorized_provenance

        async with engine.begin() as connection:
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO market_events (
                        id, cluster_id, revision, event_type, event_status, direction,
                        materiality, impact_horizon, confidence, event_time, published_at,
                        first_seen_at, valid_from, valid_to, extractor_version, rule_version,
                        payload, logical_event_id, revision_known_at, supersedes_revision_id
                    )
                    SELECT
                        :id, cluster_id, :revision, event_type, event_status, direction,
                        materiality, impact_horizon, confidence, event_time, published_at,
                        first_seen_at, :revision_known_at, valid_to, extractor_version,
                        rule_version, CAST(:payload AS jsonb), logical_event_id,
                        :revision_known_at, id
                      FROM market_events
                     WHERE logical_event_id = :event_id
                       AND revision = :supersedes_revision
                    """
                ),
                {
                    "id": uuid4(),
                    "revision": event.revision + 1,
                    "revision_known_at": out_of_scope_known_at,
                    "payload": json.dumps(out_of_scope_payload),
                    "event_id": event.event_id,
                    "supersedes_revision": event.revision,
                },
            )
            assert inserted.rowcount == 1

        scoped_visible = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=out_of_scope_known_at + timedelta(seconds=1),
                timeline_limit=20,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )

        assert hidden is None
        assert visible is not None
        assert visible.revision_known_at == execution_completed
        assert wrong_scope is None
        assert scoped_visible is not None
        assert scoped_visible.revision == event.revision
        assert [item.revision for item in scoped_visible.revision_timeline.items] == [
            event.revision
        ]
    finally:
        await engine.dispose()


async def _exercise_free_source_v1_2_persistence(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    cutoff = datetime(2026, 7, 31, 0, 55, tzinfo=UTC)
    execution_started = cutoff + timedelta(minutes=10)
    execution_completed = execution_started + timedelta(seconds=2)
    adapter = FreeLiveAdapter("eastmoney_public_market")
    fetch_batch = adapter.normalize(
        _free_fetch_request(adapter=adapter, as_of=cutoff),
        [
            {
                "日期": "2026-07-30",
                "开盘": "91.0",
                "最高": "94.0",
                "最低": "90.0",
                "收盘": "93.0",
                "成交量": "100",
                "成交额": "9300",
            }
        ],
        execution_started_at=execution_started,
        execution_completed_at=execution_completed,
    )
    batch = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    try:
        lease = await repository.acquire_source_lease(
            batch=batch,
            owner="free-source-v1-2",
        )
        stored = await repository.persist_source_batch(
            batch=batch,
            window_start=cutoff - timedelta(days=1),
            window_end=cutoff,
            lease=lease,
        )
        assert stored.idempotent_replay is False
        assert stored.evidence_stored == 1
        assert (
            await _scalar(
                engine,
                "SELECT finished_at FROM source_runs WHERE id = :batch_id",
                batch_id=stored.batch_audit_id,
            )
            == execution_completed
        )
        assert await _capability_refresh_state(
            engine,
            source_id=batch.source_id,
            stream=batch.stream,
        ) == (
            1,
            batch.coverages[0].progress.business_watermark,
            execution_completed,
            batch.next_cursor,
        )
        assert (
            await _scalar(
                engine,
                """
                SELECT count(*)
                  FROM raw_documents
                 WHERE source_id = :source_id
                   AND event_time IS NOT NULL
                   AND provenance->>'schema_version' = '1.2'
                   AND provenance->>'runtime_mode' = 'dev_live'
                   AND provenance->>'access_channel' = 'akshare'
                   AND provenance->>'quality_tier' = 'free_structured'
                   AND provenance->>'evidence_role' = 'structured_helper'
                   AND provenance->>'rights_status' = 'unverified'
                """,
                source_id=batch.source_id,
            )
            == 1
        )

        event = batch.events[0]
        hidden = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=cutoff,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        visible = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=execution_completed,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert hidden is None
        assert visible is not None
        assert visible.schema_version == "1.2"
        assert visible.evidence[0].provenance == adapter.provenance

        replay_lease = await repository.acquire_source_lease(
            batch=batch,
            owner="free-source-v1-2-replay",
        )
        assert replay_lease.provider_cursor == batch.next_cursor
        assert replay_lease.business_watermark == (batch.coverages[0].progress.business_watermark)
        assert replay_lease.last_success_at == execution_completed
        replayed = await repository.persist_source_batch(
            batch=batch,
            window_start=cutoff - timedelta(days=1),
            window_end=cutoff,
            lease=replay_lease,
        )
        assert replayed.idempotent_replay is True
        assert (
            await _scalar(
                engine,
                "SELECT finished_at FROM source_runs WHERE id = :batch_id",
                batch_id=stored.batch_audit_id,
            )
            == execution_completed
        )
        assert await _capability_refresh_state(
            engine,
            source_id=batch.source_id,
            stream=batch.stream,
        ) == (
            1,
            batch.coverages[0].progress.business_watermark,
            execution_completed,
            batch.next_cursor,
        )

        stale_success = await repository.acquire_source_lease(
            batch=batch,
            owner="free-source-success-same-owner",
            lease_seconds=300,
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE source_cursors
                       SET lease_expires_at = clock_timestamp() - interval '1 second'
                     WHERE source_id = :source_id AND stream = :stream
                    """
                ),
                {"source_id": batch.source_id, "stream": batch.stream},
            )
        current_success = await repository.acquire_source_lease(
            batch=batch,
            owner="free-source-success-same-owner",
            lease_seconds=300,
        )
        assert current_success.lease_expires_at != stale_success.lease_expires_at
        with pytest.raises(ApplicationError, match="R1_CURSOR_CAS_CONFLICT"):
            await repository.persist_source_batch(
                batch=batch,
                window_start=cutoff - timedelta(days=1),
                window_end=cutoff + timedelta(minutes=1),
                lease=stale_success,
            )
        async with engine.connect() as connection:
            success_owner_and_expiry = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT lease_owner, lease_expires_at
                              FROM source_cursors
                             WHERE source_id = :source_id AND stream = :stream
                            """
                        ),
                        {"source_id": batch.source_id, "stream": batch.stream},
                    )
                )
                .mappings()
                .one()
            )
        assert success_owner_and_expiry["lease_owner"] == current_success.owner
        assert success_owner_and_expiry["lease_expires_at"] == current_success.lease_expires_at
        advanced = await repository.persist_source_batch(
            batch=batch,
            window_start=cutoff - timedelta(days=1),
            window_end=cutoff + timedelta(minutes=1),
            lease=current_success,
        )
        assert advanced.idempotent_replay is False
        assert await _capability_refresh_state(
            engine,
            source_id=batch.source_id,
            stream=batch.stream,
        ) == (
            2,
            batch.coverages[0].progress.business_watermark,
            execution_completed,
            batch.next_cursor,
        )

        empty_adapter = FreeLiveAdapter("gov_cn_public_macro")
        empty_fetch = empty_adapter.normalize(
            _free_fetch_request(adapter=empty_adapter, as_of=cutoff),
            [],
            execution_started_at=execution_started,
            execution_completed_at=execution_completed,
            response_etag='"gov-policy-v1"',
            response_last_modified="Thu, 30 Jul 2026 10:00:00 GMT",
        )
        empty_batch = source_batch_from_fetch(
            fetch_batch=empty_fetch,
            capability=empty_adapter.capability,
            provenance=empty_adapter.provenance,
            license_id=FREE_SOURCE_LICENSE_ID,
        )
        assert empty_batch.events == ()
        assert empty_batch.coverages[0].status is SourceCoverageStatus.OK
        assert empty_batch.coverages[0].gap_reason is None
        assert empty_batch.coverages[0].progress.business_watermark == cutoff
        assert empty_batch.coverages[0].last_success_at == execution_completed
        assert empty_batch.next_cursor is not None
        assert json.loads(empty_batch.next_cursor) == {
            "etag": '"gov-policy-v1"',
            "last_modified": "Thu, 30 Jul 2026 10:00:00 GMT",
            "version": 1,
            "window_end": cutoff.isoformat(),
        }
        empty_lease = await repository.acquire_source_lease(
            batch=empty_batch,
            owner="free-source-empty",
        )
        before_empty = await _capability_refresh_state(
            engine,
            source_id=empty_batch.source_id,
            stream=empty_batch.stream,
        )
        assert before_empty == (0, None, None, None)
        await repository.persist_source_batch(
            batch=empty_batch,
            window_start=cutoff - timedelta(days=1),
            window_end=cutoff,
            lease=empty_lease,
        )
        assert await _capability_refresh_state(
            engine,
            source_id=empty_batch.source_id,
            stream=empty_batch.stream,
        ) == (
            1,
            cutoff,
            execution_completed,
            empty_batch.next_cursor,
        )
        resumed_empty_lease = await repository.acquire_source_lease(
            batch=empty_batch,
            owner="free-source-empty-resume",
        )
        assert resumed_empty_lease.provider_cursor == empty_batch.next_cursor
        assert resumed_empty_lease.business_watermark == cutoff
        assert resumed_empty_lease.last_success_at == execution_completed
        resumed_empty = await repository.persist_source_batch(
            batch=empty_batch,
            window_start=cutoff - timedelta(days=1),
            window_end=cutoff,
            lease=resumed_empty_lease,
        )
        assert resumed_empty.idempotent_replay is True

        failure = free_source_gap(
            source_id="sse_public_disclosure",
            observed_at=cutoff,
            reason_code="FREE_SOURCE_UNAVAILABLE",
            status=SourceCoverageStatus.UNAVAILABLE,
        )
        failure_lease = await repository.acquire_source_lease(
            batch=failure,
            owner="free-source-v1-2-failure",
        )
        before_failure = await _capability_refresh_state(
            engine,
            source_id=failure.source_id,
            stream=failure.stream,
        )
        await repository.record_source_failure(
            batch=failure,
            window_end=cutoff,
            lease=failure_lease,
            reason_code="FREE_SOURCE_UNAVAILABLE",
        )
        assert (
            await _capability_refresh_state(
                engine,
                source_id=failure.source_id,
                stream=failure.stream,
            )
            == before_failure
            == (0, None, None, None)
        )

        stale = await repository.acquire_source_lease(
            batch=failure,
            owner="free-source-same-owner",
            lease_seconds=300,
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE source_cursors
                       SET lease_expires_at = clock_timestamp() - interval '1 second'
                     WHERE source_id = :source_id AND stream = :stream
                    """
                ),
                {"source_id": failure.source_id, "stream": failure.stream},
            )
        current = await repository.acquire_source_lease(
            batch=failure,
            owner="free-source-same-owner",
            lease_seconds=300,
        )
        assert current.lease_expires_at != stale.lease_expires_at
        with pytest.raises(ApplicationError, match="R1_CURSOR_CAS_CONFLICT"):
            await repository.record_source_failure(
                batch=failure,
                window_end=cutoff + timedelta(minutes=1),
                lease=stale,
                reason_code="FREE_SOURCE_STALE_LEASE",
            )
        async with engine.connect() as connection:
            owner_and_expiry = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT lease_owner, lease_expires_at
                              FROM source_cursors
                             WHERE source_id = :source_id AND stream = :stream
                            """
                        ),
                        {"source_id": failure.source_id, "stream": failure.stream},
                    )
                )
                .mappings()
                .one()
            )
        assert owner_and_expiry["lease_owner"] == current.owner
        assert owner_and_expiry["lease_expires_at"] == current.lease_expires_at
        await repository.record_source_failure(
            batch=failure,
            window_end=cutoff + timedelta(minutes=2),
            lease=current,
            reason_code="FREE_SOURCE_UNAVAILABLE",
        )
    finally:
        await engine.dispose()


async def _record_r1_batch(
    repository: PostgresR1PremarketRepository,
    *,
    batch: R1SourceBatch,
    window_start: datetime,
    window_end: datetime,
    owner: str,
) -> None:
    lease = await repository.acquire_source_lease(batch=batch, owner=owner)
    if batch.coverages[0].status is SourceCoverageStatus.OK:
        await repository.persist_source_batch(
            batch=batch,
            window_start=window_start,
            window_end=window_end,
            lease=lease,
        )
    else:
        reason_code = batch.coverages[0].gap_reason
        assert reason_code is not None
        await repository.record_source_failure(
            batch=batch,
            window_end=window_end,
            lease=lease,
            reason_code=reason_code,
        )


def _official_macro_success_batch(
    *,
    source_id: str,
    window_end: datetime,
    execution_started_at: datetime,
    execution_completed_at: datetime,
    include_event: bool = True,
) -> R1SourceBatch:
    adapter = FreeLiveAdapter(source_id)
    provider_values = {
        "gov_cn_public_macro": (
            "国务院办公厅",
            "政策文件",
            "https://www.gov.cn/zhengce/content/2026-08/03/content_0000001.htm",
        ),
        "miit_public_macro": (
            "工业和信息化部",
            "政务公开",
            "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/art/2026/art_test.html",
        ),
    }
    publisher, category, url = provider_values[source_id]
    fetch_batch = adapter.normalize(
        _free_fetch_request(adapter=adapter, as_of=window_end),
        (
            [
                {
                    "title": f"{publisher}开发期元数据线索",
                    "published_at": (window_end - timedelta(hours=1)).isoformat(),
                    "publisher": publisher,
                    "category": category,
                    "url": url,
                }
            ]
            if include_event
            else []
        ),
        execution_started_at=execution_started_at,
        execution_completed_at=execution_completed_at,
    )
    return source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )


def _official_macro_window_batch(
    *,
    window_end: datetime,
    execution_started_at: datetime,
    execution_completed_at: datetime,
    title: str | None,
    response_etag: str,
    request_cursor: str | None,
    publisher: str = "国务院办公厅",
    category: str = "政策文件",
    published_at: str = "2026-08-03T00:00:00+00:00",
    canonical_url: str = ("https://www.gov.cn/zhengce/content/2026-08/03/content_0000001.htm"),
) -> R1SourceBatch:
    adapter = FreeLiveAdapter("gov_cn_public_macro")
    request = _free_fetch_request(adapter=adapter, as_of=window_end).model_copy(
        update={"cursor": request_cursor}
    )
    rows = []
    if title is not None:
        rows.append(
            {
                "document_id": "gov-policy-window-001",
                "title": title,
                "published_at": published_at,
                "publisher": publisher,
                "category": category,
                "url": canonical_url,
            }
        )
    fetch_batch = adapter.normalize(
        request,
        rows,
        execution_started_at=execution_started_at,
        execution_completed_at=execution_completed_at,
        response_etag=response_etag,
        response_last_modified="Mon, 03 Aug 2026 00:00:00 GMT",
    )
    return source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )


async def _exercise_official_same_window_correction(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    source_ids = ("gov_cn_public_macro",)
    window_one = (
        datetime(2026, 7, 31, 7, 0, tzinfo=UTC),
        datetime(2026, 8, 3, 0, 55, tzinfo=UTC),
    )
    window_two = (
        datetime(2026, 8, 3, 7, 0, tzinfo=UTC),
        datetime(2026, 8, 4, 0, 55, tzinfo=UTC),
    )
    first_completed = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    no_change_completed = datetime(2026, 8, 4, 0, 57, tzinfo=UTC)
    correction_completed = datetime(2026, 8, 4, 1, 2, tzinfo=UTC)
    revert_completed = datetime(2026, 8, 4, 1, 7, tzinfo=UTC)
    first_batch = _official_macro_window_batch(
        window_end=window_one[1],
        execution_started_at=first_completed - timedelta(seconds=2),
        execution_completed_at=first_completed,
        title="国务院办公厅开发期政策元数据线索",
        response_etag='"policy-v1"',
        request_cursor=None,
    )
    try:
        first_lease = await repository.acquire_source_lease(
            batch=first_batch,
            owner="official-window-one",
        )
        first_stored = await repository.persist_source_batch(
            batch=first_batch,
            window_start=window_one[0],
            window_end=window_one[1],
            lease=first_lease,
        )
        first = await repository.generate_and_store_r1_briefing(
            window_start=window_one[0],
            window_end=window_one[1],
            generated_at=first_completed,
            source_ids=source_ids,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        first_item = first.briefing.sections.macro_flashes.items[0]
        assert first_stored.event_revisions_stored == 1
        assert first_item.event_revision == 1
        assert first_item.change_type is BriefingChangeType.NEW
        assert await _capability_refresh_state(
            engine,
            source_id=first_batch.source_id,
            stream=first_batch.stream,
        ) == (1, window_one[1], first_completed, first_batch.next_cursor)

        no_change_batch = _official_macro_window_batch(
            window_end=window_two[1],
            execution_started_at=no_change_completed - timedelta(seconds=2),
            execution_completed_at=no_change_completed,
            title=None,
            response_etag='"policy-v1"',
            request_cursor=first_batch.next_cursor,
        )
        no_change_lease = await repository.acquire_source_lease(
            batch=no_change_batch,
            owner="official-window-two-no-change",
        )
        no_change_stored = await repository.persist_source_batch(
            batch=no_change_batch,
            window_start=window_two[0],
            window_end=window_two[1],
            lease=no_change_lease,
        )
        no_change = await repository.generate_and_store_r1_briefing(
            window_start=window_two[0],
            window_end=window_two[1],
            generated_at=no_change_completed,
            source_ids=source_ids,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        assert no_change_batch.events == ()
        assert no_change_stored.event_revisions_stored == 0
        assert no_change.event_count == 0
        assert no_change.evidence_count == 0
        assert no_change.briefing.sections.macro_flashes.items == []
        assert await _scalar(engine, "SELECT count(*) FROM market_events") == 1
        assert await _scalar(engine, "SELECT count(*) FROM raw_documents") == 1
        assert await _capability_refresh_state(
            engine,
            source_id=no_change_batch.source_id,
            stream=no_change_batch.stream,
        ) == (2, window_two[1], no_change_completed, no_change_batch.next_cursor)

        correction_batch = _official_macro_window_batch(
            window_end=window_two[1],
            execution_started_at=correction_completed - timedelta(seconds=2),
            execution_completed_at=correction_completed,
            title="国务院办公厅开发期政策元数据更正线索",
            response_etag='"policy-v2"',
            request_cursor=no_change_batch.next_cursor,
        )
        assert correction_batch.events[0].event_id == first_item.event_id
        correction_lease = await repository.acquire_source_lease(
            batch=correction_batch,
            owner="official-window-two-correction",
        )
        correction_stored = await repository.persist_source_batch(
            batch=correction_batch,
            window_start=window_two[0],
            window_end=window_two[1],
            lease=correction_lease,
        )
        correction = await repository.generate_and_store_r1_briefing(
            window_start=window_two[0],
            window_end=window_two[1],
            generated_at=correction_completed,
            source_ids=source_ids,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        correction_item = correction.briefing.sections.macro_flashes.items[0]
        assert correction_stored.idempotent_replay is False
        assert correction_stored.event_revisions_stored == 1
        assert correction.event_count == 1
        assert correction_item.event_id == first_item.event_id
        assert correction_item.event_revision == 2
        assert correction_item.change_type is BriefingChangeType.CORRECTION
        assert correction.briefing.comparison_since == window_two[0]
        assert correction.briefing.as_of == correction_completed

        before = await repository.get_event_evidence(
            event_id=first_item.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=correction_completed - timedelta(microseconds=1),
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        current = await repository.get_event_evidence(
            event_id=first_item.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=correction_completed,
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert before is not None and before.revision == 1
        assert current is not None and current.revision == 2
        assert current.change_type is BriefingChangeType.CORRECTION
        assert current.supersedes_revision == 1
        assert current.correction is not None
        assert len(current.evidence) == 2
        assert await _scalar(engine, "SELECT count(*) FROM market_events") == 2
        assert await _scalar(engine, "SELECT count(*) FROM raw_documents") == 2
        assert await _scalar(engine, "SELECT count(*) FROM event_documents") == 3
        assert (
            await _scalar(
                engine,
                "SELECT count(*) FROM source_runs WHERE scheduled_for = :window_end",
                window_end=window_two[1],
            )
            == 2
        )
        assert await _capability_refresh_state(
            engine,
            source_id=correction_batch.source_id,
            stream=correction_batch.stream,
        ) == (3, window_two[1], correction_completed, correction_batch.next_cursor)

        counts_before_replay = (
            await _scalar(engine, "SELECT count(*) FROM market_events"),
            await _scalar(engine, "SELECT count(*) FROM raw_documents"),
            await _scalar(engine, "SELECT count(*) FROM event_documents"),
            await _scalar(engine, "SELECT count(*) FROM source_runs"),
        )
        replay_lease = await repository.acquire_source_lease(
            batch=correction_batch,
            owner="official-window-two-correction-replay",
        )
        replayed = await repository.persist_source_batch(
            batch=correction_batch,
            window_start=window_two[0],
            window_end=window_two[1],
            lease=replay_lease,
        )
        assert replayed.idempotent_replay is True
        assert (
            await _scalar(engine, "SELECT count(*) FROM market_events"),
            await _scalar(engine, "SELECT count(*) FROM raw_documents"),
            await _scalar(engine, "SELECT count(*) FROM event_documents"),
            await _scalar(engine, "SELECT count(*) FROM source_runs"),
        ) == counts_before_replay
        assert await _capability_refresh_state(
            engine,
            source_id=correction_batch.source_id,
            stream=correction_batch.stream,
        ) == (3, window_two[1], correction_completed, correction_batch.next_cursor)

        reverted_batch = _official_macro_window_batch(
            window_end=window_two[1],
            execution_started_at=revert_completed - timedelta(seconds=2),
            execution_completed_at=revert_completed,
            title="国务院办公厅开发期政策元数据线索",
            response_etag='"policy-v3"',
            request_cursor=correction_batch.next_cursor,
        )
        reverted_lease = await repository.acquire_source_lease(
            batch=reverted_batch,
            owner="official-window-two-revert",
        )
        reverted_stored = await repository.persist_source_batch(
            batch=reverted_batch,
            window_start=window_two[0],
            window_end=window_two[1],
            lease=reverted_lease,
        )
        reverted = await repository.get_event_evidence(
            event_id=first_item.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=revert_completed,
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        immediately_before_revert = await repository.get_event_evidence(
            event_id=first_item.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=revert_completed - timedelta(microseconds=1),
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert reverted_stored.event_revisions_stored == 1
        assert immediately_before_revert is not None
        assert immediately_before_revert.revision == 2
        assert immediately_before_revert.facts[0].value.value.endswith("更正线索")
        assert reverted is not None
        assert reverted.revision == 3
        assert reverted.change_type is BriefingChangeType.CORRECTION
        assert reverted.supersedes_revision == 2
        assert reverted.facts[0].value.value == "国务院办公厅开发期政策元数据线索"
        assert reverted.correction is not None
        assert reverted.correction.previous_value.value.endswith("更正线索")
        assert reverted.correction.current_value.value == "国务院办公厅开发期政策元数据线索"
        assert [item.revision for item in reverted.revision_timeline.items] == [3, 2, 1]
        assert len(reverted.evidence) == 2
        assert set(reverted.facts[0].evidence_ids) == {
            reverted_batch.events[0].facts[0].evidence_ids[0]
        }
        assert await _scalar(engine, "SELECT count(*) FROM market_events") == 3
        assert await _scalar(engine, "SELECT count(*) FROM raw_documents") == 2
        assert await _scalar(engine, "SELECT count(*) FROM event_documents") == 5
        assert await _capability_refresh_state(
            engine,
            source_id=reverted_batch.source_id,
            stream=reverted_batch.stream,
        ) == (4, window_two[1], revert_completed, reverted_batch.next_cursor)

        metadata_states = (
            {
                "publisher": "国务院",
                "category": "政策文件",
                "published_at": "2026-08-03T00:00:00+00:00",
                "canonical_url": (
                    "https://www.gov.cn/zhengce/content/2026-08/03/content_0000001.htm"
                ),
            },
            {
                "publisher": "国务院",
                "category": "政策法规",
                "published_at": "2026-08-03T00:00:00+00:00",
                "canonical_url": (
                    "https://www.gov.cn/zhengce/content/2026-08/03/content_0000001.htm"
                ),
            },
            {
                "publisher": "国务院",
                "category": "政策法规",
                "published_at": "2026-08-03T00:01:00+00:00",
                "canonical_url": (
                    "https://www.gov.cn/zhengce/content/2026-08/03/content_0000001.htm"
                ),
            },
            {
                "publisher": "国务院",
                "category": "政策法规",
                "published_at": "2026-08-03T00:01:00+00:00",
                "canonical_url": (
                    "https://www.gov.cn/zhengce/content/2026-08/03/content_0000002.htm"
                ),
            },
        )
        previous_batch = reverted_batch
        metadata_hashes: list[str] = []
        final_batch = reverted_batch
        final_completed = revert_completed
        for offset, metadata in enumerate(metadata_states, start=1):
            completed_at = revert_completed + timedelta(minutes=offset * 5)
            batch = _official_macro_window_batch(
                window_end=window_two[1],
                execution_started_at=completed_at - timedelta(seconds=2),
                execution_completed_at=completed_at,
                title="国务院办公厅开发期政策元数据线索",
                response_etag=f'"policy-v{offset + 3}"',
                request_cursor=previous_batch.next_cursor,
                **metadata,
            )
            lease = await repository.acquire_source_lease(
                batch=batch,
                owner=f"official-window-two-metadata-{offset}",
            )
            stored = await repository.persist_source_batch(
                batch=batch,
                window_start=window_two[0],
                window_end=window_two[1],
                lease=lease,
            )
            before_update = await repository.get_event_evidence(
                event_id=first_item.event_id,
                selector=EventEvidenceRevisionSelector(
                    schema_version="1.0",
                    as_of=completed_at - timedelta(microseconds=1),
                ),
                expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
                expected_release_tier=SourceReleaseTier.DEV_ONLY,
            )
            updated = await repository.get_event_evidence(
                event_id=first_item.event_id,
                selector=EventEvidenceRevisionSelector(
                    schema_version="1.0",
                    as_of=completed_at,
                ),
                expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
                expected_release_tier=SourceReleaseTier.DEV_ONLY,
            )
            expected_revision = offset + 3
            assert stored.event_revisions_stored == 1
            assert before_update is not None and before_update.revision == expected_revision - 1
            assert updated is not None and updated.revision == expected_revision
            assert updated.change_type is BriefingChangeType.UPDATE
            assert updated.supersedes_revision == expected_revision - 1
            assert updated.correction is None
            assert updated.facts[0].value.value == "国务院办公厅开发期政策元数据线索"
            assert len(updated.evidence) == 1
            expected_evidence = batch.events[0].evidence[0]
            assert updated.evidence[0] == expected_evidence
            assert updated.facts[0].evidence_ids == [expected_evidence.evidence_id]
            metadata_hashes.append(expected_evidence.content_sha256)
            assert await _capability_refresh_state(
                engine,
                source_id=batch.source_id,
                stream=batch.stream,
            ) == (
                expected_revision + 1,
                window_two[1],
                completed_at,
                batch.next_cursor,
            )
            previous_batch = batch
            final_batch = batch
            final_completed = completed_at

        assert len(set(metadata_hashes)) == len(metadata_states)
        final = await repository.get_event_evidence(
            event_id=first_item.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=final_completed,
            ),
            expected_runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert final is not None and final.revision == 7
        assert [item.revision for item in final.revision_timeline.items] == [7, 6, 5, 4, 3, 2, 1]
        assert str(final.evidence[0].source_url).endswith("content_0000002.htm")
        assert final.evidence[0].published_at == datetime(2026, 8, 3, 0, 1, tzinfo=UTC)
        assert await _scalar(engine, "SELECT count(*) FROM market_events") == 7
        assert await _scalar(engine, "SELECT count(*) FROM raw_documents") == 6
        assert await _scalar(engine, "SELECT count(*) FROM event_documents") == 9

        final_counts = (
            await _scalar(engine, "SELECT count(*) FROM market_events"),
            await _scalar(engine, "SELECT count(*) FROM raw_documents"),
            await _scalar(engine, "SELECT count(*) FROM event_documents"),
            await _scalar(engine, "SELECT count(*) FROM source_runs"),
        )
        final_replay_lease = await repository.acquire_source_lease(
            batch=final_batch,
            owner="official-window-two-final-replay",
        )
        final_replayed = await repository.persist_source_batch(
            batch=final_batch,
            window_start=window_two[0],
            window_end=window_two[1],
            lease=final_replay_lease,
        )
        assert final_replayed.idempotent_replay is True
        assert (
            await _scalar(engine, "SELECT count(*) FROM market_events"),
            await _scalar(engine, "SELECT count(*) FROM raw_documents"),
            await _scalar(engine, "SELECT count(*) FROM event_documents"),
            await _scalar(engine, "SELECT count(*) FROM source_runs"),
        ) == final_counts
        assert await _capability_refresh_state(
            engine,
            source_id=final_batch.source_id,
            stream=final_batch.stream,
        ) == (8, window_two[1], final_completed, final_batch.next_cursor)
    finally:
        await engine.dispose()


def _assert_five_section_gap_briefing(
    briefing: PremarketBriefingViewV1_2,
    *,
    expected_macro_items: int,
) -> None:
    assert briefing.schema_version == "1.2"
    assert briefing.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
    assert briefing.release_tier is SourceReleaseTier.DEV_ONLY
    assert {item.capability for item in briefing.source_coverage} == {
        "statutory.announcements",
        "financial.changes",
        "market.snapshot",
        "company_industry.news",
        "macro.flashes",
    }
    projected = [item for item in briefing.source_coverage if item.capability != "macro.flashes"]
    assert len(projected) == 4
    assert all(item.status is SourceCoverageStatus.NOT_QUALIFIED for item in projected)
    assert all(item.gap_reason == "R1_NO_ACTIVE_SOURCE_FOR_CAPABILITY" for item in projected)
    assert all(item.last_success_at is None for item in projected)
    assert all(item.progress.business_watermark is None for item in projected)
    assert all(item.progress.advance_state is SourceAdvanceState.NOT_ADVANCED for item in projected)
    assert all(
        item.provenance.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
        and item.provenance.release_tier is SourceReleaseTier.DEV_ONLY
        and item.provenance.rights_status is SourceRightsStatusV1_2.UNVERIFIED
        and item.provenance.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
        and item.provenance.quality_tier is SourceQualityTier.FREE_OFFICIAL
        and item.provenance.evidence_role is SourceEvidenceRole.DISCOVERY
        for item in briefing.source_coverage
    )
    assert briefing.sections.statutory_announcements.items == []
    assert briefing.sections.financial_changes.items == []
    assert briefing.sections.market_snapshot.items == []
    assert briefing.sections.company_industry_news.items == []
    assert len(briefing.sections.macro_flashes.items) == expected_macro_items


async def _rewrite_terminal_run_capability(
    engine: AsyncEngine,
    *,
    source_id: str,
    window_end: datetime,
    capability: str,
) -> None:
    stream = f"r1_premarket:{SourceRuntimeModeV1_2.DEV_LIVE.value}:{capability}"
    async with engine.begin() as connection:
        updated = await connection.execute(
            text(
                """
                UPDATE source_runs
                   SET stream = :stream,
                       metrics = jsonb_set(
                           jsonb_set(
                               metrics,
                               '{capability}',
                               to_jsonb(CAST(:capability AS text))
                           ),
                           '{coverages,0,capability}',
                           to_jsonb(CAST(:capability AS text))
                       )
                 WHERE source_id = :source_id
                   AND scheduled_for = :window_end
                """
            ),
            {
                "stream": stream,
                "capability": capability,
                "source_id": source_id,
                "window_end": window_end,
            },
        )
    assert updated.rowcount == 1


async def _exercise_cross_process_research_quota(database_url: str) -> None:
    """Independent DB sessions exercise the same lock boundary used by worker processes."""

    engines = (create_async_engine(database_url), create_async_engine(database_url))
    entered_at: list[float] = []

    async def reserve(engine: AsyncEngine) -> None:
        limiter = PostgresAccountRateLimiter(engine, minimum_interval_seconds=2.0)
        await limiter.wait(asyncio.sleep)
        entered_at.append(monotonic())

    try:
        await asyncio.gather(*(reserve(engine) for engine in engines))
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines))

    assert len(entered_at) == 2
    assert max(entered_at) - min(entered_at) >= 1.95


async def _exercise_active_macro_gap_projection(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    active_source_ids = ("gov_cn_public_macro", "miit_public_macro")
    first_window_start = datetime(2026, 8, 2, 0, 55, tzinfo=UTC)
    first_window_end = datetime(2026, 8, 3, 0, 55, tzinfo=UTC)
    first_execution_started = first_window_end + timedelta(minutes=1)
    first_execution_completed = first_execution_started + timedelta(seconds=2)
    gov_success = _official_macro_success_batch(
        source_id=active_source_ids[0],
        window_end=first_window_end,
        execution_started_at=first_execution_started,
        execution_completed_at=first_execution_completed,
    )
    miit_failure = free_source_gap(
        source_id=active_source_ids[1],
        observed_at=first_window_end,
        reason_code="FREE_SOURCE_UNAVAILABLE",
        status=SourceCoverageStatus.UNAVAILABLE,
    )
    try:
        await _record_r1_batch(
            repository,
            batch=gov_success,
            window_start=first_window_start,
            window_end=first_window_end,
            owner="gap-projection-gov-success",
        )
        await _record_r1_batch(
            repository,
            batch=miit_failure,
            window_start=first_window_start,
            window_end=first_window_end,
            owner="gap-projection-miit-failure",
        )
        await _rewrite_terminal_run_capability(
            engine,
            source_id=active_source_ids[0],
            window_end=first_window_end,
            capability="unknown.capability",
        )
        with pytest.raises(
            ApplicationError,
            match="R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
        ) as unknown_capability:
            await repository.generate_and_store_r1_briefing(
                window_start=first_window_start,
                window_end=first_window_end,
                generated_at=first_window_end + timedelta(minutes=10),
                source_ids=active_source_ids,
                runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            )
        assert unknown_capability.value.retryable is True
        assert await _scalar(engine, "SELECT count(*) FROM briefings") == 0
        await _rewrite_terminal_run_capability(
            engine,
            source_id=active_source_ids[0],
            window_end=first_window_end,
            capability="macro.flashes",
        )
        first = await repository.generate_and_store_r1_briefing(
            window_start=first_window_start,
            window_end=first_window_end,
            generated_at=first_window_end + timedelta(minutes=10),
            source_ids=active_source_ids,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        replay = await repository.generate_and_store_r1_briefing(
            window_start=first_window_start,
            window_end=first_window_end,
            generated_at=first_window_end + timedelta(minutes=10),
            source_ids=active_source_ids,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        assert isinstance(first.briefing, PremarketBriefingViewV1_2)
        _assert_five_section_gap_briefing(first.briefing, expected_macro_items=1)
        assert first.reason_codes == (
            "FREE_SOURCE_UNAVAILABLE",
            "R1_NO_ACTIVE_SOURCE_FOR_CAPABILITY",
        )
        assert replay.idempotent_replay is True
        assert replay.briefing.briefing_id == first.briefing.briefing_id

        primary_key = (
            first.briefing.ingestion_progress.source_id,
            first.briefing.ingestion_progress.capability,
        )
        actual_macro = {
            (item.source_id, item.capability): item
            for item in first.briefing.source_coverage
            if item.capability == "macro.flashes"
        }
        assert primary_key in actual_macro
        primary_coverage = actual_macro[primary_key]
        assert primary_coverage.status is SourceCoverageStatus.UNAVAILABLE
        assert first.briefing.ingestion_progress.business_watermark == (
            primary_coverage.progress.business_watermark
        )
        assert (
            first.briefing.ingestion_progress.batch_audit_id
            == primary_coverage.progress.batch_audit_id
        )
        assert (
            first.briefing.ingestion_progress.advance_state
            is primary_coverage.progress.advance_state
        )
        assert first.briefing.ingestion_progress.data_mode is primary_coverage.progress.data_mode
        assert first.briefing.ingestion_progress.provenance == primary_coverage.provenance

        assert (
            await _scalar(
                engine,
                """
                SELECT count(*)
                  FROM source_runs
                 WHERE scheduled_for = :window_end
                   AND metrics->>'capability' <> 'macro.flashes'
                """,
                window_end=first_window_end,
            )
            == 0
        )
        assert (
            await _scalar(
                engine,
                "SELECT count(*) FROM source_runs WHERE scheduled_for = :window_end",
                window_end=first_window_end,
            )
            == 2
        )
        async with engine.connect() as connection:
            registered_sources = tuple(
                row[0]
                for row in (
                    await connection.execute(text("SELECT id FROM data_sources ORDER BY id"))
                ).all()
            )
        assert registered_sources == active_source_ids

        second_window_start = first_window_start + timedelta(days=1)
        second_window_end = first_window_end + timedelta(days=1)
        gov_failure = free_source_gap(
            source_id=active_source_ids[0],
            observed_at=second_window_end,
            reason_code="FREE_SOURCE_SCHEMA_CHANGED",
            status=SourceCoverageStatus.UNAVAILABLE,
        )
        second_miit_failure = free_source_gap(
            source_id=active_source_ids[1],
            observed_at=second_window_end,
            reason_code="FREE_SOURCE_RATE_LIMITED",
            status=SourceCoverageStatus.UNAVAILABLE,
        )
        await _record_r1_batch(
            repository,
            batch=gov_failure,
            window_start=second_window_start,
            window_end=second_window_end,
            owner="gap-projection-gov-failure",
        )
        with pytest.raises(ApplicationError, match="R1_SOURCE_TERMINAL_STATE_INCOMPLETE"):
            await repository.generate_and_store_r1_briefing(
                window_start=second_window_start,
                window_end=second_window_end,
                generated_at=second_window_end + timedelta(minutes=10),
                source_ids=active_source_ids,
                runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
            )
        await _record_r1_batch(
            repository,
            batch=second_miit_failure,
            window_start=second_window_start,
            window_end=second_window_end,
            owner="gap-projection-miit-second-failure",
        )
        second = await repository.generate_and_store_r1_briefing(
            window_start=second_window_start,
            window_end=second_window_end,
            generated_at=second_window_end + timedelta(minutes=10),
            source_ids=active_source_ids,
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        assert isinstance(second.briefing, PremarketBriefingViewV1_2)
        _assert_five_section_gap_briefing(second.briefing, expected_macro_items=0)
        assert second.event_count == 0
        assert second.evidence_count == 0
        assert set(second.reason_codes) == {
            "FREE_SOURCE_RATE_LIMITED",
            "FREE_SOURCE_SCHEMA_CHANGED",
            "R1_NO_ACTIVE_SOURCE_FOR_CAPABILITY",
        }
        assert (
            await _scalar(
                engine,
                "SELECT count(*) FROM data_sources WHERE NOT (id = ANY(:source_ids))",
                source_ids=list(active_source_ids),
            )
            == 0
        )
    finally:
        await engine.dispose()


async def _exercise_dual_official_source_effective_as_of(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    window_start = datetime(2026, 8, 3, 0, 55, tzinfo=UTC)
    window_end = datetime(2026, 8, 4, 0, 55, tzinfo=UTC)
    gov_finished_at = window_end + timedelta(minutes=1)
    miit_finished_at = window_end + timedelta(minutes=5)
    gov = _official_macro_success_batch(
        source_id="gov_cn_public_macro",
        window_end=window_end,
        execution_started_at=gov_finished_at - timedelta(seconds=2),
        execution_completed_at=gov_finished_at,
    )
    miit = _official_macro_success_batch(
        source_id="miit_public_macro",
        window_end=window_end,
        execution_started_at=miit_finished_at - timedelta(seconds=2),
        execution_completed_at=miit_finished_at,
        include_event=False,
    )
    event = gov.events[0]
    try:
        await _record_r1_batch(
            repository,
            batch=gov,
            window_start=window_start,
            window_end=window_end,
            owner="dual-source-gov",
        )
        await _record_r1_batch(
            repository,
            batch=miit,
            window_start=window_start,
            window_end=window_end,
            owner="dual-source-miit",
        )
        stored = await repository.generate_and_store_r1_briefing(
            window_start=window_start,
            window_end=window_end,
            generated_at=window_end + timedelta(minutes=10),
            source_ids=("gov_cn_public_macro", "miit_public_macro"),
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
        assert isinstance(stored.briefing, PremarketBriefingViewV1_2)
        assert stored.briefing.as_of == miit_finished_at
        assert stored.event_count == 1

        before_known = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=gov_finished_at - timedelta(microseconds=1),
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert before_known is None
        at_known = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=gov_finished_at,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert at_known is not None
        assert at_known.as_of == gov_finished_at
        assert at_known.revision_known_at == gov_finished_at
        briefing_evidence = await repository.get_event_evidence(
            event_id=event.event_id,
            selector=EventEvidenceRevisionSelector(
                schema_version="1.0",
                as_of=stored.briefing.as_of,
                revision=event.revision,
            ),
            expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            expected_release_tier=SourceReleaseTier.DEV_ONLY,
        )
        assert briefing_evidence is not None
        assert briefing_evidence.as_of == miit_finished_at
        assert briefing_evidence.revision_known_at == gov_finished_at
        validate_briefing_evidence_projection(
            briefing=stored.briefing,
            evidence_by_event_id={event.event_id: briefing_evidence},
        )

        async with engine.connect() as connection:
            persisted = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT payload->>'as_of' AS as_of,
                                   payload->>'revision_known_at' AS revision_known_at
                              FROM market_events
                             WHERE logical_event_id = :event_id
                               AND revision = :revision
                            """
                        ),
                        {"event_id": event.event_id, "revision": event.revision},
                    )
                )
                .mappings()
                .one()
            )
        assert datetime.fromisoformat(persisted["as_of"]) == gov_finished_at
        assert datetime.fromisoformat(persisted["revision_known_at"]) == gov_finished_at
    finally:
        await engine.dispose()


async def _exercise_provider_neutral_briefing(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    window_start = datetime(2026, 7, 28, 7, tzinfo=UTC)
    window_end = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
    generated_at = window_end + timedelta(minutes=10)
    batches = build_demo_replay_batches(
        window_start=window_start,
        window_end=window_end,
        instrument_id="XSHG:603986",
    )
    try:
        for batch in batches:
            lease = await repository.acquire_source_lease(
                batch=batch,
                owner=f"briefing-{batch.capability}",
            )
            await repository.persist_source_batch(
                batch=batch,
                window_start=window_start,
                window_end=window_end,
                lease=lease,
            )

        monkeypatch.setattr(
            runtime_premarket,
            "get_settings",
            lambda: SimpleNamespace(
                database_url=SimpleNamespace(get_secret_value=lambda: database_url)
            ),
        )
        request = build_runtime_brief_request(
            generated_at,
            runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        )
        first_result = await worker_tasks._execute_premarket_brief_async(
            request,
            generated_at,
        )
        replayed_result = await worker_tasks._execute_premarket_brief_async(
            request,
            generated_at,
        )
        assert first_result.result is not None
        assert replayed_result.result is not None
        async with engine.connect() as connection:
            payload = await connection.scalar(
                text("SELECT payload FROM briefings WHERE id = :briefing_id"),
                {"briefing_id": first_result.result.briefing_id},
            )
        first = PremarketBriefingViewV1_1.model_validate(payload)

        assert first.schema_version == "1.1"
        assert first.runtime_mode is SourceRuntimeMode.DEMO_REPLAY
        assert len(first.source_coverage) == 5
        assert all(coverage.status is SourceCoverageStatus.OK for coverage in first.source_coverage)
        assert first_result.result.reason_codes == []
        assert first_result.result.event_count > 0
        assert first_result.result.evidence_count > 0
        assert first_result.result.idempotent_replay is False
        assert replayed_result.result.idempotent_replay is True
        assert replayed_result.result.briefing_id == first_result.result.briefing_id
        assert (
            await _scalar(
                engine,
                "SELECT count(*) FROM briefings WHERE id = :briefing_id",
                briefing_id=first_result.result.briefing_id,
            )
            == 1
        )
    finally:
        await engine.dispose()


def _retarget_briefing(
    briefing: PremarketBriefingViewV1_1,
    *,
    runtime_mode: SourceRuntimeMode,
    release_tier: SourceReleaseTier,
) -> PremarketBriefingViewV1_1:
    data_mode = source_data_mode_for_runtime(runtime_mode)

    def provenance(value: SourceProvenance) -> SourceProvenance:
        return value.model_copy(
            update={
                "runtime_mode": runtime_mode,
                "release_tier": release_tier,
                "access_channel": {
                    SourceRuntimeMode.DEMO_REPLAY: SourceAccessChannel.DEMO_REPLAY,
                    SourceRuntimeMode.DEV_LIVE: SourceAccessChannel.AKSHARE,
                    SourceRuntimeMode.AUTHORIZED: SourceAccessChannel.AUTHORIZED_API,
                }[runtime_mode],
                "rights_status": (
                    SourceRightsStatus.APPROVED
                    if runtime_mode is SourceRuntimeMode.AUTHORIZED
                    else SourceRightsStatus.UNVERIFIED
                ),
            }
        )

    progress = briefing.ingestion_progress.model_copy(
        update={
            "data_mode": data_mode,
            "provenance": provenance(briefing.ingestion_progress.provenance),
        }
    )
    coverages = [
        item.model_copy(
            update={
                "progress": item.progress.model_copy(update={"data_mode": data_mode}),
                "provenance": provenance(item.provenance),
            }
        )
        for item in briefing.source_coverage
    ]
    return PremarketBriefingViewV1_1.model_validate(
        briefing.model_copy(
            update={
                "runtime_mode": runtime_mode,
                "release_tier": release_tier,
                "ingestion_progress": progress,
                "source_coverage": coverages,
            }
        )
    )


async def _exercise_briefing_mode_isolation(database_url: str) -> None:
    engine = create_async_engine(database_url)
    repository = PostgresR1PremarketRepository(engine)
    window_start = datetime(2026, 7, 28, 7, tzinfo=UTC)
    window_end = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
    batches = build_demo_replay_batches(
        window_start=window_start,
        window_end=window_end,
        instrument_id="XSHG:603986",
    )
    try:
        for batch in batches:
            lease = await repository.acquire_source_lease(
                batch=batch,
                owner=f"mode-isolation-{batch.capability}",
            )
            await repository.persist_source_batch(
                batch=batch,
                window_start=window_start,
                window_end=window_end,
                lease=lease,
            )
        stored = await repository.generate_and_store_r1_briefing(
            window_start=window_start,
            window_end=window_end,
            generated_at=window_end + timedelta(minutes=10),
            source_ids=tuple(batch.source_id for batch in batches),
            runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        )
        payloads = {
            SourceRuntimeMode.DEMO_REPLAY: stored.briefing,
            SourceRuntimeMode.DEV_LIVE: _retarget_briefing(
                stored.briefing,
                runtime_mode=SourceRuntimeMode.DEV_LIVE,
                release_tier=SourceReleaseTier.DEV_ONLY,
            ),
            SourceRuntimeMode.AUTHORIZED: _retarget_briefing(
                stored.briefing,
                runtime_mode=SourceRuntimeMode.AUTHORIZED,
                release_tier=SourceReleaseTier.AUTHORIZED,
            ),
        }
        async with engine.begin() as connection:
            owner_id = await connection.scalar(
                text("SELECT owner_id FROM briefings WHERE id = :briefing_id"),
                {"briefing_id": stored.briefing.briefing_id},
            )
            assert owner_id is not None
            for offset, mode in enumerate(
                (SourceRuntimeMode.DEV_LIVE, SourceRuntimeMode.AUTHORIZED),
                start=1,
            ):
                await connection.execute(
                    text(
                        """
                        INSERT INTO briefings (
                            id, owner_id, kind, generated_at, as_of, data_delay_seconds,
                            omitted_duplicate_count, source_health, model_version, prompt_hash,
                            rule_version, payload
                        )
                        VALUES (
                            :id, :owner_id, 'premarket', :generated_at, :as_of, 0,
                            0, CAST('[]' AS jsonb), :model_version, :prompt_hash,
                            :rule_version, CAST(:payload AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "owner_id": owner_id,
                        "generated_at": window_end + timedelta(minutes=10 + offset),
                        "as_of": window_end,
                        "model_version": f"mode-isolation-{mode.value}",
                        "prompt_hash": str(offset) * 64,
                        "rule_version": "r1-fixture-v1",
                        "payload": json.dumps(payloads[mode].model_dump(mode="json")),
                    },
                )

        expected = (
            (SourceRuntimeMode.DEMO_REPLAY, SourceReleaseTier.DEV_ONLY),
            (SourceRuntimeMode.DEV_LIVE, SourceReleaseTier.DEV_ONLY),
            (SourceRuntimeMode.AUTHORIZED, SourceReleaseTier.AUTHORIZED),
        )
        for mode, tier in expected:
            latest = await repository.get_latest_briefing(
                as_of=window_end,
                expected_runtime_mode=mode,
                expected_release_tier=tier,
            )
            assert latest is not None
            assert isinstance(latest, PremarketBriefingViewV1_1)
            assert latest.runtime_mode is mode
            assert latest.release_tier is tier

        assert await repository.get_latest_briefing(as_of=window_end) is None
        assert (
            await repository.get_latest_briefing(
                as_of=window_end,
                expected_runtime_mode=SourceRuntimeMode.DEV_LIVE,
                expected_release_tier=SourceReleaseTier.AUTHORIZED,
            )
            is None
        )
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_r1_source_cursor_lease_cas_and_replay_on_real_postgres() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "cursor"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_cursor_protocol(database_url))


@pytest.mark.integration
def test_research_account_quota_is_shared_across_independent_database_sessions() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "research_quota"))
    asyncio.run(_exercise_cross_process_research_quota(database_url))


@pytest.mark.integration
def test_concurrent_briefing_generation_stores_one_effective_briefing() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "briefing_concurrency"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_briefing_concurrency(database_url))


@pytest.mark.integration
def test_provider_neutral_replay_persists_provenance_and_isolates_failure_cursor() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "provider_neutral"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_provider_neutral_replay(database_url))


@pytest.mark.integration
def test_dev_live_knowledge_time_is_not_visible_at_the_data_cutoff() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "dev_live_pit"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_dev_live_knowledge_time(database_url))


@pytest.mark.integration
def test_free_source_v1_2_persists_provenance_and_preserves_pit_and_cursor() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "free_source_v1_2"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_free_source_v1_2_persistence(database_url))


@pytest.mark.integration
def test_official_source_no_change_then_same_window_correction_is_pit_visible() -> None:
    database_url = asyncio.run(
        _create_isolated_database(_database_url(), "official_correction_window")
    )
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_official_same_window_correction(database_url))


@pytest.mark.integration
def test_active_macro_sources_project_explicit_five_capability_gaps() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "active_macro_gaps"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_active_macro_gap_projection(database_url))


@pytest.mark.integration
def test_dual_official_sources_rebase_query_view_without_changing_pit() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "dual_source_as_of"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_dual_official_source_effective_as_of(database_url))


@pytest.mark.integration
def test_v1_1_ingest_results_flow_through_brief_callback_and_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "r1_briefing_v1_1"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_provider_neutral_briefing(database_url, monkeypatch))


@pytest.mark.integration
def test_latest_briefing_is_isolated_by_runtime_mode_and_release_tier() -> None:
    database_url = asyncio.run(_create_isolated_database(_database_url(), "briefing_mode"))
    table_count = asyncio.run(_table_count(database_url))
    assert table_count == 0, "the R1 PostgreSQL database must be disposable and empty"
    _upgrade(database_url)
    asyncio.run(_exercise_briefing_mode_isolation(database_url))
