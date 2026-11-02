"""PostgreSQL integration for the unified R2 global event catalog."""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.integration.test_active_target_repository import _database_url, _upgrade

from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.global_events import (
    GlobalEventCatalogStatus,
    PolicyRefreshRequest,
)
from stock_assistant.contracts.persistence import (
    MarketEventLookupRequest,
    MarketEventQueryV1_1,
    MarketEventUpsertRequest,
)
from stock_assistant.platform.db import market_events as market_event_module
from stock_assistant.platform.db.market_events import (
    MarketEventRepositoryError,
    PostgresMarketEventRepository,
)
from stock_assistant.platform.research.synthetic_global_events import (
    GLOBAL_EVENT_CATALOG_SOURCE_ID,
    synthetic_global_events,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
STREAM = "global_official_events"
DIRECT_EVENT_ID = UUID("00000000-0000-0000-0000-000000009901")
DIRECT_CLUSTER_ID = UUID("00000000-0000-0000-0000-000000009902")


def _refresh_request(run_at: datetime, suffix: str) -> PolicyRefreshRequest:
    events = market_event_module.synthetic_global_events()
    manifest_sha256 = market_event_module.synthetic_global_event_manifest_sha256(events)
    return PolicyRefreshRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=run_at,
            requested_at=run_at,
            idempotency_key=f"r2-global-refresh-{suffix}-{manifest_sha256}",
        ),
        scheduled_for=run_at,
        fixture_manifest_sha256=manifest_sha256,
    )


def _operation(as_of: datetime) -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        as_of=as_of,
        requested_at=NOW,
    )


