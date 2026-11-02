"""Recovery acceptance tests against a disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from stock_assistant.config import get_settings
from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.messaging import (
    MessageEnvelope,
    MessageHeader,
    MessagePublishRequest,
)
from stock_assistant.platform.db.recovery import (
    InboxProcessingOutcome,
    InboxStateConflictError,
    OutboxIdentityConflictError,
    OutboxReleaseOutcome,
    PostgresInboxDeduplicator,
    PostgresJobRecoveryStore,
    PostgresOutboxPublisher,
    PostgresOutboxRelayStore,
    PostgresUnitOfWork,
    RecoveryFoundationError,
)
from stock_assistant.ports.messaging import MessagePublisherPort
from stock_assistant.ports.persistence import UnitOfWorkPort

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_HASH = "a" * 64
BASE_TIME = datetime(2099, 1, 2, 0, 0, tzinfo=UTC)


def _uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://stockassistant.invalid/r0p-recovery/{label}")


def _lease_identity(label: str) -> str:
    return f"relay-{_uuid(f'lease:{label}').hex}"


class SyntheticPayload(ContractModel):
    """Private test payload; it is deliberately not a published product schema."""

    schema_version: Literal["1.0"]
    value: str


class SyntheticAlternatePayload(ContractModel):
    """A different schema used to prove Inbox schema identity is enforced."""

    schema_version: Literal["1.0"]
    value: str
    qualifier: str


class SyntheticCrash(RuntimeError):
    """Raised at an intentional crash boundary."""


class _FakeTransaction:
    def __init__(self) -> None:
        self.is_active = True
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1
        self.is_active = False

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.is_active = False


class _FakeConnection:
    def __init__(self, *, begin_error: BaseException | None = None) -> None:
        self.transaction = _FakeTransaction()
        self.begin_error = begin_error
        self.begin_calls = 0
        self.close_calls = 0

    async def begin(self) -> _FakeTransaction:
        self.begin_calls += 1
        if self.begin_error is not None:
            raise self.begin_error
        return self.transaction

    async def close(self) -> None:
        self.close_calls += 1


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.connect_calls = 0

    async def connect(self) -> _FakeConnection:
        self.connect_calls += 1
        return self.connection


def _database_url() -> str:
    url = os.environ.get("R0P_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("R0P_TEST_DATABASE_URL is required for real recovery tests")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg":
        pytest.fail("recovery tests require a postgresql+asyncpg URL")
    if parsed.host not in {"127.0.0.1", "localhost", "postgres"}:
        pytest.fail("recovery tests only accept a local or CI-internal PostgreSQL host")
    if parsed.database is None or not parsed.database.startswith("stock_assistant_r0p_test"):
        pytest.fail("recovery database name must start with stock_assistant_r0p_test")
    return url


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(REPOSITORY_ROOT / "infra" / "migrations"))
    config.set_main_option("prepend_sys_path", f"{REPOSITORY_ROOT} {REPOSITORY_ROOT / 'src'}")
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="module")
def database_url() -> str:
    url = _database_url()
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(url), "head")
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()
    return url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    database_engine = create_async_engine(database_url)
    try:
        yield database_engine
    finally:
        await database_engine.dispose()


@pytest.mark.parametrize("terminal_action", ["commit", "rollback"])
async def test_unit_of_work_is_terminal_after_explicit_completion(
    terminal_action: Literal["commit", "rollback"],
) -> None:
    connection = _FakeConnection()
    fake_engine = _FakeEngine(connection)
    unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, fake_engine))

    async with unit_of_work:
        assert unit_of_work.connection is cast(AsyncConnection, connection)
        if terminal_action == "commit":
            await unit_of_work.commit()
        else:
            await unit_of_work.rollback()

        with pytest.raises(RecoveryFoundationError, match="not active"):
            _ = unit_of_work.connection
        with pytest.raises(RecoveryFoundationError, match="no active transaction"):
            await unit_of_work.commit()
        with pytest.raises(RecoveryFoundationError, match="no active transaction"):
            await unit_of_work.rollback()

    assert connection.close_calls == 1
    assert connection.transaction.commit_calls == (1 if terminal_action == "commit" else 0)
    assert connection.transaction.rollback_calls == (1 if terminal_action == "rollback" else 0)
    with pytest.raises(RecoveryFoundationError, match="cannot be reused"):
        await unit_of_work.__aenter__()
    assert fake_engine.connect_calls == 1


async def test_unit_of_work_closes_connection_when_begin_fails() -> None:
    connection = _FakeConnection(
        begin_error=SyntheticCrash("intentional transaction begin failure")
    )
    fake_engine = _FakeEngine(connection)
    unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, fake_engine))

    with pytest.raises(SyntheticCrash, match="begin failure"):
        await unit_of_work.__aenter__()

    assert connection.begin_calls == 1
    assert connection.close_calls == 1
    with pytest.raises(RecoveryFoundationError, match="not active"):
        _ = unit_of_work.connection
    with pytest.raises(RecoveryFoundationError, match="cannot be reused"):
        await unit_of_work.__aenter__()
    assert fake_engine.connect_calls == 1


def test_recovery_adapters_satisfy_declared_runtime_ports() -> None:
    connection = _FakeConnection()
    unit_of_work = PostgresUnitOfWork(cast(AsyncEngine, _FakeEngine(connection)))
    publisher = PostgresOutboxPublisher[SyntheticPayload](unit_of_work)

    assert isinstance(unit_of_work, UnitOfWorkPort)
    assert isinstance(publisher, MessagePublisherPort)


def _publication(
    *,
    idempotency_key: str,
    message_id: UUID | None = None,
    occurred_at: datetime | None = None,
    identity_label: str = "primary",
    producer: str = "recovery-test",
    partition_key: str = "synthetic:recovery",
    payload_value: str = "synthetic",
    headers: list[MessageHeader] | None = None,
    attempt: int = 1,
) -> MessagePublishRequest[SyntheticPayload]:
    occurred = occurred_at or BASE_TIME
    correlation_id = _uuid(f"message-correlation:{idempotency_key}:{identity_label}")
    context = MutationContext(
        schema_version="1.0",
        request_id=_uuid(f"message-request:{idempotency_key}:{identity_label}"),
        correlation_id=correlation_id,
        actor_id="recovery-test",
        workspace_id="synthetic",
        as_of=occurred,
        requested_at=occurred,
        idempotency_key=idempotency_key,
    )
    envelope = MessageEnvelope[SyntheticPayload](
        schema_version="1.0",
        message_id=message_id or _uuid(f"message-id:{idempotency_key}:{identity_label}"),
        event_name="com.stockassistant.test.synthetic.v1",
        producer=producer,
        occurred_at=occurred,
        produced_at=occurred,
        correlation_id=correlation_id,
        partition_key=partition_key,
        idempotency_key=idempotency_key,
        attempt=attempt,
        payload=SyntheticPayload(schema_version="1.0", value=payload_value),
        headers=headers or [],
    )
    return MessagePublishRequest[SyntheticPayload](
        schema_version="1.0",
        context=context,
        envelope=envelope,
    )


def _alternate_schema_envelope(
    envelope: MessageEnvelope[SyntheticPayload],
) -> MessageEnvelope[SyntheticAlternatePayload]:
    return MessageEnvelope[SyntheticAlternatePayload](
        schema_version=envelope.schema_version,
        message_id=envelope.message_id,
        event_name=envelope.event_name,
        producer=envelope.producer,
        occurred_at=envelope.occurred_at,
        produced_at=envelope.produced_at,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        partition_key=envelope.partition_key,
        idempotency_key=envelope.idempotency_key,
        attempt=envelope.attempt,
        payload=SyntheticAlternatePayload(
            schema_version="1.0",
            value=envelope.payload.value,
            qualifier="different-schema",
        ),
        headers=envelope.headers,
    )


def _changed_synthetic_envelope(
    envelope: MessageEnvelope[SyntheticPayload],
    *,
    producer: str | None = None,
    partition_key: str | None = None,
    payload_value: str | None = None,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
    occurred_at: datetime | None = None,
    produced_at: datetime | None = None,
    headers: list[MessageHeader] | None = None,
) -> MessageEnvelope[SyntheticPayload]:
    return MessageEnvelope[SyntheticPayload](
        schema_version=envelope.schema_version,
        message_id=envelope.message_id,
        event_name=envelope.event_name,
        producer=producer or envelope.producer,
        occurred_at=occurred_at or envelope.occurred_at,
        produced_at=produced_at or envelope.produced_at,
        correlation_id=correlation_id or envelope.correlation_id,
        causation_id=causation_id or envelope.causation_id,
        partition_key=partition_key or envelope.partition_key,
        idempotency_key=envelope.idempotency_key,
        attempt=envelope.attempt,
        payload=SyntheticPayload(
            schema_version="1.0",
            value=payload_value or envelope.payload.value,
        ),
        headers=envelope.headers if headers is None else headers,
    )


async def _insert_succeeded_job(
    connection: AsyncConnection,
    *,
    job_id: UUID,
    idempotency_key: str,
    observed_at: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO job_runs (
                id,
                task_name,
                idempotency_key,
                status,
                scheduled_for,
                started_at,
                finished_at,
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
                request_payload,
                result_payload
            )
            VALUES (
                :job_id,
                'stock_assistant.test.synthetic.v1',
                :idempotency_key,
                'succeeded',
                :observed_at,
                :observed_at,
                :observed_at,
                1,
                '{}'::jsonb,
                'test.synthetic.request',
                '1.0',
                :schema_hash,
                'test.synthetic.result',
                '1.0',
                :schema_hash,
                :request_id,
                :correlation_id,
                'recovery-test',
                'synthetic',
                :observed_at,
                :observed_at,
                :observed_at,
                'recovery-test',
                3,
                '{"schema_version":"1.0"}'::jsonb,
                '{"schema_version":"1.0","outcome":"synthetic"}'::jsonb
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            """
        ),
        {
            "job_id": job_id,
            "idempotency_key": idempotency_key,
            "schema_hash": SCHEMA_HASH,
            "request_id": _uuid(f"job-request:{job_id}"),
            "correlation_id": _uuid(f"job-correlation:{job_id}"),
            "observed_at": observed_at,
        },
    )


