from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest

import stock_assistant.modules.research.web_fallback as fallback_module
from stock_assistant.contracts.discovery import (
    ResearchAcquisitionCandidate,
    ResearchAcquisitionState,
    ResearchWebNormalizationDraft,
    ResearchWebNormalizedFactDraft,
    ResearchWebQueryPlanDraft,
    ResearchWebQueryPlanItem,
    ResearchWebRetrievalReceipt,
    ResearchWebRetrievalReceiptStatus,
    ResearchWebRetrievalReceiptWriteResult,
    SourceDiscoveryCandidate,
    SourceDiscoveryQuery,
    SourceDiscoveryResult,
    SourceDiscoveryStatus,
)
from stock_assistant.contracts.ingestion import (
    FetchBatch,
    FetchRequest,
    RightsCheck,
    RightsDecision,
)
from stock_assistant.contracts.intelligence import GenerationRequest, GenerationResponse
from stock_assistant.contracts.research import (
    CoverageGap,
    ResearchAnalysisConfig,
    ResearchCutoffMode,
    ResearchEvidencePacket,
    ResearchEvidenceSourceLevel,
    ResearchProgress,
    ResearchRealSourceMode,
    ResearchSessionStatus,
    ResearchSessionView,
    ResearchSourceMode,
    ResearchStage,
)
from stock_assistant.contracts.research_reader import contains_forbidden_investment_advice
from stock_assistant.modules.research.web_fallback import (
    ResearchWebFallbackCoordinator,
    empty_real_evidence_packet,
    enabled_web_rights_snapshots,
)
from stock_assistant.platform.llm.deepseek import (
    DEEPSEEK_PRO_MODEL,
    DeepSeekLLMProvider,
    DeepSeekProviderError,
    DeepSeekTransportRequest,
    DeepSeekTransportResponse,
)
from stock_assistant.platform.llm.synthetic_research import SyntheticResearchLLMProvider
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet
from stock_assistant.platform.sources import governed_web as governed_web_module
from stock_assistant.platform.sources.governed_web import (
    DevelopmentResearchWebRightsPolicy,
    GovernedWebSourceConnector,
    GovernedWebSourceError,
)
from stock_assistant.ports.ingestion import RightsPolicyPort, SourceConnectorPort
from stock_assistant.ports.intelligence import LLMProviderError, LLMProviderFailureKind

_CORE_PRODUCTS = "business.core_products"
_CUSTOMER_REVENUE_CHAIN = "business.customer_revenue_chain"
_PRODUCT_STRUCTURE_CHANGE = "business.product_structure_change"
_CANONICAL_BUSINESS_GAPS = (
    _CORE_PRODUCTS,
    _CUSTOMER_REVENUE_CHAIN,
    _PRODUCT_STRUCTURE_CHANGE,
)
_VALID_LAST_MODIFIED = "Tue, 25 Aug 2026 00:00:00 GMT"
_ADVICE_MARKER = "公司公开披露材料仅供研究参考却仍出现ＰＲＩＣＥ　ＴＡＲＧＥＴ字样。"


def _formal_original(body: str, *, date: str = "2026-08-25") -> str:
    """Fixed official-file envelope for downstream acquisition regressions.

    Excerpts stay unchanged; wrong-subject fixtures retain their own code.
    Unrelated candidates never acquire an invented issuer.
    """
    code = next((value for value in ("603986", "688307", "600519") if value in body), None)
    if code is None:
        return body
    return f"2025 年年度报告\n证券代码: {code}\n发布日期: {date}\n" + body


class _AuditProvider(SyntheticResearchLLMProvider):
    def __init__(self, *, outcome: str = "passed", omit: bool = False) -> None:
        self.outcome, self.omit, self.calls = outcome, omit, 0

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls += 1
        assert request.task_type == "research_data_audit"
        data = json.loads(request.messages[-1].content)
        verdicts = [
            {
                "fact_key": fact["missing_fact_key"],
                "outcome": self.outcome,
                "reason_code": "SUPPORTED" if self.outcome == "passed" else "SUBJECT_MISMATCH",
                "reason": "原文支持。"
                if self.outcome == "passed"
                else "这是另一家公司的芯片资料。",
            }
            for fact in data["candidate_facts"]
        ]
        if self.omit:
            verdicts[0]["fact_key"] = "unrequested.key"
        return GenerationResponse(
            schema_version="1.0",
            payload={"schema_version": "1.0", "verdicts": verdicts},
            payload_namespace="research.data_audit",
            payload_owner="research",
            response_schema_hash=request.response_schema_hash,
            provider="offline-audit",
            model="offline-pro",
            adapter_version="test.v1",
            prompt_hash="a" * 64,
            raw_response_hash="b" * 64,
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            schema_validated=True,
        )


class _AcquisitionProvider(_AuditProvider):
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        data = json.loads(request.messages[-1].content)
        if request.task_type == "research_data_audit":
            self.outcome = "rejected" if "wrong-company" in data["source_text"] else "passed"
            return await super().generate(request)
        self.calls += 1
        if request.task_type == "research_web_gap_plan":
            payload = {
                "schema_version": "1.0",
                "queries": [
                    {
                        "query": "查询 "
                        + data["gaps"][0]["area"]
                        + " "
                        + str(data["attempt_number"]),
                        "missing_fact_keys": [data["gaps"][0]["area"]],
                    }
                ],
            }
        else:
            assert request.task_type == "research_web_evidence_normalization"
            payload = {
                "schema_version": "1.0",
                "facts": [
                    {
                        "missing_fact_key": key,
                        "source_excerpt": data["source_text"],
                        "value": "",
                        "unit": "",
                    }
                    for key in data["missing_fact_keys"]
                ],
            }
        return GenerationResponse(
            schema_version="1.0",
            payload=payload,
            payload_namespace="research.acquisition",
            payload_owner="research",
            response_schema_hash=request.response_schema_hash,
            provider="offline-flash",
            model="offline-flash",
            adapter_version="test.v1",
            prompt_hash="a" * 64,
            raw_response_hash="b" * 64,
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            schema_validated=True,
        )


class _AcquisitionDiscovery:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.counts: dict[str, int] = {}

    async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
        key = query.missing_fact_keys[0]
        self.counts[key] = self.counts.get(key, 0) + 1
        return SourceDiscoveryResult(
            schema_version="1.0",
            provider="offline-discovery",
            status=SourceDiscoveryStatus.SUCCEEDED,
            request_sha256="a" * 64,
            candidates=[_candidate(self._now, path=f"{key}/{self.counts[key]}")],
            searched_at=self._now,
            credits_used=1,
        )


@pytest.mark.parametrize(
    "mode", ["dated-page", "dated-business-resume", "duplicate-query", "duplicate-content"]
)
@pytest.mark.parametrize(
    "boundary", ["discovery-start", "discovery-saved", "extraction-start", "audit-start"]
)
async def test_source_dates_and_dedup_survive_acquisition_resume(mode: str, boundary: str) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )

    class Provider(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            if mode == "duplicate-query" and request.task_type == "research_web_gap_plan":
                response.payload["queries"][0]["query"] = "同一查询"  # type: ignore[index]
            return response

    class Discovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            response = await super().discover(query)
            response.candidates[0] = response.candidates[0].model_copy(
                update={"published_at": None}
            )
            return response

    flash, pro, discovery = Provider(), Provider(), Discovery(now)
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    class Interrupted(RuntimeError):
        pass

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        interrupt_key = (
            "business.core_products" if mode == "dated-business-resume" else "identity.company"
        )
        candidate_ready = any(
            fact.missing_fact_key == interrupt_key
            for item in state.candidates
            for fact in item.facts
        )
        discovery_ready = state.active_gap == interrupt_key and state.pending_query is not None
        extraction_ready = any(
            interrupt_key in item.requested_fact_keys and not item.extraction_complete
            for item in state.candidates
        )
        local_ready = mode == "dated-business-resume" and any(
            f.key == "identity.company" for f in packet.facts
        )

        at_boundary = {
            "discovery-start": (discovery_ready and state.pending_discovery is None) or local_ready,
            "discovery-saved": (discovery_ready and state.pending_discovery is not None)
            or local_ready,
            "extraction-start": extraction_ready,
            "audit-start": candidate_ready,
        }[boundary]
        if at_boundary and not interrupted:
            interrupted = True
            raise Interrupted

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        # Identical rejected original text appears at different URLs in duplicate modes.
        marker = "" if mode.startswith("dated-") else "wrong-company 芯片"
        body = (
            f"中润光学 688307 光学镜头。{marker}。离线隔离回归资料。"
            "2025年末市盈率18倍\uff0c同期行业可比口径20倍。"
        ) * 8
        return httpx.Response(
            200,
            text='<article><h1>2025 年年度报告</h1><meta name="pubdate" content="2026-08-25">'
            + _formal_original(body)
            + "</article>",
            headers={"content-type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=_Receipts(),
        )
        with pytest.raises(Interrupted):
            await coordinator.collect_real(
                session=session,
                packet=empty_real_evidence_packet(session),
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=5),
                state=None,
                save=save,
                progress=progress,
            )
        state, packet = saved[-1]
        result = await coordinator.collect_real(
            session=session,
            packet=packet,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            state=state,
            save=save,
            progress=progress,
        )
    final = saved[-1][0]
    assert final.complete
    assert final.active_gap is final.pending_query is final.pending_discovery is None
    assert "document_disclosure" in final.candidates[0].publication_basis
    assert final.attempted_queries and final.attempted_contents
    if boundary == "extraction-start":
        unknown_key = (
            "business.core_products" if mode == "dated-business-resume" else "identity.company"
        )
        assert unknown_key not in {fact.key for fact in result.facts}
        assert pro.calls == (1 if mode == "dated-business-resume" else 0)
        assert sum(unknown_key in item.requested_fact_keys for item in final.candidates) == 1
        assert final.attempts[unknown_key] <= 4
    elif mode.startswith("dated-"):
        assert result.facts and all(fact.data_audit_status == "passed" for fact in result.facts)
        assert pro.calls > 0
        assert all(count == 1 for count in final.attempts.values())
        assert all(count == 1 for count in discovery.counts.values())
    else:
        assert result.facts == []
        assert pro.calls == 1
        assert discovery.counts == {"identity.company": 1 if mode == "duplicate-query" else 4}
        assert len(final.attempted_contents["identity.company"]) == 1


@pytest.mark.parametrize("success_on", [4, 5])
@pytest.mark.parametrize("interrupt_after_identity", [False, True])
@pytest.mark.parametrize("rejection_kind", ["audit", "missing-date", "future-date"])
async def test_independent_rejection_refetches_up_to_third_extra_attempt(
    success_on: int, interrupt_after_identity: bool, rejection_kind: str
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )

    class DateAwareDiscovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            result = await super().discover(query)
            if (
                query.missing_fact_keys == ["identity.company"]
                and self.counts["identity.company"] < success_on
                and rejection_kind != "audit"
            ):
                result.candidates[0] = result.candidates[0].model_copy(
                    update={
                        "published_at": None
                        if rejection_kind == "missing-date"
                        else now + timedelta(days=1)
                    }
                )
            return result

    discovery = DateAwareDiscovery(now)
    flash, pro = _AcquisitionProvider(), _AcquisitionProvider()
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    class WorkerInterruption(RuntimeError):
        pass

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if interrupt_after_identity and not interrupted and len(packet.facts) == 1:
            interrupted = True
            raise WorkerInterruption

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        wrong = (
            "identity.company" in request.url.path
            and int(request.url.path.rsplit("/", 1)[1]) < success_on
        )
        marker = (
            "wrong-company 芯片"
            if wrong and rejection_kind == "audit"
            else "中润光学 688307 光学镜头"
        )
        content = (
            f"{marker}。离线测试资料, 仅用于验证采集隔离, 不是真实公司披露。"
            "2025年末市盈率18倍\uff0c同期行业可比口径20倍。"
            f"路径 {request.url.path}。" * 3
        )
        content = _formal_original(
            content,
            date=(now + timedelta(days=1)).isoformat()
            if wrong and rejection_kind == "future-date"
            else "2026-08-25",
        )
        if wrong and rejection_kind == "missing-date":
            content = content.replace("发布日期", "文件创建日期")
        return httpx.Response(
            200,
            text=content,
            headers={
                "content-type": "text/plain",
                "last-modified": _VALID_LAST_MODIFIED,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=_Receipts(),
        )
        try:
            result = await coordinator.collect_real(
                session=session,
                packet=empty_real_evidence_packet(session),
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=5),
                state=None,
                save=save,
                progress=progress,
            )
        except WorkerInterruption:
            durable_state, durable_packet = saved[-1]
            assert durable_state.attempts == {"identity.company": 4}
            assert durable_packet.facts[0].data_audit_status == "passed"
            result = await coordinator.collect_real(
                session=session,
                packet=durable_packet,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=10),
                state=durable_state,
                save=save,
                progress=progress,
            )
        completed_calls = flash.calls + pro.calls
        resumed = await coordinator.collect_real(
            session=session,
            packet=result,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=10),
            state=saved[-1][0],
            save=save,
            progress=progress,
        )
        assert resumed == result
        assert flash.calls + pro.calls == completed_calls
        assert saved[-1][0].deadline_at == now + timedelta(minutes=10)
    assert discovery.counts["identity.company"] == 4
    state = saved[-1][0]
    assert all(count <= 4 for count in state.attempts.values())
    assert all("wrong-company" not in fact.statement for fact in result.facts)
    assert all(fact.data_audit_status == "passed" for fact in result.facts)
    rejected = [
        verdict
        for item in state.candidates
        for verdict in item.verdicts
        if verdict.outcome == "rejected"
    ]
    assert rejected == []  # Wrong identity is rejected by the locator before model audit.
    if rejection_kind == "audit":
        assert sum(
            outcome.reason == "WEB_FORMAL_IDENTITY_UNVERIFIED"
            for outcome in state.verification_outcomes
        ) == min(4, success_on - 1)
    if success_on == 4:
        assert {"identity.company", "business.core_products"}.issubset(
            {fact.key for fact in result.facts}
        )
    else:
        assert result.facts == []
        assert discovery.counts == {"identity.company": 4}


