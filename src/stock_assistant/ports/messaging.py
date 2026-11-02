"""Messaging and realtime-notification ports."""

from typing import Protocol, TypeVar, runtime_checkable

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.messaging import (
    DeliveryReceipt,
    MessageAcknowledgeRequest,
    MessageBatch,
    MessageDispositionResult,
    MessagePublishRequest,
    MessagePullRequest,
    MessageRejectRequest,
)
from stock_assistant.contracts.notifications import NotificationDeliveryRequest

PayloadT = TypeVar("PayloadT", bound=ContractModel)


@runtime_checkable
class MessagePublisherPort(Protocol[PayloadT]):
    async def publish(self, request: MessagePublishRequest[PayloadT]) -> DeliveryReceipt: ...


@runtime_checkable
class MessageConsumerPort(Protocol[PayloadT]):
    async def pull(self, request: MessagePullRequest) -> MessageBatch[PayloadT]: ...

    async def acknowledge(self, request: MessageAcknowledgeRequest) -> MessageDispositionResult: ...

    async def reject(self, request: MessageRejectRequest) -> MessageDispositionResult: ...


@runtime_checkable
class NotificationDeliveryPort(Protocol):
    """Deliver to a configured destination without exposing its address."""

    async def deliver(self, request: NotificationDeliveryRequest) -> DeliveryReceipt: ...
