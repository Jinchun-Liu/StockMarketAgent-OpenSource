"""PostgreSQL notification-inbox reads for resumable behavior SSE."""

from __future__ import annotations

from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.contracts.behavior import (
    BEHAVIOR_BAND_TRANSITION_EVENT,
    BehaviorBandTransitionV1,
)
from stock_assistant.contracts.messaging import MessageEnvelope

_STREAM_SELECT: Final = """
    SELECT message_id, event_name, idempotency_key, envelope
      FROM notification_inbox_items
     WHERE recipient_key = :recipient_key
       AND event_name = :event_name
       AND occurred_at >= (
            date_trunc('day', timezone('Asia/Shanghai', CURRENT_TIMESTAMP))
            AT TIME ZONE 'Asia/Shanghai'
       )
       AND occurred_at < (
            date_trunc('day', timezone('Asia/Shanghai', CURRENT_TIMESTAMP))
            + INTERVAL '1 day'
       ) AT TIME ZONE 'Asia/Shanghai'
"""
_STREAM_FROM_START_SQL: Final = _STREAM_SELECT + " ORDER BY projected_at, message_id LIMIT :limit"
_STREAM_AFTER_SQL: Final = (
    _STREAM_SELECT
    + """
       AND (projected_at, message_id) > (
            :cursor_projected_at,
            CAST(:cursor_message_id AS uuid)
       )
     ORDER BY projected_at, message_id
     LIMIT :limit
    """
)


class BehaviorStreamCursorNotFoundError(ValueError):
    """The requested persistent message cursor does not exist in this stream."""


class BehaviorStreamContractError(ValueError):
    """A persisted transition row does not satisfy the published event contract."""


def _envelope(row: RowMapping) -> MessageEnvelope[BehaviorBandTransitionV1]:
    try:
        envelope = MessageEnvelope[BehaviorBandTransitionV1].model_validate(row["envelope"])
    except ValidationError as error:
        raise BehaviorStreamContractError(
            "persisted inbox envelope does not satisfy its contract"
        ) from error
    if envelope.message_id != row["message_id"]:
        raise BehaviorStreamContractError("inbox and envelope message IDs do not match")
    if envelope.event_name != row["event_name"]:
        raise BehaviorStreamContractError("inbox and envelope event names do not match")
    if envelope.idempotency_key != row["idempotency_key"]:
        raise BehaviorStreamContractError("inbox and envelope idempotency keys do not match")
    if envelope.message_id != envelope.payload.event_id:
        raise BehaviorStreamContractError("message and behavior event IDs do not match")
    if envelope.idempotency_key != envelope.payload.idempotency_key:
        raise BehaviorStreamContractError("message and behavior idempotency keys do not match")
    return envelope


class PostgresBehaviorNotificationInbox:
    """Read already-projected public behavior events from the user-facing inbox."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read_after(
        self,
        after_message_id: UUID | None,
        *,
        limit: int = 100,
    ) -> list[MessageEnvelope[BehaviorBandTransitionV1]]:
        if not 1 <= limit <= 100:
            raise ValueError("behavior stream batch limit must be between 1 and 100")

        parameters: dict[str, object] = {
            "recipient_key": "public:r1",
            "event_name": BEHAVIOR_BAND_TRANSITION_EVENT,
            "limit": limit,
        }
        query = _STREAM_FROM_START_SQL
        async with self._engine.connect() as connection:
            if after_message_id is not None:
                cursor = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT projected_at, message_id
                                  FROM notification_inbox_items
                                 WHERE recipient_key = :recipient_key
                                   AND event_name = :event_name
                                   AND message_id = CAST(:after_message_id AS uuid)
                                 LIMIT 1
                                """
                            ),
                            {
                                "event_name": BEHAVIOR_BAND_TRANSITION_EVENT,
                                "recipient_key": "public:r1",
                                "after_message_id": str(after_message_id),
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if cursor is None:
                    raise BehaviorStreamCursorNotFoundError(
                        "behavior transition cursor does not exist"
                    )
                parameters.update(
                    {
                        "cursor_projected_at": cursor["projected_at"],
                        "cursor_message_id": str(cursor["message_id"]),
                    }
                )
                query = _STREAM_AFTER_SQL

            rows = (
                (
                    await connection.execute(
                        text(query),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        return [_envelope(row) for row in rows]


__all__ = [
    "BehaviorStreamContractError",
    "BehaviorStreamCursorNotFoundError",
    "PostgresBehaviorNotificationInbox",
]
