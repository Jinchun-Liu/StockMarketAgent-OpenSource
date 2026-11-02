from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest

from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.storage import (
    ObjectDeleteRequest,
    ObjectExistsRequest,
    ObjectReadRequest,
    ObjectWriteRequest,
    RetentionClass,
)
from stock_assistant.platform.object_store.minio_store import MinioObjectStore


@dataclass
class _PutResult:
    version_id: str | None = "synthetic-version-1"


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._buffer = BytesIO(payload)

    def read(self, amount: int = -1) -> bytes:
        return self._buffer.read(amount)

    def close(self) -> None:
        self._buffer.close()

    def release_conn(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.bucket = False
        self.objects: dict[str, bytes] = {}

    def bucket_exists(self, bucket_name: str) -> bool:
        assert bucket_name == "reports"
        return self.bucket

    def make_bucket(self, bucket_name: str) -> None:
        assert bucket_name == "reports"
        self.bucket = True

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BytesIO,
        length: int,
        content_type: str,
        metadata: dict[str, str],
    ) -> _PutResult:
        assert bucket_name == "reports"
        assert content_type == "application/json"
        assert metadata == {}
        payload = data.read()
        assert len(payload) == length
        self.objects[object_name] = payload
        return _PutResult()

    def get_object(self, bucket_name: str, object_name: str) -> _Response:
        assert bucket_name == "reports"
        return _Response(self.objects[object_name])

    def stat_object(self, bucket_name: str, object_name: str) -> object:
        assert bucket_name == "reports"
        if object_name not in self.objects:
            raise OSError("missing")
        return object()

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        assert bucket_name == "reports"
        self.objects.pop(object_name, None)


async def _chunks(payload: bytes):
    midpoint = len(payload) // 2
    yield payload[:midpoint]
    yield payload[midpoint:]


def _contexts(now: datetime) -> tuple[MutationContext, OperationContext]:
    mutation = MutationContext(
        schema_version="1.0",
        as_of=now,
        requested_at=now,
        idempotency_key="research-object-store-0001",
    )
    operation = OperationContext(
        schema_version="1.0",
        as_of=now,
        requested_at=now,
    )
    return mutation, operation


@pytest.mark.asyncio
async def test_minio_adapter_checks_digest_streams_and_deletes() -> None:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    payload = b'{"synthetic":true}'
    mutation, operation = _contexts(now)
    client = _Client()
    store = MinioObjectStore(client, bucket="reports")
    reference = await store.put(
        ObjectWriteRequest(
            schema_version="1.0",
            context=mutation,
            namespace="research_reports",
            suggested_key="research/session/report.json",
            media_type="application/json",
            content_length=len(payload),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            retention_class=RetentionClass.SHORT_TERM,
            retention_until=now + timedelta(days=7),
        ),
        _chunks(payload),
    )
    streamed = store.stream(
        ObjectReadRequest(
            schema_version="1.0",
            context=operation,
            reference=reference,
        )
    )
    assert b"".join([chunk async for chunk in streamed]) == payload
    assert (
        await store.exists(
            ObjectExistsRequest(
                schema_version="1.0",
                context=operation,
                reference=reference,
            )
        )
    ).exists is True
    deleted = await store.delete(
        ObjectDeleteRequest(
            schema_version="1.0",
            context=mutation,
            reference=reference,
        )
    )
    assert deleted.deleted is True
    assert (
        await store.exists(
            ObjectExistsRequest(
                schema_version="1.0",
                context=operation,
                reference=reference,
            )
        )
    ).exists is False


@pytest.mark.asyncio
async def test_minio_adapter_rejects_wrong_digest_without_writing() -> None:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    payload = b"synthetic"
    mutation, _ = _contexts(now)
    client = _Client()
    store = MinioObjectStore(client, bucket="reports")

    with pytest.raises(ValueError, match="OBJECT_DIGEST_MISMATCH"):
        await store.put(
            ObjectWriteRequest(
                schema_version="1.0",
                context=mutation,
                namespace="research_reports",
                suggested_key="research/session/report.json",
                media_type="application/json",
                content_length=len(payload),
                content_sha256="0" * 64,
                retention_class=RetentionClass.SHORT_TERM,
                retention_until=now + timedelta(days=7),
            ),
            _chunks(payload),
        )
    assert client.objects == {}
