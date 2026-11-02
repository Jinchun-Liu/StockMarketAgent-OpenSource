import asyncio
import copy
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.common.errors import ApplicationError
from stock_assistant.config.yaml_config import load_versioned_config
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import FetchRequest
from stock_assistant.modules.ingestion.r1_sources import source_batch_from_fetch
from stock_assistant.platform.publication.public_safety import PublicSafetyError, scan_public_json
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_LICENSE_ID,
    FREE_SOURCE_MATRIX,
    FREE_SOURCE_RIGHTS_HASH,
    FreeLiveAdapter,
    configured_research_preview_sources,
)
from stock_assistant.platform.sources.research_preview_runner import (
    PROMAX_BASE_URL,
    PROMAX_PROVIDER,
    RESEARCH_PREVIEW_BASE_URL,
    RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS,
    RESEARCH_PREVIEW_SOURCE_IDS,
    RESEARCH_PREVIEW_TOTAL_TIMEOUT_SECONDS,
    AccountRateLimiter,
    HttpxResearchPreviewRunner,
    PostgresAccountRateLimiter,
    ResearchPreviewRunnerError,
)

NOW = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)


def test_research_preview_timeout_budget_matches_provider_contract() -> None:
    assert RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS == 30.0
    assert RESEARCH_PREVIEW_TOTAL_TIMEOUT_SECONDS >= (
        2 * RESEARCH_PREVIEW_READ_TIMEOUT_SECONDS + 1.0
    )


def _request(capability: str) -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            request_id=UUID(int=501),
            correlation_id=UUID(int=502),
            actor_id="r1-research-preview-test",
            as_of=NOW,
            requested_at=NOW - timedelta(seconds=1),
        ),
        source_id=RESEARCH_PREVIEW_SOURCE_IDS[capability],
        capability=capability,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        since=NOW - timedelta(days=30),
        until=NOW,
        instrument_ids=["XSHG:603986"],
        limit=100,
    )


def test_config_enables_exactly_one_research_route_per_r1_capability() -> None:
    configured = configured_research_preview_sources(
        load_versioned_config("configs/sources.yaml").data
    )

    assert {item.source_id for item in configured} == set(RESEARCH_PREVIEW_SOURCE_IDS.values())
    assert {item.capability for item in configured} == set(RESEARCH_PREVIEW_SOURCE_IDS)
    assert all(item.enabled for item in configured)
    assert set(RESEARCH_PREVIEW_SOURCE_IDS.values()) <= {
        item.source_id for item in FREE_SOURCE_MATRIX
    }


def test_config_rejects_a_changed_provider_host() -> None:
    document = copy.deepcopy(load_versioned_config("configs/sources.yaml").data)
    source = next(item for item in document["sources"] if item["id"] == "research_preview_market")
    source["dev_live"]["allowed_hosts"] = ["example.com"]

    with pytest.raises(RuntimeError, match="host allowlist"):
        configured_research_preview_sources(document)


@pytest.mark.parametrize(
    ("capability", "expected_endpoint", "expected_key"),
    (
        ("statutory.announcements", "anns_d", "ts_code"),
        ("financial.changes", "income", "ts_code"),
        ("market.snapshot", "daily", "ts_code"),
        ("company_industry.news", "news", "src"),
        ("macro.flashes", "npr", "ptype"),
    ),
)
def test_adapter_plans_only_the_fixed_five_operations(
    capability: str,
    expected_endpoint: str,
    expected_key: str,
) -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS[capability])

    arguments = adapter.function_arguments(_request(capability))

    assert adapter.endpoint_name == expected_endpoint
    assert expected_key in arguments
    assert "token" not in arguments