@pytest.mark.parametrize("outcome", ["passed", "rejected"])
@pytest.mark.parametrize("key", ["business.core_products", "financial.quality", "risk.resilience"])
async def test_real_data_audit_is_a_separate_call_and_retains_rejection(
    outcome: str, key: str
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    extraction = _RecordingProvider()
    auditor = _AuditProvider(outcome=outcome)
    coordinator = ResearchWebFallbackCoordinator(
        provider=extraction,
        audit_provider=auditor,
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    source = "另一家公司披露主营业务为存储芯片。"
    candidate = ResearchAcquisitionCandidate(
        candidate=_candidate(now),
        content_sha256="a" * 64,
        first_seen_at=now,
        revision_known_at=now,
        source_context=source,
        facts=[ResearchWebNormalizedFactDraft(missing_fact_key=key, source_excerpt=source)],
    )
    result = await coordinator._audit(
        session=session,
        candidate=candidate,
        official_identity=empty_real_evidence_packet(session),
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
    )
    assert result.verdicts[0].outcome == "rejected"
    assert result.verdicts[0].reason_code == "SUBJECT_MISMATCH"
    assert extraction.task_types == []
    assert auditor.calls == 1


@pytest.mark.parametrize(
    ("locator", "shape", "expected"),
    [
        ("PDF page 12", "normal", "报告期内收购测试\n元件业务。"),
        ("PDF page 25", "normal", "报告期内收购测试 元件业务。"),
        ("PDF page 99", "normal", "报告期内收购测试元件业务。"),
        ("", "normal", "报告期内收购测试元件业务。"),
        ("PDF page 12; PDF page 25", "normal", "报告期内收购测试元件业务。"),
        ("PDF page 12", "duplicate_page", "报告期内收购测试元件业务。"),
        ("PDF page 12", "within_page", "报告期内收购测试元件业务。"),
        ("PDF page 12", "omitted", "报告期内收购测试元件业务。"),
    ],
)
@pytest.mark.parametrize("key", [_PRODUCT_STRUCTURE_CHANGE, "financial.quality"])
async def test_repeated_excerpt_uses_only_an_unambiguous_original_page(
    locator: str, shape: str, expected: str, key: str
) -> None:
    quote = "报告期内收购测试\n元件业务。"
    source = "[PDF page 12]\n" + quote + "\n[PDF page 25]\n" + quote.replace("\n", " ")
    if shape == "duplicate_page":
        source = source.replace("[PDF page 25]", "[PDF page 12]")
    elif shape == "within_page":
        source = source.replace("[PDF page 25]", "同页重复:")
    elif shape == "omitted":
        source = source.replace(
            "[PDF page 12]\n", "[PDF page 12]\n无关内容。" + fallback_module._CONTEXT_OMISSION
        )
    now = datetime.now(UTC)

    class Provider(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            response.payload = {
                "schema_version": "1.0",
                "facts": [
                    {
                        "missing_fact_key": key,
                        "source_excerpt": quote.replace("\n", ""),
                        "source_location": locator,
                    }
                ],
            }
            return response

    coordinator = ResearchWebFallbackCoordinator(
        provider=Provider(),
        audit_provider=_AuditProvider(),
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    result = await coordinator._normalize(
        session=_session(now),
        source_text=source,
        missing_fact_keys=[key],
        source_url=str(_candidate(now).url),
    )
    assert result.facts[0].source_excerpt == expected
    assert result.facts[0].source_location == locator
    assert result.facts[0].value == result.facts[0].unit == ""


@pytest.mark.parametrize(
    ("source", "excerpt", "key", "value", "unit", "expected"),
    [
        (
            "主营光学\n镜头及 成像组件。",
            "主营光学镜头及成像组件。",
            "business.core_products",
            "",
            "",
            "主营光学\n镜头及 成像组件。",
        ),
        ("测试\t光学公司", "测试光学公司", "identity.company", "", "", "测试\t光学公司"),
        (
            "客户采购光学\n镜头。",
            "客户采购光学镜头。",
            "business.customer_revenue_chain",
            "",
            "",
            "客户采购光学\n镜头。",
        ),
        (
            "董事会审议资本\n配置。",
            "董事会审议资本配置。",
            "management.capital_allocation",
            "",
            "",
            "董事会审议资本\n配置。",
        ),
        (
            "市盈率比较采用统一\n口径。",
            "市盈率比较采用统一口径。",
            "valuation.context",
            "",
            "",
            "市盈率比较采用统一\n口径。",
        ),
        (
            "公司面临客户集中\n风险。",
            "公司面临客户集中风险。",
            "risk.resilience",
            "",
            "",
            "公司面临客户集中\n风险。",
        ),
        ("债务100\n万元", "债务100万元", "risk.resilience", "100", "万元", "债务100万元"),
        (
            "客户采购光学\n镜头。客户采购光学 镜头。",
            "客户采购光学镜头。",
            "business.customer_revenue_chain",
            "",
            "",
            "客户采购光学镜头。",
        ),
        (
            "董事会审议资本\n配置。",
            "董事会批准资本配置。",
            "management.capital_allocation",
            "",
            "",
            "董事会批准资本配置。",
        ),
        ("主营光学镜头。", "主营光学镜头。", "business.core_products", "", "", "主营光学镜头。"),
        ("主营光学\n镜头。", "主营光学芯片。", "business.core_products", "", "", "主营光学芯片。"),
        (
            "主营光学\n镜头及组件。",
            "主营光学组件。",
            "business.core_products",
            "",
            "",
            "主营光学组件。",
        ),
        (
            "主营光学\n镜头。主营光学 镜头。",
            "主营光学镜头。",
            "business.core_products",
            "",
            "",
            "主营光学镜头。",
        ),
        ("营收100\n万元", "营收100万元", "business.core_products", "100", "万元", "营收100万元"),
        ("营收100\n万元", "营收100万元", "financial.quality", "", "", "营收100\n万元"),
        ("营收100\n万元", "营收101万元", "financial.quality", "", "", "营收101万元"),
        ("营收100\n万元", "营收100元", "financial.quality", "", "", "营收100元"),
        (
            "2025年 营收100\n万元",
            "2024年营收100万元",
            "financial.quality",
            "",
            "",
            "2024年营收100万元",
        ),
        (
            "营收100\n万元\uff0c利润20万元",
            "营收100万元\uff0c利润20万元",
            "financial.quality",
            "",
            "",
            "营收100\n万元\uff0c利润20万元",
        ),
        (
            "营收100\n万元\uff0c成本80万元\uff0c利润20万元",
            "营收100万元\uff0c利润20万元",
            "financial.quality",
            "",
            "",
            "营收100万元\uff0c利润20万元",
        ),
        (
            "公司专有工艺形成制\n造优势。",
            "公司专有工艺形成制造优势。",
            "competitive.position",
            "",
            "",
            "公司专有工艺形成制\n造优势。",
        ),
        (
            "光\n" * 600 + "学",
            "光" * 600 + "学",
            "business.core_products",
            "",
            "",
            "光\n" * 600 + "学",
        ),
        (
            "光\n" * 1100 + "学",
            "光" * 1100 + "学",
            "business.core_products",
            "",
            "",
            "光" * 1100 + "学",
        ),
    ],
)
async def test_narrative_excerpt_restores_only_unique_source_whitespace_before_audit(
    source: str, excerpt: str, key: str, value: str, unit: str, expected: str
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"instrument_id": "XSHG:688307", "instrument_name": "中润光学"}
    )
    source_text = "[PDF page 12] 中润光学 688307\n" + source

    class Provider(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            response.payload = {
                "schema_version": "1.0",
                "facts": [
                    {
                        "missing_fact_key": key,
                        "source_excerpt": excerpt,
                        "value": value,
                        "unit": unit,
                    }
                ],
            }
            return response

    class Auditor(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            data = json.loads(request.messages[-1].content)
            assert data["candidate_facts"][0]["source_excerpt"] == expected
            assert data["source_text"] == source_text
            return await super().generate(request)

    auditor = Auditor()
    coordinator = ResearchWebFallbackCoordinator(
        provider=Provider(),
        audit_provider=auditor,
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    normalized = await coordinator._normalize(
        session=session,
        source_text=source_text,
        missing_fact_keys=[key],
        source_url=str(_candidate(now).url),
    )
    assert normalized.facts[0].source_excerpt == expected
    candidate = ResearchAcquisitionCandidate(
        candidate=_candidate(now),
        content_sha256="a" * 64,
        first_seen_at=now,
        revision_known_at=now,
        source_context=source_text,
        facts=normalized.facts,
    )
    audit = await coordinator._audit(
        session=session,
        candidate=candidate,
        official_identity=None,
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
    )
    assert auditor.calls == 1
    assert (audit.verdicts[0].outcome == "passed") == (expected in source_text)


@pytest.mark.parametrize(
    ("real", "key", "enabled", "focused"),
    [
        (True, "identity.company", True, True),
        (True, "business.core_products", True, True),
        (True, "financial.quality", True, False),
        (False, "business.core_products", True, False),
        (True, "identity.company", False, True),
    ],
)
async def test_anchor_discovery_uses_existing_enabled_official_registrations(
    real: bool,
    key: str,
    enabled: bool,
    focused: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session = _session(now)
    allowlist = fallback_module._load_allowlist()
    if not enabled:
        allowlist = allowlist.model_copy(
            update={
                "sources": [
                    item.model_copy(update={"enabled": False})
                    if item.source_id == "cninfo_static"
                    else item
                    for item in allowlist.sources
                ]
            }
        )
        monkeypatch.setattr(fallback_module, "_load_allowlist", lambda: allowlist)
    registered = sorted(item.domain for item in allowlist.sources if item.enabled)
    discovery = _Discovery(now=now, candidates=[], status=SourceDiscoveryStatus.NO_RESULTS)
    coordinator = ResearchWebFallbackCoordinator(
        provider=_AcquisitionProvider(),
        audit_provider=_AuditProvider(),
        discovery=discovery,
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    if real:
        coordinator._acquisition = ResearchAcquisitionState(
            schema_version="1.3",
            rule_version=fallback_module.WEB_FALLBACK_RULE_VERSION,
            instrument_id=session.instrument_id,
            requested_as_of=now,
            deadline_at=now + timedelta(minutes=5),
            input_sha256="a" * 64,
        )
    packet = empty_real_evidence_packet(session).model_copy(
        update={
            "coverage_gaps": [
                CoverageGap(
                    area=key, reason_code="REQUIRED_FIELD_MISSING", detail="离线查询范围回归"
                )
            ]
        }
    )
    await coordinator.complete(
        session=session,
        packet=packet,
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=now + timedelta(minutes=5),
    )
    assert len(discovery.queries) == 1
    assert discovery.queries[0].include_domains == (
        sorted(
            item.domain
            for item in allowlist.sources
            if item.enabled
            and item.source_level
            in {
                ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
                ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
            }
        )
        if focused
        else registered
    )
    assert set(discovery.queries[0].include_domains) <= set(registered)


async def test_missing_data_audit_verdict_fails_closed() -> None:
    now = datetime.now(UTC)
    coordinator = ResearchWebFallbackCoordinator(
        provider=_RecordingProvider(),
        audit_provider=_AuditProvider(omit=True),
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    candidate = ResearchAcquisitionCandidate(
        candidate=_candidate(now),
        content_sha256="a" * 64,
        first_seen_at=now,
        revision_known_at=now,
        source_context="公司主营光学镜头。",
        facts=[
            ResearchWebNormalizedFactDraft(
                missing_fact_key="business.core_products", source_excerpt="公司主营光学镜头。"
            )
        ],
    )
    with pytest.raises(LLMProviderError, match="MODEL_DATA_AUDIT_INVALID"):
        await coordinator._audit(
            session=_session(now),
            candidate=candidate,
            official_identity=None,
            source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
        )


@pytest.mark.parametrize(
    ("key", "excerpt", "value", "unit", "period", "expected"),
    [
        ("identity.company", "另一家公司 000001。", "", "", "", "SUBJECT_MISMATCH"),
        ("financial.quality", "原文没有这句话。", "", "", "", "EXCERPT_MISMATCH"),
        (
            "financial.quality",
            "2025年 单位:万元 营收 100",
            "101",
            "万元",
            "2025年",
            "VALUE_MISMATCH",
        ),
        (
            "financial.quality",
            "2025年 单位:万元 营收 100",
            "100",
            "亿元",
            "2025年",
            "VALUE_MISMATCH",
        ),
        (
            "financial.quality",
            "2025年 单位:万元 营收 100",
            "100",
            "万元",
            "2024年",
            "PERIOD_MISMATCH",
        ),
        ("financial.quality", "2025年 单位:万元 营收 100", "100", "万元", "2025年", "SUPPORTED"),
    ],
)
async def test_audit_program_checks_cannot_be_overridden_by_pro_pass(
    key: str,
    excerpt: str,
    value: str,
    unit: str,
    period: str,
    expected: str,
) -> None:
    now = datetime.now(UTC)
    coordinator = ResearchWebFallbackCoordinator(
        provider=_RecordingProvider(),
        audit_provider=_AuditProvider(),
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    original = "2025年 单位:万元 营收 100"
    original = (
        "另一家公司 000001。" if key == "identity.company" else "中润光学 688307。"
    ) + original
    verdict = await coordinator._audit(
        session=_session(now).model_copy(
            update={"instrument_id": "XSHG:688307", "instrument_name": "中润光学"}
        ),
        candidate=ResearchAcquisitionCandidate(
            candidate=_candidate(now),
            content_sha256="a" * 64,
            first_seen_at=now,
            revision_known_at=now,
            source_context=original,
            facts=[
                ResearchWebNormalizedFactDraft(
                    missing_fact_key=key,
                    source_excerpt=excerpt,
                    value=value,
                    unit=unit,
                    period=period,
                )
            ],
        ),
        official_identity=None,
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
    )
    assert verdict.verdicts[0].reason_code == expected
    assert (verdict.verdicts[0].outcome == "passed") == (expected == "SUPPORTED")


async def test_real_acquisition_never_fills_default_facts_and_stops_after_four_attempts() -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    packet = empty_real_evidence_packet(session)
    assert packet.facts == [] and packet.citations == []
    snapshots: list[ResearchAcquisitionState] = []

    async def save(state: ResearchAcquisitionState, _packet: ResearchEvidencePacket) -> None:
        snapshots.append(state.model_copy(deep=True))

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    coordinator = ResearchWebFallbackCoordinator(
        provider=_RecordingProvider(),
        audit_provider=_AuditProvider(),
        discovery=_Discovery(now=now, candidates=[], status=SourceDiscoveryStatus.NO_RESULTS),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    result = await coordinator.collect_real(
        session=session,
        packet=packet,
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=now + timedelta(minutes=5),
        state=None,
        save=save,
        progress=progress,
    )
    assert result.facts == []
    assert snapshots[-1].attempts == {"identity.company": 4}
    assert snapshots[-1].complete


class _DiscoveryFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Discovery:
    def __init__(
        self,
        *,
        now: datetime,
        candidates: list[SourceDiscoveryCandidate],
        status: SourceDiscoveryStatus = SourceDiscoveryStatus.SUCCEEDED,
        error: Exception | None = None,
        entered: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.calls = 0
        self.queries: list[SourceDiscoveryQuery] = []
        self._now = now
        self._candidates = candidates
        self._status = status
        self._error = error
        self._entered = entered
        self._release = release

    async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
        self.calls += 1
        self.queries.append(query)
        assert query.end_date == query.context.as_of
        assert query.max_results == 5
        if self._entered is not None:
            self._entered.set()
        if self._release is not None:
            await self._release.wait()
        if self._error is not None:
            raise self._error
        return SourceDiscoveryResult(
            schema_version="1.0",
            provider="synthetic-discovery",
            status=self._status,
            request_sha256="a" * 64,
            candidates=self._candidates,
            searched_at=self._now,
            credits_used=1,
        )


class _RecordingProvider(SyntheticResearchLLMProvider):
    def __init__(self) -> None:
        self.task_types: list[str] = []

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.task_types.append(request.task_type)
        return await super().generate(request)


class _FailingPlanProvider(SyntheticResearchLLMProvider):
    def __init__(self, failure_kind: LLMProviderFailureKind) -> None:
        self._failure_kind = failure_kind

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if request.task_type == "research_web_gap_plan":
            raise LLMProviderError(
                "MODEL_PLAN_FAILED",
                failure_kind=self._failure_kind,
                retryable=False,
            )
        return await super().generate(request)


class _Receipts:
    def __init__(self) -> None:
        self.rows: dict[UUID, ResearchWebRetrievalReceipt] = {}

    async def save(
        self, receipt: ResearchWebRetrievalReceipt
    ) -> ResearchWebRetrievalReceiptWriteResult:
        replay = receipt.receipt_id in self.rows
        self.rows.setdefault(receipt.receipt_id, receipt)
        return ResearchWebRetrievalReceiptWriteResult(
            schema_version="1.0",
            receipt_id=receipt.receipt_id,
            stored=not replay,
            idempotent_replay=replay,
            processed_at=receipt.created_at,
        )

    async def purge_expired(self, *, now: datetime) -> int:
        expired = [key for key, value in self.rows.items() if value.expires_at <= now]
        for key in expired:
            del self.rows[key]
        return len(expired)


@pytest.mark.parametrize("failure", ["dns", "robots", "audit", "future-date", "normalization"])
@pytest.mark.parametrize("interrupt", [False, True])
async def test_real_acquisition_uses_next_candidate_after_rejection(
    failure: str, interrupt: bool
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )

    class Discovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            result = await super().discover(query)
            if query.missing_fact_keys == ["identity.company"]:
                result.candidates = [
                    _candidate(
                        now,
                        path=f"identity.company/candidate/{index}",
                        published_at=(
                            now + timedelta(days=1)
                            if failure == "future-date" and index == 1
                            else None
                        ),
                    )
                    for index in range(1, 6)
                ]
            return result

    class Provider(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            if request.task_type == "research_web_evidence_normalization":
                data = json.loads(request.messages[-1].content)
                if failure == "normalization" and data["source_url"].endswith("/candidate/1"):
                    raise LLMProviderError(
                        "DEEPSEEK_RESPONSE_SCHEMA_INVALID",
                        failure_kind=LLMProviderFailureKind.INVALID_OUTPUT,
                    )
            return await super().generate(request)

    discovery = Discovery(now)
    flash, pro, receipts = Provider(), _AcquisitionProvider(), _Receipts()
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    requests: list[str] = []
    resolutions = 0
    interrupted = False

    async def resolver(_host: str) -> tuple[str, ...]:
        nonlocal resolutions
        resolutions += 1
        # Preserve the actual connector's DNS rejection for the first source.
        return ("8.8.8.8" if failure == "dns" and resolutions == 2 else "1.1.1.1",)

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if (
            interrupt
            and not interrupted
            and state.active_gap == "identity.company"
            and state.pending_discovery is not None
            and len(state.attempted_sources.get("identity.company", [])) == 1
        ):
            interrupted = True
            raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            rule = "Disallow: /identity.company/candidate/1" if failure == "robots" else "Allow: /"
            return httpx.Response(200, text=f"User-agent: *\n{rule}")
        first = request.url.path == "/identity.company/candidate/1"
        if failure == "robots":
            assert not first, "Rejected first source must not be downloaded"
        marker = "wrong-company 芯片" if failure == "audit" and first else "中润光学 688307"
        return httpx.Response(
            200,
            text=(
                _formal_original(
                    f"{marker} 光学镜头。离线隔离回归资料 {request.url.path}。" * 5,
                    date=(now + timedelta(days=1)).isoformat()
                    if failure == "future-date" and first
                    else "2026-08-25",
                )
            ),
            headers={"content-type": "text/plain", "last-modified": _VALID_LAST_MODIFIED},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=receipts,
        )
        if interrupt:
            with pytest.raises(asyncio.CancelledError):
                await coordinator.collect_real(
                    session=session,
                    packet=empty_real_evidence_packet(session),
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                    state=None,
                    save=save,
                    progress=progress,
                )
        result = await coordinator.collect_real(
            session=session,
            packet=saved[-1][1] if saved else empty_real_evidence_packet(session),
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            state=saved[-1][0] if saved else None,
            save=save,
            progress=progress,
        )

    assert {"identity.company", "business.core_products"}.issubset(
        {fact.key for fact in result.facts}
    )
    assert discovery.counts["identity.company"] == 1
    assert requests.count("/identity.company/candidate/1") == (0 if failure == "robots" else 1)
    assert requests.count("/identity.company/candidate/2") == 1
    assert not any(f"/identity.company/candidate/{i}" in requests for i in range(3, 6))
    assert all(fact.data_audit_status == "passed" for fact in result.facts)
    assert all("wrong-company" not in fact.statement for fact in result.facts)
    assert len(saved[-1][0].attempted_sources["identity.company"]) == 2
    if failure == "audit":
        assert any(
            outcome.reason == "WEB_FORMAL_IDENTITY_UNVERIFIED"
            for outcome in saved[-1][0].verification_outcomes
        )
        assert all(
            "candidate/1" not in str(candidate.candidate.url)
            for candidate in saved[-1][0].candidates
        )
    elif failure == "normalization":
        assert all(
            row.status is ResearchWebRetrievalReceiptStatus.FETCHED
            for row in receipts.rows.values()
        )
        assert not saved[-1][0].candidates[0].extraction_complete
    else:
        assert any(
            row.status is not ResearchWebRetrievalReceiptStatus.FETCHED
            for row in receipts.rows.values()
        )


@pytest.mark.parametrize("mode", ["alternative", "resume", "unavailable"])
async def test_supplemental_transport_failure_keeps_other_candidates_and_gaps_running(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fallback_module,
        "REAL_FACT_GAPS",
        {
            "identity.company": "主体",
            "business.core_products": "主营",
            "financial.quality": "财务",
            "risk.resilience": "风险",
        },
    )
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    failed = SourceDiscoveryCandidate.model_validate(
        {
            **_candidate(now).model_dump(mode="json"),
            "url": "https://www.stcn.com/financial/1",
            "domain": "www.stcn.com",
        }
    )
    failed_hash = hashlib.sha256(str(failed.url).encode()).hexdigest()

    class Discovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            result = await super().discover(query)
            if query.missing_fact_keys == ["financial.quality"]:
                result.candidates = [failed] + ([] if mode == "unavailable" else result.candidates)
            return result

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            if request.task_type == "research_web_evidence_normalization":
                data = json.loads(request.messages[-1].content)
                if "/financial.quality/" not in data["source_url"]:
                    response.payload["facts"] = [
                        fact
                        for fact in response.payload["facts"]
                        if fact["missing_fact_key"] != "financial.quality"
                    ]
            return response

    discovery, receipts = Discovery(now), _Receipts()
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    requests: list[tuple[str, str]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if (
            mode == "resume"
            and not interrupted
            and state.active_gap == "financial.quality"
            and state.pending_discovery is not None
            and _gap_reasons(packet).get("financial.quality") == "WEB_FETCH_TEMPORARILY_UNAVAILABLE"
        ):
            interrupted = True
            raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.host, request.url.path))
        if request.url.host == "www.stcn.com":
            assert request.url.path == "/robots.txt", "No document without robots verdict"
            raise httpx.ReadTimeout("offline supplemental robots timeout", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200,
            text=(
                _formal_original(
                    f"中润光学 688307 光学镜头。离线隔离回归资料 {request.url.path}。" * 5
                )
            ),
            headers={"content-type": "text/plain", "last-modified": _VALID_LAST_MODIFIED},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=Flash(),
            audit_provider=Flash(),
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn", "www.stcn.com"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=receipts,
        )
        if mode == "resume":
            with pytest.raises(asyncio.CancelledError):
                await coordinator.collect_real(
                    session=session,
                    packet=empty_real_evidence_packet(session),
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                    state=None,
                    save=save,
                    progress=progress,
                )
            assert saved[-1][0].pending_discovery is not None
            assert failed_hash not in saved[-1][0].attempted_sources.get("financial.quality", [])
        result = await coordinator.collect_real(
            session=session,
            packet=saved[-1][1] if saved else empty_real_evidence_packet(session),
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            state=saved[-1][0] if saved else None,
            save=save,
            progress=progress,
        )

    keys = {fact.key for fact in result.facts}
    assert {"identity.company", "business.core_products", "risk.resilience"} <= keys
    assert ("financial.quality" in keys) is (mode != "unavailable"), (
        discovery.counts,
        saved[-1][0].attempts,
        [(g.area, g.reason_code) for g in result.coverage_gaps],
    )
    assert discovery.counts["financial.quality"] == (4 if mode == "unavailable" else 1)
    assert (
        requests.count(("www.stcn.com", "/robots.txt"))
        == {"alternative": 2, "resume": 4, "unavailable": 8}[mode]
    )
    assert requests.count(("www.cninfo.com.cn", "/identity.company/1")) == 1
    assert requests.count(("www.cninfo.com.cn", "/financial.quality/1")) == (
        0 if mode == "unavailable" else 1
    )
    assert all(
        failed_hash not in state.attempted_sources.get("financial.quality", [])
        for state, _packet in saved
    )
    assert len(saved[-1][0].attempted_sources.get("financial.quality", [])) == (
        0 if mode == "unavailable" else 1
    )
    assert saved[-1][0].complete
    assert all(fact.data_audit_status == "passed" for fact in result.facts)
    assert any(
        row.status is ResearchWebRetrievalReceiptStatus.FAILED for row in receipts.rows.values()
    )
    if mode == "unavailable":
        assert _gap_reasons(result)["financial.quality"] == "WEB_FETCH_TEMPORARILY_UNAVAILABLE"


@pytest.mark.parametrize("failure", ["invalid", "auth", "transport", "timeout", "cancel"])
async def test_real_normalization_exhaustion_keeps_other_topics_but_preserves_external_failures(
    failure: str,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    attempted: list[str] = []

    class Provider(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            data = json.loads(request.messages[-1].content)
            if request.task_type == "research_web_evidence_normalization" and data[
                "missing_fact_keys"
            ] == ["valuation.context"]:
                attempted.append(data["source_url"])
                if failure == "timeout":
                    raise TimeoutError
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise LLMProviderError(
                    "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
                    if failure == "invalid"
                    else "PROVIDER_UNAVAILABLE",
                    failure_kind={
                        "invalid": LLMProviderFailureKind.INVALID_OUTPUT,
                        "auth": LLMProviderFailureKind.AUTH_CONFIG,
                        "transport": LLMProviderFailureKind.TRANSPORT,
                    }[failure],
                )
            return await super().generate(request)

    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200,
            text=(
                _formal_original(
                    f"中润光学 688307 光学镜头。离线隔离回归资料 {request.url.path}。" * 5
                )
            ),
            headers={"content-type": "text/plain", "last-modified": _VALID_LAST_MODIFIED},
        )

    discovery, receipts = _AcquisitionDiscovery(now), _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=Provider(),
            audit_provider=_AcquisitionProvider(),
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=receipts,
        )
        operation = coordinator.collect_real(
            session=session,
            packet=empty_real_evidence_packet(session),
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            state=None,
            save=save,
            progress=progress,
        )
        if failure == "invalid":
            result = await operation
            keys = {fact.key for fact in result.facts}
            assert {
                "identity.company",
                "business.core_products",
                "financial.quality",
                "risk.resilience",
            } <= keys
            assert "valuation.context" not in keys
            assert _gap_reasons(result)["valuation.context"] == "WEB_FACT_VALIDATION_FAILED"
            assert len(attempted) == len(set(attempted)) == 5
            assert saved[-1][0].attempts["valuation.context"] == 4
            assert saved[-1][0].complete
            assert all(fact.data_audit_status == "passed" for fact in result.facts)
            candidates = [
                item
                for item in saved[-1][0].candidates
                if "valuation.context" in item.requested_fact_keys
            ]
            assert len(candidates) == 6
            assert candidates[0].extraction_complete
            assert candidates[0].verdicts[0].outcome != "passed"
            candidates = candidates[1:]
            assert all(not item.extraction_complete for item in candidates)
            assert [item.attempt_numbers["valuation.context"] for item in candidates] == [
                1,
                1,
                2,
                3,
                4,
            ]
            assert all(len(sources) <= 5 for sources in saved[-1][0].round_sources.values())
            assert all(
                row.status is ResearchWebRetrievalReceiptStatus.FETCHED
                for row in receipts.rows.values()
            )
        else:
            error_type = (
                TimeoutError
                if failure == "timeout"
                else (asyncio.CancelledError if failure == "cancel" else LLMProviderError)
            )
            with pytest.raises(error_type):
                await operation
            assert len(attempted) == 1
            assert not saved[-1][0].complete
            assert saved[-1][0].pending_discovery is None
            pending = saved[-1][0].candidates[-1]
            assert pending.document_id is not None and not pending.extraction_complete
            assert "valuation.context" in pending.requested_fact_keys
            assert saved[-1][0].attempts["valuation.context"] == 1
            assert not saved[-1][0].attempted_sources.get("valuation.context")
            assert "risk.resilience" not in discovery.counts


@pytest.mark.parametrize("interrupt_after", [0, 1, 5])
async def test_real_acquisition_exhausts_five_rejected_candidates_without_replay(
    interrupt_after: int,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )

    class Discovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            result = await super().discover(query)
            result.candidates = [_candidate(now, path=f"blocked/{i}") for i in range(5)]
            return result

    class Connector:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def fetch(self, request: FetchRequest) -> FetchBatch:
            url = str(request.options["url"])
            self.urls.append(url)
            raise GovernedWebSourceError(
                "WEB_DNS_REBINDING_BLOCKED"
                if url.endswith("/0")
                else "WEB_PUBLICATION_TIME_UNKNOWN"
            )

    discovery, connector = Discovery(now), Connector()
    flash, pro = _AcquisitionProvider(), _AcquisitionProvider()
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if (
            interrupt_after
            and not interrupted
            and state.active_gap == "identity.company"
            and state.pending_discovery is not None
            and len(state.attempted_sources.get("identity.company", [])) == interrupt_after
        ):
            interrupted = True
            raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=discovery,
        connector=connector,
        rights=_rights(),
        receipts=_Receipts(),
    )
    if interrupt_after:
        with pytest.raises(asyncio.CancelledError):
            await coordinator.collect_real(
                session=session,
                packet=empty_real_evidence_packet(session),
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=5),
                state=None,
                save=save,
                progress=progress,
            )
    result = await coordinator.collect_real(
        session=session,
        packet=saved[-1][1] if saved else empty_real_evidence_packet(session),
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=now + timedelta(minutes=5),
        state=saved[-1][0] if saved else None,
        save=save,
        progress=progress,
    )
    assert len(connector.urls) == len(set(connector.urls)) == 5
    assert discovery.counts == {"identity.company": 4}
    assert result.facts == [] and pro.calls == 0 and flash.calls == 4
    assert _gap_reasons(result)["identity.company"] == "WEB_SOURCE_ACCESS_BLOCKED"
    assert "WEB_DNS_REBINDING_BLOCKED" in next(
        gap.detail for gap in result.coverage_gaps if gap.area == "identity.company"
    )
    assert saved[-1][0].complete
    assert saved[-1][0].pending_discovery is None
    assert len(saved[-1][0].attempted_sources["identity.company"]) == 5


@pytest.mark.parametrize("failure", ["timeout", "connect", "robots"])
async def test_real_fetch_interruption_preserves_attempt_and_discovery_for_recovery(
    failure: str,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    discovery = _AcquisitionDiscovery(now)
    flash, pro = _AcquisitionProvider(), _AcquisitionProvider()
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    requests: list[str] = []
    unavailable = True

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if unavailable:
            if failure == "robots":
                return httpx.Response(200, text="User-agent: *\nDisallow: /")
            error_type = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
            raise error_type("offline transport interruption", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200,
            text=(
                _formal_original(
                    f"中润光学 688307 光学镜头。离线隔离测试资料 {request.url.path}。" * 8
                )
            ),
            headers={"content-type": "text/plain", "last-modified": _VALID_LAST_MODIFIED},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=_Receipts(),
        )
        if failure == "robots":
            result = await coordinator.collect_real(
                session=session,
                packet=empty_real_evidence_packet(session),
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=5),
                state=None,
                save=save,
                progress=progress,
            )
            assert result.facts == []
            assert discovery.counts == {"identity.company": 4}
            assert requests == ["/robots.txt"] * 4
            assert saved[-1][0].robots_origin_stops == []
            assert saved[-1][0].complete
            assert _gap_reasons(result)["identity.company"] == "WEB_SOURCE_ACCESS_BLOCKED"
            return
        for _ in range(2):
            state, packet = saved[-1] if saved else (None, empty_real_evidence_packet(session))
            with pytest.raises(GovernedWebSourceError) as caught:
                await coordinator.collect_real(
                    session=session,
                    packet=packet,
                    state=state,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                    save=save,
                    progress=progress,
                )
            assert caught.value.code == "WEB_FETCH_TEMPORARILY_UNAVAILABLE"
            assert caught.value.retryable
            state, packet = saved[-1]
            assert state.attempts == {"identity.company": 1}
            assert not state.complete and state.pending_discovery is not None
            assert not state.attempted_sources and not state.candidates
            assert packet.facts == [] and pro.calls == 0
            assert discovery.counts == {"identity.company": 1}
        assert requests == ["/robots.txt"] * 4
        unavailable = False
        result = await coordinator.collect_real(
            session=session,
            packet=saved[-1][1],
            state=saved[-1][0],
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            save=save,
            progress=progress,
        )
    assert discovery.counts["identity.company"] == 1
    assert saved[-1][0].attempts["identity.company"] == 1
    assert {"identity.company", "business.core_products"}.issubset(
        {fact.key for fact in result.facts}
    )
    assert all(fact.data_audit_status == "passed" for fact in result.facts)


class _FailingConnector:
    async def fetch(self, _request: FetchRequest) -> FetchBatch:
        raise OSError("synthetic governed fetch failure")


@pytest.mark.parametrize("mode", ["robots-unreadable", "document-unreadable", "fourth-succeeds"])
async def test_content_decode_failure_is_bounded_before_audit_and_scoring(mode: str) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    flash, pro, discovery = (
        _AcquisitionProvider(),
        _AcquisitionProvider(),
        _AcquisitionDiscovery(now),
    )
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    requests: list[str] = []
    identity_documents = 0

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    async def progress(_stage: ResearchStage, _message: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal identity_documents
        requests.append(request.url.path)
        robots = request.url.path == "/robots.txt"
        if robots and mode != "robots-unreadable":
            return httpx.Response(404)
        if request.url.path.startswith("/identity.company/"):
            identity_documents += 1
        if mode == "robots-unreadable" or (
            request.url.path.startswith("/identity.company/")
            and (mode == "document-unreadable" or identity_documents < 4)
        ):
            return httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                stream=httpx.ByteStream(b"invalid offline gzip"),
            )
        return httpx.Response(
            200,
            text=_formal_original(
                f"中润光学 688307 光学镜头。离线隔离测试资料 {request.url.path}。" * 8
            ),
            headers={"content-type": "text/plain", "last-modified": _VALID_LAST_MODIFIED},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=flash,
            audit_provider=pro,
            discovery=discovery,
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=_Receipts(),
        )
        result = await coordinator.collect_real(
            session=session,
            packet=empty_real_evidence_packet(session),
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            state=None,
            save=save,
            progress=progress,
        )
    state = saved[-1][0]
    assert state.complete and state.pending_discovery is None
    if mode == "robots-unreadable":
        assert requests == ["/robots.txt"] * 4
        assert state.attempts == {"identity.company": 4}
        assert state.robots_origin_stops == []
        assert result.facts == [] and pro.calls == 0
        assert _gap_reasons(result)["identity.company"] == "WEB_SOURCE_ACCESS_BLOCKED"
    else:
        assert identity_documents == 4
        assert state.attempts["identity.company"] == 4
        assert len(set(state.attempted_queries["identity.company"])) == 4
        assert len(set(state.attempted_sources["identity.company"])) == 4
        if mode == "document-unreadable":
            assert discovery.counts == {"identity.company": 4}
            assert flash.calls == 4 and pro.calls == 0 and result.facts == []
            assert any("WEB_CONTENT_DECODE_FAILED" in gap.detail for gap in result.coverage_gaps)
        else:
            assert {"identity.company", "business.core_products"}.issubset(
                {fact.key for fact in result.facts}
            )
            assert all(fact.data_audit_status == "passed" for fact in result.facts)


class _DeniedRights:
    async def authorize(self, check: RightsCheck) -> RightsDecision:
        return RightsDecision(
            allowed=False,
            reason_code="WEB_RIGHTS_BLOCKED",
            decided_at=check.context.requested_at,
            rights_snapshot_hash=check.rights_snapshot_hash,
        )


class _UnboundCoordinator(ResearchWebFallbackCoordinator):
    async def _normalize(
        self,
        *,
        session: ResearchSessionView,
        source_text: str,
        missing_fact_keys: list[str],
        source_url: str,
        prior_audit_feedback: dict[str, str] | None = None,
    ) -> ResearchWebNormalizationDraft:
        del session, source_text, source_url
        return ResearchWebNormalizationDraft(
            schema_version="1.0",
            facts=[
                ResearchWebNormalizedFactDraft(
                    missing_fact_key=missing_fact_keys[0],
                    source_excerpt="正文中不存在的数值 999 元",
                    value="999",
                    unit="元",
                )
            ],
        )


class _AllGapPlanCoordinator(ResearchWebFallbackCoordinator):
    async def _plan(
        self,
        *,
        session: ResearchSessionView,
        packet: ResearchEvidencePacket,
        gaps: list[CoverageGap],
    ) -> ResearchWebQueryPlanDraft:
        del session, packet
        return ResearchWebQueryPlanDraft(
            schema_version="1.0",
            queries=[
                ResearchWebQueryPlanItem(
                    query="兆易创新 核心业务 产品结构",
                    missing_fact_keys=[gap.area for gap in gaps],
                )
            ],
        )


class _DuplicateGapPlanCoordinator(ResearchWebFallbackCoordinator):
    async def _plan(
        self,
        *,
        session: ResearchSessionView,
        packet: ResearchEvidencePacket,
        gaps: list[CoverageGap],
    ) -> ResearchWebQueryPlanDraft:
        del session, packet
        key = gaps[0].area
        return ResearchWebQueryPlanDraft(
            schema_version="1.0",
            queries=[
                ResearchWebQueryPlanItem(
                    query="兆易创新 核心业务",
                    missing_fact_keys=[key],
                ),
                ResearchWebQueryPlanItem(
                    query="兆易创新 主要产品",
                    missing_fact_keys=[key],
                ),
            ],
        )


class _InvalidNormalizationCoordinator(ResearchWebFallbackCoordinator):
    async def _normalize(
        self,
        *,
        session: ResearchSessionView,
        source_text: str,
        missing_fact_keys: list[str],
        source_url: str,
        prior_audit_feedback: dict[str, str] | None = None,
    ) -> ResearchWebNormalizationDraft:
        del session, source_text, missing_fact_keys, source_url
        raise LLMProviderError(
            "MODEL_NORMALIZATION_INVALID",
            failure_kind=LLMProviderFailureKind.INVALID_OUTPUT,
            retryable=False,
        )


class _UnavailableNormalizationCoordinator(ResearchWebFallbackCoordinator):
    async def _normalize(
        self,
        *,
        session: ResearchSessionView,
        source_text: str,
        missing_fact_keys: list[str],
        source_url: str,
        prior_audit_feedback: dict[str, str] | None = None,
    ) -> ResearchWebNormalizationDraft:
        del session, source_text, missing_fact_keys, source_url
        raise LLMProviderError(
            "MODEL_NORMALIZATION_UNAVAILABLE",
            failure_kind=LLMProviderFailureKind.TRANSPORT,
            retryable=False,
        )


class _AdviceNormalizationCoordinator(ResearchWebFallbackCoordinator):
    async def _normalize(
        self,
        *,
        session: ResearchSessionView,
        source_text: str,
        missing_fact_keys: list[str],
        source_url: str,
        prior_audit_feedback: dict[str, str] | None = None,
    ) -> ResearchWebNormalizationDraft:
        if "normalized-advice" not in source_url:
            return await super()._normalize(
                session=session,
                source_text=source_text,
                missing_fact_keys=missing_fact_keys,
                source_url=source_url,
                prior_audit_feedback=prior_audit_feedback,
            )
        assert _ADVICE_MARKER in source_text
        return ResearchWebNormalizationDraft(
            schema_version="1.0",
            facts=[
                ResearchWebNormalizedFactDraft(
                    missing_fact_key=missing_fact_keys[0],
                    source_excerpt=_ADVICE_MARKER,
                )
            ],
        )


class _BlockingRights:
    def __init__(
        self,
        delegate: RightsPolicyPort,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._delegate = delegate
        self._entered = entered
        self._release = release

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        self._entered.set()
        await self._release.wait()
        return await self._delegate.authorize(check)


class _BlockingConnector:
    def __init__(
        self,
        delegate: SourceConnectorPort,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._delegate = delegate
        self._entered = entered
        self._release = release

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        self._entered.set()
        await self._release.wait()
        return await self._delegate.fetch(request)


class _BlockingNormalizationCoordinator(ResearchWebFallbackCoordinator):
    def __init__(
        self,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._entered = entered
        self._release = release

    async def _normalize(
        self,
        *,
        session: ResearchSessionView,
        source_text: str,
        missing_fact_keys: list[str],
        source_url: str,
        prior_audit_feedback: dict[str, str] | None = None,
    ) -> ResearchWebNormalizationDraft:
        self._entered.set()
        await self._release.wait()
        return await super()._normalize(
            session=session,
            source_text=source_text,
            missing_fact_keys=missing_fact_keys,
            source_url=source_url,
            prior_audit_feedback=prior_audit_feedback,
        )


class _BlockingReceipts(_Receipts):
    def __init__(self, *, entered: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__()
        self._entered = entered
        self._release = release

    async def save(
        self, receipt: ResearchWebRetrievalReceipt
    ) -> ResearchWebRetrievalReceiptWriteResult:
        self._entered.set()
        await self._release.wait()
        return await super().save(receipt)


def _session(now: datetime) -> ResearchSessionView:
    return ResearchSessionView(
        schema_version="1.0",
        session_id=uuid4(),
        job_id=uuid4(),
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=now,
        analysis_config=ResearchAnalysisConfig(schema_version="1.0"),
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        status=ResearchSessionStatus.RUNNING,
        progress=ResearchProgress(
            stage=ResearchStage.COLLECTING_HISTORY,
            message="正在补齐已记录的数据缺口。",
            latest_progress_at=now,
            checkpoint="built-in-gap-recorded",
        ),
        created_at=now - timedelta(minutes=1),
        updated_at=now,
        expires_at=now + timedelta(days=7),
    )


def _packet(
    session: ResearchSessionView,
    *gap_keys: str,
    reason_code: str = "REQUIRED_FIELD_MISSING",
) -> ResearchEvidencePacket:
    base = synthetic_evidence_packet(
        instrument_id=session.instrument_id,
        instrument_name=session.instrument_name,
        as_of=session.as_of,
    )
    return base.model_copy(
        update={
            "coverage_gaps": [
                *base.coverage_gaps,
                *(
                    CoverageGap(
                        area=key,
                        reason_code=reason_code,
                        detail="内置业务资料缺少必填项, 允许当前研究受控补证。",
                        requested_start=base.coverage_start,
                        actual_start=base.coverage_start,
                        actual_end=base.coverage_end,
                    )
                    for key in gap_keys
                ),
            ]
        }
    )


def _candidate(
    now: datetime,
    *,
    path: str = "disclosure/1",
    published_at: datetime | None = None,
    publication_time_missing: bool = False,
) -> SourceDiscoveryCandidate:
    return SourceDiscoveryCandidate.model_validate(
        {
            "schema_version": "1.0",
            "url": f"https://www.cninfo.com.cn/{path}",
            "title": "可核验公开披露",
            "domain": "www.cninfo.com.cn",
            "published_at": (
                None if publication_time_missing else published_at or now - timedelta(days=2)
            ),
            "discovery_score": "0.90",
        }
    )


def _page_handler(
    *,
    robots_body: str = "User-agent: *\nAllow: /",
    last_modified: str | None = _VALID_LAST_MODIFIED,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots_body)
        headers = {"content-type": "text/plain; charset=utf-8"}
        if last_modified is not None:
            headers["last-modified"] = last_modified
        return httpx.Response(
            200,
            text=(
                "公开披露明确说明了待补齐业务事实。" * 20 + "收入为100元。收入为10, 成本单位为元。"
            ),
            headers=headers,
        )

    return handler


def _advice_page_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /")
    safe_body = "公开披露明确说明了待补齐业务事实。" * 20
    if request.url.path.startswith("/raw-advice/"):
        body = f"{safe_body}{_ADVICE_MARKER}"
    elif request.url.path.startswith("/normalized-advice/"):
        body = f"{_ADVICE_MARKER}{safe_body}"
    else:
        body = safe_body
    return httpx.Response(
        200,
        text=body,
        headers={
            "content-type": "text/plain; charset=utf-8",
            "last-modified": _VALID_LAST_MODIFIED,
        },
    )


async def _public_resolver(_hostname: str) -> tuple[str, ...]:
    return ("1.1.1.1",)


async def _private_resolver(_hostname: str) -> tuple[str, ...]:
    return ("127.0.0.1",)


def _rights() -> DevelopmentResearchWebRightsPolicy:
    return DevelopmentResearchWebRightsPolicy(enabled_web_rights_snapshots())


def _coordinator(
    *,
    client: httpx.AsyncClient,
    discovery: _Discovery,
    receipts: _Receipts,
    clock: Callable[[], datetime],
    provider: SyntheticResearchLLMProvider | None = None,
    rights: RightsPolicyPort | None = None,
    resolver: Callable[[str], object] = _public_resolver,
    coordinator_type: type[ResearchWebFallbackCoordinator] = ResearchWebFallbackCoordinator,
) -> ResearchWebFallbackCoordinator:
    return coordinator_type(
        provider=provider or SyntheticResearchLLMProvider(),
        discovery=discovery,
        connector=GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=resolver,  # type: ignore[arg-type]
            clock=clock,
        ),
        rights=rights or _rights(),
        receipts=receipts,
    )


async def _complete(
    coordinator: ResearchWebFallbackCoordinator,
    *,
    session: ResearchSessionView,
    packet: ResearchEvidencePacket,
    mode: ResearchCutoffMode = ResearchCutoffMode.CURRENT_RUN,
    deadline: datetime | None = None,
) -> ResearchEvidencePacket:
    return await coordinator.complete(
        session=session,
        packet=packet,
        cutoff_mode=mode,
        task_deadline_at=deadline or session.as_of + timedelta(seconds=30),
    )


def _gap_reasons(packet: ResearchEvidencePacket) -> dict[str, str]:
    return {gap.area: gap.reason_code for gap in packet.coverage_gaps}


@pytest.mark.asyncio
async def test_fallback_never_runs_without_a_canonical_business_gap() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, "business.revenue_mix", reason_code="API_TIMEOUT")
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    provider = _RecordingProvider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now - timedelta(days=1),
                provider=provider,
            ),
            session=session,
            packet=packet,
        )

    assert result == packet
    assert result.data_manifest_sha256 == packet.data_manifest_sha256
    assert discovery.calls == 0
    assert provider.task_types == []
    assert receipts.rows == {}


@pytest.mark.asyncio
async def test_normalizer_fact_outside_the_approved_plan_is_a_hard_failure() -> None:
    class _OutOfPlanCoordinator(ResearchWebFallbackCoordinator):
        async def _normalize(
            self,
            *,
            session: ResearchSessionView,
            source_text: str,
            missing_fact_keys: list[str],
            source_url: str,
            prior_audit_feedback: dict[str, str] | None = None,
        ) -> ResearchWebNormalizationDraft:
            del session, missing_fact_keys, source_url
            return ResearchWebNormalizationDraft(
                schema_version="1.0",
                facts=[
                    ResearchWebNormalizedFactDraft(
                        missing_fact_key=_CUSTOMER_REVENUE_CHAIN,
                        source_excerpt=source_text[:80],
                    )
                ],
            )

    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        coordinator = _coordinator(
            client=client,
            discovery=_Discovery(now=now, candidates=[_candidate(now)]),
            receipts=receipts,
            clock=lambda: now,
            coordinator_type=_OutOfPlanCoordinator,
        )
        with pytest.raises(ValueError, match="outside the approved gap plan"):
            await _complete(coordinator, session=session, packet=packet)

    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FAILED


@pytest.mark.asyncio
async def test_fallback_is_pit_bounded_and_idempotent_across_replays() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert any(fact.key == _CORE_PRODUCTS for fact in result.facts)
    assert _CORE_PRODUCTS not in _gap_reasons(result)


@pytest.mark.asyncio
async def test_fetch_after_task_deadline_is_a_validation_gap() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    deadline = now + timedelta(seconds=2)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: deadline + timedelta(microseconds=1),
            ),
            session=session,
            packet=packet,
            deadline=deadline,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_FACT_VALIDATION_FAILED"
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FAILED


@pytest.mark.asyncio
async def test_customer_revenue_chain_gap_can_be_completed() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CUSTOMER_REVENUE_CHAIN)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _CUSTOMER_REVENUE_CHAIN not in _gap_reasons(result)
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FETCHED


@pytest.mark.asyncio
async def test_product_structure_change_gap_can_be_completed() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _PRODUCT_STRUCTURE_CHANGE)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _PRODUCT_STRUCTURE_CHANGE not in _gap_reasons(result)
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FETCHED


@pytest.mark.asyncio
async def test_second_candidate_is_used_when_the_first_is_blocked() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(
        now=now,
        candidates=[
            _candidate(now, path="blocked/1"),
            _candidate(now, path="disclosure/2"),
            _candidate(now, path="unused/3"),
            _candidate(now, path="unused/4"),
            _candidate(now, path="unused/5"),
        ],
    )
    receipts = _Receipts()
    robots = "User-agent: *\nDisallow: /blocked\nAllow: /"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_page_handler(robots_body=robots))
    ) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _CORE_PRODUCTS not in _gap_reasons(result)
    assert {row.status for row in receipts.rows.values()} == {
        ResearchWebRetrievalReceiptStatus.FAILED,
        ResearchWebRetrievalReceiptStatus.FETCHED,
    }


@pytest.mark.asyncio
async def test_advice_bearing_candidate_is_an_audited_validation_gap() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_advice_page_handler)) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(
                    now=now,
                    candidates=[_candidate(now, path="normalized-advice/1")],
                ),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_AdviceNormalizationCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_FACT_VALIDATION_FAILED"
    assert len(receipts.rows) == 1
    row = next(iter(receipts.rows.values()))
    assert row.status is ResearchWebRetrievalReceiptStatus.FAILED
    assert row.rule_version == "research-real-acquisition.v32"


@pytest.mark.asyncio
async def test_advice_bearing_first_candidate_continues_to_safe_second() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    first_candidate = _candidate(now, path="normalized-advice/1")
    second_candidate = _candidate(now, path="second")
    candidates = [first_candidate, second_candidate]
    async with httpx.AsyncClient(transport=httpx.MockTransport(_advice_page_handler)) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=candidates),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_AdviceNormalizationCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _CORE_PRODUCTS not in _gap_reasons(result)
    new_fact = next(fact for fact in result.facts if fact.key == _CORE_PRODUCTS)
    assert contains_forbidden_investment_advice(new_fact.statement) is False
    statuses = [row.status for row in receipts.rows.values()]
    assert statuses[0] is ResearchWebRetrievalReceiptStatus.FAILED
    assert statuses[1] is ResearchWebRetrievalReceiptStatus.FETCHED
    assert all(
        row.rule_version == "research-real-acquisition.v32" for row in receipts.rows.values()
    )


@pytest.mark.asyncio
async def test_advice_elsewhere_does_not_reject_a_safe_bound_excerpt() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    candidates = [_candidate(now, path="raw-advice/1")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(_advice_page_handler)) as client:
        coordinator = _coordinator(
            client=client,
            discovery=_Discovery(now=now, candidates=candidates),
            receipts=receipts,
            clock=lambda: now,
        )
        result = await _complete(coordinator, session=session, packet=packet)

    assert _CORE_PRODUCTS not in _gap_reasons(result)
    fact = next(item for item in result.facts if item.key == _CORE_PRODUCTS)
    assert contains_forbidden_investment_advice(fact.statement) is False
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FETCHED


@pytest.mark.asyncio
async def test_candidate_domain_must_match_url_host_before_source_selection() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    spoofed = SourceDiscoveryCandidate.model_validate(
        {
            "schema_version": "1.0",
            "url": "https://www.gov.cn/disclosure/1",
            "title": "伪造来源域名",
            "domain": "www.cninfo.com.cn",
            "published_at": now - timedelta(days=2),
            "discovery_score": "0.90",
        }
    )
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[spoofed]),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_SOURCE_ACCESS_BLOCKED"
    receipt = next(iter(receipts.rows.values()))
    assert receipt.status is ResearchWebRetrievalReceiptStatus.REJECTED
    assert receipt.selected_source_id is None
    assert receipt.selected_url_sha256 is None
    assert not result.citations[-1].locator.startswith("https://www.gov.cn/")


@pytest.mark.asyncio
async def test_five_failed_candidates_keep_the_strongest_reason() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(
        now=now,
        candidates=[
            _candidate(now, path="blocked/1"),
            _candidate(now, path="no-date/2", publication_time_missing=True),
            _candidate(now, path="future/3", published_at=now + timedelta(seconds=1)),
            _candidate(now, path="unbound/4"),
            _candidate(now, path="unbound/5"),
        ],
    )
    receipts = _Receipts()
    robots = "User-agent: *\nDisallow: /blocked\nAllow: /"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_page_handler(robots_body=robots))
    ) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_UnboundCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_FACT_VALIDATION_FAILED"
    assert len(receipts.rows) == 5
    assert {row.status for row in receipts.rows.values()} == {
        ResearchWebRetrievalReceiptStatus.REJECTED,
        ResearchWebRetrievalReceiptStatus.FAILED,
    }


