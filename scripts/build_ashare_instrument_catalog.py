"""Build the immutable development A-share identity catalog.

This command is intentionally separate from application startup. Before any network
I/O it verifies exact non-commercial FETCH/RETAIN grants in ``configs/sources.yaml``.
It then normalizes official exchange directories and writes a time-bounded snapshot
that the API can search without runtime network access.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlparse
from uuid import UUID, uuid5

import akshare as ak
import httpx
import yaml

CATALOG_VERSION: Final = "ashare-directory.dev.v1.1.20260831"
SOURCE_MODE: Final = "DEV_ONLY/UNVERIFIED"
SSE_URL: Final = "https://www.sse.com.cn/assortment/stock/list/share/"
SZSE_URL: Final = "https://www.szse.cn/market/product/stock/list/index.html"
BSE_URL: Final = "https://www.bse.cn/nq/listedcompany.html"
SSE_SOURCE_ID: Final = "sse_official_instrument_directory"
SZSE_SOURCE_ID: Final = "szse_official_instrument_directory"
BSE_SOURCE_ID: Final = "bse_official_instrument_directory"
SZSE_ALIAS_SOURCE_ID: Final = "szse_official_identity_change_disclosures"
SSE_ALIAS_SOURCE_ID: Final = "sse_official_identity_change_disclosures"
SOURCE_IDS: Final = (
    SSE_SOURCE_ID,
    SZSE_SOURCE_ID,
    BSE_SOURCE_ID,
    SZSE_ALIAS_SOURCE_ID,
    SSE_ALIAS_SOURCE_ID,
)
SOURCE_CAPABILITIES: Final = {
    SSE_SOURCE_ID: "instrument_directory",
    SZSE_SOURCE_ID: "instrument_directory",
    BSE_SOURCE_ID: "instrument_directory",
    SZSE_ALIAS_SOURCE_ID: "instrument_identity_alias",
    SSE_ALIAS_SOURCE_ID: "instrument_identity_alias",
}
_ALIAS_EVIDENCE_NAMESPACE: Final = UUID("12dd63a3-b4d3-53d9-80ab-9500d6310dda")
_MAX_ALIAS_DOCUMENT_BYTES: Final = 20 * 1024 * 1024
EXPECTED_CANONICAL_COUNTS: Final = {"XSHG": 2_461, "XSHE": 3_084, "XBSE": 339}
EXPECTED_LISTING_COUNTS: Final = {"active": 5_551, "delisted": 333}
OFFICIAL_IDENTITY_ALIASES: Final = {
    "XSHE:001872": (
        "000022",
        "深赤湾A",
        SZSE_ALIAS_SOURCE_ID,
        "https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-08-20/"
        "f00e40bd-6bfe-4cc5-8d83-794d18016c17.PDF",
    ),
    "XSHE:001914": (
        "000043",
        "中航善达",
        SZSE_ALIAS_SOURCE_ID,
        "https://www.szse.cn/disclosure/notice/general/t20191211_572534.html",
    ),
    "XSHE:302132": (
        "300114",
        "中航电测",
        SZSE_ALIAS_SOURCE_ID,
        "https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-15/"
        "cedb693a-f5ee-4463-9682-ea33d406b569.PDF",
    ),
    "XSHG:601607": (
        "600849",
        "上海医药",
        SSE_ALIAS_SOURCE_ID,
        "https://static.sse.com.cn/bond/bridge2/disclosure/announcement/c/202505/"
        "46567a_20250514_IAXU.pdf",
    ),
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        default=_json_scalar,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_scalar(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"unsupported catalog source scalar: {type(value).__name__}")


def _date(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"nan", "nat", "none"}:
        return None
    return date.fromisoformat(normalized[:10]).isoformat()


def _row(
    *,
    exchange: str,
    symbol: str,
    short_name: str,
    legal_name: str,
    legal_name_verified: bool,
    board: str,
    listed_at: object,
    delisted_at: object = None,
    source_id: str,
    retrieved_at: datetime,
    legacy_symbols: Iterable[str] = (),
    legacy_names: Iterable[str] = (),
    alias_evidence_ids: Iterable[str] = (),
) -> dict[str, object]:
    if exchange not in {"XSHG", "XSHE", "XBSE"}:
        raise ValueError("unsupported exchange")
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError(f"invalid A-share symbol: {symbol}")
    listed = _date(listed_at)
    delisted = _date(delisted_at)
    return {
        "instrument_id": f"{exchange}:{symbol}",
        "short_name": short_name.strip(),
        "legal_name": legal_name.strip(),
        "legal_name_verified": legal_name_verified,
        "listing_status": "delisted" if delisted is not None else "active",
        "board": board,
        "listed_at": listed,
        "delisted_at": delisted,
        "revision_known_at": retrieved_at.isoformat(),
        "source_id": source_id,
        "legacy_symbols": sorted(set(legacy_symbols)),
        "legacy_names": sorted(set(legacy_names)),
        "alias_evidence_ids": sorted(set(alias_evidence_ids)),
    }


def _sse_rows(retrieved_at: datetime) -> tuple[list[dict[str, object]], list[object]]:
    normalized: list[dict[str, object]] = []
    source_rows: list[object] = []
    for symbol, board in (("主板A股", "sse_main"), ("科创板", "sse_star")):
        frame = ak.stock_info_sh_name_code(symbol=symbol)
        for item in frame.to_dict("records"):
            source_rows.append(item)
            normalized.append(
                _row(
                    exchange="XSHG",
                    symbol=str(item["证券代码"]),
                    short_name=str(item["证券简称"]),
                    legal_name=str(item["公司全称"]),
                    legal_name_verified=True,
                    board=board,
                    listed_at=item["上市日期"],
                    source_id=SSE_SOURCE_ID,
                    retrieved_at=retrieved_at,
                )
            )
    return normalized, source_rows


def _szse_rows(retrieved_at: datetime) -> tuple[list[dict[str, object]], list[object]]:
    frame = ak.stock_info_sz_name_code(symbol="A股列表")
    normalized: list[dict[str, object]] = []
    source_rows = frame.to_dict("records")
    board_map = {"主板": "szse_main", "创业板": "szse_chinext"}
    for item in source_rows:
        short_name = str(item["A股简称"])
        normalized.append(
            _row(
                exchange="XSHE",
                symbol=str(item["A股代码"]),
                short_name=short_name,
                legal_name=short_name,
                legal_name_verified=False,
                board=board_map.get(str(item["板块"]), "szse_a_share"),
                listed_at=item["A股上市日期"],
                source_id=SZSE_SOURCE_ID,
                retrieved_at=retrieved_at,
            )
        )
    return normalized, source_rows


def _bse_rows(retrieved_at: datetime) -> tuple[list[dict[str, object]], list[object]]:
    frame = ak.stock_info_bj_name_code()
    normalized: list[dict[str, object]] = []
    source_rows = frame.to_dict("records")
    for item in source_rows:
        symbol = str(item["证券代码"])
        short_name = str(item["证券简称"])
        legacy_symbols = ("430047",) if symbol == "920047" else ()
        normalized.append(
            _row(
                exchange="XBSE",
                symbol=symbol,
                short_name=short_name,
                legal_name=short_name,
                legal_name_verified=False,
                board="bse",
                listed_at=item["上市日期"],
                source_id=BSE_SOURCE_ID,
                retrieved_at=retrieved_at,
                legacy_symbols=legacy_symbols,
            )
        )
    return normalized, source_rows


def _official_delisted_rows(
    retrieved_at: datetime,
) -> tuple[list[dict[str, object]], list[object], list[object]]:
    sse_frame = ak.stock_info_sh_delist(symbol="全部")
    szse_frame = ak.stock_info_sz_delist(symbol="终止上市公司")
    sse_source = sse_frame.to_dict("records")
    szse_source = szse_frame.to_dict("records")
    normalized: list[dict[str, object]] = []
    for item in sse_source:
        symbol = str(item["公司代码"])
        if not symbol.startswith(("60", "68")):
            continue
        normalized.append(
            _row(
                exchange="XSHG",
                symbol=symbol,
                short_name=str(item["公司简称"]),
                legal_name=str(item["公司简称"]),
                legal_name_verified=False,
                board="historical_delisted",
                listed_at=item["上市日期"],
                delisted_at=item["暂停上市日期"],
                source_id=SSE_SOURCE_ID,
                retrieved_at=retrieved_at,
            )
        )
    for item in szse_source:
        symbol = str(item["证券代码"])
        if symbol.startswith("2"):
            continue
        normalized.append(
            _row(
                exchange="XSHE",
                symbol=symbol,
                short_name=str(item["证券简称"]),
                legal_name=str(item["证券简称"]),
                legal_name_verified=False,
                board="historical_delisted",
                listed_at=item["上市日期"],
                delisted_at=item["终止上市日期"],
                source_id=SZSE_SOURCE_ID,
                retrieved_at=retrieved_at,
            )
        )
    return normalized, sse_source, szse_source


def _rights_snapshots(path: Path) -> dict[str, dict[str, object]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = {item.get("id"): item for item in document.get("sources", [])}
    snapshots: dict[str, dict[str, object]] = {}
    for source_id in SOURCE_IDS:
        source = sources.get(source_id)
        if not isinstance(source, dict):
            raise ValueError(f"rights record missing for {source_id}")
        rights = source.get("data_rights")
        if not isinstance(rights, dict):
            raise ValueError(f"rights record invalid for {source_id}")
        dev_live = source.get("dev_live")
        if not isinstance(dev_live, dict):
            raise ValueError(f"runtime source record invalid for {source_id}")
        allowed_hosts = dev_live.get("allowed_hosts")
        canonical_url = dev_live.get("canonical_url")
        if (
            not isinstance(allowed_hosts, list)
            or not all(isinstance(item, str) and item for item in allowed_hosts)
            or not isinstance(canonical_url, str)
        ):
            raise ValueError(f"source allowlist invalid for {source_id}")
        required_urls = [canonical_url]
        required_urls.extend(
            source_url
            for _, _, alias_source_id, source_url in OFFICIAL_IDENTITY_ALIASES.values()
            if alias_source_id == source_id
        )
        required_hosts = {urlparse(item).hostname for item in required_urls}
        if None in required_hosts or not required_hosts.issubset(set(allowed_hosts)):
            raise ValueError(f"source allowlist does not cover fixed inputs for {source_id}")
        required_operations = {"fetch", "retain"}
        operations = set(rights.get("permitted_operations", []))
        permitted_uses = set(rights.get("permitted_uses", []))
        if (
            source.get("enabled") is not True
            or SOURCE_CAPABILITIES[source_id] not in source.get("capabilities", [])
            or not required_operations.issubset(operations)
            or "noncommercial_private_research_preview" not in permitted_uses
            or rights.get("cache_allowed") != "allowlisted_metadata_only"
            or rights.get("redistribution_allowed") is not False
            or not isinstance(rights.get("retention_days"), int)
            or rights["retention_days"] <= 0
        ):
            raise ValueError(
                f"rights record does not permit private catalog retention: {source_id}"
            )
        snapshots[source_id] = {
            "source": source,
            "sha256": hashlib.sha256(_canonical_json(source)).hexdigest(),
            "retention_days": rights["retention_days"],
        }
    return snapshots


def _receipt(
    *,
    source_id: str,
    source_url: str,
    adapter: str,
    rows: list[object],
    retrieved_at: datetime,
    rights_snapshot: dict[str, object],
) -> dict[str, object]:
    retention_value = rights_snapshot["retention_days"]
    if not isinstance(retention_value, int):
        raise TypeError("validated retention_days must be an integer")
    retention_days = retention_value
    return {
        "source_id": source_id,
        "source_url": source_url,
        "adapter": adapter,
        "retrieved_at": retrieved_at.isoformat(),
        "record_count": len(rows),
        "normalized_source_sha256": hashlib.sha256(_canonical_json(rows)).hexdigest(),
        "rights_snapshot_sha256": rights_snapshot["sha256"],
        "retention_until": (retrieved_at + timedelta(days=retention_days)).isoformat(),
        "permitted_operations": ["FETCH", "RETAIN"],
        "permitted_use": "NONCOMMERCIAL_PRIVATE_RESEARCH_PREVIEW",
        "rights_state": SOURCE_MODE,
    }


def _fetch_official_alias_evidence(
    *,
    retrieved_at: datetime,
    rights: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Hash fixed exchange disclosures without retaining their full text."""

    evidence: list[dict[str, object]] = []
    with httpx.Client(
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
        headers={
            "User-Agent": "StockMarketAgent/0.1 (personal-research; contact=repository-owner)"
        },
    ) as client:
        for instrument_id, (
            legacy_symbol,
            legacy_name,
            source_id,
            source_url,
        ) in OFFICIAL_IDENTITY_ALIASES.items():
            snapshot = rights[source_id]
            source = cast("dict[str, object]", snapshot["source"])
            dev_live = cast("dict[str, object]", source["dev_live"])
            allowed_hosts = cast("list[str]", dev_live["allowed_hosts"])
            host = urlparse(source_url).hostname
            if host not in allowed_hosts:
                raise ValueError(f"official alias host is not rights-allowlisted: {instrument_id}")
            with client.stream("GET", source_url) as response:
                if response.is_redirect:
                    raise ValueError(f"official alias disclosure redirected: {instrument_id}")
                response.raise_for_status()
                content_sha256 = _bounded_response_sha256(
                    response,
                    instrument_id=instrument_id,
                )
            retention_days = snapshot["retention_days"]
            if not isinstance(retention_days, int):
                raise TypeError("validated retention_days must be an integer")
            evidence.append(
                {
                    "evidence_id": str(
                        uuid5(
                            _ALIAS_EVIDENCE_NAMESPACE,
                            f"{source_id}:{instrument_id}:{legacy_symbol}:{source_url}",
                        )
                    ),
                    "instrument_id": instrument_id,
                    "legacy_symbol": legacy_symbol,
                    "legacy_name": legacy_name,
                    "source_id": source_id,
                    "source_url": source_url,
                    "retrieved_at": retrieved_at.isoformat(),
                    "content_sha256": content_sha256,
                    "rights_snapshot_sha256": snapshot["sha256"],
                    "retention_until": (retrieved_at + timedelta(days=retention_days)).isoformat(),
                }
            )
    return evidence


