from __future__ import annotations

import base64
import hashlib
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
    StoredObjectRef,
)
from stock_assistant.platform.object_store.s3_store import S3ObjectStore

SESSION_ID = "10000000-0000-4000-8000-000000000001"
REPORT_ID = "20000000-0000-4000-8000-000000000002"


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._buffer = BytesIO(payload)

    def read(self, amount: int = -1) -> bytes:
        return self._buffer.read(amount)

    def close(self) -> None:
        self._buffer.close()


class _Client:
    def __init__(self, *, version_id: str | None = "v1") -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.last_delete: dict[str, object] | None = None
        self.last_get: dict[str, object] | None = None
        self.last_head: dict[str, object] | None = None
        self.version_id = version_id

    def put_object(self, **kwargs: object) -> dict[str, object]:
        object_name = str(kwargs["Key"])
        payload = kwargs["Body"]
        metadata = kwargs["Metadata"]
        assert isinstance(payload, bytes)
        assert isinstance(metadata, dict)
        assert kwargs["Bucket"] == "stockmarketagent-r2-artifacts"
        assert kwargs["ServerSideEncryption"] == "AES256"
        assert kwargs["ChecksumAlgorithm"] == "SHA256"
        self.objects[object_name] = (payload, metadata)
        response: dict[str, object] = {"ChecksumSHA256": kwargs["ChecksumSHA256"]}
        if self.version_id is not None:
            response["VersionId"] = self.version_id
        return response

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.last_get = kwargs
        payload, _ = self.objects[str(kwargs["Key"])]
        return {"Body": _Body(payload)}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.last_head = kwargs
        payload, metadata = self.objects[str(kwargs["Key"])]
        return {
            "ContentLength": len(payload),
            "Metadata": metadata,
            "ServerSideEncryption": "AES256",
        }

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.last_delete = kwargs
        self.objects.pop(str(kwargs["Key"]), None)
        return {}


async def _chunks(payload: bytes):
    yield payload[:5]
    yield payload[5:]


def _contexts(now: datetime) -> tuple[MutationContext, OperationContext]:
    return (
        MutationContext(
            schema_version="1.0",
            as_of=now,
            requested_at=now,
            idempotency_key="s3-research-artifact-0001",
        ),
        OperationContext(schema_version="1.0", as_of=now, requested_at=now),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reader_sha", ["a" * 40, "b" * 40])
async def test_s3_adapter_scopes_checksums_streams_and_deletes(reader_sha: str) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    payload = b'{"synthetic":true}'
    digest = hashlib.sha256(payload).hexdigest()
    mutation, operation = _contexts(now)
    client = _Client()
    store = S3ObjectStore(
        client,
        bucket="stockmarketagent-r2-artifacts",
        product_sha="a" * 40,
    )
    reference = await store.put(
        ObjectWriteRequest(
            schema_version="1.0",
            context=mutation,
            namespace="research_reports",
            suggested_key=f"research/{SESSION_ID}/{REPORT_ID}.json",
            media_type="application/json",
            content_length=len(payload),
            content_sha256=digest,
            retention_class=RetentionClass.SHORT_TERM,
            retention_until=now + timedelta(days=7),
        ),
        _chunks(payload),
    )

    expected_checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    assert reference.object_key.startswith(f"releases/{'a' * 40}/research/")
    assert reference.version_id == "v1"
    assert client.objects[reference.object_key][1]["content-sha256"] == digest
    assert expected_checksum

    store = S3ObjectStore(client, bucket="stockmarketagent-r2-artifacts", product_sha=reader_sha)
    stream = store.stream(
        ObjectReadRequest(schema_version="1.0", context=operation, reference=reference)
    )
    assert b"".join([chunk async for chunk in stream]) == payload
    assert (
        await store.exists(
            ObjectExistsRequest(schema_version="1.0", context=operation, reference=reference)
        )
    ).exists
    for arguments in (client.last_get, client.last_head):
        assert arguments is not None
        assert arguments["Bucket"] == "stockmarketagent-r2-artifacts"
        assert arguments["Key"] == reference.object_key
        assert arguments["VersionId"] == "v1"
    assert (
        await store.delete(
            ObjectDeleteRequest(schema_version="1.0", context=mutation, reference=reference)
        )
    ).deleted
    assert client.last_delete == {
        "Bucket": "stockmarketagent-r2-artifacts",
        "Key": reference.object_key,
        "VersionId": "v1",
    }
    assert client.objects == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("namespace", "object_key"),
    [
        ("other_reports", f"releases/{'a' * 40}/research/{SESSION_ID}/{REPORT_ID}.json"),
        ("research_reports", f"s3://other-bucket/research/{SESSION_ID}/{REPORT_ID}.json"),
        ("research_reports", f"releases/{'a' * 40}/private/report.json"),
        ("research_reports", f"releases/{'a' * 39}/research/{SESSION_ID}/{REPORT_ID}.json"),
        ("research_reports", f"releases/{'a' * 40}/research/{'-' * 36}/{REPORT_ID}.json"),
        ("research_reports", f"releases/{'a' * 40}/research/../{REPORT_ID}.json"),
        ("research_reports", f"releases/{'a' * 40}/research/{SESSION_ID}/{REPORT_ID}.json/extra"),
    ],
)
async def test_s3_adapter_rejects_invalid_persisted_paths_before_io(
    namespace: str, object_key: str
) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    mutation, operation = _contexts(now)
    client = _Client()
    store = S3ObjectStore(client, bucket="stockmarketagent-r2-artifacts", product_sha="a" * 40)
    reference = StoredObjectRef(
        schema_version="1.0",
        namespace=namespace,
        object_key=object_key,
        version_id="v1",
        media_type="application/json",
        content_length=2,
        content_sha256=hashlib.sha256(b"{}").hexdigest(),
        stored_at=now,
        retention_class=RetentionClass.SHORT_TERM,
        retention_until=now + timedelta(days=7),
    )
    with pytest.raises(ValueError, match=r"OBJECT_(KEY|NAMESPACE)_INVALID"):
        async for _ in store.stream(
            ObjectReadRequest(schema_version="1.0", context=operation, reference=reference)
        ):
            pytest.fail("an invalid reference reached the object body")
    with pytest.raises(ValueError, match=r"OBJECT_(KEY|NAMESPACE)_INVALID"):
        await store.exists(
            ObjectExistsRequest(schema_version="1.0", context=operation, reference=reference)
        )
    with pytest.raises(ValueError, match=r"OBJECT_(KEY|NAMESPACE)_INVALID"):
        await store.delete(
            ObjectDeleteRequest(schema_version="1.0", context=mutation, reference=reference)
        )
    assert client.last_get is None and client.last_head is None and client.last_delete is None