@pytest.mark.asyncio
async def test_discovery_no_result_is_audited_and_explained() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(
        now=now,
        candidates=[],
        status=SourceDiscoveryStatus.NO_RESULTS,
    )
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_DISCOVERY_NO_RESULT"
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (SourceDiscoveryStatus.FAILED, "WEB_DISCOVERY_UNAVAILABLE"),
        (SourceDiscoveryStatus.QUOTA_EXHAUSTED, "WEB_DISCOVERY_LIMITED"),
    ],
)
async def test_discovery_status_failures_are_audited_as_failed(
    status: SourceDiscoveryStatus,
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[], status=status),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == expected_reason
    receipt = next(iter(receipts.rows.values()))
    assert receipt.status is ResearchWebRetrievalReceiptStatus.FAILED
    assert receipt.selected_source_id is None
    assert receipt.selected_url_sha256 is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        pytest.param(
            _DiscoveryFailure("TAVILY_QUOTA_EXHAUSTED"),
            "WEB_DISCOVERY_LIMITED",
            id="quota",
        ),
        pytest.param(
            _DiscoveryFailure("TAVILY_AUTHENTICATION_FAILED"),
            "WEB_DISCOVERY_UNAVAILABLE",
            id="auth",
        ),
        pytest.param(
            _DiscoveryFailure("TAVILY_CONFIGURATION_MISSING"),
            "WEB_DISCOVERY_UNAVAILABLE",
            id="config",
        ),
        pytest.param(TimeoutError("deadline"), "WEB_DISCOVERY_UNAVAILABLE", id="timeout"),
    ],
)
async def test_discovery_failures_degrade_to_stable_gaps(
    failure: Exception,
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[], error=failure),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == expected_reason
    receipt = next(iter(receipts.rows.values()))
    assert receipt.status is ResearchWebRetrievalReceiptStatus.FAILED
    assert receipt.selected_source_id is None
    assert receipt.selected_url_sha256 is None
    serialized = receipt.model_dump_json()
    assert "兆易创新 核心业务" not in serialized
    assert "TAVILY" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        (LLMProviderFailureKind.INVALID_OUTPUT, "WEB_QUERY_NOT_PLANNED"),
        (LLMProviderFailureKind.TRANSPORT, "WEB_DISCOVERY_UNAVAILABLE"),
        (LLMProviderFailureKind.AUTH_CONFIG, "WEB_DISCOVERY_UNAVAILABLE"),
    ],
)
async def test_query_plan_model_failures_use_provider_neutral_reasons(
    failure_kind: LLMProviderFailureKind,
    expected_reason: str,
) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
                provider=_FailingPlanProvider(failure_kind),
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == expected_reason
    assert discovery.calls == 0
    assert receipts.rows == {}