def _bounded_response_sha256(
    response: httpx.Response,
    *,
    instrument_id: str,
    max_bytes: int = _MAX_ALIAS_DOCUMENT_BYTES,
) -> str:
    """Hash a response while aborting before an oversized body is buffered."""

    digest = hashlib.sha256()
    consumed = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        consumed += len(chunk)
        if consumed > max_bytes:
            raise ValueError(f"official alias disclosure size is invalid: {instrument_id}")
        digest.update(chunk)
    if consumed == 0:
        raise ValueError(f"official alias disclosure size is invalid: {instrument_id}")
    return digest.hexdigest()


def _deduplicate_official_rows(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Collapse exchange-directory duplicate IDs only when their identity facts agree."""

    by_identifier: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_identifier.setdefault(str(row["instrument_id"]), []).append(row)
    normalized: list[dict[str, object]] = []
    for instrument_id, candidates in by_identifier.items():
        identity_shapes = {
            (
                item["short_name"],
                item["listing_status"],
                item["delisted_at"],
                item["source_id"],
            )
            for item in candidates
        }
        if len(identity_shapes) != 1:
            raise ValueError(f"conflicting official directory rows for {instrument_id}")
        normalized.append(
            min(
                candidates,
                key=lambda item: str(item["listed_at"] or "9999-12-31"),
            )
        )
    return normalized


def _apply_official_identity_aliases(
    rows: list[dict[str, object]],
    alias_evidence: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Attach exchange-announced historical codes to their continuing canonical listing."""

    by_identifier = {str(row["instrument_id"]): row for row in rows}
    canonical_symbols = {str(row["instrument_id"]).split(":", maxsplit=1)[1] for row in rows}
    evidence_by_instrument = {str(item["instrument_id"]): item for item in alias_evidence}
    if len(evidence_by_instrument) != len(OFFICIAL_IDENTITY_ALIASES):
        raise ValueError("official identity aliases require one unique evidence receipt each")
    for instrument_id, (
        legacy_symbol,
        legacy_name,
        source_id,
        evidence_url,
    ) in OFFICIAL_IDENTITY_ALIASES.items():
        target = by_identifier.get(instrument_id)
        if target is None:
            raise ValueError(
                f"official alias target missing from current directory: {instrument_id}"
            )
        if legacy_symbol in canonical_symbols:
            raise ValueError(f"official alias collides with a canonical symbol: {legacy_symbol}")
        receipt = evidence_by_instrument.get(instrument_id)
        if receipt is None or (
            receipt["legacy_symbol"],
            receipt["legacy_name"],
            receipt["source_id"],
            receipt["source_url"],
        ) != (legacy_symbol, legacy_name, source_id, evidence_url):
            raise ValueError(f"official alias evidence receipt mismatch: {instrument_id}")
        target["legacy_symbols"] = sorted(
            {*cast("list[str]", target["legacy_symbols"]), legacy_symbol}
        )
        target["legacy_names"] = sorted({*cast("list[str]", target["legacy_names"]), legacy_name})
        target["alias_evidence_ids"] = sorted(
            {
                *cast("list[str]", target["alias_evidence_ids"]),
                str(receipt["evidence_id"]),
            }
        )
    return rows


def build_catalog(retrieved_at: datetime, *, rights_config: Path) -> dict[str, object]:
    rights = _rights_snapshots(rights_config)
    alias_evidence = _fetch_official_alias_evidence(retrieved_at=retrieved_at, rights=rights)
    sse, sse_source = _sse_rows(retrieved_at)
    szse, szse_source = _szse_rows(retrieved_at)
    bse, bse_source = _bse_rows(retrieved_at)
    delisted, sse_delisted_source, szse_delisted_source = _official_delisted_rows(retrieved_at)
    instruments = sorted(
        _apply_official_identity_aliases(
            _deduplicate_official_rows([*sse, *szse, *bse, *delisted]),
            alias_evidence,
        ),
        key=lambda item: str(item["instrument_id"]),
    )
    identifiers = [str(item["instrument_id"]) for item in instruments]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("catalog contains duplicate canonical instrument IDs")
    exchange_counts = {
        exchange: sum(str(item["instrument_id"]).startswith(f"{exchange}:") for item in instruments)
        for exchange in EXPECTED_CANONICAL_COUNTS
    }
    listing_counts = {
        status: sum(item["listing_status"] == status for item in instruments)
        for status in EXPECTED_LISTING_COUNTS
    }
    if exchange_counts != EXPECTED_CANONICAL_COUNTS or listing_counts != EXPECTED_LISTING_COUNTS:
        raise ValueError(
            "catalog counts differ from the versioned official snapshot expectation: "
            f"exchange={exchange_counts}, listing={listing_counts}"
        )
    receipts = [
        _receipt(
            source_id=SSE_SOURCE_ID,
            source_url=SSE_URL,
            adapter="AKShare 1.18.70 official SSE directory adapters",
            rows=[*sse_source, *sse_delisted_source],
            retrieved_at=retrieved_at,
            rights_snapshot=rights[SSE_SOURCE_ID],
        ),
        _receipt(
            source_id=SZSE_SOURCE_ID,
            source_url=SZSE_URL,
            adapter="AKShare 1.18.70 official SZSE directory adapters",
            rows=[*szse_source, *szse_delisted_source],
            retrieved_at=retrieved_at,
            rights_snapshot=rights[SZSE_SOURCE_ID],
        ),
        _receipt(
            source_id=BSE_SOURCE_ID,
            source_url=BSE_URL,
            adapter="AKShare 1.18.70 stock_info_bj_name_code",
            rows=bse_source,
            retrieved_at=retrieved_at,
            rights_snapshot=rights[BSE_SOURCE_ID],
        ),
        _receipt(
            source_id=SZSE_ALIAS_SOURCE_ID,
            source_url="https://www.szse.cn/disclosure/notice/",
            adapter="Fixed official SZSE identity-change disclosures",
            rows=[item for item in alias_evidence if item["source_id"] == SZSE_ALIAS_SOURCE_ID],
            retrieved_at=retrieved_at,
            rights_snapshot=rights[SZSE_ALIAS_SOURCE_ID],
        ),
        _receipt(
            source_id=SSE_ALIAS_SOURCE_ID,
            source_url="https://www.sse.com.cn/disclosure/listedinfo/announcement/",
            adapter="Fixed official SSE identity-change disclosures",
            rows=[item for item in alias_evidence if item["source_id"] == SSE_ALIAS_SOURCE_ID],
            retrieved_at=retrieved_at,
            rights_snapshot=rights[SSE_ALIAS_SOURCE_ID],
        ),
    ]
    expires_at = min(str(item["retention_until"]) for item in receipts)
    return {
        "schema_version": "1.0",
        "catalog_version": CATALOG_VERSION,
        "source_mode": SOURCE_MODE,
        "retrieved_at": retrieved_at.isoformat(),
        "expires_at": expires_at,
        "notice": (
            "Private R2 research preview only. Source access and redistribution rights "
            "are not production-qualified; refresh must remain explicit and audited."
        ),
        "sources": receipts,
        "alias_evidence": alias_evidence,
        "instruments": instruments,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rights-config", type=Path, default=Path("configs/sources.yaml"))
    args = parser.parse_args()
    if not args.allow_network:
        raise SystemExit("--allow-network is required for an explicit catalog refresh")
    retrieved_at = datetime.now(UTC)
    payload = build_catalog(retrieved_at, rights_config=args.rights_config)
    instruments = cast("list[object]", payload["instruments"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = _canonical_json(payload) + b"\n"
    if args.output.suffix == ".gz":
        with args.output.open("wb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as archive:
                archive.write(serialized)
    else:
        args.output.write_bytes(serialized)
    print(
        json.dumps(
            {
                "catalog_version": payload["catalog_version"],
                "instrument_count": len(instruments),
                "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
