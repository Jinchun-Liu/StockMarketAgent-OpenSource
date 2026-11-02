"""Deterministic company-quality scoring and reader Markdown compilation."""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from stock_assistant.contracts.research import (
    ClaimType,
    CoverageGap,
    ResearchClaim,
    ResearchEvidenceAcquisitionMode,
    ResearchEvidenceCitation,
    ResearchEvidencePacket,
    ResearchEvidenceSourceLevel,
    ResearchEvidenceVerificationStatus,
    ResearchReportSection,
    ResearchSectionKind,
)
from stock_assistant.contracts.research_reader import (
    ResearchGlossaryItem,
    ResearchReaderDraft,
    ResearchReaderEdition,
    ResearchScoreAssessmentDraft,
    ResearchScoreAssessmentGrade,
    ResearchScoreBasis,
    ResearchScorecard,
    ResearchScoreDimension,
    ResearchScoreDimensionId,
    ResearchScoreStatus,
    ResearchWebRetrievalDisclosure,
    score_band_for,
)

SCORING_RULE_VERSION: Final = "company-quality-score.v4"
READER_COMPILER_VERSION: Final = "safe-reader-markdown.v6"
_HUNDRED: Final = Decimal("100")
_FIFTY: Final = Decimal("50")
_WEIGHTS: Final = {
    ResearchScoreDimensionId.BUSINESS_QUALITY: Decimal("0.20"),
    ResearchScoreDimensionId.FINANCIAL_QUALITY: Decimal("0.20"),
    ResearchScoreDimensionId.COMPETITIVE_DURABILITY: Decimal("0.15"),
    ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION: Decimal("0.10"),
    ResearchScoreDimensionId.VALUATION_CONTEXT: Decimal("0.15"),
    ResearchScoreDimensionId.RISK_RESILIENCE: Decimal("0.20"),
}
_NAMES: Final = {
    ResearchScoreDimensionId.BUSINESS_QUALITY: "商业模式与业务质量",
    ResearchScoreDimensionId.FINANCIAL_QUALITY: "财务质量与持续性",
    ResearchScoreDimensionId.COMPETITIVE_DURABILITY: "竞争优势与耐久性",
    ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION: "管理层与资本配置",
    ResearchScoreDimensionId.VALUATION_CONTEXT: "历史估值位置",
    ResearchScoreDimensionId.RISK_RESILIENCE: "风险韧性与可证伪性",
}
_GRADE_VALUES: Final = {
    ResearchScoreAssessmentGrade.MATERIAL_WEAKNESS: 0,
    ResearchScoreAssessmentGrade.WEAKNESS: 1,
    ResearchScoreAssessmentGrade.BALANCED: 2,
    ResearchScoreAssessmentGrade.STRENGTH: 3,
    ResearchScoreAssessmentGrade.EXCEPTIONAL: 4,
}
_FACT_PREFIX_DIMENSIONS: Final = {
    "business.": ResearchScoreDimensionId.BUSINESS_QUALITY,
    "financial.": ResearchScoreDimensionId.FINANCIAL_QUALITY,
    "competitive.": ResearchScoreDimensionId.COMPETITIVE_DURABILITY,
    "industry.": ResearchScoreDimensionId.COMPETITIVE_DURABILITY,
    "management.": ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION,
    "governance.": ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION,
    "capital_allocation.": ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION,
    "valuation.": ResearchScoreDimensionId.VALUATION_CONTEXT,
    "risk.": ResearchScoreDimensionId.RISK_RESILIENCE,
    "event.": ResearchScoreDimensionId.RISK_RESILIENCE,
    "external_event.": ResearchScoreDimensionId.RISK_RESILIENCE,
    "macro.": ResearchScoreDimensionId.RISK_RESILIENCE,
    "conflict.": ResearchScoreDimensionId.RISK_RESILIENCE,
}
PUBLIC_ANALYSIS_STEPS: Final = (
    "确认证券身份和分析时间截点",
    "检查历史数据覆盖与缺口",
    "理解公司如何创造收入和现金",
    "比较财务质量、行业位置和重要事件",
    "分别寻找支持证据和反证",
    "按统一规则计算质量分与证据可信度",
    "列出未知项、证伪条件和仍需核验的问题",
)


