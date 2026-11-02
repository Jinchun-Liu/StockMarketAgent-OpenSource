"""PostgreSQL recovery primitives for jobs, transactional Outbox and technical Inbox.

These adapters intentionally stop below product-event composition. They persist the
already-frozen broker-neutral contracts, coordinate database transactions, and expose
lease/deduplication mechanics for later composition roots.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from types import TracebackType
from typing import Final, Self, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.messaging import (
    DeliveryReceipt,
    DeliveryStatus,
    MessageEnvelope,
    MessagePublishRequest,
)

_SCHEMA_VERSION_PATTERN: Final = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
_SCHEMA_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_.-]{1,199}$")
_CONSUMER_ID_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{1,99}$")
_LEASE_TOKEN_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]{7,199}$")
_REASON_CODE_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class RecoveryFoundationError(RuntimeError):
    """Base class for stable, non-secret recovery failures."""


class OutboxIdentityConflictError(RecoveryFoundationError):
    """An idempotency identity was reused for a different logical message."""


class InboxStateConflictError(RecoveryFoundationError):
    """A technical Inbox row cannot be safely resumed automatically."""


class InboxProcessingOutcome(StrEnum):
    """Whether this delivery ran its side effect or was already committed."""

    PROCESSED = "processed"
    IDEMPOTENT_REPLAY = "idempotent_replay"


class OutboxReleaseOutcome(StrEnum):
    """Result of releasing one owned delivery attempt."""

    NOT_OWNED = "not_owned"
    RETRY_SCHEDULED = "retry_scheduled"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class _UnitOfWorkState(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SchemaIdentity:
    """Runtime identity of a validated private or published payload model."""

    name: str
    version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ClaimedOutboxMessage:
    """One unpublished logical message held by a unique relay lease token."""

    outbox_id: UUID
    message_id: UUID
    event_name: str
    envelope_schema_version: str
    payload_schema_name: str
    payload_schema_version: str
    payload_schema_sha256: str
    producer: str
    correlation_id: UUID
    causation_id: UUID | None
    partition_key: str
    idempotency_key: str
    occurred_at: datetime
    produced_at: datetime
    payload: dict[str, JsonValue]
    headers: list[dict[str, JsonValue]]
    attempt: int
    lease_token: str
    leased_at: datetime
    lease_expires_at: datetime

    def to_envelope_data(self) -> dict[str, JsonValue]:
        """Rebuild the complete persisted envelope as transport-neutral JSON data."""

        return {
            "schema_version": self.envelope_schema_version,
            "message_id": str(self.message_id),
            "event_name": self.event_name,
            "producer": self.producer,
            "occurred_at": self.occurred_at.isoformat(),
            "produced_at": self.produced_at.isoformat(),
            "correlation_id": str(self.correlation_id),
            "causation_id": None if self.causation_id is None else str(self.causation_id),
            "partition_key": self.partition_key,
            "idempotency_key": self.idempotency_key,
            "attempt": self.attempt,
            "payload": _json_object(self.payload),
            "headers": cast(JsonValue, _json_array_of_objects(self.headers)),
        }


@dataclass(frozen=True, slots=True)
class InboxProcessingResult:
    """Transactionally committed technical-consumer disposition."""

    outcome: InboxProcessingOutcome
    incoming_message_id: UUID
    stored_message_id: UUID
    processed_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveredJobState:
    """Point-in-time state reconstructed from the append-only transition ledger."""

    job_id: UUID
    sequence_number: int
    status: JobStatus
    attempt: int
    occurred_at: datetime
    recorded_at: datetime
    request_id: UUID | None
    correlation_id: UUID | None
    reason_code: str | None


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _as_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise RecoveryFoundationError(f"database field {field_name} is not a UUID")
    return value


def _as_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise RecoveryFoundationError(f"database field {field_name} is not a datetime")
    _require_aware(value, field_name=field_name)
    return value


def _as_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise RecoveryFoundationError(f"database field {field_name} is not text")
    return value


def _as_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _as_uuid(value, field_name=field_name)


def _as_optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _as_string(value, field_name=field_name)


def _as_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryFoundationError(f"database field {field_name} is not an integer")
    return value


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        if any(not isinstance(key, str) for key in mapping):
            raise RecoveryFoundationError("JSON object keys must be strings")
        return {cast(str, key): _json_value(item) for key, item in mapping.items()}
    raise RecoveryFoundationError("database JSON contains an unsupported value")


def _json_object(value: object) -> dict[str, JsonValue]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise RecoveryFoundationError("database payload must be a JSON object")
    return converted


def _json_array_of_objects(value: object) -> list[dict[str, JsonValue]]:
    converted = _json_value(value)
    if not isinstance(converted, list) or any(not isinstance(item, dict) for item in converted):
        raise RecoveryFoundationError("database headers must be a JSON array of objects")
    return [cast(dict[str, JsonValue], item) for item in converted]


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _payload_schema_identity(payload: ContractModel) -> SchemaIdentity:
    raw_version: object = getattr(payload, "schema_version", None)
    if not isinstance(raw_version, str) or not _SCHEMA_VERSION_PATTERN.fullmatch(raw_version):
        raise RecoveryFoundationError(
            "Outbox payloads must expose an explicit MAJOR.MINOR schema_version"
        )
    raw_name = f"{payload.__class__.__module__}.{payload.__class__.__qualname__}".casefold()
    name = re.sub(r"[^a-z0-9_.-]+", "-", raw_name).strip(".-")
    if not _SCHEMA_NAME_PATTERN.fullmatch(name):
        raise RecoveryFoundationError("Outbox payload model does not have a stable schema name")
    schema_json = json.dumps(
        payload.__class__.model_json_schema(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return SchemaIdentity(
        name=name,
        version=raw_version,
        sha256=sha256(schema_json.encode("utf-8")).hexdigest(),
    )


def _inbox_safe_metadata[PayloadT: ContractModel](
    envelope: MessageEnvelope[PayloadT],
) -> dict[str, JsonValue]:
    """Return non-sensitive hashes needed to reject identity/content collisions."""

    schema = _payload_schema_identity(envelope.payload)
    logical_content = {
        "envelope_schema_version": envelope.schema_version,
        "event_name": envelope.event_name,
        "producer": envelope.producer,
        "occurred_at": envelope.occurred_at.astimezone(UTC).isoformat(),
        "produced_at": envelope.produced_at.astimezone(UTC).isoformat(),
        "correlation_id": str(envelope.correlation_id),
        "causation_id": (None if envelope.causation_id is None else str(envelope.causation_id)),
        "partition_key": envelope.partition_key,
        "idempotency_key": envelope.idempotency_key,
        "payload_schema_name": schema.name,
        "payload_schema_version": schema.version,
        "payload_schema_sha256": schema.sha256,
        "payload": envelope.payload.model_dump(mode="json"),
        "headers": [header.model_dump(mode="json") for header in envelope.headers],
    }
    content_sha256 = sha256(_canonical_json(logical_content).encode("utf-8")).hexdigest()
    return {
        "identity_version": "1.0",
        "logical_content_sha256": content_sha256,
        "payload_schema_name": schema.name,
        "payload_schema_version": schema.version,
        "payload_schema_sha256": schema.sha256,
    }


class PostgresUnitOfWork:
    """Explicit SQLAlchemy transaction implementing ``UnitOfWorkPort`` semantics."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._connection: AsyncConnection | None = None
        self._transaction: AsyncTransaction | None = None
        self._state = _UnitOfWorkState.NEW

    @property
    def connection(self) -> AsyncConnection:
        """Return the active platform-internal connection."""

        if (
            self._state is not _UnitOfWorkState.ACTIVE
            or self._connection is None
            or self._transaction is None
        ):
            raise RecoveryFoundationError("Unit of Work is not active")
        return self._connection

    async def __aenter__(self) -> Self:
        if self._state is not _UnitOfWorkState.NEW:
            raise RecoveryFoundationError("Unit of Work cannot be reused")
        connection = await self._engine.connect()
        self._connection = connection
        try:
            self._transaction = await connection.begin()
        except BaseException:
            self._state = _UnitOfWorkState.FAILED
            self._connection = None
            self._transaction = None
            await connection.close()
            raise
        self._state = _UnitOfWorkState.ACTIVE
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        transaction = self._transaction
        connection = self._connection
        try:
            if transaction is not None and transaction.is_active:
                await transaction.rollback()
                if self._state is _UnitOfWorkState.ACTIVE:
                    self._state = _UnitOfWorkState.ROLLED_BACK
        finally:
            if connection is not None:
                await connection.close()
            self._transaction = None
            self._connection = None

    async def commit(self) -> None:
        transaction = self._transaction
        if (
            self._state is not _UnitOfWorkState.ACTIVE
            or transaction is None
            or not transaction.is_active
        ):
            raise RecoveryFoundationError("Unit of Work has no active transaction")
        try:
            await transaction.commit()
        except BaseException:
            self._state = _UnitOfWorkState.FAILED
            raise
        self._state = _UnitOfWorkState.COMMITTED

    async def rollback(self) -> None:
        transaction = self._transaction
        if (
            self._state is not _UnitOfWorkState.ACTIVE
            or transaction is None
            or not transaction.is_active
        ):
            raise RecoveryFoundationError("Unit of Work has no active transaction")
        try:
            await transaction.rollback()
        except BaseException:
            self._state = _UnitOfWorkState.FAILED
            raise
        self._state = _UnitOfWorkState.ROLLED_BACK