async def _count(
    engine: AsyncEngine,
    statement: str,
    parameters: Mapping[str, object],
) -> int:
    async with engine.connect() as connection:
        value = await connection.scalar(text(statement), parameters)
    assert isinstance(value, int)
    return value


async def _outbox_delivery_state(
    engine: AsyncEngine,
    *,
    message_id: UUID,
) -> Mapping[str, object]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT
                            attempt,
                            lease_owner,
                            leased_at,
                            lease_expires_at,
                            last_error_code,
                            published_at
                          FROM outbox_events
                         WHERE message_id = :message_id
                        """
                    ),
                    {"message_id": message_id},
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


async def _record_business_result_and_outbox(
    engine: AsyncEngine,
    *,
    job_id: UUID,
    job_idempotency_key: str,
    publication: MessagePublishRequest[SyntheticPayload],
    observed_at: datetime,
    crash_before_commit: bool = False,
) -> UUID:
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await _insert_succeeded_job(
            unit_of_work.connection,
            job_id=job_id,
            idempotency_key=job_idempotency_key,
            observed_at=observed_at,
        )
        receipt = await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(publication)
        if crash_before_commit:
            raise SyntheticCrash("intentional crash before business transaction commit")
        await unit_of_work.commit()
        return receipt.message_id


async def test_business_result_and_outbox_are_atomic_and_idempotent(
    engine: AsyncEngine,
) -> None:
    observed_at = BASE_TIME
    job_id = _uuid("atomic-job")
    job_key = "synthetic-job-atomic-v1"
    message_key = "synthetic-message-atomic-v1"
    publication = _publication(idempotency_key=message_key, occurred_at=observed_at)

    with pytest.raises(SyntheticCrash, match="before business transaction commit"):
        await _record_business_result_and_outbox(
            engine,
            job_id=job_id,
            job_idempotency_key=job_key,
            publication=publication,
            observed_at=observed_at,
            crash_before_commit=True,
        )

    assert (
        await _count(
            engine,
            "SELECT count(*) FROM job_runs WHERE idempotency_key = :key",
            {"key": job_key},
        )
        == 0
    )
    assert (
        await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE idempotency_key = :key",
            {"key": message_key},
        )
        == 0
    )

    message_ids = await asyncio.gather(
        *(
            _record_business_result_and_outbox(
                engine,
                job_id=job_id,
                job_idempotency_key=job_key,
                publication=publication,
                observed_at=observed_at,
            )
            for _ in range(100)
        )
    )

    assert set(message_ids) == {publication.envelope.message_id}
    assert (
        await _count(
            engine,
            "SELECT count(*) FROM job_runs WHERE idempotency_key = :key",
            {"key": job_key},
        )
        == 1
    )

    conflicting_publication = _publication(
        idempotency_key=message_key,
        occurred_at=observed_at,
        identity_label="conflict",
    )
    with pytest.raises(
        OutboxIdentityConflictError,
        match="different message content",
    ):
        async with PostgresUnitOfWork(engine) as unit_of_work:
            await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(
                conflicting_publication
            )
    assert (
        await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE idempotency_key = :key",
            {"key": message_key},
        )
        == 1
    )


async def test_outbox_commit_and_expired_lease_recover_without_new_logical_message(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=1)
    message_key = "synthetic-message-lease-v1"
    publication = _publication(
        idempotency_key=message_key,
        occurred_at=occurred_at,
        headers=[
            MessageHeader(
                name="trace-flags",
                value="synthetic-recovery",
            )
        ],
    )
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(publication)
        await unit_of_work.commit()

    relay = PostgresOutboxRelayStore(engine)
    first_claim_at = occurred_at + timedelta(seconds=2)
    first = await relay.claim(
        lease_token=_lease_identity("first"),
        claimed_at=first_claim_at,
        lease_seconds=5,
        limit=500,
    )
    claimed = next(item for item in first if item.message_id == publication.envelope.message_id)
    assert claimed.attempt == 1
    assert claimed.envelope_schema_version == publication.envelope.schema_version
    assert claimed.payload_schema_version == publication.envelope.payload.schema_version
    assert len(claimed.payload_schema_sha256) == 64
    reconstructed = MessageEnvelope[SyntheticPayload].model_validate(claimed.to_envelope_data())
    assert reconstructed == publication.envelope

    second_claim_at = first_claim_at + timedelta(seconds=6)
    second = await relay.claim(
        lease_token=claimed.lease_token,
        claimed_at=second_claim_at,
        lease_seconds=5,
        limit=500,
    )
    reclaimed = next(item for item in second if item.message_id == publication.envelope.message_id)
    assert reclaimed.attempt == 2
    assert reclaimed.outbox_id == claimed.outbox_id

    assert not await relay.mark_published(
        message_id=claimed.message_id,
        lease_token=claimed.lease_token,
        expected_attempt=claimed.attempt,
        published_at=second_claim_at + timedelta(seconds=1),
    )
    assert (
        await relay.release_for_retry(
            message_id=claimed.message_id,
            lease_token=claimed.lease_token,
            expected_attempt=claimed.attempt,
            available_at=second_claim_at + timedelta(seconds=2),
            reason_code="STALE_LEASE_ATTEMPT",
        )
        is OutboxReleaseOutcome.NOT_OWNED
    )
    assert await relay.mark_published(
        message_id=reclaimed.message_id,
        lease_token=reclaimed.lease_token,
        expected_attempt=reclaimed.attempt,
        published_at=second_claim_at + timedelta(seconds=1),
    )
    assert (
        await _count(
            engine,
            "SELECT count(*) FROM outbox_events WHERE idempotency_key = :key",
            {"key": message_key},
        )
        == 1
    )


async def test_outbox_ninety_ninth_failure_schedules_claimable_final_attempt(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=1, minutes=15)
    publication = _publication(
        idempotency_key="synthetic-message-attempt-99-v1",
        occurred_at=occurred_at,
        attempt=99,
    )
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(publication)
        await unit_of_work.commit()

    relay = PostgresOutboxRelayStore(engine)
    claimed_at = occurred_at + timedelta(seconds=1)
    claimed_batch = await relay.claim(
        lease_token=_lease_identity("attempt-99"),
        claimed_at=claimed_at,
        lease_seconds=5,
        limit=500,
    )
    claimed = next(
        item for item in claimed_batch if item.message_id == publication.envelope.message_id
    )
    assert claimed.attempt == 99

    retry_at = claimed_at + timedelta(seconds=2)
    outcome = await relay.release_for_retry(
        message_id=claimed.message_id,
        lease_token=claimed.lease_token,
        expected_attempt=claimed.attempt,
        available_at=retry_at,
        reason_code="SYNTHETIC_RETRYABLE_FAILURE",
    )
    assert outcome is OutboxReleaseOutcome.RETRY_SCHEDULED
    scheduled = await _outbox_delivery_state(
        engine,
        message_id=claimed.message_id,
    )
    assert scheduled["attempt"] == 100
    assert scheduled["last_error_code"] == "SYNTHETIC_RETRYABLE_FAILURE"
    assert scheduled["lease_owner"] is None

    final_batch = await relay.claim(
        lease_token=_lease_identity("attempt-100-final"),
        claimed_at=retry_at,
        lease_seconds=5,
        limit=500,
    )
    final_attempt = next(
        item for item in final_batch if item.message_id == publication.envelope.message_id
    )
    assert final_attempt.attempt == 100
    assert await relay.mark_published(
        message_id=final_attempt.message_id,
        lease_token=final_attempt.lease_token,
        expected_attempt=final_attempt.attempt,
        published_at=retry_at + timedelta(seconds=1),
    )


async def test_outbox_explicit_final_failure_is_auditable_and_not_reclaimed(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=1, minutes=30)
    publication = _publication(
        idempotency_key="synthetic-message-attempt-100-failure-v1",
        occurred_at=occurred_at,
        attempt=100,
    )
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(publication)
        await unit_of_work.commit()

    relay = PostgresOutboxRelayStore(engine)
    claimed_at = occurred_at + timedelta(seconds=1)
    claimed_batch = await relay.claim(
        lease_token=_lease_identity("attempt-100-failure"),
        claimed_at=claimed_at,
        lease_seconds=5,
        limit=500,
    )
    claimed = next(
        item for item in claimed_batch if item.message_id == publication.envelope.message_id
    )
    assert claimed.attempt == 100

    outcome = await relay.release_for_retry(
        message_id=claimed.message_id,
        lease_token=claimed.lease_token,
        expected_attempt=claimed.attempt,
        available_at=claimed_at + timedelta(seconds=2),
        reason_code="SYNTHETIC_RETRYABLE_FAILURE",
    )
    assert outcome is OutboxReleaseOutcome.ATTEMPTS_EXHAUSTED
    exhausted = await _outbox_delivery_state(
        engine,
        message_id=claimed.message_id,
    )
    assert exhausted["attempt"] == 100
    assert exhausted["last_error_code"] == "OUTBOX_ATTEMPTS_EXHAUSTED"
    assert exhausted["lease_owner"] is None
    assert exhausted["leased_at"] is None
    assert exhausted["lease_expires_at"] is None

    later_batch = await relay.claim(
        lease_token=_lease_identity("attempt-100-failure-later"),
        claimed_at=claimed_at + timedelta(seconds=10),
        lease_seconds=5,
        limit=500,
    )
    assert all(item.message_id != claimed.message_id for item in later_batch)


async def test_outbox_expired_final_lease_is_exhausted_and_not_reclaimed(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=1, minutes=45)
    publication = _publication(
        idempotency_key="synthetic-message-attempt-100-expiry-v1",
        occurred_at=occurred_at,
        attempt=100,
    )
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(publication)
        await unit_of_work.commit()

    relay = PostgresOutboxRelayStore(engine)
    claimed_at = occurred_at + timedelta(seconds=1)
    claimed_batch = await relay.claim(
        lease_token=_lease_identity("attempt-100-expiry"),
        claimed_at=claimed_at,
        lease_seconds=5,
        limit=500,
    )
    claimed = next(
        item for item in claimed_batch if item.message_id == publication.envelope.message_id
    )
    assert claimed.attempt == 100

    after_expiry = claimed.lease_expires_at + timedelta(microseconds=1)
    later_batch = await relay.claim(
        lease_token=_lease_identity("attempt-100-expiry-later"),
        claimed_at=after_expiry,
        lease_seconds=5,
        limit=500,
    )
    assert all(item.message_id != claimed.message_id for item in later_batch)
    exhausted = await _outbox_delivery_state(
        engine,
        message_id=claimed.message_id,
    )
    assert exhausted["attempt"] == 100
    assert exhausted["last_error_code"] == "OUTBOX_ATTEMPTS_EXHAUSTED"
    assert exhausted["lease_owner"] is None
    assert exhausted["leased_at"] is None
    assert exhausted["lease_expires_at"] is None


async def test_transactional_inbox_rolls_back_crashes_and_suppresses_redelivery(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=2)
    message_key = "synthetic-message-inbox-v1"
    publication = _publication(idempotency_key=message_key, occurred_at=occurred_at)
    envelope = publication.envelope
    consumer = PostgresInboxDeduplicator(engine)
    resource_id = str(envelope.message_id)

    async def crashing_handler(
        connection: AsyncConnection,
        _: MessageEnvelope[SyntheticPayload],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_log (
                    action,
                    resource_type,
                    resource_id,
                    request_id,
                    metadata,
                    created_at
                )
                VALUES (
                    'synthetic_recovery_effect',
                    'technical_message',
                    :resource_id,
                    :request_id,
                    '{}'::jsonb,
                    :created_at
                )
                """
            ),
            {
                "resource_id": resource_id,
                "request_id": str(_uuid("crashing-handler-request")),
                "created_at": occurred_at + timedelta(seconds=2),
            },
        )
        raise SyntheticCrash("intentional crash before consumer transaction commit")

    with pytest.raises(SyntheticCrash, match="before consumer transaction commit"):
        await consumer.process_once(
            consumer_id="recovery-consumer",
            envelope=envelope,
            received_at=occurred_at + timedelta(seconds=1),
            processed_at=occurred_at + timedelta(seconds=2),
            delivery_attempt=1,
            handler=crashing_handler,
        )

    assert (
        await _count(
            engine,
            """
            SELECT count(*)
              FROM audit_log
             WHERE action = 'synthetic_recovery_effect'
               AND resource_id = :resource_id
            """,
            {"resource_id": resource_id},
        )
        == 0
    )
    assert (
        await _count(
            engine,
            """
            SELECT count(*)
              FROM message_inbox_receipts
             WHERE consumer_id = 'recovery-consumer'
               AND message_id = :message_id
            """,
            {"message_id": envelope.message_id},
        )
        == 0
    )

    async def successful_handler(
        connection: AsyncConnection,
        _: MessageEnvelope[SyntheticPayload],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_log (
                    action,
                    resource_type,
                    resource_id,
                    request_id,
                    metadata,
                    created_at
                )
                VALUES (
                    'synthetic_recovery_effect',
                    'technical_message',
                    :resource_id,
                    :request_id,
                    '{}'::jsonb,
                    :created_at
                )
                """
            ),
            {
                "resource_id": resource_id,
                "request_id": str(_uuid("successful-handler-request")),
                "created_at": occurred_at + timedelta(seconds=3),
            },
        )

    first = await consumer.process_once(
        consumer_id="recovery-consumer",
        envelope=envelope,
        received_at=occurred_at + timedelta(seconds=2),
        processed_at=occurred_at + timedelta(seconds=3),
        delivery_attempt=2,
        handler=successful_handler,
    )
    assert first.outcome is InboxProcessingOutcome.PROCESSED

    replay_handler_calls = 0

    async def must_not_repeat(
        _: AsyncConnection,
        __: MessageEnvelope[SyntheticPayload],
    ) -> None:
        nonlocal replay_handler_calls
        replay_handler_calls += 1

    replay = await consumer.process_once(
        consumer_id="recovery-consumer",
        envelope=envelope,
        received_at=occurred_at + timedelta(seconds=4),
        processed_at=occurred_at + timedelta(seconds=5),
        delivery_attempt=3,
        handler=must_not_repeat,
    )
    assert replay.outcome is InboxProcessingOutcome.IDEMPOTENT_REPLAY
    assert replay_handler_calls == 0
    assert (
        await _count(
            engine,
            """
            SELECT count(*)
              FROM audit_log
             WHERE action = 'synthetic_recovery_effect'
               AND resource_id = :resource_id
            """,
            {"resource_id": resource_id},
        )
        == 1
    )


async def test_inbox_rejects_reused_identity_with_different_logical_content(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=2, minutes=30)
    message_key = "synthetic-message-inbox-collision-v1"
    publication = _publication(idempotency_key=message_key, occurred_at=occurred_at)
    envelope = publication.envelope
    consumer = PostgresInboxDeduplicator(engine)

    async def no_effect(
        _: AsyncConnection,
        __: MessageEnvelope[SyntheticPayload],
    ) -> None:
        return None

    processed = await consumer.process_once(
        consumer_id="identity-consumer",
        envelope=envelope,
        received_at=occurred_at + timedelta(seconds=1),
        processed_at=occurred_at + timedelta(seconds=2),
        delivery_attempt=1,
        handler=no_effect,
    )
    assert processed.outcome is InboxProcessingOutcome.PROCESSED

    same_content_new_delivery = _publication(
        idempotency_key=message_key,
        message_id=_uuid("inbox-collision-logical-replay"),
        occurred_at=occurred_at,
    ).envelope
    replay = await consumer.process_once(
        consumer_id="identity-consumer",
        envelope=same_content_new_delivery,
        received_at=occurred_at + timedelta(seconds=3),
        processed_at=occurred_at + timedelta(seconds=4),
        delivery_attempt=2,
        handler=no_effect,
    )
    assert replay.outcome is InboxProcessingOutcome.IDEMPOTENT_REPLAY
    assert replay.incoming_message_id == same_content_new_delivery.message_id
    assert replay.stored_message_id == envelope.message_id

    changed_envelopes = (
        _changed_synthetic_envelope(
            envelope,
            producer="recovery-test-alternate",
        ),
        _changed_synthetic_envelope(
            envelope,
            partition_key="synthetic:alternate",
        ),
        _changed_synthetic_envelope(
            envelope,
            payload_value="changed-content",
        ),
        _changed_synthetic_envelope(
            envelope,
            correlation_id=_uuid("inbox-collision-correlation"),
        ),
        _changed_synthetic_envelope(
            envelope,
            causation_id=_uuid("inbox-collision-causation"),
        ),
        _changed_synthetic_envelope(
            envelope,
            occurred_at=envelope.occurred_at - timedelta(microseconds=1),
        ),
        _changed_synthetic_envelope(
            envelope,
            produced_at=envelope.produced_at + timedelta(microseconds=1),
        ),
        _changed_synthetic_envelope(
            envelope,
            headers=[MessageHeader(name="trace-flags", value="changed-header")],
        ),
    )
    for attempt, changed in enumerate(changed_envelopes, start=3):
        with pytest.raises(InboxStateConflictError, match="Inbox identity was reused"):
            await consumer.process_once(
                consumer_id="identity-consumer",
                envelope=changed,
                received_at=occurred_at + timedelta(seconds=attempt + 2),
                processed_at=occurred_at + timedelta(seconds=attempt + 3),
                delivery_attempt=attempt,
                handler=no_effect,
            )

    alternate_schema = _alternate_schema_envelope(envelope)

    async def alternate_no_effect(
        _: AsyncConnection,
        __: MessageEnvelope[SyntheticAlternatePayload],
    ) -> None:
        return None

    with pytest.raises(InboxStateConflictError, match="Inbox identity was reused"):
        await consumer.process_once(
            consumer_id="identity-consumer",
            envelope=alternate_schema,
            received_at=occurred_at + timedelta(seconds=10),
            processed_at=occurred_at + timedelta(seconds=11),
            delivery_attempt=10,
            handler=alternate_no_effect,
        )

    async with engine.connect() as connection:
        safe_metadata = await connection.scalar(
            text(
                """
                SELECT safe_metadata
                  FROM message_inbox_receipts
                 WHERE consumer_id = 'identity-consumer'
                   AND message_id = :message_id
                """
            ),
            {"message_id": envelope.message_id},
        )
    assert isinstance(safe_metadata, dict)
    assert set(safe_metadata) == {
        "identity_version",
        "logical_content_sha256",
        "payload_schema_name",
        "payload_schema_version",
        "payload_schema_sha256",
    }
    assert safe_metadata["logical_content_sha256"] != envelope.payload.value
    assert len(safe_metadata["logical_content_sha256"]) == 64


async def test_relay_crash_after_broker_delivery_replays_without_duplicate_side_effect(
    engine: AsyncEngine,
) -> None:
    occurred_at = BASE_TIME + timedelta(hours=2, minutes=45)
    message_key = "synthetic-message-relay-crash-v1"
    publication = _publication(
        idempotency_key=message_key,
        occurred_at=occurred_at,
        headers=[MessageHeader(name="trace-flags", value="relay-crash")],
    )
    async with PostgresUnitOfWork(engine) as unit_of_work:
        await PostgresOutboxPublisher[SyntheticPayload](unit_of_work).publish(publication)
        await unit_of_work.commit()

    relay = PostgresOutboxRelayStore(engine)
    first_claim_at = occurred_at + timedelta(seconds=1)
    first_batch = await relay.claim(
        lease_token=_lease_identity("relay-crash-first"),
        claimed_at=first_claim_at,
        lease_seconds=5,
        limit=500,
    )
    first_claim = next(
        item for item in first_batch if item.message_id == publication.envelope.message_id
    )
    first_delivery = MessageEnvelope[SyntheticPayload].model_validate(
        first_claim.to_envelope_data()
    )

    consumer = PostgresInboxDeduplicator(engine)
    resource_id = str(first_delivery.message_id)

    async def record_effect(
        connection: AsyncConnection,
        _: MessageEnvelope[SyntheticPayload],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_log (
                    action,
                    resource_type,
                    resource_id,
                    request_id,
                    metadata,
                    created_at
                )
                VALUES (
                    'synthetic_relay_crash_effect',
                    'technical_message',
                    :resource_id,
                    :request_id,
                    '{}'::jsonb,
                    :created_at
                )
                """
            ),
            {
                "resource_id": resource_id,
                "request_id": str(_uuid("relay-crash-effect-request")),
                "created_at": occurred_at + timedelta(seconds=2),
            },
        )

    first_result = await consumer.process_once(
        consumer_id="relay-crash-consumer",
        envelope=first_delivery,
        received_at=occurred_at + timedelta(seconds=2),
        processed_at=occurred_at + timedelta(seconds=3),
        delivery_attempt=first_claim.attempt,
        handler=record_effect,
    )
    assert first_result.outcome is InboxProcessingOutcome.PROCESSED
    # The broker delivery succeeded, but the relay process crashes before mark_published.

    second_claim_at = first_claim.lease_expires_at + timedelta(microseconds=1)
    second_batch = await relay.claim(
        lease_token=_lease_identity("relay-crash-second"),
        claimed_at=second_claim_at,
        lease_seconds=5,
        limit=500,
    )
    second_claim = next(
        item for item in second_batch if item.message_id == publication.envelope.message_id
    )
    assert second_claim.attempt == first_claim.attempt + 1
    duplicate_delivery = MessageEnvelope[SyntheticPayload].model_validate(
        second_claim.to_envelope_data()
    )

    duplicate_handler_calls = 0

    async def must_not_repeat(
        _: AsyncConnection,
        __: MessageEnvelope[SyntheticPayload],
    ) -> None:
        nonlocal duplicate_handler_calls
        duplicate_handler_calls += 1

    replay = await consumer.process_once(
        consumer_id="relay-crash-consumer",
        envelope=duplicate_delivery,
        received_at=second_claim_at + timedelta(seconds=1),
        processed_at=second_claim_at + timedelta(seconds=2),
        delivery_attempt=second_claim.attempt,
        handler=must_not_repeat,
    )
    assert replay.outcome is InboxProcessingOutcome.IDEMPOTENT_REPLAY
    assert duplicate_handler_calls == 0
    assert await relay.mark_published(
        message_id=second_claim.message_id,
        lease_token=second_claim.lease_token,
        expected_attempt=second_claim.attempt,
        published_at=second_claim_at + timedelta(seconds=3),
    )
    assert (
        await _count(
            engine,
            """
            SELECT count(*)
              FROM audit_log
             WHERE action = 'synthetic_relay_crash_effect'
               AND resource_id = :resource_id
            """,
            {"resource_id": resource_id},
        )
        == 1
    )
    assert (
        await _count(
            engine,
            """
            SELECT count(*)
              FROM message_inbox_receipts
             WHERE consumer_id = 'relay-crash-consumer'
               AND message_id = :message_id
            """,
            {"message_id": publication.envelope.message_id},
        )
        == 1
    )


