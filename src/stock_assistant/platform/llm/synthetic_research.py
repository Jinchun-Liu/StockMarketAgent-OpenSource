"""Offline model adapter used only to prove the R2 product chain."""

from __future__ import annotations

import hashlib
import json
from typing import Final, Literal

from pydantic import TypeAdapter

from stock_assistant.contracts.discovery import (
    ResearchWebNormalizationDraft,
    ResearchWebNormalizedFactDraft,
    ResearchWebQueryPlanDraft,
    ResearchWebQueryPlanItem,
)
from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    GenerationResponse,
    ModelOperation,
    ModelProviderCapability,
    PrivacyLevel,
)
from stock_assistant.contracts.research import (
    ResearchEvidenceBasis,
    ResearchEvidenceFact,
    ResearchHypothesisPatch,
    ResearchHypothesisSelection,
    ResearchNarrativeCandidate,
    ResearchNarrativeDraft,
    ResearchNarrativeHypothesis,
    ResearchNarrativePatch,
    ResearchNarrativeSection,
    ResearchSectionKind,
    ResearchSectionPatch,
    ResearchSupportLeaf,
    ResearchSupportLeafAssessment,
    ResearchSupportLeafItem,
    contains_numeric_expression,
)
from stock_assistant.contracts.research_reader import (
    ResearchScoreAssessmentDraft,
    ResearchScoreAssessmentGrade,
    ResearchScoreCandidate,
    ResearchScoreDimensionId,
    ResearchScoreFactSelection,
)

SYNTHETIC_MODEL: Final = "deterministic-research-narrator"
SYNTHETIC_ADAPTER_VERSION: Final = "synthetic-research-llm.v5"

