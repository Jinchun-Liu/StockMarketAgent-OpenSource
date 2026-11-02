"""Broker-neutral message envelopes and delivery acknowledgements."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext


class MessageHeader(ContractModel):
    """A safe, explicitly typed extension header."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    value: str = Field(min_length=1, max_length=2048)


class MessageEnvelope[PayloadT: ContractModel](ContractModel):
    """Versioned integration event independent of a specific broker."""

    schema_version: Literal["1.0"] = "1.0"
    message_id: UUID = Field(default_factory=uuid4)
    event_name: str = Field(
        pattern=(
            r"^com\.stockassistant\.[a-z][a-z0-9_]*"
            r"(?:\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*$"
        )
    )
    producer: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    occurred_at: AwareDatetime
    produced_at: AwareDatetime
    correlation_id: UUID
    causation_id: UUID | None = None
    partition_key: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=16, max_length=200)
    attempt: int = Field(default=1, ge=1, le=100)
    payload: PayloadT
    headers: list[MessageHeader] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def production_must_follow_occurrence(self) -> "MessageEnvelope[PayloadT]":
        if self.produced_at < self.occurred_at:
            raise ValueError("produced_at cannot be earlier than occurred_at")
        if len({header.name for header in self.headers}) != len(self.headers):
            raise ValueError("message header names must be unique")
        return self


class MessagePublishRequest[PayloadT: ContractModel](ContractModel):
    """Idempotent publish command that keeps trace context aligned with its envelope."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    envelope: MessageEnvelope[PayloadT]

    @model_validator(mode="after")
    def context_must_match_envelope(self) -> "MessagePublishRequest[PayloadT]":
        if self.context.idempotency_key != self.envelope.idempotency_key:
            raise ValueError("publish context and envelope idempotency keys must match")
        if self.context.correlation_id != self.envelope.correlation_id:
            raise ValueError("publish context and envelope correlation IDs must match")
        return self


class MessagePullRequest(ContractModel):
    """Bounded pull request suitable for queues and event streams."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    consumer_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    event_names: list[str] = Field(min_length=1, max_length=100)
    max_messages: int = Field(default=10, ge=1, le=500)
    wait_seconds: float = Field(default=1, ge=0, le=60)


class ReceivedMessage[PayloadT: ContractModel](ContractModel):
    """Envelope plus the opaque token required for broker acknowledgement."""

    envelope: MessageEnvelope[PayloadT]
    acknowledgement_token: SecretStr = Field(min_length=1, max_length=4096)
    delivery_attempt: int = Field(default=1, ge=1)
    received_at: AwareDatetime


class MessageBatch[PayloadT: ContractModel](ContractModel):
    """A broker-neutral, bounded batch of received messages."""

    schema_version: Literal["1.0"] = "1.0"
    messages: list[ReceivedMessage[PayloadT]] = Field(default_factory=list, max_length=500)
    polled_at: AwareDatetime


class DeadLetterAction(StrEnum):
    """Explicit consumer disposition; retryability is never represented by a bare bool."""

    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    DISCARD = "discard"


class MessageAcknowledgeRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    consumer_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    message_id: UUID
    acknowledgement_token: SecretStr = Field(min_length=1, max_length=4096)


class MessageRejectRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    consumer_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    message_id: UUID
    acknowledgement_token: SecretStr = Field(min_length=1, max_length=4096)
    action: DeadLetterAction
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)

    @model_validator(mode="after")
    def retry_delay_must_match_action(self) -> "MessageRejectRequest":
        if self.action is DeadLetterAction.RETRY and self.retry_after_seconds is None:
            raise ValueError("retry_after_seconds is required for a retry disposition")
        if self.action is not DeadLetterAction.RETRY and self.retry_after_seconds is not None:
            raise ValueError("retry_after_seconds is only valid for a retry disposition")
        return self


class MessageDispositionStatus(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    DISCARDED = "discarded"


class MessageDispositionResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    message_id: UUID
    status: MessageDispositionStatus
    processed_at: AwareDatetime
    idempotent_replay: bool = False
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def reason_must_match_status(self) -> "MessageDispositionResult":
        if self.status is MessageDispositionStatus.ACKNOWLEDGED and self.reason_code is not None:
            raise ValueError("acknowledged messages cannot carry a rejection reason")
        if self.status is not MessageDispositionStatus.ACKNOWLEDGED and self.reason_code is None:
            raise ValueError("non-acknowledged dispositions require reason_code")
        return self


class DeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    SUPPRESSED = "suppressed"
    RETRY_SCHEDULED = "retry_scheduled"
    PERMANENT_FAILURE = "permanent_failure"
    REJECTED = "rejected"


class DeliveryReceipt(ContractModel):
    """Stable acknowledgement returned by brokers and notification adapters."""

    message_id: UUID
    status: DeliveryStatus
    accepted_at: AwareDatetime
    delivered_at: AwareDatetime | None = None
    provider_message_id: str | None = Field(default=None, max_length=500)
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def state_fields_must_be_consistent(self) -> "DeliveryReceipt":
        if self.delivered_at is not None and self.delivered_at < self.accepted_at:
            raise ValueError("delivered_at cannot be earlier than accepted_at")
        if self.status is DeliveryStatus.DELIVERED and self.delivered_at is None:
            raise ValueError("delivered_at is required for delivered messages")
        if self.status is DeliveryStatus.RETRY_SCHEDULED and self.retry_after_seconds is None:
            raise ValueError("retry_after_seconds is required when retry is scheduled")
        reason_required = {
            DeliveryStatus.SUPPRESSED,
            DeliveryStatus.PERMANENT_FAILURE,
            DeliveryStatus.REJECTED,
        }
        if self.status in reason_required and self.reason_code is None:
            raise ValueError("reason_code is required for suppressed or rejected delivery")
        return self