@pytest.mark.asyncio
async def test_s3_adapter_rejects_unscoped_key_before_write() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    mutation, _ = _contexts(now)
    client = _Client()
    store = S3ObjectStore(
        client,
        bucket="stockmarketagent-r2-artifacts",
        product_sha="b" * 40,
    )

    with pytest.raises(ValueError, match="OBJECT_KEY_INVALID"):
        await store.put(
            ObjectWriteRequest(
                schema_version="1.0",
                context=mutation,
                namespace="research_reports",
                suggested_key="unscoped/report.json",
                media_type="application/json",
                content_length=2,
                content_sha256=hashlib.sha256(b"{}").hexdigest(),
                retention_class=RetentionClass.SHORT_TERM,
                retention_until=now + timedelta(days=7),
            ),
            _chunks(b"{}"),
        )
    assert client.objects == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("version_id", [None, "", "   "])
async def test_s3_adapter_requires_a_confirmed_version_id(version_id: str | None) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    payload = b'{"synthetic":true}'
    mutation, _ = _contexts(now)
    client = _Client(version_id=version_id)
    store = S3ObjectStore(
        client,
        bucket="stockmarketagent-r2-artifacts",
        product_sha="c" * 40,
    )

    with pytest.raises(OSError, match="S3_VERSION_NOT_CONFIRMED"):
        await store.put(
            ObjectWriteRequest(
                schema_version="1.0",
                context=mutation,
                namespace="research_reports",
                suggested_key=f"research/{SESSION_ID}/{REPORT_ID}.json",
                media_type="application/json",
                content_length=len(payload),
                content_sha256=hashlib.sha256(payload).hexdigest(),
                retention_class=RetentionClass.SHORT_TERM,
                retention_until=now + timedelta(days=7),
            ),
            _chunks(payload),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("version_id", [None, "", "   "])
async def test_s3_adapter_refuses_reads_and_delete_without_an_exact_version(
    version_id: str | None,
) -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    payload = b'{"synthetic":true}'
    mutation, operation = _contexts(now)
    client = _Client()
    store = S3ObjectStore(
        client,
        bucket="stockmarketagent-r2-artifacts",
        product_sha="d" * 40,
    )
    reference = await store.put(
        ObjectWriteRequest(
            schema_version="1.0",
            context=mutation,
            namespace="research_reports",
            suggested_key=f"research/{SESSION_ID}/{REPORT_ID}.json",
            media_type="application/json",
            content_length=len(payload),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            retention_class=RetentionClass.SHORT_TERM,
            retention_until=now + timedelta(days=7),
        ),
        _chunks(payload),
    )
    invalid_reference = reference.model_copy(update={"version_id": version_id})

    with pytest.raises(ValueError, match="S3_VERSION_REQUIRED_FOR_READ"):
        async for _ in store.stream(
            ObjectReadRequest(schema_version="1.0", context=operation, reference=invalid_reference)
        ):
            pytest.fail("an unversioned reference reached the object body")
    with pytest.raises(ValueError, match="S3_VERSION_REQUIRED_FOR_READ"):
        await store.exists(
            ObjectExistsRequest(
                schema_version="1.0", context=operation, reference=invalid_reference
            )
        )
    with pytest.raises(ValueError, match="S3_VERSION_REQUIRED_FOR_DELETE"):
        await store.delete(
            ObjectDeleteRequest(
                schema_version="1.0",
                context=mutation,
                reference=invalid_reference,
            )
        )
    assert client.last_get is None and client.last_head is None and client.last_delete is None
