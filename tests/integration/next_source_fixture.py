"""Fixed public-source fakes shared by memory and real PostgreSQL recovery tests."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid5

import httpx
from tests.unit.qmr import test_research_fallback as t

from stock_assistant.contracts.discovery import (
    SourceDiscoveryCandidate,
    SourceDiscoveryResult,
    SourceDiscoveryStatus,
)
from stock_assistant.platform.discovery.cninfo import CninfoSourceDiscovery

FORMAL_MATERIAL = json.loads(Path("tests/fixtures/research/formal-disclosure.v1.json").read_text())


async def official_run(
    session, state, packet, save, before_model=None, wrong_company=False, formal_attachment=False
):
    requests, queries, model_tasks, audit_keys, audit_requests = [], [], [], [], []
    now = session.as_of

    class Discovery(t._AcquisitionDiscovery):
        async def discover(self, query):
            queries.append(query.model_dump(mode="json"))
            key = query.missing_fact_keys[0]
            candidates = [
                SourceDiscoveryCandidate.model_validate(
                    t._candidate(now, path=f"{key}/candidate/{i}").model_dump(mode="json")
                    | {
                        "url": f"https://{domain}/{key}/candidate/{i}",
                        "domain": domain,
                        "candidate_id": uuid5(
                            session.session_id, f"https://{domain}/{key}/candidate/{i}"
                        ),
                    }
                )
                for i, domain in enumerate(["static.cninfo.com.cn", "www.zmax-opt.com"])
            ]
            return SourceDiscoveryResult(
                schema_version="1.2",
                provider="frozen-search",
                status=SourceDiscoveryStatus.SUCCEEDED,
                request_sha256="a" * 64,
                candidates=candidates[: query.max_results],
                searched_at=now,
            )

    class Flash(t._AcquisitionProvider):
        async def generate(self, request):
            model_tasks.append(request.task_type)
            if before_model is not None:
                await before_model(request)
            response = await super().generate(request)
            if formal_attachment and request.task_type == "research_web_evidence_normalization":
                # The frozen material supports exactly these three topics. The
                # generic scheduler fake deliberately ignores topic semantics,
                # so it is unsuitable as a factual-benefit oracle on its own.

                material = FORMAL_MATERIAL
                text = material["formal_text"]
                excerpts = {
                    "identity.company": next(
                        line for line in text.splitlines() if line.startswith("公司全称")
                    ),
                    "business.core_products": next(
                        line for line in text.splitlines() if line.startswith("报告期内公司主营")
                    ),
                    "financial.quality": text.split("三、合并财务报表\n", 1)[1].strip(),
                }
                data = json.loads(request.messages[-1].content)
                response.payload = {
                    "schema_version": "1.0",
                    "facts": [
                        {
                            "missing_fact_key": key,
                            "source_excerpt": excerpts[key],
                            "value": material["gold"]["revenue"]
                            if key == "financial.quality"
                            else "",
                            "unit": "人民币元" if key == "financial.quality" else "",
                            "period": material["gold"]["period"]
                            if key == "financial.quality"
                            else "",
                        }
                        for key in data["missing_fact_keys"]
                        if key in excerpts and excerpts[key] in data["source_text"]
                    ],
                }
            return response

    class Pro(t._AcquisitionProvider):
        async def generate(self, request):
            audit_requests.append(request.task_type)
            data = json.loads(request.messages[-1].content)
            audit_keys.extend(fact["missing_fact_key"] for fact in data["candidate_facts"])
            return await super().generate(request)

    def handler(request):
        origin = f"https://{request.url.host}"
        if state is not None:
            assert origin not in {s.origin for s in state.robots_origin_stops}, (
                "persisted robots stop must prevent all new HTTP requests"
            )
        requests.append((request.url.host, request.url.path))
        if request.url.host in {"www.cninfo.com.cn", "static.cninfo.com.cn"}:
            assert request.url.path == "/robots.txt"
            return httpx.Response(403)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if formal_attachment:
            from tests.integration.formal_disclosure_fixture import formal_pdf

            if request.url.path == "/files/annual.pdf":
                return httpx.Response(
                    200, content=formal_pdf(), headers={"content-type": "application/pdf"}
                )
            return httpx.Response(
                200,
                text=json.loads(
                    Path("tests/fixtures/research/formal-disclosure.v1.json").read_text()
                )["parent_html"],
                headers={"content-type": "text/html"},
            )
        identity = (
            "wrong-company 兆易创新 证券代码603986" if wrong_company else "中润光学 证券代码688307"
        )
        text = (
            f"2025 年年度报告\n发布日期: 2026-04-20\n{identity} 光学镜头。"
            "主营业务与经营模式: 公司生产光学镜头并向客户销售。\n\n"
            "主营业务分析: 报告期内产品结构保持稳定。\n\n"
            "合并利润表: 2025年, 人民币万元, 营业收入100, 净利润10, 经营现金流12。\n\n"
            "核心竞争力分析: 公司持续研发光学镜头。\n\n"
            "利润分配方案: 2025年度拟现金分红, 预案尚需股东大会审议。\n\n"
            "风险因素: 客户需求变化可能影响订单。\n\n"
            "行业情况: 光学镜头市场存在竞争。"
        )
        return httpx.Response(
            200,
            text=text,
            headers={
                "content-type": "text/plain",
                "last-modified": t._VALID_LAST_MODIFIED,
            },
        )

    async def progress(stage, message):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = t.ResearchWebFallbackCoordinator(
            provider=Flash(),
            audit_provider=Pro(),
            discovery=Discovery(now),
            disclosure_discovery=CninfoSourceDiscovery(
                instrument_id=session.instrument_id,
                instrument_name=session.instrument_name,
                client=client,
                allowed_domains={"www.cninfo.com.cn", "static.cninfo.com.cn"},
                resolver=t._public_resolver,
                clock=lambda: now,
            ),
            connector=t.GovernedWebSourceConnector(
                client=client,
                allowed_domains={"www.cninfo.com.cn", "static.cninfo.com.cn", "www.zmax-opt.com"},
                resolver=t._public_resolver,
                clock=lambda: now,
            ),
            rights=t._rights(),
            receipts=t._Receipts(),
        )
        result = await coordinator.collect_real(
            session=session,
            packet=packet,
            state=state,
            cutoff_mode=t.ResearchCutoffMode.CURRENT_RUN,
            task_deadline_at=state.deadline_at if state is not None else now + timedelta(minutes=5),
            save=save,
            progress=progress,
        )
    return result, {
        "requests": requests,
        "queries": queries,
        "model_tasks": model_tasks,
        "audit_keys": audit_keys,
        "audit_requests": audit_requests,
    }