def has_valuation_subject(statement: str) -> bool:
    """Necessary topic evidence, never proof of a usable valuation comparison.

    Profitability, cash distributions and a bare share price do not establish
    valuation. Dates, actual versus missing values, denominator consistency and
    historical/comparable benchmarks still require independent semantic audit.
    """

    return (
        re.search(
            r"市盈率|市净率|市销率|市现率|股息率|盈利收益率|自由现金流收益率"
            r"|估值(?:倍数|分位|口径|区间)|企业价值倍数|现金流折现|折现现金流"
            r"|(?<![A-Za-z])(?:P\s*/?\s*[EBS]|EV\s*/\s*[A-Za-z]+"
            r"|PEG|DCF)(?![A-Za-z])|price[- ]to[- ](?:earnings|book|sales)"
            r"|dividend yield|earnings yield|valuation percentile"
            # A written quotient can establish the valuation subject without an
            # acronym. Bounded operands stay within one source clause; this does
            # not calculate the quotient or establish comparable denominators.
            r"|(?:企业价值|总?市值|股价|收盘价)[^。;\uff1b\n]{0,80}"
            r"(?:[/\u00f7]|除以)[^。;\uff1b\n]{0,80}"
            r"(?:利润|收益|净资产|收入|现金流)",
            unicodedata.normalize("NFKC", statement),
            re.IGNORECASE,
        )
        is not None
    )


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _reliability(citations: list[ResearchEvidenceCitation]) -> Decimal:
    if not citations:
        return Decimal("0")
    if any(
        item.verification_status is ResearchEvidenceVerificationStatus.UNVERIFIED
        for item in citations
    ):
        return Decimal("0")
    independent_groups = {item.independent_source_group for item in citations}
    if len(independent_groups) >= 2 and all(
        item.verification_status is ResearchEvidenceVerificationStatus.INDEPENDENTLY_VERIFIED
        for item in citations
    ):
        return Decimal("1.00")
    if any(
        item.source_level is ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL for item in citations
    ):
        return Decimal("1.00")
    if any(item.source_level is ResearchEvidenceSourceLevel.SYNTHETIC for item in citations):
        return Decimal("0")
    if any(item.source_level is ResearchEvidenceSourceLevel.COMPANY_OFFICIAL for item in citations):
        return Decimal("0.90")
    if any(
        item.source_level is ResearchEvidenceSourceLevel.TRUSTED_MEDIA_DATA for item in citations
    ):
        return Decimal("0.70")
    return Decimal("0")


def build_score_evidence_allowlist(
    *,
    packet: ResearchEvidencePacket,
    claims: list[ResearchClaim],
    scoring_basis: ResearchScoreBasis = ResearchScoreBasis.REAL_EVIDENCE,
    require_data_audit: bool = False,
) -> dict[ResearchScoreDimensionId, frozenset[UUID]]:
    """Assign only eligible, published facts to their scoring dimensions."""

    published_ids = {
        citation.evidence_id
        for claim in claims
        for citation in (*claim.evidence, *claim.counter_evidence)
    }
    citations = {item.evidence_id: item for item in packet.citations}
    allowed: dict[ResearchScoreDimensionId, set[UUID]] = {
        dimension: set() for dimension in ResearchScoreDimensionId
    }
    for fact in packet.facts:
        if require_data_audit and fact.data_audit_status not in {"passed", "api_validated"}:
            continue
        dimension = score_fact_dimension(fact.key)
        citation = citations.get(fact.evidence_id)
        if dimension is None or citation is None or fact.evidence_id not in published_ids:
            continue
        if scoring_basis is ResearchScoreBasis.SYNTHETIC_SCENARIO:
            if (
                citation.source_level is not ResearchEvidenceSourceLevel.SYNTHETIC
                or citation.acquisition_mode is not ResearchEvidenceAcquisitionMode.BUILT_IN_API
            ):
                continue
        elif (
            citation.source_level is ResearchEvidenceSourceLevel.SYNTHETIC
            or citation.verification_status is ResearchEvidenceVerificationStatus.UNVERIFIED
        ):
            continue
        allowed[dimension].add(fact.evidence_id)
    return {dimension: frozenset(values) for dimension, values in allowed.items()}


