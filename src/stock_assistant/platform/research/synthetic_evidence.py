"""Original deterministic evidence for zero-network R2 product verification."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID, uuid5

from stock_assistant.contracts.global_events import (
    ResearchExposureDirectness,
    ResearchExposureEdge,
    ResearchExposureProfile,
)
from stock_assistant.contracts.research import (
    CoverageGap,
    ResearchEvidenceCitation,
    ResearchEvidenceFact,
    ResearchEvidencePacket,
)

_NAMESPACE: Final = UUID("5fd3ffef-8adf-5da9-83ac-8ff5db877942")
SYNTHETIC_EVIDENCE_FIXTURE_VERSION: Final = "r2-original-synthetic.v4"
_V3_FACTS: Final = (
    (
        "business.revenue_mix",
        "合成样本中核心业务仍是主要收入来源\uff0c存量客户已续签采购框架。"
        "采购集中于少数客户\uff0c框架协议不等于不可撤销订单。具体产品与客户类型未披露。",
        None,
        None,
    ),
    (
        "business.change",
        "合成样本记录产品组合调整已执行\uff0c交付验收记录显示返工减少。"
        "关键原材料采购报价同步上升\uff0c新增成本尚未完全转嫁给客户。"
        "具体产品调整前后的收入构成未披露。",
        None,
        None,
    ),
    (
        "financial.revenue",
        "合成口径以五年前营业收入为基期 100.00\uff0c期末同合并范围收入指数为 126.40。"
        "期间逐年收入数据未提供。",
        "126.40",
        "index",
    ),
    (
        "financial.margin",
        "合成最近季度毛利率为 38.20%\uff0c上年同季为 35.20%\uff0c合并范围及核算口径相同。"
        "当期采购涨价的原材料尚在库存\uff0c未全部结转成本。",
        "38.20",
        "percent",
    ),
    (
        "valuation.percentile",
        "合成滚动市盈率在同标的五年历史月末观测中处于 54.00 分位。"
        "行业可比公司估值与盈利预测未提供。",
        "54.00",
        "percentile",
    ),
    (
        "event.major",
        "合成事件流记录了一次重大产能调整\uff0c新增设备已完成验收\uff0c已签订单覆盖部分计划产出。"
        "其余产能尚无锁定订单\uff0c量产良率和交付进度待验证。",
        None,
        None,
    ),
    (
        "macro.exposure",
        "合成样本的核心业务属于半导体行业\uff0c存在半导体周期暴露。"
        "公司有登记的在研项目与研发支出\uff0c但研发支持资格尚未确认\uff0c未收到补贴。"
        "受限技术范围是否涵盖公司在用设备仍未核实。",
        None,
        None,
    ),
    (
        "conflict.unit",
        "合成第二来源的单位口径无法对齐\uff0c因此该数值保持未知。"
        "合成资料未提供审计后的经营现金流与应收账龄\uff0c回款质量尚待验证。",
        None,
        None,
    ),
)


# A separately versioned scenario: these are observations, never model grades or conclusions.
_FACTS: Final = (
    (
        "business.revenue_mix",
        "合成场景中公司设计并销售工业控制存储芯片\uff0c客户为工业设备制造商。"
        "客户通过产品验证后签订采购框架\uff0c芯片交付验收"
        "后确认收入\uff0c收入扣除代工、封装测试和研发销售"
        "支出形成利润。"
        "存量客户已续签采购框架\uff0c但采购集中于少数客户\uff0c框架协议不等于不可撤销订单。",
        None,
        None,
    ),
    (
        "business.change",
        "合成场景记录产品组合由通用存储转向工业级高耐温芯片\uff0c调整已执行\uff0c交付验收记录显示返工减少。"
        "工业级产品收入占比由上年同季的 30.00% 升至最近季度的 45.00%\uff0c合并范围不变。"
        "关键原材料采购报价同步上升\uff0c新增成本尚未完全转嫁给客户。",
        "45.00",
        "percent",
    ),
    (
        "financial.revenue",
        "合成口径以五年前营业收入为基期 100.00\uff0c期末同合并范围收入指数为 126.40。"
        "期间逐年收入数据未提供。",
        "126.40",
        "index",
    ),
    (
        "financial.margin",
        "合成最近季度毛利率为 38.20%\uff0c上年同季为 35.20%\uff0c合并范围及核算口径相同。"
        "当期采购涨价的原材料尚在库存\uff0c未全部结转成本。",
        "38.20",
        "percent",
    ),
    (
        "valuation.percentile",
        "合成滚动市盈率在同标的五年历史月末观测中处于 54.00 分位。"
        "历史窗口与可比公司均采用最近完整年度扣除非经常损益后的盈利口径。",
        "54.00",
        "percentile",
    ),
    (
        "event.major",
        "合成事件流记录重大产能调整\uff0c新增设备已完成验收\uff0c已签订单覆盖部分计划产出。"
        "其余产能尚无锁定订单\uff0c量产良率和交付进度待验证。",
        None,
        None,
    ),
    (
        "macro.exposure",
        "合成场景的核心业务属于半导体行业\uff0c存在半导体周期暴露。"
        "公司有登记的在研项目与研发支出\uff0c但研发支持资格尚未确认\uff0c未收到补贴。"
        "受限技术范围是否涵盖公司在用设备仍未核实。",
        None,
        None,
    ),
    (
        "financial.cash_flow",
        "合成最近完整年度经营现金流净额为 120.00 百万元\uff0c"
        "净利润为 100.00 百万元\uff0c合并范围一致。"
        "经营现金流含客户预付款\uff0c不能直接当作可持续回款\uff1b"
        "应收账款中逾期超过一年占比为 8.00%。",
        "120.00",
        "CNY_million",
    ),
    (
        "competitive.substitution",
        "合成客户验证记录显示工业级芯片满足既有设备的耐温要求\uff0c更换芯片需要客户重新认证。"
        "同行替代产品已进入部分客户的样品验证\uff0c客户未承诺独家采购\uff0c重新认证并不保证持续留存。",
        None,
        None,
    ),
    (
        "competitive.pricing",
        "合成最近年度同口径续约记录显示存量工业客户保持采购\uff0c标准产品报价受到竞争对手压价影响。"
        "公司尚未证明新产品能够在竞争性采购中维持报价。",
        None,
        None,
    ),
    (
        "management.capital_allocation",
        "合成董事会记录显示新增设备投资分阶段审批\uff0c后续支出与验收和订单落实挂钩。"
        "管理层曾暂停缺少订单支持的扩产项目\uff0c同时核心客户拓展里程碑已延期。",
        None,
        None,
    ),
    (
        "governance.oversight",
        "合成治理记录列明关联采购由独立委员审议\uff0c重大投资执行情况定期向董事会报告。"
        "董事会尚未完成对延期客户项目的责任复盘\uff0c管理层执行与监督效果仍需跟踪。",
        None,
        None,
    ),
    (
        "valuation.comparability",
        "合成最近年度同口径滚动市盈率为 24.00 倍\uff0c主营相近的同行样本中位数为 22.00 倍。"
        "同行均使用扣除非经常损益后的年度盈利\uff0c但产品"
        "结构和增长阶段不同\uff0c估值差异不能单独证明价格"
        "便宜或昂贵。",
        "24.00",
        "multiple",
    ),
    (
        "risk.liquidity",
        "合成最近年末非受限现金为 180.00 百万元\uff0c未来一年到期有息债务为 100.00 百万元。"
        "同一期间已承诺资本支出为 90.00 百万元\uff1b"
        "银行授信尚未提款且续期未承诺\uff0c不能等同现金。",
        "180.00",
        "CNY_million",
    ),
    (
        "risk.supply",
        "合成采购记录显示关键晶圆产能来自外部代工厂\uff0c公司保留安全库存。"
        "备用供应商尚未完成全部产品认证\uff0c安全库存消耗后可能发生交付中断。",
        None,
        None,
    ),
)


# Independent scenario inputs, never company facts or model answers. v3/v4 stay unchanged.
_SCENARIO_IDS: Final = {
    "XBSE:920227": "cashflow-improving",
    "XSHE:301596": "growth-consuming-cash",
    "XSHG:600078": "customer-concentration",
    "XSHG:688311": "substitute-competition",
    "XSHE:001289": "capital-allocation-divergence",
    "XSHG:603699": "liquidity-stress",
    "XSHG:603729": "cycle-reversal",
    "XSHG:688186": "valuation-comparability",
    "XSHE:000762": "missing-competition-management",
    "XSHE:300225": "missing-bear-evidence",
}
_SCENARIO_FACTS: Final = {
    "cashflow-improving": (
        "financial.cash_flow",
        "合成最近年度净利润为 100.00 百万元\uff0c经营现金流为 1"
        "50.00 百万元\uff0c上年经营现金流为 80.00 百万元。改"
        "善来自应收款回收\uff1b其中一笔长期拖欠款已一次性收回\uff0c该项回收不"
        "能重复。",
        "150.00",
        "CNY_million",
    ),
    "growth-consuming-cash": (
        "financial.cash_flow",
        "合成最近年度收入同比增长 30.00%\uff0c净利"
        "润为 100.00 百万元\uff0c经营现金流为 -"
        "40.00 百万元。客户账期延长及备货占用了"
        "现金\uff1b已签订单尚未全部交付\uff0c不能将其等同收款"
        "。",
        "-40.00",
        "CNY_million",
    ),
    "customer-concentration": (
        "business.revenue_mix",
        "合成公司销售工业芯片\uff0c前三大客户贡献最近年"
        "度收入的 70.00%\uff0c最大客户占 45.0"
        "0%。已续签采购框架\uff0c但客户可按月调整订单\uff1b"
        "现有规模有助于批量生产\uff0c亦使需求集中。",
        "70.00",
        "percent",
    ),
    "substitute-competition": (
        "competitive.substitution",
        "合成测试显示原产品在高温稳定性上仍优于替代方"
        "案\uff1b替代产品报价低 20.00%\uff0c已通过两家"
        "客户的通用工况认证\uff0c高温工况尚未通过。两种方"
        "案对应的适用场景不同。",
        "20.00",
        "percent",
    ),
    "capital-allocation-divergence": (
        "management.capital_allocation",
        "合成董事会记录显示最近年度维护性资本支出 40.00 百万元"
        "\uff0c扩张支出 120.00 百万元。扩张项目的实际投产比原计划"
        "延迟两个季度\uff1b旧产线维护项目已按期完成。管理层暂停新增扩张承"
        "诺并继续维护投入。",
        "120.00",
        "CNY_million",
    ),
    "liquidity-stress": (
        "risk.liquidity",
        "合成最近年末非受限现金 60.00 百万元\uff0c未来一年到期有息"
        "债务 140.00 百万元\uff0c已承诺资本支出 50.00 百万"
        "元。银行仅续签其中 40.00 百万元贷款\uff0c未续签部分尚无确"
        "定融资。",
        "60.00",
        "CNY_million",
    ),
    "cycle-reversal": (
        "financial.margin",
        "合成最近季度毛利率 22.00%\uff0c上年同季 "
        "35.00%\uff0c合并及核算口径相同。产品售价下"
        "行时高价库存已结转\uff1b期末新采购报价低于上年同"
        "期\uff0c但销售价格是否企稳尚无记录。",
        "22.00",
        "percent",
    ),
    "valuation-comparability": (
        "valuation.comparability",
        "合成公司最近年度市盈率 18.00 倍\uff0c同行"
        "样本中位数 15.00 倍。公司盈利含一次性"
        "资产处置收益\uff0c同行按扣非盈利计算\uff1b未提供可统"
        "一口径的数据\uff0c两者不能直接判断相对便宜。",
        "18.00",
        "multiple",
    ),
}
_POSITIVE_ONLY_FACTS: Final = (
    (
        "business.revenue_mix",
        "合成公司销售标准化工业零件\uff0c最近年度交付均有验收记录及对应回款。",
        None,
        None,
    ),
    (
        "business.change",
        "合成最近年度生产线改造已验收\uff0c同口径返工率由 4.00% 降至 2.00%。",
        "2.00",
        "percent",
    ),
    (
        "financial.cash_flow",
        "合成最近年度净利润 100.00 百万元\uff0c经"
        "营现金流 130.00 百万元\uff0c均为同一合并"
        "范围年度数据。",
        "130.00",
        "CNY_million",
    ),
    (
        "competitive.substitution",
        "合成同工况盲测中现有产品耐久时长超过参测替代品\uff0c测试记录及样品批次完整。",
        None,
        None,
    ),
    (
        "management.capital_allocation",
        "合成已立项设备维护按批准预算完成\uff0c验收和付款均经过独立复核。",
        None,
        None,
    ),
    (
        "valuation.comparability",
        "合成公司与同产品同行均使用扣非年度盈利。公司市盈率 16.00 倍\uff0c同行中位数 20.00 倍。",
        "16.00",
        "multiple",
    ),
    (
        "risk.liquidity",
        "合成最近年末非受限现金 200.00 百万元"
        "\uff0c未来一年全部到期债务 40.00 百万元\uff0c"
        "已承诺资本支出 30.00 百万元。",
        "200.00",
        "CNY_million",
    ),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def synthetic_evidence_packet(
    *,
    instrument_id: str,
    instrument_name: str,
    as_of: datetime,
    include_builtin_api_gap: bool = False,
    fixture_version: Literal["v3", "v4"] = "v4",
) -> ResearchEvidencePacket:
    """Build a PIT-safe packet with two independent synthetic source roles."""

    version = f"r2-original-synthetic.{fixture_version}"
    scenario_facts: tuple[tuple[str, str, str | None, str | None], ...] = (
        _V3_FACTS if fixture_version == "v3" else _FACTS
    )
    scenario = _SCENARIO_IDS.get(instrument_id) if fixture_version == "v4" else None
    if scenario:
        version = f"r2-scenario-v1.{scenario}"
        if scenario == "missing-bear-evidence":
            scenario_facts = _POSITIVE_ONLY_FACTS
        elif scenario == "missing-competition-management":
            scenario_facts = tuple(
                item
                for item in _FACTS
                if not item[0].startswith(("competitive.", "management.", "governance."))
            )
        else:
            replacement = _SCENARIO_FACTS[scenario]
            scenario_facts = tuple(
                replacement if item[0] == replacement[0] else item for item in _FACTS
            )
    if scenario == "valuation-comparability":
        scenario_facts = tuple(
            (
                "valuation.percentile",
                "合成五年历史月末市盈率按扣非盈利计算。"
                "本期仅有包含一次性收益的盈利口径\uff0c无法计算可比历史分位。",
                None,
                None,
            )
            if item[0] == "valuation.percentile"
            else item
            for item in scenario_facts
        )
    coverage_start = as_of - timedelta(days=365 * 5)
    citations: list[ResearchEvidenceCitation] = []
    facts: list[ResearchEvidenceFact] = []
    evidence_ids_by_fact_key: dict[str, UUID] = {}
    for index, (key, statement, value, unit) in enumerate(scenario_facts):
        evidence_id = uuid5(
            _NAMESPACE,
            _canonical(
                {
                    "fixture_version": version,
                    "instrument_id": instrument_id,
                    "as_of": as_of.isoformat(),
                    "key": key,
                    "statement": statement,
                    "value": value,
                    "unit": unit,
                }
            ).decode("utf-8"),
        )
        evidence_ids_by_fact_key[key] = evidence_id
        known_at = as_of - timedelta(days=max(1, 120 - index * 11))
        role = "synthetic_statutory" if index % 2 == 0 else "synthetic_market_reference"
        citations.append(
            ResearchEvidenceCitation(
                evidence_id=evidence_id,
                source_provider_id=(
                    "r2_synthetic_statutory" if index % 2 == 0 else "r2_synthetic_market_reference"
                ),
                independent_source_group=(
                    "synthetic_disclosure_group" if index % 2 == 0 else "synthetic_market_group"
                ),
                source_role=role,
                source_label=f"R2 original synthetic source {1 + index % 2}",
                published_at=known_at,
                first_seen_at=known_at,
                revision_known_at=known_at,
                locator=(
                    f"synthetic://r2/{version if scenario else fixture_version}/"
                    f"{instrument_id}/{key}"
                ),
            )
        )
        facts.append(
            ResearchEvidenceFact(
                key=key,
                statement=statement,
                value=value,
                unit=unit,
                evidence_id=evidence_id,
            )
        )
    manifest = {
        "instrument_id": instrument_id,
        "as_of": as_of.isoformat(),
        "facts": [
            item.model_dump(mode="json", exclude={"data_audit_status", "source_location", "period"})
            for item in facts
        ],
        "fixture_version": version,
    }
    coverage_gaps = [
        CoverageGap(
            area="listing_history",
            reason_code="SYNTHETIC_EVENT_SAMPLE_BOUNDED",
            detail="上市以来事件扫描为原创合成样本, 不代表真实完整历史。",
            requested_start=coverage_start,
            actual_start=coverage_start,
            actual_end=as_of,
            evidence_ids=[item.evidence_id for item in citations],
        )
    ]
    if fixture_version == "v4":
        coverage_gaps.append(
            CoverageGap(
                area="research.synthetic_scenario",
                reason_code="SYNTHETIC_SCENARIO_NOT_COMPANY_FACTS",
                detail=(
                    (
                        f"本报告使用独立合成情景 {scenario} (版本 {version}), "
                        "仅用于验证研究与评分流程; "
                        if scenario
                        else "本报告使用通用半导体合成案例\uff0c仅用于验证研究与评分流程\uff1b"
                    )
                    + "所选证券名称和代码仅标识本次会话\uff0c场景业务、财务、行业暴露和评分"
                    "均不代表该真实公司。真实公司六维资料尚未接入本次研究。"
                ),
            )
        )
    if include_builtin_api_gap and fixture_version == "v3":
        coverage_gaps.extend(
            CoverageGap(
                area=area,
                reason_code="REQUIRED_FIELD_MISSING",
                detail=detail,
                requested_start=coverage_start,
                actual_start=coverage_start,
                actual_end=as_of,
                evidence_ids=[item.evidence_id for item in citations],
            )
            for area, detail in (
                (
                    "business.core_products",
                    "内置资料未说明公司的核心产品或服务, 允许当前研究受控补证。",
                ),
                (
                    "business.customer_revenue_chain",
                    "内置资料未完整说明客户类型及收入、利润形成链条, 允许当前研究受控补证。",
                ),
                (
                    "business.product_structure_change",
                    "内置资料未说明具体产品调整前后的收入构成, 允许当前研究受控补证。",
                ),
            )
        )
    return ResearchEvidencePacket(
        schema_version="1.0",
        instrument_id=instrument_id,
        instrument_name=instrument_name,
        as_of=as_of,
        coverage_start=coverage_start,
        coverage_end=as_of,
        coverage_gaps=coverage_gaps,
        facts=facts,
        citations=citations,
        exposure_profile=None
        if scenario == "missing-bear-evidence"
        else ResearchExposureProfile(
            instrument_id=instrument_id,
            company_id=f"company:{instrument_id}",
            industry_ids=["industry:semiconductor"],
            region_ids=["region:china"],
            edges=[
                ResearchExposureEdge(
                    from_entity_id=f"company:{instrument_id}",
                    to_entity_id="industry:semiconductor",
                    directness=ResearchExposureDirectness.ONE_HOP_EVIDENCE,
                    hop_count=1,
                    evidence_ids=[evidence_ids_by_fact_key["macro.exposure"]],
                )
            ],
        ),
        data_manifest_sha256=hashlib.sha256(_canonical(manifest)).hexdigest(),
    )
