from __future__ import annotations

import ast
import copy
import inspect
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import BaseHandler, Request, build_opener
from urllib.response import addinfourl
from uuid import UUID

import pytest
from pydantic import JsonValue

from stock_assistant.common.errors import ApplicationError
from stock_assistant.config.yaml_config import load_versioned_config
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    FetchRequest,
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
    SourceAccessChannelV1_2,
    SourceEvidenceRole,
    SourceProvenanceV1_2,
    SourceRecordV1_2,
    SourceRuntimeModeV1_2,
    SourceTier,
)
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.modules.ingestion.r1_sources import source_batch_from_fetch
from stock_assistant.platform.sources import free_source_worker
from stock_assistant.platform.sources.free_source_runner import (
    FREE_SOURCE_ENDPOINT_ALLOWLIST,
    FreeSourceProcessResult,
    FreeSourceRunnerError,
    FreeSourceRunResult,
    FreeSourceSubprocessRunner,
    validate_free_source_arguments,
)
from stock_assistant.platform.sources.r1_free_live import (
    DISABLED_OFFICIAL_DOMAINS,
    FREE_SOURCE_CATALOG,
    FREE_SOURCE_CATALOG_BY_ID,
    FREE_SOURCE_LICENSE_ID,
    FREE_SOURCE_MATRIX,
    FREE_SOURCE_RIGHTS_HASH,
    FreeDevelopmentRightsPolicy,
    FreeLiveAdapter,
    RightsGatedFreeConnector,
    active_free_source_definitions,
    configured_discovery_sources,
    free_source_gap,
    free_sources_for_capability,
)

NOW = datetime(2026, 7, 31, 0, 55, tzinfo=UTC)
STARTED = NOW + timedelta(minutes=10)
COMPLETED = STARTED + timedelta(seconds=2)


def _context() -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        request_id=UUID(int=71),
        correlation_id=UUID(int=72),
        actor_id="r1-free-source-test",
        as_of=NOW,
        requested_at=NOW - timedelta(seconds=1),
    )


def _request(source_id: str, *, instrument_id: str = "XSHG:603986") -> FetchRequest:
    definition = FREE_SOURCE_CATALOG_BY_ID[source_id]
    return FetchRequest(
        schema_version="1.0",
        context=_context(),
        source_id=source_id,
        capability=definition.capability,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        since=NOW - timedelta(days=30),
        until=NOW,
        instrument_ids=[instrument_id],
        limit=100,
    )


class FakeRowsRunner:
    def __init__(self, rows: list[dict[str, JsonValue]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, Mapping[str, JsonValue]]] = []

    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        self.calls.append((endpoint_name, arguments))
        return self.rows


class FakeRunResultRunner:
    def __init__(self, result: FreeSourceRunResult) -> None:
        self.result = result
        self.calls: list[tuple[str, Mapping[str, JsonValue]]] = []

    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> FreeSourceRunResult:
        self.calls.append((endpoint_name, arguments))
        return self.result


class DeniedPolicy:
    def __init__(self) -> None:
        self.checks: list[RightsCheck] = []

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        self.checks.append(check)
        return RightsDecision(
            allowed=False,
            reason_code="FREE_RIGHTS_BLOCKED",
            decided_at=NOW,
            rights_snapshot_hash=check.rights_snapshot_hash,
        )


class AllowedTestPolicy:
    """Explicit test-only grant for disabled catalog adapter normalization."""

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        return RightsDecision(
            allowed=True,
            reason_code="TEST_ONLY_ALLOWED",
            decided_at=NOW,
            rights_snapshot_hash=check.rights_snapshot_hash,
            retention_until=NOW + timedelta(days=1),
        )


def test_free_source_matrix_covers_five_capabilities_and_fixed_roles() -> None:
    assert len(FREE_SOURCE_CATALOG) == 18
    assert {item.capability for item in FREE_SOURCE_CATALOG} == {
        "statutory.announcements",
        "financial.changes",
        "market.snapshot",
        "company_industry.news",
        "macro.flashes",
    }
    assert {item.source_id for item in FREE_SOURCE_MATRIX} == {
        "research_preview_announcements",
        "research_preview_company_news",
        "research_preview_financials",
        "research_preview_macro",
        "research_preview_market",
    }
    assert tuple(item.source_id for item in free_sources_for_capability("market.snapshot")) == (
        "research_preview_market",
    )
    assert FREE_SOURCE_CATALOG_BY_ID["cninfo_public_statutory"].evidence_role is (
        SourceEvidenceRole.AUTHORITATIVE
    )
    assert FREE_SOURCE_CATALOG_BY_ID["sse_public_disclosure"].evidence_role is (
        SourceEvidenceRole.CROSS_CHECK
    )
    assert FREE_SOURCE_CATALOG_BY_ID["eastmoney_public_news"].evidence_role is (
        SourceEvidenceRole.DISCOVERY
    )
    assert FREE_SOURCE_CATALOG_BY_ID["baostock_public_market"].access_channel is (
        SourceAccessChannelV1_2.BAOSTOCK_SDK
    )
    assert set(DISABLED_OFFICIAL_DOMAINS) == {
        "ndrc.gov.cn",
        "mofcom.gov.cn",
        "pbc.gov.cn",
    }
    assert FREE_SOURCE_CATALOG_BY_ID["cninfo_public_statutory"].fact_eligibility is (
        FactEligibility.COMPANY_FACT
    )
    assert (
        FREE_SOURCE_CATALOG_BY_ID["baostock_public_market"].content_tier is ContentTier.MARKET_DATA
    )
    assert FREE_SOURCE_CATALOG_BY_ID["eastmoney_public_news"].fact_eligibility is (
        FactEligibility.INELIGIBLE
    )
    for source_id, expected_url in (
        ("gov_cn_public_macro", "https://sousuo.www.gov.cn/zcwjk/"),
        ("miit_public_macro", "https://www.miit.gov.cn/zwgk/index.html"),
    ):
        definition = FREE_SOURCE_CATALOG_BY_ID[source_id]
        assert definition.evidence_role is SourceEvidenceRole.DISCOVERY
        assert definition.fact_eligibility is FactEligibility.CONTEXT_ONLY
        assert definition.canonical_url == expected_url
        assert definition.enabled is False
        adapter = FreeLiveAdapter(source_id)
        assert adapter.descriptor.enabled is False
        assert adapter.provenance.runtime_mode.value == "dev_live"
        assert adapter.provenance.release_tier.value == "dev_only"
        assert adapter.provenance.rights_status.value == "unverified"


