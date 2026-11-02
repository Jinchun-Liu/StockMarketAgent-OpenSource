from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

import stock_assistant.platform.discovery.tavily as tavily_module
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.discovery import (
    ResearchWebQueryPlanItem,
    ResearchWebRetrievalReceipt,
    ResearchWebRetrievalReceiptStatus,
    SourceDiscoveryCandidate,
    SourceDiscoveryQuery,
    SourceDiscoveryResult,
    SourceDiscoveryStatus,
)
from stock_assistant.platform.discovery.tavily import TavilyDiscoveryError, TavilySourceDiscovery

AS_OF = datetime(2026, 8, 27, tzinfo=UTC)


def _query() -> SourceDiscoveryQuery:
    return SourceDiscoveryQuery(
        schema_version="1.0",
        context=OperationContext(schema_version="1.0", as_of=AS_OF, requested_at=AS_OF),
        query="兆易创新 年度报告 收入结构",
        missing_fact_keys=["business.revenue_mix"],
        include_domains=["www.cninfo.com.cn"],
        end_date=AS_OF,
        max_results=5,
    )


@pytest.mark.asyncio
async def test_tavily_is_url_discovery_only_and_rejects_future_or_unlisted_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["search_depth"] == "advanced"
        assert payload["include_answer"] is False
        assert payload["include_raw_content"] is False
        assert payload["include_images"] is False
        assert payload["max_results"] == 5
        return httpx.Response(
            200,
            json={
                "answer": "must be ignored",
                "results": [
                    {
                        "url": "https://www.cninfo.com.cn/report/1",
                        "title": "可见公告",
                        "score": 0.9,
                        "published_date": "2026-08-26T00:00:00Z",
                        "raw_content": "must be ignored",
                    },
                    {
                        "url": "https://www.cninfo.com.cn/report/future",
                        "title": "未来公告",
                        "score": 0.8,
                        "published_date": "2026-08-28T00:00:00Z",
                    },
                    {
                        "url": "https://evil.example/report",
                        "title": "未登记域名",
                        "score": 1,
                        "published_date": "2026-08-26T00:00:00Z",
                    },
                ],
            },
        )

    query = _query()
    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.discovery.tavily"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await TavilySourceDiscovery(
                api_key=SecretStr("test-key"), client=client
            ).discover(query)

    assert result.status is SourceDiscoveryStatus.SUCCEEDED
    assert len(result.candidates) == 1
    assert result.diagnostics.raw_count == 3
    assert result.diagnostics.domain_filtered == 1
    assert result.diagnostics.date_filtered == 1
    assert result.diagnostics.selected_count == 1
    assert result.candidates[0].domain == "www.cninfo.com.cn"
    assert "answer" not in result.model_dump(mode="json")
    assert "raw_content" not in result.model_dump(mode="json")
    record = next(
        item for item in caplog.records if item.__dict__.get("event") == "research_web_discovery"
    )
    assert record.__dict__["provider"] == "tavily"
    assert record.__dict__["status"] == "succeeded"
    assert record.__dict__["candidate_count"] == 1
    assert record.__dict__["credits_used"] == 2
    assert record.__dict__["correlation_id"] == str(query.context.correlation_id)
    rendered = record.getMessage()
    assert "task_type=research_web_gap_discovery" in rendered
    assert f"correlation_id={query.context.correlation_id}" in rendered
    assert "status=succeeded" in rendered
    assert "candidate_count=1" in rendered
    assert "credits_used=2" in rendered
    assert "test-key" not in record.getMessage()
    assert "兆易创新" not in record.getMessage()
    assert "www.cninfo.com.cn" not in record.getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (401, "TAVILY_AUTHENTICATION_FAILED", False),
        (403, "TAVILY_AUTHENTICATION_FAILED", False),
        (429, "TAVILY_QUOTA_EXHAUSTED", True),
        (432, "TAVILY_QUOTA_EXHAUSTED", True),
        (422, "TAVILY_REQUEST_REJECTED", False),
        (503, "TAVILY_TEMPORARILY_UNAVAILABLE", True),
    ],
)
async def test_tavily_classifies_http_failures_without_response_text(
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    upstream_marker = "upstream-response-marker"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=upstream_marker)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TavilyDiscoveryError, match=code) as exc_info:
            await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
                _query()
            )

    assert exc_info.value.code == code
    assert exc_info.value.retryable is retryable
    assert upstream_marker not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"unexpected": []},
        {"results": ["not-an-object"]},
        {"results": [{"url": "not-a-url", "title": "bad", "score": 0.5}]},
        {
            "results": [
                {
                    "url": "https://www.cninfo.com.cn/report/1",
                    "title": "bad score",
                    "score": "not-a-decimal",
                }
            ]
        },
    ],
)
async def test_tavily_converts_candidate_validation_failures_to_stable_code(
    document: object,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=document))
    ) as client:
        with pytest.raises(TavilyDiscoveryError, match="TAVILY_RESPONSE_INVALID") as exc_info:
            await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
                _query()
            )

    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_tavily_distinguishes_egress_proxy_rejection() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("proxy-detail-marker")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            TavilyDiscoveryError,
            match="TAVILY_EGRESS_PROXY_REJECTED",
        ) as exc_info:
            await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
                _query()
            )

    assert exc_info.value.retryable is False
    assert "proxy-detail-marker" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_classifies_transport_timeout() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout-detail-marker")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            TavilyDiscoveryError,
            match="TAVILY_TEMPORARILY_UNAVAILABLE",
        ) as exc_info:
            await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
                _query()
            )

    assert exc_info.value.retryable is True
    assert "timeout-detail-marker" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        (b"{invalid-json", "TAVILY_RESPONSE_INVALID"),
        (b"x" * (1024 * 1024 + 1), "TAVILY_RESPONSE_TOO_LARGE"),
    ],
)
async def test_tavily_rejects_invalid_or_oversized_response_bodies(
    body: bytes,
    expected_code: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(TavilyDiscoveryError, match=expected_code):
            await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
                _query()
            )


