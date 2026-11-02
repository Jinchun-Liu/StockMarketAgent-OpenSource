from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

import pytest

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.runtime_resources import runtime_resource_path
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.instruments import (
    EntityKind,
    InstrumentLookupRequest,
    InstrumentSearchMatchType,
    InstrumentSearchQuery,
    MasterDataQuery,
    ResolutionStatus,
)
from stock_assistant.platform.instruments.synthetic_master import (
    BundledAshareInstrumentMasterData,
    SyntheticAshareInstrumentMasterData,
)

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _context() -> OperationContext:
    return OperationContext(schema_version="1.0", as_of=NOW, requested_at=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "603986",
        "603986.SH",
        "SH603986",
        "SH:603986",
        "603986.SS",
        "XSHG:603986",
        "兆易创新",
        "  兆易创新  ",
    ],
)
async def test_search_normalizes_supported_code_and_name_shapes(query: str) -> None:
    result = await SyntheticAshareInstrumentMasterData().search(
        InstrumentSearchQuery(schema_version="1.0", query=query, limit=10, as_of=NOW)
    )

    assert result.matches[0].instrument_id == "XSHG:603986"
    assert result.matches[0].match_type is not InstrumentSearchMatchType.PARTIAL_NAME


@pytest.mark.asyncio
async def test_partial_name_keeps_ambiguous_candidates_for_user_confirmation() -> None:
    result = await SyntheticAshareInstrumentMasterData().search(
        InstrumentSearchQuery(schema_version="1.0", query="平安", limit=10, as_of=NOW)
    )

    assert result.requires_confirmation is True
    assert {"XSHE:000001", "XSHG:601318"}.issubset({item.instrument_id for item in result.matches})
    assert all(item.match_type is InstrumentSearchMatchType.PARTIAL_NAME for item in result.matches)


@pytest.mark.asyncio
async def test_search_returns_real_active_and_delisted_status_without_guessing() -> None:
    adapter = BundledAshareInstrumentMasterData()
    active = await adapter.search_v1_1(
        InstrumentSearchQuery(schema_version="1.0", query="600000", limit=10, as_of=NOW)
    )
    delisted = await adapter.search_v1_1(
        InstrumentSearchQuery(schema_version="1.0", query="600001", limit=10, as_of=NOW)
    )

    assert active.matches[0].short_name == "浦发银行"
    assert active.matches[0].listing_status.value == "active"
    assert active.matches[0].legal_name_verified is True
    assert delisted.matches[0].listing_status.value == "delisted"
    assert delisted.matches[0].legal_name_verified is False


def test_bundled_catalog_covers_current_shenzhen_shanghai_and_beijing_universe() -> None:
    payload = json.loads(
        gzip.decompress(
            runtime_resource_path("instruments/ashare-catalog.dev.v1.json.gz").read_bytes()
        ).decode("utf-8")
    )
    rows = payload["instruments"]
    identifiers = [item["instrument_id"] for item in rows]

    assert payload["source_mode"] == "DEV_ONLY/UNVERIFIED"
    assert len(rows) == 5_884
    assert len(identifiers) == len(set(identifiers))
    assert sum(item.startswith("XSHG:") for item in identifiers) == 2_461
    assert sum(item.startswith("XSHE:") for item in identifiers) == 3_084
    assert sum(item.startswith("XBSE:") for item in identifiers) == 339
    assert sum(item["listing_status"] == "active" for item in rows) == 5_551
    assert sum(item["listing_status"] == "delisted" for item in rows) == 333
    assert {item["source_id"] for item in payload["sources"]} == {
        "sse_official_instrument_directory",
        "szse_official_instrument_directory",
        "bse_official_instrument_directory",
        "szse_official_identity_change_disclosures",
        "sse_official_identity_change_disclosures",
    }
    assert all(len(item["rights_snapshot_sha256"]) == 64 for item in payload["sources"])
    assert all(item["permitted_operations"] == ["FETCH", "RETAIN"] for item in payload["sources"])
    assert {item["source_id"]: item["record_count"] for item in payload["sources"]} == {
        "sse_official_instrument_directory": 2_474,
        "szse_official_instrument_directory": 3_105,
        "bse_official_instrument_directory": 339,
        "szse_official_identity_change_disclosures": 3,
        "sse_official_identity_change_disclosures": 1,
    }
    assert len(payload["alias_evidence"]) == 4
    assert all(len(item["content_sha256"]) == 64 for item in payload["alias_evidence"])
    assert all(len(item["rights_snapshot_sha256"]) == 64 for item in payload["alias_evidence"])
    evidence_ids = {item["evidence_id"] for item in payload["alias_evidence"]}
    assert evidence_ids == {
        evidence_id for item in rows for evidence_id in item["alias_evidence_ids"]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_id", "expected_name"),
    [
        ("600000", "XSHG:600000", "浦发银行"),
        ("石头科技", "XSHG:688169", "石头科技"),
        ("002594.SZ", "XSHE:002594", "比亚迪"),
        ("中际旭创", "XSHE:300308", "中际旭创"),
        ("920000.BJ", "XBSE:920000", "安徽凤凰"),
        ("430047.BJ", "XBSE:920047", "诺思兰德"),
        ("000022", "XSHE:001872", "招商港口"),
        ("000043.SZ", "XSHE:001914", "招商积余"),
        ("300114", "XSHE:302132", "中航成飞"),
        ("SH600849", "XSHG:601607", "上海医药"),
        ("深赤湾A", "XSHE:001872", "招商港口"),
        ("中航善达", "XSHE:001914", "招商积余"),
        ("中航电测", "XSHE:302132", "中航成飞"),
    ],
)
async def test_bundled_catalog_searches_representative_a_shares(
    query: str,
    expected_id: str,
    expected_name: str,
) -> None:
    result = await BundledAshareInstrumentMasterData().search(
        InstrumentSearchQuery(schema_version="1.0", query=query, limit=10, as_of=NOW)
    )

    assert result.matches[0].instrument_id == expected_id
    assert result.matches[0].short_name == expected_name