async def _cleanup(engine: AsyncEngine) -> None:
    events = synthetic_global_events()
    logical_ids = [str(item.event_id) for item in events] + [str(DIRECT_EVENT_ID)]
    cluster_ids = [str(item.cluster_id) for item in events] + [str(DIRECT_CLUSTER_ID)]
    evidence_ids = [str(evidence.evidence_id) for item in events for evidence in item.evidence]
    source_ids = {
        GLOBAL_EVENT_CATALOG_SOURCE_ID,
        *(evidence.source_id for item in events for evidence in item.evidence),
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                DELETE FROM event_claims
                 WHERE event_id IN (
                    SELECT id FROM market_events
                     WHERE logical_event_id = ANY(CAST(:event_ids AS uuid[]))
                 )
                """
            ),
            {"event_ids": logical_ids},
        )
        await connection.execute(
            text(
                """
                DELETE FROM event_entities
                 WHERE event_id IN (
                    SELECT id FROM market_events
                     WHERE logical_event_id = ANY(CAST(:event_ids AS uuid[]))
                 )
                """
            ),
            {"event_ids": logical_ids},
        )
        await connection.execute(
            text(
                """
                DELETE FROM event_documents
                 WHERE event_id IN (
                    SELECT id FROM market_events
                     WHERE logical_event_id = ANY(CAST(:event_ids AS uuid[]))
                 )
                """
            ),
            {"event_ids": logical_ids},
        )
        await connection.execute(
            text(
                "DELETE FROM market_events WHERE logical_event_id = ANY(CAST(:event_ids AS uuid[]))"
            ),
            {"event_ids": logical_ids},
        )
        await connection.execute(
            text("DELETE FROM event_clusters WHERE id = ANY(CAST(:cluster_ids AS uuid[]))"),
            {"cluster_ids": cluster_ids},
        )
        await connection.execute(
            text("DELETE FROM raw_documents WHERE id = ANY(CAST(:evidence_ids AS uuid[]))"),
            {"evidence_ids": evidence_ids},
        )
        await connection.execute(
            text("DELETE FROM source_runs WHERE source_id = :source_id AND stream = :stream"),
            {"source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID, "stream": STREAM},
        )
        await connection.execute(
            text("DELETE FROM source_cursors WHERE source_id = :source_id AND stream = :stream"),
            {"source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID, "stream": STREAM},
        )
        await connection.execute(
            text("DELETE FROM data_sources WHERE id = ANY(CAST(:source_ids AS text[]))"),
            {"source_ids": sorted(source_ids)},
        )


@pytest.mark.asyncio
async def test_global_catalog_refresh_replay_pit_matching_and_failure_atomicity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresMarketEventRepository(engine)
    events = synthetic_global_events()
    original, correction = events[:2]
    request = _refresh_request(NOW, "initial")
    try:
        await _cleanup(engine)
        mismatched = request.model_copy(update={"fixture_manifest_sha256": "0" * 64})
        with pytest.raises(
            MarketEventRepositoryError,
            match="GLOBAL_EVENT_MANIFEST_MISMATCH",
        ):
            await repository.refresh_synthetic_catalog(mismatched)
        first = await repository.refresh_synthetic_catalog(request)
        assert first.events_seen == len(events)
        assert first.event_revisions_created == len(events)
        assert first.cursor_advanced is True
        assert first.idempotent_replay is False

        equivalent_timezone_replay = await repository.refresh_synthetic_catalog(
            _refresh_request(NOW.astimezone(ZoneInfo("Asia/Shanghai")), "timezone-replay")
        )
        assert equivalent_timezone_replay.idempotent_replay is True
        assert equivalent_timezone_replay.cursor_advanced is False

        for _ in range(100):
            replay = await repository.refresh_synthetic_catalog(request)
            assert replay.idempotent_replay is True
            assert replay.cursor_advanced is False
            assert replay.event_revisions_created == len(events)

        async with engine.connect() as connection:
            revision_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM market_events "
                    "WHERE logical_event_id = ANY(CAST(:event_ids AS uuid[]))"
                ),
                {"event_ids": [str(item.event_id) for item in events]},
            )
            cursor_before_failure = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT cursor_hash, cursor_version, business_watermark,
                                   last_success_at
                              FROM source_cursors
                             WHERE source_id = :source_id AND stream = :stream
                            """
                        ),
                        {"source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID, "stream": STREAM},
                    )
                )
                .mappings()
                .one()
            )
        assert revision_count == len(events)

        before_correction = await repository.get_event(
            MarketEventLookupRequest(
                schema_version="1.0",
                context=_operation(correction.revision_known_at - timedelta(seconds=1)),
                event_id=original.event_id,
            )
        )
        after_correction = await repository.get_event(
            MarketEventLookupRequest(
                schema_version="1.0",
                context=_operation(NOW),
                event_id=original.event_id,
            )
        )
        assert before_correction.event is not None
        assert before_correction.event.revision == 1
        assert after_correction.event is not None
        assert after_correction.event.revision == 2

        query = MarketEventQueryV1_1(
            schema_version="1.1",
            context=_operation(NOW),
            industry_ids=["industry:semiconductor"],
            limit=100,
        )
        page = await repository.list_events_by_industry(query)
        assert page.items
        assert all(
            any(
                fact.name == "industry_id" and fact.value == "industry:semiconductor"
                for fact in item.facts
            )
            for item in page.items
        )
        assert all(item.revision_known_at <= NOW for item in page.items)
        catalog = await repository.get_catalog_slice(query)
        assert catalog.status is GlobalEventCatalogStatus.READY_WITH_MATCHES

        direct_event = events[2].model_copy(
            update={
                "event_id": DIRECT_EVENT_ID,
                "cluster_id": DIRECT_CLUSTER_ID,
                "security_ids": ["XSHG:603986"],
                "company_ids": [],
                "region_ids": [],
                "facts": [fact for fact in events[2].facts if fact.name != "industry_id"],
            }
        )
        await repository.upsert_event(
            MarketEventUpsertRequest(
                schema_version="1.0",
                context=MutationContext(
                    schema_version="1.0",
                    as_of=NOW,
                    requested_at=NOW,
                    idempotency_key="r2-direct-event-without-industry",
                ),
                event=direct_event,
            )
        )
        exposure_catalog = await repository.get_catalog_slice(
            MarketEventQueryV1_1(
                schema_version="1.1",
                context=_operation(NOW),
                security_ids=["XSHG:603986"],
                company_ids=["company:XSHG:603986"],
                region_ids=["region:china"],
                industry_ids=["industry:semiconductor"],
                limit=100,
            )
        )
        assert exposure_catalog.status is GlobalEventCatalogStatus.READY_WITH_MATCHES
        assert direct_event.event_id in {item.event_id for item in exposure_catalog.events}

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE data_sources "
                    "SET rights_record = CAST(:rights_record AS jsonb) "
                    "WHERE id = :source_id"
                ),
                {
                    "source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID,
                    "rights_record": '{"catalog_read_allowed":false}',
                },
            )
        rights_blocked = await repository.get_catalog_slice(query)
        assert rights_blocked.status is GlobalEventCatalogStatus.RIGHTS_BLOCKED
        assert rights_blocked.reason_code == "EVENT_CATALOG_RIGHTS_BLOCKED"

        conflicting = original.model_copy(update={"materiality": 0.11})
        monkeypatch.setattr(
            market_event_module,
            "synthetic_global_events",
            lambda: (conflicting,),
        )
        failed_request = _refresh_request(NOW + timedelta(hours=1), "conflict")
        with pytest.raises(
            MarketEventRepositoryError,
            match="MARKET_EVENT_REVISION_CONFLICT",
        ):
            await repository.refresh_synthetic_catalog(failed_request)

        async with engine.connect() as connection:
            cursor_after_failure = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT cursor_hash, cursor_version, business_watermark,
                                   last_success_at
                              FROM source_cursors
                             WHERE source_id = :source_id AND stream = :stream
                            """
                        ),
                        {"source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID, "stream": STREAM},
                    )
                )
                .mappings()
                .one()
            )
            failed_run_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM source_runs "
                    "WHERE source_id = :source_id AND scheduled_for = :scheduled_for"
                ),
                {
                    "source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID,
                    "scheduled_for": failed_request.scheduled_for,
                },
            )
        assert dict(cursor_after_failure) == dict(cursor_before_failure)
        assert failed_run_count == 0
    finally:
        monkeypatch.setattr(
            market_event_module,
            "synthetic_global_events",
            synthetic_global_events,
        )
        await _cleanup(engine)
        await engine.dispose()
