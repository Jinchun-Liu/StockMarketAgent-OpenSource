"""Versioned S3 snapshot publication with immutable assets and a CAS pointer."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from stock_assistant.contracts.public_snapshots import (
    PublicSnapshotManifestV1_1,
    ValidatedPublicSnapshotBundle,
    validate_public_snapshot_bundle,
)
from stock_assistant.modules.briefings.projection import (
    validate_briefing_evidence_projection,
)
from stock_assistant.platform.publication.public_safety import scan_public_json
from stock_assistant.platform.publication.snapshot_bundle import SnapshotDocuments

_CURRENT_KEY = "data/current.json"
_IMMUTABLE_CACHE = "public,max-age=31536000,immutable"
_CURRENT_CACHE = "no-store,max-age=0,must-revalidate"


class _Body(Protocol):
    def read(self) -> bytes: ...


class S3SnapshotClient(Protocol):
    """Small boto3 S3 seam used by deterministic unit tests."""

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


class SnapshotPublicationError(RuntimeError):
    """The immutable snapshot or pointer switch could not be proven safe."""


class CurrentSnapshotMissing(SnapshotPublicationError):
    """No public pointer currently exists."""


@dataclass(frozen=True, slots=True)
class SnapshotPublicationResult:
    snapshot_id: str
    pointer_switched: bool
    idempotent_winner: bool
    current_etag: str | None
    current_version_id: str | None


@dataclass(frozen=True, slots=True)
class RemoteSnapshot:
    validated: ValidatedPublicSnapshotBundle
    pointer_document: bytes
    manifest_document: bytes
    asset_documents: dict[str, bytes]
    current_etag: str | None
    current_version_id: str | None


class S3SnapshotPublisher:
    """Publish all immutable bytes before conditionally switching ``current.json``."""

    def __init__(self, *, client: S3SnapshotClient, bucket: str) -> None:
        if not bucket:
            raise ValueError("snapshot bucket is required")
        self._client = client
        self._bucket = bucket

    def publish(self, snapshot: SnapshotDocuments) -> SnapshotPublicationResult:
        """Prevalidate, upload, full-readback, then perform one conditional pointer write."""

        validated_local = self._validate_local(snapshot)
        pointer = validated_local.pointer
        snapshot_id = str(pointer.snapshot_id)
        for key, document in sorted(snapshot.asset_documents.items()):
            self._put_immutable(key, document)
        self._put_immutable(pointer.manifest_path, snapshot.manifest_document)

        remote_assets = {key: self._get_exact(key).document for key in snapshot.asset_documents}
        remote_manifest = self._get_exact(pointer.manifest_path).document
        if remote_manifest != snapshot.manifest_document or remote_assets != dict(
            snapshot.asset_documents
        ):
            raise SnapshotPublicationError("SNAPSHOT_REMOTE_READBACK_MISMATCH")
        validated_remote = validate_public_snapshot_bundle(
            pointer_document=snapshot.pointer_document,
            manifest_document=remote_manifest,
            asset_documents=remote_assets,
        )
        validate_briefing_evidence_projection(
            briefing=validated_remote.briefing,
            evidence_by_event_id=validated_remote.evidence_by_event_id,
        )
        scan_public_json(remote_manifest, label="remote manifest")
        for path, document in remote_assets.items():
            scan_public_json(document, label=f"remote {path}")

        previous = self._get_optional(_CURRENT_KEY)
        request: dict[str, object] = self._put_arguments(
            key=_CURRENT_KEY,
            document=snapshot.pointer_document,
            cache_control=_CURRENT_CACHE,
        )
        if previous is None:
            request["IfNoneMatch"] = "*"
        else:
            _require_current_identity(previous)
            assert previous.etag is not None
            request["IfMatch"] = previous.etag
        try:
            response = self._client.put_object(**request)
        except Exception as error:
            if not _is_precondition_failed(error):
                raise SnapshotPublicationError("CURRENT_POINTER_SWITCH_FAILED") from error
            winner = self._get_exact(_CURRENT_KEY)
            _require_current_identity(winner)
            if winner.document != snapshot.pointer_document:
                raise SnapshotPublicationError("CURRENT_POINTER_CONCURRENT_CONFLICT") from error
            self._read_pointer(winner)
            return SnapshotPublicationResult(
                snapshot_id=snapshot_id,
                pointer_switched=False,
                idempotent_winner=True,
                current_etag=winner.etag,
                current_version_id=winner.version_id,
            )
        version_id = _version_id_or_none(response.get("VersionId"))
        if version_id is None:
            raise SnapshotPublicationError("CURRENT_POINTER_VERSION_ID_MISSING")
        receipt_etag = _string_or_none(response.get("ETag"))
        if receipt_etag is None:
            raise SnapshotPublicationError("CURRENT_POINTER_ETAG_MISSING")
        written = self._get_exact(_CURRENT_KEY, version_id=version_id)
        if (
            written.document != snapshot.pointer_document
            or written.version_id != version_id
            or written.etag != receipt_etag
        ):
            raise SnapshotPublicationError("CURRENT_POINTER_VERSION_READBACK_MISMATCH")
        self._read_pointer(written)
        winner = self._get_exact(_CURRENT_KEY)
        _require_current_identity(winner)
        if winner.document != snapshot.pointer_document:
            raise SnapshotPublicationError("CURRENT_POINTER_CONCURRENT_CONFLICT")
        self._read_pointer(winner)
        is_exact_winner = winner.version_id == version_id
        return SnapshotPublicationResult(
            snapshot_id=snapshot_id,
            pointer_switched=is_exact_winner,
            idempotent_winner=not is_exact_winner,
            current_etag=winner.etag,
            current_version_id=winner.version_id,
        )

    def read_current(self) -> RemoteSnapshot:
        """Read and validate the full winner, including every immutable asset."""

        pointer = self._get_optional(_CURRENT_KEY)
        if pointer is None:
            raise CurrentSnapshotMissing("CURRENT_POINTER_MISSING")
        _require_current_identity(pointer)
        return self._read_pointer(pointer)

    def read_pointer_version(self, *, version_id: str) -> RemoteSnapshot:
        """Validate one historical pointer version before an operator restores it."""

        if not version_id or len(version_id) > 1024:
            raise ValueError("CURRENT_POINTER_VERSION_ID_INVALID")
        return self._read_pointer(self._get_exact(_CURRENT_KEY, version_id=version_id))

    def restore_pointer_version(
        self,
        *,
        version_id: str,
        expected_snapshot_id: str,
    ) -> tuple[RemoteSnapshot, str | None]:
        """Validate and CAS-restore an exact historical pointer after schedules stop."""

        target = self.read_pointer_version(version_id=version_id)
        if str(target.validated.pointer.snapshot_id) != expected_snapshot_id:
            raise SnapshotPublicationError("ROLLBACK_SNAPSHOT_ID_MISMATCH")
        current = self._get_exact(_CURRENT_KEY)
        if current.etag is None:
            raise SnapshotPublicationError("CURRENT_POINTER_ETAG_MISSING")
        restored_version = self.restore_pointer(
            document=target.pointer_document,
            expected_current_etag=current.etag,
        )
        return target, restored_version

    def _read_pointer(self, pointer: _RemoteObject) -> RemoteSnapshot:
        from stock_assistant.contracts.public_snapshots import PublicSnapshotPointerV1

        parsed_pointer = PublicSnapshotPointerV1.model_validate_json(pointer.document)
        manifest = self._get_exact(parsed_pointer.manifest_path)
        parsed_manifest = PublicSnapshotManifestV1_1.model_validate_json(manifest.document)
        assets = {
            asset.path: self._get_exact(asset.path).document for asset in parsed_manifest.assets
        }
        validated = validate_public_snapshot_bundle(
            pointer_document=pointer.document,
            manifest_document=manifest.document,
            asset_documents=assets,
        )
        validate_briefing_evidence_projection(
            briefing=validated.briefing,
            evidence_by_event_id=validated.evidence_by_event_id,
        )
        scan_public_json(pointer.document, label="remote pointer")
        scan_public_json(manifest.document, label="remote manifest")
        for path, document in assets.items():
            scan_public_json(document, label=f"remote {path}")
        return RemoteSnapshot(
            validated=validated,
            pointer_document=pointer.document,
            manifest_document=manifest.document,
            asset_documents=assets,
            current_etag=pointer.etag,
            current_version_id=pointer.version_id,
        )

    def restore_pointer(self, *, document: bytes, expected_current_etag: str) -> str | None:
        """Move the versioned pointer back with CAS; callers must disable schedules first."""

        scan_public_json(document, label="rollback pointer")
        try:
            response = self._client.put_object(
                **self._put_arguments(
                    key=_CURRENT_KEY,
                    document=document,
                    cache_control=_CURRENT_CACHE,
                ),
                IfMatch=expected_current_etag,
            )
        except Exception as error:
            code = (
                "ROLLBACK_POINTER_CONCURRENT_CONFLICT"
                if _is_precondition_failed(error)
                else "ROLLBACK_POINTER_SWITCH_FAILED"
            )
            raise SnapshotPublicationError(code) from error
        version_id = _version_id_or_none(response.get("VersionId"))
        if version_id is None:
            raise SnapshotPublicationError("ROLLBACK_POINTER_VERSION_ID_MISSING")
        receipt_etag = _string_or_none(response.get("ETag"))
        if receipt_etag is None:
            raise SnapshotPublicationError("ROLLBACK_POINTER_ETAG_MISSING")
        written = self._get_exact(_CURRENT_KEY, version_id=version_id)
        if (
            written.document != document
            or written.version_id != version_id
            or written.etag != receipt_etag
        ):
            raise SnapshotPublicationError("ROLLBACK_POINTER_VERSION_READBACK_MISMATCH")
        self._read_pointer(written)
        winner = self._get_exact(_CURRENT_KEY)
        _require_current_identity(winner)
        if (
            winner.document != document
            or winner.version_id != version_id
            or winner.etag != receipt_etag
        ):
            raise SnapshotPublicationError("ROLLBACK_POINTER_CONCURRENT_CONFLICT")
        self._read_pointer(winner)
        return winner.version_id

    def _validate_local(self, snapshot: SnapshotDocuments) -> ValidatedPublicSnapshotBundle:
        validated = validate_public_snapshot_bundle(
            pointer_document=snapshot.pointer_document,
            manifest_document=snapshot.manifest_document,
            asset_documents=snapshot.asset_documents,
        )
        if snapshot.pointer != validated.pointer or snapshot.manifest != validated.manifest:
            raise SnapshotPublicationError("SNAPSHOT_MODEL_BYTES_SPLIT_BRAIN")
        validate_briefing_evidence_projection(
            briefing=validated.briefing,
            evidence_by_event_id=validated.evidence_by_event_id,
        )
        scan_public_json(snapshot.pointer_document, label="pointer")
        scan_public_json(snapshot.manifest_document, label="manifest")
        for path, document in snapshot.asset_documents.items():
            scan_public_json(document, label=path)
        return validated

    def _put_immutable(self, key: str, document: bytes) -> None:
        try:
            self._client.put_object(
                **self._put_arguments(
                    key=key,
                    document=document,
                    cache_control=_IMMUTABLE_CACHE,
                ),
                IfNoneMatch="*",
            )
        except Exception as error:
            if not _is_precondition_failed(error):
                raise SnapshotPublicationError("SNAPSHOT_IMMUTABLE_UPLOAD_FAILED") from error
            existing = self._get_exact(key)
            if existing.document != document:
                raise SnapshotPublicationError("SNAPSHOT_IMMUTABLE_KEY_COLLISION") from error

    def _put_arguments(
        self,
        *,
        key: str,
        document: bytes,
        cache_control: str,
    ) -> dict[str, object]:
        digest = hashlib.sha256(document).digest()
        return {
            "Bucket": self._bucket,
            "Key": key,
            "Body": document,
            "ContentType": "application/json",
            "CacheControl": cache_control,
            "ServerSideEncryption": "AES256",
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": base64.b64encode(digest).decode("ascii"),
        }

    def _get_optional(self, key: str) -> _RemoteObject | None:
        try:
            return self._get_exact(key)
        except CurrentSnapshotMissing:
            return None
        except SnapshotPublicationError as error:
            cause = error.__cause__
            if (
                key == _CURRENT_KEY
                and isinstance(cause, Exception)
                and _error_code(cause) in {"403", "AccessDenied"}
                and self._list_proves_current_missing()
            ):
                return None
            raise

    def _list_proves_current_missing(self) -> bool:
        try:
            response = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=_CURRENT_KEY,
                MaxKeys=1,
            )
        except Exception as error:
            raise SnapshotPublicationError("CURRENT_POINTER_ABSENCE_UNPROVEN") from error
        contents = response.get("Contents", [])
        key_count = response.get("KeyCount")
        if (
            response.get("IsTruncated") is not False
            or not isinstance(contents, list)
            or not isinstance(key_count, int)
            or isinstance(key_count, bool)
            or key_count != len(contents)
            or contents
        ):
            return False
        return True

    def _get_exact(self, key: str, *, version_id: str | None = None) -> _RemoteObject:
        request: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "ChecksumMode": "ENABLED",
        }
        if version_id is not None:
            request["VersionId"] = version_id
        try:
            response = self._client.get_object(**request)
        except Exception as error:
            if _is_not_found(error):
                raise CurrentSnapshotMissing(f"OBJECT_MISSING:{key}") from error
            raise SnapshotPublicationError("SNAPSHOT_REMOTE_READ_FAILED") from error
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise SnapshotPublicationError("SNAPSHOT_REMOTE_BODY_MISSING")
        document = cast("_Body", body).read()
        if not isinstance(document, bytes):
            raise SnapshotPublicationError("SNAPSHOT_REMOTE_BODY_INVALID")
        expected_checksum = base64.b64encode(hashlib.sha256(document).digest()).decode("ascii")
        checksum = _string_or_none(response.get("ChecksumSHA256"))
        if checksum is not None and checksum != expected_checksum:
            raise SnapshotPublicationError("SNAPSHOT_REMOTE_CHECKSUM_MISMATCH")
        returned_version_id = _version_id_or_none(response.get("VersionId"))
        if version_id is not None and returned_version_id != version_id:
            raise SnapshotPublicationError("SNAPSHOT_REMOTE_VERSION_MISMATCH")
        return _RemoteObject(
            document=document,
            etag=_string_or_none(response.get("ETag")),
            version_id=returned_version_id,
        )


@dataclass(frozen=True, slots=True)
class _RemoteObject:
    document: bytes
    etag: str | None
    version_id: str | None


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _version_id_or_none(value: object) -> str | None:
    resolved = _string_or_none(value)
    return None if resolved in {None, "null"} else resolved


def _require_current_identity(value: _RemoteObject) -> None:
    if value.etag is None:
        raise SnapshotPublicationError("CURRENT_POINTER_ETAG_MISSING")
    if value.version_id is None:
        raise SnapshotPublicationError("CURRENT_POINTER_VERSION_ID_MISSING")


def _error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    payload = response.get("Error")
    if not isinstance(payload, Mapping):
        return None
    code = payload.get("Code")
    return str(code) if code is not None else None


def _is_not_found(error: Exception) -> bool:
    return _error_code(error) in {"404", "NoSuchKey", "NotFound"}


def _is_precondition_failed(error: Exception) -> bool:
    return _error_code(error) in {"409", "412", "ConditionalRequestConflict", "PreconditionFailed"}


__all__ = [
    "CurrentSnapshotMissing",
    "RemoteSnapshot",
    "S3SnapshotClient",
    "S3SnapshotPublisher",
    "SnapshotPublicationError",
    "SnapshotPublicationResult",
]