@pytest.mark.parametrize(
    ("capability", "row", "expected_title"),
    (
        (
            "statutory.announcements",
            {
                "ann_date": "20260810",
                "ts_code": "603986.SH",
                "name": "兆易创新",
                "title": "兆易创新关于董事会会议的公告",
                "url": "https://static.cninfo.com.cn/finalpage/test.pdf",
                "rec_time": "2026-08-10 09:00:00",
            },
            "兆易创新关于董事会会议的公告",
        ),
        (
            "financial.changes",
            {
                "ts_code": "603986.SH",
                "ann_date": "20260810",
                "end_date": "20260630",
                "basic_eps": "2.31",
                "total_revenue": "4100000000.00",
                "n_income_attr_p": "520000000.00",
            },
            "兆易创新 20260630 财务报表摘要",
        ),
        (
            "market.snapshot",
            {
                "ts_code": "603986.SH",
                "trade_date": "20260810",
                "open": "100.10",
                "high": "103.20",
                "low": "99.80",
                "close": "102.50",
                "vol": "1000.00",
                "amount": "5000.00",
            },
            "兆易创新 20260810 日行情",
        ),
        (
            "company_industry.news",
            {
                "datetime": "2026-08-10 10:00:00",
                "title": "兆易创新发布新一代存储产品",
                "channels": "公司",
            },
            "兆易创新发布新一代存储产品",
        ),
        (
            "macro.flashes",
            {
                "pubtime": "2026-08-10 08:00:00",
                "title": "国务院发布科技创新支持政策",
                "url": "https://www.gov.cn/zhengce/test.htm",
                "pcode": "国发(2026)1号",
                "puborg": "国务院",
                "ptype": "科技",
            },
            "国务院发布科技创新支持政策",
        ),
    ),
)
def test_adapter_normalizes_allowlisted_fields_for_all_five_capabilities(
    capability: str,
    row: dict[str, object],
    expected_title: str,
) -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS[capability])
    request = _request(capability)

    batch = adapter.normalize(
        request,
        [row],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )

    assert len(batch.records) == 1
    assert batch.records[0].title == expected_title
    assert batch.records[0].provenance is not None
    assert batch.records[0].provenance.runtime_mode == "dev_live"
    assert batch.records[0].provenance.rights_status == "unverified"
    assert "content" not in batch.records[0].payload
    source_batch = source_batch_from_fetch(
        fetch_batch=batch,
        capability=capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    assert source_batch.coverages[0].status.value == "ok"


def test_income_uses_actual_disclosure_date_for_point_in_time_visibility() -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS["financial.changes"])
    request = _request("financial.changes")
    base_row = {
        "ts_code": "603986.SH",
        "ann_date": "20260801",
        "end_date": "20260630",
        "basic_eps": "2.31",
    }

    visible = adapter.normalize(
        request,
        [{**base_row, "f_ann_date": "20260810"}],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )
    future = adapter.normalize(
        request,
        [{**base_row, "f_ann_date": "20260811"}],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )

    assert len(visible.records) == 1
    assert visible.records[0].event_time == datetime(2026, 8, 9, 16, 0, tzinfo=UTC)
    assert visible.records[0].published_at == visible.records[0].event_time
    assert future.records == []


