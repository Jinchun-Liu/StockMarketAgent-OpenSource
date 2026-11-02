from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.discovery import SourceDiscoveryQuery, SourceDiscoveryStatus

NOW = datetime(2026, 6, 1, tzinfo=UTC)
DOMAINS = {"www.cninfo.com.cn", "static.cninfo.com.cn"}


def query() -> SourceDiscoveryQuery:
    return SourceDiscoveryQuery(
        schema_version="1.0",
        context=OperationContext(schema_version="1.0", as_of=NOW, requested_at=NOW),
        query="离线公司 官方年报",
        missing_fact_keys=["business.core_products"],
        include_domains=sorted(DOMAINS),
        end_date=NOW,
        max_results=5,
    )


async def public_dns(_host: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


def announcement(**changes: object) -> dict[str, object]:
    return {
        "secCode": "603986",
        "secName": "离线公司",
        "announcementTitle": "2025年年度报告",
        "adjunctUrl": "finalpage/2026-03-28/1234567890.PDF",
        "announcementTime": int(datetime(2026, 3, 28, tzinfo=UTC).timestamp() * 1000),
        **changes,
    }


@pytest.mark.parametrize(
    "invalid",
    [
        {"secCode": "000001"},
        {"secName": "其他公司"},
        {"adjunctUrl": "https://evil.example/report.pdf"},
        {"adjunctUrl": "finalpage/2026-03-28/../private.PDF"},
        {"announcementTime": int((NOW + timedelta(days=1)).timestamp() * 1000)},
        {"announcementTime": "yesterday"},
    ],
)
async def test_official_index_binds_company_and_filters_untrusted_rows(
    invalid: dict[str, object],
) -> None:
    from stock_assistant.platform.discovery.cninfo import CninfoSourceDiscovery

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.scheme == "https" and request.url.host == "www.cninfo.com.cn"
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"stockList": [{"code": "603986", "orgId": "test-org", "zwjc": "离线公司"}]},
            )
        form = parse_qs(request.content.decode())
        assert form["stock"] == ["603986,test-org"]
        assert form["pageSize"] == ["5"] and form["pageNum"] == ["1"]
        return httpx.Response(
            200, json={"announcements": [announcement(**invalid), announcement()]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CninfoSourceDiscovery(
            instrument_id="XSHG:603986",
            instrument_name="离线公司",
            client=client,
            allowed_domains=DOMAINS,
            resolver=public_dns,
            clock=lambda: NOW,
        )
        result = await provider.discover(query())
    assert result.status is SourceDiscoveryStatus.SUCCEEDED
    assert len(result.candidates) == 1 and result.credits_used == 0
    assert (
        str(result.candidates[0].url)
        == "https://static.cninfo.com.cn/finalpage/2026-03-28/1234567890.PDF"
    )
    assert len(calls) == 4
    binding = result.candidates[0].official_index
    assert binding is not None
    assert binding.identity.instrument_id == "XSHG:603986"
    assert binding.identity.canonical_url == str(result.candidates[0].url)
    assert binding.publication.kind == "official_index"
    assert binding.publication.raw_value == str(announcement()["announcementTime"])
    assert binding.publication.location.field == "announcementTime"
    assert binding.publication.published_at == result.candidates[0].published_at


@pytest.mark.parametrize(
    "failure", ["robots", "post_timeout", "index_identity", "disabled", "future_cutoff"]
)
async def test_official_index_fails_closed_without_repeating_post(failure: str) -> None:
    from stock_assistant.platform.discovery.cninfo import CninfoSourceDiscovery

    posts = 0
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts, calls
        calls += 1
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /"
                if failure == "robots"
                else "User-agent: *\nAllow: /",
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "stockList": [
                        {
                            "code": "603986",
                            "orgId": "test-org",
                            "zwjc": "其他公司" if failure == "index_identity" else "离线公司",
                        }
                    ]
                },
            )
        posts += 1
        if failure == "post_timeout":
            raise httpx.ReadTimeout("private-marker", request=request)
        return httpx.Response(200, json={"announcements": [announcement()]})

    q = query()
    if failure == "disabled":
        q = q.model_copy(update={"include_domains": ["static.cninfo.com.cn"]})
    if failure == "future_cutoff":
        q = q.model_copy(update={"end_date": NOW + timedelta(days=1)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CninfoSourceDiscovery(
            instrument_id="XSHG:603986",
            instrument_name="离线公司",
            client=client,
            allowed_domains=DOMAINS,
            resolver=public_dns,
            clock=lambda: NOW,
        )
        result = await provider.discover(q)
    assert not result.candidates and result.status is not SourceDiscoveryStatus.SUCCEEDED
    assert "private-marker" not in result.model_dump_json()
    assert posts == (1 if failure == "post_timeout" else 0)
    if failure in {"disabled", "future_cutoff"}:
        assert calls == 0
