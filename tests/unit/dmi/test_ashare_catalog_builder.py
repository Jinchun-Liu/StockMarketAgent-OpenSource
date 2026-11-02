from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml
from scripts.build_ashare_instrument_catalog import (
    OFFICIAL_IDENTITY_ALIASES,
    SOURCE_IDS,
    _apply_official_identity_aliases,
    _bounded_response_sha256,
    _deduplicate_official_rows,
    _rights_snapshots,
)


class _ChunkedOfficialDocument(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.consumed = 0

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


def test_directory_builder_requires_exact_fetch_and_retain_rights(tmp_path: Path) -> None:
    source_path = Path("configs/sources.yaml")
    snapshots = _rights_snapshots(source_path)

    assert set(snapshots) == set(SOURCE_IDS)
    assert all(len(str(item["sha256"])) == 64 for item in snapshots.values())

    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    target = next(item for item in document["sources"] if item["id"] == SOURCE_IDS[0])
    target["data_rights"]["permitted_operations"] = ["fetch"]
    blocked_path = tmp_path / "sources.yaml"
    blocked_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="does not permit private catalog retention"):
        _rights_snapshots(blocked_path)


def test_directory_builder_requires_alias_disclosure_hosts_before_network(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(Path("configs/sources.yaml").read_text(encoding="utf-8"))
    target = next(
        item
        for item in document["sources"]
        if item["id"] == "szse_official_identity_change_disclosures"
    )
    target["dev_live"]["allowed_hosts"] = ["www.szse.cn"]
    blocked_path = tmp_path / "sources.yaml"
    blocked_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="allowlist does not cover fixed inputs"):
        _rights_snapshots(blocked_path)


def test_alias_disclosure_hashing_aborts_before_buffering_oversized_body() -> None:
    stream = _ChunkedOfficialDocument((b"a" * 4, b"b" * 4, b"c", b"unread"))
    response = httpx.Response(200, stream=stream)

    with pytest.raises(ValueError, match="official alias disclosure size is invalid"):
        _bounded_response_sha256(response, instrument_id="XSHE:001872", max_bytes=8)

    assert stream.consumed == 3


def test_alias_disclosure_hashing_accepts_bounded_stream() -> None:
    content = b"official exchange disclosure"
    stream = _ChunkedOfficialDocument((content[:8], content[8:]))
    response = httpx.Response(200, stream=stream)

    assert (
        _bounded_response_sha256(
            response,
            instrument_id="XSHE:001872",
            max_bytes=len(content),
        )
        == hashlib.sha256(content).hexdigest()
    )


def test_directory_builder_only_deduplicates_agreeing_identity_rows() -> None:
    first = {
        "instrument_id": "XSHG:600001",
        "short_name": "邯郸钢铁",
        "listing_status": "delisted",
        "listed_at": "1998-01-22",
        "delisted_at": "2009-12-29",
        "source_id": SOURCE_IDS[0],
    }
    later_duplicate = {**first, "listed_at": "1999-06-09"}

    assert _deduplicate_official_rows([later_duplicate, first]) == [first]
    with pytest.raises(ValueError, match="conflicting official directory rows"):
        _deduplicate_official_rows([first, {**later_duplicate, "short_name": "另一家公司"}])


def test_directory_builder_attaches_only_official_code_change_aliases() -> None:
    rows = [
        {
            "instrument_id": instrument_id,
            "legacy_symbols": [],
            "legacy_names": [],
            "alias_evidence_ids": [],
        }
        for instrument_id in OFFICIAL_IDENTITY_ALIASES
    ]
    evidence = [
        {
            "evidence_id": f"evidence-{index}",
            "instrument_id": instrument_id,
            "legacy_symbol": legacy_symbol,
            "legacy_name": legacy_name,
            "source_id": source_id,
            "source_url": source_url,
        }
        for index, (
            instrument_id,
            (legacy_symbol, legacy_name, source_id, source_url),
        ) in enumerate(OFFICIAL_IDENTITY_ALIASES.items())
    ]

    enriched = _apply_official_identity_aliases(rows, evidence)

    by_identifier = {str(item["instrument_id"]): item for item in enriched}
    for index, (
        instrument_id,
        (legacy_symbol, legacy_name, _, _),
    ) in enumerate(OFFICIAL_IDENTITY_ALIASES.items()):
        assert by_identifier[instrument_id]["legacy_symbols"] == [legacy_symbol]
        assert by_identifier[instrument_id]["legacy_names"] == [legacy_name]
        assert by_identifier[instrument_id]["alias_evidence_ids"] == [f"evidence-{index}"]


def test_bundled_alias_evidence_matches_current_rights_snapshots() -> None:
    payload = json.loads(
        gzip.decompress(
            Path("resources/instruments/ashare-catalog.dev.v1.json.gz").read_bytes()
        ).decode("utf-8")
    )
    rights = _rights_snapshots(Path("configs/sources.yaml"))
    receipts = {item["source_id"]: item for item in payload["sources"]}

    for evidence in payload["alias_evidence"]:
        source_id = evidence["source_id"]
        assert evidence["rights_snapshot_sha256"] == rights[source_id]["sha256"]
        assert evidence["rights_snapshot_sha256"] == receipts[source_id]["rights_snapshot_sha256"]
        assert evidence["retention_until"] == payload["expires_at"]