def score_fact_dimension(fact_key: str) -> ResearchScoreDimensionId | None:
    return next(
        (
            mapped
            for prefix, mapped in _FACT_PREFIX_DIMENSIONS.items()
            if fact_key.startswith(prefix)
        ),
        None,
    )


def build_scorecard(
    *,
    assessment: ResearchScoreAssessmentDraft,
    claims: list[ResearchClaim],
    evidence_allowlist: dict[ResearchScoreDimensionId, frozenset[UUID]],
    scoring_basis: ResearchScoreBasis = ResearchScoreBasis.REAL_EVIDENCE,
    rejected_dimensions: set[ResearchScoreDimensionId] | None = None,
) -> ResearchScorecard:
    citations = {
        citation.evidence_id: citation
        for claim in claims
        for citation in (*claim.evidence, *claim.counter_evidence)
    }
    dimensions: list[ResearchScoreDimension] = []
    for dimension, item in assessment.dimension_items():
        evidence_ids = list(dict.fromkeys(item.evidence_ids))
        counter_ids = list(dict.fromkeys(item.counter_evidence_ids))
        all_ids = list(dict.fromkeys([*evidence_ids, *counter_ids]))
        allowed_ids = evidence_allowlist.get(dimension, frozenset())
        if any(evidence_id not in allowed_ids for evidence_id in all_ids):
            raise ValueError("score evidence is not allowed for this dimension")
        selected = [citations[evidence_id] for evidence_id in all_ids if evidence_id in citations]
        if len(selected) != len(all_ids):
            raise ValueError("score evidence must resolve to published claim citations")
        if dimension in (rejected_dimensions or set()):
            dimensions.append(
                ResearchScoreDimension(
                    schema_version="1.0",
                    dimension=dimension,
                    display_name=_NAMES[dimension],
                    weight=_WEIGHTS[dimension],
                    reasons=["该维度的判断或理由尚未通过独立证据核验。"],
                    evidence_ids=evidence_ids,
                    counter_evidence_ids=counter_ids,
                    unscored_reason="支持核验未通过或尚未完成\uff0c本维度未评分。",
                )
            )
            continue
        if item.grade is ResearchScoreAssessmentGrade.ABSTAIN:
            dimensions.append(
                ResearchScoreDimension(
                    schema_version="1.0",
                    dimension=dimension,
                    display_name=_NAMES[dimension],
                    weight=_WEIGHTS[dimension],
                    reasons=item.reasons,
                    evidence_ids=evidence_ids,
                    counter_evidence_ids=counter_ids,
                    unscored_reason="该维度证据不足, 模型已弃权。",
                )
            )
            continue
        if scoring_basis is ResearchScoreBasis.SYNTHETIC_SCENARIO:
            if any(
                citation.source_level is not ResearchEvidenceSourceLevel.SYNTHETIC
                or citation.acquisition_mode is not ResearchEvidenceAcquisitionMode.BUILT_IN_API
                for citation in selected
            ):
                raise ValueError("synthetic scoring requires synthetic snapshot citations")
            factor = Decimal("1")
        else:
            factor = _reliability(selected)
        if factor == 0:
            dimensions.append(
                ResearchScoreDimension(
                    schema_version="1.0",
                    dimension=dimension,
                    display_name=_NAMES[dimension],
                    weight=_WEIGHTS[dimension],
                    reasons=item.reasons,
                    evidence_ids=evidence_ids,
                    counter_evidence_ids=counter_ids,
                    unscored_reason="来源、时间或独立性未达到评分门槛。",
                )
            )
            continue
        grade = _GRADE_VALUES[item.grade]
        raw = Decimal(grade) * Decimal("25")
        adjusted = _FIFTY + (raw - _FIFTY) * factor
        dimensions.append(
            ResearchScoreDimension(
                schema_version="1.0",
                dimension=dimension,
                display_name=_NAMES[dimension],
                weight=_WEIGHTS[dimension],
                evidence_grade=grade,
                raw_score=_quantize(raw),
                reliability_factor=factor,
                adjusted_score=_quantize(adjusted),
                reasons=item.reasons,
                evidence_ids=evidence_ids,
                counter_evidence_ids=counter_ids,
            )
        )
    evidence_confidence = _quantize(
        sum(
            (
                (dimension.reliability_factor or Decimal("0")) * _WEIGHTS[dimension.dimension]
                for dimension in dimensions
            ),
            Decimal("0"),
        )
        * _HUNDRED
    )
    all_scored = all(item.adjusted_score is not None for item in dimensions)
    synthetic = scoring_basis is ResearchScoreBasis.SYNTHETIC_SCENARIO
    confidence_meaning = (
        "真实来源核验\uff1a不适用\uff08合成\uff09。场景计算系数为一\uff0c不代表真实来源可信度。"
        if synthetic
        else "该数值只衡量证据覆盖、独立性和可追溯性。"
    )
    if synthetic:
        evidence_confidence = Decimal("0")
    if not all_scored:
        return ResearchScorecard(
            schema_version="1.1",
            scoring_basis=scoring_basis,
            status=ResearchScoreStatus.NOT_SCORED,
            score_meaning="事实覆盖尚未满足各项评分维度的最低门槛, 暂不发布总分。",
            evidence_confidence=evidence_confidence,
            evidence_confidence_meaning=confidence_meaning,
            dimensions=dimensions,
            scoring_rule_version=SCORING_RULE_VERSION,
        )
    overall = _quantize(
        sum(
            (
                ((dimension.adjusted_score or Decimal("0")) * _WEIGHTS[dimension.dimension])
                for dimension in dimensions
            ),
            Decimal("0"),
        )
    )
    band, meaning = score_band_for(overall)
    return ResearchScorecard(
        schema_version="1.1",
        scoring_basis=scoring_basis,
        status=ResearchScoreStatus.SCORED,
        overall_score=overall,
        score_band=band,
        score_meaning=("模拟总分按合成场景设定计算\uff0c不代表真实公司质量。" + meaning)
        if synthetic
        else meaning,
        evidence_confidence=evidence_confidence,
        evidence_confidence_meaning=confidence_meaning,
        dimensions=dimensions,
        scoring_rule_version=SCORING_RULE_VERSION,
    )