def test_promax_announcement_cninfo_attachment_survives_public_evidence_scan() -> None:
    capability = "statutory.announcements"
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS[capability])
    fetch_batch = adapter.normalize(
        _request(capability),
        [
            {
                "ann_date": "20260810",
                "ts_code": "603986.SH",
                "name": "兆易创新",
                "title": "兆易创新关于董事会会议的公告",
                "url": "https://static.cninfo.com.cn/finalpage/test.pdf",
                "rec_time": "2026-08-10 09:00:00",
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )
    source_batch = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability=capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    evidence = source_batch.events[0].evidence[0]

    assert str(evidence.source_url) == "https://static.cninfo.com.cn/finalpage/test.pdf"
    scan_public_json(
        json.dumps(
            {"evidence": [evidence.model_dump(mode="json")]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(),
        label="promax-announcement-public-evidence",
    )


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "https://static.cninfo.com.cn/other/test.pdf",
        "https://static.cninfo.com.cn/finalpage/test.html",
        "https://static.cninfo.com.cn/finalpage/test.pdf?token=redacted",
        "https://static.cninfo.com.cn/finalpage/../other.pdf",
        "https://static.cninfo.com.cn/finalpage/%2e%2e/other.pdf",
        "https://static.cninfo.com.cn/finalpage//test.pdf",
        "https://static.cninfo.com.cn/finalpage/%2Ftest.pdf",
        "https://example.com/finalpage/test.pdf",
    ),
)
def test_promax_announcement_drops_noncanonical_attachment_urls(unsafe_url: str) -> None:
    capability = "statutory.announcements"
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS[capability])
    fetch_batch = adapter.normalize(
        _request(capability),
        [
            {
                "ann_date": "20260810",
                "ts_code": "603986.SH",
                "title": "兆易创新关于董事会会议的公告",
                "url": unsafe_url,
                "rec_time": "2026-08-10 09:00:00",
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )
    source_batch = source_batch_from_fetch(
        fetch_batch=fetch_batch,
        capability=capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )

    assert fetch_batch.records[0].canonical_url is None
    assert str(source_batch.events[0].evidence[0].source_url) == (
        "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71"
    )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            json.dumps({"source_url": unsafe_url}, separators=(",", ":")).encode(),
            label="unsafe-promax-announcement-url",
        )


def test_company_news_drops_unrelated_titles_without_advancing_a_fact() -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS["company_industry.news"])

    batch = adapter.normalize(
        _request("company_industry.news"),
        [
            {
                "datetime": "2026-08-10 10:00:00",
                "title": "其他公司发布新产品",
                "channels": "公司",
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )

    assert batch.records == []


def test_adapter_drops_rows_before_the_requested_pit_window() -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS["market.snapshot"])

    batch = adapter.normalize(
        _request("market.snapshot"),
        [
            {
                "ts_code": "603986.SH",
                "trade_date": "20260701",
                "close": "99.00",
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )

    assert batch.records == []


def test_cursor_keeps_a_seven_day_overlap_for_late_provider_rows() -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS["market.snapshot"])
    first_request = _request("market.snapshot")
    first = adapter.normalize(
        first_request,
        [],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )
    next_until = NOW + timedelta(days=1)
    next_request = first_request.model_copy(
        update={
            "context": first_request.context.model_copy(update={"as_of": next_until}),
            "since": NOW,
            "until": next_until,
            "cursor": first.next_cursor,
        }
    )

    arguments = adapter.function_arguments(next_request)
    late = adapter.normalize(
        next_request,
        [
            {
                "ts_code": "603986.SH",
                "trade_date": "20260807",
                "close": "101.20",
            }
        ],
        execution_started_at=next_until,
        execution_completed_at=next_until + timedelta(seconds=1),
    )

    assert arguments["start_date"] == "20260803"
    assert len(late.records) == 1
    assert late.source_watermark == datetime(2026, 8, 6, 16, 0, tzinfo=UTC)


def test_adapter_fails_closed_instead_of_slicing_and_advancing_cursor() -> None:
    adapter = FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS["market.snapshot"])
    request = _request("market.snapshot").model_copy(update={"limit": 1})

    with pytest.raises(ApplicationError) as exc_info:
        adapter.normalize(
            request,
            [
                {"ts_code": "603986.SH", "trade_date": "20260809", "close": "101.20"},
                {"ts_code": "603986.SH", "trade_date": "20260810", "close": "102.50"},
            ],
            execution_started_at=NOW,
            execution_completed_at=NOW + timedelta(seconds=1),
        )

    assert exc_info.value.safe_message == "R1_SOURCE_RESULT_TRUNCATED"


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.asyncio
async def test_http_runner_uses_provider_protocol_and_shared_account_quota() -> None:
    payloads: list[dict[str, object]] = []
    fake_time = FakeTime()
    limiter = AccountRateLimiter(clock=fake_time.monotonic)

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_date", "close"],
                    "items": [["603986.SH", "20260810", "102.50"]],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        first = HttpxResearchPreviewRunner(
            token=SecretStr("test-only-token"),
            client=client,
            limiter=limiter,
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
        )
        second = HttpxResearchPreviewRunner(
            token=SecretStr("test-only-token"),
            client=client,
            limiter=limiter,
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
        )
        arguments = {
            "ts_code": "603986.SH",
            "start_date": "20260801",
            "end_date": "20260810",
        }
        await first.run("daily", arguments)
        await second.run("daily", arguments)

    assert fake_time.value == 2.0
    assert payloads[0]["api_name"] == "daily"
    assert payloads[0]["params"] == arguments
    assert payloads[0]["token"] == payloads[1]["token"]
    assert "test-only-token" not in repr(first)


@pytest.mark.asyncio
async def test_promax_runner_uses_fixed_get_path_and_header_without_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["ts_code", "ann_date", "end_date", "basic_eps"],
                    "items": [["603986.SH", "20260810", "20260630", "2.31"]],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url=PROMAX_BASE_URL,
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("tsr_test-only-key"),
            client=client,
            provider=PROMAX_PROVIDER,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        rows = await runner.run(
            "income",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
        )

    assert rows == [
        {
            "ts_code": "603986.SH",
            "ann_date": "20260810",
            "end_date": "20260630",
            "basic_eps": "2.31",
        }
    ]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/tushare/pro/income"
    assert requests[0].headers["X-API-Key"] == "tsr_test-only-key"
    assert requests[0].url.params["ts_code"] == "603986.SH"
    assert requests[0].url.params["start_date"] == "20260801"
    assert requests[0].url.params["end_date"] == "20260810"
    assert (
        requests[0]
        .url.params["fields"]
        .startswith("ts_code,ann_date,f_ann_date,end_date,report_type")
    )
    assert "limit" not in requests[0].url.params
    assert "tsr_test-only-key" not in str(requests[0].url)


@pytest.mark.parametrize(
    ("endpoint_name", "arguments", "expected_fields", "expected_limit"),
    (
        (
            "anns_d",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
            None,
            "50",
        ),
        (
            "income",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
            "ts_code,ann_date,f_ann_date,end_date,report_type",
            None,
        ),
        (
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 00:00:00",
                "end_date": "2026-08-10 06:00:00",
            },
            "datetime,title,channels",
            "100",
        ),
        (
            "npr",
            {
                "ptype": "科技",
                "start_date": "2026-08-05 00:00:00",
                "end_date": "2026-08-10 00:00:00",
            },
            "pubtime,title,url,pcode,puborg,ptype",
            "100",
        ),
    ),
)
@pytest.mark.asyncio
async def test_promax_get_contract_uses_endpoint_specific_bounded_controls(
    endpoint_name: str,
    arguments: dict[str, str],
    expected_fields: str | None,
    expected_limit: str | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"code": 0, "data": {"fields": ["placeholder"], "items": []}},
        )

    async with httpx.AsyncClient(
        base_url=PROMAX_BASE_URL,
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("tsr_test-only-key"),
            client=client,
            provider=PROMAX_PROVIDER,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        assert await runner.run(endpoint_name, arguments) == []

    assert len(requests) >= 1
    for request in requests:
        if expected_fields is None:
            assert "fields" not in request.url.params
        else:
            assert request.url.params["fields"].startswith(expected_fields)
        if expected_limit is None:
            assert "limit" not in request.url.params
        else:
            assert request.url.params["limit"] == expected_limit


@pytest.mark.asyncio
async def test_promax_accepts_a_canonical_empty_result_without_inventing_rows() -> None:
    async with httpx.AsyncClient(
        base_url=PROMAX_BASE_URL,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"code": 0, "data": {"fields": [], "items": []}},
            )
        ),
        follow_redirects=False,
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("tsr_test-only-key"),
            client=client,
            provider=PROMAX_PROVIDER,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )

        assert (
            await runner.run(
                "npr",
                {
                    "ptype": "科技",
                    "start_date": "2026-08-05 00:00:00",
                    "end_date": "2026-08-10 00:00:00",
                },
            )
            == []
        )