class PostgresOutboxPublisher[PayloadT: ContractModel]:
    """Persist a broker-neutral envelope in the caller's business transaction."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    async def publish(
        self,
        request: MessagePublishRequest[PayloadT],
    ) -> DeliveryReceipt:
        envelope = request.envelope
        schema = _payload_schema_identity(envelope.payload)
        payload = envelope.payload.model_dump(mode="json")
        headers = [header.model_dump(mode="json") for header in envelope.headers]
        result = await self._unit_of_work.connection.execute(
            text(
                """
                INSERT INTO outbox_events (
                    id,
                    topic,
                    aggregate_type,
                    aggregate_id,
                    payload,
                    created_at,
                    published_at,
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
                    causation_id,
                    partition_key,
                    idempotency_key,
                    headers,
                    available_at
                )
                VALUES (
                    :message_id,
                    :event_name,
                    'technical_message',
                    :partition_key,
                    CAST(:payload AS jsonb),
                    GREATEST(clock_timestamp(), :produced_at),
                    NULL,
                    :attempt,
                    :message_id,
                    :event_name,
                    :envelope_schema_version,
                    :payload_schema_name,
                    :payload_schema_version,
                    :payload_schema_sha256,
                    :producer,
                    :occurred_at,
                    :produced_at,
                    :correlation_id,
                    :causation_id,
                    :partition_key,
                    :idempotency_key,
                    CAST(:headers AS jsonb),
                    GREATEST(clock_timestamp(), :produced_at)
                )
                ON CONFLICT DO NOTHING
                RETURNING message_id, created_at
                """
            ),
            {
                "message_id": envelope.message_id,
                "event_name": envelope.event_name,
                "payload": _canonical_json(payload),
                "attempt": envelope.attempt,
                "envelope_schema_version": envelope.schema_version,
                "payload_schema_name": schema.name,
                "payload_schema_version": schema.version,
                "payload_schema_sha256": schema.sha256,
                "producer": envelope.producer,
                "occurred_at": envelope.occurred_at,
                "produced_at": envelope.produced_at,
                "correlation_id": envelope.correlation_id,
                "causation_id": envelope.causation_id,
                "partition_key": envelope.partition_key,
                "idempotency_key": envelope.idempotency_key,
                "headers": _canonical_json(headers),
            },
        )
        inserted = result.mappings().one_or_none()
        if inserted is not None:
            return DeliveryReceipt(
                message_id=_as_uuid(inserted["message_id"], field_name="message_id"),
                status=DeliveryStatus.ACCEPTED,
                accepted_at=_as_datetime(inserted["created_at"], field_name="created_at"),
            )

        replay_rows = (
            (
                await self._unit_of_work.connection.execute(
                    text(
                        """
                        SELECT
                            message_id,
                            event_name,
                            envelope_schema_version,
                            payload_schema_name,
                            payload_schema_version,
                            producer,
                            occurred_at,
                            produced_at,
                            correlation_id,
                            causation_id,
                            partition_key,
                            idempotency_key,
                            payload,
                            headers,
                            payload_schema_sha256,
                            created_at
                          FROM outbox_events
                         WHERE message_id = :message_id
                            OR (
                                producer = :producer
                                AND event_name = :event_name
                                AND idempotency_key = :idempotency_key
                            )
                         FOR UPDATE
                        """
                    ),
                    {
                        "message_id": envelope.message_id,
                        "producer": envelope.producer,
                        "event_name": envelope.event_name,
                        "idempotency_key": envelope.idempotency_key,
                    },
                )
            )
            .mappings()
            .all()
        )
        if len(replay_rows) != 1:
            raise OutboxIdentityConflictError(
                "Outbox identity resolves to zero or multiple logical messages"
            )
        replay = replay_rows[0]
        expected_identity = (
            envelope.event_name,
            envelope.schema_version,
            schema.name,
            schema.version,
            envelope.producer,
            envelope.occurred_at,
            envelope.produced_at,
            envelope.correlation_id,
            envelope.causation_id,
            envelope.partition_key,
            envelope.idempotency_key,
            schema.sha256,
            _canonical_json(payload),
            _canonical_json(headers),
        )
        actual_identity = (
            _as_string(replay["event_name"], field_name="event_name"),
            _as_string(
                replay["envelope_schema_version"],
                field_name="envelope_schema_version",
            ),
            _as_string(replay["payload_schema_name"], field_name="payload_schema_name"),
            _as_string(
                replay["payload_schema_version"],
                field_name="payload_schema_version",
            ),
            _as_string(replay["producer"], field_name="producer"),
            _as_datetime(replay["occurred_at"], field_name="occurred_at"),
            _as_datetime(replay["produced_at"], field_name="produced_at"),
            _as_uuid(replay["correlation_id"], field_name="correlation_id"),
            _as_optional_uuid(replay["causation_id"], field_name="causation_id"),
            _as_string(replay["partition_key"], field_name="partition_key"),
            _as_string(replay["idempotency_key"], field_name="idempotency_key"),
            _as_string(replay["payload_schema_sha256"], field_name="payload_schema_sha256"),
            _canonical_json(replay["payload"]),
            _canonical_json(replay["headers"]),
        )
        if actual_identity != expected_identity:
            raise OutboxIdentityConflictError(
                "Outbox idempotency key was reused for different message content"
            )
        return DeliveryReceipt(
            message_id=_as_uuid(replay["message_id"], field_name="message_id"),
            status=DeliveryStatus.ACCEPTED,
            accepted_at=_as_datetime(replay["created_at"], field_name="created_at"),
        )


class PostgresOutboxRelayStore:
    """Lease unpublished Outbox rows without hiding at-least-once delivery semantics."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def claim(
        self,
        *,
        lease_token: str,
        claimed_at: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[ClaimedOutboxMessage]:
        if not _LEASE_TOKEN_PATTERN.fullmatch(lease_token):
            raise ValueError("lease_token must be a stable lower-case token")
        _require_aware(claimed_at, field_name="claimed_at")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    WITH exhausted AS (
                        SELECT id
                          FROM outbox_events
                         WHERE published_at IS NULL
                           AND attempt = 100
                           AND lease_expires_at IS NOT NULL
                           AND lease_expires_at <= :claimed_at
                         ORDER BY lease_expires_at, id
                         FOR UPDATE SKIP LOCKED
                         LIMIT :limit
                    )
                    UPDATE outbox_events AS outbox
                       SET lease_owner = NULL,
                           leased_at = NULL,
                           lease_expires_at = NULL,
                           last_error_code = 'OUTBOX_ATTEMPTS_EXHAUSTED'
                      FROM exhausted
                     WHERE outbox.id = exhausted.id
                    """
                ),
                {"claimed_at": claimed_at, "limit": limit},
            )
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            WITH candidates AS (
                                SELECT id
                                  FROM outbox_events
                                 WHERE published_at IS NULL
                                   AND available_at <= :claimed_at
                                   AND (
                                       lease_expires_at IS NULL
                                       OR lease_expires_at <= :claimed_at
                                   )
                                   AND (
                                       attempt < 100
                                       OR (
                                           attempt = 100
                                           AND lease_owner IS NULL
                                           AND last_error_code IS DISTINCT FROM
                                               'OUTBOX_ATTEMPTS_EXHAUSTED'
                                       )
                                   )
                                 ORDER BY available_at, created_at, id
                                 FOR UPDATE SKIP LOCKED
                                 LIMIT :limit
                            )
                            UPDATE outbox_events AS outbox
                               SET attempt = CASE
                                       WHEN outbox.lease_owner IS NULL THEN outbox.attempt
                                       ELSE outbox.attempt + 1
                                   END,
                                   lease_owner = :lease_token,
                                   leased_at = :claimed_at,
                                   lease_expires_at = :lease_expires_at,
                                   last_error_code = NULL
                              FROM candidates
                             WHERE outbox.id = candidates.id
                            RETURNING
                                outbox.id,
                                outbox.message_id,
                                outbox.event_name,
                                outbox.envelope_schema_version,
                                outbox.payload_schema_name,
                                outbox.payload_schema_version,
                                outbox.payload_schema_sha256,
                                outbox.producer,
                                outbox.correlation_id,
                                outbox.causation_id,
                                outbox.partition_key,
                                outbox.idempotency_key,
                                outbox.occurred_at,
                                outbox.produced_at,
                                outbox.payload,
                                outbox.headers,
                                outbox.attempt,
                                outbox.lease_owner,
                                outbox.leased_at,
                                outbox.lease_expires_at
                            """
                        ),
                        {
                            "claimed_at": claimed_at,
                            "lease_expires_at": lease_expires_at,
                            "lease_token": lease_token,
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [
            ClaimedOutboxMessage(
                outbox_id=_as_uuid(row["id"], field_name="id"),
                message_id=_as_uuid(row["message_id"], field_name="message_id"),
                event_name=_as_string(row["event_name"], field_name="event_name"),
                envelope_schema_version=_as_string(
                    row["envelope_schema_version"],
                    field_name="envelope_schema_version",
                ),
                payload_schema_name=_as_string(
                    row["payload_schema_name"],
                    field_name="payload_schema_name",
                ),
                payload_schema_version=_as_string(
                    row["payload_schema_version"],
                    field_name="payload_schema_version",
                ),
                payload_schema_sha256=_as_string(
                    row["payload_schema_sha256"],
                    field_name="payload_schema_sha256",
                ),
                producer=_as_string(row["producer"], field_name="producer"),
                correlation_id=_as_uuid(row["correlation_id"], field_name="correlation_id"),
                causation_id=_as_optional_uuid(row["causation_id"], field_name="causation_id"),
                partition_key=_as_string(row["partition_key"], field_name="partition_key"),
                idempotency_key=_as_string(row["idempotency_key"], field_name="idempotency_key"),
                occurred_at=_as_datetime(row["occurred_at"], field_name="occurred_at"),
                produced_at=_as_datetime(row["produced_at"], field_name="produced_at"),
                payload=_json_object(row["payload"]),
                headers=_json_array_of_objects(row["headers"]),
                attempt=_as_integer(row["attempt"], field_name="attempt"),
                lease_token=_as_string(row["lease_owner"], field_name="lease_owner"),
                leased_at=_as_datetime(row["leased_at"], field_name="leased_at"),
                lease_expires_at=_as_datetime(
                    row["lease_expires_at"], field_name="lease_expires_at"
                ),
            )
            for row in rows
        ]

    async def mark_published(
        self,
        *,
        message_id: UUID,
        lease_token: str,
        expected_attempt: int,
        published_at: datetime,
    ) -> bool:
        if not _LEASE_TOKEN_PATTERN.fullmatch(lease_token):
            raise ValueError("lease_token must be a stable lower-case token")
        if not 1 <= expected_attempt <= 100:
            raise ValueError("expected_attempt must be between 1 and 100")
        _require_aware(published_at, field_name="published_at")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE outbox_events
                       SET published_at = :published_at,
                           lease_owner = NULL,
                           leased_at = NULL,
                           lease_expires_at = NULL,
                           last_error_code = NULL
                     WHERE message_id = :message_id
                       AND published_at IS NULL
                       AND lease_owner = :lease_token
                       AND attempt = :expected_attempt
                    RETURNING id
                    """
                ),
                {
                    "message_id": message_id,
                    "lease_token": lease_token,
                    "expected_attempt": expected_attempt,
                    "published_at": published_at,
                },
            )
            return result.scalar_one_or_none() is not None

    async def release_for_retry(
        self,
        *,
        message_id: UUID,
        lease_token: str,
        expected_attempt: int,
        available_at: datetime,
        reason_code: str,
    ) -> OutboxReleaseOutcome:
        if not _LEASE_TOKEN_PATTERN.fullmatch(lease_token):
            raise ValueError("lease_token must be a stable lower-case token")
        if not 1 <= expected_attempt <= 100:
            raise ValueError("expected_attempt must be between 1 and 100")
        _require_aware(available_at, field_name="available_at")
        if not _REASON_CODE_PATTERN.fullmatch(reason_code):
            raise ValueError("reason_code must be an upper snake-case stable code")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE outbox_events
                       SET attempt = LEAST(attempt + 1, 100),
                           available_at = :available_at,
                           lease_owner = NULL,
                           leased_at = NULL,
                           lease_expires_at = NULL,
                           last_error_code = CASE
                               WHEN attempt = 100
                                   THEN 'OUTBOX_ATTEMPTS_EXHAUSTED'
                               ELSE :reason_code
                           END
                     WHERE message_id = :message_id
                       AND published_at IS NULL
                       AND lease_owner = :lease_token
                       AND attempt = :expected_attempt
                    RETURNING attempt, last_error_code
                    """
                ),
                {
                    "message_id": message_id,
                    "lease_token": lease_token,
                    "expected_attempt": expected_attempt,
                    "available_at": available_at,
                    "reason_code": reason_code,
                },
            )
            released = result.mappings().one_or_none()
        if released is None:
            return OutboxReleaseOutcome.NOT_OWNED
        stored_attempt = _as_integer(released["attempt"], field_name="attempt")
        stored_reason = _as_string(
            released["last_error_code"],
            field_name="last_error_code",
        )
        if stored_attempt == 100 and stored_reason == "OUTBOX_ATTEMPTS_EXHAUSTED":
            return OutboxReleaseOutcome.ATTEMPTS_EXHAUSTED
        return OutboxReleaseOutcome.RETRY_SCHEDULED


