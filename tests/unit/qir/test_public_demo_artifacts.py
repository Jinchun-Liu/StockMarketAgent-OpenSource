from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.public_demo_artifacts import (
    STATIC_NON_REQUEST_PREFIXES,
    STATIC_NON_REQUEST_URLS,
    PublicArtifactError,
    _version_public_static_assets,
    scan_public_tree,
    verify_public_pair,
)


def _decoded(value: str) -> str:
    return bytes.fromhex(value).decode("ascii")


def _snapshot(root: Path) -> Path:
    root.mkdir()
    (root / "evidence").mkdir()
    values: dict[str, object] = {
        "active-target.json": {"data": {"found": True}},
        "latest-briefing.json": {
            "data": {
                "runtime_mode": "public_static",
                "release_tier": "dev_only",
                "rights_status": "not_applicable",
            }
        },
        "evidence-index.json": {"schema_version": "1.0", "events": []},
        "build-metadata.json": {
            "schema_version": "1.0",
            "project": "StockMarketAgent",
            "product_commit": "a" * 40,
            "generated_at": "2026-07-31T01:05:00Z",
            "runtime_mode": "PUBLIC_STATIC",
            "release_tier": "DEV_ONLY",
            "data_mode": "DEMO_REPLAY",
            "instrument_id": "XSHG:603986",
        },
        "evidence/10000000-0000-4000-8000-000000000001.json": {
            "data": {
                "runtime_mode": "public_static",
                "release_tier": "dev_only",
                "rights_status": "not_applicable",
                "source_url": (
                    "https://example.com/stockmarketagent/synthetic-evidence/"
                    "00000000-0000-4000-8000-000000000001"
                ),
            }
        },
    }
    for name, value in values.items():
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value), encoding="utf-8")
    return root


def test_snapshot_scanner_accepts_only_bounded_public_shape(tmp_path: Path) -> None:
    result = scan_public_tree(_snapshot(tmp_path / "snapshot"), kind="snapshot")
    assert result["files"] == 5
    assert result["bytes"] > 0


def test_snapshot_scanner_rejects_sensitive_filename(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snapshot")
    (root / _decoded("7365637265742e656e76")).write_text("value=x", encoding="utf-8")
    with pytest.raises(PublicArtifactError, match="sensitive filename"):
        scan_public_tree(root, kind="snapshot")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{}", "unknown snapshot file"),
        ('{"746f6b656e":"value"}', "forbidden JSON field"),
        ('{"runtime_mode":"dev_live"}', "non-public runtime mode"),
        ('{"value":"666978747572652e696e76616c6964"}', "unknown snapshot file"),
    ],
)
def test_snapshot_scanner_rejects_unknown_or_unsafe_content(
    tmp_path: Path, content: str, message: str
) -> None:
    root = _snapshot(tmp_path / "snapshot")
    if "unknown snapshot" in message:
        destination = root / "extra.json"
    else:
        destination = root / "evidence/extra.json"
        if "746f6b656e" in content:
            content = json.dumps({_decoded("746f6b656e"): "value"})
    destination.write_text(content, encoding="utf-8")
    with pytest.raises(PublicArtifactError, match=message):
        scan_public_tree(root, kind="snapshot")


def test_scanner_rejects_private_reference_and_loopback_api(tmp_path: Path) -> None:
    for name, value, message in (
        ("one", _decoded("68747470733a2f2f666978747572652e696e76616c6964"), "private runtime"),
        ("two", "http://127.0.0.1:8000/api/v1", "loopback API"),
        ("three", "https://localhost:3000/", "loopback API"),
    ):
        root = _snapshot(tmp_path / name)
        (root / "evidence/extra.json").write_text(json.dumps({"value": value}))
        with pytest.raises(PublicArtifactError, match=message):
            scan_public_tree(root, kind="snapshot")


def test_scanner_rejects_recursive_symlink(tmp_path: Path) -> None:
    root = _snapshot(tmp_path / "snapshot")
    (root / "evidence/linked.json").symlink_to(root / "active-target.json")
    with pytest.raises(PublicArtifactError, match="symlink"):
        scan_public_tree(root, kind="snapshot")