@pytest.mark.asyncio
async def test_promax_rejects_items_without_a_field_schema() -> None:
    async with httpx.AsyncClient(
        base_url=PROMAX_BASE_URL,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"code": 0, "data": {"fields": [], "items": [["hidden"]]}},
            )
        ),
        follow_redirects=False,
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("tsr_test-only-key"),
            client=client,
            provider=PROMAX_PROVIDER,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )

        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(
                "npr",
                {
                    "ptype": "科技",
                    "start_date": "2026-08-05 00:00:00",
                    "end_date": "2026-08-10 00:00:00",
                },
            )

    assert exc_info.value.reason_code == "RESEARCH_DATA_SCHEMA_CHANGED"


@pytest.mark.parametrize(
    ("endpoint_name", "arguments", "provider_limit"),
    (
        (
            "anns_d",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
            50,
        ),
        (
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 00:00:00",
                "end_date": "2026-08-10 06:00:00",
            },
            100,
        ),
        (
            "npr",
            {
                "ptype": "科技",
                "start_date": "2026-08-05 00:00:00",
                "end_date": "2026-08-10 00:00:00",
            },
            100,
        ),
    ),
)
@pytest.mark.asyncio
async def test_promax_runner_rejects_each_full_requested_page_as_truncated(
    endpoint_name: str,
    arguments: dict[str, str],
    provider_limit: int,
) -> None:
    async with httpx.AsyncClient(
        base_url=PROMAX_BASE_URL,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fields": ["row"],
                        "items": [[index] for index in range(provider_limit)],
                    },
                },
            )
        ),
        follow_redirects=False,
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("tsr_test-only-key"),
            client=client,
            provider=PROMAX_PROVIDER,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(endpoint_name, arguments)

    assert exc_info.value.reason_code == "RESEARCH_DATA_RESULT_TRUNCATED"


