"""Operational, authorization, audit and redaction contracts."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, JsonValue, SecretBytes, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import (
    ComponentHealth,
    EntityRef,
    HealthStatus,
    MutationContext,
    OperationContext,
)


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    BLOCKED_RIGHTS = "blocked_rights"


class HealthResponse(ContractModel):
    """Minimal load-balancer-safe health response; the only unwrapped HTTP DTO."""

    status: Literal["ok", "degraded"]
    service: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    timestamp: AwareDatetime


class RuntimeCapabilities(ContractModel):
    """Public feature flags; safety-critical trading remains hard-disabled."""

    local_model: str = Field(min_length=1, max_length=200)
    external_llm_enabled: bool
    social_source_enabled: bool
    directional_forecast_enabled: bool
    trading_enabled: Literal[False] = False


class CapabilityDescriptor(ContractModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    display_name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    status: CapabilityStatus
    required_dependencies: list[str] = Field(default_factory=list, max_length=100)
    safe_detail: str | None = Field(default=None, max_length=500)


class ServiceInfo(ContractModel):
    service_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    service_version: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(default="1.0", pattern=r"^1\.[0-9]+$")
    environment: str = Field(min_length=1, max_length=100)
    started_at: AwareDatetime
    capabilities: list[CapabilityDescriptor] = Field(default_factory=list, max_length=500)


class HealthReport(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    service: ServiceInfo
    status: HealthStatus
    checked_at: AwareDatetime
    components: list[ComponentHealth] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def component_names_must_be_unique(self) -> "HealthReport":
        names = [component.component for component in self.components]
        if len(set(names)) != len(names):
            raise ValueError("health component names must be unique")
        return self


class AuthorizationRequest(ContractModel):
    context: OperationContext
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    resource: EntityRef
    required_scopes: list[str] = Field(default_factory=list, max_length=100)


class AuthorizationDecision(ContractModel):
    allowed: bool
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    decided_at: AwareDatetime
    policy_version: str = Field(min_length=1, max_length=200)
    granted_scopes: list[str] = Field(default_factory=list, max_length=100)


class AuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


class AuditAttribute(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: JsonValue


class AuditRecord(ContractModel):
    """Secret-free audit record; adapters must keep unsafe details in protected logs."""

    audit_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime
    actor_id: str = Field(min_length=1, max_length=200)
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    resource: EntityRef
    outcome: AuditOutcome
    correlation_id: UUID
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    attributes: list[AuditAttribute] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def audit_attributes_must_be_unique(self) -> "AuditRecord":
        if len({item.name for item in self.attributes}) != len(self.attributes):
            raise ValueError("audit attribute names must be unique")
        return self


class AuditWriteRequest(ContractModel):
    """Idempotent audit append with trace identity bound to the record."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    record: AuditRecord

    @model_validator(mode="after")
    def trace_identity_must_match(self) -> "AuditWriteRequest":
        if self.context.correlation_id != self.record.correlation_id:
            raise ValueError("audit context and record correlation IDs must match")
        if self.context.actor_id != self.record.actor_id:
            raise ValueError("audit context and record actor IDs must match")
        return self


class AuditWriteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    audit_id: UUID
    stored_at: AwareDatetime
    idempotent_replay: bool = False


class SecretRef(ContractModel):
    secret_id: str = Field(pattern=r"^[a-z][a-z0-9_./-]{1,199}$")
    version: str | None = Field(default=None, max_length=200)


class SecretMaterial(ContractModel):
    """Short-lived secret bytes; callers must never serialize or log this model."""

    reference: SecretRef
    value: SecretBytes
    loaded_at: AwareDatetime
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def expiry_must_follow_load(self) -> "SecretMaterial":
        if not self.value.get_secret_value():
            raise ValueError("secret material cannot be empty")
        if self.expires_at is not None and self.expires_at <= self.loaded_at:
            raise ValueError("expires_at must be later than loaded_at")
        return self


class RedactionRequest(ContractModel):
    context: OperationContext
    text: str = Field(min_length=1, max_length=200_000)
    policy_id: str = Field(min_length=1, max_length=200)


class RedactionResult(ContractModel):
    redacted_text: str = Field(min_length=1, max_length=200_000)
    original_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(min_length=1, max_length=200)
    replacements: int = Field(ge=0)
