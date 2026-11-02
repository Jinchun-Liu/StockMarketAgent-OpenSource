"""Repeatable, receipt-bound release and rollback for the existing public shell.

This operator never writes or deletes ``data/**``. Rollback restores exact predecessor versions
for overwritten shell objects and retains receipt-bound newly introduced objects unreferenced;
it never deletes an object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Protocol, cast
from urllib.parse import quote

from scripts.public_demo_aws import (
    STACK_NAME,
    AwsCli,
    AwsSafetyError,
    _canonical_hash,
    _ensure_versioned_file,
    _identity,
    _metadata,
    _optional_head_object,
    _outputs,
    _remote_build_metadata,
    _route_invalidation_paths,
    _sha,
    _shell_object_policy,
    _validate_build_metadata,
    _verify_versioned_file,
    _versioned_head,
    _versioned_object_body,
    verify_existing_stack,
)

RECEIPT_SCHEMA: Final = "1.1"
RECEIPT_PREFIX: Final = "ops/shell-releases"
CONTENT_TYPE: Final = "application/json"
NO_STORE: Final = "no-store,max-age=0"
ENCRYPTION: Final = "AES256"


class AwsInvoker(Protocol):
    def json(self, service: str, operation: str, *arguments: str) -> object: ...

    def text(self, service: str, operation: str, *arguments: str) -> str: ...


@dataclass(frozen=True, slots=True)
class FrozenShell:
    files: tuple[tuple[str, bytes], ...]
    inventory_hash: str


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AwsSafetyError(f"{label} must be an object")
    return value


def _safe_shell_path(path: str) -> str:
    candidate = Path(path)
    if (
        not path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or path.startswith("data/")
        or path.startswith("ops/")
    ):
        raise AwsSafetyError(f"SHELL_RELEASE_PATH_FORBIDDEN:{path}")
    return path


def freeze_shell(artifact_dir: Path, *, new_product_sha: str) -> FrozenShell:
    """Capture one immutable byte inventory and reject data/operator namespaces."""

    root = artifact_dir.resolve()
    if not root.is_dir():
        raise AwsSafetyError("SHELL_ARTIFACT_DIRECTORY_MISSING")
    files: list[tuple[str, bytes]] = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise AwsSafetyError("SHELL_ARTIFACT_SYMLINK_FORBIDDEN")
        if not item.is_file():
            continue
        relative = _safe_shell_path(item.relative_to(root).as_posix())
        files.append((relative, item.read_bytes()))
    if not files or "build-metadata.json" not in dict(files):
        raise AwsSafetyError("SHELL_ARTIFACT_INCOMPLETE")
    try:
        metadata = _object(json.loads(dict(files)["build-metadata.json"]), "build metadata")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AwsSafetyError("SHELL_BUILD_METADATA_INVALID") from error
    _validate_build_metadata(metadata, product_sha=_sha(new_product_sha))
    inventory = [
        {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for path, body in files
    ]
    return FrozenShell(files=tuple(files), inventory_hash=_canonical_hash(inventory))


def _release_groups(shell: FrozenShell) -> tuple[list[tuple[str, bytes]], ...]:
    immutable: list[tuple[str, bytes]] = []
    mutable: list[tuple[str, bytes]] = []
    routes: list[tuple[str, bytes]] = []
    build_metadata: list[tuple[str, bytes]] = []
    for path, body in shell.files:
        _safe_shell_path(path)
        if path.startswith("_next/static/"):
            immutable.append((path, body))
        elif path == "build-metadata.json":
            build_metadata.append((path, body))
        elif Path(path).suffix.casefold() == ".html":
            routes.append((path, body))
        else:
            mutable.append((path, body))
    if not immutable or not routes or len(build_metadata) != 1:
        raise AwsSafetyError("SHELL_ARTIFACT_INCOMPLETE")
    return immutable, mutable, routes, build_metadata


def _validate_existing_shell_build_metadata(
    value: dict[str, object],
    *,
    product_sha: str,
    allow_legacy_project_omission: bool,
) -> None:
    """Accept the exact pre-R1 public metadata profile only at the old-shell boundary."""

    if allow_legacy_project_omission and "project" not in value:
        legacy_profile = {
            "schema_version": "1.0",
            "product_commit": product_sha,
            "runtime_mode": "PUBLIC_STATIC",
            "release_tier": "DEV_ONLY",
            "data_mode": "DEMO_REPLAY",
        }
        if all(value.get(key) == expected for key, expected in legacy_profile.items()):
            return
    _validate_build_metadata(value, product_sha=product_sha)


def _put_json(
    invoker: AwsInvoker,
    *,
    bucket: str,
    key: str,
    body: bytes,
) -> dict[str, object]:
    digest = hashlib.sha256(body).hexdigest()
    existing = _optional_head_object(invoker, bucket=bucket, key=key)
    if existing is not None:
        return _verify_versioned_file(
            invoker,
            bucket=bucket,
            key=key,
            expected_body=body,
            expected_content_type=CONTENT_TYPE,
            expected_cache_control=NO_STORE,
            expected_server_side_encryption=ENCRYPTION,
            receipt=existing,
        )
    with TemporaryDirectory(prefix="stockmarketagent-shell-release-") as temporary:
        source = Path(temporary).resolve() / "receipt.json"
        source.write_bytes(body)
        created = _object(
            invoker.json(
                "s3api",
                "put-object",
                "--bucket",
                bucket,
                "--key",
                key,
                "--body",
                str(source),
                "--content-type",
                CONTENT_TYPE,
                "--cache-control",
                NO_STORE,
                "--server-side-encryption",
                ENCRYPTION,
                "--metadata",
                f"sha256={digest}",
                "--if-none-match",
                "*",
            ),
            "put shell receipt",
        )
    return _verify_versioned_file(
        invoker,
        bucket=bucket,
        key=key,
        expected_body=body,
        expected_content_type=CONTENT_TYPE,
        expected_cache_control=NO_STORE,
        expected_server_side_encryption=ENCRYPTION,
        receipt=created,
    )


def _put_state(
    invoker: AwsInvoker,
    *,
    bucket: str,
    key: str,
    metadata: dict[str, str],
    etag: str | None,
) -> dict[str, object]:
    arguments = [
        "--bucket",
        bucket,
        "--key",
        key,
        "--cache-control",
        NO_STORE,
        "--server-side-encryption",
        ENCRYPTION,
        "--metadata",
        ",".join(f"{name}={value}" for name, value in sorted(metadata.items())),
    ]
    arguments.extend(("--if-none-match", "*") if etag is None else ("--if-match", etag))
    created = _object(invoker.json("s3api", "put-object", *arguments), "put release state")
    _versioned_head(created, key=key)
    current = _optional_head_object(invoker, bucket=bucket, key=key)
    if current is None or _metadata(current, label="release state") != metadata:
        raise AwsSafetyError("SHELL_RELEASE_STATE_READBACK_MISMATCH")
    return current


def _target(invoker: AwsInvoker, *, expected_account_id: str) -> tuple[dict[str, str], str]:
    _identity(invoker, expected_account_id)
    stack = verify_existing_stack(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", STACK_NAME)
    )
    outputs = _outputs(stack)
    stack_id = stack.get("StackId")
    if not isinstance(stack_id, str):
        raise AwsSafetyError("SHELL_RELEASE_TARGET_INVALID")
    return outputs, stack_id


def _receipt_document(
    *,
    old_product_sha: str,
    new_product_sha: str,
    shell: FrozenShell,
    outputs: dict[str, str],
    stack_id: str,
    previous: list[dict[str, str]],
    introduced_paths: Sequence[str],
) -> tuple[str, bytes]:
    intent = {
        "schema_version": RECEIPT_SCHEMA,
        "old_product_sha": old_product_sha,
        "new_product_sha": new_product_sha,
        "inventory_hash": shell.inventory_hash,
        "stack_id": stack_id,
        "bucket": outputs["BucketName"],
        "distribution_id": outputs["DistributionId"],
    }
    release_id = _canonical_hash(intent)
    inventory = [
        {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for path, body in shell.files
    ]
    document = {
        "schema_version": RECEIPT_SCHEMA,
        "release_id": release_id,
        "intent": intent,
        "inventory": inventory,
        "previous_versions": previous,
        "introduced_paths": list(introduced_paths),
    }
    return release_id, (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def release_existing_shell(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    old_product_sha: str,
    new_product_sha: str,
    artifact_dir: Path,
    confirmation: str,
) -> dict[str, str]:
    """Publish one old-to-new intent; rerunning the exact completed intent is a no-op."""

    old_sha, new_sha = _sha(old_product_sha), _sha(new_product_sha)
    if old_sha == new_sha or confirmation != f"RELEASE_EXISTING_SHELL:{old_sha}:{new_sha}":
        raise AwsSafetyError("SHELL_RELEASE_CONFIRMATION_REQUIRED")
    shell = freeze_shell(artifact_dir, new_product_sha=new_sha)
    immutable, mutable, routes, build_metadata = _release_groups(shell)
    outputs, stack_id = _target(invoker, expected_account_id=expected_account_id)
    bucket = outputs["BucketName"]
    deployed = _remote_build_metadata(invoker, bucket=bucket)
    if deployed.get("product_commit") == new_sha:
        expected_old_or_new = new_sha
    else:
        expected_old_or_new = old_sha
    _validate_existing_shell_build_metadata(
        deployed,
        product_sha=expected_old_or_new,
        allow_legacy_project_omission=expected_old_or_new == old_sha,
    )

    release_id, _ = _receipt_document(
        old_product_sha=old_sha,
        new_product_sha=new_sha,
        shell=shell,
        outputs=outputs,
        stack_id=stack_id,
        previous=[],
        introduced_paths=(),
    )
    receipt_key = f"{RECEIPT_PREFIX}/{release_id}/receipt.json"
    state_key = f"{RECEIPT_PREFIX}/{release_id}/state"
    state = _optional_head_object(invoker, bucket=bucket, key=state_key)
    if state is not None:
        state_metadata = _metadata(state, label="release state")
        receipt_version = state_metadata.get("receipt-version-id", "")
        receipt_sha256 = state_metadata.get("receipt-sha256", "")
        if (
            state_metadata.get("old-product-sha") != old_sha
            or state_metadata.get("new-product-sha") != new_sha
            or state_metadata.get("receipt-key") != receipt_key
            or not receipt_version
            or not re.fullmatch(r"[a-f0-9]{64}", receipt_sha256)
        ):
            raise AwsSafetyError("SHELL_RELEASE_STATE_MISMATCH")
        receipt_body = _versioned_object_body(
            invoker,
            bucket=bucket,
            key=receipt_key,
            version_id=receipt_version,
        )
        if hashlib.sha256(receipt_body).hexdigest() != receipt_sha256:
            raise AwsSafetyError("SHELL_RELEASE_RECEIPT_DIGEST_MISMATCH")
        _validated_receipt(json.loads(receipt_body), release_id=release_id)
        if state_metadata.get("state") == "COMPLETE":
            if deployed.get("product_commit") != new_sha:
                raise AwsSafetyError("SHELL_RELEASE_STATE_MISMATCH")
            return {
                **outputs,
                "ReleaseId": release_id,
                "ReceiptVersionId": receipt_version,
                "StateVersionId": _versioned_head(state, key=state_key)[1],
                "InvalidationId": state_metadata["invalidation-id"],
            }
        if state_metadata.get("state") != "IN_PROGRESS":
            raise AwsSafetyError("SHELL_RELEASE_STATE_MISMATCH")
    else:
        if deployed.get("product_commit") == new_sha:
            raise AwsSafetyError("SHELL_RELEASE_RECEIPT_MISSING_FOR_DEPLOYED_SHA")
        previous: list[dict[str, str]] = []
        introduced_paths: list[str] = []
        for path, _body in (*mutable, *routes, *build_metadata):
            head = _optional_head_object(invoker, bucket=bucket, key=path)
            if head is None:
                introduced_paths.append(path)
                continue
            etag, version_id = _versioned_head(head, key=path)
            previous.append({"path": path, "etag": etag, "version_id": version_id})
        _, receipt_body = _receipt_document(
            old_product_sha=old_sha,
            new_product_sha=new_sha,
            shell=shell,
            outputs=outputs,
            stack_id=stack_id,
            previous=previous,
            introduced_paths=introduced_paths,
        )
        receipt = _put_json(invoker, bucket=bucket, key=receipt_key, body=receipt_body)
        _, receipt_version = _versioned_head(receipt, key=receipt_key)
        receipt_sha256 = hashlib.sha256(receipt_body).hexdigest()
        state = _put_state(
            invoker,
            bucket=bucket,
            key=state_key,
            metadata={
                "state": "IN_PROGRESS",
                "old-product-sha": old_sha,
                "new-product-sha": new_sha,
                "receipt-key": receipt_key,
                "receipt-version-id": receipt_version,
                "receipt-sha256": receipt_sha256,
            },
            etag=None,
        )

    for path, body in (*immutable, *mutable, *routes, *build_metadata):
        _ensure_versioned_file(
            invoker,
            bucket=bucket,
            relative=path,
            body=body,
            immutable=path.startswith("_next/static/"),
        )
    _validate_build_metadata(_remote_build_metadata(invoker, bucket=bucket), product_sha=new_sha)
    paths = _route_invalidation_paths([path for path, _body in (*routes, *build_metadata)])
    invalidation = _object(
        invoker.json(
            "cloudfront",
            "create-invalidation",
            "--distribution-id",
            outputs["DistributionId"],
            "--invalidation-batch",
            json.dumps(
                {
                    "CallerReference": f"shell-release-{release_id}",
                    "Paths": {"Quantity": len(paths), "Items": list(paths)},
                },
                separators=(",", ":"),
            ),
        ),
        "create invalidation",
    )
    invalidation_id = _object(invalidation.get("Invalidation"), "invalidation").get("Id")
    if not isinstance(invalidation_id, str) or not invalidation_id:
        raise AwsSafetyError("SHELL_RELEASE_INVALIDATION_INVALID")
    invoker.text(
        "cloudfront",
        "wait",
        "invalidation-completed",
        "--distribution-id",
        outputs["DistributionId"],
        "--id",
        invalidation_id,
    )
    etag, _ = _versioned_head(state, key=state_key)
    completed = _put_state(
        invoker,
        bucket=bucket,
        key=state_key,
        metadata={
            **_metadata(state, label="release state"),
            "state": "COMPLETE",
            "invalidation-id": invalidation_id,
        },
        etag=etag,
    )
    return {
        **outputs,
        "ReleaseId": release_id,
        "ReceiptVersionId": receipt_version,
        "StateVersionId": _versioned_head(completed, key=state_key)[1],
        "InvalidationId": invalidation_id,
    }


def _validated_receipt(document: object, *, release_id: str) -> dict[str, object]:
    receipt = _object(document, "rollback receipt")
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("release_id") != release_id:
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    intent = _object(receipt.get("intent"), "rollback intent")
    if _canonical_hash(intent) != release_id:
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    inventory = receipt.get("inventory")
    if not isinstance(inventory, list) or not inventory:
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    inventory_paths: set[str] = set()
    for raw in inventory:
        item = _object(raw, "release inventory object")
        path = item.get("path")
        size = item.get("size")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or path in inventory_paths
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
        _safe_shell_path(path)
        inventory_paths.add(path)
    previous = receipt.get("previous_versions")
    if not isinstance(previous, list) or not previous:
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    previous_paths: set[str] = set()
    for raw in previous:
        item = _object(raw, "rollback object")
        path = item.get("path")
        if not isinstance(path, str) or path not in inventory_paths or path in previous_paths:
            raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
        _safe_shell_path(path)
        if not all(isinstance(item.get(key), str) and item[key] for key in ("etag", "version_id")):
            raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
        previous_paths.add(path)
    introduced = receipt.get("introduced_paths")
    if not isinstance(introduced, list):
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    introduced_paths: set[str] = set()
    for raw in introduced:
        if (
            not isinstance(raw, str)
            or raw == "build-metadata.json"
            or raw not in inventory_paths
            or raw in previous_paths
            or raw in introduced_paths
        ):
            raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
        _safe_shell_path(raw)
        introduced_paths.add(raw)
    rollback_paths = {path for path in inventory_paths if not path.startswith("_next/static/")}
    if previous_paths | introduced_paths != rollback_paths:
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    return receipt


def rollback_existing_shell(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    release_id: str,
    confirmation: str,
) -> dict[str, str]:
    """Restore only exact previous non-data object versions recorded before release."""

    if confirmation != f"ROLLBACK_EXISTING_SHELL:{release_id}":
        raise AwsSafetyError("SHELL_ROLLBACK_CONFIRMATION_REQUIRED")
    outputs, stack_id = _target(invoker, expected_account_id=expected_account_id)
    bucket = outputs["BucketName"]
    receipt_key = f"{RECEIPT_PREFIX}/{release_id}/receipt.json"
    state_key = f"{RECEIPT_PREFIX}/{release_id}/state"
    state = _optional_head_object(invoker, bucket=bucket, key=state_key)
    if state is None:
        raise AwsSafetyError("SHELL_ROLLBACK_STATE_MISSING")
    metadata = _metadata(state, label="release state")
    if metadata.get("state") not in {"COMPLETE", "ROLLED_BACK"}:
        raise AwsSafetyError("SHELL_ROLLBACK_STATE_INVALID")
    if metadata.get("state") == "ROLLED_BACK":
        return {
            **outputs,
            "ReleaseId": release_id,
            "StateVersionId": _versioned_head(state, key=state_key)[1],
        }
    receipt_version = metadata.get("receipt-version-id", "")
    receipt_sha256 = metadata.get("receipt-sha256", "")
    if not receipt_version or not re.fullmatch(r"[a-f0-9]{64}", receipt_sha256):
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_INVALID")
    body = _versioned_object_body(
        invoker,
        bucket=bucket,
        key=receipt_key,
        version_id=receipt_version,
    )
    if hashlib.sha256(body).hexdigest() != receipt_sha256:
        raise AwsSafetyError("SHELL_ROLLBACK_RECEIPT_DIGEST_MISMATCH")
    receipt = _validated_receipt(json.loads(body), release_id=release_id)
    intent = _object(receipt["intent"], "rollback intent")
    if intent.get("bucket") != bucket or intent.get("stack_id") != stack_id:
        raise AwsSafetyError("SHELL_ROLLBACK_TARGET_MISMATCH")
    new_sha = cast(str, intent["new_product_sha"])
    old_sha = cast(str, intent["old_product_sha"])
    _validate_build_metadata(_remote_build_metadata(invoker, bucket=bucket), product_sha=new_sha)
    introduced = cast(list[str], receipt["introduced_paths"])
    inventory = cast(list[dict[str, object]], receipt["inventory"])
    inventory_by_path = {cast(str, item["path"]): item for item in inventory}
    for path in introduced:
        current = _optional_head_object(invoker, bucket=bucket, key=path)
        if current is None:
            raise AwsSafetyError(f"SHELL_ROLLBACK_INTRODUCED_OBJECT_MISMATCH:{path}")
        _, version_id = _versioned_head(current, key=path)
        current_body = _versioned_object_body(
            invoker,
            bucket=bucket,
            key=path,
            version_id=version_id,
        )
        inventory_item = inventory_by_path[path]
        if (
            len(current_body) != inventory_item["size"]
            or hashlib.sha256(current_body).hexdigest() != inventory_item["sha256"]
        ):
            raise AwsSafetyError(f"SHELL_ROLLBACK_INTRODUCED_OBJECT_MISMATCH:{path}")
        content_type, cache_control = _shell_object_policy(path, immutable=False)
        _verify_versioned_file(
            invoker,
            bucket=bucket,
            key=path,
            expected_body=current_body,
            expected_content_type=content_type,
            expected_cache_control=cache_control,
            expected_server_side_encryption=ENCRYPTION,
            receipt=current,
        )
    previous = cast(list[dict[str, str]], receipt["previous_versions"])
    ordered = sorted(
        previous,
        key=lambda item: (item["path"] == "build-metadata.json", item["path"]),
    )
    for item in ordered:
        path = _safe_shell_path(item["path"])
        old_body = _versioned_object_body(
            invoker,
            bucket=bucket,
            key=path,
            version_id=item["version_id"],
        )
        copy_source = (
            quote(f"{bucket}/{path}", safe="/") + "?versionId=" + quote(item["version_id"], safe="")
        )
        copied = _object(
            invoker.json(
                "s3api",
                "copy-object",
                "--bucket",
                bucket,
                "--key",
                path,
                "--copy-source",
                copy_source,
                "--metadata-directive",
                "COPY",
            ),
            "rollback copy",
        )
        copied_version = copied.get("VersionId")
        if not isinstance(copied_version, str) or not copied_version:
            raise AwsSafetyError(f"SHELL_ROLLBACK_VERSION_MISSING:{path}")
        current = _optional_head_object(invoker, bucket=bucket, key=path)
        if current is None or _versioned_head(current, key=path)[1] != copied_version:
            raise AwsSafetyError(f"SHELL_ROLLBACK_VERSION_READBACK_MISMATCH:{path}")
        if (
            _versioned_object_body(
                invoker,
                bucket=bucket,
                key=path,
                version_id=copied_version,
            )
            != old_body
        ):
            raise AwsSafetyError(f"SHELL_ROLLBACK_READBACK_MISMATCH:{path}")
    _validate_existing_shell_build_metadata(
        _remote_build_metadata(invoker, bucket=bucket),
        product_sha=old_sha,
        allow_legacy_project_omission=True,
    )
    paths = _route_invalidation_paths([item["path"] for item in previous])
    invalidation = _object(
        invoker.json(
            "cloudfront",
            "create-invalidation",
            "--distribution-id",
            outputs["DistributionId"],
            "--invalidation-batch",
            json.dumps(
                {
                    "CallerReference": f"shell-rollback-{release_id}",
                    "Paths": {"Quantity": len(paths), "Items": list(paths)},
                },
                separators=(",", ":"),
            ),
        ),
        "rollback invalidation",
    )
    invalidation_id = _object(invalidation.get("Invalidation"), "invalidation").get("Id")
    if not isinstance(invalidation_id, str) or not invalidation_id:
        raise AwsSafetyError("SHELL_ROLLBACK_INVALIDATION_INVALID")
    invoker.text(
        "cloudfront",
        "wait",
        "invalidation-completed",
        "--distribution-id",
        outputs["DistributionId"],
        "--id",
        invalidation_id,
    )
    etag, _ = _versioned_head(state, key=state_key)
    rolled_back = _put_state(
        invoker,
        bucket=bucket,
        key=state_key,
        metadata={**metadata, "state": "ROLLED_BACK", "rollback-invalidation-id": invalidation_id},
        etag=etag,
    )
    return {
        **outputs,
        "ReleaseId": release_id,
        "RollbackInvalidationId": invalidation_id,
        "RetainedIntroducedCount": str(len(introduced)),
        "StateVersionId": _versioned_head(rolled_back, key=state_key)[1],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-account-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    release = sub.add_parser("release-existing-shell")
    release.add_argument("--old-product-sha", required=True)
    release.add_argument("--new-product-sha", required=True)
    release.add_argument("--artifact-dir", type=Path, required=True)
    release.add_argument("--confirm", required=True)
    rollback = sub.add_parser("rollback-existing-shell")
    rollback.add_argument("--release-id", required=True)
    rollback.add_argument("--confirm", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    invoker = AwsCli()
    if arguments.command == "release-existing-shell":
        result = release_existing_shell(
            invoker,
            expected_account_id=arguments.expected_account_id,
            old_product_sha=arguments.old_product_sha,
            new_product_sha=arguments.new_product_sha,
            artifact_dir=arguments.artifact_dir,
            confirmation=arguments.confirm,
        )
    else:
        result = rollback_existing_shell(
            invoker,
            expected_account_id=arguments.expected_account_id,
            release_id=arguments.release_id,
            confirmation=arguments.confirm,
        )
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FrozenShell",
    "freeze_shell",
    "release_existing_shell",
    "rollback_existing_shell",
]