def build_legacy_unscored_scorecard() -> ResearchScorecard:
    """Represent retained 1.0 reports without inventing retrospective model grades."""

    return ResearchScorecard(
        schema_version="1.0",
        status=ResearchScoreStatus.NOT_SCORED,
        score_meaning="该历史报告生成时尚未包含当前评分结构, 因此暂不发布总分。",
        evidence_confidence=Decimal("0"),
        evidence_confidence_meaning="历史报告未记录当前评分所需的完整方法与来源回执。",
        dimensions=[
            ResearchScoreDimension(
                schema_version="1.0",
                dimension=dimension,
                display_name=_NAMES[dimension],
                weight=_WEIGHTS[dimension],
                unscored_reason="历史报告缺少当前评分版本所需的结构化输入。",
            )
            for dimension in ResearchScoreDimensionId
        ],
        scoring_rule_version=f"{SCORING_RULE_VERSION}.legacy-compatibility",
    )


def build_model_unavailable_scorecard(
    *, scoring_basis: ResearchScoreBasis = ResearchScoreBasis.REAL_EVIDENCE
) -> ResearchScorecard:
    """Publish an explicit unscored result when optional model scoring is unavailable."""

    return ResearchScorecard(
        schema_version="1.1",
        scoring_basis=scoring_basis,
        status=ResearchScoreStatus.NOT_SCORED,
        score_meaning="模型评分不可用, 暂不发布总分。",
        evidence_confidence=Decimal("0"),
        evidence_confidence_meaning="评分尚未完成\uff0c未取得可核验的模型结果\uff1b现有事实仍按原证据展示。",
        dimensions=[
            ResearchScoreDimension(
                schema_version="1.0",
                dimension=dimension,
                display_name=_NAMES[dimension],
                weight=_WEIGHTS[dimension],
                unscored_reason="评分尚未完成\uff0c未取得可核验的模型结果。",
            )
            for dimension in ResearchScoreDimensionId
        ],
        scoring_rule_version=f"{SCORING_RULE_VERSION}.model-unavailable",
    )


