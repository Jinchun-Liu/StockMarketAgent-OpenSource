from __future__ import annotations

import asyncio
import gzip
import logging
import re
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import FactEligibility, FetchRequest
from stock_assistant.platform.network import connect_proxy
from stock_assistant.platform.sources.governed_web import (
    GOVERNED_WEB_CAPABILITY,
    GOVERNED_WEB_SOURCE_ID,
    GovernedWebSourceConnector,
    GovernedWebSourceError,
    _pdf_text,
)

AS_OF = datetime(2026, 8, 27, tzinfo=UTC)


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("robots_status", [200, 404, 410])
async def test_compressed_robots_and_document_are_decoded_once(
    encoding: str, robots_status: int
) -> None:
    document = "Offline company disclosure context. " * 20
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        robots = request.url.path == "/robots.txt"
        body = ("User-agent: *\nAllow: /" if robots else document).encode()
        compressed = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
        return httpx.Response(
            robots_status if robots else 200,
            headers={
                "content-type": "text/plain; charset=utf-8",
                "content-encoding": encoding,
                "content-length": str(len(compressed)),
                "last-modified": "Tue, 25 Aug 2026 00:00:00 GMT",
            },
            stream=httpx.ByteStream(compressed),
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
            clock=lambda: AS_OF,
        )
        result = await connector.fetch(_request())
    assert paths == ["/robots.txt", "/report/1"]
    assert result.records[0].payload["text"] == document


@pytest.mark.parametrize("phase", ["robots", "document"])
async def test_malformed_compression_is_a_safe_source_error(
    phase: str, caplog: pytest.LogCaptureFixture
) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if phase == "document" and request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(b"invalid compressed private-marker"),
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.sources.governed_web"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=resolver,
            )
            with pytest.raises(GovernedWebSourceError) as caught:
                await connector.fetch(_request())
    assert caught.value.code == (
        "WEB_ROBOTS_UNAVAILABLE" if phase == "robots" else "WEB_CONTENT_DECODE_FAILED"
    )
    assert not caught.value.retryable
    assert paths == (["/robots.txt"] if phase == "robots" else ["/robots.txt", "/report/1"])
    assert "failure_kind=DecodingError" in caplog.text
    assert "private-marker" not in caplog.text


@pytest.mark.parametrize("policy", ["denied", "oversized"])
async def test_compression_keeps_robots_and_decoded_size_limits(policy: str) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        body = b"User-agent: *\nDisallow: /" if policy == "denied" else b"#" * (512 * 1024 + 1)
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(body)),
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
        )
        reason = "WEB_ROBOTS_BLOCKED" if policy == "denied" else "WEB_CONTENT_TOO_LARGE"
        with pytest.raises(GovernedWebSourceError, match=reason):
            await connector.fetch(_request())
    assert paths == ["/robots.txt"]


