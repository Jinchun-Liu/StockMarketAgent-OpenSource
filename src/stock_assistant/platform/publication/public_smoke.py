"""Reusable fail-closed public smoke for one immutable preview snapshot."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from stock_assistant.contracts.public_snapshots import (
    PublicSnapshotManifestV1_1,
    PublicSnapshotPointerV1,
    validate_public_snapshot_bundle,
)
from stock_assistant.modules.briefings.projection import (
    validate_briefing_evidence_projection,
)
from stock_assistant.platform.publication.public_safety import scan_public_json

_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
_MAX_DOCUMENT_BYTES: Final = 10 * 1024 * 1024
_MAX_BUNDLE_BYTES: Final = 32 * 1024 * 1024
_CLOCK_SKEW: Final = timedelta(minutes=5)
_CURRENT_CACHE: Final = frozenset({"no-store", "max-age=0", "must-revalidate"})
_IMMUTABLE_CACHE: Final = frozenset({"public", "max-age=31536000", "immutable"})
_HSTS: Final = frozenset({"max-age=31536000", "includesubdomains", "preload"})


class ContinuousPreviewSmokeError(RuntimeError):
    """A public response crossed the reviewed snapshot delivery boundary."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, file_pointer, code, message, headers, new_url
        return None


def _header_map(items: list[tuple[str, str]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_name, value in items:
        name = raw_name.casefold()
        if name in headers:
            raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_HEADER_DUPLICATED")
        headers[name] = value
    return headers


def _base_host(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as error:
        raise ContinuousPreviewSmokeError("PUBLIC_BASE_URL_INVALID") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ContinuousPreviewSmokeError("PUBLIC_BASE_URL_INVALID")
    return parsed.hostname


def _https_get(url: str, *, expected_host: str) -> tuple[int, bytes, dict[str, str]]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_URL_ESCAPED_HOST")
    request = Request(  # noqa: S310 - exact HTTPS host is checked immediately above.
        url,
        headers={"User-Agent": "StockMarketAgent-continuous-preview-smoke/1"},
    )
    try:
        with build_opener(_RejectRedirects()).open(request, timeout=15) as response:
            return (
                response.status,
                response.read(_MAX_DOCUMENT_BYTES + 1),
                _header_map(list(response.headers.items())),
            )
    except HTTPError as error:
        return (
            error.code,
            error.read(_MAX_DOCUMENT_BYTES + 1),
            _header_map(list(error.headers.items())),
        )
    except URLError as error:
        raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_REQUEST_FAILED") from error


def _directives(value: str, *, delimiter: str, label: str) -> dict[str, tuple[str, ...]]:
    parsed: dict[str, tuple[str, ...]] = {}
    for raw in value.split(delimiter):
        tokens = tuple(part.casefold() for part in raw.strip().split())
        if not tokens:
            continue
        name = tokens[0]
        if name in parsed:
            raise ContinuousPreviewSmokeError(f"{label}_DUPLICATED")
        parsed[name] = tokens[1:]
    return parsed


def _flat_directives(value: str, *, delimiter: str, label: str) -> frozenset[str]:
    parts = [part.strip().casefold() for part in value.split(delimiter) if part.strip()]
    if any(any(character.isspace() for character in part) for part in parts):
        raise ContinuousPreviewSmokeError(f"{label}_MALFORMED")
    if len(parts) != len(set(parts)):
        raise ContinuousPreviewSmokeError(f"{label}_DUPLICATED")
    return frozenset(parts)


def _validate_headers(headers: dict[str, str], *, path: str, immutable: bool) -> None:
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if (
        content_type != "application/json"
        or headers.get("x-content-type-options", "").strip().casefold() != "nosniff"
    ):
        raise ContinuousPreviewSmokeError(f"PUBLIC_SNAPSHOT_HEADERS_INVALID:{path}")

    csp = _directives(
        headers.get("content-security-policy", ""),
        delimiter=";",
        label="PUBLIC_CSP",
    )
    if (
        csp.get("default-src") != ("'self'",)
        or csp.get("connect-src") != ("'self'",)
        or csp.get("object-src") != ("'none'",)
        or csp.get("frame-ancestors") != ("'none'",)
    ):
        raise ContinuousPreviewSmokeError(f"PUBLIC_SNAPSHOT_CSP_INVALID:{path}")

    hsts_parts = _flat_directives(
        headers.get("strict-transport-security", ""),
        delimiter=";",
        label="PUBLIC_HSTS",
    )
    if hsts_parts != _HSTS:
        raise ContinuousPreviewSmokeError(f"PUBLIC_SNAPSHOT_HSTS_INVALID:{path}")

    cache_parts = _flat_directives(
        headers.get("cache-control", ""),
        delimiter=",",
        label="PUBLIC_CACHE_CONTROL",
    )
    expected = _IMMUTABLE_CACHE if immutable else _CURRENT_CACHE
    if cache_parts != expected:
        code = "PUBLIC_SNAPSHOT_CACHE_INVALID" if immutable else "PUBLIC_POINTER_CACHE_INVALID"
        raise ContinuousPreviewSmokeError(f"{code}:{path}")


def _read_json(
    *,
    base_url: str,
    expected_host: str,
    path: str,
    immutable: bool,
) -> bytes:
    status, document, headers = _https_get(
        f"{base_url.rstrip('/')}/{path}",
        expected_host=expected_host,
    )
    if status != 200 or not document or len(document) > _MAX_DOCUMENT_BYTES:
        raise ContinuousPreviewSmokeError(f"PUBLIC_SNAPSHOT_RESPONSE_INVALID:{path}")
    _validate_headers(headers, path=path, immutable=immutable)
    scan_public_json(document, label=f"public smoke {path}")
    return document


def smoke_snapshot(
    *,
    base_url: str,
    expected_product_sha: str,
    expected_snapshot_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Fetch one exact pointer winner and prove its full public bundle is current."""

    if _SHA.fullmatch(expected_product_sha) is None:
        raise ContinuousPreviewSmokeError("EXPECTED_PRODUCT_SHA_INVALID")
    host = _base_host(base_url)
    pointer_document = _read_json(
        base_url=base_url,
        expected_host=host,
        path="data/current.json",
        immutable=False,
    )
    pointer = PublicSnapshotPointerV1.model_validate_json(pointer_document)
    if pointer.product_commit != expected_product_sha:
        raise ContinuousPreviewSmokeError("PUBLIC_POINTER_PRODUCT_SHA_MISMATCH")
    if expected_snapshot_id is not None and str(pointer.snapshot_id) != expected_snapshot_id:
        raise ContinuousPreviewSmokeError("PUBLIC_POINTER_SNAPSHOT_ID_MISMATCH")
    manifest_document = _read_json(
        base_url=base_url,
        expected_host=host,
        path=pointer.manifest_path,
        immutable=True,
    )
    manifest = PublicSnapshotManifestV1_1.model_validate_json(manifest_document)
    asset_documents: dict[str, bytes] = {}
    total_size = len(pointer_document) + len(manifest_document)
    for asset in manifest.assets:
        document = _read_json(
            base_url=base_url,
            expected_host=host,
            path=asset.path,
            immutable=True,
        )
        total_size += len(document)
        if total_size > _MAX_BUNDLE_BYTES:
            raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_BUNDLE_TOO_LARGE")
        asset_documents[asset.path] = document
    validated = validate_public_snapshot_bundle(
        pointer_document=pointer_document,
        manifest_document=manifest_document,
        asset_documents=asset_documents,
    )
    validate_briefing_evidence_projection(
        briefing=validated.briefing,
        evidence_by_event_id=validated.evidence_by_event_id,
    )
    observed_at = datetime.now(UTC) if now is None else now
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ContinuousPreviewSmokeError("SMOKE_TIME_MUST_BE_TIMEZONE_AWARE")
    if pointer.published_at > observed_at + _CLOCK_SKEW:
        raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_PUBLISHED_IN_FUTURE")
    if manifest.fresh_until < observed_at:
        raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_NOT_FRESH")
    return {
        "snapshot_id": str(pointer.snapshot_id),
        "product_commit": pointer.product_commit,
        "published_at": pointer.published_at.isoformat(),
        "fresh_until": manifest.fresh_until.isoformat(),
        "origin_mode": manifest.origin_mode.value,
        "completeness": manifest.completeness.value,
        "run_status": validated.run_status.status.value,
    }


def smoke_snapshot_with_retry(
    *,
    base_url: str,
    expected_product_sha: str,
    expected_snapshot_id: str,
    attempts: int,
) -> dict[str, str]:
    """Retry a just-published CloudFront winner a small, fixed number of times."""

    if not 1 <= attempts <= 5:
        raise ValueError("public smoke attempts must be between one and five")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return smoke_snapshot(
                base_url=base_url,
                expected_product_sha=expected_product_sha,
                expected_snapshot_id=expected_snapshot_id,
            )
        except (ContinuousPreviewSmokeError, OSError, ValueError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 4))
    raise ContinuousPreviewSmokeError("PUBLIC_SNAPSHOT_SMOKE_RETRIES_EXHAUSTED") from last_error


__all__ = [
    "ContinuousPreviewSmokeError",
    "smoke_snapshot",
    "smoke_snapshot_with_retry",
]