def build_synthetic_unscored_scorecard() -> ResearchScorecard:
    """Keep synthetic workflow checks separate from real-company assessment."""

    return ResearchScorecard(
        schema_version="1.0",
        status=ResearchScoreStatus.NOT_SCORED,
        score_meaning="合成资料只验证研究流程, 不评价真实公司质量, 因此不发布数字分。",
        evidence_confidence=Decimal("0"),
        evidence_confidence_meaning="合成资料的证据可信度固定为零, 不代表真实来源覆盖。",
        dimensions=[
            ResearchScoreDimension(
                schema_version="1.0",
                dimension=dimension,
                display_name=_NAMES[dimension],
                weight=_WEIGHTS[dimension],
                unscored_reason="合成资料不用于真实公司评分。",
            )
            for dimension in ResearchScoreDimensionId
        ],
        scoring_rule_version=f"{SCORING_RULE_VERSION}.synthetic-not-scored",
    )


def _escape_markdown(value: str) -> str:
    normalized = " ".join(value.split())
    for character in ("\\", "`", "*", "_", "{", "}", "[", "]", "<", ">", "#", "|"):
        normalized = normalized.replace(character, f"\\{character}")
    return normalized


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {_escape_markdown(value)}" for value in values) or "- 暂无可核验内容"


def compile_data_audit_disclosure(
    reader: ResearchReaderEdition, packet: ResearchEvidencePacket
) -> ResearchReaderEdition:
    citations = {item.evidence_id: item for item in packet.citations}
    labels = {
        ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL: "交易所或监管披露",
        ResearchEvidenceSourceLevel.COMPANY_OFFICIAL: "公司官网",
        ResearchEvidenceSourceLevel.TRUSTED_MEDIA_DATA: "登记媒体",
    }
    lines = [
        "## 资料审核与原文位置",
        "DEV_ONLY / UNVERIFIED。审核通过表示原文支持, 不代表正式来源授权或独立交叉验证。",
    ]
    for fact in packet.facts:
        citation = citations[fact.evidence_id]
        status = "独立资料审核通过" if fact.data_audit_status == "passed" else "接口字段已核验"
        lines.append(
            "- "
            + _escape_markdown(
                f"{fact.key}: {status}; "
                f"{labels.get(citation.source_level, citation.source_level.value)}; "
                f"证据 {fact.evidence_id}; 原文 {fact.source_location or '位置未记录'}; "
                f"期间 {fact.period or '非数值事实, 参见原文'}。"
            )
        )
    return ResearchReaderEdition.model_validate(
        {
            **reader.model_dump(),
            "safe_markdown": reader.safe_markdown + "\n\n" + "\n\n".join(lines),
        }
    )


def ordered_unique_coverage_gaps(gaps: list[CoverageGap]) -> list[CoverageGap]:
    """Keep the first complete occurrence of each gap without reordering evidence."""

    unique: list[CoverageGap] = []
    seen: set[str] = set()
    for gap in gaps:
        identity = gap.model_dump_json(exclude_none=False)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(gap)
    return unique


def _coverage_gap_markdown(gaps: list[CoverageGap]) -> str:
    if not gaps:
        return "当前未记录额外证据缺口。"
    visible = gaps[:20]
    lines = [f"- {_escape_markdown(item.detail)}" for item in visible]
    if len(gaps) > len(visible):
        lines.append("- 其余缺口请在页面的完整缺口列表中核对。")
    return "\n".join(lines)