@pytest.mark.asyncio
async def test_tavily_reports_no_results_and_advanced_search_credit_cost() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"results": []}))
    ) as client:
        result = await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
            _query()
        )

    assert result.status is SourceDiscoveryStatus.NO_RESULTS
    assert result.candidates == []
    assert result.credits_used == 2
    assert result.diagnostics.raw_count == 0
    assert result.diagnostics.selected_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://www.cninfo.com.cn/report/1", "https://www.cninfo.com.cn/report/1"),
        (
            "http://WWW.CNINFO.COM.CN:80/a%20b?q=1#page=2",
            "https://www.cninfo.com.cn/a%20b?q=1#page=2",
        ),
        ("https://www.cninfo.com.cn/report/1", "https://www.cninfo.com.cn/report/1"),
        ("http://www.cninfo.com.cn:8080/report/1", None),
        ("http://user@www.cninfo.com.cn/report/1", None),
        ("http://user:password@www.cninfo.com.cn/report/1", None),
        ("http://unregistered.example/report/1", None),
        ("http://www.cninfo.com.cn.unregistered.example/report/1", None),
    ],
)
async def test_discovery_http_hints_become_https_candidates_without_plaintext_io(
    url: str,
    expected: str | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"results": [{"url": url, "title": "披露提示", "score": 1}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
            _query()
        )
    assert len(requests) == 1
    assert requests[0].method == "POST" and str(requests[0].url) == tavily_module.TAVILY_SEARCH_URL
    assert [str(item.url) for item in result.candidates] == ([] if expected is None else [expected])
    assert all(item.url.scheme == "https" for item in result.candidates)


@pytest.mark.asyncio
async def test_https_candidate_from_http_hint_still_rejects_future_publication() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "http://www.cninfo.com.cn/report/future",
                            "title": "未来披露",
                            "score": 1,
                            "published_date": "2026-08-28T00:00:00Z",
                        }
                    ]
                },
            )
        )
    ) as client:
        result = await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
            _query()
        )
    assert result.candidates == []
    assert result.diagnostics.date_filtered == 1


@pytest.mark.asyncio
async def test_tavily_distinguishes_filtered_empty_from_upstream_empty() -> None:
    rows = [
        {"url": "http://www.cninfo.com.cn:8080/report", "title": "unsafe port", "score": 1},
        {"url": "https://unregistered.example/report", "title": "unlisted", "score": 1},
        {
            "url": "https://www.cninfo.com.cn/report",
            "title": "future",
            "score": 1,
            "published_date": "2026-08-28T00:00:00Z",
        },
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"results": rows}))
    ) as client:
        result = await TavilySourceDiscovery(api_key=SecretStr("test-key"), client=client).discover(
            _query()
        )
    assert result.status is SourceDiscoveryStatus.NO_RESULTS
    assert result.diagnostics.raw_count == 3
    assert result.diagnostics.protocol_filtered == 1
    assert result.diagnostics.domain_filtered == 1
    assert result.diagnostics.date_filtered == 1
    assert result.diagnostics.selected_count == 0


