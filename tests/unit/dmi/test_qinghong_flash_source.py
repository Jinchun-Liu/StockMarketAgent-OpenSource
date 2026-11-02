from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.config.yaml_config import load_versioned_config
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import FetchRequest
from stock_assistant.modules.ingestion.r1_sources import source_batch_from_fetch
from stock_assistant.platform.publication.public_safety import PublicSafetyError, scan_public_json
from stock_assistant.platform.sources.qinghong_flash_runner import (
    QINGHONG_SOURCE_IDS,
    HttpxQinghongFlashRunner,
    QinghongFlashRunnerError,
)
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_CATALOG_BY_ID,
    FREE_SOURCE_LICENSE_ID,
    FREE_SOURCE_MATRIX,
    FREE_SOURCE_RIGHTS_HASH,
    FreeDevelopmentRightsPolicy,
    FreeLiveAdapter,
    RightsGatedFreeConnector,
    configured_qinghong_sources,
)
from stock_assistant.platform.sources.research_preview_runner import AccountRateLimiter

NOW = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
SECURE_TEST_ORIGIN = "https://124.223.180.129:3220"


def _request(capability: str) -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(
            schema_version="1.0",
            request_id=UUID(int=701),
            correlation_id=UUID(int=702),
            actor_id="r1-qinghong-test",
            as_of=NOW,
            requested_at=NOW - timedelta(seconds=1),
        ),
        source_id=QINGHONG_SOURCE_IDS[capability],
        capability=capability,
        rights_snapshot_hash=FREE_SOURCE_RIGHTS_HASH,
        since=NOW - timedelta(days=1),
        until=NOW,
        instrument_ids=["XSHG:603986"],
        limit=100,
    )


def test_config_keeps_three_qinghong_routes_dormant_until_https_exists() -> None:
    definitions = configured_qinghong_sources(load_versioned_config("configs/sources.yaml").data)

    assert {item.source_id for item in definitions} == set(QINGHONG_SOURCE_IDS.values())
    assert all(not item.enabled for item in definitions)
    assert set(QINGHONG_SOURCE_IDS.values()).isdisjoint(
        {item.source_id for item in FREE_SOURCE_MATRIX}
    )
    assert set(QINGHONG_SOURCE_IDS.values()) <= set(FREE_SOURCE_CATALOG_BY_ID)


def test_config_rejects_activation_before_a_secure_origin_is_frozen() -> None:
    document = copy.deepcopy(load_versioned_config("configs/sources.yaml").data)
    source = next(item for item in document["sources"] if item["id"] == "qinghong_cls_flash")
    source["enabled"] = True

    with pytest.raises(RuntimeError, match="source boundary"):
        configured_qinghong_sources(document)


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", sorted(QINGHONG_SOURCE_IDS))
async def test_dormant_source_rights_deny_fetch_before_network(capability: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url=SECURE_TEST_ORIGIN,
        transport=httpx.MockTransport(handler),
    ) as client:
        connector = RightsGatedFreeConnector(
            adapter=FreeLiveAdapter(QINGHONG_SOURCE_IDS[capability]),
            rights_policy=FreeDevelopmentRightsPolicy(),
            runner=HttpxQinghongFlashRunner(
                api_key=SecretStr("test-only-key"),
                client=client,
                limiter=AccountRateLimiter(),
            ),
        )

        with pytest.raises(ApplicationError) as exc_info:
            await connector.fetch(_request(capability))

    assert exc_info.value.code is ErrorCode.RIGHTS_BLOCKED
    assert exc_info.value.safe_message == "FREE_RIGHTS_BLOCKED"
    assert requests == []


@pytest.mark.asyncio
async def test_runner_rejects_the_documented_plaintext_origin_without_network() -> None:
    async with httpx.AsyncClient(base_url="http://124.223.180.129:3220") as client:
        with pytest.raises(ValueError, match="HTTPS"):
            HttpxQinghongFlashRunner(
                api_key=SecretStr("test-only-key"),
                client=client,
                limiter=AccountRateLimiter(),
            )


@pytest.mark.asyncio
async def test_runner_rejects_redirect_following_before_any_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/credential"})

    async with httpx.AsyncClient(
        base_url=SECURE_TEST_ORIGIN,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="redirect"):
            HttpxQinghongFlashRunner(
                api_key=SecretStr("test-only-key"),
                client=client,
                limiter=AccountRateLimiter(),
            )

    assert requests == []