_SECTION_COPY: Final = {
    ResearchSectionKind.TARGET_AND_COVERAGE: (
        "标的、截点与覆盖",
        "本报告只使用分析截点前可见的合成证据, 覆盖不足处单独列明。",
    ),
    ResearchSectionKind.BUSINESS_MODEL: (
        "业务模式与变化",
        "从收入结构、竞争位置和关键业务变化等角度描述公司, 不外推未知事实。",
    ),
    ResearchSectionKind.FINANCIAL_QUALITY: (
        "财务质量与季度趋势",
        "按主要历史窗口和季度比较口径分析, 实际不足部分保持未知。",
    ),
    ResearchSectionKind.VALUATION_CONTEXT: (
        "历史估值位置",
        "仅展示历史口径和可复算位置, 不生成价格目标或交易建议。",
    ),
    ResearchSectionKind.MAJOR_EVENTS_AND_MACRO: (
        "重大事件、国际事件与宏观暴露",
        "扫描上市以来重大事项和国际官方事件, 只沿直接或单层证据路径形成条件化影响。",
    ),
    ResearchSectionKind.BULL_CASE: (
        "多方论点",
        "积极情景作为可证伪假设呈现, 不对未来回报作保证。",
    ),
    ResearchSectionKind.BEAR_CASE: (
        "空方与风险论点",
        "风险情景同时列出支持证据、反证和触发条件。",
    ),
    ResearchSectionKind.CONFLICTS_AND_UNKNOWNS: (
        "来源冲突与未知项",
        "冲突、单位疑点和无法核验的信息不会被模型补全。",
    ),
    ResearchSectionKind.FALSIFICATION_AND_FOLLOW_UP: (
        "可证伪条件与待核验问题",
        "后续研究应围绕能够推翻当前假设的新证据展开。",
    ),
    ResearchSectionKind.EVIDENCE_AND_VERSIONS: (
        "证据与版本",
        "逐条保留证据时间、来源角色以及模型、规则和校验器版本。",
    ),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _grounded_synthetic_narrative(facts: list[ResearchEvidenceFact]) -> ResearchNarrativeDraft:
    """Original replay prose bound to actual fixture text; never used for live inference."""

    qualitative = [item for item in facts if not contains_numeric_expression(item.statement)]
    hypotheses = []
    if len(qualitative) >= 2:
        supporting, counter = qualitative[:2]
        support = ResearchEvidenceBasis(
            evidence_id=supporting.evidence_id,
            fact_key=str(supporting.model_dump()["key"]),
            excerpt=supporting.statement,
        )
        against = ResearchEvidenceBasis(
            evidence_id=counter.evidence_id,
            fact_key=str(counter.model_dump()["key"]),
            excerpt=counter.statement,
        )
        hypotheses = [
            ResearchNarrativeHypothesis(
                section=ResearchSectionKind.BULL_CASE,
                statement=f"“{supporting.statement}”是积极情景的观察基础\uff1b若这一基础能够延续\uff0c经营韧性才有支撑。但“{counter.statement}”意味着仍需核对变化的实际影响。",
                supporting_evidence_ids=[support.evidence_id],
                counter_evidence_ids=[against.evidence_id],
                supporting_basis=[support],
                counter_basis=[against],
                falsification_conditions=["后续披露显示原有经营基础不再成立。"],
                uncertainty="合成观察不能证明真实公司的经营趋势\uff0c变化的影响仍待核验。",
            ),
            ResearchNarrativeHypothesis(
                section=ResearchSectionKind.BEAR_CASE,
                statement=f"“{counter.statement}”构成需要观察的不确定因素\uff1b若相关变化削弱“{supporting.statement}”所反映的经营基础\uff0c风险才可能扩大。",
                supporting_evidence_ids=[against.evidence_id],
                counter_evidence_ids=[support.evidence_id],
                supporting_basis=[against],
                counter_basis=[support],
                falsification_conditions=["后续披露显示变化未削弱原有经营基础。"],
                uncertainty="该风险属于有条件的合成情景\uff0c不代表已经发生的公司事实。",
            ),
        ]
    sections = [
        ResearchNarrativeSection(kind=kind, title=copy[0], summary=copy[1])
        for kind, copy in _SECTION_COPY.items()
    ]
    for section in sections:
        if section.kind is ResearchSectionKind.CONFLICTS_AND_UNKNOWNS:
            section.summary = (
                "当前资料保留了经营基础和变化观察。看多情景取决于原有基础能够延续\uff0c看空情景取决于变化是否削弱该基础\uff1b"
                "现有合成资料不足以判断影响方向\uff0c仍需后续披露验证。"
                if hypotheses
                else "当前资料不足以形成有依据的双向分析\uff0c综合判断保持未完成。"
            )
    return ResearchNarrativeDraft(schema_version="1.0", sections=sections, hypotheses=hypotheses)


class SyntheticResearchLLMProvider:
    """A schema-valid, zero-network model double with explicit non-production identity."""

    _failure_mode: Literal["none", "unsupported_hypothesis", "invalid_excerpt"] = "none"

    def __init__(
        self,
        *,
        failure_mode: Literal["none", "unsupported_hypothesis", "invalid_excerpt"] = "none",
    ) -> None:
        self._failure_mode = failure_mode

    @property
    def provider_name(self) -> str:
        return "synthetic-research"

    @property
    def capability(self) -> ModelProviderCapability:
        return ModelProviderCapability(
            schema_version="1.0",
            provider_name=self.provider_name,
            model=SYNTHETIC_MODEL,
            adapter_version=SYNTHETIC_ADAPTER_VERSION,
            operations=[ModelOperation.GENERATION],
            supported_privacy_levels=[PrivacyLevel.LOCAL_ONLY],
            external_processing=False,
            structured_output=True,
            maximum_input_tokens=32_000,
            maximum_output_tokens=8_000,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        draft: (
            ResearchNarrativeDraft
            | ResearchNarrativeCandidate
            | ResearchNarrativePatch
            | ResearchScoreCandidate
            | ResearchSupportLeafAssessment
            | ResearchScoreAssessmentDraft
            | ResearchWebQueryPlanDraft
            | ResearchWebNormalizationDraft
        )
        if request.task_type in {"research_report_narrative", "research_support_assessment"}:
            payload_input = json.loads(request.messages[-1].content)
            facts = TypeAdapter(list[ResearchEvidenceFact]).validate_python(payload_input["facts"])
            expected = _grounded_synthetic_narrative(facts)
            if request.task_type == "research_report_narrative":
                draft = expected
                if self._failure_mode == "unsupported_hypothesis" and draft.hypotheses:
                    draft.hypotheses[
                        0
                    ].statement = "若公司的月球采矿许可证继续有效\uff0c星际运输业务可能改善。"
                definitions = request.response_schema.get("$defs")
                kind_schema = (
                    definitions.get("ResearchSectionKind")
                    if isinstance(definitions, dict)
                    else None
                )
                allowed_kinds = kind_schema.get("enum") if isinstance(kind_schema, dict) else None
                sections = [
                    item
                    for item in draft.sections
                    if not isinstance(allowed_kinds, list) or item.kind.value in allowed_kinds
                ]
                draft = ResearchNarrativeCandidate(
                    schema_version="2.0",
                    sections=sections,
                    hypotheses=[
                        ResearchHypothesisSelection(
                            section=item.section,
                            statement=item.statement,
                            supporting_fact_keys=[
                                anchor.fact_key for anchor in item.supporting_basis
                            ],
                            counter_fact_keys=[anchor.fact_key for anchor in item.counter_basis],
                            falsification_conditions=item.falsification_conditions,
                            uncertainty=item.uncertainty,
                        )
                        for item in draft.hypotheses
                    ],
                )
                if self._failure_mode == "invalid_excerpt" and draft.hypotheses:
                    draft.hypotheses[0].supporting_fact_keys = ["unknown.fact"]
                if request.response_schema_namespace == "research.narrative_patch":
                    original = ResearchNarrativeCandidate.model_validate(
                        payload_input["previous_untrusted_draft"]
                    )
                    allowed = set(payload_input["allowed_patch_item_ids"])
                    draft = ResearchNarrativePatch(
                        schema_version="1.0",
                        hypotheses=[
                            ResearchHypothesisPatch(
                                item_id=f"hypothesis:{index}",
                                replacement=next(
                                    (
                                        candidate
                                        for candidate in draft.hypotheses
                                        if candidate.section is item.section
                                    ),
                                    None,
                                ),
                            )
                            for index, item in enumerate(original.hypotheses)
                            if f"hypothesis:{index}" in allowed
                        ],
                        sections=[
                            ResearchSectionPatch(
                                item_id=f"section:{index}",
                                replacement=next(
                                    (
                                        candidate
                                        for candidate in draft.sections
                                        if candidate.kind is item.kind
                                    ),
                                    None,
                                ),
                            )
                            for index, item in enumerate(original.sections)
                            if f"section:{index}" in allowed
                        ],
                    )
            else:
                # A fixed fixture oracle, not a general-purpose semantic classifier.
                # Unexpected arguments and altered basis are explicitly rejected.
                approved_hypotheses = [item.model_dump(mode="json") for item in expected.hypotheses]
                approved_sections = {item.kind.value: item.summary for item in expected.sections}
                verdicts = []
                for item in payload_input["items"]:
                    if "hypothesis" in item:
                        supported = item["hypothesis"] in approved_hypotheses
                    elif "assessment" in item:
                        assessment = item["assessment"]
                        supported = assessment["reasons"] in [
                            ["当前允许证据呈现正反混合。"],
                            ["当前维度缺少允许用于评分的证据。"],
                        ]
                    else:
                        section = item["section"]
                        supported = approved_sections.get(section["kind"]) == section["summary"]
                    verdicts.append(
                        ResearchSupportLeafItem(
                            item_id=item["item_id"],
                            field_checks=[
                                ResearchSupportLeaf(
                                    field_path=check["field_path"],
                                    criterion=check["criterion"],
                                    reason_code="SUPPORTED"
                                    if supported
                                    else "INFERENCE_UNSUPPORTED",
                                    target_quote=item["fields_to_check"][check["field_path"]][:400],
                                    reason="固定合成样本逐字段校验。",
                                )
                                for check in item["required_field_checks"]
                            ],
                        )
                    )
                draft = ResearchSupportLeafAssessment(schema_version="4.0", verdicts=verdicts)
        elif request.task_type == "research_web_gap_plan":
            plan_input = json.loads(request.messages[-1].content)
            first_gap = plan_input["gaps"][0]
            draft = ResearchWebQueryPlanDraft(
                schema_version="1.0",
                queries=[
                    ResearchWebQueryPlanItem(
                        query=f"{plan_input['instrument_name']} {first_gap['area']}",
                        missing_fact_keys=[first_gap["area"]],
                    )
                ],
            )
        elif request.task_type == "research_web_evidence_normalization":
            normalization_input = json.loads(request.messages[-1].content)
            draft = ResearchWebNormalizationDraft(
                schema_version="1.0",
                facts=[
                    ResearchWebNormalizedFactDraft(
                        missing_fact_key=normalization_input["missing_fact_keys"][0],
                        source_excerpt=normalization_input["source_text"][:80],
                    )
                ],
            )
        elif request.task_type == "research_score_assessment":
            score_input = json.loads(request.messages[-1].content)
            raw_allowlist = score_input["score_evidence_allowlist"]
            selections = {}
            for dimension in ResearchScoreDimensionId:
                allowed = raw_allowlist[dimension.value]
                selections[dimension.value] = ResearchScoreFactSelection(
                    grade=ResearchScoreAssessmentGrade.BALANCED
                    if allowed
                    else ResearchScoreAssessmentGrade.ABSTAIN,
                    reasons=[
                        "当前允许证据呈现正反混合。"
                        if allowed
                        else "当前维度缺少允许用于评分的证据。"
                    ],
                    supporting_fact_keys=allowed[:1],
                    counter_fact_keys=allowed[1:2],
                )
            draft = ResearchScoreCandidate.model_validate({"schema_version": "2.0", **selections})

        else:
            raise ValueError("SYNTHETIC_RESEARCH_TASK_UNSUPPORTED")
        prompt = [item.model_dump(mode="json") for item in request.messages]
        payload = draft.model_dump(mode="json")
        return GenerationResponse(
            schema_version="1.0",
            payload=payload,
            payload_namespace=request.response_schema_namespace,
            payload_owner=request.response_schema_owner,
            response_schema_hash=request.response_schema_hash,
            provider=self.provider_name,
            model=SYNTHETIC_MODEL,
            adapter_version=SYNTHETIC_ADAPTER_VERSION,
            prompt_hash=hashlib.sha256(_canonical(prompt)).hexdigest(),
            raw_response_hash=hashlib.sha256(_canonical(payload)).hexdigest(),
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            schema_validated=True,
            degraded=False,
        )


__all__ = ["SYNTHETIC_ADAPTER_VERSION", "SyntheticResearchLLMProvider"]