@pytest.mark.asyncio
async def test_runner_rejects_wrong_origin_or_redirect_following() -> None:
    async with httpx.AsyncClient(base_url="https://example.invalid") as wrong_origin:
        with pytest.raises(ValueError, match="fixed provider origin"):
            HttpxResearchPreviewRunner(
                token=SecretStr("sentinel"),
                client=wrong_origin,
            )
    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        follow_redirects=True,
    ) as redirecting:
        with pytest.raises(ValueError, match="fixed provider origin"):
            HttpxResearchPreviewRunner(
                token=SecretStr("sentinel"),
                client=redirecting,
            )


@pytest.mark.asyncio
async def test_http_runner_maps_permission_and_upstream_text_to_stable_codes() -> None:
    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"code": 2002, "msg": "private provider detail"},
            )
        ),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(
                "income",
                {
                    "ts_code": "603986.SH",
                    "start_date": "20260801",
                    "end_date": "20260810",
                },
            )

    assert exc_info.value.reason_code == "RESEARCH_DATA_PERMISSION_BLOCKED"
    assert "private provider detail" not in str(exc_info.value)
    assert "sentinel" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("endpoint_name", "arguments", "expected_requests"),
    (
        (
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 02:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
            6,
        ),
        (
            "npr",
            {
                "ptype": "科技",
                "start_date": "2026-07-11 08:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
            6,
        ),
    ),
)
@pytest.mark.asyncio
async def test_discovery_endpoints_split_wide_windows_before_transport(
    endpoint_name: str,
    arguments: dict[str, str],
    expected_requests: int,
) -> None:
    payloads: list[dict[str, object]] = []
    fake_time = FakeTime()

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["published_at"],
                    "items": [],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(clock=fake_time.monotonic),
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
        )

        assert await runner.run(endpoint_name, arguments) == []

    assert len(payloads) == expected_requests
    first_params = cast(dict[str, object], payloads[0]["params"])
    last_params = cast(dict[str, object], payloads[-1]["params"])
    assert first_params != arguments
    assert first_params["start_date"] == arguments["start_date"]
    assert last_params["end_date"] == arguments["end_date"]


@pytest.mark.parametrize(
    ("endpoint_name", "arguments"),
    (
        (
            "news",
            {
                "src": "eastmoney",
                "start_date": "not-a-date",
                "end_date": "2026-08-10 08:00:00",
            },
        ),
        (
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 08:00:00",
                "end_date": "2026-08-09 08:00:00",
            },
        ),
        (
            "npr",
            {
                "ptype": "科技",
                "start_date": "2026-06-01 08:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_discovery_endpoints_reject_invalid_or_unbounded_windows(
    endpoint_name: str,
    arguments: dict[str, str],
) -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            retry_delays_seconds=(),
        )

        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(endpoint_name, arguments)

    assert requests == 0
    assert exc_info.value.reason_code == "RESEARCH_DATA_REQUEST_INVALID"


@pytest.mark.asyncio
async def test_segmented_discovery_deduplicates_inclusive_window_boundaries() -> None:
    requests = 0
    fake_time = FakeTime()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["datetime", "title", "channels"],
                    "items": [["2026-08-10 02:00:00", "兆易创新快讯", "公司"]],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(clock=fake_time.monotonic),
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
        )

        rows = await runner.run(
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 06:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
        )

    assert requests == 2
    assert rows == [
        {
            "datetime": "2026-08-10 02:00:00",
            "title": "兆易创新快讯",
            "channels": "公司",
        }
    ]