def test_config_switch_enables_one_source_without_hiding_invalid_safety_labels() -> None:
    document = copy.deepcopy(load_versioned_config("configs/sources.yaml").data)
    raw_sources = document["sources"]
    assert isinstance(raw_sources, list)
    gov_source = next(
        source
        for source in raw_sources
        if isinstance(source, dict) and source.get("id") == "gov_cn_public_macro"
    )
    gov_source["enabled"] = True

    definitions = configured_discovery_sources(document)

    assert {item.source_id for item in definitions} == {
        "gov_cn_public_macro",
        "miit_public_macro",
    }
    assert {item.source_id for item in active_free_source_definitions(definitions)} == {
        "gov_cn_public_macro"
    }
    profile = gov_source["dev_live"]
    assert isinstance(profile, dict)
    profile["rights_status"] = "approved"
    with pytest.raises(RuntimeError, match="safety labels are invalid"):
        configured_discovery_sources(document)


@pytest.mark.asyncio
async def test_free_rights_profile_allows_metadata_and_rejects_ai_fulltext_and_export() -> None:
    policy = FreeDevelopmentRightsPolicy()
    request = _request("research_preview_macro")

    allowed = await policy.authorize(
        RightsCheck(
            context=request.context,
            source_id=request.source_id,
            operation=RightsOperation.DERIVE,
            rights_snapshot_hash=request.rights_snapshot_hash,
            content_category=request.capability,
            permitted_use=RightsPermittedUse.SUMMARY,
        )
    )
    denied = [
        await policy.authorize(
            RightsCheck(
                context=request.context,
                source_id=request.source_id,
                operation=operation,
                rights_snapshot_hash=request.rights_snapshot_hash,
                content_category=request.capability,
                permitted_use=permitted_use,
            )
        )
        for operation, permitted_use in (
            (RightsOperation.CACHE_FULLTEXT, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.EMBED, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.TRAIN, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.EXPORT, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.REDISTRIBUTE, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.DERIVE, RightsPermittedUse.AI_USE),
        )
    ]

    assert allowed.allowed is True
    assert all(item.allowed is False for item in denied)
    disabled = await policy.authorize(
        RightsCheck(
            context=request.context,
            source_id="eastmoney_public_market",
            operation=RightsOperation.FETCH,
            rights_snapshot_hash=request.rights_snapshot_hash,
            content_category="market.snapshot",
            permitted_use=RightsPermittedUse.NON_DISPLAY,
        )
    )
    assert disabled.allowed is False


@pytest.mark.asyncio
async def test_target_and_rights_fail_before_any_login_or_network_runner() -> None:
    runner = FakeRowsRunner([])
    denied = DeniedPolicy()
    connector = RightsGatedFreeConnector(
        adapter=FreeLiveAdapter("baostock_public_market"),
        rights_policy=denied,
        runner=runner,
    )

    with pytest.raises(ApplicationError, match="FREE_RIGHTS_BLOCKED"):
        await connector.fetch(_request("baostock_public_market"))
    assert len(denied.checks) == 1
    assert runner.calls == []

    with pytest.raises(ApplicationError, match="R1_TARGET_LOCKED"):
        await connector.fetch(_request("baostock_public_market", instrument_id="XSHG:600000"))
    assert len(denied.checks) == 1
    assert runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_id", "rows", "expected_field"),
    (
        (
            "cninfo_public_statutory",
            [
                {
                    "公告标题": "董事会公告",
                    "公告时间": "2026-07-30 16:00:00",
                    "公告链接": "https://example.invalid/announcement",
                    "公告类型": "董事会",
                    "正文": "must-not-persist",
                }
            ],
            "公告标题",
        ),
        (
            "eastmoney_public_financial",
            [
                {
                    "日期": "2026-06-30",
                    "净资产收益率(%)": "12.3",
                    "未授权字段": "must-not-persist",
                }
            ],
            "净资产收益率(%)",
        ),
        (
            "eastmoney_public_market",
            [
                {"日期": "2026-07-29", "收盘": 90},
                {"日期": "2026-07-30", "收盘": 93},
            ],
            "收盘",
        ),
        (
            "eastmoney_public_news",
            [
                {
                    "新闻标题": "产品进展",
                    "发布时间": "2026-07-30 17:00:00",
                    "文章来源": "开发来源",
                    "新闻链接": "https://example.invalid/news",
                    "新闻内容": "must-not-persist",
                }
            ],
            "新闻标题",
        ),
    ),
)
async def test_akshare_routes_emit_complete_v1_2_provenance_without_fulltext(
    source_id: str,
    rows: list[dict[str, JsonValue]],
    expected_field: str,
) -> None:
    runner = FakeRowsRunner(rows)
    adapter = FreeLiveAdapter(source_id)
    connector = RightsGatedFreeConnector(
        adapter=adapter,
        rights_policy=AllowedTestPolicy(),
        runner=runner,
        clock=iter((STARTED, COMPLETED)).__next__,
    )

    batch = await connector.fetch(_request(source_id))

    assert len(batch.records) == 1
    record = batch.records[0]
    assert isinstance(record, SourceRecordV1_2)
    assert isinstance(record.provenance, SourceProvenanceV1_2)
    assert record.provenance.schema_version == "1.2"
    assert record.provenance.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
    assert record.provenance.rights_status.value == "unverified"
    assert expected_field in record.payload
    assert "must-not-persist" not in json.dumps(record.payload, ensure_ascii=False)
    assert runner.calls[0][0] == adapter.endpoint_name


