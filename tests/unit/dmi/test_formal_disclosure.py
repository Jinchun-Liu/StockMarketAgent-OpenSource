"""Frozen formal-disclosure regression. All material and transport are synthetic."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.formal_disclosure import OfficialDocumentIndexBinding
from stock_assistant.contracts.ingestion import FetchRequest
from stock_assistant.platform.sources.formal_document import formal_identity, formal_publication
from stock_assistant.platform.sources.governed_web import (
    GOVERNED_WEB_CAPABILITY,
    GOVERNED_WEB_SOURCE_ID,
    GovernedWebSourceConnector,
    GovernedWebSourceError,
)

FIXTURE = Path("tests/fixtures/research/formal-disclosure.v1.json")
MATERIAL = json.loads(FIXTURE.read_text())
NOW = datetime(2026, 9, 19, tzinfo=UTC)


@pytest.mark.parametrize("change", ["valid", "wrong_file", "wrong_issuer", "date_conflict"])
def test_exact_official_index_proof_is_independent_of_search_hint(change: str) -> None:
    identity = formal_identity(
        text=MATERIAL["formal_text"],
        url=MATERIAL["attachment_url"],
        instrument_id=MATERIAL["instrument_id"],
        instrument_name=MATERIAL["instrument_name"],
        index=None,
    )
    published, proofs = formal_publication(
        text=MATERIAL["formal_text"],
        url=MATERIAL["attachment_url"],
        metadata=[],
        index=None,
        receipt_id=UUID(int=1),
        cutoff=NOW,
    )
    binding = OfficialDocumentIndexBinding(
        identity=identity.model_copy(update={"binding_basis": "official_index"}),
        publication=proofs[0].model_copy(update={"kind": "official_index"}),
        index_url="https://www.cninfo.com.cn/new/hisAnnouncement/query",
        row_id="synthetic-row",
        source_id="cninfo",
    )
    if change in {"wrong_file", "wrong_issuer"}:
        binding = binding.model_copy(
            update={
                "identity": binding.identity.model_copy(
                    update={"canonical_url": "https://www.zmax-opt.com/other.pdf"}
                    if change == "wrong_file"
                    else {"issuer_name": "另一公司"}
                )
            }
        )
        with pytest.raises(GovernedWebSourceError, match="WEB_FORMAL_INDEX_MISMATCH"):
            formal_identity(
                text=MATERIAL["formal_text"],
                url=MATERIAL["attachment_url"],
                instrument_id=MATERIAL["instrument_id"],
                instrument_name=MATERIAL["instrument_name"],
                index=binding,
            )
        return
    text = MATERIAL["formal_text"].replace("发布日期", "董事会签署日期")
    if change == "date_conflict":
        text += "\n发布日期: 2026-04-21\n"
        with pytest.raises(GovernedWebSourceError, match="WEB_PUBLICATION_TIME_CONFLICT"):
            formal_publication(
                text=text,
                url=MATERIAL["attachment_url"],
                metadata=[],
                index=binding,
                receipt_id=UUID(int=2),
                cutoff=NOW,
            )
    else:
        stamp, retained = formal_publication(
            text=text,
            url=MATERIAL["attachment_url"],
            metadata=[],
            index=binding,
            receipt_id=UUID(int=2),
            cutoff=NOW,
        )
        assert stamp == published and retained == [binding.publication]


@pytest.mark.parametrize(
    ("variant", "count"),
    [
        ("undated", 1),
        ("short", 1),
        ("future_parent", 1),
        ("child", 0),
        ("capacity", 4),
        ("link_limit", 0),
        ("outside", 0),
        ("malformed", 1),
    ],
)
async def test_undated_parent_preserves_direct_attachment_without_becoming_evidence(
    variant: str, count: int
) -> None:
    material = MATERIAL
    calls: list[str] = []
    body = material["parent_html"]
    headers = {"content-type": "text/html"}
    if variant == "short":
        body = '<a href="/files/annual.pdf">报告</a>'
    elif variant == "future_parent":
        headers["last-modified"] = "Mon, 21 Sep 2026 00:00:00 GMT"
    elif variant == "capacity":
        body = "".join(f'<a href="/files/{i}.pdf">报告</a>' for i in range(5))
    elif variant == "link_limit":
        body = '<a href="/news">新闻</a>' * 12 + body
    elif variant == "outside":
        body = body.replace("/files/annual.pdf", "https://unregistered.example/annual.pdf")
    elif variant == "malformed":
        body = '<a href="https://[bad/annual.pdf">无效</a>' + body

    def response(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers=headers)

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.zmax-opt.com"},
            resolver=resolver,
            clock=lambda: NOW,
        )
        request = FetchRequest(
            schema_version="1.0",
            context=OperationContext(schema_version="1.0", as_of=NOW, requested_at=NOW),
            source_id=GOVERNED_WEB_SOURCE_ID,
            capability=GOVERNED_WEB_CAPABILITY,
            rights_snapshot_hash="a" * 64,
            limit=1,
            options_namespace="research.web_fetch",
            options_owner="research",
            options={
                "url": material["parent_url"],
                "registered_source_id": "official_zmax",
                "source_level": "company_official",
                "source_group": "zmax",
                "research_use": "excerpt_bound_fact",
                "current_run_observation": True,
                "acquisition_rule": "research-real-acquisition.v32",
                "instrument_id": material["instrument_id"],
                "instrument_name": material["instrument_name"],
                "direct_attachment": variant == "child",
            },
        )
        with pytest.raises(GovernedWebSourceError) as caught:
            await connector.fetch(request)
    outcome = getattr(caught.value, "discovery_outcome", None)
    assert outcome is not None
    assert len(outcome.attachment_candidates) == count
    if count == 1:
        assert outcome.attachment_candidates[0].canonical_url == material["attachment_url"]
        assert outcome.attachment_candidates[0].official_index is None
    if variant in {"capacity", "link_limit"}:
        assert outcome.attachment_capacity_exceeded
    assert calls == ["/robots.txt", "/investors/reports.html"]


def test_frozen_formal_identity_publication_and_financial_locations() -> None:
    text = MATERIAL["formal_text"]
    identity = formal_identity(
        text=text,
        url=MATERIAL["attachment_url"],
        instrument_id=MATERIAL["instrument_id"],
        instrument_name=MATERIAL["instrument_name"],
        index=None,
    )
    published, proofs = formal_publication(
        text=text,
        url=MATERIAL["attachment_url"],
        metadata=[],
        index=None,
        receipt_id=UUID(int=1),
        cutoff=NOW,
    )
    assert published.isoformat().replace("+00:00", "Z") == MATERIAL["gold"]["published_at"]
    assert identity.document_kind == "annual"
    assert all(
        anchor.start is not None and anchor.end is not None for anchor in identity.identity_anchors
    )
    assert proofs[0].location.page_number == 1
    assert "单位\uff1a人民币元\n项目 2025年度 2024年度\n营业收入 1000000.00 900000.00" in text


@pytest.mark.parametrize("replacement", ["董事会签署日期", "文件创建日期", "版权日期", "报告期间"])
def test_nonpublication_labels_cannot_prove_date(replacement: str) -> None:
    text = MATERIAL["formal_text"].replace("发布日期", replacement)
    with pytest.raises(GovernedWebSourceError, match="WEB_PUBLICATION_TIME_UNKNOWN"):
        formal_publication(
            text=text,
            url=MATERIAL["attachment_url"],
            metadata=[],
            index=None,
            receipt_id=UUID(int=1),
            cutoff=NOW,
        )


@pytest.mark.parametrize("change", ["wrong_code", "wrong_issuer", "news", "summary"])
def test_formal_identity_negative_cases(change: str) -> None:
    text = MATERIAL["formal_text"]
    if change == "wrong_code":
        text = text.replace("688307", "600519")
    elif change == "wrong_issuer":
        text = text.replace("中润光学", "另一公司")
    elif change == "news":
        text = text.replace("2025 年年度报告", "公司新闻")
    else:
        text = text.replace("年度报告", "年度报告摘要")
    with pytest.raises(GovernedWebSourceError):
        formal_identity(
            text=text,
            url=MATERIAL["attachment_url"],
            instrument_id=MATERIAL["instrument_id"],
            instrument_name=MATERIAL["instrument_name"],
            index=None,
        )


@pytest.mark.parametrize("date", ["2026-04-21", "2026-09-21"])
def test_conflicting_publication_is_rejected(date: str) -> None:
    with pytest.raises(GovernedWebSourceError, match="WEB_PUBLICATION_TIME_CONFLICT"):
        formal_publication(
            text=MATERIAL["formal_text"],
            url=MATERIAL["attachment_url"],
            metadata=[(date, "jsonld:datePublished")],
            index=None,
            receipt_id=UUID(int=1),
            cutoff=NOW,
        )


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("valid", None),
        ("missing_date", "WEB_PUBLICATION_TIME_UNKNOWN"),
        ("future", "WEB_PUBLICATION_TIME_INVALID"),
        ("wrong_issuer", "WEB_FORMAL_IDENTITY_UNVERIFIED"),
        ("news_meta", "WEB_FORMAL_DOCUMENT_UNVERIFIED"),
        ("html_formal", None),
        ("date_conflict", "WEB_PUBLICATION_TIME_CONFLICT"),
        ("robots403", "WEB_ROBOTS_UNAVAILABLE"),
        ("broken_pdf", "WEB_PDF_TEXT_UNAVAILABLE"),
    ],
)
async def test_formal_transport_fixed_positive_negative(change: str, expected: str | None) -> None:
    body = MATERIAL["formal_text"]
    if change == "missing_date":
        body = body.replace("发布日期", "董事会签署日期")
    elif change == "future":
        body = body.replace("2026年04月20日", "2026年09月21日")
    elif change == "wrong_issuer":
        body = body.replace("688307", "600519")
    media_type = "text/plain"
    if change == "broken_pdf":
        media_type = "application/pdf"
    if change in {"news_meta", "html_formal", "date_conflict"}:
        media_type = "text/html"
        date = "2026-04-21" if change == "date_conflict" else "2026-04-20"
        body = f'<meta name="pubdate" content="{date}">{body}'
        if change != "news_meta":
            body = f"<article><h1>2025 年年度报告</h1>{body}</article>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(403 if change == "robots403" else 404)
        return httpx.Response(200, text=body, headers={"content-type": media_type})

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.zmax-opt.com"},
            resolver=resolver,
            clock=lambda: NOW,
        )
        request = FetchRequest(
            schema_version="1.0",
            context=OperationContext(schema_version="1.0", as_of=NOW, requested_at=NOW),
            source_id=GOVERNED_WEB_SOURCE_ID,
            capability=GOVERNED_WEB_CAPABILITY,
            rights_snapshot_hash="a" * 64,
            limit=1,
            options_namespace="research.web_fetch",
            options_owner="research",
            options={
                "url": MATERIAL["attachment_url"],
                "registered_source_id": "zmax_official",
                "source_level": "company_official",
                "source_group": "zmax",
                "research_use": "excerpt_bound_fact",
                "current_run_observation": True,
                "acquisition_rule": "research-real-acquisition.v32",
                "instrument_id": MATERIAL["instrument_id"],
                "instrument_name": MATERIAL["instrument_name"],
                "published_at": "2026-04-20T00:00:00Z",
            },
        )
        if expected:
            with pytest.raises(GovernedWebSourceError, match=expected) as caught:
                await connector.fetch(request)
            if change != "robots403":
                assert caught.value.discovery_outcome is not None
                assert caught.value.discovery_outcome.reason == expected
                if change == "broken_pdf":
                    assert caught.value.discovery_outcome.stage == "extraction"
                    assert not caught.value.discovery_outcome.formal_metadata_checked
        else:
            batch = await connector.fetch(request)
            assert batch.schema_version == "1.1"
            assert batch.records[0].formal_document is not None
            assert (
                batch.discovery_outcome is not None and batch.discovery_outcome.stage == "verified"
            )


def test_publication_preserves_explicit_timezone_and_rejects_embedded_reference() -> None:
    stamp, _ = formal_publication(
        text="Publication Date: 2026-04-20T08:30:00Z",
        url=MATERIAL["attachment_url"],
        metadata=[],
        index=None,
        receipt_id=UUID(int=1),
        cutoff=NOW,
    )
    assert stamp == datetime(2026, 4, 20, 8, 30, tzinfo=UTC)
    with pytest.raises(GovernedWebSourceError, match="WEB_PUBLICATION_TIME_UNKNOWN"):
        formal_publication(
            text="所引用旧文件的发布日期: 2026-04-20",
            url=MATERIAL["attachment_url"],
            metadata=[],
            index=None,
            receipt_id=UUID(int=1),
            cutoff=NOW,
        )
