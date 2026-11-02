"""Realtime notification wire contract."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext


class NotificationChangeType(StrEnum):
    NEW = "new"
    UPDATE = "update"
    CORRECTION = "correction"
    CONTRADICTION = "contradiction"


class NotificationSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Notification(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    notification_id: UUID = Field(default_factory=uuid4)
    event_cluster_id: UUID
    event_revision: int = Field(ge=1)
    change_type: NotificationChangeType
    severity: NotificationSeverity
    title: str = Field(min_length=1, max_length=300)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    created_at: AwareDatetime
    idempotency_key: str = Field(min_length=32, max_length=200)


class NotificationDeliveryRequest(ContractModel):
    """Channel-neutral delivery command with mutation context and safe destination ID."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    notification: Notification
    channel: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    destination_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def delivery_and_notification_must_share_idempotency(self) -> "NotificationDeliveryRequest":
        if self.context.idempotency_key != self.notification.idempotency_key:
            raise ValueError("delivery context must use the notification idempotency_key")
        return self