@pytest.mark.asyncio
async def test_baostock_cross_check_is_unadjusted_bounded_metadata() -> None:
    runner = FakeRowsRunner(
        [
            {
                "date": "2026-07-30",
                "code": "sh.603986",
                "open": "91.0",
                "high": "94.0",
                "low": "90.0",
                "close": "93.0",
                "volume": "100",
                "amount": "9300",
                "private": "must-not-persist",
            }
        ]
    )
    adapter = FreeLiveAdapter("baostock_public_market")
    connector = RightsGatedFreeConnector(
        adapter=adapter,
        rights_policy=AllowedTestPolicy(),
        runner=runner,
        clock=iter((STARTED, COMPLETED)).__next__,
    )

    batch = await connector.fetch(_request(adapter.source_id))

    assert batch.records[0].payload == {
        "date": "2026-07-30",
        "code": "sh.603986",
        "open": "91.0",
        "high": "94.0",
        "low": "90.0",
        "close": "93.0",
        "volume": "100",
        "amount": "9300",
    }
    assert batch.records[0].provenance.evidence_role is SourceEvidenceRole.CROSS_CHECK
    assert runner.calls[0][1]["code"] == "sh.603986"
    assert runner.calls[0][1]["fields"] == ("date,code,open,high,low,close,volume,amount")


