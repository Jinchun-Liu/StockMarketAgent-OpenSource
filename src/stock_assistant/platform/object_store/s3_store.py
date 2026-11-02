"""AWS S3 ObjectStorePort adapter for private short-lived research artifacts."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import AsyncIterable, AsyncIterator
from typing import Final, Protocol, cast

from botocore.exceptions import ClientError

from stock_assistant.contracts.storage import (
    ObjectDeleteRequest,
    ObjectDeleteResult,
    ObjectExistsRequest,
    ObjectExistsResult,
    ObjectReadRequest,
    ObjectWriteRequest,
    StoredObjectRef,
)

_MAX_ARTIFACT_BYTES: Final = 10 * 1024 * 1024
_UUID_PATH: Final = r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}"
_SAFE_KEY: Final = re.compile(rf"research/{_UUID_PATH}/{_UUID_PATH}\.json")
_STORED_KEY: Final = re.compile(rf"releases/[0-9a-f]{{40}}/{_SAFE_KEY.pattern}")


class _StreamingBody(Protocol):
    def read(self, amount: int = ...) -> bytes: ...

    def close(self) -> None: ...


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> dict[str, object]: ...

    def get_object(self, **kwargs: object) -> dict[str, object]: ...

    def head_object(self, **kwargs: object) -> dict[str, object]: ...

    def delete_object(self, **kwargs: object) -> dict[str, object]: ...


class S3ObjectStore:
    """Write under this release; retain exact references to prior research artifacts."""

    def __init__(self, client: object, *, bucket: str, product_sha: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{40}", product_sha):
            raise ValueError("PRODUCT_SHA_INVALID")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("S3_BUCKET_INVALID")
        self._client = cast("_S3Client", client)
        self._bucket = bucket
        self._prefix = f"releases/{product_sha}/"

    def _key(self, suggested_key: str | None) -> str:
        if suggested_key is None or not _SAFE_KEY.fullmatch(suggested_key):
            raise ValueError("OBJECT_KEY_INVALID")
        return f"{self._prefix}{suggested_key}"

    def _assert_reference(self, reference: StoredObjectRef) -> None:
        if reference.namespace != "research_reports":
            raise ValueError("OBJECT_NAMESPACE_INVALID")
        if _STORED_KEY.fullmatch(reference.object_key) is None:
            raise ValueError("OBJECT_KEY_INVALID")

    def _read_arguments(self, reference: StoredObjectRef) -> dict[str, object]:
        self._assert_reference(reference)
        if reference.version_id is None or not reference.version_id.strip():
            raise ValueError("S3_VERSION_REQUIRED_FOR_READ")
        return {
            "Bucket": self._bucket,
            "Key": reference.object_key,
            "VersionId": reference.version_id,
        }

    async def put(
        self,
        request: ObjectWriteRequest,
        content: AsyncIterable[bytes],
    ) -> StoredObjectRef:
        if request.namespace != "research_reports":
            raise ValueError("OBJECT_NAMESPACE_INVALID")
        if request.content_length > _MAX_ARTIFACT_BYTES:
            raise ValueError("OBJECT_TOO_LARGE")
        key = self._key(request.suggested_key)
        parts: list[bytes] = []
        observed = 0
        async for chunk in content:
            observed += len(chunk)
            if observed > request.content_length or observed > _MAX_ARTIFACT_BYTES:
                raise ValueError("OBJECT_LENGTH_MISMATCH")
            parts.append(chunk)
        payload = b"".join(parts)
        digest = hashlib.sha256(payload).hexdigest()
        if observed != request.content_length:
            raise ValueError("OBJECT_LENGTH_MISMATCH")
        if digest != request.content_sha256:
            raise ValueError("OBJECT_DIGEST_MISMATCH")
        checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
        metadata = {
            "content-sha256": digest,
            "retention-until": (
                request.retention_until.isoformat() if request.retention_until else "indefinite"
            ),
        }
        for attribute in request.attributes:
            metadata[f"attr-{attribute.name}"] = attribute.value
        response = await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=payload,
            ContentLength=len(payload),
            ContentType=request.media_type,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=checksum,
            ServerSideEncryption="AES256",
            Metadata=metadata,
        )
        if response.get("ChecksumSHA256") != checksum:
            raise OSError("S3_CHECKSUM_NOT_CONFIRMED")
        version_id = response.get("VersionId")
        if not isinstance(version_id, str) or not version_id.strip():
            raise OSError("S3_VERSION_NOT_CONFIRMED")
        return StoredObjectRef(
            schema_version="1.0",
            namespace=request.namespace,
            object_key=key,
            version_id=version_id,
            media_type=request.media_type,
            content_length=request.content_length,
            content_sha256=request.content_sha256,
            stored_at=request.context.requested_at,
            retention_class=request.retention_class,
            retention_until=request.retention_until,
            rights_snapshot_hash=request.rights_snapshot_hash,
        )

    async def stream(self, request: ObjectReadRequest) -> AsyncIterator[bytes]:
        arguments = self._read_arguments(request.reference)
        arguments["ChecksumMode"] = "ENABLED"
        response = await asyncio.to_thread(self._client.get_object, **arguments)
        body = response.get("Body")
        if body is None:
            raise OSError("S3_OBJECT_BODY_MISSING")
        stream = cast("_StreamingBody", body)
        hasher = hashlib.sha256()
        observed = 0
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 64 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                hasher.update(chunk)
                if observed > request.reference.content_length:
                    raise OSError("S3_OBJECT_LENGTH_MISMATCH")
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)
        if observed != request.reference.content_length:
            raise OSError("S3_OBJECT_LENGTH_MISMATCH")
        if hasher.hexdigest() != request.reference.content_sha256:
            raise OSError("S3_OBJECT_DIGEST_MISMATCH")

    async def delete(self, request: ObjectDeleteRequest) -> ObjectDeleteResult:
        self._assert_reference(request.reference)
        version_id = request.reference.version_id
        if version_id is None or not version_id.strip():
            raise ValueError("S3_VERSION_REQUIRED_FOR_DELETE")
        arguments: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": request.reference.object_key,
            "VersionId": version_id,
        }
        await asyncio.to_thread(self._client.delete_object, **arguments)
        return ObjectDeleteResult(
            schema_version="1.0",
            object_id=request.reference.object_id,
            deleted=True,
            processed_at=request.context.requested_at,
        )

    async def exists(self, request: ObjectExistsRequest) -> ObjectExistsResult:
        arguments = self._read_arguments(request.reference)
        try:
            response = await asyncio.to_thread(self._client.head_object, **arguments)
            metadata = response.get("Metadata")
            exists = (
                response.get("ServerSideEncryption") == "AES256"
                and isinstance(metadata, dict)
                and metadata.get("content-sha256") == request.reference.content_sha256
                and response.get("ContentLength") == request.reference.content_length
            )
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in {403, 404}:
                raise
            exists = False
        return ObjectExistsResult(
            schema_version="1.0",
            object_id=request.reference.object_id,
            exists=exists,
            checked_at=request.context.requested_at,
        )