class PostgresInboxDeduplicator:
    """Commit a database side effect and technical receipt in one transaction.

    The handler receives the same database connection and must remain a bounded
    database-only operation. External effects require their own idempotent adapter
    and are intentionally outside this R0P primitive.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def process_once[PayloadT: ContractModel](
        self,
        *,
        consumer_id: str,
        envelope: MessageEnvelope[PayloadT],
        received_at: datetime,
        processed_at: datetime,
        delivery_attempt: int,
        handler: Callable[
            [AsyncConnection, MessageEnvelope[PayloadT]],
            Awaitable[None],
        ],
    ) -> InboxProcessingResult:
        if not _CONSUMER_ID_PATTERN.fullmatch(consumer_id):
            raise ValueError("consumer_id must be a stable lower-case identifier")
        _require_aware(received_at, field_name="received_at")
        _require_aware(processed_at, field_name="processed_at")
        if processed_at < received_at:
            raise ValueError("processed_at cannot precede received_at")
        if not 1 <= delivery_attempt <= 1000:
            raise ValueError("delivery_attempt must be between 1 and 1000")
        safe_metadata = _inbox_safe_metadata(envelope)

        async with self._engine.begin() as connection:
            inserted = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO message_inbox_receipts (
                            consumer_id,
                            message_id,
                            event_name,
                            idempotency_key,
                            received_at,
                            processing_started_at,
                            delivery_attempt,
                            disposition,
                            safe_metadata
                        )
                        VALUES (
                            :consumer_id,
                            :message_id,
                            :event_name,
                            :idempotency_key,
                            :received_at,
                            :received_at,
                            :delivery_attempt,
                            'processing',
                            CAST(:safe_metadata AS jsonb)
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING message_id
                        """
                    ),
                    {
                        "consumer_id": consumer_id,
                        "message_id": envelope.message_id,
                        "event_name": envelope.event_name,
                        "idempotency_key": envelope.idempotency_key,
                        "received_at": received_at,
                        "delivery_attempt": delivery_attempt,
                        "safe_metadata": _canonical_json(safe_metadata),
                    },
                )
            ).scalar_one_or_none()

            if inserted is None:
                rows = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT
                                    message_id,
                                    event_name,
                                    idempotency_key,
                                    disposition,
                                    processed_at,
                                    safe_metadata
                                  FROM message_inbox_receipts
                                 WHERE consumer_id = :consumer_id
                                   AND (
                                       message_id = :message_id
                                       OR (
                                           event_name = :event_name
                                           AND idempotency_key = :idempotency_key
                                       )
                                   )
                                 FOR UPDATE
                                """
                            ),
                            {
                                "consumer_id": consumer_id,
                                "message_id": envelope.message_id,
                                "event_name": envelope.event_name,
                                "idempotency_key": envelope.idempotency_key,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                if len(rows) != 1:
                    raise InboxStateConflictError(
                        "Inbox identity resolves to zero or multiple technical receipts"
                    )
                existing = rows[0]
                stored_message_id = _as_uuid(existing["message_id"], field_name="message_id")
                if (
                    _as_string(existing["event_name"], field_name="event_name")
                    != envelope.event_name
                    or _as_string(existing["idempotency_key"], field_name="idempotency_key")
                    != envelope.idempotency_key
                ):
                    raise InboxStateConflictError(
                        "Inbox message identity was reused for a different logical message"
                    )
                stored_metadata = _json_object(existing["safe_metadata"])
                if stored_metadata != safe_metadata:
                    raise InboxStateConflictError(
                        "Inbox identity was reused for different trace, time, producer, "
                        "partition, schema, headers or payload content"
                    )
                disposition = _as_string(existing["disposition"], field_name="disposition")
                if disposition in {"acknowledged", "dead_lettered", "discarded"}:
                    stored_processed_at = _as_datetime(
                        existing["processed_at"], field_name="processed_at"
                    )
                    return InboxProcessingResult(
                        outcome=InboxProcessingOutcome.IDEMPOTENT_REPLAY,
                        incoming_message_id=envelope.message_id,
                        stored_message_id=stored_message_id,
                        processed_at=stored_processed_at,
                    )
                if disposition == "processing":
                    raise InboxStateConflictError(
                        "Committed processing receipt requires manual reconciliation"
                    )
                await connection.execute(
                    text(
                        """
                        UPDATE message_inbox_receipts
                           SET processing_started_at = :received_at,
                               processed_at = NULL,
                               delivery_attempt = :delivery_attempt,
                               disposition = 'processing',
                               reason_code = NULL
                         WHERE consumer_id = :consumer_id
                           AND message_id = :stored_message_id
                        """
                    ),
                    {
                        "consumer_id": consumer_id,
                        "stored_message_id": stored_message_id,
                        "received_at": received_at,
                        "delivery_attempt": delivery_attempt,
                    },
                )
            else:
                stored_message_id = _as_uuid(inserted, field_name="message_id")

            await handler(connection, envelope)
            await connection.execute(
                text(
                    """
                    UPDATE message_inbox_receipts
                       SET disposition = 'acknowledged',
                           processed_at = :processed_at,
                           reason_code = NULL
                     WHERE consumer_id = :consumer_id
                       AND message_id = :stored_message_id
                    """
                ),
                {
                    "consumer_id": consumer_id,
                    "stored_message_id": stored_message_id,
                    "processed_at": processed_at,
                },
            )
            return InboxProcessingResult(
                outcome=InboxProcessingOutcome.PROCESSED,
                incoming_message_id=envelope.message_id,
                stored_message_id=stored_message_id,
                processed_at=processed_at,
            )


class PostgresJobRecoveryStore:
    """Read historical job state only from the append-only transition ledger."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def recover(
        self,
        *,
        job_id: UUID,
        as_of: datetime,
    ) -> RecoveredJobState | None:
        _require_aware(as_of, field_name="as_of")
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                                job_run_id,
                                sequence_number,
                                to_status,
                                attempt,
                                occurred_at,
                                recorded_at,
                                request_id,
                                correlation_id,
                                reason_code
                              FROM job_run_transitions
                             WHERE job_run_id = :job_id
                               AND occurred_at <= :as_of
                               AND recorded_at <= :as_of
                             ORDER BY sequence_number DESC
                             LIMIT 1
                            """
                        ),
                        {"job_id": job_id, "as_of": as_of},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        raw_status = _as_string(row["to_status"], field_name="to_status")
        try:
            status = JobStatus(raw_status)
        except ValueError as exc:
            raise RecoveryFoundationError(
                "transition ledger contains an unknown job status"
            ) from exc
        return RecoveredJobState(
            job_id=_as_uuid(row["job_run_id"], field_name="job_run_id"),
            sequence_number=_as_integer(row["sequence_number"], field_name="sequence_number"),
            status=status,
            attempt=_as_integer(row["attempt"], field_name="attempt"),
            occurred_at=_as_datetime(row["occurred_at"], field_name="occurred_at"),
            recorded_at=_as_datetime(row["recorded_at"], field_name="recorded_at"),
            request_id=_as_optional_uuid(row["request_id"], field_name="request_id"),
            correlation_id=_as_optional_uuid(row["correlation_id"], field_name="correlation_id"),
            reason_code=_as_optional_string(row["reason_code"], field_name="reason_code"),
        )