@pytest.mark.parametrize("failure_kind", ["policy", "connection", "timeout"])
async def test_real_httpx_connect_trace_preserves_proxy_failure_semantics(
    failure_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def proxy_resolver(_host: str) -> tuple[str, ...]:
        if failure_kind == "policy":
            raise connect_proxy.ConnectProxyError("EGRESS_PROXY_ADDRESS_BLOCKED")
        if failure_kind == "connection":
            raise OSError("test-only connection failure")
        raise TimeoutError("test-only timeout")

    async def source_resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    monkeypatch.setattr(connect_proxy, "_resolve", proxy_resolver)
    server = await asyncio.start_server(
        lambda reader, writer: connect_proxy.handle_connect(
            reader, writer, allowed_hosts=("www.cninfo.com.cn",)
        ),
        "127.0.0.1",
        0,
    )
    async with server:
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{port}", trust_env=False) as client:
            connector = GovernedWebSourceConnector(
                client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=source_resolver
            )
            with pytest.raises(GovernedWebSourceError) as caught:
                async with asyncio.timeout(2):
                    await connector.fetch(_request())
    assert caught.value.retryable is (failure_kind != "policy")
    assert caught.value.code == (
        "WEB_FETCH_REJECTED" if failure_kind == "policy" else "WEB_FETCH_TEMPORARILY_UNAVAILABLE"
    )


@pytest.mark.parametrize("phase", ["robots", "document"])
@pytest.mark.parametrize(
    ("proxy_status", "connect_observed"),
    [(403, True), (407, True), (429, True), (502, True), (504, True), (None, True), (504, False)],
)
async def test_proxy_failure_uses_observed_status_and_never_error_text(
    phase: str, proxy_status: int | None, connect_observed: bool, caplog: pytest.LogCaptureFixture
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if phase == "document" and request.url.path == "/robots.txt":
            return httpx.Response(404)
        trace = cast(
            Callable[[str, dict[str, object]], Awaitable[None]], request.extensions["trace"]
        )
        if connect_observed:
            await trace(
                "http11.send_request_headers.started",
                {"request": SimpleNamespace(method=b"CONNECT")},
            )
        if proxy_status is not None:
            await trace(
                "http11.receive_response_headers.complete",
                {"return_value": (b"HTTP/1.1", proxy_status, b"private-marker", [])},
            )
        raise httpx.ProxyError("504 private-marker", request=request)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.sources.governed_web"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = GovernedWebSourceConnector(
                client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=resolver
            )
            with pytest.raises(GovernedWebSourceError) as caught:
                await connector.fetch(_request())
    retryable = connect_observed and proxy_status in {502, 504}
    assert caught.value.retryable is retryable
    assert caught.value.code == (
        "WEB_FETCH_TEMPORARILY_UNAVAILABLE" if retryable else "WEB_FETCH_REJECTED"
    )
    assert "private-marker" not in caplog.text


@pytest.mark.parametrize("phase", ["robots", "document"])
async def test_fetch_trace_identifies_failure_stage_without_logging_raw_content(
    phase: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if phase == "document" and request.url.path == "/robots.txt":
            return httpx.Response(404)
        trace = cast(
            Callable[[str, dict[str, object]], Awaitable[None]], request.extensions["trace"]
        )
        await trace("connection.start_tls.started", {"private_marker": "must-not-be-logged"})
        raise httpx.ConnectTimeout("must-not-be-logged", request=request)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.sources.governed_web"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=resolver,
            )
            with pytest.raises(GovernedWebSourceError) as caught:
                await connector.fetch(_request())
    assert caught.value.code == "WEB_FETCH_TEMPORARILY_UNAVAILABLE"
    assert caught.value.retryable
    records = [r.getMessage() for r in caplog.records if r.name.endswith("governed_web")]
    assert f"phase={phase} transport_phase=start_tls" in records[-1]
    assert "failure_kind=ConnectTimeout" in records[-1]
    assert "tls_state=started" in records[-1]
    assert "hostname=www.cninfo.com.cn" in records[-1]
    assert "correlation_id=" in records[-1]
    assert "must-not-be-logged" not in " ".join(records)
    assert "/report/1" not in " ".join(records)


@pytest.mark.parametrize("tls_complete", [False, True])
async def test_response_timeout_logs_whether_tls_was_established(
    tls_complete: bool, caplog: pytest.LogCaptureFixture
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        trace = cast(
            Callable[[str, dict[str, object]], Awaitable[None]], request.extensions["trace"]
        )
        if tls_complete:
            await trace("connection.start_tls.complete", {})
        await trace("http11.receive_response_headers.started", {})
        raise httpx.ReadTimeout("private-marker", request=request)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.sources.governed_web"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            connector = GovernedWebSourceConnector(
                client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=resolver
            )
            with pytest.raises(GovernedWebSourceError, match="WEB_FETCH_TEMPORARILY_UNAVAILABLE"):
                await connector.fetch(_request())
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "transport_phase=receive_response_headers" in messages
    assert f"tls_state={'completed' if tls_complete else 'not_observed'}" in messages
    assert "failure_kind=ReadTimeout" in messages
    assert "private-marker" not in messages


@pytest.mark.parametrize("status", [404, 410, 401, 403, 429, 500, 503, 302])
async def test_missing_robots_is_distinct_from_access_denial_and_unreachable(status: int) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text="not a robots policy")
        return httpx.Response(
            200,
            text="Company disclosure context. " * 20,
            headers={
                "content-type": "text/plain",
                "last-modified": "Tue, 25 Aug 2026 00:00:00 GMT",
            },
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
            clock=lambda: AS_OF,
        )
        if status in {404, 410}:
            result = await connector.fetch(_request())
            assert len(result.records) == 1
            assert requested == ["/robots.txt", "/report/1"]
        else:
            with pytest.raises(GovernedWebSourceError, match="WEB_ROBOTS_UNAVAILABLE"):
                await connector.fetch(_request())
            assert requested == ["/robots.txt"]