@pytest.mark.asyncio
async def test_resolve_and_pit_reject_unknown_or_future_visible_instruments() -> None:
    adapter = SyntheticAshareInstrumentMasterData()
    resolved = await adapter.resolve(
        InstrumentLookupRequest(
            schema_version="1.0", context=_context(), instrument_id="XSHG:603986"
        )
    )
    assert resolved.status is ResolutionStatus.RESOLVED

    future_cutoff = datetime(2000, 1, 1, tzinfo=UTC)
    missing = await adapter.search(
        InstrumentSearchQuery(schema_version="1.0", query="603986", limit=10, as_of=future_cutoff)
    )
    assert missing.matches == []

    non_a_share = await adapter.search(
        InstrumentSearchQuery(schema_version="1.0", query="AAPL", limit=10, as_of=NOW)
    )
    assert non_a_share.matches == []


@pytest.mark.asyncio
async def test_current_snapshot_never_claims_visibility_before_its_real_retrieval() -> None:
    adapter = BundledAshareInstrumentMasterData()
    cutoff = datetime(2026, 8, 30, 23, 59, tzinfo=UTC)

    search = await adapter.search(
        InstrumentSearchQuery(schema_version="1.0", query="603986", limit=10, as_of=cutoff)
    )
    resolved = await adapter.resolve(
        InstrumentLookupRequest(
            schema_version="1.0",
            context=OperationContext(schema_version="1.0", as_of=cutoff, requested_at=NOW),
            instrument_id="XSHG:603986",
        )
    )

    assert search.matches == []
    assert resolved.status is ResolutionStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_expired_bundled_catalog_fails_closed_for_every_runtime_entrypoint() -> None:
    payload = json.loads(
        gzip.decompress(
            runtime_resource_path("instruments/ashare-catalog.dev.v1.json.gz").read_bytes()
        ).decode("utf-8")
    )
    expired_now = datetime.fromisoformat(payload["expires_at"]) + timedelta(microseconds=1)
    adapter = BundledAshareInstrumentMasterData(clock=lambda: expired_now)

    calls = (
        adapter.search(
            InstrumentSearchQuery(schema_version="1.0", query="603986", limit=10, as_of=NOW)
        ),
        adapter.search_v1_1(
            InstrumentSearchQuery(schema_version="1.0", query="603986", limit=10, as_of=NOW)
        ),
        adapter.resolve(
            InstrumentLookupRequest(
                schema_version="1.0", context=_context(), instrument_id="XSHG:603986"
            )
        ),
        adapter.query(
            MasterDataQuery(
                schema_version="1.0",
                context=_context(),
                entity_kinds=[EntityKind.INSTRUMENT],
                limit=1,
            )
        ),
    )
    for call in calls:
        with pytest.raises(ApplicationError) as captured:
            await call
        assert captured.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert captured.value.safe_message == "INSTRUMENT_CATALOG_EXPIRED"


@pytest.mark.asyncio
async def test_v1_search_projection_remains_strict_without_v1_1_fields() -> None:
    result = await BundledAshareInstrumentMasterData().search(
        InstrumentSearchQuery(schema_version="1.0", query="603986", limit=10, as_of=NOW)
    )

    assert result.schema_version == "1.0"
    assert "legal_name_verified" not in result.matches[0].model_dump(mode="json")


@pytest.mark.asyncio
async def test_resolve_accepts_exchange_aliases_and_rejects_unknown_symbols() -> None:
    adapter = SyntheticAshareInstrumentMasterData()
    for exchange in ("XSHG", "SSE"):
        resolved = await adapter.resolve(
            InstrumentLookupRequest(
                schema_version="1.0",
                context=_context(),
                symbol="603986",
                exchange=exchange,
            )
        )
        assert resolved.status is ResolutionStatus.RESOLVED

    unknown = await adapter.resolve(
        InstrumentLookupRequest(
            schema_version="1.0",
            context=_context(),
            symbol="999999",
            exchange="XSHG",
        )
    )
    assert unknown.status is ResolutionStatus.NOT_FOUND

    changed_code = await adapter.resolve(
        InstrumentLookupRequest(
            schema_version="1.0",
            context=_context(),
            instrument_id="XSHE:000022",
        )
    )
    assert changed_code.status is ResolutionStatus.RESOLVED
    assert changed_code.matches[0].instrument.instrument_id == "XSHE:001872"
    assert len(changed_code.matches[0].evidence_ids) == 2


@pytest.mark.asyncio
async def test_master_query_filters_entities_identity_and_inactive_status() -> None:
    adapter = SyntheticAshareInstrumentMasterData()
    active = await adapter.query(
        MasterDataQuery(
            schema_version="1.0",
            context=_context(),
            entity_kinds=[EntityKind.INSTRUMENT, EntityKind.COMPANY],
            instrument_ids=["XSHG:603986", "XSHG:600001"],
            include_inactive=False,
            limit=10,
        )
    )
    assert [item.instrument.instrument_id for item in active.instruments] == ["XSHG:603986"]
    assert [item.short_name for item in active.companies] == ["兆易创新"]

    with_inactive = await adapter.query(
        MasterDataQuery(
            schema_version="1.0",
            context=_context(),
            entity_kinds=[EntityKind.INSTRUMENT],
            instrument_ids=["XSHG:600001"],
            include_inactive=True,
            limit=1,
        )
    )
    assert with_inactive.instruments[0].listing_status.value == "delisted"
    assert with_inactive.companies == []
