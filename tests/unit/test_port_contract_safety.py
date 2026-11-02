import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import stock_assistant.contracts.storage as storage_contracts
from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    GenerationResponse,
    ModelOperation,
    ModelProviderCapability,
    PrivacyLevel,
)
from stock_assistant.contracts.messaging import (
    DeadLetterAction,
    MessageEnvelope,
    MessagePublishRequest,
    MessageRejectRequest,
    ReceivedMessage,
)
from stock_assistant.contracts.persistence import MarketEventQuery, MarketEventUpsertRequest
from stock_assistant.contracts.storage import (
    ObjectWriteRequest,
    RetentionClass,
    StoredObjectRef,
)
from stock_assistant.ports.infrastructure import CachePort, DistributedLockPort, ObjectStorePort
from stock_assistant.ports.messaging import MessageConsumerPort, MessagePublisherPort
from stock_assistant.ports.persistence import MarketEventRepositoryPort, PortfolioRepositoryPort
from stock_assistant.ports.security import SecretProviderPort


class _Payload(ContractModel):
    value: str


def _context(now: datetime) -> OperationContext:
    return OperationContext(schema_version="1.0", as_of=now)


def _mutation_context(
    now: datetime, *, idempotency_key: str = "mutation-idempotency-key-0001"
) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=now,
        idempotency_key=idempotency_key,
    )


def _schema_hash(schema: dict[str, object]) -> str:
    canonical = json.dumps(
        schema,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_generation_contract_binds_schema_context_and_validated_payload() -> None:
    now = datetime.now(UTC)
    schema: dict[str, object] = {"type": "object"}
    schema_hash = _schema_hash(schema)

    request = GenerationRequest(
        schema_version="1.0",
        task_type="event_extraction",
        messages=[{"role": "user", "content": "synthetic evidence"}],
        response_schema=schema,
        response_schema_hash=schema_hash,
        response_schema_namespace="events.market_event",
        response_schema_owner="events",
        context=_context(now),
    )
    assert request.response_schema_hash == schema_hash

    with pytest.raises(ValidationError):
        GenerationRequest(
            schema_version="1.0",
            task_type="event_extraction",
            messages=[{"role": "user", "content": "synthetic evidence"}],
            response_schema=schema,
            response_schema_hash="0" * 64,
            response_schema_namespace="events.market_event",
            response_schema_owner="events",
            context=_context(now),
        )

    with pytest.raises(ValidationError):
        GenerationResponse(
            schema_version="1.0",
            payload={"direction": "neutral"},
            payload_namespace="events.market_event",
            payload_owner="events",
            response_schema_hash=schema_hash,
            provider="local",
            model="synthetic",
            adapter_version="1.0.0",
            prompt_hash="a" * 64,
            raw_response_hash="b" * 64,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            schema_validated=False,
        )


def test_external_capability_cannot_claim_local_only_processing() -> None:
    with pytest.raises(ValidationError):
        ModelProviderCapability(
            schema_version="1.0",
            provider_name="external_provider",
            model="synthetic",
            adapter_version="1.0.0",
            operations=[ModelOperation.GENERATION],
            supported_privacy_levels=[PrivacyLevel.LOCAL_ONLY],
            external_processing=True,
            structured_output=True,
        )


def test_publish_context_must_match_envelope_idempotency_and_correlation() -> None:
    now = datetime.now(UTC)
    context = _mutation_context(now)
    envelope = MessageEnvelope[_Payload](
        schema_version="1.0",
        event_name="com.stockassistant.tests.completed.v1",
        producer="unit-tests",
        occurred_at=now,
        produced_at=now,
        correlation_id=context.correlation_id,
        partition_key="tests",
        idempotency_key="different-idempotency-key-0001",
        payload=_Payload(value="safe"),
    )
    with pytest.raises(ValidationError):
        MessagePublishRequest[_Payload](
            schema_version="1.0",
            context=context,
            envelope=envelope,
        )


def test_acknowledgement_tokens_are_masked_and_rejection_is_explicit() -> None:
    now = datetime.now(UTC)
    context = _mutation_context(now)
    opaque_token = "-".join(("synthetic", "opaque", "handle"))
    envelope = MessageEnvelope[_Payload](
        schema_version="1.0",
        event_name="com.stockassistant.tests.completed.v1",
        producer="unit-tests",
        occurred_at=now,
        produced_at=now,
        correlation_id=context.correlation_id,
        partition_key="tests",
        idempotency_key=context.idempotency_key,
        payload=_Payload(value="safe"),
    )
    received = ReceivedMessage[_Payload](
        envelope=envelope,
        acknowledgement_token=opaque_token,
        received_at=now,
    )
    assert opaque_token not in repr(received)
    assert opaque_token not in received.model_dump_json()

    with pytest.raises(ValidationError):
        MessageRejectRequest(
            schema_version="1.0",
            context=context,
            consumer_id="test-consumer",
            message_id=envelope.message_id,
            acknowledgement_token=opaque_token,
            action=DeadLetterAction.RETRY,
            reason_code="DEPENDENCY_UNAVAILABLE",
        )


def test_object_retention_is_explicit_and_rights_snapshot_survives_reference() -> None:
    now = datetime.now(UTC)
    context = _mutation_context(now)
    with pytest.raises(ValidationError):
        ObjectWriteRequest(
            schema_version="1.0",
            context=context,
            namespace="evidence",
            media_type="application/json",
            content_length=2,
            content_sha256="a" * 64,
            retention_class=RetentionClass.EPISODIC,
        )

    rights_hash = "b" * 64
    reference = StoredObjectRef(
        schema_version="1.0",
        namespace="evidence",
        object_key="safe/object.json",
        media_type="application/json",
        content_length=2,
        content_sha256="a" * 64,
        stored_at=now,
        retention_class=RetentionClass.EPISODIC,
        retention_until=now + timedelta(days=7),
        rights_snapshot_hash=rights_hash,
    )
    assert reference.rights_snapshot_hash == rights_hash


def test_event_repository_query_is_aggregate_specific_and_point_in_time_safe() -> None:
    now = datetime.now(UTC)
    assert not hasattr(storage_contracts, "RepositoryQuery")
    with pytest.raises(ValidationError):
        MarketEventQuery(
            schema_version="1.0",
            context=_context(now),
            revision_known_until=now + timedelta(seconds=1),
        )


def test_event_upsert_requires_exact_optimistic_revision(event_factory) -> None:
    now = datetime.now(UTC)
    event = event_factory()
    with pytest.raises(ValidationError):
        MarketEventUpsertRequest(
            schema_version="1.0",
            context=_mutation_context(now),
            event=event,
            expected_revision=event.revision,
        )


@pytest.mark.parametrize(
    ("port", "method_names"),
    [
        (ObjectStorePort, ("stream", "delete", "exists")),
        (CachePort, ("get", "set", "delete")),
        (DistributedLockPort, ("acquire", "renew", "release")),
        (MessagePublisherPort, ("publish",)),
        (MessageConsumerPort, ("pull", "acknowledge", "reject")),
        (MarketEventRepositoryPort, ("get_event", "upsert_event", "list_events")),
        (
            PortfolioRepositoryPort,
            ("get_latest", "save_snapshot", "get_active_target", "save_active_target"),
        ),
        (SecretProviderPort, ("resolve",)),
    ],
)
def test_public_io_ports_use_one_request_contract(
    port: type[object], method_names: tuple[str, ...]
) -> None:
    for method_name in method_names:
        parameters = tuple(inspect.signature(getattr(port, method_name)).parameters)
        assert parameters == ("self", "request")
