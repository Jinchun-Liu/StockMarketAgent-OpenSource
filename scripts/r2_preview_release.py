"""Atomic, non-destructive publisher for the private R2 static Web."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from scripts.r2_preview_aws import (
    AwsCli,
    PreviewAwsError,
    locate_aws_cli,
    verify_cli_v2,
    verify_identity,
)

PUBLIC_STACK_NAME: Final = "stockmarketagent-public-demo-tokyo"
SHA: Final = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE_CACHE: Final = "public,max-age=31536000,immutable"
MUTABLE_CACHE: Final = "public,max-age=300,must-revalidate"
NO_STORE: Final = "no-store,max-age=0"
R2_RUNTIME_MODE: Final = "R2_PRIVATE_PREVIEW"


class AwsInvoker(Protocol):
    def json(self, service: str, operation: str, *arguments: str) -> object: ...

    def text(self, service: str, operation: str, *arguments: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ReleaseObject:
    source: Path
    key: str
    content_type: str
    cache_control: str
    sha256: str


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PreviewAwsError(f"{label}:OBJECT_REQUIRED")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise PreviewAwsError(f"{label}:ARRAY_REQUIRED")
    return value


def _content_type(path: Path) -> str:
    explicit = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }
    if path.suffix in explicit:
        return explicit[path.suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _build_metadata(*, product_sha: str) -> dict[str, str]:
    if SHA.fullmatch(product_sha) is None:
        raise PreviewAwsError("PRODUCT_SHA_INVALID")
    return {
        "schema_version": "1.0",
        "project": "StockMarketAgent",
        "product_commit": product_sha,
        "runtime_mode": R2_RUNTIME_MODE,
    }


def _read_json_object(path: Path, *, error_code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreviewAwsError(error_code) from error
    if not isinstance(value, dict):
        raise PreviewAwsError(error_code)
    return value


def _write_build_metadata(artifact_dir: Path, *, product_sha: str) -> dict[str, str]:
    root = artifact_dir.resolve()
    if root != artifact_dir or not root.is_dir():
        raise PreviewAwsError("ARTIFACT_DIRECTORY_CANONICAL_REQUIRED")
    destination = root / "build-metadata.json"
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise PreviewAwsError("ARTIFACT_PATH_FORBIDDEN")
    metadata = _build_metadata(product_sha=product_sha)
    destination.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def inventory(artifact_dir: Path, *, product_sha: str) -> tuple[ReleaseObject, ...]:
    if SHA.fullmatch(product_sha) is None:
        raise PreviewAwsError("PRODUCT_SHA_INVALID")
    root = artifact_dir.resolve()
    if root != artifact_dir or not root.is_dir():
        raise PreviewAwsError("ARTIFACT_DIRECTORY_CANONICAL_REQUIRED")
    objects: list[ReleaseObject] = []
    for path in sorted(root.rglob(chr(42))):
        if path.is_symlink():
            raise PreviewAwsError("ARTIFACT_SYMLINK_FORBIDDEN")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if ".." in Path(relative).parts:
            raise PreviewAwsError("ARTIFACT_PATH_FORBIDDEN")
        immutable = relative.startswith("_next/static/")
        key = f"assets/releases/{product_sha}/{relative}" if immutable else relative
        cache_control = (
            IMMUTABLE_CACHE
            if immutable
            else (
                NO_STORE
                if relative in {"runtime-config.json", "build-metadata.json"}
                else MUTABLE_CACHE
            )
        )
        objects.append(
            ReleaseObject(
                source=path,
                key=key,
                content_type=_content_type(path),
                cache_control=cache_control,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    names = {item.key for item in objects}
    if not {"index.html", "runtime-config.json", "build-metadata.json"}.issubset(names):
        raise PreviewAwsError("R2_WEB_ARTIFACT_INCOMPLETE")
    runtime_config = _read_json_object(
        root / "runtime-config.json", error_code="R2_WEB_RUNTIME_CONFIG_INVALID"
    )
    if runtime_config.get("product_sha") != product_sha:
        raise PreviewAwsError("R2_WEB_RUNTIME_CONFIG_INVALID")
    build_metadata = _read_json_object(
        root / "build-metadata.json", error_code="R2_WEB_BUILD_METADATA_INVALID"
    )
    if build_metadata != _build_metadata(product_sha=product_sha):
        raise PreviewAwsError("R2_WEB_BUILD_METADATA_INVALID")
    prefix_parts = ("assets", "releases", product_sha, "_next", "static", "")
    immutable_prefix = chr(47).join(prefix_parts)
    if not any(item.key.startswith(immutable_prefix) for item in objects):
        raise PreviewAwsError("IMMUTABLE_ASSET_SET_MISSING")
    return tuple(objects)


def _ordered(objects: tuple[ReleaseObject, ...]) -> tuple[ReleaseObject, ...]:
    asset_prefix = chr(47).join(("assets", "releases", ""))

    def priority(item: ReleaseObject) -> tuple[int, str]:
        object_name = item.key
        if object_name.startswith(asset_prefix):
            return (0, object_name)
        if object_name == "runtime-config.json":
            return (2, object_name)
        if object_name.endswith(".html"):
            return (3, object_name)
        if object_name == "build-metadata.json":
            return (4, object_name)
        return (1, object_name)

    return tuple(sorted(objects, key=priority))


def _stack_outputs(invoker: AwsInvoker, *, product_sha: str) -> dict[str, str]:
    payload = _object(
        invoker.json("cloudformation", "describe-stacks", "--stack-name", PUBLIC_STACK_NAME),
        "public shell stack",
    )
    stacks = _array(payload.get("Stacks"), "public shell stacks")
    stack = _object(stacks[0], "public shell stack") if len(stacks) == 1 else {}
    result: dict[str, str] = {}
    for raw in _array(stack.get("Outputs"), "public shell outputs"):
        output = _object(raw, "public shell output")
        name, value = output.get("OutputKey"), output.get("OutputValue")
        if not isinstance(name, str) or not isinstance(value, str) or name in result:
            raise PreviewAwsError("PUBLIC_SHELL_OUTPUT_INVALID")
        result[name] = value
    if result.get("ProductSha") != product_sha:
        raise PreviewAwsError("PUBLIC_SHELL_PRODUCT_SHA_MISMATCH")
    for required in ("BucketName", "DistributionId", "PublicDemoUrl"):
        if not result.get(required):
            raise PreviewAwsError(f"PUBLIC_SHELL_OUTPUT_MISSING:{required}")
    return result


def _invalidation_paths() -> tuple[str, ...]:
    slash = chr(47)
    return tuple(
        slash + value
        for value in (
            "",
            "index.html",
            "404.html",
            "runtime-config.json",
            "build-metadata.json",
            "history/*",
            "research/*",
            "auth/callback/*",
        )
    )


def publish(
    invoker: AwsInvoker,
    *,
    expected_account_id: str,
    product_sha: str,
    artifact_dir: Path,
    confirmation: str,
) -> dict[str, object]:
    if confirmation != "PUBLISH_R2_WEB":
        raise PreviewAwsError("PUBLISH_CONFIRMATION_REQUIRED")
    verify_identity(invoker.json("sts", "get-caller-identity"), expected_account_id)
    outputs = _stack_outputs(invoker, product_sha=product_sha)
    objects = inventory(artifact_dir, product_sha=product_sha)
    bucket = outputs["BucketName"]
    uploaded: list[dict[str, object]] = []
    for item in _ordered(objects):
        object_name = item.key
        checksum = base64.b64encode(bytes.fromhex(item.sha256)).decode("ascii")
        result = _object(
            invoker.json(
                "s3api",
                "put-object",
                "--bucket",
                bucket,
                "--key",
                object_name,
                "--body",
                str(item.source),
                "--content-type",
                item.content_type,
                "--cache-control",
                item.cache_control,
                "--server-side-encryption",
                "AES256",
                "--checksum-sha256",
                checksum,
                "--metadata",
                f"productsha={product_sha},sha256={item.sha256}",
            ),
            f"put {object_name}",
        )
        version_id = result.get("VersionId")
        if not isinstance(version_id, str) or not version_id.strip():
            raise PreviewAwsError(f"VERSIONED_UPLOAD_NOT_CONFIRMED:{object_name}")
        uploaded.append({"object_name": object_name, "sha256": item.sha256})
    paths = _invalidation_paths()
    invalidation = _object(
        invoker.json(
            "cloudfront",
            "create-invalidation",
            "--distribution-id",
            outputs["DistributionId"],
            "--paths",
            *paths,
        ),
        "scoped invalidation",
    )
    record = _object(invalidation.get("Invalidation"), "invalidation")
    invalidation_id = record.get("Id")
    if not isinstance(invalidation_id, str):
        raise PreviewAwsError("INVALIDATION_ID_MISSING")
    invoker.text(
        "cloudfront",
        "wait",
        "invalidation-completed",
        "--distribution-id",
        outputs["DistributionId"],
        "--id",
        invalidation_id,
    )
    return {
        "schema_version": "1.0",
        "product_sha": product_sha,
        "bucket": bucket,
        "distribution_id": outputs["DistributionId"],
        "public_url": outputs["PublicDemoUrl"],
        "uploaded": uploaded,
        "invalidation_id": invalidation_id,
        "invalidation_paths": list(paths),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-account-id")
    parser.add_argument("--product-sha", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--prepare-build-metadata", action="store_true")
    operation.add_argument("--confirm")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        artifact_dir = args.artifact_dir.resolve()
        if args.prepare_build_metadata:
            result: object = _write_build_metadata(artifact_dir, product_sha=args.product_sha)
        else:
            if not args.expected_account_id:
                raise PreviewAwsError("EXPECTED_ACCOUNT_ID_REQUIRED")
            executable = locate_aws_cli()
            verify_cli_v2(executable)
            result = publish(
                AwsCli(executable),
                expected_account_id=args.expected_account_id,
                product_sha=args.product_sha,
                artifact_dir=artifact_dir,
                confirmation=args.confirm,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
    except (
        PreviewAwsError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"R2 Web release blocked: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