@pytest.mark.asyncio
async def test_tavily_rejects_empty_api_key_before_network() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(TavilyDiscoveryError, match="TAVILY_API_KEY_MISSING"):
            TavilySourceDiscovery(
                api_key=SecretStr("  "),
                client=client,
            )


@pytest.mark.asyncio
async def test_tavily_rotates_ordered_deduplicated_pool_only_for_quota_or_auth(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempted_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        provided = request.headers["Authorization"].removeprefix("Bearer ")
        attempted_tokens.append(provided)
        if provided == "fixture-slot-a":
            return httpx.Response(429)
        if provided == "fixture-slot-b":
            return httpx.Response(401)
        return httpx.Response(200, json={"results": []})

    pool = SecretStr(
        json.dumps(
            {
                "api_keys": [
                    "fixture-slot-a",
                    "fixture-slot-a",
                    "fixture-slot-b",
                    "fixture-slot-c",
                ]
            }
        )
    )
    query = _query()
    with caplog.at_level(logging.WARNING, logger="stock_assistant.platform.discovery.tavily"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await TavilySourceDiscovery(api_key=pool, client=client).discover(query)

    assert result.status is SourceDiscoveryStatus.NO_RESULTS
    assert attempted_tokens == ["fixture-slot-a", "fixture-slot-b", "fixture-slot-c"]
    rotation_records = [
        record
        for record in caplog.records
        if record.__dict__.get("event") == "tavily_api_key_rotated"
    ]
    assert [record.__dict__["key_slot"] for record in rotation_records] == [1, 2]
    assert [record.__dict__["failure_code"] for record in rotation_records] == [
        "TAVILY_QUOTA_EXHAUSTED",
        "TAVILY_AUTHENTICATION_FAILED",
    ]
    assert all(
        record.__dict__["correlation_id"] == str(query.context.correlation_id)
        for record in rotation_records
    )
    rendered = "\n".join(record.getMessage() for record in rotation_records)
    assert "fixture-slot-a" not in rendered
    assert "fixture-slot-b" not in rendered
    assert "fixture-slot-c" not in rendered


@pytest.mark.asyncio
async def test_tavily_rotates_after_plan_usage_limit_and_returns_next_success() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(432)
        return httpx.Response(200, json={"results": []})

    pool = SecretStr(json.dumps({"api_keys": ["fixture-slot-a", "fixture-slot-b"]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TavilySourceDiscovery(api_key=pool, client=client).discover(_query())

    assert attempts == 2
    assert result.status is SourceDiscoveryStatus.NO_RESULTS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "expected_code", "retryable"),
    [
        ([401, 403], "TAVILY_AUTHENTICATION_FAILED", False),
        ([429, 429], "TAVILY_QUOTA_EXHAUSTED", True),
        ([432, 432], "TAVILY_QUOTA_EXHAUSTED", True),
        # Mixed exhaustion prefers quota because a valid slot may work after reset.
        ([401, 429, 403], "TAVILY_QUOTA_EXHAUSTED", True),
    ],
)
async def test_tavily_pool_exhaustion_has_bounded_attempts_and_stable_precedence(
    statuses: list[int],
    expected_code: str,
    retryable: bool,
) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        status = statuses[attempts]
        attempts += 1
        return httpx.Response(status)

    pool = SecretStr(
        json.dumps({"api_keys": [f"fixture-slot-{index}" for index in range(len(statuses))]})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TavilyDiscoveryError, match=expected_code) as exc_info:
            await TavilySourceDiscovery(api_key=pool, client=client).discover(_query())

    assert attempts == len(statuses)
    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (422, "TAVILY_REQUEST_REJECTED"),
        (503, "TAVILY_TEMPORARILY_UNAVAILABLE"),
        ("transport", "TAVILY_TEMPORARILY_UNAVAILABLE"),
        ("proxy", "TAVILY_EGRESS_PROXY_REJECTED"),
    ],
)
async def test_tavily_pool_does_not_rotate_for_server_or_transport_failure(
    failure: int | str,
    expected_code: str,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "transport":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if failure == "proxy":
            raise httpx.ProxyError("fixture proxy rejection", request=request)
        assert isinstance(failure, int)
        return httpx.Response(failure)

    pool = SecretStr(json.dumps({"api_keys": ["fixture-slot-a", "fixture-slot-b"]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TavilyDiscoveryError, match=expected_code):
            await TavilySourceDiscovery(api_key=pool, client=client).discover(_query())

    assert attempts == 1


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        '{"api_keys": []}',
        '{"api_keys": ["fixture-slot-a", 1]}',
        '{"api_keys": ["  "]}',
        '{"api_keys": ["fixture-slot-a"',
        "{not-json",
    ],
)
def test_tavily_rejects_invalid_structured_key_pool(value: str) -> None:
    with pytest.raises(TavilyDiscoveryError, match="TAVILY_API_KEY_INVALID"):
        TavilySourceDiscovery(api_key=SecretStr(value), client=httpx.AsyncClient())


@pytest.mark.asyncio
async def test_tavily_rejects_more_than_ten_unique_keys_before_network() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"results": []})

    value = SecretStr(json.dumps({"api_keys": [f"fixture-slot-{index}" for index in range(11)]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TavilyDiscoveryError, match="TAVILY_API_KEY_POOL_TOO_LARGE"):
            TavilySourceDiscovery(api_key=value, client=client)

    assert attempts == 0


@pytest.mark.asyncio
async def test_tavily_uses_one_absolute_deadline_across_rotated_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    second_attempt_started = asyncio.Event()
    blocked = asyncio.Event()
    real_timeout = asyncio.timeout
    timeout_calls: list[float | None] = []

    def tracked_timeout(delay: float | None) -> asyncio.Timeout:
        timeout_calls.append(delay)
        return real_timeout(delay)

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429)
        second_attempt_started.set()
        await blocked.wait()
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(tavily_module, "_REQUEST_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(tavily_module.asyncio, "timeout", tracked_timeout)
    pool = SecretStr(json.dumps({"api_keys": ["fixture-slot-a", "fixture-slot-b"]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            TavilyDiscoveryError,
            match="TAVILY_TEMPORARILY_UNAVAILABLE",
        ) as exc_info:
            await TavilySourceDiscovery(api_key=pool, client=client).discover(_query())

    assert second_attempt_started.is_set()
    assert attempts == 2
    assert timeout_calls == [0.01]
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_tavily_external_cancellation_passes_through() -> None:
    request_started = asyncio.Event()
    blocked = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        request_started.set()
        await blocked.wait()
        return httpx.Response(200, json={"results": []})

    pool = SecretStr(json.dumps({"api_keys": ["fixture-slot-a", "fixture-slot-b"]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(
            TavilySourceDiscovery(api_key=pool, client=client).discover(_query())
        )
        await request_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_discovery_query_rejects_urls_and_future_cutoffs() -> None:
    with pytest.raises(ValueError):
        ResearchWebQueryPlanItem(
            query="https://example.com/report",
            missing_fact_keys=["business.revenue_mix"],
        )
    with pytest.raises(ValueError):
        SourceDiscoveryQuery.model_validate(
            {**_query().model_dump(mode="python"), "end_date": datetime(2026, 8, 28, tzinfo=UTC)}
        )

    for update in (
        {"start_date": AS_OF, "end_date": AS_OF - timedelta(seconds=1)},
        {"include_domains": ["www.cninfo.com.cn", "www.cninfo.com.cn"]},
        {"include_domains": ["WWW.CNINFO.COM.CN"]},
    ):
        with pytest.raises(ValueError):
            SourceDiscoveryQuery.model_validate({**_query().model_dump(mode="python"), **update})


def test_discovery_results_and_receipts_fail_closed_on_inconsistent_state() -> None:
    candidate = SourceDiscoveryCandidate(
        schema_version="1.0",
        url="https://www.cninfo.com.cn/report/1",
        title="可见公告",
        domain="www.cninfo.com.cn",
        published_at=AS_OF,
        discovery_score="0.9",
    )
    result_base = {
        "schema_version": "1.0",
        "provider": "tavily",
        "request_sha256": "a" * 64,
        "searched_at": AS_OF,
    }
    with pytest.raises(ValueError, match="successful discovery requires candidates"):
        SourceDiscoveryResult(**result_base, status=SourceDiscoveryStatus.SUCCEEDED)
    with pytest.raises(ValueError, match="non-successful discovery"):
        SourceDiscoveryResult(
            **result_base,
            status=SourceDiscoveryStatus.NO_RESULTS,
            candidates=[candidate],
        )

    receipt_base = {
        "schema_version": "1.0",
        "session_id": uuid4(),
        "query_sha256": "b" * 64,
        "status": ResearchWebRetrievalReceiptStatus.REJECTED,
        "rule_version": "research-web-fallback.v1",
        "created_at": AS_OF,
        "expires_at": AS_OF + timedelta(days=7),
    }
    for update in (
        {"candidate_url_sha256": ["too-short"]},
        {"expires_at": AS_OF},
        {"selected_source_id": "cninfo", "selected_url_sha256": None},
    ):
        with pytest.raises(ValueError):
            ResearchWebRetrievalReceipt.model_validate({**receipt_base, **update})