def compile_reader_edition(
    *,
    instrument_name: str,
    draft: ResearchReaderDraft,
    scorecard: ResearchScorecard,
    claim_ids: list[UUID],
    coverage_gaps: list[CoverageGap],
) -> ResearchReaderEdition:
    synthetic_not_scored = (
        scorecard.overall_score is None and "synthetic" in scorecard.scoring_rule_version
    )
    score_line = (
        f"{scorecard.overall_score} 分 · {scorecard.score_band}"
        if scorecard.overall_score is not None
        else "暂不评分"
    )
    confidence_line = (
        "合成资料不计分" if synthetic_not_scored else f"{scorecard.evidence_confidence} 分"
    )
    glossary = (
        "\n".join(
            f"- **{_escape_markdown(item.term)}**: {_escape_markdown(item.explanation)}"
            for item in draft.glossary
        )
        or "- 本次没有新增需要解释的术语"
    )
    normalized_gaps = ordered_unique_coverage_gaps(coverage_gaps)
    markdown = "\n\n".join(
        (
            f"# {_escape_markdown(instrument_name)}研究简明版",
            f"**综合质量: {score_line}**  \n**证据可信度: {confidence_line}**",
            f"> {_escape_markdown(draft.one_sentence_takeaway)}",
            "## 公司靠什么赚钱\n\n" + _bullets(draft.how_the_company_makes_money),
            "## 为什么得到这个分\n\n" + _bullets(draft.why_this_score),
            "## 证据缺口与补充状态\n\n" + _coverage_gap_markdown(normalized_gaps),
            "## 主要优势\n\n" + _bullets(draft.main_strengths),
            "## 主要风险\n\n" + _bullets(draft.main_risks),
            "## 简单分析过程\n\n"
            + "\n".join(
                f"{index}. {_escape_markdown(step)}"
                for index, step in enumerate(PUBLIC_ANALYSIS_STEPS, start=1)
            ),
            "## 仍不知道什么\n\n" + _bullets(draft.unknowns),
            "## 词语解释\n\n" + glossary,
            f"> {scorecard.recommendation_notice}",
        )
    )
    return ResearchReaderEdition(
        schema_version="1.0",
        one_sentence_takeaway=draft.one_sentence_takeaway,
        how_the_company_makes_money=draft.how_the_company_makes_money,
        why_this_score=draft.why_this_score,
        main_strengths=draft.main_strengths,
        main_risks=draft.main_risks,
        public_analysis_steps=list(PUBLIC_ANALYSIS_STEPS),
        unknowns=draft.unknowns,
        glossary=[
            ResearchGlossaryItem(term=item.term, explanation=item.explanation)
            for item in draft.glossary
        ],
        claim_ids=list(dict.fromkeys(claim_ids)),
        safe_markdown=markdown,
        compiler_version=READER_COMPILER_VERSION,
    )


