"""Database-free branch coverage for the MarketEventRepository adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.global_events import GlobalEventCatalogStatus
from stock_assistant.contracts.persistence import (
    MarketEventLookupRequest,
    MarketEventQuery,
    MarketEventQueryV1_1,
    MarketEventUpsertRequest,
)
from stock_assistant.platform.db import market_events as adapter
from stock_assistant.platform.db.market_events import PostgresMarketEventRepository
from stock_assistant.platform.research.synthetic_global_events import (
    GLOBAL_EVENT_RIGHTS_HASH,
    synthetic_global_events,
)

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return None if not self._rows else self._rows[0]

    def one(self) -> dict[str, object]:
        return self._rows[0]

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self._results = results

    async def execute(self, _statement: object, _parameters: object = None) -> _Result:
        return self._results.pop(0)


class _ConnectionContext:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self, *result_sets: list[dict[str, object]]) -> None:
        self._connection = _Connection([_Result(rows) for rows in result_sets])

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self._connection)

    def begin(self) -> _ConnectionContext:
        return _ConnectionContext(self._connection)


def _operation(*, as_of: datetime = NOW, requested_at: datetime = NOW) -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        as_of=as_of,
        requested_at=requested_at,
    )


def _repository(*result_sets: list[dict[str, object]]) -> PostgresMarketEventRepository:
    return PostgresMarketEventRepository(cast(AsyncEngine, cast(object, _Engine(*result_sets))))


def _catalog_state(
    *,
    last_success_at: datetime | None,
    read_allowed: bool = True,
) -> dict[str, object]:
    return {
        "business_watermark": NOW - timedelta(hours=1),
        "last_success_at": last_success_at,
        "rights_record": {
            "catalog_read_allowed": read_allowed,
            "license_id": "sma-original-synthetic-v1",
        },
        "rights_hash": GLOBAL_EVENT_RIGHTS_HASH,
        "rights_expiry": None,
    }


def test_cursor_helpers_are_stable_and_reject_invalid_values() -> None:
    row_id = UUID("00000000-0000-0000-0000-000000000901")
    encoded = adapter._cursor(NOW, row_id)
    cursor_time, decoded_id = adapter._decode_cursor(encoded)

    assert cursor_time == str(NOW)
    assert decoded_id == row_id
    assert adapter._row_id(row_id, 1) == adapter._row_id(row_id, 1)
    assert adapter._json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    with pytest.raises(ValueError, match="invalid market-event cursor"):
        adapter._decode_cursor("not-json")


@pytest.mark.asyncio
async def test_get_event_preserves_pit_and_explicit_revision_paths() -> None:
    event = synthetic_global_events()[0]
    repository = _repository(
        [{"payload": event.model_dump(mode="json")}],
        [],
    )
    latest = await repository.get_event(
        MarketEventLookupRequest(
            schema_version="1.0",
            context=_operation(),
            event_id=event.event_id,
        )
    )
    missing_revision = await repository.get_event(
        MarketEventLookupRequest(
            schema_version="1.0",
            context=_operation(),
            event_id=event.event_id,
            revision=99,
        )
    )

    assert latest.found is True
    assert latest.event == event
    assert missing_revision.found is False
    assert missing_revision.event is None


@pytest.mark.asyncio
async def test_list_events_supports_v1_and_v1_1_pagination() -> None:
    first, second = synthetic_global_events()[2:4]
    rows = [
        {"id": first.event_id, "payload": first.model_dump(mode="json"), "sort_time": NOW},
        {
            "id": second.event_id,
            "payload": second.model_dump(mode="json"),
            "sort_time": NOW - timedelta(minutes=1),
        },
    ]
    repository = _repository(rows, [])
    first_page = await repository.list_events(
        MarketEventQuery(schema_version="1.0", context=_operation(), limit=1)
    )
    empty_industry_page = await repository.list_events_by_industry(
        MarketEventQueryV1_1(
            schema_version="1.1",
            context=_operation(),
            industry_ids=["industry:unmatched"],
            cursor=first_page.next_cursor,
            limit=1,
        )
    )

    assert first_page.items == [first]
    assert first_page.has_more is True
    assert first_page.next_cursor is not None
    assert empty_industry_page.items == []
    assert empty_industry_page.has_more is False


@pytest.mark.asyncio
async def test_catalog_states_distinguish_unavailable_stale_and_no_matches() -> None:
    unavailable = await _repository([]).get_catalog_slice(
        MarketEventQueryV1_1(schema_version="1.1", context=_operation())
    )
    rights_blocked = await _repository(
        [_catalog_state(last_success_at=NOW - timedelta(minutes=10), read_allowed=False)]
    ).get_catalog_slice(MarketEventQueryV1_1(schema_version="1.1", context=_operation()))
    stale = await _repository(
        [_catalog_state(last_success_at=NOW - timedelta(hours=3))]
    ).get_catalog_slice(MarketEventQueryV1_1(schema_version="1.1", context=_operation()))
    ready_empty = await _repository(
        [_catalog_state(last_success_at=NOW - timedelta(minutes=10))],
        [],
    ).get_catalog_slice(MarketEventQueryV1_1(schema_version="1.1", context=_operation()))

    assert unavailable.status is GlobalEventCatalogStatus.UNAVAILABLE
    assert unavailable.reason_code == "EVENT_CATALOG_NOT_REFRESHED"
    assert rights_blocked.status is GlobalEventCatalogStatus.RIGHTS_BLOCKED
    assert rights_blocked.reason_code == "EVENT_CATALOG_RIGHTS_BLOCKED"
    assert stale.status is GlobalEventCatalogStatus.STALE
    assert stale.reason_code == "EVENT_CATALOG_STALE"
    assert ready_empty.status is GlobalEventCatalogStatus.READY_NO_MATCHES
    assert ready_empty.reason_code is None


@pytest.mark.asyncio
async def test_upsert_delegates_to_append_only_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = synthetic_global_events()[0]
    calls: list[tuple[object, object]] = []

    async def _persist(
        connection: object,
        received: object,
        *,
        expected_revision: object,
    ) -> tuple[bool, int]:
        calls.append((connection, received))
        assert expected_revision is None
        return True, 1

    monkeypatch.setattr(adapter, "_persist_event", _persist)
    result = await _repository().upsert_event(
        MarketEventUpsertRequest(
            schema_version="1.0",
            context=MutationContext(
                schema_version="1.0",
                as_of=NOW,
                requested_at=NOW,
                idempotency_key="unit-market-event-upsert",
            ),
            event=event,
        )
    )

    assert result.created is True
    assert result.idempotent_replay is False
    assert calls and calls[0][1] == event


@pytest.mark.asyncio
async def test_upsert_rejects_future_revision_before_database_access() -> None:
    event = synthetic_global_events()[0].model_copy(
        update={"revision_known_at": NOW + timedelta(seconds=1)}
    )
    with pytest.raises(adapter.MarketEventRepositoryError, match="MARKET_EVENT_FUTURE_REVISION"):
        await _repository().upsert_event(
            MarketEventUpsertRequest(
                schema_version="1.0",
                context=MutationContext(
                    schema_version="1.0",
                    as_of=NOW,
                    requested_at=NOW,
                    idempotency_key="future-market-event-upsert",
                ),
                event=event,
            )
        )