async def _insert_queued_job(
    connection: AsyncConnection,
    *,
    job_id: UUID,
    idempotency_key: str,
    queued_at: datetime,
) -> None:
    await connection.execute(
        text(
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
                :job_id,
                'stock_assistant.test.synthetic.v1',
                :idempotency_key,
                'queued',
                :queued_at,
                1,
                '{}'::jsonb,
                'test.synthetic.request',
                '1.0',
                :schema_hash,
                'test.synthetic.result',
                '1.0',
                :schema_hash,
                :request_id,
                :correlation_id,
                'recovery-test',
                'synthetic',
                :queued_at,
                :queued_at,
                :queued_at,
                'recovery-test',
                3,
                '{"schema_version":"1.0"}'::jsonb
            )
            """
        ),
        {
            "job_id": job_id,
            "idempotency_key": idempotency_key,
            "schema_hash": SCHEMA_HASH,
            "request_id": _uuid(f"queued-job-request:{job_id}"),
            "correlation_id": _uuid(f"queued-job-correlation:{job_id}"),
            "queued_at": queued_at,
        },
    )


async def _insert_job_with_status(
    connection: AsyncConnection,
    *,
    job_id: UUID,
    status: JobStatus,
    observed_at: datetime,
) -> None:
    in_progress = status in {JobStatus.QUEUED, JobStatus.RUNNING}
    carries_result = status in {
        JobStatus.SUCCEEDED,
        JobStatus.DEGRADED,
        JobStatus.SKIPPED,
    }
    failed = status is JobStatus.FAILED
    await connection.execute(
        text(
            """
            INSERT INTO job_runs (
                id,
                task_name,
                idempotency_key,
                status,
                scheduled_for,
                started_at,
                finished_at,
                attempt,
                error_code,
                safe_error_message,
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
                request_payload,
                result_payload
            )
            VALUES (
                :job_id,
                'stock_assistant.test.synthetic.v1',
                :idempotency_key,
                :status,
                :observed_at,
                :started_at,
                :finished_at,
                1,
                :error_code,
                :safe_error_message,
                '{}'::jsonb,
                'test.synthetic.request',
                '1.0',
                :schema_hash,
                'test.synthetic.result',
                '1.0',
                :schema_hash,
                :request_id,
                :correlation_id,
                'recovery-test',
                'synthetic',
                :observed_at,
                :observed_at,
                :observed_at,
                'recovery-test',
                3,
                '{"schema_version":"1.0"}'::jsonb,
                CAST(:result_payload AS jsonb)
            )
            """
        ),
        {
            "job_id": job_id,
            "idempotency_key": f"synthetic-job-state-{status.value}-v1",
            "status": status.value,
            "observed_at": observed_at,
            "started_at": None if status is JobStatus.QUEUED else observed_at,
            "finished_at": None if in_progress else observed_at,
            "error_code": "SYNTHETIC_FAILURE" if failed else None,
            "safe_error_message": "Synthetic failure" if failed else None,
            "schema_hash": SCHEMA_HASH,
            "request_id": _uuid(f"state-job-request:{status.value}"),
            "correlation_id": _uuid(f"state-job-correlation:{status.value}"),
            "result_payload": (
                '{"schema_version":"1.0","outcome":"synthetic"}' if carries_result else None
            ),
        },
    )


async def test_job_state_is_recovered_from_point_in_time_transition_ledger(
    engine: AsyncEngine,
) -> None:
    queued_at = BASE_TIME + timedelta(hours=3)
    running_at = queued_at + timedelta(seconds=10)
    failed_at = queued_at + timedelta(seconds=20)
    job_id = _uuid("point-in-time-job")

    async with engine.begin() as connection:
        await _insert_queued_job(
            connection,
            job_id=job_id,
            idempotency_key="synthetic-job-point-in-time-v1",
            queued_at=queued_at,
        )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE job_runs
                   SET status = 'running',
                       started_at = :running_at
                 WHERE id = :job_id
                """
            ),
            {"job_id": job_id, "running_at": running_at},
        )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE job_runs
                   SET status = 'failed',
                       finished_at = :failed_at,
                       error_code = 'SYNTHETIC_FAILURE',
                       safe_error_message = 'Synthetic recovery test failure'
                 WHERE id = :job_id
                """
            ),
            {"job_id": job_id, "failed_at": failed_at},
        )

    recovery = PostgresJobRecoveryStore(engine)
    before_queue = await recovery.recover(
        job_id=job_id,
        as_of=queued_at - timedelta(microseconds=1),
    )
    assert before_queue is None

    queued = await recovery.recover(job_id=job_id, as_of=queued_at)
    running = await recovery.recover(job_id=job_id, as_of=running_at)
    failed = await recovery.recover(job_id=job_id, as_of=failed_at)

    assert queued is not None
    assert queued.status is JobStatus.QUEUED
    assert queued.sequence_number == 1
    assert queued.recorded_at <= queued_at
    assert running is not None
    assert running.status is JobStatus.RUNNING
    assert running.sequence_number == 2
    assert running.recorded_at <= running_at
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.sequence_number == 3
    assert failed.recorded_at <= failed_at


async def test_job_recovery_rejects_backdated_transitions_until_recorded(
    engine: AsyncEngine,
) -> None:
    historical_queued_at = datetime(2020, 1, 2, 0, 0, tzinfo=UTC)
    historical_running_at = historical_queued_at + timedelta(seconds=10)
    job_id = _uuid("knowledge-time-job")
    async with engine.begin() as connection:
        await _insert_queued_job(
            connection,
            job_id=job_id,
            idempotency_key="synthetic-job-knowledge-time-v1",
            queued_at=historical_queued_at,
        )
    async with engine.connect() as connection:
        queued_recorded_at = await connection.scalar(
            text(
                """
                SELECT recorded_at
                  FROM job_run_transitions
                 WHERE job_run_id = :job_id
                   AND sequence_number = 1
                """
            ),
            {"job_id": job_id},
        )
    assert isinstance(queued_recorded_at, datetime)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE job_runs
                   SET status = 'running',
                       started_at = :running_at
                 WHERE id = :job_id
                """
            ),
            {"job_id": job_id, "running_at": historical_running_at},
        )
    async with engine.connect() as connection:
        running_recorded_at = await connection.scalar(
            text(
                """
                SELECT recorded_at
                  FROM job_run_transitions
                 WHERE job_run_id = :job_id
                   AND sequence_number = 2
                """
            ),
            {"job_id": job_id},
        )
    assert isinstance(running_recorded_at, datetime)
    assert running_recorded_at > queued_recorded_at

    recovery = PostgresJobRecoveryStore(engine)
    before_running_was_recorded = await recovery.recover(
        job_id=job_id,
        as_of=running_recorded_at - timedelta(microseconds=1),
    )
    assert before_running_was_recorded is not None
    assert before_running_was_recorded.status is JobStatus.QUEUED
    assert before_running_was_recorded.recorded_at == queued_recorded_at

    after_running_was_recorded = await recovery.recover(
        job_id=job_id,
        as_of=running_recorded_at,
    )
    assert after_running_was_recorded is not None
    assert after_running_was_recorded.status is JobStatus.RUNNING
    assert after_running_was_recorded.recorded_at == running_recorded_at


async def test_all_eight_job_states_are_recoverable(
    engine: AsyncEngine,
) -> None:
    observed_at = BASE_TIME + timedelta(hours=4)
    jobs = {status: _uuid(f"job-state:{status.value}") for status in JobStatus}
    assert len(jobs) == 8
    async with engine.begin() as connection:
        for status, job_id in jobs.items():
            await _insert_job_with_status(
                connection,
                job_id=job_id,
                status=status,
                observed_at=observed_at,
            )

    recovery = PostgresJobRecoveryStore(engine)
    recovered = await asyncio.gather(
        *(
            recovery.recover(
                job_id=job_id,
                as_of=observed_at,
            )
            for job_id in jobs.values()
        )
    )
    recovered_statuses = {state.status for state in recovered if state is not None}
    assert recovered_statuses == set(JobStatus)
