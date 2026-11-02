"""Stable primitives shared by every process and adapter boundary."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString


class HealthStatus(StrEnum):
    """Portable component health states."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    BLOCKED_RIGHTS = "blocked_rights"


class OperationContext(ContractModel):
    """Trace, identity and point-in-time context for one boundary operation."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    actor_id: str = Field(default="local-user", min_length=1, max_length=200)
    workspace_id: str = Field(default="default", min_length=1, max_length=200)
    as_of: AwareDatetime
    requested_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline_at: AwareDatetime | None = None
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=200)
    locale: str = Field(default="zh-CN", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)

    @model_validator(mode="after")
    def operation_context_must_be_consistent(self) -> "OperationContext":
        if "correlation_id" not in self.model_fields_set:
            object.__setattr__(self, "correlation_id", self.request_id)
        if self.deadline_at is not None and self.deadline_at <= self.requested_at:
            raise ValueError("deadline_at must be later than requested_at")
        return self


class MutationContext(OperationContext):
    """Operation context for a state change, requiring a stable idempotency key."""

    idempotency_key: str = Field(min_length=16, max_length=200)


class CursorPage[ItemT](ContractModel):
    """Opaque-cursor page that remains stable while records are appended."""

    schema_version: Literal["1.0"] = "1.0"
    items: list[ItemT]
    next_cursor: str | None = Field(default=None, max_length=2048)
    has_more: bool = False
    as_of: AwareDatetime

    @model_validator(mode="after")
    def continuation_requires_cursor(self) -> "CursorPage[ItemT]":
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        return self


class EntityRef(ContractModel):
    """Namespaced reference that never relies on a display name as identity."""

    entity_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    entity_id: str = Field(min_length=1, max_length=200)


class InstrumentRef(ContractModel):
    """Market-neutral security identity suitable for future exchanges."""

    instrument_id: str = Field(pattern=r"^[A-Z0-9_]+:[A-Za-z0-9._-]+$")
    symbol: str = Field(min_length=1, max_length=64)
    exchange: str = Field(pattern=r"^[A-Z0-9_]{2,32}$")
    market: str = Field(pattern=r"^[A-Z0-9_]{2,32}$")
    asset_class: str = Field(default="equity", pattern=r"^[a-z][a-z0-9_]{1,31}$")
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")


class MoneyValue(ContractModel):
    """Exact monetary value; Decimal serializes as a JSON string."""

    amount: DecimalString
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ComponentHealth(ContractModel):
    """Framework-independent health result."""

    component: str = Field(min_length=1, max_length=200)
    status: HealthStatus
    checked_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    latency_ms: int | None = Field(default=None, ge=0)
    last_success_at: AwareDatetime | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    safe_detail: str | None = Field(default=None, max_length=500)
