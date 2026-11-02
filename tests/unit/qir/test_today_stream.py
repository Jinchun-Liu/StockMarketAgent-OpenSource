from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from apps.api.http import application_error_handler
from apps.api.routes.today_stream import (
    BehaviorTransitionEventSource,
    create_today_stream_router,
    stream_behavior_transitions,
)
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import StreamingResponse

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.behavior import (
    BandTransitionDirection,
    BehaviorAssessmentLineageRef,
    BehaviorBandTransitionV1,
    PressureBand,
    PressureMode,
    behavior_transition_idempotency_key,
)
from stock_assistant.contracts.messaging import MessageEnvelope
from stock_assistant.platform.db.behavior_stream import PostgresBehaviorNotificationInbox

AS_OF = datetime(2026, 8, 7, 2, 0, tzinfo=UTC)
HASH = "a" * 64


def _envelope(
    *,
    current_band: PressureBand,
    previous_band: PressureBand,
    event_id: UUID | None = None,
) -> MessageEnvelope[BehaviorBandTransitionV1]:
    message_id = event_id or uuid4()
    previous_assessment_id = uuid4()
    current_assessment_id = uuid4()
    idempotency_key = behavior_transition_idempotency_key(
        instrument_id="XSHG:603986",
        mode=PressureMode.INTRADAY_10M,
        trading_date=AS_OF.date(),
        target_band=current_band,
    )
    payload = BehaviorBandTransitionV1(
        schema_version="1.0",
        event_id=message_id,
        mode=PressureMode.INTRADAY_10M,
        previous_band=previous_band,
        current_band=current_band,
        previous_assessment_id=previous_assessment_id,
        current_assessment_id=current_assessment_id,
        direction=(
            BandTransitionDirection.HIGHER
            if list(PressureBand).index(current_band) > list(PressureBand).index(previous_band)
            else BandTransitionDirection.LOWER
        ),
        comparison_semantics_hash=HASH,
        market_data_lineage_refs=[
            BehaviorAssessmentLineageRef(
                assessment_id=assessment_id,
                data_manifest_hash=HASH,
                rights_snapshot_hash=HASH,
                max_input_revision_known_at=AS_OF,
            )
            for assessment_id in (previous_assessment_id, current_assessment_id)
        ],
        effective_at=AS_OF,
        consecutive_complete_windows=2,
        trading_date=AS_OF.date(),
        idempotency_key=idempotency_key,
    )
    return MessageEnvelope[BehaviorBandTransitionV1](
        schema_version="1.0",
        message_id=message_id,
        event_name=payload.event_name,
        producer="behavior_worker",
        occurred_at=AS_OF,
        produced_at=AS_OF,
        correlation_id=uuid4(),
        partition_key=payload.instrument_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds


class _Source:
    def __init__(
        self,
        batches: list[list[MessageEnvelope[BehaviorBandTransitionV1]]] | None = None,
    ) -> None:
        self.batches = batches or []
        self.cursors: list[UUID | None] = []

    async def read_after(
        self,
        after_message_id: UUID | None,
        *,
        limit: int = 100,
    ) -> list[MessageEnvelope[BehaviorBandTransitionV1]]:
        assert limit == 100
        self.cursors.append(after_message_id)
        return self.batches.pop(0) if self.batches else []


@pytest.mark.asyncio
async def test_sse_is_typed_resumable_bounded_and_idempotent() -> None:
    first = _envelope(
        current_band=PressureBand.ATTENTION,
        previous_band=PressureBand.WARMING,
    )
    duplicate_business_event = _envelope(
        current_band=PressureBand.ATTENTION,
        previous_band=PressureBand.WARMING,
    )
    later = _envelope(
        current_band=PressureBand.CROWDED,
        previous_band=PressureBand.ATTENTION,
    )
    source = _Source([[later]])
    clock = _Clock()

    chunks = [
        chunk
        async for chunk in stream_behavior_transitions(
            source,
            after_message_id=None,
            initial_events=[first, duplicate_business_event],
            clock=clock,
            sleep=clock.sleep,
        )
    ]

    assert clock.value == 55
    assert chunks.count(": heartbeat\n\n") == 3
    data_chunks = [chunk for chunk in chunks if chunk.startswith("id:")]
    assert len(data_chunks) == 2
    assert source.cursors[0] == duplicate_business_event.message_id
    for chunk in data_chunks:
        fields = dict(line.split(": ", 1) for line in chunk.strip().splitlines())
        envelope = MessageEnvelope[BehaviorBandTransitionV1].model_validate(
            json.loads(fields["data"])
        )
        assert fields["id"] == str(envelope.message_id)
        assert fields["event"] == envelope.event_name


@pytest.mark.asyncio
async def test_route_preflights_last_event_id_before_opening_stream() -> None:
    cursor = uuid4()
    source = _Source()
    stream_router = create_today_stream_router(lambda _request: source)
    route = cast(APIRoute, stream_router.routes[0])
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/today/stream",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = await route.endpoint(request=request, last_event_id=str(cursor))

    assert isinstance(response, StreamingResponse)
    assert source.cursors == [cursor]
    assert response.headers["cache-control"] == "no-cache, no-store, max-age=0"
    await response.body_iterator.aclose()


def test_route_returns_503_when_postgres_preflight_is_unavailable() -> None:
    class UnavailableSource:
        async def read_after(
            self,
            after_message_id: UUID | None,
            *,
            limit: int = 100,
        ) -> list[MessageEnvelope[BehaviorBandTransitionV1]]:
            del after_message_id, limit
            raise OperationalError("SELECT", {}, OSError("offline"))

    api = FastAPI()
    api.add_exception_handler(ApplicationError, application_error_handler)
    api.include_router(
        create_today_stream_router(
            lambda _request: cast(BehaviorTransitionEventSource, UnavailableSource())
        ),
        prefix="/api/v1",
    )

    with TestClient(api) as client:
        response = client.get(
            "/api/v1/today/stream",
            headers={"Last-Event-ID": str(uuid4())},
        )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_postgres_adapter_reads_only_the_user_facing_inbox_projection() -> None:
    cursor = uuid4()
    event = _envelope(
        current_band=PressureBand.ATTENTION,
        previous_band=PressureBand.WARMING,
    )

    class Result:
        def __init__(self, value: object) -> None:
            self.value = value

        def mappings(self) -> Result:
            return self

        def one_or_none(self) -> object:
            return self.value

        def all(self) -> object:
            return self.value

    class Connection:
        def __init__(self) -> None:
            self.sql: list[str] = []
            self.results = [
                Result({"projected_at": AS_OF, "message_id": cursor}),
                Result(
                    [
                        {
                            "message_id": event.message_id,
                            "event_name": event.event_name,
                            "idempotency_key": event.idempotency_key,
                            "envelope": event.model_dump(mode="json"),
                        }
                    ]
                ),
            ]

        async def execute(self, statement: object, parameters: object) -> Result:
            del parameters
            self.sql.append(str(statement))
            return self.results.pop(0)

    class ConnectionContext:
        def __init__(self, connection: Connection) -> None:
            self.connection = connection

        async def __aenter__(self) -> Connection:
            return self.connection

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Engine:
        def __init__(self) -> None:
            self.connection = Connection()

        def connect(self) -> ConnectionContext:
            return ConnectionContext(self.connection)

    engine = Engine()
    received = await PostgresBehaviorNotificationInbox(cast(AsyncEngine, engine)).read_after(cursor)

    assert received == [event]
    sql = "\n".join(engine.connection.sql)
    assert "notification_inbox_items" in sql
    assert "recipient_key" in sql
    assert "outbox_events" not in sql
    assert "message_inbox_receipts" not in sql