def test_artifact_scanner_accepts_next_export_files(tmp_path: Path) -> None:
    root = tmp_path / "out"
    (root / "_next/static").mkdir(parents=True)
    (root / "index.html").write_text("<main>PUBLIC_STATIC</main>", encoding="utf-8")
    (root / "404.html").write_text("not found", encoding="utf-8")
    (root / "_next/static/app.js").write_text("console.log('static')", encoding="utf-8")
    assert scan_public_tree(root, kind="artifact")["files"] == 3


def test_public_static_assets_move_to_the_release_prefix(tmp_path: Path) -> None:
    sha = "b" * 40
    root = tmp_path / "out"
    source = root / "_next/static/chunks"
    source.mkdir(parents=True)
    (source / "app.js").write_text("client", encoding="utf-8")
    expected = f"/_next/static/releases/{sha}/_next/static/"
    (root / "index.html").write_text(f'<script src="{expected}chunks/app.js"></script>')

    _version_public_static_assets(root, sha)

    versioned = root / f"_next/static/releases/{sha}/_next/static/chunks/app.js"
    assert versioned.read_text(encoding="utf-8") == "client"
    assert not (root / "_next/static/chunks").exists()


def test_public_static_assets_reject_mismatched_export_reference(tmp_path: Path) -> None:
    sha = "c" * 40
    root = tmp_path / "out"
    (root / "_next/static/chunks").mkdir(parents=True)
    (root / "_next/static/chunks/app.js").write_text("client", encoding="utf-8")
    (root / "index.html").write_text('<script src="/_next/static/chunks/app.js"></script>')

    with pytest.raises(PublicArtifactError, match="asset prefix mismatch"):
        _version_public_static_assets(root, sha)


def test_artifact_scanner_rejects_binary_files_instead_of_skipping_them(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "index.html").write_text("ok", encoding="utf-8")
    (root / "font.woff2").write_bytes(b"wOF2")
    with pytest.raises(PublicArtifactError, match="unknown artifact extension"):
        scan_public_tree(root, kind="artifact")


@pytest.mark.parametrize(
    "secret_like",
    [
        "-----BEGIN" + " PRIVATE KEY-----",
        "AKIA" + "A" * 16,
        "ASIA" + "B" * 16,
        "aws_secret_access_key=" + "c" * 12,
        "access_token=" + "d" * 12,
        "cookie=" + "e" * 12,
    ],
)
def test_static_public_scanner_rejects_shared_secret_signatures(
    tmp_path: Path,
    secret_like: str,
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "index.html").write_text(secret_like, encoding="utf-8")
    with pytest.raises(PublicArtifactError, match="secret-like"):
        scan_public_tree(root, kind="artifact")


def test_static_non_request_url_allowlist_is_reasoned_and_exact(tmp_path: Path) -> None:
    assert all(reason.strip() for reason in STATIC_NON_REQUEST_URLS.values())
    assert all(reason.strip() for reason in STATIC_NON_REQUEST_PREFIXES.values())
    root = tmp_path / "out"
    root.mkdir()
    allowed = [
        *STATIC_NON_REQUEST_URLS,
        *(f"{prefix.rstrip('/')}/locked-reference" for prefix in STATIC_NON_REQUEST_PREFIXES),
    ]
    (root / "index.html").write_text("\n".join(allowed), encoding="utf-8")
    assert scan_public_tree(root, kind="artifact")["files"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://tracker.example/collect",
        "https://a.evil.example/",
        "http://10.0.0.8/private",
        "https://github.com/another-owner/project",
        "https://github.com.evil.example/zloirock/core-js",
        "https://example.com.evil.example/stockmarketagent/synthetic-evidence/"
        "00000000-0000-4000-8000-000000000001",
        "https://nextjs.org/telemetry",
    ],
)
def test_artifact_scanner_rejects_every_unapproved_absolute_url(
    tmp_path: Path,
    url: str,
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "index.html").write_text(url, encoding="utf-8")
    with pytest.raises(PublicArtifactError, match="unapproved absolute URL"):
        scan_public_tree(root, kind="artifact")


