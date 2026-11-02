from __future__ import annotations

from datetime import timedelta

import pytest
from tests.unit.qir.test_continuous_preview_runtime import (
    _briefing_item_payload,
    _mutate_briefing_bundle,
    _official_bundle,
)

from stock_assistant.platform.publication import public_smoke as smoke_module
from stock_assistant.platform.publication.snapshot_bundle import SnapshotDocuments


def _headers(*, immutable: bool) -> dict[str, str]:
    return {
        "cache-control": (
            "public,max-age=31536000,immutable"
            if immutable
            else "no-store,max-age=0,must-revalidate"
        ),
        "content-security-policy": (
            "default-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        ),
        "content-type": "application/json",
        "strict-transport-security": "max-age=31536000; includeSubDomains; preload",
        "x-content-type-options": "nosniff",
    }


def _install_bundle(monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotDocuments) -> None:
    documents = {
        "data/current.json": snapshot.pointer_document,
        snapshot.pointer.manifest_path: snapshot.manifest_document,
        **snapshot.asset_documents,
    }

    def fake_get(
        url: str,
        *,
        expected_host: str,
    ) -> tuple[int, bytes, dict[str, str]]:
        assert expected_host == "preview.example.com"
        path = url.removeprefix("https://preview.example.com/")
        return 200, documents[path], _headers(immutable=path != "data/current.json")

    monkeypatch.setattr(smoke_module, "_https_get", fake_get)


def test_smoke_validates_full_bundle_freshness_hashes_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _official_bundle()
    _install_bundle(monkeypatch, snapshot)

    result = smoke_module.smoke_snapshot(
        base_url="https://preview.example.com",
        expected_product_sha=snapshot.pointer.product_commit,
        now=snapshot.pointer.published_at,
    )

    assert result["snapshot_id"] == str(snapshot.pointer.snapshot_id)
    assert result["run_status"] in {"succeeded", "degraded"}


def test_smoke_rejects_stale_snapshot_and_cross_host_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _official_bundle()
    _install_bundle(monkeypatch, snapshot)

    with pytest.raises(smoke_module.ContinuousPreviewSmokeError, match="NOT_FRESH"):
        smoke_module.smoke_snapshot(
            base_url="https://preview.example.com",
            expected_product_sha=snapshot.pointer.product_commit,
            now=snapshot.manifest.fresh_until + timedelta(seconds=1),
        )
    with pytest.raises(smoke_module.ContinuousPreviewSmokeError, match="BASE_URL_INVALID"):
        smoke_module.smoke_snapshot(
            base_url="https://preview.example.com/data",
            expected_product_sha=snapshot.pointer.product_commit,
            now=snapshot.pointer.published_at,
        )


@pytest.mark.parametrize(
    ("header", "value"),
    [
        (
            "content-security-policy",
            "default-src 'self'; connect-src 'self'; x-object-src 'none'; frame-ancestors 'none'",
        ),
        (
            "content-security-policy",
            "default-src 'self'; connect-src 'self'; object-src 'none' https://evil; "
            "frame-ancestors 'none'",
        ),
        (
            "content-security-policy",
            "default-src 'self'; connect-src 'self'; object-src 'none'; "
            "object-src https://evil; frame-ancestors 'none'",
        ),
        (
            "strict-transport-security",
            "max-age=315360000; includeSubDomains; preload",
        ),
        (
            "strict-transport-security",
            "max-age=31536000; includeSubDomainsX; preload",
        ),
        ("cache-control", "x-no-store,max-age=00,must-revalidate"),
        ("cache-control", "private,max-age=315360000,immutable"),
    ],
)
def test_security_header_parser_rejects_substring_spoofs(
    header: str,
    value: str,
) -> None:
    headers = _headers(immutable=False)
    headers[header] = value
    with pytest.raises(smoke_module.ContinuousPreviewSmokeError):
        smoke_module._validate_headers(
            headers,
            path="data/current.json",
            immutable=False,
        )


def test_security_header_parser_accepts_case_order_and_whitespace_semantics() -> None:
    headers = _headers(immutable=True)
    headers["content-security-policy"] = (
        "FRAME-ANCESTORS   'NONE' ; OBJECT-SRC 'NONE'; CONNECT-SRC 'SELF'; DEFAULT-SRC 'SELF'"
    )
    headers["strict-transport-security"] = "PRELOAD; includeSUBDOMAINS; MAX-AGE=31536000"
    headers["cache-control"] = " immutable , MAX-AGE=31536000 , PUBLIC "

    smoke_module._validate_headers(headers, path="immutable.json", immutable=True)


def test_response_header_map_rejects_case_insensitive_duplicates() -> None:
    with pytest.raises(
        smoke_module.ContinuousPreviewSmokeError,
        match="HEADER_DUPLICATED",
    ):
        smoke_module._header_map(
            [
                ("Content-Security-Policy", "default-src 'self'"),
                ("content-security-policy", "default-src https://evil"),
            ]
        )


def test_smoke_rejects_resealed_briefing_evidence_projection_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _mutate_briefing_bundle(
        _official_bundle(),
        lambda payload: _briefing_item_payload(payload).__setitem__(
            "headline",
            "resealed but false headline",
        ),
    )
    _install_bundle(monkeypatch, snapshot)

    with pytest.raises(ValueError, match="exact evidence projection"):
        smoke_module.smoke_snapshot(
            base_url="https://preview.example.com",
            expected_product_sha=snapshot.pointer.product_commit,
            now=snapshot.pointer.published_at,
        )