def compile_complete_reader_edition(
    *,
    instrument_name: str,
    sections: list[ResearchReportSection],
    claims: list[ResearchClaim],
    scorecard: ResearchScorecard,
    coverage_gaps: list[CoverageGap],
    research_complete: bool = True,
) -> ResearchReaderEdition:
    """Arrange validated research without a second rewrite or dropping detailed arguments."""

    by_kind = {section.kind: section for section in sections}
    by_id = {claim.claim_id: claim for claim in claims}
    synthesis = by_kind[ResearchSectionKind.CONFLICTS_AND_UNKNOWNS].summary

    def claim_markdown(claim: ResearchClaim) -> str:
        label = {
            ClaimType.FACT: "事实",
            ClaimType.SYSTEM_INFERENCE: "系统推断",
            ClaimType.MODEL_HYPOTHESIS: "条件化分析",
        }[claim.claim_type]
        parts = [
            f"**{label}**\u3000{_escape_markdown(claim.statement)}",
            f"[查看依据](#claim-{claim.claim_id})",
        ]
        if claim.uncertainty:
            parts.append("判断的限制\uff1a" + _escape_markdown(claim.uncertainty))
        if claim.falsification_conditions:
            parts.append(
                "出现以下情况时\uff0c需要重新判断\uff1a\n\n"
                + _bullets(claim.falsification_conditions)
            )
        if claim.counter_evidence:
            parts.append(
                "反证来源\uff1a"
                + "\uff1b".join(
                    _escape_markdown(item.source_label) for item in claim.counter_evidence
                )
            )
        elif claim.claim_type is ClaimType.MODEL_HYPOTHESIS:
            parts.append("当前资料未找到直接反证\uff1b这不表示不存在风险或反对意见。")
        if claim.calculation:
            parts.append("推断依据\uff1a" + _escape_markdown(claim.calculation.formula))
            parts.extend(
                _escape_markdown(f"{item.name}\uff1a{item.value} {item.unit or ''}")
                for item in claim.calculation.inputs
            )
        return "\n\n".join(parts)

    def section_markdown(kind: ResearchSectionKind, *, with_title: bool = True) -> str:
        section = by_kind[kind]
        parts = [f"### {_escape_markdown(section.title)}"] if with_title else []
        parts.append(_escape_markdown(section.summary))
        parts.extend(claim_markdown(by_id[identifier]) for identifier in section.claim_ids)
        return "\n\n".join(parts)

    fact_kinds = (
        ResearchSectionKind.TARGET_AND_COVERAGE,
        ResearchSectionKind.BUSINESS_MODEL,
        ResearchSectionKind.FINANCIAL_QUALITY,
        ResearchSectionKind.VALUATION_CONTEXT,
        ResearchSectionKind.MAJOR_EVENTS_AND_MACRO,
    )
    source_lines = []
    seen_sources: set[UUID] = set()
    for claim in claims:
        for citation in (*claim.evidence, *claim.counter_evidence):
            if citation.evidence_id not in seen_sources:
                seen_sources.add(citation.evidence_id)
                source_lines.append(
                    f"- {_escape_markdown(citation.source_label)}\uff1b"
                    f"发布于 {citation.published_at.isoformat()}\uff1b"
                    f"已知修订 {citation.revision_known_at.isoformat()}。"
                    f"[查看依据](#source-{citation.evidence_id})"
                )
    synthetic = scorecard.scoring_basis is ResearchScoreBasis.SYNTHETIC_SCENARIO
    score_lines = [
        ("**模拟总分\uff1a" if synthetic else "**综合质量分\uff1a")
        + (
            f"{scorecard.overall_score} / 100**"
            if scorecard.overall_score is not None
            else "未评分**"
        ),
        _escape_markdown(scorecard.score_meaning),
        "真实来源核验\uff1a不适用\uff08合成\uff09"
        if synthetic
        else f"证据可信度\uff1a{scorecard.evidence_confidence} / 100",
    ]
    for dimension in scorecard.dimensions:
        number = (
            f"{dimension.adjusted_score} / 100"
            if dimension.adjusted_score is not None
            else "未评分"
        )
        score_lines.append(f"### {_escape_markdown(dimension.display_name)}\uff1a{number}")
        score_lines.extend(_escape_markdown(reason) for reason in dimension.reasons)
        if dimension.unscored_reason:
            score_lines.append(_escape_markdown(dimension.unscored_reason))
        for label, identifiers in (
            ("支持依据", dimension.evidence_ids),
            ("反证依据", dimension.counter_evidence_ids),
        ):
            if identifiers:
                score_lines.append(
                    label
                    + "\uff1a"
                    + "\uff1b".join(
                        f"[查看依据](#source-{identifier})" for identifier in identifiers
                    )
                )
    notice = (
        "合成资料\uff0c仅用于验证研究流程\uff0c不代表真实公司结论。"
        if synthetic
        else "研究预览\uff0c仅供研究和纸面验证\uff0c不构成投资建议。"
    )
    markdown = "\n\n".join(
        (
            f"# {_escape_markdown(instrument_name)}研究报告" + (" · 合成模拟" if synthetic else ""),
            f"> {notice}",
            "研究质量\uff1a"
            + ("完整" if research_complete else "部分完成\uff0c核心论点仍有证据缺口。"),
            "## 核心结论\n\n"
            + _escape_markdown(synthesis)
            + "\n\n"
            + "\n\n".join(score_lines)
            + "\n\n主要问题与限制\uff1a\n\n"
            + _coverage_gap_markdown(ordered_unique_coverage_gaps(coverage_gaps)),
            "## 关键事实\n\n" + "\n\n".join(section_markdown(kind) for kind in fact_kinds),
            "## 看多分析\uff08Bull\uff09\n\n"
            + section_markdown(ResearchSectionKind.BULL_CASE, with_title=False),
            "## 看空分析\uff08Bear\uff09\n\n"
            + section_markdown(ResearchSectionKind.BEAR_CASE, with_title=False),
            "## 综合判断\n\n"
            + section_markdown(ResearchSectionKind.CONFLICTS_AND_UNKNOWNS, with_title=False),
            "## 风险与待验证\n\n"
            + section_markdown(ResearchSectionKind.FALSIFICATION_AND_FOLLOW_UP, with_title=False)
            + "\n\n"
            + _bullets([gap.detail for gap in ordered_unique_coverage_gaps(coverage_gaps)]),
            "## 来源与方法\n\n"
            + section_markdown(ResearchSectionKind.EVIDENCE_AND_VERSIONS, with_title=False)
            + "\n\n"
            + "\n".join(source_lines)
            + "\n\n"
            + _escape_markdown(scorecard.score_meaning)
            + "\n\n"
            + _escape_markdown(scorecard.recommendation_notice),
        )
    )
    return ResearchReaderEdition(
        schema_version="1.0",
        one_sentence_takeaway=synthesis[:500],
        how_the_company_makes_money=[by_kind[ResearchSectionKind.BUSINESS_MODEL].summary],
        why_this_score=[scorecard.score_meaning],
        main_strengths=[by_kind[ResearchSectionKind.BULL_CASE].summary],
        main_risks=[by_kind[ResearchSectionKind.BEAR_CASE].summary],
        public_analysis_steps=list(PUBLIC_ANALYSIS_STEPS),
        unknowns=[gap.detail for gap in coverage_gaps[:20]],
        glossary=[],
        claim_ids=[claim.claim_id for claim in claims],
        safe_markdown=markdown,
        compiler_version="safe-reader-markdown.v7",
    )