@pytest.mark.asyncio
async def test_invalid_normalization_output_is_a_fact_validation_gap() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_InvalidNormalizationCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_FACT_VALIDATION_FAILED"
    assert len(receipts.rows) == 1


@pytest.mark.asyncio
async def test_unavailable_normalizer_stops_candidate_traversal() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    candidates = [_candidate(now, path=f"candidate/{index}") for index in range(5)]
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=candidates),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_UnavailableNormalizationCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_DISCOVERY_UNAVAILABLE"
    assert len(receipts.rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_case", ["rights", "robots", "ssrf"])
async def test_rights_robots_and_ssrf_failures_are_access_gaps(failure_case: str) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    rights = cast(RightsPolicyPort, _DeniedRights()) if failure_case == "rights" else _rights()
    resolver = _private_resolver if failure_case == "ssrf" else _public_resolver
    robots = "User-agent: *\nDisallow: /" if failure_case == "robots" else "User-agent: *\nAllow: /"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_page_handler(robots_body=robots))
    ) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
                rights=rights,
                resolver=resolver,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_SOURCE_ACCESS_BLOCKED"
    assert len(receipts.rows) == 1
    expected_status = (
        ResearchWebRetrievalReceiptStatus.REJECTED
        if failure_case == "rights"
        else ResearchWebRetrievalReceiptStatus.FAILED
    )
    assert next(iter(receipts.rows.values())).status is expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("time_case", "published_at", "last_modified"),
    [
        pytest.param("missing-publication", None, _VALID_LAST_MODIFIED),
        pytest.param(
            "future-publication",
            datetime(2026, 8, 27, 3, 0, 1, tzinfo=UTC),
            _VALID_LAST_MODIFIED,
        ),
        pytest.param("missing-revision", None, None),
        pytest.param(
            "future-revision",
            None,
            "Fri, 28 Aug 2026 00:00:00 GMT",
        ),
    ],
)
async def test_unknown_or_future_source_times_remain_pit_gaps(
    time_case: str,
    published_at: datetime | None,
    last_modified: str | None,
) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    candidate = _candidate(
        now,
        published_at=published_at,
        publication_time_missing=time_case == "missing-publication",
    )
    receipts = _Receipts()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_page_handler(last_modified=last_modified))
    ) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[candidate]),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_PIT_NOT_ELIGIBLE"
    assert len(receipts.rows) == 1


@pytest.mark.asyncio
async def test_unplanned_canonical_gaps_get_query_not_planned_reason() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, *_CANONICAL_BUSINESS_GAPS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    reasons = _gap_reasons(result)
    assert _CORE_PRODUCTS not in reasons
    assert reasons[_CUSTOMER_REVENUE_CHAIN] == "WEB_QUERY_NOT_PLANNED"
    assert reasons[_PRODUCT_STRUCTURE_CHANGE] == "WEB_QUERY_NOT_PLANNED"


@pytest.mark.asyncio
async def test_partial_success_marks_unfilled_planned_gaps_as_validation_failures() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, *_CANONICAL_BUSINESS_GAPS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_AllGapPlanCoordinator,
            ),
            session=session,
            packet=packet,
        )

    reasons = _gap_reasons(result)
    assert _CORE_PRODUCTS not in reasons
    assert reasons[_CUSTOMER_REVENUE_CHAIN] == "WEB_FACT_VALIDATION_FAILED"
    assert reasons[_PRODUCT_STRUCTURE_CHANGE] == "WEB_FACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_filled_gap_is_not_discovered_again_by_a_later_plan_item() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_DuplicateGapPlanCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _CORE_PRODUCTS not in _gap_reasons(result)
    assert discovery.calls == 1
    assert len(receipts.rows) == 1


@pytest.mark.asyncio
async def test_cancellation_during_discovery_is_never_converted_to_a_gap() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    entered = asyncio.Event()
    release = asyncio.Event()
    discovery = _Discovery(
        now=now,
        candidates=[_candidate(now)],
        entered=entered,
        release=release,
    )
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        task = asyncio.create_task(
            _complete(
                _coordinator(
                    client=client,
                    discovery=discovery,
                    receipts=receipts,
                    clock=lambda: now,
                ),
                session=session,
                packet=packet,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    assert receipts.rows == {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        coordinator = _coordinator(
            client=client,
            discovery=discovery,
            receipts=receipts,
            clock=lambda: now,
        )
        replays = [await _complete(coordinator, session=session, packet=packet) for _ in range(10)]

    assert len({result.data_manifest_sha256 for result in replays}) == 1
    assert discovery.calls == 11
    assert len(receipts.rows) == 1
    assert all(any(fact.key == _CORE_PRODUCTS for fact in result.facts) for result in replays)
    assert all(result.web_retrieval_receipt_ids == list(receipts.rows) for result in replays)
    assert all(_CORE_PRODUCTS not in _gap_reasons(result) for result in replays)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["rights", "fetch", "normalization", "receipt-save"])
async def test_cancellation_in_candidate_pipeline_is_never_converted_to_a_gap(
    stage: str,
) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    entered = asyncio.Event()
    release = asyncio.Event()
    stored_receipts: _Receipts = (
        _BlockingReceipts(entered=entered, release=release)
        if stage == "receipt-save"
        else _Receipts()
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        connector: SourceConnectorPort = GovernedWebSourceConnector(
            client=client,
            allowed_domains={"www.cninfo.com.cn"},
            resolver=_public_resolver,
            clock=lambda: now,
        )
        rights: RightsPolicyPort = _rights()
        if stage == "rights":
            rights = cast(
                RightsPolicyPort,
                _BlockingRights(rights, entered=entered, release=release),
            )
        if stage == "fetch":
            connector = cast(
                SourceConnectorPort,
                _BlockingConnector(connector, entered=entered, release=release),
            )
        coordinator: ResearchWebFallbackCoordinator
        coordinator_kwargs = {
            "provider": SyntheticResearchLLMProvider(),
            "discovery": _Discovery(now=now, candidates=[_candidate(now)]),
            "connector": connector,
            "rights": rights,
            "receipts": stored_receipts,
        }
        if stage == "normalization":
            coordinator = _BlockingNormalizationCoordinator(
                entered=entered,
                release=release,
                **coordinator_kwargs,
            )
        else:
            coordinator = ResearchWebFallbackCoordinator(**coordinator_kwargs)  # type: ignore[arg-type]
        task = asyncio.create_task(_complete(coordinator, session=session, packet=packet))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    assert stored_receipts.rows == {}


@pytest.mark.asyncio
async def test_current_first_seen_after_cutoff_before_deadline_advances_as_of() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    first_seen = now + timedelta(seconds=1)
    deadline = now + timedelta(seconds=2)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: first_seen,
            ),
            session=session,
            packet=packet,
            deadline=deadline,
        )

    assert result.as_of == first_seen
    assert result.citations[-1].first_seen_at == first_seen
    assert result.citations[-1].published_at < now
    assert discovery.queries[0].end_date == now
    assert _CORE_PRODUCTS not in _gap_reasons(result)
    assert len(receipts.rows) == 1
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FETCHED


@pytest.mark.asyncio
async def test_unbound_model_fact_degrades_to_an_audited_validation_gap() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_UnboundCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_FACT_VALIDATION_FAILED"
    assert len(receipts.rows) == 1
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_excerpt", "value", "unit"),
    [
        ("收入为100元", "10", "元"),
        ("收入为10, 成本单位为元", "10", "元"),
    ],
)
async def test_numeric_binding_failures_degrade_to_validation_gaps(
    source_excerpt: str,
    value: str,
    unit: str,
) -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)

    class _MisboundCoordinator(ResearchWebFallbackCoordinator):
        async def _normalize(
            self,
            *,
            session: ResearchSessionView,
            source_text: str,
            missing_fact_keys: list[str],
            source_url: str,
            prior_audit_feedback: dict[str, str] | None = None,
        ) -> ResearchWebNormalizationDraft:
            del session, source_text, source_url
            return ResearchWebNormalizationDraft(
                schema_version="1.0",
                facts=[
                    ResearchWebNormalizedFactDraft(
                        missing_fact_key=missing_fact_keys[0],
                        source_excerpt=source_excerpt,
                        value=value,
                        unit=unit,
                    )
                ],
            )

    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=_Discovery(now=now, candidates=[_candidate(now)]),
                receipts=receipts,
                clock=lambda: now,
                coordinator_type=_MisboundCoordinator,
            ),
            session=session,
            packet=packet,
        )

    assert _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_FACT_VALIDATION_FAILED"
    assert len(receipts.rows) == 1
    assert next(iter(receipts.rows.values())).status is ResearchWebRetrievalReceiptStatus.FAILED


@pytest.mark.asyncio
async def test_failed_fetch_is_audited_and_degrades_instead_of_propagating() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    receipts = _Receipts()
    coordinator = ResearchWebFallbackCoordinator(
        provider=SyntheticResearchLLMProvider(),
        discovery=_Discovery(now=now, candidates=[_candidate(now)]),
        connector=cast(SourceConnectorPort, _FailingConnector()),
        rights=_rights(),
        receipts=receipts,
    )

    results = [await _complete(coordinator, session=session, packet=packet) for _ in range(2)]

    assert all(
        _gap_reasons(result)[_CORE_PRODUCTS] == "WEB_SOURCE_ACCESS_BLOCKED" for result in results
    )
    assert len(receipts.rows) == 1
    receipt = next(iter(receipts.rows.values()))
    assert receipt.status is ResearchWebRetrievalReceiptStatus.FAILED
    assert receipt.selected_source_id == "cninfo"
    assert receipt.selected_url_sha256 is not None


@pytest.mark.asyncio
async def test_historical_replay_records_pit_gaps_without_live_discovery() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, *_CANONICAL_BUSINESS_GAPS)
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    provider = _RecordingProvider()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
                provider=provider,
            ),
            session=session,
            packet=packet,
            mode=ResearchCutoffMode.HISTORICAL_REPLAY,
        )

    assert discovery.calls == 0
    assert provider.task_types == []
    assert receipts.rows == {}
    assert all(
        _gap_reasons(result)[key] == "WEB_PIT_NOT_ELIGIBLE" for key in _CANONICAL_BUSINESS_GAPS
    )
    assert result.as_of == now
    assert result.data_manifest_sha256 != packet.data_manifest_sha256


def test_three_canonical_areas_are_distinct() -> None:
    assert len(set(_CANONICAL_BUSINESS_GAPS)) == 3


@pytest.mark.asyncio
async def test_core_business_gap_can_be_completed() -> None:
    now = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session, _CORE_PRODUCTS)
    discovery = _Discovery(now=now, candidates=[_candidate(now)])
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_page_handler())) as client:
        result = await _complete(
            _coordinator(
                client=client,
                discovery=discovery,
                receipts=receipts,
                clock=lambda: now,
            ),
            session=session,
            packet=packet,
        )

    assert _CORE_PRODUCTS not in _gap_reasons(result)
    assert result.data_manifest_sha256 != packet.data_manifest_sha256


