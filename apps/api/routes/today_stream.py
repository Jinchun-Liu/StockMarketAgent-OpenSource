"""Resumable SSE delivery of persisted, typed behavior transition events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Final, Protocol
from uuid import UUID

from fastapi import APIRouter, Header, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import StreamingResponse

from apps.api.routes.active_target import PROBLEM_DETAILS_SCHEMA, _database_engine
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.behavior import BehaviorBandTransitionV1
from stock_assistant.contracts.messaging import MessageEnvelope
from stock_assistant.platform.db.behavior_stream import (
    BehaviorStreamContractError,
    BehaviorStreamCursorNotFoundError,
    PostgresBehaviorNotificationInbox,
)

HEARTBEAT_SECONDS: Final = 15.0
STREAM_LIFETIME_SECONDS: Final = 55.0
POLL_SECONDS: Final = 1.0


class BehaviorTransitionEventSource(Protocol):
    """Narrow read surface used by the HTTP delivery adapter."""

    async def read_after(
        self,
        after_message_id: UUID | None,
        *,
        limit: int = 100,
    ) -> list[MessageEnvelope[BehaviorBandTransitionV1]]: ...


type SourceFactory = Callable[[Request], BehaviorTransitionEventSource]
type MonotonicClock = Callable[[], float]
type AsyncSleep = Callable[[float], Awaitable[None]]
type DisconnectCheck = Callable[[], Awaitable[bool]]


def _format_event(envelope: MessageEnvelope[BehaviorBandTransitionV1]) -> str:
    return (
        f"id: {envelope.message_id}\n"
        f"event: {envelope.event_name}\n"
        f"data: {envelope.model_dump_json()}\n\n"
    )


async def stream_behavior_transitions(
    source: BehaviorTransitionEventSource,
    *,
    after_message_id: UUID | None,
    initial_events: list[MessageEnvelope[BehaviorBandTransitionV1]],
    clock: MonotonicClock = time.monotonic,
    sleep: AsyncSleep = asyncio.sleep,
    disconnected: DisconnectCheck | None = None,
) -> AsyncIterator[str]:
    """Yield a bounded SSE session with stable resume and duplicate suppression."""

    started_at = clock()
    next_heartbeat_at = started_at + HEARTBEAT_SECONDS
    cursor = after_message_id
    pending = initial_events
    seen_message_ids: set[UUID] = set()
    seen_idempotency_keys: set[str] = set()

    while clock() - started_at < STREAM_LIFETIME_SECONDS:
        for envelope in pending:
            duplicate = (
                envelope.message_id in seen_message_ids
                or envelope.idempotency_key in seen_idempotency_keys
            )
            cursor = envelope.message_id
            if duplicate:
                continue
            seen_message_ids.add(envelope.message_id)
            seen_idempotency_keys.add(envelope.idempotency_key)
            yield _format_event(envelope)

        if disconnected is not None and await disconnected():
            return

        now = clock()
        if now - started_at >= STREAM_LIFETIME_SECONDS:
            return
        if now >= next_heartbeat_at:
            yield ": heartbeat\n\n"
            while next_heartbeat_at <= now:
                next_heartbeat_at += HEARTBEAT_SECONDS

        wait_for = min(
            POLL_SECONDS,
            started_at + STREAM_LIFETIME_SECONDS - now,
            next_heartbeat_at - now,
        )
        if wait_for > 0:
            await sleep(wait_for)
        try:
            pending = await source.read_after(cursor, limit=100)
        except (SQLAlchemyError, OSError, BehaviorStreamContractError):
            return


def _postgres_source(request: Request) -> BehaviorTransitionEventSource:
    return PostgresBehaviorNotificationInbox(_database_engine(request))


def _parse_last_event_id(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as error:
        raise ApplicationError(
            ErrorCode.VALIDATION_ERROR,
            "The stream resume cursor is invalid.",
        ) from error


async def _serve_today_stream(
    request: Request,
    last_event_id: str | None,
    source_factory: SourceFactory,
) -> StreamingResponse:
    cursor = _parse_last_event_id(last_event_id)
    source = source_factory(request)
    try:
        initial_events = await source.read_after(cursor, limit=100)
    except BehaviorStreamCursorNotFoundError as error:
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "The stream resume cursor is no longer available.",
        ) from error
    except (SQLAlchemyError, OSError, BehaviorStreamContractError) as error:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Live updates are temporarily unavailable.",
            retryable=True,
        ) from error

    return StreamingResponse(
        stream_behavior_transitions(
            source,
            after_message_id=cursor,
            initial_events=initial_events,
            disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def create_today_stream_router(source_factory: SourceFactory) -> APIRouter:
    stream_router = APIRouter(tags=["today"])

    @stream_router.get(
        "/today/stream",
        response_model=MessageEnvelope[BehaviorBandTransitionV1],
        response_class=StreamingResponse,
        responses={
            200: {"content": {"text/event-stream": {}}},
            503: {
                "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
            },
        },
    )
    async def get_today_stream(
        request: Request,
        last_event_id: Annotated[
            str | None,
            Header(alias="Last-Event-ID"),
        ] = None,
    ) -> StreamingResponse:
        return await _serve_today_stream(request, last_event_id, source_factory)

    return stream_router


router = APIRouter(tags=["today"])


@router.get(
    "/today/stream",
    response_model=MessageEnvelope[BehaviorBandTransitionV1],
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        503: {
            "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
        },
    },
)
async def get_today_stream(
    request: Request,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID"),
    ] = None,
) -> StreamingResponse:
    return await _serve_today_stream(request, last_event_id, _postgres_source)


__all__ = [
    "BehaviorTransitionEventSource",
    "create_today_stream_router",
    "router",
    "stream_behavior_transitions",
]