@pytest.mark.parametrize(
    ("markup", "error", "basis"),
    [
        (
            '<meta property="article:published_time" content="2026-08-25T10:30:00+08:00">',
            None,
            "html_meta",
        ),
        (
            '<time itemprop="datePublished" datetime="2026-08-25T10:30:00+08:00"></time>',
            None,
            "html_time",
        ),
        (
            '<script type="application/ld+json">'
            '{"datePublished":"2026-08-25T10:30:00+08:00"}</script>',
            None,
            "jsonld",
        ),
        ("发布日期: 2026年08月25日", None, "publication_label"),
        ("2026 年年度报告 Copyright 2026", "WEB_PUBLICATION_TIME_UNKNOWN", ""),
        ("发布日期: 2026-08-28", "WEB_PUBLICATION_TIME_INVALID", ""),
        ("发布日期: 2026-08-25 公告日期: 2026-08-26", "WEB_PUBLICATION_TIME_CONFLICT", ""),
    ],
)
async def test_current_missing_search_date_is_resolved_only_from_source(
    markup: str,
    error: str | None,
    basis: str,
) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200,
            text=markup + "<p>Company disclosure context.</p>" * 20,
            headers={"content-type": "text/html", "last-modified": "Tue, 25 Aug 2026 00:00:00 GMT"},
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
            clock=lambda: AS_OF,
        )
        request = _request()
        request.options["published_at"] = None
        request.options["current_run_observation"] = True
        if error:
            with pytest.raises(GovernedWebSourceError, match=error):
                await connector.fetch(request)
        else:
            result = await connector.fetch(request)
            record = result.records[0]
            assert record.published_at is not None and record.published_at < AS_OF
            assert basis in str(record.payload["publication_basis"])
            assert record.published_at != record.first_seen_at
        assert requested == ["/robots.txt", "/report/1"]


async def test_historical_missing_search_date_does_not_fetch() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Historical undated sources must not initiate live fetching")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(client=client, allowed_domains={"www.cninfo.com.cn"})
        request = _request()
        request.options["published_at"] = None
        with pytest.raises(GovernedWebSourceError, match="WEB_PUBLICATION_TIME_UNKNOWN"):
            await connector.fetch(request)


def _text_pdf(*, blank: bool = False, table: bool = False) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    if not blank:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 40 700 Td (2025 annual report. Company optical lens business. "
            b"Revenue unit CNY. Audited consolidated financial statements "
            b"and reporting period.) Tj ET"
        )
        if table:
            # Wide glyphs affect layout's fixed-width estimate; the actual
            # numeric columns still have a visible gap in PDF coordinates.
            stream.set_data(
                b"BT /F1 12 Tf 40 740 Td (" + b"W" * 40 + b") Tj ET "
                b"BT /F1 12 Tf 40 720 Td (" + b"W" * 40 + b") Tj ET "
                b"BT /F1 12 Tf 50 680 Td (2025) Tj ET "
                b"BT /F1 12 Tf 125 680 Td (2024) Tj ET "
                b"BT /F1 12 Tf 50 650 Td (123,456.78) Tj ET "
                b"BT /F1 12 Tf 125 650 Td (234,567.89) Tj ET"
            )
        page[NameObject("/Contents")] = stream
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def test_pdf_text_keeps_page_and_table_context() -> None:
    result = await _pdf_text(_text_pdf())
    assert "[PDF page 1]" in result
    assert "2025 annual report" in result
    assert "Revenue unit CNY" in result


@pytest.mark.parametrize("content", [b"not a PDF", _text_pdf(blank=True)])
async def test_pdf_without_reliable_text_is_a_gap(content: bytes) -> None:
    with pytest.raises(GovernedWebSourceError, match="WEB_PDF_TEXT_UNAVAILABLE"):
        await _pdf_text(content)


@pytest.mark.parametrize("current_run", [False, True])
async def test_current_version_observation_never_invents_historical_last_modified(
    current_run: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200, text="Public company disclosure. " * 10, headers={"content-type": "text/plain"}
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
            clock=lambda: AS_OF,
        )
        request = _request()
        request.options["current_run_observation"] = current_run
        if current_run:
            result = await connector.fetch(request)
            record = result.records[0]
            assert record.payload["revision_basis"] == "current_observation"
            assert record.payload["last_modified_at"] == record.first_seen_at.isoformat()
            assert record.published_at != record.first_seen_at
        else:
            with pytest.raises(GovernedWebSourceError, match="WEB_HISTORICAL_VERSION_UNKNOWN"):
                await connector.fetch(request)


def _request(url: str = "https://www.cninfo.com.cn/report/1") -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=OperationContext(schema_version="1.0", as_of=AS_OF, requested_at=AS_OF),
        source_id=GOVERNED_WEB_SOURCE_ID,
        capability=GOVERNED_WEB_CAPABILITY,
        rights_snapshot_hash="a" * 64,
        until=AS_OF,
        limit=1,
        options={
            "url": url,
            "registered_source_id": "cninfo_public",
            "source_level": "regulatory_official",
            "source_group": "cninfo_official",
            "published_at": "2026-08-26T00:00:00+00:00",
            "research_use": "excerpt_bound_fact",
        },
        options_namespace="research.web_fetch",
        options_owner="research",
    )