@pytest.mark.parametrize(
    "official_status", [SourceDiscoveryStatus.NO_RESULTS, SourceDiscoveryStatus.FAILED]
)
async def test_first_official_index_attempt_falls_back_to_three_directed_searches(
    official_status: SourceDiscoveryStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    monkeypatch.setattr(fallback_module, "REAL_FACT_GAPS", {"identity.company": "公司主体"})
    official = _Discovery(now=now, candidates=[], status=official_status)
    discovery = _Discovery(now=now, candidates=[], status=SourceDiscoveryStatus.NO_RESULTS)
    model = _AcquisitionProvider()
    coordinator = ResearchWebFallbackCoordinator(
        provider=model,
        audit_provider=_AuditProvider(),
        discovery=discovery,
        disclosure_discovery=official,
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    saved: list[ResearchAcquisitionState] = []

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append(state.model_copy(deep=True))

    async def progress(stage: ResearchStage, message: str) -> None:
        pass

    result = await coordinator.collect_real(
        session=session,
        packet=empty_real_evidence_packet(session),
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=now + timedelta(minutes=5),
        state=None,
        save=save,
        progress=progress,
    )
    assert len(official.queries) == 1
    assert set(official.queries[0].include_domains) == {"www.cninfo.com.cn", "static.cninfo.com.cn"}
    assert len(discovery.queries) == 3 and model.calls == 3
    assert saved[-1].attempts["identity.company"] == 4 and saved[-1].complete
    assert not result.facts


@pytest.mark.parametrize("interrupt", [False, True])
async def test_official_anchor_candidates_pass_existing_audit_and_resume_without_rediscovery(
    interrupt: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    monkeypatch.setattr(
        fallback_module,
        "REAL_FACT_GAPS",
        {
            "identity.company": "主体",
            "business.core_products": "主营",
        },
    )
    official = _Discovery(now=now, candidates=[_candidate(now)])
    search = _Discovery(now=now, candidates=[], status=SourceDiscoveryStatus.NO_RESULTS)
    flash, pro = _AcquisitionProvider(), _AuditProvider()
    snapshots: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if interrupt and not interrupted and state.pending_discovery is not None:
            interrupted = True
            raise asyncio.CancelledError

    async def progress(stage: ResearchStage, message: str) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            text=_formal_original("603986 兆易创新 离线合成测试公司主营测试芯片。" * 8),
            headers={"content-type": "text/plain"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        def coordinator() -> ResearchWebFallbackCoordinator:
            return ResearchWebFallbackCoordinator(
                provider=flash,
                audit_provider=pro,
                discovery=search,
                disclosure_discovery=official,
                connector=GovernedWebSourceConnector(
                    client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=_public_resolver
                ),
                rights=_rights(),
                receipts=_Receipts(),
            )

        packet = empty_real_evidence_packet(session)
        state = None
        if interrupt:
            with pytest.raises(asyncio.CancelledError):
                await coordinator().collect_real(
                    session=session,
                    packet=packet,
                    state=state,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                    save=save,
                    progress=progress,
                )
            state, packet = snapshots[-1]
        result = await coordinator().collect_real(
            session=session,
            packet=packet,
            state=state,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            save=save,
            progress=progress,
        )
    assert {x.key for x in result.facts} == {"identity.company", "business.core_products"}
    assert pro.calls == 2 and flash.calls == 2 and len(official.queries) == 1
    assert search.queries == [] and snapshots[-1][0].complete


class _MultipleFactProvider(_AcquisitionProvider):
    def __init__(self, facts: list[ResearchWebNormalizedFactDraft]) -> None:
        super().__init__()
        self.facts = facts

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        response = await super().generate(request)
        return response.model_copy(
            update={
                "payload": {
                    "schema_version": "1.0",
                    "facts": [fact.model_dump(mode="json") for fact in self.facts],
                }
            }
        )


@pytest.mark.parametrize("mixed_keys", [False, True])
@pytest.mark.parametrize("invalid_first_value", [False, True])
async def test_repeated_extracted_keys_audit_only_the_fact_that_can_be_published(
    mixed_keys: bool, invalid_first_value: bool
) -> None:
    now = datetime(2026, 9, 10, tzinfo=UTC)
    keys = (
        [
            "financial.quality",
            "risk.resilience",
            "financial.quality",
            "business.core_products",
            "risk.resilience",
        ]
        if mixed_keys
        else ["financial.quality"] * 5
    )
    facts = [
        ResearchWebNormalizedFactDraft(
            missing_fact_key=key,
            source_excerpt=f"2025年 单位:万元 离线合成指标{index} {100 + index}",
            value=str(999 if invalid_first_value and index == 0 else 100 + index),
            unit="万元",
            period="2025年",
            source_location=f"PDF page {index + 1}",
        )
        for index, key in enumerate(keys)
    ]
    source = "603986 兆易创新\n" + "\n".join(fact.source_excerpt for fact in facts)
    pro = _AuditProvider()
    coordinator = ResearchWebFallbackCoordinator(
        provider=_MultipleFactProvider(facts),
        audit_provider=pro,
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    normalized = await coordinator._normalize(
        session=_session(now),
        source_text=source,
        missing_fact_keys=list(dict.fromkeys(keys)),
        source_url=str(_candidate(now).url),
    )
    candidate = ResearchAcquisitionCandidate(
        candidate=_candidate(now),
        content_sha256="a" * 64,
        first_seen_at=now,
        revision_known_at=now,
        source_context=source,
        facts=normalized.facts,
    )
    audit = await coordinator._audit(
        session=_session(now),
        candidate=candidate,
        official_identity=None,
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
    )
    expected = [
        fact for index, fact in enumerate(facts) if fact.missing_fact_key not in keys[:index]
    ]
    assert normalized.facts == expected
    assert [v.fact_key for v in audit.verdicts] == list(dict.fromkeys(keys))
    assert audit.verdicts[0].outcome == ("rejected" if invalid_first_value else "passed")
    assert audit.verdicts[0].reason_code == (
        "VALUE_MISMATCH" if invalid_first_value else "SUPPORTED"
    )
    assert pro.calls == 1


@pytest.mark.parametrize("invalid_verdicts", ["none", "extra", "duplicate", "missing"])
async def test_audit_scope_excludes_context_and_invalid_verdicts_remain_closed(
    invalid_verdicts: str,
) -> None:
    now = datetime(2026, 9, 10, tzinfo=UTC)

    class ScopedAudit(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            data = json.loads(request.messages[-1].content)
            assert data["expected_fact_keys"] == ["financial.quality"]
            assert data["accepted_facts"]
            assert "context only" in request.messages[0].content
            response = await super().generate(request)
            payload = json.loads(json.dumps(response.payload))
            verdict = payload["verdicts"][0]
            if invalid_verdicts == "extra":
                payload["verdicts"].append({**verdict, "fact_key": "identity.company"})
            elif invalid_verdicts == "duplicate":
                payload["verdicts"].append(verdict.copy())
            elif invalid_verdicts == "missing":
                payload["verdicts"] = []
            return response.model_copy(update={"payload": payload})

    pro = ScopedAudit()
    coordinator = ResearchWebFallbackCoordinator(
        provider=_RecordingProvider(),
        audit_provider=pro,
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    candidate = ResearchAcquisitionCandidate(
        candidate=_candidate(now),
        content_sha256="a" * 64,
        first_seen_at=now,
        revision_known_at=now,
        source_context="603986 兆易创新 2025年 单位:万元 营业收入 100",
        facts=[
            ResearchWebNormalizedFactDraft(
                missing_fact_key="financial.quality",
                source_excerpt="2025年 单位:万元 营业收入 100",
                value="100",
                unit="万元",
                period="2025年",
            )
        ],
    )

    async def audit() -> None:
        result = await coordinator._audit(
            session=_session(now),
            candidate=candidate,
            official_identity=_packet(_session(now)),
            source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
        )
        assert result.verdicts[0].outcome == "passed"

    if invalid_verdicts == "none":
        await audit()
    else:
        with pytest.raises(LLMProviderError, match="MODEL_DATA_AUDIT_INVALID"):
            await audit()
    assert pro.calls == 1


@pytest.mark.parametrize(
    "reason", ["INSUFFICIENT_EVIDENCE", "EXCERPT_MISMATCH", "SUBJECT_MISMATCH", "SOURCE_CONFLICT"]
)
@pytest.mark.parametrize("boundary", ["none", "extracted", "audited", "receipt"])
@pytest.mark.parametrize("corrected", [False, True])
@pytest.mark.parametrize("rediscovered", [True, False])
async def test_rejected_source_gets_one_feedback_correction_in_later_query(
    reason: str,
    boundary: str,
    corrected: bool,
    rediscovered: bool | str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    key = "financial.quality"
    monkeypatch.setattr(
        fallback_module,
        "REAL_FACT_GAPS",
        {"identity.company": "主体", "business.core_products": "主营", key: "财务"},
    )
    table = "2025年 单位:万元 营业收入100 净利润20 经营现金流15; 2024年收入90 净利润18 现金流12。"
    source = "603986 兆易创新 公司主营离线合成芯片。\n" * 5 + table
    unavailable = isinstance(rediscovered, SourceDiscoveryStatus)
    repairable = reason in {"INSUFFICIENT_EVIDENCE", "EXCERPT_MISMATCH"}
    extracts: list[dict[str, object]] = []
    audit_count = 0

    class Discovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            result = await super().discover(query)
            result.request_sha256 = hashlib.sha256(query.query.encode()).hexdigest()
            if query.missing_fact_keys == [key]:
                # Also exercise copied content at different URLs; neither aliases
                # nor a duplicate within one batch may replenish the correction.
                suffix = str(self.counts[key]) if boundary == "audited" else "annual"
                candidate = _candidate(now, path=f"financial.quality/{suffix}")
                result.candidates = [candidate, candidate]
                if rediscovered == "full" and self.counts[key] > 1:
                    result.candidates = [
                        _candidate(now, path=f"unrelated/{index}") for index in range(5)
                    ]
                elif rediscovered is not True and self.counts[key] > 1:
                    result = result.model_copy(
                        update={
                            "status": rediscovered
                            if unavailable
                            else SourceDiscoveryStatus.NO_RESULTS,
                            "candidates": [],
                        }
                    )
            return result

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            if request.task_type == "research_web_evidence_normalization" and data[
                "missing_fact_keys"
            ] == [key]:
                extracts.append(data)
                if len(extracts) == 2:
                    assert reason in data["prior_audit_feedback"][key]
                    assert (
                        "补齐盈利和现金流或选择可定位连续摘录" in data["prior_audit_feedback"][key]
                    )
                excerpt = table if len(extracts) == 2 and corrected else "营业收入100"
                response = response.model_copy(
                    update={
                        "payload": {
                            "schema_version": "1.0",
                            "facts": [
                                {
                                    "missing_fact_key": key,
                                    "source_excerpt": excerpt,
                                    "value": "",
                                    "unit": "",
                                    "period": "2025年",
                                }
                            ],
                        }
                    }
                )
            return response

    class Pro(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            nonlocal audit_count
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            if data["expected_fact_keys"] == [key]:
                audit_count += 1
                if not (audit_count == 2 and corrected):
                    response = response.model_copy(
                        update={
                            "payload": {
                                "schema_version": "1.0",
                                "verdicts": [
                                    {
                                        "fact_key": key,
                                        "outcome": "rejected",
                                        "reason_code": reason,
                                        "reason": "补齐盈利和现金流或选择可定位连续摘录。",
                                    }
                                ],
                            }
                        }
                    )
            return response

    discovery, flash, pro = Discovery(now), Flash(), Pro()
    snapshots: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        candidates = [
            c for c in state.candidates if any(f.missing_fact_key == key for f in c.facts)
        ]
        if (
            boundary == "receipt"
            and repairable
            and not interrupted
            and len(candidates) == 2
            and candidates[-1].verdicts
            and (not corrected or any(f.key == key for f in packet.facts))
        ):
            interrupted = True
            raise asyncio.CancelledError
        if (
            repairable
            and boundary in {"extracted", "audited"}
            and not interrupted
            and len(candidates) == 2
            and bool(candidates[-1].verdicts) == (boundary == "audited")
        ):
            interrupted = True
            raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        pass

    class Receipts(_Receipts):
        async def save(
            self, receipt: ResearchWebRetrievalReceipt
        ) -> ResearchWebRetrievalReceiptWriteResult:
            nonlocal interrupted
            if (
                boundary == "receipt"
                and repairable
                and not interrupted
                and len(extracts) == 2
                and audit_count == 2
            ):
                interrupted = True
                raise asyncio.CancelledError
            return await super().save(receipt)

    receipts = Receipts()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = (
            "Unrelated disclosure without the requested financial table. " * 10
            if ("/unrelated/" in request.url.path)
            else source
        )
        return httpx.Response(
            200, text=_formal_original(body), headers={"content-type": "text/plain"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        def coordinator() -> ResearchWebFallbackCoordinator:
            return ResearchWebFallbackCoordinator(
                provider=flash,
                audit_provider=pro,
                discovery=discovery,
                connector=GovernedWebSourceConnector(
                    client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=_public_resolver
                ),
                rights=_rights(),
                receipts=receipts,
            )

        packet, state = empty_real_evidence_packet(session), None
        if repairable and boundary != "none":
            with pytest.raises(asyncio.CancelledError):
                await coordinator().collect_real(
                    session=session,
                    packet=packet,
                    state=state,
                    save=save,
                    progress=progress,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                )
            state, packet = snapshots[-1]
        result = await coordinator().collect_real(
            session=session,
            packet=packet,
            state=state,
            save=save,
            progress=progress,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
        )
    assert len(extracts) == audit_count == (2 if repairable else 1)
    final = next((f for f in result.facts if f.key == key), None)
    if repairable and corrected:
        assert (
            final is not None and final.statement == table and final.data_audit_status == "passed"
        )
        assert discovery.counts.get(key, 0) == 0
        assert snapshots[-1][0].attempts[key] == 1
    else:
        assert final is None and discovery.counts[key] == (2 if unavailable else 4)
        assert any(g.area == key for g in result.coverage_gaps)
    assert snapshots[-1][0].complete


@pytest.mark.parametrize("boundary", ["none", "extracted", "audited", "receipt"])
async def test_full_discovery_batch_reserves_known_source_correction(
    boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await test_rejected_source_gets_one_feedback_correction_in_later_query(
        reason="INSUFFICIENT_EVIDENCE",
        boundary=boundary,
        corrected=True,
        rediscovered="full",
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("title", ["2025年年度报告", "2025 Annual Report"])
@pytest.mark.parametrize(
    ("search", "boundary", "failure"),
    [
        ("empty", "none", "none"),
        ("full", "none", "none"),
        ("empty", "extracted", "none"),
        ("full", "audited", "none"),
        ("empty", "none", "rights"),
        ("empty", "none", "robots"),
        ("empty", "none", "future"),
        ("empty", "none", "subject"),
        ("empty", "none", "excerpt"),
        ("empty", "none", "audit"),
        ("failed", "none", "none"),
        ("quota", "none", "none"),
    ],
)
async def test_verified_annual_is_available_for_untried_financial_topic(
    title: str,
    search: str,
    boundary: str,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    wide_english: bool = False,
    retained_original: bool = True,
) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return now.astimezone(tz)

    monkeypatch.setattr(fallback_module, "datetime", FixedDatetime)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    financial = "financial.quality"
    if not retained_original or failure in {"rights", "robots", "future", "subject"}:
        # These existing transport rejection cases exercise the fresh-fetch path.
        # Snapshot eligibility and its rejection boundaries are covered separately.
        async def without_local_reuse(self, *, packet, **kwargs):
            return packet

        monkeypatch.setattr(ResearchWebFallbackCoordinator, "_reuse_documents", without_local_reuse)
    monkeypatch.setattr(
        fallback_module,
        "REAL_FACT_GAPS",
        {"identity.company": "主体", _CORE_PRODUCTS: "主营", financial: "财务"},
    )
    table = (
        "Key accounting data 2025 2024; Monetary Unit: Yuan Currency: RMB; "
        "Operating revenue 100 80; Net profit 20 10; "
        "Net cash flows from operating activities 15 12."
    )
    if wide_english:
        table = (
            "VII.   Key Accounting Data and Financial Indicators in the past three years\n"
            "Monetary Unit: Yuan Currency: RMB\n"
            + "Item".ljust(180)
            + "2025 2024\n"
            + "Operating revenue".ljust(180)
            + "100 80\n"
            + "Total profit".ljust(180)
            + "25 15\n"
            + "Net profit attributable to".ljust(180)
            + "20 10\n"
            + "shareholders of the listed company\n"
            + "Net profit excluding non-recurring items".ljust(180)
            + "19 9\n"
            + "Net cash flows from operating".ljust(180)
            + "15 12\n"
            + "activities"
        )
        assert 1000 < len(table) <= 2000
    original = "603986 兆易创新 公司主营离线合成芯片。\n" + "离线合成背景。\n" * 4 + table
    annual = _candidate(now, path="annual").model_copy(update={"title": title})
    current_topic = ""
    extracts: list[str] = []
    financial_audits = 0
    fetched_topics: list[str] = []

    class Discovery(_AcquisitionDiscovery):
        async def discover(self, query: SourceDiscoveryQuery) -> SourceDiscoveryResult:
            nonlocal current_topic
            current_topic = query.missing_fact_keys[0]
            response = await super().discover(query)
            response.request_sha256 = hashlib.sha256(query.query.encode()).hexdigest()
            if current_topic != financial:
                response.candidates = [annual]
            elif search == "full":
                response.candidates = [
                    _candidate(now, path=f"unrelated/{index}") for index in range(5)
                ]
            else:
                response = response.model_copy(
                    update={
                        "candidates": [],
                        "status": {
                            "empty": SourceDiscoveryStatus.NO_RESULTS,
                            "failed": SourceDiscoveryStatus.FAILED,
                            "quota": SourceDiscoveryStatus.QUOTA_EXHAUSTED,
                        }[search],
                    }
                )
            return response

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            if (
                wide_english
                and request.task_type == "research_web_evidence_normalization"
                and data["missing_fact_keys"] != [financial]
            ):
                response.payload["facts"][0]["source_excerpt"] = original.splitlines()[0]
            if request.task_type == "research_web_evidence_normalization" and data[
                "missing_fact_keys"
            ] == [financial]:
                extracts.append(data["source_url"])
                response.payload = {
                    "schema_version": "1.0",
                    "facts": [
                        {
                            "missing_fact_key": financial,
                            "source_excerpt": (
                                "Operating revenue100Net profit20Cash flow15"
                                if wide_english
                                else table.replace("100", "999")
                                if failure == "excerpt"
                                else table
                            ),
                            "source_location": (
                                next(iter(data["financial_source_spans"]), "source_span:99")
                                if wide_english and failure != "excerpt"
                                else ""
                            ),
                            "value": "",
                            "unit": "",
                            "period": "2025",
                        }
                    ],
                }
            return response

    class Pro(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            nonlocal financial_audits
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            if data["expected_fact_keys"] == [financial]:
                financial_audits += 1
                if failure == "audit":
                    response.payload = {
                        "schema_version": "1.0",
                        "verdicts": [
                            {
                                "fact_key": financial,
                                "outcome": "uncertain",
                                "reason_code": "INSUFFICIENT_EVIDENCE",
                                "reason": "需要补齐财务口径。",
                            }
                        ],
                    }
            return response

    class Rights:
        async def authorize(self, check: RightsCheck) -> RightsDecision:
            policy: RightsPolicyPort = (
                _DeniedRights() if current_topic == financial and failure == "rights" else _rights()
            )
            return await policy.authorize(check)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if current_topic == financial and failure == "robots":
                return httpx.Response(200, text="User-agent: *\nDisallow: /")
            return httpx.Response(404)
        fetched_topics.append(current_topic)
        if current_topic == financial and failure == "dns":
            raise GovernedWebSourceError("WEB_DNS_REBINDING_BLOCKED")
        body = original
        if "/unrelated/" in request.url.path or (
            current_topic == financial and failure == "subject"
        ):
            body = "600519 贵州茅台 另一家公司的公开材料。" * 20
        headers = {"content-type": "text/plain"}
        if current_topic == financial and failure == "future":
            headers["last-modified"] = (now + timedelta(days=1)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )
        return httpx.Response(200, text=_formal_original(body), headers=headers)

    snapshots: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        snapshots.append(
            (
                ResearchAcquisitionState.model_validate_json(state.model_dump_json()),
                ResearchEvidencePacket.model_validate_json(packet.model_dump_json()),
            )
        )
        matching = [
            c for c in state.candidates if any(f.missing_fact_key == financial for f in c.facts)
        ]
        if (
            boundary != "none"
            and not interrupted
            and matching
            and bool(matching[-1].verdicts) == (boundary == "audited")
        ):
            interrupted = True
            raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        pass

    flash, pro, discovery = Flash(), Pro(), Discovery(now)
    receipts = _Receipts()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        def coordinator() -> ResearchWebFallbackCoordinator:
            return ResearchWebFallbackCoordinator(
                provider=flash,
                audit_provider=pro,
                discovery=discovery,
                connector=GovernedWebSourceConnector(
                    client=client,
                    allowed_domains={"www.cninfo.com.cn"},
                    resolver=_public_resolver,
                    clock=lambda: now,
                ),
                rights=Rights(),
                receipts=receipts,
            )

        packet, state = empty_real_evidence_packet(session), None
        if boundary != "none":
            with pytest.raises(asyncio.CancelledError):
                await coordinator().collect_real(
                    session=session,
                    packet=packet,
                    state=state,
                    save=save,
                    progress=progress,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                )
            state, packet = snapshots[-1]
        result = await coordinator().collect_real(
            session=session,
            packet=packet,
            state=state,
            save=save,
            progress=progress,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
        )
    final = next((f for f in result.facts if f.key == financial), None)
    if failure == "dns" and not retained_original:
        assert final is None
        assert snapshots[-1][0].provider_calls == 10
        assert fetched_topics == ["identity.company", _CORE_PRODUCTS]
        assert snapshots[-1][0].complete
        return
    if failure in {"none", "dns"}:
        assert (
            final is not None and final.statement == table and final.data_audit_status == "passed"
        )
        assert extracts == [str(annual.url)] and financial_audits == 1
        assert discovery.counts.get(financial, 0) == 0
        assert snapshots[-1][0].provider_calls == 7  # One plan, three extracts and audits.
        citation = next(c for c in result.citations if c.evidence_id == final.evidence_id)
        assert citation.locator == str(annual.url)
        if retained_original:
            assert fetched_topics == ["identity.company"]
            assert citation.first_seen_at == now
    else:
        assert final is None and any(g.area == financial for g in result.coverage_gaps)
        assert len(extracts) <= 2  # Existing single correction, never replenished.
        if failure in {"rights", "robots", "future"} or search in {"failed", "quota"}:
            assert not extracts and financial_audits == 0
        if failure in {"rights", "robots"} or search in {"failed", "quota"}:
            assert financial not in fetched_topics
    assert {"identity.company", _CORE_PRODUCTS}.issubset({f.key for f in result.facts})
    assert snapshots[-1][0].complete
    assert all(len(r.candidate_url_sha256) <= 5 for r in receipts.rows.values())


@pytest.mark.parametrize(
    ("search", "boundary", "failure"),
    [
        ("empty", "none", "none"),
        ("full", "none", "none"),
        ("empty", "extracted", "none"),
        ("full", "audited", "none"),
        ("empty", "none", "excerpt"),
        ("empty", "none", "audit"),
    ],
)
async def test_wide_english_financial_span_reaches_audit_and_checkpoint(
    search: str, boundary: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await test_verified_annual_is_available_for_untried_financial_topic(
        title="2025 Annual Report",
        search=search,
        boundary=boundary,
        failure=failure,
        monkeypatch=monkeypatch,
        wide_english=True,
    )


@pytest.mark.parametrize(
    "status", [SourceDiscoveryStatus.FAILED, SourceDiscoveryStatus.QUOTA_EXHAUSTED]
)
async def test_unavailable_search_does_not_reuse_rejected_source(
    status: SourceDiscoveryStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    await test_rejected_source_gets_one_feedback_correction_in_later_query(
        reason="INSUFFICIENT_EVIDENCE",
        boundary="none",
        corrected=True,
        rediscovered=status,
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("select_span", [False, True])
async def test_one_financial_candidate_is_checkpointed_audited_and_published_after_resume(
    interrupt: bool, select_span: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    monkeypatch.setattr(
        fallback_module,
        "REAL_FACT_GAPS",
        {
            "identity.company": "主体",
            "business.core_products": "主营",
            "financial.quality": "财务",
        },
    )
    financial = [
        ResearchWebNormalizedFactDraft(
            missing_fact_key="financial.quality",
            source_excerpt=f"2025年 单位:万元 离线合成指标{index} {100 + index}",
            value=str(100 + index),
            unit="万元",
            period="2025年",
        )
        for index in range(5)
    ]
    source = "603986 兆易创新 公司主营离线合成芯片。\n" + "\n".join(
        f.source_excerpt for f in financial
    )

    if select_span:
        table = (
            "一、主要会计数据\n单位\uff1a万元\n主要会计数据 2025年 2024年\n"
            "营业收入 100 80\n净利润 20 10\n经营活动现金流量净额 60 70"
        )
        source = "603986 兆易创新 公司主营离线合成芯片\uff0c产品面向汽车与工业客户。\n" + table
        financial = [
            ResearchWebNormalizedFactDraft(
                missing_fact_key="financial.quality",
                source_excerpt="主要会计数据",
                source_location="source_span:0",
                value="100",
                unit="万元",
                period="2025年",
            )
        ]
    expected = (
        financial[0].model_copy(update={"source_excerpt": table}) if select_span else financial[0]
    )

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            if request.task_type == "research_web_evidence_normalization" and (
                data["missing_fact_keys"] == ["financial.quality"]
            ):
                if select_span:
                    assert data["financial_source_spans"] == {"source_span:0": table}
                return response.model_copy(
                    update={
                        "payload": {
                            "schema_version": "1.0",
                            "facts": [f.model_dump(mode="json") for f in financial],
                        }
                    }
                )
            return response

    discovery, flash, pro = _AcquisitionDiscovery(now), Flash(), _AuditProvider()
    snapshots: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if (
            state.candidates
            and state.candidates[-1].facts
            and state.candidates[-1].facts[0].missing_fact_key == "financial.quality"
        ):
            assert state.candidates[-1].facts == [expected]
            if interrupt and not interrupted and not state.candidates[-1].verdicts:
                interrupted = True
                raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text=_formal_original(source), headers={"content-type": "text/plain"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        def coordinator() -> ResearchWebFallbackCoordinator:
            return ResearchWebFallbackCoordinator(
                provider=flash,
                audit_provider=pro,
                discovery=discovery,
                connector=GovernedWebSourceConnector(
                    client=client,
                    allowed_domains={"www.cninfo.com.cn"},
                    resolver=_public_resolver,
                ),
                rights=_rights(),
                receipts=_Receipts(),
            )

        packet, state = empty_real_evidence_packet(session), None
        if interrupt:
            with pytest.raises(asyncio.CancelledError):
                await coordinator().collect_real(
                    session=session,
                    packet=packet,
                    state=state,
                    save=save,
                    progress=progress,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                )
            state, packet = snapshots[-1]
        result = await coordinator().collect_real(
            session=session,
            packet=packet,
            state=state,
            save=save,
            progress=progress,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
        )
    assert len(result.facts) == 3
    final = next(f for f in result.facts if f.key == "financial.quality")
    assert str(final.value) == "100" and final.statement == expected.source_excerpt
    assert final.data_audit_status == "passed"
    assert pro.calls == 3 and flash.calls == 4
    assert set(discovery.counts.values()) == {1}
    assert snapshots[-1][0].complete


@pytest.mark.parametrize(
    ("key", "heading"),
    [
        ("competitive.position", "三、 核心竞争力分析"),
        ("risk.resilience", "四、 风险因素"),
        ("financial.quality", "一、 主要会计数据"),
        (
            _PRODUCT_STRUCTURE_CHANGE,
            "一、报告期内公司所从事的主要业务、经营模式、行业情况说明",
        ),
    ],
)
def test_topic_context_includes_late_body_with_identity_and_original_page_marker(
    key: str, heading: str
) -> None:
    head = "[PDF page 1]\n离线测试公司 603986 2025年年度报告\n"
    pointer = f"请查阅第三节之{heading}中的内容。\n"
    source = (
        head
        + pointer
        + "无关背景。" * 10000
        + f"\n[PDF page 38]\n{heading}\n真实主题正文。"
        + "正文。" * 9000
    )
    context = _selected_test_context(source, [key])
    assert len(context) <= 40000
    assert "离线测试公司 603986" in context
    assert "[PDF page 38]" in context and "真实主题正文。" in context
    assert "正文。" * 10 in context
    assert context != source[:40000]


def test_business_context_prefers_full_business_heading_over_later_results() -> None:
    heading = "一、报告期内公司所从事的主要业务、经营模式、行业情况说明"
    source = (
        "[PDF page 1]\n603986 离线公司年度报告\n"
        + heading
        + "........12\n"
        + "详见本报告第三节之"
        + heading
        + "。\n"
        + "其他内容。" * 9000
        + "\n[PDF page 12]\n"
        + heading
        + "\n报告期内新增测试元件业务。\n"
        + "业务正文。" * 5000
        + "\n[PDF page 41]\n五、报告期内主要经营情况\n成本结构变动。"
    )
    context = _selected_test_context(source, [_PRODUCT_STRUCTURE_CHANGE])
    assert len(context) <= 40000
    assert "[PDF page 12]" in context and "报告期内新增测试元件业务。" in context
    assert "成本结构变动。" not in context


def test_topic_context_leaves_short_and_unmatched_documents_unchanged() -> None:
    short = "[PDF page 1]\n603986 离线公司\n请查阅风险因素。"
    assert _selected_test_context(short, ["risk.resilience"]) == short
    long = short + "正文。" * 15000
    assert _selected_test_context(long, ["unknown.key"]) == ""
    assert _selected_test_context(long, ["risk.resilience"]) == ""


@pytest.mark.parametrize(
    ("key", "heading", "excerpt"),
    [
        (
            "risk.resilience",
            "四、 风险因素",
            "公司原材料依赖单一供货商\uff0c供应中断可能影响交付。",
        ),
        (
            _PRODUCT_STRUCTURE_CHANGE,
            "三、 报告期内公司从事的主要业务",
            "报告期内公司收购测试元件企业\uff0c\n将主营产品扩展至测试光学元器件。",
        ),
        (
            _PRODUCT_STRUCTURE_CHANGE,
            "一、报告期内公司所从事的主要业务、经营模式、行业情况说明",
            "报告期内公司收购测试元件企业\uff0c\n将主营产品扩展至测试光学元器件。",
        ),
    ],
)
@pytest.mark.parametrize("duplicate_excerpt", [False, True])
@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("audit_passes", [False, True])
async def test_real_operating_fact_reaches_extraction_audit_and_checkpoint_resume(
    key: str,
    heading: str,
    excerpt: str,
    interrupt: bool,
    audit_passes: bool,
    duplicate_excerpt: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    gaps = {
        area: detail
        for area, detail in fallback_module.REAL_FACT_GAPS.items()
        if area in {"identity.company", "business.core_products", key}
    }
    monkeypatch.setattr(fallback_module, "REAL_FACT_GAPS", gaps)
    source = (
        "[PDF page 1]\n603986 兆易创新 公司主营离线合成芯片。\n请查阅风险因素。\n"
        + "无关背景。" * 10000
        + f"\n[PDF page 38]\n{heading}\n"
        + excerpt
    )
    if duplicate_excerpt:
        source += "\n[PDF page 50]\n" + excerpt.replace("\n", " ")

    async def fixed_pdf_text(content: bytes) -> str:
        assert content == b"%PDF-fixed-offline-pages"
        return _formal_original(source)

    monkeypatch.setattr(governed_web_module, "_pdf_text", fixed_pdf_text)
    seen_contexts: list[str] = []

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            if request.task_type == "research_web_evidence_normalization" and data[
                "missing_fact_keys"
            ] == [key]:
                assert data["gap_requirements"] == {key: gaps[key]}
                if key == "risk.resilience":
                    assert "具体风险敞口及其条件性影响" in gaps[key]
                    assert "不代表整体风险承受能力评价" in gaps[key]
                else:
                    assert "报告期内" in gaps[key] and "产品" in gaps[key]
                assert excerpt in data["source_text"] and len(data["source_text"]) <= 40000
                seen_contexts.append(data["source_text"])
                return response.model_copy(
                    update={
                        "payload": {
                            "schema_version": "1.0",
                            "facts": [
                                {
                                    "missing_fact_key": key,
                                    "source_excerpt": excerpt.replace("\n", ""),
                                    "source_location": "PDF page 38",
                                }
                            ],
                        }
                    }
                )
            if request.task_type == "research_web_evidence_normalization":
                response.payload = {
                    "schema_version": "1.0",
                    "facts": [
                        {
                            "missing_fact_key": data["missing_fact_keys"][0],
                            "source_excerpt": "603986 兆易创新 公司主营离线合成芯片。",
                        }
                    ],
                }
            return response

    class Pro(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            data = json.loads(request.messages[-1].content)
            self.outcome = "passed"
            if data["expected_fact_keys"] == [key]:
                assert data["gap_requirements"] == {key: gaps[key]}
                if key == "risk.resilience":
                    assert "具体风险敞口及其条件性影响" in gaps[key]
                    assert "不代表整体风险承受能力评价" in gaps[key]
                else:
                    assert "报告期内" in gaps[key] and "产品" in gaps[key]
                assert data["source_text"] == seen_contexts[0]
                assert data["candidate_facts"][0]["source_excerpt"] == excerpt
                self.outcome = "passed" if audit_passes else "rejected"
            return await super().generate(request)

    discovery, flash, pro = _AcquisitionDiscovery(now), Flash(), Pro()
    snapshots: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal interrupted
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if (
            state.candidates
            and state.candidates[-1].facts
            and state.candidates[-1].facts[0].missing_fact_key == key
        ):
            assert _stored_source_context(state, state.candidates[-1]) == seen_contexts[0]
            if interrupt and not interrupted and not state.candidates[-1].verdicts:
                interrupted = True
                raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(
                200,
                content=b"%PDF-fixed-offline-pages",
                headers={"content-type": "application/pdf"},
            )
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        def coordinator() -> ResearchWebFallbackCoordinator:
            return ResearchWebFallbackCoordinator(
                provider=flash,
                audit_provider=pro,
                discovery=discovery,
                connector=GovernedWebSourceConnector(
                    client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=_public_resolver
                ),
                rights=_rights(),
                receipts=_Receipts(),
            )

        packet, state = empty_real_evidence_packet(session), None
        if interrupt:
            with pytest.raises(asyncio.CancelledError):
                await coordinator().collect_real(
                    session=session,
                    packet=packet,
                    state=state,
                    save=save,
                    progress=progress,
                    cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                    task_deadline_at=now + timedelta(minutes=5),
                )
            state, packet = snapshots[-1]
        result = await coordinator().collect_real(
            session=session,
            packet=packet,
            state=state,
            save=save,
            progress=progress,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
        )
    if not audit_passes:
        assert key not in {fact.key for fact in result.facts}
        assert key in _gap_reasons(result)
        assert snapshots[-1][0].attempts[key] == 4
        assert snapshots[-1][0].complete
        return
    fact = next(f for f in result.facts if f.key == key)
    assert fact.statement == excerpt and "PDF page 38" in fact.source_location
    assert len(seen_contexts) == 1 and pro.calls == 3 and flash.calls == 4
    assert set(discovery.counts.values()) == {1}


@pytest.mark.parametrize(
    ("key", "excerpt", "passed"),
    [
        (
            "risk.resilience",
            "公司已在本报告中详细阐述可能面临的风险\uff0c敬请查阅第三节风险因素中的内容。",
            False,
        ),
        ("valuation.context", "公司拟向股东派发现金红利\uff0c分配方案尚待股东会审议。", False),
        ("valuation.context", "基本每股收益1.23元\uff0c净资产收益率8.76%。", False),
        ("valuation.context", "每股收益同比提高\uff0c净资产收益率有所改善。", False),
        ("valuation.context", "利润分配每股0.30元\uff0c净资产收益率8.76%。", False),
        ("valuation.context", "本期股息率3.2%\uff0c与同期行业口径比较。", True),
        ("valuation.context", "P/E (TTM) 18.2; historical median 22.4.", True),
        ("valuation.context", "P/B 1.4; historical median 1.8.", True),
        ("valuation.context", "2025-12-31 EV/FCF 12.0x; historical median EV/FCF 16.0x.", True),
        (
            "valuation.context",
            "2025年末总市值180亿元/同期归母净利润10亿元=18倍\uff1b同口径历史中位数22倍。",
            True,
        ),
        ("valuation.context", "股价36元除以每股净资产12元为3倍\uff0c历史同口径中位数2.8倍。", True),
        ("valuation.context", "本期股价36元。每股收益1.23元。", False),
        ("financial.quality", "研发投入占营业收入的比例\uff08%\uff09 12.00 10.00", False),
        (
            "financial.quality",
            "研发投入占营业收入的比例为12%;同期营业收入与经营现金流均有所增长。",
            True,
        ),
        ("risk.resilience", "公司依赖单一原材料供货商\uff0c供应中断可能影响交付。", True),
        ("valuation.context", "当前估值市盈率的可比口径为同一报告期的行业公司。", True),
    ],
)
@pytest.mark.parametrize("audit_outcome", ["passed", "rejected", "uncertain"])
async def test_nonresponsive_gap_quote_preserves_program_and_independent_audit(
    key: str, excerpt: str, passed: bool, audit_outcome: str
) -> None:
    now = datetime.now(UTC)
    candidate = ResearchAcquisitionCandidate(
        candidate=_candidate(now),
        content_sha256="a" * 64,
        first_seen_at=now,
        revision_known_at=now,
        source_context="603986 兆易创新 2025年\n" + excerpt,
        facts=[ResearchWebNormalizedFactDraft(missing_fact_key=key, source_excerpt=excerpt)],
    )
    coordinator = ResearchWebFallbackCoordinator(
        provider=_AcquisitionProvider(),
        audit_provider=_AuditProvider(outcome=audit_outcome),
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    audit = await coordinator._audit(
        session=_session(now),
        candidate=candidate,
        official_identity=None,
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
    )
    assert (audit.verdicts[0].outcome == "passed") is (passed and audit_outcome == "passed")
    if audit_outcome != "passed":
        assert audit.verdicts[0].outcome == audit_outcome
        assert audit.verdicts[0].reason_code == "SUBJECT_MISMATCH"
    elif not passed:
        assert audit.verdicts[0].reason_code == "INSUFFICIENT_EVIDENCE"


def test_multiple_topic_windows_keep_late_headings_without_duplicate_source_spans() -> None:
    source = "[PDF page 1]\n603986 离线公司\n" + "开头背景。" * 9000
    source += "\n[PDF page 30]\n三、 核心竞争力分析\n独有工艺支持快速交付。" + "中段背景。" * 2000
    source += "\n[PDF page 60]\n四、 风险因素\n单一供货商构成供应中断风险。"
    context = _selected_test_context(source, ["competitive.position", "risk.resilience"])
    assert len(context) <= 40000
    assert "独有工艺支持快速交付。" in context
    assert "单一供货商构成供应中断风险。" in context
    assert context.count("[PDF page 30]") == context.count("[PDF page 60]") == 1
    nearby = source.replace("中段背景。" * 2000, "短背景。")
    merged = _selected_test_context(nearby, ["competitive.position", "risk.resilience"])
    assert merged.count("独有工艺支持快速交付。") == 1
    assert merged.count("单一供货商构成供应中断风险。") == 1


async def test_multi_gap_candidate_retains_both_topic_windows_across_checkpoint_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    keys = ["competitive.position", "risk.resilience"]
    seeded = _packet(session, *keys)
    anchor_keys = {"identity.company", "business.core_products"}
    seeded = seeded.model_copy(
        update={
            "facts": [
                seeded.facts[0].model_copy(update={"key": key}) for key in sorted(anchor_keys)
            ]
        }
    )
    monkeypatch.setattr(fallback_module, "REAL_FACT_GAPS", {key: key for key in keys})
    excerpts = ["专有工艺支持公司快速交付。", "单一供货商可能导致供应中断。"]
    source = "[PDF page 1]\n603986 兆易创新\n" + "开头背景。" * 9000
    source += "\n[PDF page 30]\n三、 核心竞争力分析\n" + excerpts[0] + "\n" + "中段背景。" * 2000
    source += "\n[PDF page 60]\n四、 风险因素\n" + excerpts[1]
    captured_context: list[str] = []

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            if request.task_type == "research_web_gap_plan":
                response.payload = {
                    "schema_version": "1.0",
                    "queries": [{"query": "离线公司竞争及风险披露", "missing_fact_keys": keys}],
                }
            else:
                data = json.loads(request.messages[-1].content)
                assert all(excerpt in data["source_text"] for excerpt in excerpts)
                captured_context.append(data["source_text"])
                response.payload = {
                    "schema_version": "1.0",
                    "facts": [
                        {"missing_fact_key": key, "source_excerpt": excerpt}
                        for key, excerpt in zip(keys, excerpts, strict=True)
                    ],
                }
            return response

    class Pro(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            data = json.loads(request.messages[-1].content)
            assert data["source_text"] == captured_context[0]
            assert data["expected_fact_keys"] == keys
            return await super().generate(request)

    flash, pro, discovery = Flash(), Pro(), _AcquisitionDiscovery(now)
    snapshots: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []
    bootstrap = True
    interrupted = False

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        nonlocal bootstrap, interrupted
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))
        if bootstrap:
            bootstrap = False
            raise asyncio.CancelledError
        if (
            state.candidates
            and state.candidates[-1].facts
            and not state.candidates[-1].verdicts
            and not interrupted
        ):
            interrupted = True
            assert all(state.attempted_contents[key] for key in keys)
            assert all(
                excerpt in _stored_source_context(state, state.candidates[-1])
                for excerpt in excerpts
            )
            raise asyncio.CancelledError

    async def progress(_stage: ResearchStage, _message: str) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(
                200,
                content=b"%PDF-fixed-offline-pages",
                headers={"content-type": "application/pdf"},
            )
        )

    async def parsed_pdf(_data: bytes) -> str:
        return _formal_original(source)

    monkeypatch.setattr(governed_web_module, "_pdf_text", parsed_pdf)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        def coordinator() -> ResearchWebFallbackCoordinator:
            return ResearchWebFallbackCoordinator(
                provider=flash,
                audit_provider=pro,
                discovery=discovery,
                connector=GovernedWebSourceConnector(
                    client=client, allowed_domains={"www.cninfo.com.cn"}, resolver=_public_resolver
                ),
                rights=_rights(),
                receipts=_Receipts(),
            )

        first = coordinator()
        with pytest.raises(asyncio.CancelledError):
            await first.collect_real(
                session=session,
                packet=seeded,
                state=None,
                save=save,
                progress=progress,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=5),
            )
        state, packet = snapshots[-1]
        with pytest.raises(asyncio.CancelledError):
            await first.complete(
                session=session,
                packet=packet,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(minutes=5),
            )
        state, packet = snapshots[-1]
        result = await coordinator().collect_real(
            session=session,
            packet=packet,
            state=state,
            save=save,
            progress=progress,
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
        )
    assert {fact.key for fact in result.facts} == set(keys) | anchor_keys
    assert len(captured_context) == 1 and flash.calls == 2 and pro.calls == 1
    assert sum(discovery.counts.values()) == 1 and snapshots[-1][0].complete
    assert all("PDF page" in fact.source_location for fact in result.facts if fact.key in keys)


@pytest.mark.parametrize("duration", [75.0, 121.0])
async def test_real_acquisition_slow_pro_audit_uses_bounded_pro_budget(
    duration: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    monkeypatch.setattr(fallback_module, "REAL_FACT_GAPS", {"identity.company": "公司主体"})
    elapsed = [0.0]
    calls: list[DeepSeekTransportRequest] = []

    class SlowAuditTransport:
        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            calls.append(request)
            elapsed[0] += min(duration, request.timeout_seconds)
            if duration > request.timeout_seconds:
                raise TimeoutError("offline slow audit")
            return DeepSeekTransportResponse(
                content=json.dumps(
                    {
                        "schema_version": "1.0",
                        "verdicts": [
                            {
                                "fact_key": "identity.company",
                                "outcome": "passed",
                                "reason_code": "SUPPORTED",
                                "reason": "原文支持公司主体。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                finish_reason="stop",
                input_tokens=20,
                output_tokens=10,
                raw_response_hash="a" * 64,
            )

    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    async def progress(stage: ResearchStage, message: str) -> None:
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(
            200,
            text=_formal_original("603986 兆易创新, 离线合成公司主体资料。" * 10),
            headers={
                "content-type": "text/plain; charset=utf-8",
                "last-modified": _VALID_LAST_MODIFIED,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=_AcquisitionProvider(),
            audit_provider=DeepSeekLLMProvider(
                model=DEEPSEEK_PRO_MODEL,
                transport=SlowAuditTransport(),
                clock=lambda: elapsed[0],
            ),
            discovery=_Discovery(now=now, candidates=[_candidate(now)]),
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=_Receipts(),
        )
        operation = coordinator.collect_real(
            session=session,
            packet=empty_real_evidence_packet(session),
            cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=now + timedelta(minutes=5),
            state=None,
            save=save,
            progress=progress,
        )
        if duration < 120:
            result = await operation
            assert [fact.key for fact in result.facts] == ["identity.company"]
            assert result.facts[0].data_audit_status == "passed"
            assert saved[-1][0].complete
        else:
            with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TIMEOUT"):
                await operation
            assert not saved[-1][0].complete and not saved[-1][1].facts
            assert saved[-1][0].candidates and not saved[-1][0].candidates[0].verdicts
    assert len(calls) == 1
    assert calls[0].timeout_seconds == 120
    assert calls[0].thinking_enabled
    assert elapsed[0] == min(duration, 120)


@pytest.mark.parametrize("interrupt", ["deadline", "cancel"])
async def test_real_slow_audit_still_obeys_outer_deadline_and_cancel(
    interrupt: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    session = _session(now).model_copy(
        update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
    )
    monkeypatch.setattr(fallback_module, "REAL_FACT_GAPS", {"identity.company": "公司主体"})
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class HangingAuditor(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

    auditor = HangingAuditor()
    saved: list[tuple[ResearchAcquisitionState, ResearchEvidencePacket]] = []

    async def save(state: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    async def progress(stage: ResearchStage, message: str) -> None:
        pass

    def identified_page(request: httpx.Request) -> httpx.Response:
        response = _page_handler()(request)
        if request.url.path != "/robots.txt":
            return httpx.Response(
                200,
                text=_formal_original("603986 兆易创新 " + response.text),
                headers=response.headers,
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(identified_page)) as client:
        coordinator = ResearchWebFallbackCoordinator(
            provider=_AcquisitionProvider(),
            audit_provider=auditor,
            discovery=_Discovery(now=now, candidates=[_candidate(now)]),
            connector=GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn"},
                resolver=_public_resolver,
                clock=lambda: now,
            ),
            rights=_rights(),
            receipts=_Receipts(),
        )
        task = asyncio.create_task(
            coordinator.collect_real(
                session=session,
                packet=empty_real_evidence_packet(session),
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=now + timedelta(seconds=0.5 if interrupt == "deadline" else 30),
                state=None,
                save=save,
                progress=progress,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        if interrupt == "cancel":
            task.cancel()
        with pytest.raises(TimeoutError if interrupt == "deadline" else asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    assert cancelled.is_set() and auditor.calls == 1
    assert saved[-1][0].candidates and not saved[-1][0].candidates[0].verdicts
    assert not saved[-1][1].facts and not saved[-1][0].complete


@pytest.mark.parametrize(
    ("locator", "key", "value", "unit", "period", "outcome", "selected", "reason"),
    [
        (
            "source_span:0",
            "financial.quality",
            "100",
            "万元",
            "2025年",
            "passed",
            True,
            "SUPPORTED",
        ),
        (
            "source_span:0",
            "financial.quality",
            "100",
            "元",
            "2025年",
            "passed",
            True,
            "VALUE_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "100",
            "万元",
            "25",
            "passed",
            True,
            "PERIOD_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "100",
            "万元",
            "2025",
            "passed",
            True,
            "PERIOD_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "101",
            "万元",
            "2025年",
            "passed",
            True,
            "VALUE_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "100",
            "亿元",
            "2025年",
            "passed",
            True,
            "VALUE_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "100",
            "万元",
            "2023年",
            "passed",
            True,
            "PERIOD_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "",
            "万元",
            "2025年",
            "passed",
            True,
            "VALUE_MISMATCH",
        ),
        (
            "source_span:0",
            "financial.quality",
            "100",
            "万元",
            "2025年",
            "rejected",
            True,
            "SUBJECT_MISMATCH",
        ),
        (
            "source_span:99",
            "financial.quality",
            "100",
            "万元",
            "2025年",
            "passed",
            False,
            "EXCERPT_MISMATCH",
        ),
        (
            "source_span:-1",
            "financial.quality",
            "100",
            "万元",
            "2025年",
            "passed",
            False,
            "EXCERPT_MISMATCH",
        ),
        (
            "PDF page 8",
            "financial.quality",
            "100",
            "万元",
            "2025年",
            "passed",
            False,
            "EXCERPT_MISMATCH",
        ),
        (
            "source_span:0",
            "business.core_products",
            "100",
            "万元",
            "2025年",
            "passed",
            False,
            "EXCERPT_MISMATCH",
        ),
    ],
)
@pytest.mark.parametrize("heading", ["(一)主要会计数据", "五、主要会计数据和财务指标"])
async def test_financial_span_selection_preserves_independent_and_numeric_audit(
    locator: str,
    key: str,
    value: str,
    unit: str,
    period: str,
    outcome: str,
    selected: bool,
    reason: str,
    heading: str,
) -> None:
    now = datetime.now(UTC)
    table = (
        f"{heading}\n单位\uff1a万元\n主要会计数据 2025年 2024年\n"
        "营业收入 100 80\n净利润 20 10\n经营活动现金流量净额 60 70"
    )
    source = "603986 兆易创新 离线合成公司。附录另有2023年记录。\n[PDF page 8]\n" + table
    transcription = "营业收入100净利润20现金流60"

    class Flash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            data = json.loads(request.messages[-1].content)
            assert data["source_text"] == source
            assert data["financial_source_spans"] == (
                {"source_span:0": table} if key == "financial.quality" else {}
            )
            response.payload = {
                "schema_version": "1.0",
                "facts": [
                    {
                        "missing_fact_key": key,
                        "source_excerpt": transcription,
                        "source_location": locator,
                        "value": value,
                        "unit": unit,
                        "period": period,
                    }
                ],
            }
            return response

    class Pro(_AuditProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            data = json.loads(request.messages[-1].content)
            assert data["source_text"] == source
            assert data["candidate_facts"][0]["source_excerpt"] == (
                table if selected else transcription
            )
            return await super().generate(request)

    flash, pro = Flash(), Pro(outcome=outcome)
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=_Discovery(now=now, candidates=[]),
        connector=_FailingConnector(),
        rights=_DeniedRights(),
        receipts=_Receipts(),
    )
    normalized = await coordinator._normalize(
        session=_session(now),
        source_text=source,
        missing_fact_keys=[key],
        source_url=str(_candidate(now).url),
    )
    fact = normalized.facts[0]
    assert fact.source_excerpt == (table if selected else transcription)
    assert (fact.value, fact.unit, fact.period) == (value, unit, period)
    verdict = await coordinator._audit(
        session=_session(now),
        candidate=ResearchAcquisitionCandidate(
            candidate=_candidate(now),
            content_sha256="a" * 64,
            first_seen_at=now,
            revision_known_at=now,
            source_context=source,
            facts=normalized.facts,
        ),
        official_identity=None,
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
    )
    assert verdict.verdicts[0].reason_code == reason
    assert flash.calls == pro.calls == 1


def test_financial_span_catalog_bounds_and_discontiguous_context() -> None:
    separator = "\n[Intervening source text omitted; original PDF page numbers retained]\n"
    first = "一、主要会计数据\n单位\uff1a万元\n2025年 营业收入 100\n"
    second = "二、主要财务指标\n2025年 2024年\n净利润 20 10\n"
    source = (
        "目录\uff1a主要会计数据......8\n请参阅主要财务指标。\n"
        + first
        + separator
        + second
        + "超长行" * 1000
    )
    spans = fallback_module._financial_source_spans(source)
    assert spans == {"source_span:0": first.rstrip("\n"), "source_span:1": second.rstrip("\n")}
    assert all(
        text in source and separator not in text and len(text) <= 2000 for text in spans.values()
    )
    many = (first + "行内数据\n" * 200) * 10
    spans = fallback_module._financial_source_spans(many)
    assert 1 <= len(spans) <= 8 and all(len(text) <= 2000 for text in spans.values())
    assert sum(len(text) for text in spans.values()) <= 8000
    assert all(text.endswith("行内数据") and text in many for text in spans.values())
    short = (first + separator) * 10
    assert len(fallback_module._financial_source_spans(short)) == 8
    assert fallback_module._financial_source_spans("请参阅主要会计数据。") == {}


@pytest.mark.parametrize(
    "heading",
    ["五、主要会计数据和财务指标", "主要会计数据和财务指标"],
)
def test_combined_financial_heading_preserves_thousand_unit_and_identity(heading: str) -> None:
    table = (
        heading
        + "\n单位\uff1a千元\n项目 2025年 2024年 本年比上年增减 2023年\n"
        + "营业收入 120000 100000 20% 90000\n"
        + "归属于上市公司股东的净利润 20000 18000 11.11% 15000\n"
        + "经营活动产生的现金流量净额 30000 25000 20% 20000"
    )
    source = (
        "cover\n" * 1200
        + "[PDF page 10]\n300001 测试公司\n"
        + "background\n" * 400
        + "[PDF page 11]\n"
        + table
        + "\n第九节 附录\n"
        + "appendix\n" * 4000
    )
    assert fallback_module._financial_source_spans(table) == {"source_span:0": table}
    selected = _selected_test_context(source, ["financial.quality"])
    assert selected != source[:40000] and "300001 测试公司" in selected
    assert len(selected) <= 40000
    spans = fallback_module._financial_source_spans(selected)
    assert spans["source_span:0"].startswith(table)
    assert spans["source_span:0"] in selected
    assert fallback_module._financial_source_spans("请参阅" + heading + "。") == {}
    assert fallback_module._financial_source_spans(heading + " ...... 11") == {}


@pytest.mark.parametrize(
    "heading",
    [
        "VII. Key Accounting Data and Financial Indicators in the past three years",
        "(I)  Key    accounting      data",
        "(II) Key financial indicators",
        "KeyAccountingData",
        "iii. KEY ACCOUNTING DATA",
    ],
)
def test_english_financial_headings_preserve_original_text_and_identity_context(
    heading: str,
) -> None:
    table = heading + "\nMonetary Unit: Yuan Currency: RMB\n2025 2024\nRevenue 100 80"
    source = (
        "cover\n" * 1200
        + "[PDF page 5]\n603986 兆易创新\n"
        + "background\n" * 400
        + "[PDF page 6]\n"
        + table
        + "\n第九节 附录\n"
        + "appendix\n" * 4000
    )
    expected = {"source_span:0": table}
    assert fallback_module._financial_source_spans(table) == expected
    selected = _selected_test_context(source, ["financial.quality"])
    assert selected != source[:40000] and "603986 兆易创新" in selected
    assert len(selected) <= 40000
    assert table in selected
    assert fallback_module._financial_source_spans("See " + heading + ".") == {}
    assert fallback_module._financial_source_spans(heading + " ...... 27") == {}


@pytest.mark.parametrize(
    ("source", "unit", "expected"),
    [
        ("单位:元/股 每股收益100", "元", False),
        ("单位:元/股 每股收益100", "股", False),
        ("单位:元/股 每股收益100", "元/股", True),
        ("单位:万元 营业收入100", "元", False),
        ("单位:百万元 营业收入100", "万元", False),
        ("单位:亿元 营业收入100", "元", False),
        ("单位:万元 营业收入100", "万元", True),
        ("单位:元 营业收入100", "元", True),
        ("单位:人民币元 营业收入100", "元", False),
        ("单位:人民币元 营业收入100", "人民币元", True),
        ("营业收入100万元,同比增加。", "万元", True),
        ("营业收入100万元,同比增加。", "元", False),
    ],
)
def test_exact_financial_unit_token(source: str, unit: str, expected: bool) -> None:
    assert (
        fallback_module._excerpt_is_bound(
            source_text=source,
            excerpt=source,
            value="100",
            unit=unit,
            audited_table_context=True,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("excerpt", "period", "expected"),
    [
        ("截至2025年 12月31日,余额100", "2025年", False),
        ("截至2025 年 12 月 31 日,余额100", "2025 年", False),
        ("截至2025年 12月31日,余额100", "12月31日", False),
        ("截至2025年12月 31日,余额100", "2025年12月", False),
        ("截至2025年 12月31日,余额100", "2025年 12月31日", True),
        ("截至2025 年 12 月 31 日,余额100", "2025 年 12 月 31 日", True),
        ("截至2025年12月 31日,余额100", "2025年12月 31日", True),
        ("2025年 第一季度 营业收入100", "2025年", False),
        ("2025年 第一季度 营业收入100", "2025年 第一季度", True),
        ("2025年 2024年 营业收入100 80", "2025年", True),
        ("2025年 2024年 营业收入100 80", "2024年", True),
        ("截至2025-12-31,余额100", "2025", False),
        ("截至2025/12/31,余额100", "2025", False),
        ("截至2025.12.31,余额100", "2025", False),
        ("截至2025 - 12 - 31,余额100", "2025", False),
        ("截至2025-12-31,余额100", "12-31", False),
        ("截至2025 / 12 / 31,余额100", "12", False),
        ("截至2025-12-31,余额100", "2025-12-31", True),
        ("截至2025/12/31,余额100", "2025/12/31", True),
        ("截至2025.12.31,余额100", "2025.12.31", True),
        ("截至2025 - 12 - 31,余额100", "2025 - 12 - 31", True),
        ("2025财年 营业收入100", "2025", False),
        ("2025会计年度 营业收入100", "2025", False),
        ("2025报告期 营业收入100", "2025", False),
        ("2025财年 营业收入100", "2025财年", True),
        ("2025会计年度 营业收入100", "2025会计年度", True),
        ("2025报告期 营业收入100", "2025报告期", True),
        ("2025会计年度 营业收入100", "2025会", False),
        ("2025报告期 营业收入100", "2025报告", False),
        ("2025 Q1 营业收入100", "2025", False),
        ("2025 H1 营业收入100", "2025", False),
        ("2025年 1\u20146月 营业收入100", "2025年", False),
        ("2025 Q1 营业收入100", "2025 Q1", True),
        ("2025 H1 营业收入100", "2025 H1", True),
        ("2025年 1\u20146月 营业收入100", "2025年 1\u20146月", True),
        ("2024\u20142025年度 营业收入100", "2025年度", False),
        ("2024\u20142025年度 营业收入100", "2024\u20142025年度", True),
        ("2025-01-01至2025-12-31 营业收入100", "2025-01-01", False),
        ("2025-01-01至2025-12-31 营业收入100", "2025-01-01至2025-12-31", True),
        ("Q1 2025 营业收入100", "2025", False),
        ("Q1 2025 营业收入100", "Q1 2025", True),
        ("2025年 营业收入100", "25", False),
        ("2025年 营业收入100", "2025", False),
        ("2025年 营业收入100", "2025年", True),
        ("2025年度 营业收入100", "2025年", False),
        ("2025年度 营业收入100", "2025年度", True),
        ("2025年第一季度 营业收入100", "2025年", False),
        ("2025年第一季度 营业收入100", "2025年第一季度", True),
        ("截至2025年12月31日,余额100", "2025年12月31日", True),
        ("报告期末,余额100", "报告期末", True),
        ("年份 2025 2024", "2025", True),
    ],
)
def test_exact_financial_period_token(excerpt: str, period: str, expected: bool) -> None:
    assert fallback_module._period_is_bound(excerpt, period) is expected


@pytest.mark.parametrize(
    ("source", "value", "expected"),
    [
        ("单位:万元 现金流(人民币100万元)", "100", False),
        ("现金流(￥100万元)", "100", False),
        ("单位:万元 现金流(人民币(境内)100万元)", "100", False),
        ("单位:万元 现金流(100万元(附注))", "100", False),
        ("单位:万元 现金流 (100)", "100", False),
        ("单位:万元 现金流\uff081,000\uff09", "1000", False),
        ("单位:万元 现金流 ( 1,000 )", "1000", False),
        ("现金流 (100万元)", "100", False),
        ("现金流 (100万元),收入100万元", "100", True),
        ("单位:万元 现金流+100", "100", True),
        ("单位:万元 现金流100-200", "100", False),
        ("单位:万元 现金流100-", "100", False),
        ("单位:万元 现金流100\u2013200", "100", False),
        ("单位:万元 现金流100\u2212", "100", False),
        ("单位:万元 现金流100 -80", "100", True),
        ("单位:万元 现金流1,00", "100", False),
        ("单位:万元 现金流10,00", "1000", False),
        ("单位:万元 现金流1,000", "1000", True),
        ("单位:万元 现金流1,000.00", "1000.00", True),
        ("单位:万元 现金流-1,000.00", "-1000.00", True),
        ("单位:万元 现金流100e6", "100", False),
        ("单位:万元 现金流100E6", "100", False),
        ("单位:万元 现金流1e-6", "-6", False),
        ("单位:万元 现金流100,利润20", "100", True),
        ("单位:万元 现金流0", "-0", False),
        ("单位:万元 现金流0.00", "-0.00", False),
        ("单位:万元 现金流-0", "0", False),
        ("单位:万元 现金流-0.00", "0.00", False),
        ("单位:万元 现金流-0.00", "-0.00", True),
        ("单位:万元 现金流+0.00", "0.00", True),
        ("单位:万元 现金流-100", "100", False),
        ("单位:万元 现金流-100", "-100", True),
        ("单位:万元 现金流100", "-100", False),
        ("单位:万元 现金流\u2212100", "100", False),
        ("单位:万元 现金流100.01", "100", False),
    ],
)
def test_financial_table_value_keeps_its_sign(source: str, value: str, expected: bool) -> None:
    assert (
        fallback_module._excerpt_is_bound(
            source_text=source,
            excerpt=source,
            value=value,
            unit="万元",
            audited_table_context=True,
        )
        is expected
    )


@pytest.mark.parametrize("boundary", ["none", "extracted", "audited"])
async def test_retained_original_avoids_repeated_fetch_failure(
    boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await test_verified_annual_is_available_for_untried_financial_topic(
        title="2025 Annual Report",
        search="empty",
        boundary=boundary,
        failure="dns",
        monkeypatch=monkeypatch,
        wide_english=True,
    )


async def test_same_fixed_original_without_reuse_reproduces_financial_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same fixed source/provider/clock; only the retained branch is disabled.
    await test_verified_annual_is_available_for_untried_financial_topic(
        title="2025 Annual Report",
        search="empty",
        boundary="none",
        failure="dns",
        monkeypatch=monkeypatch,
        wide_english=True,
        retained_original=False,
    )


@pytest.mark.parametrize(
    "mismatch",
    [
        "none",
        "url",
        "hash",
        "rights",
        "provider",
        "level",
        "publication",
        "revision",
        "future",
        "audit",
        "fact",
        "same_topic",
        "clipped_missing_table",
        "clipped_financial",
        "clipped_other_topic",
    ],
)
async def test_legacy_inline_never_promotes_to_reusable_original(mismatch: str) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    session = _session(now)
    packet = _packet(session)
    selected = _candidate(now).model_copy(update={"title": "2025 Annual Report"})
    registration = fallback_module._WebSourceRegistration(
        source_id="test-original",
        domain=selected.domain,
        source_level=ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
        source_group="issuer",
        enabled=True,
        release_tier="dev_only",
        rights_status="unverified",
        research_use="excerpt_bound_fact",
    )
    citation = packet.citations[0].model_copy(
        update={
            "locator": str(selected.url),
            "content_sha256": hashlib.sha256(b"Original verified context.").hexdigest(),
            "rights_snapshot_sha256": "b" * 64,
            "source_provider_id": registration.source_id,
            "source_level": registration.source_level,
            "published_at": selected.published_at,
            "first_seen_at": now,
            "revision_known_at": now - timedelta(days=1),
        }
    )
    fact = packet.facts[0].model_copy(
        update={
            "evidence_id": citation.evidence_id,
            "data_audit_status": "passed",
        }
    )
    prior = ResearchAcquisitionCandidate.model_validate(
        {
            "candidate": selected,
            "content_sha256": hashlib.sha256(b"Original verified context.").hexdigest(),
            "first_seen_at": now,
            "revision_known_at": now - timedelta(days=1),
            "source_context": "Original verified context.",
            "facts": [
                {
                    "missing_fact_key": "identity.company",
                    "source_excerpt": "Original verified context.",
                    "value": "",
                    "unit": "",
                    "period": "",
                }
            ],
            "verdicts": [
                {
                    "fact_key": "identity.company",
                    "outcome": "passed",
                    "reason_code": "SUPPORTED",
                    "reason": "原文支持。",
                }
            ],
        }
    )
    changes = {
        "url": {"locator": "https://www.cninfo.com.cn/another"},
        "hash": {"content_sha256": "c" * 64},
        "rights": {"rights_snapshot_sha256": "c" * 64},
        "provider": {"source_provider_id": "another-source"},
        "level": {"source_level": ResearchEvidenceSourceLevel.TRUSTED_MEDIA_DATA},
        "publication": {"published_at": now - timedelta(days=3)},
        "revision": {"revision_known_at": now - timedelta(days=3)},
        "future": {"first_seen_at": now + timedelta(days=1)},
    }
    citation = citation.model_copy(update=changes.get(mismatch, {}))
    if mismatch == "audit":
        prior.verdicts = []
    if mismatch == "fact":
        fact = fact.model_copy(update={"data_audit_status": "not_reviewed"})
    if mismatch == "same_topic":
        prior.facts[0].missing_fact_key = "financial.quality"
    if mismatch.startswith("clipped_"):
        prior.source_context = (
            "Key Accounting Data\nUnit RMB Yuan; 2025 2024\nRevenue 100 80; Cash flow 15 12"
            if mismatch != "clipped_missing_table"
            else "Clipped identity text only."
        )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="d" * 64,
        candidates=[prior],
    )
    coordinator = ResearchWebFallbackCoordinator(
        provider=_AcquisitionProvider(),
        discovery=_AcquisitionDiscovery(now),
        connector=_FailingConnector(),
        rights=_rights(),
        receipts=_Receipts(),
    )
    coordinator._acquisition = state
    coordinator._packet = packet.model_copy(update={"citations": [citation], "facts": [fact]})
    # An audited inline excerpt is still not an adapter-confirmed original.
    # Citation aliases, audit flags and apparent financial headings cannot promote it.
    before = coordinator._packet.model_copy(deep=True)
    result = await coordinator._reuse_documents(
        session=session,
        packet=coordinator._packet,
        registrations={registration.domain: registration},
        task_deadline_at=state.deadline_at,
    )
    assert result == before and state.documents == []
    assert state.candidates == [prior]


def _stored_source_context(
    state: ResearchAcquisitionState, candidate: ResearchAcquisitionCandidate
) -> str:
    if candidate.document_id is None:
        assert candidate.source_context is not None
        return candidate.source_context
    original = next(
        document for document in state.documents if document.document_id == candidate.document_id
    )
    return fallback_module._source_window_text(original.body, candidate.context_spans)


def _document_fixture(body: str, now: datetime, *, pdf: bool = False):
    registration = next(
        item
        for item in fallback_module._load_allowlist().sources
        if item.domain == "www.cninfo.com.cn" and item.enabled
    )
    document = fallback_module.ResearchAcquisitionDocument(
        candidate=_candidate(now),
        source_id=registration.source_id,
        rights_snapshot_sha256=hashlib.sha256(
            fallback_module._canonical(registration.model_dump(mode="json"))
        ).hexdigest(),
        content_sha256=hashlib.sha256(body.encode()).hexdigest(),
        body=body,
        first_seen_at=now,
        revision_known_at=now - timedelta(days=1),
        discovery_request_sha256="a" * 64,
        retrieval_receipt_id=uuid4(),
        extraction_scope="complete",
        pages=fallback_module._source_pages(body) if pdf else [],
    )
    # This helper seeds already verified originals for scheduling tests. HTTP
    # classification/date rejection has separate governed-transport regressions.
    from stock_assistant.contracts.formal_disclosure import (
        FormalDocumentIdentity,
        FormalDocumentLocation,
        FormalDocumentVerification,
        PublicationDateEvidence,
    )

    assert document.candidate.published_at is not None
    proof = FormalDocumentVerification(
        identity=FormalDocumentIdentity(
            document_kind="annual",
            issuer_name="兆易创新",
            instrument_id="XSHG:603986",
            title="2025 年年度报告",
            native_document_id="frozen-r3-annual",
            canonical_url=str(document.candidate.url),
            binding_basis="official_index",
            identity_anchors=[
                FormalDocumentLocation(field="normalized_original", start=0, end=min(12, len(body)))
            ],
        ),
        publication_evidence=[
            PublicationDateEvidence(
                kind="official_index",
                raw_value=document.candidate.published_at.isoformat(),
                published_at=document.candidate.published_at,
                precision="instant",
                timezone="UTC",
                canonical_url=str(document.candidate.url),
                location=FormalDocumentLocation(field="official_row.publication"),
                observation_sha256="a" * 64,
                receipt_id=document.retrieval_receipt_id,
            )
        ],
        published_at=document.candidate.published_at,
        source_id=document.source_id,
        normalized_original_sha256=document.content_sha256,
        rights_snapshot_sha256=document.rights_snapshot_sha256,
        first_seen_at=document.first_seen_at,
        pit_cutoff=now,
        media_type="application/pdf" if pdf else "text/plain",
    )
    document = document.model_copy(update={"formal_document": proof})
    return document, registration


@pytest.mark.parametrize(
    "heading",
    [
        "主要会计数据和财务指标",
        "Key Accounting Data and Financial Indicators in the past three years",
        "合并利润表",
    ],
)
def test_document_windows_keep_late_identity_and_full_cross_page_table(heading: str) -> None:
    pages = []
    for number in range(1, 42):
        content = "说明文字" * (140 if number < 10 else 390)
        if number == 9:
            content = "603986 兆易创新 公司身份\n" + content
        elif number == 38:
            content = heading + "\n单位:万元 币种:人民币 2025年 2024年\n营业收入 100 80\n"
        elif number == 39:
            content = "净利润 20 10\n经营现金流 15 12\n"
        elif number == 40:
            content = "第八节 其他事项\n" + content
        pages.append(f"[PDF page {number}]\n{content}\n")
    body = "\n".join(pages)
    assert 4000 < body.index("603986") < 6000
    assert body.index(heading) > 40000
    locations = fallback_module._source_pages(body)
    identity = fallback_module._identity_spans(
        body, name="兆易创新", code="603986", pages=locations
    )
    windows = fallback_module._topic_windows(body, "financial.quality", identity, locations)
    assert len(windows) == 1
    context = fallback_module._source_window_text(body, windows[0])
    assert len(context) <= 40000
    for expected in (
        "603986",
        "兆易创新",
        heading,
        "单位:万元",
        "2025年 2024年",
        "营业收入 100 80",
        "净利润 20 10",
        "经营现金流 15 12",
    ):
        assert expected in context
    assert "[PDF page 39]" in context
    assert all(body[span.start : span.end] in context for span in windows[0])


@pytest.mark.asyncio
async def test_fifth_complete_window_is_deferred_without_attempt_or_empty_pro_call() -> None:
    keys_and_headings = [
        ("business.customer_revenue_chain", "经营模式"),
        ("business.product_structure_change", "主营业务分析"),
        ("financial.quality", "合并利润表"),
        ("competitive.position", "核心竞争力分析"),
        ("management.capital_allocation", "利润分配方案"),
    ]
    body = "603986 兆易创新\n\n" + "\n\n".join(
        heading + "\n" + "原始说明" * 2200 for _, heading in keys_and_headings
    )
    now = datetime.now(UTC)
    session = _session(now)
    document, registration = _document_fixture(body, now)

    class EmptyFlash(_AcquisitionProvider):
        requests: list[GenerationRequest]

        def __init__(self) -> None:
            super().__init__()
            self.requests = []

        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.requests.append(request)
            response = await super().generate(request)
            return response.model_copy(update={"payload": {"schema_version": "1.0", "facts": []}})

    flash, pro = EmptyFlash(), _AuditProvider()
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=_AcquisitionDiscovery(now),
        connector=_FailingConnector(),
        rights=_rights(),
        receipts=_Receipts(),
    )
    seed = _packet(session)
    seed = seed.model_copy(
        update={
            "facts": [
                seed.facts[0].model_copy(update={"key": "identity.company"}),
                seed.facts[1].model_copy(update={"key": "business.core_products"}),
            ]
        }
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
    )
    coordinator._acquisition, coordinator._packet = state, seed
    saved = []

    async def save(value: ResearchAcquisitionState, packet: ResearchEvidencePacket) -> None:
        saved.append(ResearchAcquisitionState.model_validate_json(value.model_dump_json()))

    coordinator._save_acquisition = save
    for index in range(3):
        state.active_fact_keys = []
        await coordinator._run_document_batch(
            document=document,
            session=session,
            registration=registration,
            requested_keys=[key for key, _ in keys_and_headings],
        )
        if index == 0:
            assert state.attempts == {key: 1 for key, _ in keys_and_headings[:4]}
            assert state.deferred_fact_keys == [keys_and_headings[4][0]]
    assert len(flash.requests) == 2 and pro.calls == 0
    assert len(state.candidates) == 2
    assert state.attempts == {key: 1 for key, _ in keys_and_headings}
    assert not state.deferred_fact_keys
    for request, candidate in zip(flash.requests, state.candidates, strict=True):
        data = json.loads(request.messages[-1].content)
        assert (
            data["missing_fact_keys"]
            == candidate.requested_fact_keys
            == list(candidate.attempt_numbers)
        )
        assert len(data["source_text"]) <= 40000
        assert candidate.source_context is None and candidate.document_id == document.document_id
        assert candidate.extraction_complete and candidate.facts == []
    assert saved[-1].documents[0] == document
    assert {check.fact_key for check in state.window_checks} >= {
        "valuation.context",
        "risk.resilience",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("search_exhausted", [False, True])
@pytest.mark.parametrize("additional_window", [False, True])
async def test_complete_drains_deferred_original_window_before_any_query(
    search_exhausted: bool,
    additional_window: bool,
) -> None:
    keys_and_headings = [
        ("business.customer_revenue_chain", "经营模式"),
        ("business.product_structure_change", "主营业务分析"),
        ("financial.quality", "合并利润表"),
        ("competitive.position", "核心竞争力分析"),
        ("management.capital_allocation", "利润分配方案"),
    ]
    target = keys_and_headings[-1][0]
    excerpt = "公司拟以现金分红方式分配利润\uff0c具体实施以股东大会审议为准。"
    body = "603986 兆易创新\n\n" + "\n\n".join(
        heading + "\n" + (excerpt if key == target else "") + "原始说明" * 2200
        for key, heading in keys_and_headings
    )
    if additional_window:
        body += "\n\n经营模式\n" + "另一完整窗口" * 1500
    now = datetime.now(UTC)
    session = _session(now)
    document, _ = _document_fixture(body, now)

    class DeferredFlash(_AcquisitionProvider):
        def __init__(self) -> None:
            super().__init__()
            self.requests = []

        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            self.requests.append(request)
            assert request.task_type == "research_web_evidence_normalization", (
                "local window must precede query"
            )
            data = json.loads(request.messages[-1].content)
            response = await super().generate(request)
            response.payload = {
                "schema_version": "1.0",
                "facts": [{"missing_fact_key": target, "source_excerpt": excerpt}]
                if target in data["missing_fact_keys"]
                else [],
            }
            return response

    flash, pro = DeferredFlash(), _AuditProvider()
    discovery = _AcquisitionDiscovery(now)
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=discovery,
        connector=_FailingConnector(),
        rights=_rights(),
        receipts=_Receipts(),
    )
    seed = _packet(session)
    seed = seed.model_copy(
        update={
            "facts": [
                seed.facts[0].model_copy(update={"key": "identity.company"}),
                seed.facts[1].model_copy(update={"key": "business.core_products"}),
            ],
            "coverage_gaps": [
                CoverageGap(
                    area=target, reason_code="REQUIRED_FIELD_MISSING", detail="资本配置缺口。"
                )
            ],
        }
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="b" * 64,
        documents=[document],
        discovery_exhausted=search_exhausted,
    )
    coordinator._acquisition, coordinator._packet = state, seed
    result = await coordinator.complete(
        session=session,
        packet=seed,
        cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
        task_deadline_at=state.deadline_at,
    )
    assert target in {fact.key for fact in result.facts}
    assert target not in {gap.area for gap in result.coverage_gaps}
    assert len(flash.requests) == 2 and pro.calls == 1
    assert discovery.counts == {}
    assert state.attempts == {
        key: 2 if additional_window and key == keys_and_headings[0][0] else 1
        for key, _ in keys_and_headings
    }
    assert state.pending_query is None and not state.deferred_fact_keys
    assert len(state.candidates) == 2


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "domain",
        "source",
        "rights",
        "publication",
        "revision",
        "future",
        "revoked",
        "disabled",
        "hash",
    ],
)
async def test_reusable_document_checks_current_identity_rights_pit_and_hash(change: str) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    document, registration = _document_fixture("603986 兆易创新\n原文。", now)
    candidate = document.candidate
    updates = {
        "domain": {"candidate": candidate.model_copy(update={"domain": "other.example"})},
        "source": {"source_id": "other-source"},
        "rights": {"rights_snapshot_sha256": "b" * 64},
        "publication": {
            "candidate": candidate.model_copy(update={"published_at": now + timedelta(days=1)})
        },
        "revision": {"revision_known_at": now + timedelta(days=1)},
        "future": {"first_seen_at": now + timedelta(days=1)},
        "hash": {"content_sha256": "b" * 64},
    }
    raw = document.model_dump(mode="python") | updates.get(change, {})
    if change in {"source", "rights", "publication", "future", "hash"}:
        with pytest.raises(ValueError, match="formal verification"):
            type(document).model_validate(raw)
        return
    document = type(document).model_validate(raw)
    coordinator = ResearchWebFallbackCoordinator(
        provider=_AcquisitionProvider(),
        discovery=_AcquisitionDiscovery(now),
        connector=_FailingConnector(),
        rights=_DeniedRights() if change == "revoked" else _rights(),
        receipts=_Receipts(),
    )
    allowed = await coordinator._document_allowed(
        document=document,
        session=_session(now),
        registration=None if change == "disabled" else registration,
        task_deadline_at=now + timedelta(minutes=5),
    )
    assert allowed is (change == "none")


def _selected_test_context(source: str, keys: list[str]) -> str:
    """Exercise the production locator, merging whole windows without truncation."""
    name = next(
        name for name in ("离线测试公司", "离线公司", "兆易创新", "测试公司") if name in source
    )
    code = "300001" if name == "测试公司" else "603986"
    pages = fallback_module._source_pages(source)
    identity = fallback_module._identity_spans(source, name=name, code=code, pages=pages)
    selected = []
    for key in keys:
        windows = fallback_module._topic_windows(source, key, identity, pages)
        if not windows:
            continue
        combined = fallback_module._merge_source_spans([*selected, *windows[0]])
        if len(fallback_module._source_window_text(source, combined)) <= 40000:
            selected = combined
    return fallback_module._source_window_text(source, selected)


@pytest.mark.parametrize(
    "heading", ["主要会计数据和财务指标", "VII. Key Accounting Data", "iii. KEY ACCOUNTING DATA"]
)
def test_contents_page_heading_is_skipped_for_complete_original_body(heading: str) -> None:
    source = "[PDF page 1]\n603986 兆易创新\n目录\n" + heading + "\n项目\n8\n其他事项\n9\n"
    source += "[PDF page 2]\n" + "背景说明。" * 8500
    source += (
        "\n[PDF page 8]\n" + heading + "\n单位:万元 币种:人民币\n项目 2025 2024\n营业收入 100 80\n"
    )
    source += "[PDF page 9]\n净利润 20 10\n经营现金流 15 12\n第十节 其他事项\n"
    pages = fallback_module._source_pages(source)
    identity = fallback_module._identity_spans(source, name="兆易创新", code="603986", pages=pages)
    windows = fallback_module._topic_windows(source, "financial.quality", identity, pages)
    assert len(windows) == 1
    selected = fallback_module._source_window_text(source, windows[0])
    assert "目录" not in selected and "[PDF page 8]" in selected and "[PDF page 9]" in selected
    assert "单位:万元 币种:人民币" in selected and "经营现金流 15 12" in selected


@pytest.mark.asyncio
async def test_invalid_observation_does_not_invalidate_another_same_hash_observation() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    session = _session(now)
    first, registration = _document_fixture("603986 兆易创新\n原始披露。", now)
    second = first.model_copy(
        update={"document_id": uuid4(), "revision_known_at": now + timedelta(days=1)}
    )
    first_id = fallback_module._source_evidence_id(
        first.content_sha256, first.source_id, first.document_id
    )
    second_id = fallback_module._source_evidence_id(
        second.content_sha256, second.source_id, second.document_id
    )
    assert first_id != second_id
    seed = _packet(session)
    citations = [
        seed.citations[0].model_copy(
            update={
                "evidence_id": identifier,
                "content_sha256": first.content_sha256,
                "source_provider_id": first.source_id,
            }
        )
        for identifier in [first_id, second_id]
    ]
    assert seed.exposure_profile is not None
    profile = seed.exposure_profile.model_copy(
        update={
            "edges": [
                seed.exposure_profile.edges[0].model_copy(update={"evidence_ids": [identifier]})
                for identifier in [first_id, second_id]
            ]
        }
    )
    packet = seed.model_copy(
        update={
            "facts": [
                seed.facts[0].model_copy(
                    update={"key": "identity.company", "evidence_id": first_id}
                ),
                seed.facts[1].model_copy(
                    update={"key": "business.core_products", "evidence_id": second_id}
                ),
            ],
            "citations": [*seed.citations, *citations],
            "exposure_profile": profile,
            "coverage_gaps": [],
        }
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="a" * 64,
        documents=[first, second],
    )
    coordinator = ResearchWebFallbackCoordinator(
        provider=_AcquisitionProvider(),
        discovery=_AcquisitionDiscovery(now),
        connector=_FailingConnector(),
        rights=_rights(),
        receipts=_Receipts(),
    )
    coordinator._acquisition, coordinator._packet = state, packet
    result = await coordinator._reuse_documents(
        session=session,
        packet=packet,
        registrations={registration.domain: registration},
        task_deadline_at=state.deadline_at,
    )
    assert [fact.evidence_id for fact in result.facts] == [first_id]
    assert {citation.evidence_id for citation in result.citations} == {
        first_id,
        *(citation.evidence_id for citation in seed.citations),
    }
    assert result.coverage_gaps[0].area == "business.core_products"
    assert packet.facts[1].evidence_id == second_id  # The input/history was not rewritten.
    assert result.exposure_profile is not None
    assert [edge.evidence_ids for edge in result.exposure_profile.edges] == [[first_id]]
    assert len(profile.edges) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["not_located", "window_unavailable_by_budget", "next_window"])
async def test_window_scheduler_advances_without_empty_batches_or_relocating_checked_windows(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    key = "financial.quality"
    monkeypatch.setattr(fallback_module, "REAL_FACT_GAPS", {key: "财务"})
    body = "603986 兆易创新\n" + "背景。" * 14000
    if mode == "window_unavailable_by_budget":
        body += "\n主要会计数据\n" + "整表原文。" * 8500
    elif mode == "next_window":
        body += "\n主要会计数据\n单位:万元 2025 2024\n营业收入 100 80\n"
        body += "主要财务指标\n单位:万元 2025 2024\n净利润 20 10\n经营现金流 15 12\n"
    document, registration = _document_fixture(body, now)
    session = _session(now)
    packet = _packet(session)
    packet = packet.model_copy(
        update={
            "facts": [
                packet.facts[0].model_copy(update={"key": "identity.company"}),
                packet.facts[1].model_copy(update={"key": "business.core_products"}),
            ]
        }
    )

    class EmptyFlash(_AcquisitionProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            return response.model_copy(update={"payload": {"schema_version": "1.0", "facts": []}})

    flash, pro = EmptyFlash(), _AuditProvider()
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=_AcquisitionDiscovery(now),
        connector=_FailingConnector(),
        rights=_rights(),
        receipts=_Receipts(),
    )
    state = ResearchAcquisitionState(
        schema_version="1.3",
        rule_version=fallback_module.WEB_FALLBACK_RULE_VERSION,
        instrument_id=session.instrument_id,
        requested_as_of=now,
        deadline_at=now + timedelta(minutes=5),
        input_sha256="a" * 64,
        documents=[document],
    )
    coordinator._acquisition, coordinator._packet = state, packet
    located = 0
    locate = fallback_module._topic_windows

    def counted(*args, **kwargs):
        nonlocal located
        located += 1
        return locate(*args, **kwargs)

    monkeypatch.setattr(fallback_module, "_topic_windows", counted)
    for _round in range(2):
        result = await coordinator._run_document_batch(
            document=document,
            session=session,
            registration=registration,
            requested_keys=[key],
        )
        assert result[0] == []
        state = ResearchAcquisitionState.model_validate_json(state.model_dump_json())
        state.active_gap, state.active_fact_keys = None, []
        coordinator._acquisition = state
    assert pro.calls == 0
    if mode == "next_window":
        assert flash.calls == 2 and state.attempts == {key: 2}
        assert len(state.candidates) == 2 and all(
            item.extraction_complete for item in state.candidates
        )
        assert state.candidates[0].fact_spans[key] != state.candidates[1].fact_spans[key]
    else:
        assert flash.calls == 0 and located == 1 and state.attempts == {}
        assert state.window_checks[0].outcome == mode and len(state.window_checks) == 1
        assert state.candidates == []


@pytest.mark.parametrize(
    "violation",
    [
        "none",
        "body_limit",
        "capacity",
        "foreign_document",
        "inline_and_ref",
        "invalid_interval",
        "batch_limit",
        "candidate_limit",
    ],
)
def test_document_checkpoint_capacity_and_reference_contract(violation: str) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    document, _registration = _document_fixture("603986 兆易创新\n" + "文" * 99988, now)
    assert len(document.body) == 100000
    documents = [document.model_copy(update={"document_id": uuid4()}) for _ in range(40)]
    document = documents[0]
    batch = {
        "candidate": document.candidate,
        "content_sha256": document.content_sha256,
        "first_seen_at": document.first_seen_at,
        "revision_known_at": document.revision_known_at,
        "publication_basis": document.publication_basis,
        "discovery_request_sha256": document.discovery_request_sha256,
        "document_id": document.document_id,
        "context_spans": [
            {"start": 0, "end": 40000 - len(document.formal_document.model_dump_json())}
        ],
        "fact_spans": {
            "identity.company": [
                {"start": 0, "end": 40000 - len(document.formal_document.model_dump_json())}
            ]
        },
        "requested_fact_keys": ["identity.company"],
        "attempt_numbers": {"identity.company": 1},
        "extraction_complete": False,
        "facts": [],
    }
    raw = {
        "schema_version": "1.3",
        "rule_version": fallback_module.WEB_FALLBACK_RULE_VERSION,
        "instrument_id": "XSHG:603986",
        "requested_as_of": now,
        "deadline_at": now + timedelta(minutes=5),
        "input_sha256": "a" * 64,
        "documents": documents,
        "candidates": [batch],
    }
    if violation == "body_limit":
        raw["documents"] = [document.model_dump(mode="python") | {"body": document.body + "文"}]
    elif violation == "capacity":
        raw["documents"] = [*documents, document.model_copy(update={"document_id": uuid4()})]
    elif violation == "foreign_document":
        batch["document_id"] = uuid4()
    elif violation == "inline_and_ref":
        batch["source_context"] = "不得冒充原文"
    elif violation == "invalid_interval":
        batch["fact_spans"] = {"identity.company": [{"start": 39999, "end": 40001}]}
    elif violation == "batch_limit":
        batch["context_spans"] = [{"start": 0, "end": 40001}]
    elif violation == "candidate_limit":
        raw["candidates"] = [batch] * 101
    if violation == "none":
        restored = ResearchAcquisitionState.model_validate(raw)
        assert sum(len(item.body) for item in restored.documents) == 4000000
        assert len(restored.candidates) == 1
    else:
        with pytest.raises(ValueError):
            ResearchAcquisitionState.model_validate(raw)