@pytest.mark.asyncio
async def test_runner_calls_only_fixed_single_source_protocol_and_drops_urls() -> None:
    seen: list[httpx.Request] = []
    published_at = int((NOW - timedelta(minutes=5)).timestamp() * 1_000)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "id": "cninfo-latest",
                "updatedTime": published_at,
                "items": [
                    {
                        "id": "announcement-1",
                        "title": "兆易创新关于董事会会议的公告",
                        "url": "http://upstream.example/item/1",
                        "pubDate": published_at,
                        "extra": {"info": "must-not-cross-boundary"},
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        base_url=SECURE_TEST_ORIGIN,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxQinghongFlashRunner(
            api_key=SecretStr("test-only-key"),
            client=client,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        rows = await runner.run("cninfo-latest", {"stock": "603986"})

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/api/s"
    assert dict(request.url.params.multi_items()) == {
        "id": "cninfo-latest",
        "stock": "603986",
    }
    assert request.headers["X-API-Key"] == "test-only-key"
    assert rows == [
        {
            "platform_id": "cninfo-latest",
            "item_id": "announcement-1",
            "title": "兆易创新关于董事会会议的公告",
            "published_at": (NOW - timedelta(minutes=5)).isoformat(),
        }
    ]
    encoded = json.dumps(rows, ensure_ascii=False)
    assert "upstream.example" not in encoded
    assert "must-not-cross-boundary" not in encoded


@pytest.mark.asyncio
async def test_runner_retries_one_rate_limit_then_stops() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429)

    async def no_wait(_seconds: float) -> None:
        return None

    async with httpx.AsyncClient(
        base_url=SECURE_TEST_ORIGIN,
        transport=httpx.MockTransport(handler),
    ) as client:
        runner = HttpxQinghongFlashRunner(
            api_key=SecretStr("test-only-key"),
            client=client,
            limiter=AccountRateLimiter(clock=lambda: 0.0),
            retry_delays_seconds=(0.0,),
            sleep=lambda seconds: sleeps.append(seconds) or no_wait(seconds),
        )
        with pytest.raises(QinghongFlashRunnerError) as exc_info:
            await runner.run("jin10", {})

    assert exc_info.value.reason_code == "QINGHONG_RATE_LIMITED"
    assert calls == 2
    assert sleeps == [0.0, 2.0]


@pytest.mark.asyncio
async def test_runner_auth_failure_never_exposes_the_key() -> None:
    key_sentinel = "test-only-key-sentinel"

    async with httpx.AsyncClient(
        base_url=SECURE_TEST_ORIGIN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(401)),
    ) as client:
        runner = HttpxQinghongFlashRunner(
            api_key=SecretStr(key_sentinel),
            client=client,
            limiter=AccountRateLimiter(),
            retry_delays_seconds=(),
        )
        with pytest.raises(QinghongFlashRunnerError) as exc_info:
            await runner.run("jin10", {})

    assert exc_info.value.reason_code == "QINGHONG_AUTH_FAILED"
    assert key_sentinel not in str(exc_info.value)
    assert key_sentinel not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("capability", "endpoint", "arguments", "expected_event_type"),
    (
        (
            "statutory.announcements",
            "cninfo-latest",
            {"stock": "603986"},
            "statutory.announcement.discovery",
        ),
        ("company_industry.news", "cls-telegraph", {}, "company.news.discovery"),
        ("macro.flashes", "jin10", {}, "macro.flash.discovery"),
    ),
)
def test_adapter_plans_and_normalizes_three_metadata_only_routes(
    capability: str,
    endpoint: str,
    arguments: dict[str, str],
    expected_event_type: str,
) -> None:
    adapter = FreeLiveAdapter(QINGHONG_SOURCE_IDS[capability])
    request = _request(capability)

    assert adapter.endpoint_name == endpoint
    assert adapter.function_arguments(request) == arguments

    title = {
        "cninfo-latest": "兆易创新关于董事会会议的公告",
        "cls-telegraph": "兆易创新发布新一代存储产品",
        "jin10": "央行发布科技企业融资支持政策",
    }[endpoint]
    batch = adapter.normalize(
        request,
        [
            {
                "platform_id": endpoint,
                "item_id": f"{endpoint}-1",
                "title": title,
                "published_at": (NOW - timedelta(minutes=5)).isoformat(),
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW + timedelta(seconds=1),
    )

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.title == title
    assert record.canonical_url is None
    assert record.fact_eligibility.value == "context_only"
    assert record.provenance is not None
    assert record.provenance.runtime_mode.value == "dev_live"
    assert record.provenance.rights_status.value == "unverified"
    assert set(record.payload) == {"platform_id", "item_id", "title", "published_at"}
    projected = source_batch_from_fetch(
        fetch_batch=batch,
        capability=capability,
        provenance=adapter.provenance,
        license_id=FREE_SOURCE_LICENSE_ID,
    )
    assert projected.coverages[0].status.value == "ok"
    assert len(projected.events) == 1
    assert projected.events[0].event_type == expected_event_type
    assert str(projected.events[0].evidence[0].source_url) == (
        "https://my.feishu.cn/wiki/T7XWwCxtIiOcLIkkXQbc5I1Tntc"
    )


def test_delayed_items_are_rechecked_and_empty_batches_do_not_advance_cursor() -> None:
    adapter = FreeLiveAdapter(QINGHONG_SOURCE_IDS["company_industry.news"])
    first_request = _request("company_industry.news")
    first = adapter.normalize(
        first_request,
        [
            {
                "platform_id": "cls-telegraph",
                "item_id": "delayed-item",
                "title": "兆易创新发布新一代存储产品",
                "published_at": (NOW - timedelta(days=3)).isoformat(),
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW,
    )
    assert len(first.records) == 1
    assert first.next_cursor is not None

    next_until = NOW + timedelta(days=1)
    next_request = first_request.model_copy(
        update={
            "context": first_request.context.model_copy(update={"as_of": next_until}),
            "since": NOW,
            "until": next_until,
            "cursor": first.next_cursor,
        }
    )
    empty = adapter.normalize(
        next_request,
        [],
        execution_started_at=next_until,
        execution_completed_at=next_until,
    )

    assert empty.records == []
    assert empty.next_cursor == first.next_cursor
    assert empty.source_watermark == NOW - timedelta(days=7)


def test_public_scanner_accepts_only_the_exact_qinghong_documentation_url() -> None:
    exact = b'{"source_url":"https://my.feishu.cn/wiki/T7XWwCxtIiOcLIkkXQbc5I1Tntc"}'
    scan_public_json(exact, label="qinghong-exact")

    with pytest.raises(PublicSafetyError, match="non-allowlisted URL"):
        scan_public_json(
            b'{"source_url":"https://my.feishu.cn/wiki/T7XWwCxtIiOcLIkkXQbc5I1Tntc?from=unsafe"}',
            label="qinghong-query",
        )


def test_company_and_macro_filters_do_not_turn_unrelated_items_into_evidence() -> None:
    company_adapter = FreeLiveAdapter(QINGHONG_SOURCE_IDS["company_industry.news"])
    macro_adapter = FreeLiveAdapter(QINGHONG_SOURCE_IDS["macro.flashes"])

    company = company_adapter.normalize(
        _request("company_industry.news"),
        [
            {
                "platform_id": "cls-telegraph",
                "item_id": "other-company",
                "title": "其他公司发布新产品",
                "published_at": (NOW - timedelta(minutes=5)).isoformat(),
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW,
    )
    macro = macro_adapter.normalize(
        _request("macro.flashes"),
        [
            {
                "platform_id": "jin10",
                "item_id": "unrelated-sports",
                "title": "某球队公布新赛季名单",
                "published_at": (NOW - timedelta(minutes=5)).isoformat(),
            }
        ],
        execution_started_at=NOW,
        execution_completed_at=NOW,
    )

    assert company.records == []
    assert macro.records == []


def test_adapter_rejects_a_cross_platform_row() -> None:
    adapter = FreeLiveAdapter(QINGHONG_SOURCE_IDS["macro.flashes"])

    with pytest.raises(ApplicationError) as exc_info:
        adapter.normalize(
            _request("macro.flashes"),
            [
                {
                    "platform_id": "cls-telegraph",
                    "item_id": "wrong-platform",
                    "title": "央行公布利率决定",
                    "published_at": (NOW - timedelta(minutes=5)).isoformat(),
                }
            ],
            execution_started_at=NOW,
            execution_completed_at=NOW,
        )

    assert exc_info.value.safe_message == "R1_SOURCE_REQUEST_MISMATCH"
