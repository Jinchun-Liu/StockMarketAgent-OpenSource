from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from scripts.public_demo_aws import AwsSafetyError
from scripts.release_existing_shell import (
    _receipt_document,
    _release_groups,
    _safe_shell_path,
    _validate_existing_shell_build_metadata,
    _validated_receipt,
    freeze_shell,
    rollback_existing_shell,
)

OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


def _artifact(root: Path) -> None:
    (root / "_next" / "static").mkdir(parents=True)
    (root / "_next" / "static" / "chunk.js").write_text("immutable", encoding="utf-8")
    (root / "index.html").write_text("<main>R1</main>", encoding="utf-8")
    (root / "status.json").write_text('{"ok":true}', encoding="utf-8")
    (root / "build-metadata.json").write_text(
        json.dumps({"project": "StockMarketAgent", "product_commit": NEW_SHA}),
        encoding="utf-8",
    )


def test_freeze_shell_rejects_data_namespace_and_orders_metadata_last(tmp_path: Path) -> None:
    _artifact(tmp_path)
    shell = freeze_shell(tmp_path, new_product_sha=NEW_SHA)
    immutable, mutable, routes, metadata = _release_groups(shell)
    assert [path for path, _body in immutable] == ["_next/static/chunk.js"]
    assert [path for path, _body in mutable] == ["status.json"]
    assert [path for path, _body in routes] == ["index.html"]
    assert [path for path, _body in metadata] == ["build-metadata.json"]

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "current.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AwsSafetyError, match="SHELL_RELEASE_PATH_FORBIDDEN"):
        freeze_shell(tmp_path, new_product_sha=NEW_SHA)


def test_freeze_shell_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("safe", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(target)

    with pytest.raises(AwsSafetyError, match="SHELL_ARTIFACT_SYMLINK_FORBIDDEN"):
        freeze_shell(tmp_path, new_product_sha=NEW_SHA)


def test_existing_shell_accepts_only_the_exact_legacy_public_metadata_profile() -> None:
    legacy = {
        "schema_version": "1.0",
        "product_commit": OLD_SHA,
        "runtime_mode": "PUBLIC_STATIC",
        "release_tier": "DEV_ONLY",
        "data_mode": "DEMO_REPLAY",
        "generated_at": "2026-07-31T01:05:00Z",
    }
    _validate_existing_shell_build_metadata(
        legacy,
        product_sha=OLD_SHA,
        allow_legacy_project_omission=True,
    )

    for key, bad_value in (
        ("product_commit", NEW_SHA),
        ("runtime_mode", "UNKNOWN"),
        ("release_tier", "COMMERCIAL"),
        ("data_mode", "LIVE"),
    ):
        mutated = {**legacy, key: bad_value}
        with pytest.raises(AwsSafetyError, match="outside the requested shell state"):
            _validate_existing_shell_build_metadata(
                mutated,
                product_sha=OLD_SHA,
                allow_legacy_project_omission=True,
            )

    with pytest.raises(AwsSafetyError, match="outside the requested shell state"):
        _validate_existing_shell_build_metadata(
            legacy,
            product_sha=OLD_SHA,
            allow_legacy_project_omission=False,
        )

    with pytest.raises(AwsSafetyError, match="outside the requested shell state"):
        _validate_existing_shell_build_metadata(
            {**legacy, "project": "another-project"},
            product_sha=OLD_SHA,
            allow_legacy_project_omission=True,
        )


def test_receipt_binds_previous_versions_and_rejects_data_rollback(tmp_path: Path) -> None:
    _artifact(tmp_path)
    shell = freeze_shell(tmp_path, new_product_sha=NEW_SHA)
    outputs = {"BucketName": "bucket", "DistributionId": "DIST"}
    release_id, body = _receipt_document(
        old_product_sha=OLD_SHA,
        new_product_sha=NEW_SHA,
        shell=shell,
        outputs=outputs,
        stack_id="arn:aws:cloudformation:ap-northeast-1:123456789012:stack/demo/id",
        previous=[
            {"path": "index.html", "etag": '"old"', "version_id": "v1"},
            {
                "path": "build-metadata.json",
                "etag": '"metadata"',
                "version_id": "v2",
            },
        ],
        introduced_paths=["status.json"],
    )
    receipt = _validated_receipt(json.loads(body), release_id=release_id)
    assert receipt["previous_versions"] == [
        {"path": "index.html", "etag": '"old"', "version_id": "v1"},
        {
            "path": "build-metadata.json",
            "etag": '"metadata"',
            "version_id": "v2",
        },
    ]
    assert receipt["introduced_paths"] == ["status.json"]

    mutated = json.loads(body)
    mutated["previous_versions"][0]["path"] = "data/current.json"
    with pytest.raises(AwsSafetyError, match="SHELL_ROLLBACK_RECEIPT_INVALID"):
        _validated_receipt(mutated, release_id=release_id)

    mutated = json.loads(body)
    mutated["introduced_paths"][0] = "data/current.json"
    with pytest.raises(AwsSafetyError, match="SHELL_ROLLBACK_RECEIPT_INVALID"):
        _validated_receipt(mutated, release_id=release_id)

    mutated = json.loads(body)
    mutated["introduced_paths"][0] = "index.html"
    with pytest.raises(AwsSafetyError, match="SHELL_ROLLBACK_RECEIPT_INVALID"):
        _validated_receipt(mutated, release_id=release_id)

    mutated = json.loads(body)
    mutated["introduced_paths"] = []
    with pytest.raises(AwsSafetyError, match="SHELL_ROLLBACK_RECEIPT_INVALID"):
        _validated_receipt(mutated, release_id=release_id)


@pytest.mark.parametrize("path", ["data/current.json", "data/snapshots/a.json", "/index.html"])
def test_shell_path_guard_is_shared_by_release_and_rollback(path: str) -> None:
    with pytest.raises(AwsSafetyError, match="SHELL_RELEASE_PATH_FORBIDDEN"):
        _safe_shell_path(path)


def test_rollback_uses_versioned_copy_and_never_delete_object() -> None:
    source = inspect.getsource(rollback_existing_shell)
    assert '"copy-object"' in source
    assert "delete-object" not in source
    assert 'item["path"] == "build-metadata.json"' in source
    assert "SHELL_ROLLBACK_INTRODUCED_OBJECT_MISMATCH" in source
    assert "RetainedIntroducedCount" in source
    assert "SHELL_ROLLBACK_VERSION_READBACK_MISMATCH" in source
    assert "SHELL_ROLLBACK_RECEIPT_DIGEST_MISMATCH" in source
