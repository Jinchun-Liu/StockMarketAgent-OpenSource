"""Replaceable ports for object storage, caches, locks, clocks and identifiers."""

from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.storage import (
    CacheDeleteRequest,
    CacheDeleteResult,
    CacheGetRequest,
    CacheLookupResult,
    CacheSetRequest,
    CacheWriteResult,
    LockAcquireResult,
    LockLease,
    LockReleaseRequest,
    LockReleaseResult,
    LockRenewRequest,
    LockRequest,
    ObjectDeleteRequest,
    ObjectDeleteResult,
    ObjectExistsRequest,
    ObjectExistsResult,
    ObjectReadRequest,
    ObjectWriteRequest,
    StoredObjectRef,
)

CacheValueT = TypeVar("CacheValueT", bound=ContractModel)


@runtime_checkable
class ObjectStorePort(Protocol):
    """Metadata stays in contracts while binary content uses a typed data plane."""

    async def put(
        self, request: ObjectWriteRequest, content: AsyncIterable[bytes]
    ) -> StoredObjectRef: ...

    def stream(self, request: ObjectReadRequest) -> AsyncIterator[bytes]: ...

    async def delete(self, request: ObjectDeleteRequest) -> ObjectDeleteResult: ...

    async def exists(self, request: ObjectExistsRequest) -> ObjectExistsResult: ...


@runtime_checkable
class CachePort(Protocol[CacheValueT]):
    async def get(self, request: CacheGetRequest) -> CacheLookupResult[CacheValueT]: ...

    async def set(self, request: CacheSetRequest[CacheValueT]) -> CacheWriteResult: ...

    async def delete(self, request: CacheDeleteRequest) -> CacheDeleteResult: ...


@runtime_checkable
class DistributedLockPort(Protocol):
    async def acquire(self, request: LockRequest) -> LockAcquireResult: ...

    async def renew(self, request: LockRenewRequest) -> LockLease: ...

    async def release(self, request: LockReleaseRequest) -> LockReleaseResult: ...


@runtime_checkable
class ClockPort(Protocol):
    """Pure primitive exception: return aware UTC without a request envelope."""

    def now(self) -> datetime: ...


@runtime_checkable
class IdentifierPort(Protocol):
    """Pure primitive exception: allocate an in-process identifier without I/O."""

    def new_uuid(self) -> UUID: ...