@pytest.mark.asyncio
async def test_allowlisted_official_metadata_emits_only_headline_discovery() -> None:
    adapter = FreeLiveAdapter("gov_cn_public_macro")
    runner = FakeRunResultRunner(
        FreeSourceRunResult(
            rows=[
                {
                    "title": "集成电路政策",
                    "published_at": "2026-07-30 18:00:00",
                    "publisher": "国务院办公厅",
                    "category": "政策文件",
                    "url": "https://www.gov.cn/zhengce/policy-1.html",
                    "summary": "must-not-persist",
                }
            ],
            response_etag='"policy-v1"',
            response_last_modified="Thu, 30 Jul 2026 10:00:00 GMT",
        )
    )
    connector = RightsGatedFreeConnector(
        adapter=adapter,
        rights_policy=AllowedTestPolicy(),
        runner=runner,
        clock=iter((STARTED, COMPLETED)).__next__,
    )
    fetch = await connector.fetch(_request(adapter.source_id))
    record = fetch.records[0]

    assert isinstance(record, SourceRecordV1_2)
    assert record.payload == {
        "title": "集成电路政策",
        "published_at": "2026-07-30 18:00:00",
        "publisher": "国务院办公厅",
        "category": "政策文件",
        "url": "https://www.gov.cn/zhengce/policy-1.html",
    }
    assert record.record_type == "macro.flash.discovery"
    assert record.fact_eligibility is FactEligibility.CONTEXT_ONLY
    assert record.provenance.evidence_role is SourceEvidenceRole.DISCOVERY
    normalized = source_batch_from_fetch(
        fetch_batch=fetch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    assert len(normalized.events) == 1
    assert normalized.events[0].event_type == "macro.flash.discovery"
    assert [fact.slot for fact in normalized.events[0].facts] == ["publication.headline"]
    assert normalized.events[0].facts[0].value.value == "集成电路政策"
    assert normalized.coverages[0].last_success_at == COMPLETED
    assert normalized.next_cursor is not None
    assert runner.calls[0][0] == "official_gov_policy"


def test_official_cursor_recovers_conditional_headers_and_rejects_injection() -> None:
    adapter = FreeLiveAdapter("miit_public_macro")
    initial = adapter.normalize(
        _request(adapter.source_id),
        [],
        execution_started_at=STARTED,
        execution_completed_at=COMPLETED,
        response_etag='"miit-v1"',
        response_last_modified="Thu, 30 Jul 2026 10:00:00 GMT",
    )
    assert initial.next_cursor is not None
    resumed = _request(adapter.source_id).model_copy(update={"cursor": initial.next_cursor})

    arguments = adapter.function_arguments(resumed)

    assert arguments["if_none_match"] == '"miit-v1"'
    assert arguments["if_modified_since"] == "Thu, 30 Jul 2026 10:00:00 GMT"
    unsafe_cursor = json.dumps(
        {
            "etag": '"safe"\r\nX-Unsafe: value',
            "last_modified": None,
            "version": 1,
            "window_end": NOW.isoformat(),
        }
    )
    with pytest.raises(ApplicationError, match="R1_SOURCE_CURSOR_INVALID"):
        adapter.function_arguments(
            _request(adapter.source_id).model_copy(update={"cursor": unsafe_cursor})
        )


@pytest.mark.parametrize(
    "source_id",
    (
        "sse_public_disclosure",
        "sse_public_financial",
    ),
)
def test_unapproved_sse_page_routes_cannot_normalize_into_facts(source_id: str) -> None:
    adapter = FreeLiveAdapter(source_id)

    with pytest.raises(ApplicationError, match="FREE_SOURCE_NOT_QUALIFIED"):
        adapter.normalize(
            _request(source_id),
            [
                {
                    "document_id": "unexpected",
                    "title": "must stay a user-link only",
                    "published_at": "2026-07-30 18:00:00",
                    "url": adapter.definition.canonical_url,
                }
            ],
            execution_started_at=STARTED,
            execution_completed_at=COMPLETED,
        )


def test_free_fetch_converts_to_provider_neutral_v1_2_batch_and_gap_never_advances() -> None:
    adapter = FreeLiveAdapter("eastmoney_public_market")
    fetch = adapter.normalize(
        _request(adapter.source_id),
        [{"日期": "2026-07-30", "收盘": 93}],
        execution_started_at=STARTED,
        execution_completed_at=COMPLETED,
    )

    normalized = source_batch_from_fetch(
        fetch_batch=fetch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    gap = free_source_gap(
        source_id="sse_public_disclosure",
        observed_at=COMPLETED,
        reason_code="FREE_SOURCE_UNAVAILABLE",
        status=SourceCoverageStatus.UNAVAILABLE,
    )

    assert normalized.stream == "r1_premarket:dev_live:market.snapshot"
    assert normalized.events[0].schema_version == "1.2"
    assert normalized.events[0].evidence[0].provenance == adapter.provenance
    assert gap.next_cursor is None
    assert gap.coverages[0].progress.business_watermark is None
    assert gap.coverages[0].progress.advance_state.value == "not_advanced"


def test_baostock_cross_check_never_becomes_event_fact_or_advances() -> None:
    adapter = FreeLiveAdapter("baostock_public_market")
    fetch = adapter.normalize(
        _request(adapter.source_id),
        [{"date": "2026-07-30", "code": "sh.603986", "close": "93"}],
        execution_started_at=STARTED,
        execution_completed_at=COMPLETED,
    )

    normalized = source_batch_from_fetch(
        fetch_batch=fetch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )

    assert fetch.records[0].provenance.evidence_role is SourceEvidenceRole.CROSS_CHECK
    assert normalized.events == ()
    assert normalized.coverages[0].status is SourceCoverageStatus.NOT_QUALIFIED
    assert normalized.coverages[0].gap_reason == "R1_SOURCE_FACT_INELIGIBLE"
    assert normalized.coverages[0].progress.advance_state.value == "not_advanced"
    assert normalized.next_cursor is None


def test_ineligible_discovery_never_becomes_fact_or_ok_coverage() -> None:
    adapter = FreeLiveAdapter("eastmoney_public_news")
    fetch = adapter.normalize(
        _request(adapter.source_id),
        [
            {
                "新闻标题": "仅用于发现",
                "发布时间": "2026-07-30 17:00:00",
                "文章来源": "开发来源",
                "新闻链接": "https://example.invalid/news",
            }
        ],
        execution_started_at=STARTED,
        execution_completed_at=COMPLETED,
    )

    normalized = source_batch_from_fetch(
        fetch_batch=fetch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )

    assert fetch.records[0].content_tier is ContentTier.DEVELOPMENT_ONLY
    assert fetch.records[0].fact_eligibility is FactEligibility.INELIGIBLE
    assert normalized.events == ()
    assert normalized.coverages[0].status is SourceCoverageStatus.NOT_QUALIFIED
    assert normalized.coverages[0].progress.advance_state.value == "not_advanced"
    assert normalized.next_cursor is None

    context_record = fetch.records[0].model_copy(
        update={
            "source_tier": SourceTier.LICENSED_AGGREGATOR,
            "content_tier": ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA,
            "fact_eligibility": FactEligibility.CONTEXT_ONLY,
        }
    )
    context_batch = fetch.model_copy(update={"records": [context_record]})
    context_only = source_batch_from_fetch(
        fetch_batch=context_batch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    assert context_only.events == ()
    assert context_only.coverages[0].status is SourceCoverageStatus.NOT_QUALIFIED


def test_future_provider_time_is_dropped_not_clamped_to_cutoff() -> None:
    adapter = FreeLiveAdapter("eastmoney_public_market")
    batch = adapter.normalize(
        _request(adapter.source_id),
        [{"日期": "2026-08-01", "收盘": 93}],
        execution_started_at=STARTED,
        execution_completed_at=COMPLETED,
    )

    assert batch.records == []
    assert batch.source_watermark == NOW
    normalized = source_batch_from_fetch(
        fetch_batch=batch,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    assert normalized.events == ()
    assert normalized.coverages[0].status is SourceCoverageStatus.OK
    assert normalized.coverages[0].gap_reason is None
    assert normalized.coverages[0].last_success_at == COMPLETED
    assert normalized.coverages[0].progress.business_watermark == NOW
    assert normalized.coverages[0].progress.advance_state.value == "advanced"
    assert normalized.next_cursor == NOW.isoformat()


def test_empty_fetch_still_requires_adapter_bound_source_rights_and_provenance() -> None:
    adapter = FreeLiveAdapter("baostock_public_market")
    empty = adapter.normalize(
        _request(adapter.source_id),
        [],
        execution_started_at=STARTED,
        execution_completed_at=COMPLETED,
    )
    mismatched_source = empty.model_copy(update={"source_id": "eastmoney_public_market"})
    mismatched_rights = empty.model_copy(update={"rights_snapshot_hash": "0" * 64})
    mismatched_provenance = adapter.provenance.model_copy(
        update={"source_identifier": "603986.SH:eastmoney_public_market:market.snapshot"}
    )

    for fetch_batch, provenance, reason in (
        (mismatched_source, adapter.provenance, "R1_SOURCE_REQUEST_MISMATCH"),
        (mismatched_rights, adapter.provenance, "R1_SOURCE_RIGHTS_MISMATCH"),
        (empty, mismatched_provenance, "R1_SOURCE_REQUEST_MISMATCH"),
    ):
        with pytest.raises(ApplicationError, match=reason):
            source_batch_from_fetch(
                fetch_batch=fetch_batch,
                capability=adapter.capability,
                provenance=provenance,
                license_id=FREE_SOURCE_LICENSE_ID,
            )

    valid = source_batch_from_fetch(
        fetch_batch=empty,
        capability=adapter.capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    assert valid.events == ()
    assert valid.coverages[0].status is SourceCoverageStatus.OK
    assert valid.coverages[0].gap_reason is None
    assert valid.coverages[0].last_success_at == COMPLETED
    assert valid.coverages[0].progress.business_watermark == NOW
    assert valid.coverages[0].progress.advance_state.value == "advanced"
    assert valid.next_cursor == NOW.isoformat()


def test_free_runner_allowlist_matches_every_automated_adapter_endpoint() -> None:
    automated_endpoints = {
        item.endpoint_name
        for item in FREE_SOURCE_MATRIX
        if item.access_channel is not SourceAccessChannelV1_2.OFFICIAL_HTTP
    }
    assert automated_endpoints <= FREE_SOURCE_ENDPOINT_ALLOWLIST
    for source_id in (
        "cninfo_public_statutory",
        "cninfo_public_financial",
        "eastmoney_public_financial",
        "eastmoney_public_market",
        "baostock_public_market",
        "eastmoney_public_news",
    ):
        adapter = FreeLiveAdapter(source_id)
        validate_free_source_arguments(
            adapter.endpoint_name,
            adapter.function_arguments(_request(source_id)),
        )


@pytest.mark.parametrize(
    ("endpoint", "arguments"),
    (
        ("baostock_history", {"code": "sz.000001"}),
        ("stock_zh_a_hist", {"symbol": "000001"}),
        ("stock_news_em", {"symbol": "000001"}),
        ("official_sse_disclosures", {"instrument_id": "XSHG:600000"}),
    ),
)
def test_free_runner_rejects_non_target_arguments(
    endpoint: str,
    arguments: dict[str, JsonValue],
) -> None:
    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_TARGET_LOCKED"):
        validate_free_source_arguments(endpoint, arguments)


def test_free_runner_rejects_caller_supplied_url_login_and_header_injection() -> None:
    base: dict[str, JsonValue] = {
        "instrument_id": "XSHG:603986",
        "since": "2026-07-01T00:00:00+00:00",
        "until": NOW.isoformat(),
    }
    for unsafe in (
        {**base, "url": "https://evil.example/"},
        {**base, "username": "user"},
        {**base, "if_none_match": '"safe"\r\nX-Unsafe: value'},
    ):
        with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_REQUEST_INVALID"):
            validate_free_source_arguments("official_gov_policy", unsafe)


def test_provider_row_for_another_security_fails_closed() -> None:
    adapter = FreeLiveAdapter("baostock_public_market")

    with pytest.raises(ApplicationError, match="R1_TARGET_LOCKED"):
        adapter.normalize(
            _request(adapter.source_id),
            [{"date": "2026-07-30", "code": "sz.000001", "close": "93"}],
            execution_started_at=STARTED,
            execution_completed_at=COMPLETED,
        )


def test_baostock_worker_loads_optional_sdk_at_runtime_and_logs_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported_modules: list[str] = []
    calls: list[str] = []

    class FakeStatus:
        error_code = "0"

    class FakeQueryResult:
        error_code = "0"
        fields = ("date", "code", "close")

        def __init__(self) -> None:
            self._rows = iter(
                (
                    ("2026-07-29", "sh.603986", "90"),
                    ("2026-07-30", "sh.603986", "93"),
                )
            )
            self._current: tuple[str, ...] | None = None

        def next(self) -> bool:
            self._current = next(self._rows, None)
            return self._current is not None

        def get_row_data(self) -> tuple[str, ...]:
            assert self._current is not None
            return self._current

    class FakeBaoStockSdk:
        def login(self) -> FakeStatus:
            calls.append("login")
            return FakeStatus()

        def query_history_k_data_plus(
            self,
            code: str,
            fields: str,
            *,
            start_date: str,
            end_date: str,
            frequency: str,
            adjustflag: str,
        ) -> FakeQueryResult:
            calls.append("query")
            assert code == "sh.603986"
            assert fields == "date,code,close"
            assert start_date == "2026-07-29"
            assert end_date == "2026-07-30"
            assert frequency == "d"
            assert adjustflag == "3"
            return FakeQueryResult()

        def logout(self) -> FakeStatus:
            calls.append("logout")
            return FakeStatus()

    fake_sdk = FakeBaoStockSdk()

    def import_module(name: str) -> object:
        imported_modules.append(name)
        return fake_sdk

    source_tree = ast.parse(inspect.getsource(free_source_worker))
    static_imports = [
        node
        for node in ast.walk(source_tree)
        if (
            isinstance(node, ast.Import)
            and any(
                alias.name == "baostock" or alias.name.startswith("baostock.")
                for alias in node.names
            )
        )
        or (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "baostock" or node.module.startswith("baostock."))
        )
    ]
    assert static_imports == []
    monkeypatch.setattr(free_source_worker.importlib, "import_module", import_module)

    rows = free_source_worker._baostock(
        {
            "code": "sh.603986",
            "fields": "date,code,close",
            "start_date": "2026-07-29",
            "end_date": "2026-07-30",
        },
        max_rows=2,
    )

    assert imported_modules == ["baostock"]
    assert rows == [
        {"date": "2026-07-29", "code": "sh.603986", "close": "90"},
        {"date": "2026-07-30", "code": "sh.603986", "close": "93"},
    ]
    assert calls == ["login", "query", "logout"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "reason", "attempts"),
    (
        (FreeSourceProcessResult(-1, "", timed_out=True), "FREE_SOURCE_TIMEOUT", 2),
        (
            FreeSourceProcessResult(
                2,
                '{"ok":false,"error_code":"FREE_SOURCE_FORBIDDEN"}',
            ),
            "FREE_SOURCE_FORBIDDEN",
            1,
        ),
        (
            FreeSourceProcessResult(
                2,
                '{"ok":false,"error_code":"FREE_SOURCE_RATE_LIMITED"}',
            ),
            "FREE_SOURCE_RATE_LIMITED",
            1,
        ),
        (
            FreeSourceProcessResult(0, '{"ok":true,"rows":{"bad":true}}'),
            "FREE_SOURCE_SCHEMA_DRIFT",
            1,
        ),
    ),
)
async def test_free_runner_classifies_timeout_403_429_and_schema_drift(
    result: FreeSourceProcessResult,
    reason: str,
    attempts: int,
) -> None:
    calls = 0

    def execute(*_args: object) -> FreeSourceProcessResult:
        nonlocal calls
        calls += 1
        return result

    async def no_sleep(_seconds: float) -> None:
        return None

    runner = FreeSourceSubprocessRunner(executor=execute, sleep=no_sleep)

    with pytest.raises(FreeSourceRunnerError, match=reason) as exc_info:
        await runner.run("baostock_history", {"code": "sh.603986"})

    assert exc_info.value.reason_code == reason
    assert calls == attempts


@pytest.mark.asyncio
async def test_free_runner_circuit_breaker_opens_after_three_retryable_failures() -> None:
    calls = 0
    clock = iter((0.0, 0.0, 0.0, 0.0, 1.0)).__next__

    def execute(*_args: object) -> FreeSourceProcessResult:
        nonlocal calls
        calls += 1
        return FreeSourceProcessResult(-1, "", timed_out=True)

    async def no_sleep(_seconds: float) -> None:
        return None

    runner = FreeSourceSubprocessRunner(
        executor=execute,
        sleep=no_sleep,
        clock=clock,
        max_attempts=1,
    )
    arguments = {
        "instrument_id": "XSHG:603986",
        "since": "2026-07-01T00:00:00+00:00",
        "until": NOW.isoformat(),
    }
    for _ in range(3):
        with pytest.raises(FreeSourceRunnerError):
            await runner.run("official_gov_policy", arguments)
    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_CIRCUIT_OPEN"):
        await runner.run("official_gov_policy", arguments)
    assert calls == 3


@pytest.mark.asyncio
async def test_free_runner_breaker_isolated_by_endpoint() -> None:
    calls: list[str] = []

    def execute(endpoint: str, *_args: object) -> FreeSourceProcessResult:
        calls.append(endpoint)
        if endpoint == "official_gov_policy":
            return FreeSourceProcessResult(-1, "", timed_out=True)
        return FreeSourceProcessResult(0, '{"ok":true,"rows":[]}')

    runner = FreeSourceSubprocessRunner(
        executor=execute,
        max_attempts=1,
        failure_threshold=1,
    )
    arguments = {
        "instrument_id": "XSHG:603986",
        "since": "2026-07-01T00:00:00+00:00",
        "until": NOW.isoformat(),
    }

    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_TIMEOUT"):
        await runner.run("official_gov_policy", arguments)
    miit = await runner.run("official_miit_policy", arguments)
    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_CIRCUIT_OPEN"):
        await runner.run("official_gov_policy", arguments)

    assert miit.rows == []
    assert calls == ["official_gov_policy", "official_miit_policy"]


@pytest.mark.asyncio
async def test_free_runner_spaces_same_domain_requests_by_at_least_five_seconds() -> None:
    sleeps: list[float] = []
    calls = 0

    def execute(*_args: object) -> FreeSourceProcessResult:
        nonlocal calls
        calls += 1
        return FreeSourceProcessResult(0, '{"ok":true,"rows":[]}')

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    runner = FreeSourceSubprocessRunner(
        executor=execute,
        sleep=record_sleep,
        rate_clock=iter((0.0, 1.0, 5.0)).__next__,
    )
    arguments = {
        "instrument_id": "XSHG:603986",
        "since": "2026-07-01T00:00:00+00:00",
        "until": NOW.isoformat(),
    }

    first = await runner.run("official_gov_policy", arguments)
    second = await runner.run("official_gov_policy", arguments)

    assert first.rows == second.rows == []
    assert calls == 2
    assert sleeps == [4.0]


@pytest.mark.asyncio
async def test_nonzero_child_exit_fails_even_when_stdout_claims_success() -> None:
    runner = FreeSourceSubprocessRunner(
        executor=lambda *_args: FreeSourceProcessResult(2, '{"ok":true,"rows":[]}'),
        max_attempts=1,
    )

    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_PROCESS_FAILED"):
        await runner.run(
            "baostock_history",
            {
                "code": "sh.603986",
                "fields": "date,code,open,high,low,close,volume,amount",
                "start_date": "2026-07-01",
                "end_date": "2026-07-30",
            },
        )


class _OfficialTestResponse:
    def __init__(
        self,
        *,
        body: bytes,
        url: str,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self._body = body
        self._url = url

    def __enter__(self) -> _OfficialTestResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def geturl(self) -> str:
        return self._url


class _OfficialTestOpener:
    def __init__(self, outcomes: list[_OfficialTestResponse | BaseException]) -> None:
        self._outcomes = outcomes
        self.requests: list[tuple[Request, float]] = []

    def open(self, request: Request, *, timeout: float) -> _OfficialTestResponse:
        self.requests.append((request, timeout))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _use_official_test_opener(
    monkeypatch: pytest.MonkeyPatch,
    *outcomes: _OfficialTestResponse | BaseException,
) -> _OfficialTestOpener:
    opener = _OfficialTestOpener(list(outcomes))
    monkeypatch.setattr(free_source_worker, "build_opener", lambda *_handlers: opener)
    return opener


def _robots_response(
    endpoint: str,
    body: bytes = b"User-agent: *\nAllow: /\n",
    *,
    status: int = 200,
) -> _OfficialTestResponse:
    page_url = free_source_worker._OFFICIAL_URLS[endpoint]
    host = urlsplit(page_url).hostname
    assert host is not None
    return _OfficialTestResponse(
        body=body,
        url=f"https://{host}/robots.txt",
        status=status,
        headers={"Content-Type": "text/plain"},
    )


def test_official_robots_cross_domain_redirect_is_rejected_before_a_second_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    class RedirectTransport(BaseHandler):
        def default_open(self, request: Request) -> object:
            requests.append(request.full_url)
            headers = Message()
            headers["Location"] = "https://www.gov.cn/redirected"
            response = addinfourl(io.BytesIO(b""), headers, request.full_url, code=302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(
        free_source_worker,
        "build_opener",
        lambda *handlers: build_opener(RedirectTransport(), *handlers),
    )

    with pytest.raises(RuntimeError, match="FREE_SOURCE_FORBIDDEN"):
        free_source_worker._official_page(
            "official_gov_policy",
            {"instrument_id": "XSHG:603986"},
            max_rows=10,
            max_bytes=4096,
            clock=lambda: 0.0,
            sleep=lambda _delay: None,
        )

    assert requests == ["https://sousuo.www.gov.cn/robots.txt"]


def test_official_same_host_redirect_obeys_domain_spacing() -> None:
    requests: list[str] = []

    class RedirectTransport(BaseHandler):
        def default_open(self, request: Request) -> object:
            requests.append(request.full_url)
            headers = Message()
            if len(requests) == 1:
                headers["Location"] = "/redirected"
                response = addinfourl(io.BytesIO(b""), headers, request.full_url, code=302)
                response.msg = "Found"
                return response
            response = addinfourl(io.BytesIO(b"ok"), headers, request.full_url, code=200)
            response.msg = "OK"
            return response

    sleeps: list[float] = []
    limiter = free_source_worker._DomainRequestLimiter(
        clock=iter((0.0, 1.0, 5.0)).__next__,
        sleep=sleeps.append,
    )
    request = Request("https://sousuo.www.gov.cn/start")
    limiter.before_request(request.full_url)
    opener = build_opener(
        RedirectTransport(),
        free_source_worker._AllowlistedRedirectHandler("official_gov_policy", limiter),
    )

    with opener.open(request, timeout=10) as response:
        assert response.read() == b"ok"

    assert requests == [
        "https://sousuo.www.gov.cn/start",
        "https://sousuo.www.gov.cn/redirected",
    ]
    assert sleeps == [4.0]


@pytest.mark.parametrize(
    ("endpoint", "structure_token"),
    [
        ("official_gov_policy", "zcwjk-list"),
        ("official_miit_policy", "zwgk-list"),
    ],
)
def test_official_robots_allow_and_valid_empty_page_are_explicit_no_change(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    structure_token: str,
) -> None:
    page_url = free_source_worker._OFFICIAL_URLS[endpoint]
    opener = _use_official_test_opener(
        monkeypatch,
        _robots_response(endpoint),
        _OfficialTestResponse(
            body=(
                f'<html><body><section class="{structure_token}"></section></body></html>'
            ).encode(),
            url=page_url,
            headers={"Content-Type": "text/html"},
        ),
    )
    sleeps: list[float] = []

    rows, response = free_source_worker._official_page(
        endpoint,
        {"instrument_id": "XSHG:603986"},
        max_rows=10,
        max_bytes=4096,
        clock=iter((0.0, 1.0, 5.0)).__next__,
        sleep=sleeps.append,
    )

    assert rows == []
    assert response["not_modified"] is False
    host = urlsplit(page_url).hostname
    assert host is not None
    assert [request.full_url for request, _timeout in opener.requests] == [
        f"https://{host}/robots.txt",
        page_url,
    ]
    assert [timeout for _request, timeout in opener.requests] == [10.0, 10.0]
    assert all(
        request.get_header("User-agent") == "StockMarketAgent-R1-metadata/1.0 (personal-research)"
        for request, _timeout in opener.requests
    )
    assert sleeps == [4.0]


@pytest.mark.parametrize("endpoint", ["official_gov_policy", "official_miit_policy"])
def test_official_200_without_endpoint_structure_is_schema_drift(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    _use_official_test_opener(
        monkeypatch,
        _robots_response(endpoint),
        _OfficialTestResponse(
            body=b"<html><body><p>No known result structure</p></body></html>",
            url=free_source_worker._OFFICIAL_URLS[endpoint],
            headers={"Content-Type": "text/html"},
        ),
    )

    with pytest.raises(RuntimeError, match="FREE_SOURCE_SCHEMA_DRIFT"):
        free_source_worker._official_page(
            endpoint,
            {"instrument_id": "XSHG:603986"},
            max_rows=10,
            max_bytes=4096,
            clock=iter((0.0, 1.0, 5.0)).__next__,
            sleep=lambda _delay: None,
        )


def test_official_robots_disallow_prevents_page_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _use_official_test_opener(
        monkeypatch,
        _robots_response(
            "official_gov_policy",
            b"User-agent: *\nDisallow: /\n",
        ),
    )

    with pytest.raises(RuntimeError, match="FREE_SOURCE_FORBIDDEN"):
        free_source_worker._official_page(
            "official_gov_policy",
            {"instrument_id": "XSHG:603986"},
            max_rows=10,
            max_bytes=4096,
            clock=lambda: 0.0,
            sleep=lambda _delay: None,
        )

    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    "robots_outcome",
    [
        URLError("offline"),
        _robots_response("official_miit_policy", status=503),
    ],
)
def test_official_robots_failure_or_non_200_prevents_page_request(
    monkeypatch: pytest.MonkeyPatch,
    robots_outcome: _OfficialTestResponse | BaseException,
) -> None:
    opener = _use_official_test_opener(monkeypatch, robots_outcome)

    with pytest.raises(RuntimeError, match="FREE_SOURCE_UNAVAILABLE"):
        free_source_worker._official_page(
            "official_miit_policy",
            {"instrument_id": "XSHG:603986"},
            max_rows=10,
            max_bytes=4096,
            clock=lambda: 0.0,
            sleep=lambda _delay: None,
        )

    assert len(opener.requests) == 1


def test_official_304_is_explicit_no_change_after_robots_allow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["ETag"] = '"policy-v2"'
    not_modified = HTTPError(
        free_source_worker._OFFICIAL_URLS["official_gov_policy"],
        304,
        "Not Modified",
        headers,
        io.BytesIO(b""),
    )
    _use_official_test_opener(
        monkeypatch,
        _robots_response("official_gov_policy"),
        not_modified,
    )

    rows, response = free_source_worker._official_page(
        "official_gov_policy",
        {"instrument_id": "XSHG:603986", "if_none_match": '"policy-v1"'},
        max_rows=10,
        max_bytes=4096,
        clock=iter((0.0, 1.0, 5.0)).__next__,
        sleep=lambda _delay: None,
    )

    assert rows == []
    assert response == {
        "etag": '"policy-v2"',
        "last_modified": None,
        "not_modified": True,
    }


def test_official_worker_extracts_only_bounded_publication_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"""
    <html><body>
      <section class="zcwjk-list">
        <li data-publisher="State Council" data-category="Policy">
          <span>2026-07-30</span>
          <a href="https://www.gov.cn/zhengce/policy-1.html">Policy headline</a>
          <p>full body must not be returned</p>
        </li>
      </section>
    </body></html>
    """
    _use_official_test_opener(
        monkeypatch,
        _robots_response("official_gov_policy"),
        _OfficialTestResponse(
            body=body,
            url="https://sousuo.www.gov.cn/zcwjk/",
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": '"policy-v1"',
                "Last-Modified": "Thu, 30 Jul 2026 10:00:00 GMT",
            },
        ),
    )

    rows, response = free_source_worker._official_page(
        "official_gov_policy",
        {"instrument_id": "XSHG:603986"},
        max_rows=10,
        max_bytes=4096,
        clock=iter((0.0, 1.0, 5.0)).__next__,
        sleep=lambda _delay: None,
    )

    assert rows == [
        {
            "title": "Policy headline",
            "published_at": "2026-07-30",
            "publisher": "State Council",
            "category": "Policy",
            "url": "https://www.gov.cn/zhengce/policy-1.html",
        }
    ]
    assert response == {
        "etag": '"policy-v1"',
        "last_modified": "Thu, 30 Jul 2026 10:00:00 GMT",
        "not_modified": False,
    }
    assert "full body" not in json.dumps(rows)


def test_official_worker_fails_closed_on_captcha_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_official_test_opener(
        monkeypatch,
        _robots_response("official_miit_policy"),
        _OfficialTestResponse(
            body="<html>请输入验证码</html>".encode(),
            url="https://www.miit.gov.cn/zwgk/index.html",
            headers={"Content-Type": "text/html"},
        ),
    )

    with pytest.raises(RuntimeError, match="FREE_SOURCE_FORBIDDEN"):
        free_source_worker._official_page(
            "official_miit_policy",
            {"instrument_id": "XSHG:603986"},
            max_rows=10,
            max_bytes=1024,
            clock=iter((0.0, 1.0, 5.0)).__next__,
            sleep=lambda _delay: None,
        )


def test_free_worker_dispatches_allowlisted_akshare_with_fixed_target(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, object] = {}

    def run_akshare(request: Mapping[str, object]) -> dict[str, object]:
        seen.update(request)
        return {"ok": True, "rows": []}

    request = {
        "endpoint": "stock_zh_a_hist",
        "arguments": {
            "symbol": "603986",
            "period": "daily",
            "start_date": "20260701",
            "end_date": "20260730",
            "adjust": "",
            "timeout": 10,
        },
        "max_rows": 100,
        "max_bytes": 10000,
    }
    monkeypatch.setattr(free_source_worker, "_run_akshare", run_akshare)
    monkeypatch.setattr(free_source_worker.sys, "stdin", io.StringIO(json.dumps(request)))

    assert free_source_worker.main() == 0
    assert seen["function"] == "stock_zh_a_hist"
    assert seen["arguments"] == request["arguments"]
    assert json.loads(capsys.readouterr().out) == {"ok": True, "rows": []}
