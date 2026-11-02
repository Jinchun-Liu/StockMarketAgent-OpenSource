"""Persistence, object-store, cache and distributed-lock contracts."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext


class RetentionClass(StrEnum):
    TRANSIENT = "transient"
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    AUDIT = "audit"
    LEGAL_HOLD = "legal_hold"


class ObjectAttribute(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: str = Field(min_length=1, max_length=2048)


class ObjectWriteRequest(ContractModel):
    """Metadata for a streamed object write; content is a separate data-plane argument."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    suggested_key: str | None = Field(default=None, max_length=1024)
    media_type: str = Field(min_length=1, max_length=200)
    content_length: int = Field(ge=0)
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    retention_class: RetentionClass
    retention_until: AwareDatetime | None = None
    rights_snapshot_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    attributes: list[ObjectAttribute] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def retention_and_attributes_must_be_valid(self) -> "ObjectWriteRequest":
        if len({item.name for item in self.attributes}) != len(self.attributes):
            raise ValueError("object attribute names must be unique")
        bounded_retention = {
            RetentionClass.TRANSIENT,
            RetentionClass.SHORT_TERM,
            RetentionClass.EPISODIC,
        }
        if self.retention_class in bounded_retention and self.retention_until is None:
            raise ValueError("bounded retention classes require retention_until")
        if self.retention_until is not None and self.retention_until <= self.context.as_of:
            raise ValueError("retention_until must be later than context.as_of")
        if self.retention_class is RetentionClass.LEGAL_HOLD and self.retention_until is not None:
            raise ValueError("legal_hold objects cannot have an automatic retention deadline")
        return self


class StoredObjectRef(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    object_id: UUID = Field(default_factory=uuid4)
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    object_key: str = Field(min_length=1, max_length=1024)
    version_id: str | None = Field(default=None, max_length=500)
    media_type: str = Field(min_length=1, max_length=200)
    content_length: int = Field(ge=0)
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    stored_at: AwareDatetime
    retention_class: RetentionClass
    retention_until: AwareDatetime | None = None
    rights_snapshot_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None

    @model_validator(mode="after")
    def retention_must_be_valid(self) -> "StoredObjectRef":
        bounded_retention = {
            RetentionClass.TRANSIENT,
            RetentionClass.SHORT_TERM,
            RetentionClass.EPISODIC,
        }
        if self.retention_class in bounded_retention and self.retention_until is None:
            raise ValueError("bounded retention classes require retention_until")
        if self.retention_until is not None and self.retention_until <= self.stored_at:
            raise ValueError("retention_until must be later than stored_at")
        if self.retention_class is RetentionClass.LEGAL_HOLD and self.retention_until is not None:
            raise ValueError("legal_hold objects cannot have an automatic retention deadline")
        return self


class ObjectReadRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    reference: StoredObjectRef


class ObjectDeleteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    reference: StoredObjectRef


class ObjectDeleteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    object_id: UUID
    deleted: bool
    processed_at: AwareDatetime
    idempotent_replay: bool = False


class ObjectExistsRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    reference: StoredObjectRef


class ObjectExistsResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    object_id: UUID
    exists: bool
    checked_at: AwareDatetime


class CacheKey(ContractModel):
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    key: str = Field(min_length=1, max_length=1024)


class CacheEntry[CacheValueT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    key: CacheKey
    value: CacheValueT
    created_at: AwareDatetime
    expires_at: AwareDatetime
    etag: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def expiry_must_follow_creation(self) -> "CacheEntry[CacheValueT]":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self


class CacheGetRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    key: CacheKey


class CacheLookupResult[CacheValueT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    hit: bool
    entry: CacheEntry[CacheValueT] | None = None
    checked_at: AwareDatetime

    @model_validator(mode="after")
    def hit_must_match_entry(self) -> "CacheLookupResult[CacheValueT]":
        if self.hit != (self.entry is not None):
            raise ValueError("cache hit must reflect whether an entry is present")
        return self


class CacheSetRequest[CacheValueT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    entry: CacheEntry[CacheValueT]


class CacheWriteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    key: CacheKey
    stored_at: AwareDatetime
    expires_at: AwareDatetime
    etag: str = Field(min_length=1, max_length=200)
    idempotent_replay: bool = False

    @model_validator(mode="after")
    def expiry_must_follow_storage(self) -> "CacheWriteResult":
        if self.expires_at <= self.stored_at:
            raise ValueError("expires_at must be later than stored_at")
        return self


class CacheDeleteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    key: CacheKey


class CacheDeleteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    key: CacheKey
    deleted: bool
    processed_at: AwareDatetime
    idempotent_replay: bool = False


class LockRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    resource_key: str = Field(min_length=1, max_length=1024)
    lease_seconds: int = Field(default=30, ge=1, le=3600)
    wait_seconds: float = Field(default=0, ge=0, le=300)


class LockLease(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    lease_id: UUID = Field(default_factory=uuid4)
    resource_key: str = Field(min_length=1, max_length=1024)
    owner_id: str = Field(min_length=1, max_length=200)
    acquired_at: AwareDatetime
    expires_at: AwareDatetime
    fencing_token: int = Field(ge=1)

    @model_validator(mode="after")
    def expiry_must_follow_acquisition(self) -> "LockLease":
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at must be later than acquired_at")
        return self


class LockAcquireResult(ContractModel):
    """Explicit lock contention outcome without a nullable port return."""

    schema_version: Literal["1.0"] = "1.0"
    acquired: bool
    lease: LockLease | None = None
    processed_at: AwareDatetime
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def acquisition_state_must_be_consistent(self) -> "LockAcquireResult":
        if self.acquired != (self.lease is not None):
            raise ValueError("acquired must reflect whether a lease is present")
        if not self.acquired and self.reason_code is None:
            raise ValueError("a failed lock acquisition requires reason_code")
        if self.lease is not None and self.lease.acquired_at > self.processed_at:
            raise ValueError("processed_at cannot be earlier than lease acquisition")
        return self


class LockRenewRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    lease: LockLease
    lease_seconds: int = Field(ge=1, le=3600)


class LockReleaseRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    lease: LockLease


class LockReleaseResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    lease_id: UUID
    released: bool
    processed_at: AwareDatetime
    idempotent_replay: bool = False