@pytest.mark.asyncio
async def test_segment_failure_discards_earlier_segment_rows() -> None:
    requests = 0
    fake_time = FakeTime()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fields": ["datetime", "title", "channels"],
                        "items": [["2026-08-10 01:00:00", "兆易创新快讯", "公司"]],
                    },
                },
            )
        return httpx.Response(503)

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(clock=fake_time.monotonic),
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
        )

        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(
                "news",
                {
                    "src": "eastmoney",
                    "start_date": "2026-08-10 06:00:00",
                    "end_date": "2026-08-10 08:00:00",
                },
            )

    assert requests == 2
    assert exc_info.value.reason_code == "RESEARCH_DATA_UPSTREAM_UNAVAILABLE"


@pytest.mark.asyncio
async def test_total_timeout_discards_rows_from_completed_segments() -> None:
    requests = 0
    never_respond = asyncio.Event()
    fake_time = FakeTime()

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fields": ["datetime", "title", "channels"],
                        "items": [["2026-08-10 01:00:00", "兆易创新快讯", "公司"]],
                    },
                },
            )
        await never_respond.wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(clock=fake_time.monotonic),
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
            total_timeout_seconds=0.01,
        )

        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(
                "news",
                {
                    "src": "eastmoney",
                    "start_date": "2026-08-10 06:00:00",
                    "end_date": "2026-08-10 08:00:00",
                },
            )

    assert requests == 2
    assert exc_info.value.reason_code == "RESEARCH_DATA_TIMEOUT"


@pytest.mark.asyncio
async def test_total_timeout_cancels_a_stalled_response_body() -> None:
    never_finish = asyncio.Event()

    class StalledBody(httpx.AsyncByteStream):
        cancelled = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"code":0,"data":'
            try:
                await never_finish.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    body = StalledBody()
    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=body)),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
            total_timeout_seconds=0.01,
        )

        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(
                "npr",
                {
                    "ptype": "科技",
                    "start_date": "2026-08-09 08:00:00",
                    "end_date": "2026-08-10 08:00:00",
                },
            )

    assert exc_info.value.reason_code == "RESEARCH_DATA_TIMEOUT"
    assert body.cancelled is True


@pytest.mark.asyncio
async def test_external_cancellation_is_not_mapped_to_a_provider_timeout() -> None:
    entered_transport = asyncio.Event()
    never_finish = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        entered_transport.set()
        await never_finish.wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            retry_delays_seconds=(),
        )
        task = asyncio.create_task(
            runner.run(
                "daily",
                {
                    "ts_code": "603986.SH",
                    "start_date": "20260801",
                    "end_date": "20260810",
                },
            )
        )
        await entered_transport.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_retryable_response_retries_once_and_reserves_another_quota_slot() -> None:
    requests = 0
    fake_time = FakeTime()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_date", "close"],
                    "items": [["603986.SH", "20260810", "102.50"]],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(clock=fake_time.monotonic),
            retry_delays_seconds=(0.25,),
            sleep=fake_time.sleep,
        )

        rows = await runner.run(
            "daily",
            {
                "ts_code": "603986.SH",
                "start_date": "20260801",
                "end_date": "20260810",
            },
        )

    assert requests == 2
    assert fake_time.value == 2.0
    assert rows == [{"ts_code": "603986.SH", "trade_date": "20260810", "close": "102.50"}]


@pytest.mark.parametrize("total_timeout_seconds", (0.0, -1.0, 240.1))
@pytest.mark.asyncio
async def test_http_runner_rejects_an_invalid_total_timeout(
    total_timeout_seconds: float,
) -> None:
    async with httpx.AsyncClient(base_url=RESEARCH_PREVIEW_BASE_URL) as client:
        with pytest.raises(ValueError, match="total timeout"):
            HttpxResearchPreviewRunner(
                token=SecretStr("sentinel"),
                client=client,
                total_timeout_seconds=total_timeout_seconds,
            )