def test_json_decoded_and_protocol_relative_urls_share_the_fail_closed_gate(
    tmp_path: Path,
) -> None:
    cases = (
        '{"value":"https:\\/\\/tracker.example\\/collect"}',
        '{"value":"//tracker.example/collect"}',
        '<script src="//tracker.example/collect"></script>',
    )
    for index, content in enumerate(cases):
        root = tmp_path / f"out-{index}"
        root.mkdir()
        suffix = ".json" if content.startswith("{") else ".html"
        (root / f"payload{suffix}").write_text(content, encoding="utf-8")
        with pytest.raises(
            PublicArtifactError,
            match=r"unapproved absolute URL|protocol-relative URL",
        ):
            scan_public_tree(root, kind="artifact")


@pytest.mark.parametrize(
    "content",
    [
        r'const endpoint = "https:\/\/tracker.example\/collect";',
        r'const endpoint = "https:\u002f\u002ftracker.example\u002fcollect";',
        r'const endpoint = "https:\U002F\U002Fevil.example\U002Fcollect";',
        r'const endpoint = "https:\x2f\x2ftracker.example\x2fcollect";',
        r'const endpoint = "https:\X2F\X2Fexample.com.evil.example\X2Fcollect";',
        r'const endpoint = "\u002f\x2ftracker.example\/collect";',
        r'const endpoint = "https:\/\/github.com.evil.example\/zloirock\/core-js";',
    ],
)
def test_javascript_url_slash_escapes_cannot_bypass_the_url_gate(
    tmp_path: Path,
    content: str,
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "bundle.js").write_text(content, encoding="utf-8")
    with pytest.raises(
        PublicArtifactError,
        match=r"unapproved absolute URL|protocol-relative URL",
    ):
        scan_public_tree(root, kind="artifact")


def test_normal_javascript_slash_escapes_are_not_urls(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    (root / "bundle.js").write_text(
        r'const paths = ["folder\/file", "folder\u002Ffile", "folder\x2ffile"];',
        encoding="utf-8",
    )
    assert scan_public_tree(root, kind="artifact")["files"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "//evil/path",
        "//evil_host/path",
        "//evil./path",
        "//user@evil/path",
    ],
)
def test_protocol_relative_authority_is_rejected_in_all_url_string_contexts(
    tmp_path: Path,
    url: str,
) -> None:
    contents = (
        json.dumps({"value": url}),
        f'const endpoint = "{url}";',
        f"const endpoint = `{url}`;",
        f"body {{ background-image: url({url}); }}",
        f"<script src={url}></script>",
    )
    for index, content in enumerate(contents):
        root = tmp_path / f"out-{index}"
        root.mkdir()
        extension = ".json" if index == 0 else ".js"
        (root / f"content{extension}").write_text(content, encoding="utf-8")
        with pytest.raises(PublicArtifactError, match="protocol-relative URL"):
            scan_public_tree(root, kind="artifact")


def test_javascript_comments_and_locked_allowlist_urls_are_not_protocol_relative(
    tmp_path: Path,
) -> None:
    root = tmp_path / "out"
    root.mkdir()
    content = "\n".join(
        [
            "// ordinary build comment",
            "const value = 1; // another legal comment",
            "/* // comment-only.invalid/path */",
            *STATIC_NON_REQUEST_URLS,
            *(f"{prefix.rstrip('/')}/locked-reference" for prefix in STATIC_NON_REQUEST_PREFIXES),
        ]
    )
    (root / "bundle.js").write_text(content, encoding="utf-8")
    assert scan_public_tree(root, kind="artifact")["files"] == 1


def test_verify_pair_binds_identical_metadata_and_sha(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path / "snapshot")
    artifact = tmp_path / "out"
    artifact.mkdir()
    (artifact / "index.html").write_text("ok", encoding="utf-8")
    (artifact / "build-metadata.json").write_bytes((snapshot / "build-metadata.json").read_bytes())
    result = verify_public_pair(snapshot, artifact, "a" * 40)
    assert result["product_sha"] == "a" * 40

    metadata = json.loads((artifact / "build-metadata.json").read_text(encoding="utf-8"))
    metadata["product_commit"] = "b" * 40
    (artifact / "build-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(PublicArtifactError, match="metadata mismatch"):
        verify_public_pair(snapshot, artifact, "a" * 40)
