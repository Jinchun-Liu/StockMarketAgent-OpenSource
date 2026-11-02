"""Typed MinIO/S3 adapter for bounded research report artifacts."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterable, AsyncIterator
from io import BytesIO
from typing import BinaryIO, Protocol, cast

from minio.error import S3Error

from stock_assistant.contracts.storage import (
    ObjectDeleteRequest,
    ObjectDeleteResult,
    ObjectExistsRequest,
    ObjectExistsResult,
    ObjectReadRequest,
    ObjectWriteRequest,
    StoredObjectRef,
)

_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


class _PutResult(Protocol):
    version_id: str | None


class _ReadResponse(Protocol):
    def read(self, amount: int = ...) -> bytes: ...

    def close(self) -> None: ...

    def release_conn(self) -> None: ...


class _MinioClient(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...

    def make_bucket(self, bucket_name: str) -> None: ...

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str,
        metadata: dict[str, str],
    ) -> _PutResult: ...

    def get_object(self, bucket_name: str, object_name: str) -> _ReadResponse: ...

    def stat_object(self, bucket_name: str, object_name: str) -> object: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...


class MinioObjectStore:
    """ObjectStorePort implementation with exact length and digest validation."""

    def __init__(self, client: object, *, bucket: str) -> None:
        self._client = cast("_MinioClient", client)
        self._bucket = bucket

    async def _ensure_bucket(self) -> None:
        exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        if not exists:
            await asyncio.to_thread(self._client.make_bucket, self._bucket)

    async def put(
        self,
        request: ObjectWriteRequest,
        content: AsyncIterable[bytes],
    ) -> StoredObjectRef:
        if request.content_length > _MAX_ARTIFACT_BYTES:
            raise ValueError("OBJECT_TOO_LARGE")
        if request.suggested_key is None or ".." in request.suggested_key:
            raise ValueError("OBJECT_KEY_INVALID")
        parts: list[bytes] = []
        observed = 0
        async for chunk in content:
            observed += len(chunk)
            if observed > request.content_length or observed > _MAX_ARTIFACT_BYTES:
                raise ValueError("OBJECT_LENGTH_MISMATCH")
            parts.append(chunk)
        payload = b"".join(parts)
        if observed != request.content_length:
            raise ValueError("OBJECT_LENGTH_MISMATCH")
        if hashlib.sha256(payload).hexdigest() != request.content_sha256:
            raise ValueError("OBJECT_DIGEST_MISMATCH")
        await self._ensure_bucket()
        result = await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            request.suggested_key,
            BytesIO(payload),
            len(payload),
            request.media_type,
            {item.name: item.value for item in request.attributes},
        )
        return StoredObjectRef(
            schema_version="1.0",
            namespace=request.namespace,
            object_key=request.suggested_key,
            version_id=result.version_id,
            media_type=request.media_type,
            content_length=request.content_length,
            content_sha256=request.content_sha256,
            stored_at=request.context.requested_at,
            retention_class=request.retention_class,
            retention_until=request.retention_until,
            rights_snapshot_hash=request.rights_snapshot_hash,
        )

    async def stream(self, request: ObjectReadRequest) -> AsyncIterator[bytes]:
        response = await asyncio.to_thread(
            self._client.get_object,
            self._bucket,
            request.reference.object_key,
        )
        try:
            while True:
                chunk = await asyncio.to_thread(response.read, 64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)

    async def delete(self, request: ObjectDeleteRequest) -> ObjectDeleteResult:
        await asyncio.to_thread(
            self._client.remove_object,
            self._bucket,
            request.reference.object_key,
        )
        return ObjectDeleteResult(
            schema_version="1.0",
            object_id=request.reference.object_id,
            deleted=True,
            processed_at=request.context.requested_at,
        )

    async def exists(self, request: ObjectExistsRequest) -> ObjectExistsResult:
        try:
            await asyncio.to_thread(
                self._client.stat_object,
                self._bucket,
                request.reference.object_key,
            )
            exists = True
        except (OSError, S3Error):
            exists = False
        return ObjectExistsResult(
            schema_version="1.0",
            object_id=request.reference.object_id,
            exists=exists,
            checked_at=request.context.requested_at,
        )