@pytest.mark.asyncio
async def test_private_dns_address_is_blocked_before_any_network_request() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nAllow: /")

    async def private_resolver(_hostname: str) -> tuple[str, ...]:
        return ("127.0.0.1",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=private_resolver,
        )
        with pytest.raises(GovernedWebSourceError, match="WEB_PRIVATE_ADDRESS_BLOCKED"):
            await connector.fetch(_request())

    assert requested == []


@pytest.mark.asyncio
async def test_redirect_is_blocked_after_robots_permission() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(302, headers={"location": "https://example.com/other"})

    async def public_resolver(_hostname: str) -> tuple[str, ...]:
        return ("1.1.1.1",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=public_resolver,
        )
        with pytest.raises(GovernedWebSourceError, match="WEB_REDIRECT_BLOCKED"):
            await connector.fetch(_request())


@pytest.mark.asyncio
async def test_unregistered_research_fact_use_is_rejected_before_network() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="unexpected")

    async def public_resolver(_hostname: str) -> tuple[str, ...]:
        return ("1.1.1.1",)

    invalid = _request().model_copy(
        update={"options": {**_request().options, "research_use": "free_form_model_fact"}}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=public_resolver,
        )
        with pytest.raises(GovernedWebSourceError, match="WEB_RESEARCH_USE_NOT_ELIGIBLE"):
            await connector.fetch(invalid)

    assert requested == []


@pytest.mark.parametrize("phase", ["robots", "document"])
@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
async def test_single_transient_fetch_interruption_recovers_without_skipping_robots(
    phase: str, failure: type[httpx.RequestError]
) -> None:
    paths: list[str] = []
    interrupted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal interrupted
        paths.append(request.url.path)
        robots = request.url.path == "/robots.txt"
        if (robots == (phase == "robots")) and not interrupted:
            interrupted = True
            raise failure("offline transient", request=request)
        return httpx.Response(
            404 if robots else 200,
            text="Offline company disclosure. " * 20,
            headers={
                "content-type": "text/plain",
                "last-modified": "Tue, 25 Aug 2026 00:00:00 GMT",
            },
        )

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
            clock=lambda: AS_OF,
        )
        result = await connector.fetch(_request())
    assert paths == (
        ["/robots.txt", "/robots.txt", "/report/1"]
        if phase == "robots"
        else ["/robots.txt", "/report/1", "/report/1"]
    )
    assert len(result.records) == 1
    assert result.records[0].fact_eligibility is FactEligibility.INELIGIBLE


@pytest.mark.parametrize("phase", ["robots", "document"])
async def test_continuous_transient_fetch_is_bounded_and_returns_no_batch(phase: str) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if phase == "document" and request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("offline persistent", request=request)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
        )
        with pytest.raises(GovernedWebSourceError) as caught:
            await connector.fetch(_request())
    assert caught.value.code == "WEB_FETCH_TEMPORARILY_UNAVAILABLE"
    assert caught.value.retryable
    assert paths == (
        ["/robots.txt"] * 2 if phase == "robots" else ["/robots.txt", "/report/1", "/report/1"]
    )


@pytest.mark.parametrize("interruption", ["deadline", "cancel"])
async def test_transient_backoff_remains_cancellable(interruption: str) -> None:
    calls = 0
    first_failure = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        first_failure.set()
        raise httpx.ReadTimeout("offline timeout", request=request)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
        )
        if interruption == "deadline":
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await connector.fetch(_request())
        else:
            task = asyncio.create_task(connector.fetch(_request()))
            await first_failure.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert calls == 1


@pytest.mark.parametrize("status", [200, 403, 429, 503])
async def test_partial_body_retry_discards_prefix_and_does_not_retry_denial(status: int) -> None:
    calls = 0
    complete = b"Offline complete disclosure. " * 3000

    class InterruptedBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"discarded partial source " * 3000
            raise httpx.ReadTimeout("offline body interruption")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls += 1
        headers = {"content-type": "text/plain", "last-modified": "Tue, 25 Aug 2026 00:00:00 GMT"}
        if calls == 1:
            return httpx.Response(status, headers=headers, stream=InterruptedBody())
        return httpx.Response(200, headers=headers, content=complete)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,
            clock=lambda: AS_OF,
        )
        if status == 200:
            result = await connector.fetch(_request())
            assert result.records[0].payload["text"] == complete.decode()
            assert calls == 2
        else:
            with pytest.raises(GovernedWebSourceError):
                await connector.fetch(_request())
            assert calls == 1


async def test_pdf_layout_keeps_adjacent_financial_columns_separate() -> None:
    result = await _pdf_text(_text_pdf(table=True))
    assert "[PDF page 1]" in result
    assert re.search(r"2025[ \t]+2024", result)
    assert re.search(r"123,456\.78[ \t]+234,567\.89", result)
    assert "123,456.78234,567.89" not in result