@pytest.mark.parametrize(
    ("endpoint_name", "arguments", "page_limit"),
    (
        (
            "anns_d",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
            2_000,
        ),
        (
            "income",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
            100,
        ),
        (
            "daily",
            {"ts_code": "603986.SH", "start_date": "20260801", "end_date": "20260810"},
            2_000,
        ),
        (
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 08:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
            1_500,
        ),
        (
            "npr",
            {
                "ptype": "科技",
                "start_date": "2026-08-10 08:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
            500,
        ),
    ),
)
@pytest.mark.asyncio
async def test_http_runner_rejects_each_full_provider_page_as_truncated(
    endpoint_name: str,
    arguments: dict[str, str],
    page_limit: int,
) -> None:
    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fields": ["row"],
                        "items": [[index] for index in range(page_limit)],
                    },
                },
            )
        ),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )

        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(endpoint_name, arguments)

    assert exc_info.value.reason_code == "RESEARCH_DATA_RESULT_TRUNCATED"


@pytest.mark.asyncio
async def test_segmented_result_accepts_complete_pages_above_legacy_global_row_limit() -> None:
    requests = 0
    fake_time = FakeTime()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        payload = json.loads(request.content)
        params = cast(dict[str, str], payload["params"])
        prefix = params["start_date"]
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "fields": ["row"],
                    "items": [[f"{prefix}-{index}"] for index in range(1_100)],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(clock=fake_time.monotonic),
            retry_delays_seconds=(),
            sleep=fake_time.sleep,
        )

        rows = await runner.run(
            "news",
            {
                "src": "eastmoney",
                "start_date": "2026-08-10 06:00:00",
                "end_date": "2026-08-10 08:00:00",
            },
        )

    assert requests == 2
    assert len(rows) == 2_200
    assert rows[0] != rows[-1]


class _ChunkedBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in (b"x" * 1_048_576, b"y" * 1_048_576, b"z", b"unread"):
            self.consumed += 1
            yield chunk


@pytest.mark.asyncio
async def test_http_runner_aborts_stream_before_buffering_an_oversized_response() -> None:
    stream = _ChunkedBody()
    async with httpx.AsyncClient(
        base_url=RESEARCH_PREVIEW_BASE_URL,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=stream)),
    ) as client:
        runner = HttpxResearchPreviewRunner(
            token=SecretStr("sentinel"),
            client=client,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        with pytest.raises(ResearchPreviewRunnerError) as exc_info:
            await runner.run(
                "income",
                {
                    "ts_code": "603986.SH",
                    "start_date": "20260801",
                    "end_date": "20260810",
                },
            )

    assert exc_info.value.reason_code == "RESEARCH_DATA_RESPONSE_TOO_LARGE"
    assert stream.consumed == 3


class _FakePostgresConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, parameters: dict[str, object]) -> None:
        self.statements.append((str(statement), parameters))


class _FakePostgresConnectionContext:
    def __init__(self, connection: _FakePostgresConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakePostgresConnection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakePostgresEngine:
    def __init__(self, connection: _FakePostgresConnection) -> None:
        self._connection = connection

    def connect(self) -> _FakePostgresConnectionContext:
        return _FakePostgresConnectionContext(self._connection)


@pytest.mark.asyncio
async def test_postgres_limiter_uses_one_database_lock_for_all_processes() -> None:
    connection = _FakePostgresConnection()
    limiter = PostgresAccountRateLimiter(
        cast(AsyncEngine, _FakePostgresEngine(connection)),
        minimum_interval_seconds=2.0,
    )

    await limiter.wait(asyncio.sleep)

    assert [statement for statement, _parameters in connection.statements] == [
        "SELECT pg_advisory_lock(:lock_id)",
        "SELECT pg_sleep(CAST(:minimum_interval_seconds AS double precision))",
        "SELECT pg_advisory_unlock(:lock_id)",
    ]
    assert connection.statements[1][1]["minimum_interval_seconds"] == 2.0


def test_adapter_rejects_another_instrument_before_transport() -> None:
    request = _request("market.snapshot").model_copy(update={"instrument_ids": ["XSHG:600000"]})

    with pytest.raises(ApplicationError, match="R1_TARGET_LOCKED"):
        FreeLiveAdapter(RESEARCH_PREVIEW_SOURCE_IDS["market.snapshot"]).function_arguments(request)