def build_web_retrieval_disclosure(
    *, citations: list[ResearchEvidenceCitation], receipt_ids: list[UUID]
) -> ResearchWebRetrievalDisclosure:
    web = [item for item in citations if item.acquisition_mode.value == "model_assisted_web"]
    unverified = [
        item
        for item in web
        if item.verification_status is not ResearchEvidenceVerificationStatus.INDEPENDENTLY_VERIFIED
    ]
    count = len(web)
    unverified_count = len(unverified)
    return ResearchWebRetrievalDisclosure(
        schema_version="1.0",
        model_assisted_item_count=count,
        not_independently_verified_count=unverified_count,
        source_levels=sorted({item.source_level.value for item in web}),
        receipt_ids=receipt_ids,
        warning=(
            f"本报告有 {count} 条信息由模型辅助联网取得, 其中 {unverified_count} 条未完成"
            "独立交叉验证, 可能存在误差; 相关评分已按证据等级向中性值收缩。"
        ),
    )


__all__ = [
    "PUBLIC_ANALYSIS_STEPS",
    "READER_COMPILER_VERSION",
    "SCORING_RULE_VERSION",
    "build_legacy_unscored_scorecard",
    "build_model_unavailable_scorecard",
    "build_score_evidence_allowlist",
    "build_scorecard",
    "build_synthetic_unscored_scorecard",
    "build_web_retrieval_disclosure",
    "compile_reader_edition",
    "ordered_unique_coverage_gaps",
]
