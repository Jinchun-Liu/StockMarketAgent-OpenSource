"""Disposable PostgreSQL verification for the R1 behavior/data vertical slice."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from stock_assistant.contracts.behavior import (
    BandTransitionDirection,
    BehaviorAssessmentLineageRef,
    BehaviorBandTransitionV1,
    BehaviorComputeRequest,
    BehaviorPressureAssessmentWriteRequest,
    BehaviorPressureQuery,
    BehaviorTransitionState,
    BehaviorTransitionStateLookup,
    BehaviorTransitionStateWriteRequest,
    HistoricalAnalogCandidateQuery,
    PressureBand,
    PressureClusterName,
    PressureMode,
    behavior_transition_idempotency_key,
)
from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.forecasting import MarketBar
from stock_assistant.contracts.market_data import (
    MarketBarRevision,
    MarketDataRevisionAppendRequest,
    MarketDataRevisionQuery,
)
from stock_assistant.contracts.messaging import (
    DeliveryStatus,
    MessageEnvelope,
    MessagePublishRequest,
)
from stock_assistant.modules.behavior.scoring import compose_behavior_pressure
from stock_assistant.modules.behavior.service import materialize_snapshot
from stock_assistant.platform.db.behavior import (
    PostgresBehaviorOutboxPublisher,
    PostgresBehaviorPressureRepository,
    PostgresMarketDataRepository,
)
from stock_assistant.platform.db.recovery import PostgresUnitOfWork

pytestmark = pytest.mark.integration


def _database_url() -> str:
    value = os.getenv("BEHAVIOR_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("BEHAVIOR_TEST_DATABASE_URL is required for PostgreSQL integration")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("behavior integration requires a postgresql+asyncpg URL")
    if parsed.host not in {"127.0.0.1", "localhost", "postgres"}:
        pytest.fail("behavior integration only accepts an isolated local/CI PostgreSQL host")
    if parsed.database is None or not parsed.database.startswith("stock_assistant_behavior_test"):
        pytest.fail("database name must start with stock_assistant_behavior_test")
    return value


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


def _mutation(at: datetime, key: str) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=at,
        requested_at=at,
        idempotency_key=key,
    )


def _operation(at: datetime) -> OperationContext:
    return OperationContext(schema_version="1.0", as_of=at, requested_at=at)


async def _publish(
    engine: AsyncEngine,
    request: MessagePublishRequest[BehaviorBandTransitionV1],
) -> DeliveryStatus:
    unit_of_work = PostgresUnitOfWork(engine)
    async with unit_of_work:
        receipt = await PostgresBehaviorOutboxPublisher(unit_of_work).publish(request)
        await unit_of_work.commit()
    return receipt.status


async def test_behavior_repositories_pit_analog_state_outbox_and_hundred_replays() -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    market_repository = PostgresMarketDataRepository(engine)
    behavior_repository = PostgresBehaviorPressureRepository(engine)
    run_id = uuid4()
    run_hash = hashlib.sha256(run_id.bytes).hexdigest()
    rights_hash = hashlib.sha256((run_id.hex + "-rights").encode()).hexdigest()
    base = datetime(2026, 8, 7, 2, 0, tzinfo=UTC) + timedelta(days=run_id.int % 5000)
    instrument = InstrumentRef(
        instrument_id="XSHG:603986",
        symbol="603986",
        exchange="XSHG",
        market="CN",
    )
    bar = MarketBar(
        instrument=instrument,
        interval="10min",
        opened_at=base - timedelta(minutes=10),
        closed_at=base,
        received_at=base,
        open_price=Decimal("80"),
        high_price=Decimal("83"),
        low_price=Decimal("79"),
        close_price=Decimal("82"),
        volume=Decimal("100000"),
        adjusted=False,
        source_id="dev_only_fixture",
        data_version="dev-only-v1",
        adjustment_version="none",
        rights_snapshot_hash=rights_hash,
    )
    revision = MarketBarRevision(
        instrument=instrument,
        effective_at=base,
        available_at=base,
        first_seen_at=base + timedelta(minutes=1),
        revision_known_at=base + timedelta(minutes=2),
        source_id="dev_only_fixture",
        rights_snapshot_hash=rights_hash,
        content_hash=run_hash,
        bar=bar,
    )
    append_request = MarketDataRevisionAppendRequest(
        schema_version="1.0",
        context=_mutation(base + timedelta(minutes=3), f"market-{run_id.hex}"),
        revisions=[revision],
    )
    try:
        first_append = await market_repository.append_revisions(append_request)
        append_replays = [
            await market_repository.append_revisions(append_request) for _ in range(99)
        ]
        assert first_append.accepted_count == 1
        assert all(item.idempotent_replay_count == 1 for item in append_replays)

        corrected_hash = hashlib.sha256((run_id.hex + "-correction").encode()).hexdigest()
        corrected = revision.model_copy(
            update={
                "revision_id": uuid4(),
                "first_seen_at": base + timedelta(minutes=4),
                "revision_known_at": base + timedelta(minutes=4),
                "supersedes_revision_id": revision.revision_id,
                "content_hash": corrected_hash,
            }
        )
        await market_repository.append_revisions(
            MarketDataRevisionAppendRequest(
                schema_version="1.0",
                context=_mutation(
                    base + timedelta(minutes=5),
                    f"market-correction-{run_id.hex}",
                ),
                revisions=[corrected],
            )
        )

        hidden = await market_repository.query_revisions(
            MarketDataRevisionQuery(
                schema_version="1.0",
                context=_operation(base + timedelta(minutes=1)),
                instrument_ids=[instrument.instrument_id],
                effective_until=base,
            )
        )
        visible = await market_repository.query_revisions(
            MarketDataRevisionQuery(
                schema_version="1.0",
                context=_operation(base + timedelta(minutes=3)),
                instrument_ids=[instrument.instrument_id],
                effective_until=base,
            )
        )
        assert hidden.items == []
        assert revision.revision_id in {item.revision_id for item in visible.items}
        assert corrected.revision_id not in {item.revision_id for item in visible.items}
        corrected_visible = await market_repository.query_revisions(
            MarketDataRevisionQuery(
                schema_version="1.0",
                context=_operation(base + timedelta(minutes=5)),
                instrument_ids=[instrument.instrument_id],
                effective_until=base,
            )
        )
        assert corrected.revision_id in {item.revision_id for item in corrected_visible.items}
        assert revision.revision_id not in {item.revision_id for item in corrected_visible.items}

        compute_request = BehaviorComputeRequest(
            schema_version="1.0",
            context=_mutation(base + timedelta(minutes=3), f"assessment-{run_id.hex}"),
            mode=PressureMode.POST_CLOSE,
            window_start=base - timedelta(hours=1),
            window_end=base,
            diagnostic_semantics_hash=hashlib.sha256(b"behavior-sem-v1").hexdigest(),
            data_manifest_hash=run_hash,
            rights_snapshot_hash=rights_hash,
        )
        score = compose_behavior_pressure(
            cluster_scores={name: 50 for name in PressureClusterName},
            reference_raw_totals=[50] * 1000,
            reference_population_hash=hashlib.sha256(b"reference-v1").hexdigest(),
        )
        snapshot = materialize_snapshot(
            request=compute_request,
            score_result=score,
            max_input_revision_known_at=revision.revision_known_at,
        )
        write_request = BehaviorPressureAssessmentWriteRequest(
            schema_version="1.0",
            context=compute_request.context,
            snapshot=snapshot,
        )
        first_assessment = await behavior_repository.save_assessment(write_request)
        assessment_replays = [
            await behavior_repository.save_assessment(write_request) for _ in range(99)
        ]
        assert first_assessment.created is True
        assert all(item.idempotent_replay for item in assessment_replays)

        hidden_assessments = await behavior_repository.query_assessments(
            BehaviorPressureQuery(
                schema_version="1.0",
                context=_operation(base + timedelta(minutes=2)),
                mode=PressureMode.POST_CLOSE,
            )
        )
        visible_assessments = await behavior_repository.query_assessments(
            BehaviorPressureQuery(
                schema_version="1.0",
                context=_operation(base + timedelta(minutes=3)),
                mode=PressureMode.POST_CLOSE,
            )
        )
        assert hidden_assessments.items == []
        assert any(
            item.assessment_id == snapshot.assessment_id for item in visible_assessments.items
        )

        selection_as_of = base + timedelta(days=5)
        vector_text = "[" + ",".join("0" for _ in range(14)) + "]"
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO behavior_analog_observations (
                        assessment_id, instrument_id, trading_day_index, as_of,
                        assessment_revision_known_at, label_available_at,
                        feature_set_version, feature_vector, outcome_payload, created_at
                    ) VALUES (
                        :assessment_id, :instrument_id, 1, :as_of,
                        :revision_known_at, :label_available_at,
                        'behavior-analog-14d.v1', CAST(:vector AS vector),
                        CAST(:outcomes AS jsonb), :created_at
                    )
                    """
                ),
                {
                    "assessment_id": snapshot.assessment_id,
                    "instrument_id": instrument.instrument_id,
                    "as_of": snapshot.as_of,
                    "revision_known_at": snapshot.pit_lineage.assessment_revision_known_at,
                    "label_available_at": selection_as_of - timedelta(days=1),
                    "vector": vector_text,
                    "outcomes": (
                        '{"return_1d":"0.01","return_3d":"0.02",'
                        '"industry_excess":"0.005","mae":"-0.03","mfe":"0.04"}'
                    ),
                    "created_at": selection_as_of,
                },
            )
        candidates = await behavior_repository.query_analog_candidates(
            HistoricalAnalogCandidateQuery(
                schema_version="1.0",
                context=_operation(selection_as_of),
                selection_as_of=selection_as_of,
                vector=[0.0] * 14,
                limit=50,
            )
        )
        assert snapshot.assessment_id in {item.assessment_id for item in candidates.items}
        loaded = await behavior_repository.load_analog_observations(
            assessment_ids=[snapshot.assessment_id],
            selection_as_of=selection_as_of,
        )
        assert len(loaded) == 1
        assert set(loaded[0].outcomes) == {
            "return_1d",
            "return_3d",
            "industry_excess",
            "mae",
            "mfe",
        }

        state_date = date(2040, 1, 1) + timedelta(days=run_id.int % 10_000)
        assert snapshot.band is not None
        state = BehaviorTransitionState(
            mode=PressureMode.POST_CLOSE,
            trading_date=state_date,
            current_band=snapshot.band,
            last_assessment_id=snapshot.assessment_id,
            revision=1,
            updated_at=base + timedelta(minutes=3),
        )
        state_request = BehaviorTransitionStateWriteRequest(
            schema_version="1.0",
            context=_mutation(base + timedelta(minutes=3), f"transition-{run_id.hex}"),
            state=state,
        )
        first_state = await behavior_repository.save_transition_state(state_request)
        state_replays = [
            await behavior_repository.save_transition_state(state_request) for _ in range(99)
        ]
        restored = await behavior_repository.get_transition_state(
            BehaviorTransitionStateLookup(
                schema_version="1.0",
                context=_operation(base + timedelta(minutes=3)),
                mode=PressureMode.POST_CLOSE,
                trading_date=state_date,
            )
        )
        assert first_state.created is True
        assert all(item.idempotent_replay for item in state_replays)
        assert restored.state == state

        target_band = PressureBand.CROWDED
        business_key = behavior_transition_idempotency_key(
            instrument_id=instrument.instrument_id,
            mode=PressureMode.POST_CLOSE,
            trading_date=state_date,
            target_band=target_band,
        )
        previous_assessment_id = uuid4()
        current_assessment_id = uuid4()
        event = BehaviorBandTransitionV1(
            schema_version="1.0",
            event_id=uuid4(),
            mode=PressureMode.POST_CLOSE,
            previous_band=PressureBand.STABLE,
            current_band=target_band,
            previous_assessment_id=previous_assessment_id,
            current_assessment_id=current_assessment_id,
            direction=BandTransitionDirection.HIGHER,
            comparison_semantics_hash=compute_request.diagnostic_semantics_hash,
            market_data_lineage_refs=[
                BehaviorAssessmentLineageRef(
                    assessment_id=previous_assessment_id,
                    data_manifest_hash=run_hash,
                    rights_snapshot_hash=rights_hash,
                    max_input_revision_known_at=base,
                ),
                BehaviorAssessmentLineageRef(
                    assessment_id=current_assessment_id,
                    data_manifest_hash=run_hash,
                    rights_snapshot_hash=rights_hash,
                    max_input_revision_known_at=base + timedelta(minutes=1),
                ),
            ],
            effective_at=base + timedelta(minutes=2),
            consecutive_complete_windows=1,
            trading_date=state_date,
            idempotency_key=business_key,
        )
        publish_context = _mutation(base + timedelta(minutes=3), business_key)
        publish_request = MessagePublishRequest(
            schema_version="1.0",
            context=publish_context,
            envelope=MessageEnvelope(
                schema_version="1.0",
                message_id=event.event_id,
                event_name=event.event_name,
                producer="behavior_worker",
                occurred_at=event.effective_at,
                produced_at=publish_context.as_of,
                correlation_id=publish_context.correlation_id,
                causation_id=publish_context.request_id,
                partition_key=instrument.instrument_id,
                idempotency_key=business_key,
                payload=event,
            ),
        )
        statuses = [await _publish(engine, publish_request) for _ in range(100)]
        assert statuses[0] is DeliveryStatus.ACCEPTED
        assert statuses[1:] == [DeliveryStatus.SUPPRESSED] * 99

        async with engine.connect() as connection:
            counts = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                (SELECT count(*) FROM behavior_pressure_assessments
                                  WHERE assessment_id = :assessment_id) AS assessments,
                                (SELECT count(*) FROM behavior_transition_states
                                  WHERE instrument_id = :instrument_id
                                    AND mode = 'post_close'
                                    AND trading_date = :trading_date) AS states,
                                (SELECT count(*) FROM outbox_events
                                  WHERE producer = 'behavior_worker'
                                    AND event_name = :event_name
                                    AND idempotency_key = :idempotency_key) AS events,
                                (SELECT count(*) FROM notification_inbox_items
                                  WHERE recipient_key = 'public:r1'
                                    AND event_name = :event_name
                                    AND idempotency_key = :idempotency_key) AS inbox_items
                            """
                        ),
                        {
                            "assessment_id": snapshot.assessment_id,
                            "instrument_id": instrument.instrument_id,
                            "trading_date": state_date,
                            "event_name": event.event_name,
                            "idempotency_key": business_key,
                        },
                    )
                )
                .mappings()
                .one()
            )
        assert (
            counts["assessments"],
            counts["states"],
            counts["events"],
            counts["inbox_items"],
        ) == (1, 1, 1, 1)
    finally:
        await engine.dispose()
