"""Reader-edition, scoring, and controlled web-disclosure contracts."""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, Field, PrivateAttr, ValidationInfo, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString

_RAW_HTML = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_IMAGE_MARKDOWN = re.compile(r"!\s*\[[^\]]*\]\s*\(")
_DANGEROUS_LINK = re.compile(r"\]\s*\(\s*(?:javascript|data|file|vbscript):", re.IGNORECASE)
MODEL_AUTHORED_NUMERIC_CHARACTERS: Final = "0-9"
MODEL_AUTHORED_PROSE_PATTERN: Final = rf"^[^{MODEL_AUTHORED_NUMERIC_CHARACTERS}]*$"
_FINANCIAL_NUMERAL_CHARACTERS: Final = "壹贰貳叁參肆伍陆陸柒捌玖拾佰仟"
_FINANCIAL_NUMERAL_RUN: Final = rf"[{_FINANCIAL_NUMERAL_CHARACTERS}]+"
_HAN_NUMERAL_RUN: Final = r"[零〇一二两兩俩倆仨三四五六七八九十百千万萬亿億兆]+"
_CJK_PUNCTUATION: Final = r"\uFF0C\u3002\uFF1B\uFF1A\uFF01\uFF1F"
_QUANTITATIVE_HAN_PATTERNS: Final = tuple(
    re.compile(pattern)
    for pattern in (
        rf"百分之{_HAN_NUMERAL_RUN}",
        rf"{_HAN_NUMERAL_RUN}分之{_HAN_NUMERAL_RUN}",
        r"半(?:年|年度|个月|個月|月|月度|日|天|周|週|季度|季|期|轮|輪|次|小时|小時|分钟|分鐘|秒)",
        rf"(?!一次){_HAN_NUMERAL_RUN}(?:年|年度|月|月份|日|天|周|季度|季|期|轮|次(?!性)|小时|分钟|秒)",
        rf"(?:每(?:年|月|季度|周|天|期)|至少|至多|最多|最少|不超过|不少于|不低于|不止|超过|不足|不到)[^{_CJK_PUNCTUATION},.;:!?]*一次(?!性)",
        r"一次(?:以上|以下|以内|以外|左右|上下|有余)",
        rf"{_HAN_NUMERAL_RUN}(?:元|美元|港元|亿元|億元|万元|萬元|万亿|萬億|股|吨|噸|公斤|千克|米|公里|平方米|兆瓦|千瓦|百分比|百分点|成|倍|%|\uFF05)",
        rf"(?:排名|位列|前){_HAN_NUMERAL_RUN}(?:大|名|位|强|高|低|家|个|項|项|$|[{_CJK_PUNCTUATION}])",
        rf"第{_HAN_NUMERAL_RUN}(?:名|位|个|家|项|項|次|期|季|轮|頁|页|章|条|條|款|阶段|階段|季度|年度|$|[{_CJK_PUNCTUATION}])",
        # Indefinite articles and one-off items are prose; explicit counts remain quantitative.
        r"(?:仅|只有|唯一|新增|增加|减少|共|合计|总计|有|只)(?:有|记录|发生)?(?:了|过)?一(?:家|个|項|项|种|種|次(?!性))",
        rf"(?!一(?:家|个|項|项|种|種)){_HAN_NUMERAL_RUN}(?:座|台|家|个|項|项|条|條|笔|筆|份|件|套|批|名|位|款|种|種)(?:工厂|工廠|公司|客户|客戶|产品|產品|项目|項目|设备|設備|人员|人員)?",
        # Bare amounts exclude qualitative endings (single, inconsistent, one of);
        # an explicit unit or a longer numeral still denotes a quantity.
        rf"(?:金额|金額|价值|價值|收入|利润|利潤|成本|价格|價格|数量|數量|占比|比例|排名|得分|评分|評分)(?:为|為|约|約|是|达|達|至|近|超过|超過|不足)?[^{_CJK_PUNCTUATION}]{{0,6}}{_HAN_NUMERAL_RUN}(?:分|(?<![单單不之]一)(?:[{_CJK_PUNCTUATION}]|$))",
    )
)
_QUANTITATIVE_FINANCIAL_PATTERNS: Final = tuple(
    re.compile(pattern)
    for pattern in (
        rf"[{_FINANCIAL_NUMERAL_CHARACTERS}]{{2,}}",
        rf"{_FINANCIAL_NUMERAL_RUN}(?:元|美元|港元|亿元|億元|万元|萬元|股|吨|噸|公斤|千克|米|公里|平方米|兆瓦|千瓦|成|倍|%|\uFF05)",
        rf"(?:金额|金額|价值|價值|收入|利润|利潤|成本|价格|價格|数量|數量|占比|比例|排名|得分|评分|評分)(?:为|為|约|約|是|达|達|至|近|超过|超過|不足)?[^{_CJK_PUNCTUATION}]{{0,6}}{_FINANCIAL_NUMERAL_RUN}(?:分|元|亿元|億元|万元|萬元|[{_CJK_PUNCTUATION}]|$)",
    )
)
_FORBIDDEN_INVESTMENT_ADVICE: Final = (
    "建议买入",
    "建议卖出",
    "买入建议",
    "卖出建议",
    "建仓",
    "清仓",
    "加仓",
    "减仓",
    "持仓比例",
    "目标价",
    "收益承诺",
    "buy rating",
    "sell rating",
    "position size",
    "price target",
)
_REPORT_ADVICE_CONCEPT: Final = (
    "(?:"
    + "|".join(
        re.escape(phrase)
        for phrase in (
            *_FORBIDDEN_INVESTMENT_ADVICE,
            "投资建议",
            "交易建议",
            "加仓建议",
            "减仓建议",
        )
    )
    + ")"
)
_NEGATIVE_REPORT_ADVICE_CLAUSE: Final = re.compile(
    r"(?:(?:本报告|本研究|本说明|本文)\s*)?(?:也|并|亦)?"
    r"(?:不提供|不构成|不包含|不给出|不作出)\s*"
    r"(?:(?:任何形式的|任何|具体|个性化)(?:的)?\s*)?"
    + _REPORT_ADVICE_CONCEPT
    + r"(?:\s*(?:、|或|及|和)\s*"
    + _REPORT_ADVICE_CONCEPT
    + r")*"
)
_STORED_REPORT_COMPATIBILITY_VERSIONS: Final = frozenset(
    {
        "r2-publication-validator.v1",
        "r2-publication-validator.v2",
        "r2-publication-validator.v2.1",
    }
)


def retained_report_compatibility_version(value: object) -> str | None:
    """Return a validator version whose published wire semantics are still retained."""

    return (
        value if isinstance(value, str) and value in _STORED_REPORT_COMPATIBILITY_VERSIONS else None
    )


def stored_report_compatibility_version(info: ValidationInfo) -> str | None:
    """Read compatibility metadata injected by the report-level wire validator."""

    context = info.context
    if not isinstance(context, dict):
        return None
    return retained_report_compatibility_version(context.get("retained_report_validator_version"))


def contains_numeric_expression(value: str) -> bool:
    """Detect closed, quantity-bearing numeric forms without rejecting ordinary names."""

    normalized = unicodedata.normalize("NFKC", value)
    if any(character.isascii() and character.isdigit() for character in normalized):
        return True
    if any(unicodedata.category(character) in {"Nd", "Nl", "No"} for character in value):
        return True
    if re.fullmatch(_FINANCIAL_NUMERAL_RUN, normalized) is not None:
        return True
    return any(
        pattern.search(normalized) is not None
        for pattern in (*_QUANTITATIVE_HAN_PATTERNS, *_QUANTITATIVE_FINANCIAL_PATTERNS)
    )


def contains_forbidden_investment_advice(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    # Exempt only whole disclaimer clauses. Colons and parentheses stay attached so
    # a quoted price or an instruction cannot hide behind an otherwise valid denial.
    return any(
        any(phrase in clause for phrase in _FORBIDDEN_INVESTMENT_ADVICE)
        and _NEGATIVE_REPORT_ADVICE_CLAUSE.fullmatch(clause.strip()) is None
        for clause in re.split(r"[,。.;!?\n\r]+", normalized)
    )


def _reject_investment_advice(value: str) -> str:
    if contains_forbidden_investment_advice(value):
        raise ValueError("model-authored prose cannot contain investment-advice language")
    return value


def _reject_numeric_expression(value: str) -> str:
    if contains_numeric_expression(value):
        raise ValueError("model-authored prose cannot introduce numeric expressions")
    return value


_MODEL_AUTHORED_SCHEMA_METADATA: Final = {
    "x-stockassistant-normalization": "NFKC",
    "x-stockassistant-language-profile": "han-dominant-user-prose",
    "x-stockassistant-no-investment-advice": True,
}
_MODEL_AUTHORED_TERM_SCHEMA_METADATA: Final = {
    "x-stockassistant-normalization": "NFKC",
    "x-stockassistant-no-numeric-expression": True,
    "x-stockassistant-no-investment-advice": True,
}


def is_han_dominant_user_prose(value: str) -> bool:
    """Require Han-led prose without pretending to be a full zh-CN classifier."""

    normalized = unicodedata.normalize("NFKC", value)
    # References and financial terms do not turn Chinese prose into English.
    prose = re.sub(r"https?://[^\s,。;、)]+", "", normalized)
    prose = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "", prose)
    prose = re.sub(r"\b(?:PE|PB|ROE|ROA|ROIC|EPS|EBITDA|EBIT|DCF|FCF|ETF|IPO)\b", "", prose)
    han_count = sum(
        "CJK UNIFIED IDEOGRAPH" in unicodedata.name(character, "")
        or "CJK COMPATIBILITY IDEOGRAPH" in unicodedata.name(character, "")
        for character in prose
    )
    latin_count = sum(character.isascii() and character.isalpha() for character in prose)
    return han_count > 0 and latin_count <= han_count


def _require_chinese_user_prose(value: str) -> str:
    if not is_han_dominant_user_prose(value):
        raise ValueError(
            "model-authored prose must contain Han characters and not be Latin-dominant"
        )
    return value


ModelAuthoredProse = Annotated[
    str,
    Field(json_schema_extra=_MODEL_AUTHORED_SCHEMA_METADATA),
    AfterValidator(_require_chinese_user_prose),
    AfterValidator(_reject_investment_advice),
]
ModelAuthoredScoreReason = Annotated[ModelAuthoredProse, Field(min_length=1, max_length=500)]
ModelAuthoredTerm = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=MODEL_AUTHORED_PROSE_PATTERN,
        json_schema_extra=_MODEL_AUTHORED_TERM_SCHEMA_METADATA,
    ),
    AfterValidator(_reject_numeric_expression),
    AfterValidator(_reject_investment_advice),
]


class ResearchScoreDimensionId(StrEnum):
    BUSINESS_QUALITY = "business_quality"
    FINANCIAL_QUALITY = "financial_quality"
    COMPETITIVE_DURABILITY = "competitive_durability"
    MANAGEMENT_AND_CAPITAL_ALLOCATION = "management_and_capital_allocation"
    VALUATION_CONTEXT = "valuation_context"
    RISK_RESILIENCE = "risk_resilience"


class ResearchScoreStatus(StrEnum):
    SCORED = "scored"
    NOT_SCORED = "not_scored"


class ResearchScoreBasis(StrEnum):
    REAL_EVIDENCE = "real_evidence"
    SYNTHETIC_SCENARIO = "synthetic_scenario"


class ResearchScoreAssessmentGrade(StrEnum):
    MATERIAL_WEAKNESS = "material_weakness"
    WEAKNESS = "weakness"
    BALANCED = "balanced"
    STRENGTH = "strength"
    EXCEPTIONAL = "exceptional"
    ABSTAIN = "abstain"


class ResearchScoreAssessmentDimensionDraft(ContractModel):
    """Model judgment input to deterministic scoring; never published directly."""

    grade: ResearchScoreAssessmentGrade
    reasons: list[ModelAuthoredScoreReason] = Field(min_length=1, max_length=4)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=30)
    counter_evidence_ids: list[UUID] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def evidence_selection_is_consistent(self) -> Self:
        if self.grade is not ResearchScoreAssessmentGrade.ABSTAIN and not self.evidence_ids:
            raise ValueError("a scored assessment requires allowed evidence")
        return self


class ResearchScoreAssessmentDraft(ContractModel):
    schema_version: Literal["1.0", "2.0"] = "1.0"
    business_quality: ResearchScoreAssessmentDimensionDraft
    financial_quality: ResearchScoreAssessmentDimensionDraft
    competitive_durability: ResearchScoreAssessmentDimensionDraft
    management_and_capital_allocation: ResearchScoreAssessmentDimensionDraft
    valuation_context: ResearchScoreAssessmentDimensionDraft
    risk_resilience: ResearchScoreAssessmentDimensionDraft

    def dimension_items(
        self,
    ) -> list[tuple[ResearchScoreDimensionId, ResearchScoreAssessmentDimensionDraft]]:
        return [
            (dimension, getattr(self, dimension.value)) for dimension in ResearchScoreDimensionId
        ]


class ResearchScoreFactSelection(ContractModel):
    grade: ResearchScoreAssessmentGrade
    reasons: list[ModelAuthoredScoreReason] = Field(min_length=1, max_length=4)
    supporting_fact_keys: list[str] = Field(default_factory=list, max_length=30)
    counter_fact_keys: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def scored_selection_requires_support(self) -> Self:
        if self.grade is not ResearchScoreAssessmentGrade.ABSTAIN and not self.supporting_fact_keys:
            raise ValueError("a scored assessment requires supporting facts")
        return self


class ResearchScoreCandidate(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    business_quality: ResearchScoreFactSelection
    financial_quality: ResearchScoreFactSelection
    competitive_durability: ResearchScoreFactSelection
    management_and_capital_allocation: ResearchScoreFactSelection
    valuation_context: ResearchScoreFactSelection
    risk_resilience: ResearchScoreFactSelection

    def dimension_items(self) -> list[tuple[ResearchScoreDimensionId, ResearchScoreFactSelection]]:
        return [
            (dimension, getattr(self, dimension.value)) for dimension in ResearchScoreDimensionId
        ]


class ResearchScoreDimension(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    dimension: ResearchScoreDimensionId
    display_name: str = Field(min_length=1, max_length=100)
    weight: DecimalString
    evidence_grade: int | None = Field(default=None, ge=0, le=4)
    raw_score: DecimalString | None = None
    reliability_factor: DecimalString | None = None
    adjusted_score: DecimalString | None = None
    reasons: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    counter_evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    unscored_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def score_fields_are_atomic(self) -> Self:
        score_fields = (
            self.evidence_grade,
            self.raw_score,
            self.reliability_factor,
            self.adjusted_score,
        )
        if any(value is None for value in score_fields):
            if any(value is not None for value in score_fields):
                raise ValueError("dimension score fields must be populated together")
            if self.unscored_reason is None:
                raise ValueError("an unscored dimension requires a reason")
        elif self.unscored_reason is not None:
            raise ValueError("a scored dimension cannot carry an unscored reason")
        for value in (self.weight, self.raw_score, self.reliability_factor, self.adjusted_score):
            if value is not None and value < 0:
                raise ValueError("score values cannot be negative")
        if self.weight > 1:
            raise ValueError("dimension weight cannot exceed one")
        if self.reliability_factor is not None and self.reliability_factor > 1:
            raise ValueError("reliability factor cannot exceed one")
        if self.raw_score is not None and self.raw_score > 100:
            raise ValueError("raw score cannot exceed one hundred")
        if self.adjusted_score is not None and self.adjusted_score > 100:
            raise ValueError("adjusted score cannot exceed one hundred")
        if self.evidence_grade is not None:
            expected_raw = Decimal(self.evidence_grade) * Decimal("25")
            if self.raw_score != expected_raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP):
                raise ValueError("raw score must equal evidence grade times twenty five")
            assert self.reliability_factor is not None
            expected_adjusted = (
                Decimal("50") + (expected_raw - Decimal("50")) * self.reliability_factor
            )
            if self.adjusted_score != expected_adjusted.quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            ):
                raise ValueError("adjusted score must match raw score and reliability")
        return self


def score_band_for(score: Decimal) -> tuple[str, str]:
    if score < 40:
        return "短板明显", "当前证据显示关键短板较多, 需要先看清风险与缺口。"
    if score < 60:
        return "优劣并存", "当前证据下优势和短板大致平衡。"
    if score < 75:
        return "基础稳健", "当前证据显示公司基础较稳, 但仍有明确待核验项。"
    if score < 90:
        return "综合较强", "当前证据显示多项质量特征较强, 同时仍需关注反证。"
    return "高质量证据下表现突出", "在高质量证据覆盖下, 多数质量维度表现突出。"


class ResearchScorecard(ContractModel):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    scoring_basis: ResearchScoreBasis = ResearchScoreBasis.REAL_EVIDENCE
    status: ResearchScoreStatus
    overall_score: DecimalString | None = None
    score_band: str | None = Field(default=None, max_length=100)
    score_meaning: str = Field(min_length=1, max_length=500)
    evidence_confidence: DecimalString
    evidence_confidence_meaning: str = Field(min_length=1, max_length=500)
    dimensions: list[ResearchScoreDimension] = Field(min_length=6, max_length=6)
    scoring_rule_version: str = Field(min_length=1, max_length=100)
    recommendation_notice: Literal["公司质量评分不构成投资建议, 也不表示推荐概率。"] = (
        "公司质量评分不构成投资建议, 也不表示推荐概率。"
    )

    @model_validator(mode="after")
    def scorecard_is_complete_and_consistent(self) -> Self:
        dimensions = [item.dimension for item in self.dimensions]
        if len(set(dimensions)) != len(dimensions) or set(dimensions) != set(
            ResearchScoreDimensionId
        ):
            raise ValueError("scorecard requires all six dimensions exactly once")
        expected_weights = {
            ResearchScoreDimensionId.BUSINESS_QUALITY: Decimal("0.20"),
            ResearchScoreDimensionId.FINANCIAL_QUALITY: Decimal("0.20"),
            ResearchScoreDimensionId.COMPETITIVE_DURABILITY: Decimal("0.15"),
            ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION: Decimal("0.10"),
            ResearchScoreDimensionId.VALUATION_CONTEXT: Decimal("0.15"),
            ResearchScoreDimensionId.RISK_RESILIENCE: Decimal("0.20"),
        }
        if any(item.weight != expected_weights[item.dimension] for item in self.dimensions):
            raise ValueError("scorecard dimensions must use the published weights")
        if not 0 <= self.evidence_confidence <= 100:
            raise ValueError("evidence confidence must be between zero and one hundred")
        all_scored = all(item.adjusted_score is not None for item in self.dimensions)
        if self.status is ResearchScoreStatus.SCORED:
            if not all_scored or self.overall_score is None or self.score_band is None:
                raise ValueError("a scored scorecard requires six scores and an overall score")
            if not 0 <= self.overall_score <= 100:
                raise ValueError("overall score must be between zero and one hundred")
            expected_overall = sum(
                ((item.adjusted_score or Decimal("0")) * item.weight for item in self.dimensions),
                Decimal("0"),
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if self.overall_score != expected_overall:
                raise ValueError("overall score must equal the weighted dimension scores")
            if self.score_band != score_band_for(expected_overall)[0]:
                raise ValueError("score band must match the published boundaries")
        else:
            if self.overall_score is not None or self.score_band is not None:
                raise ValueError("an unscored scorecard cannot publish an overall score or band")
            if all_scored:
                raise ValueError("an unscored scorecard requires at least one unscored dimension")
        if self.scoring_basis is ResearchScoreBasis.SYNTHETIC_SCENARIO:
            if self.schema_version != "1.1":
                raise ValueError("synthetic scoring requires scorecard version 1.1")
            if self.evidence_confidence != 0:
                raise ValueError("synthetic scoring cannot claim real source confidence")
            if any(
                item.adjusted_score is not None and item.reliability_factor != 1
                for item in self.dimensions
            ):
                raise ValueError("synthetic scored dimensions require scenario coefficient one")
        elif self.scoring_rule_version.startswith(
            ("company-quality-score.v2", "company-quality-score.v3", "company-quality-score.v4")
        ):
            expected_confidence = (
                sum(
                    (
                        (item.reliability_factor or Decimal("0")) * item.weight
                        for item in self.dimensions
                    ),
                    Decimal("0"),
                )
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if self.evidence_confidence != expected_confidence:
                raise ValueError("evidence confidence must equal weighted reliability coverage")
        return self


class ResearchGlossaryItem(ContractModel):
    term: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=500)


class ResearchGlossaryDraftItem(ContractModel):
    """Model-only glossary item; public ResearchReport 1.1 remains immutable."""

    term: ModelAuthoredTerm = Field(min_length=1, max_length=100)
    explanation: ModelAuthoredProse = Field(min_length=1, max_length=500)


class ResearchReaderDraft(ContractModel):
    """Model-authored plain-language structure; Markdown is compiled later."""

    schema_version: Literal["2.0"] = "2.0"
    one_sentence_takeaway: ModelAuthoredProse = Field(min_length=1, max_length=500)
    how_the_company_makes_money: list[ModelAuthoredProse] = Field(min_length=3, max_length=3)
    why_this_score: list[ModelAuthoredProse] = Field(min_length=3, max_length=3)
    main_strengths: list[ModelAuthoredProse] = Field(min_length=1, max_length=8)
    main_risks: list[ModelAuthoredProse] = Field(min_length=1, max_length=8)
    unknowns: list[ModelAuthoredProse] = Field(default_factory=list, max_length=20)
    glossary: list[ResearchGlossaryDraftItem] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def investment_advice_is_forbidden(self) -> Self:
        text = "\n".join(
            [
                self.one_sentence_takeaway,
                *self.how_the_company_makes_money,
                *self.why_this_score,
                *self.main_strengths,
                *self.main_risks,
                *self.unknowns,
                *(item.term for item in self.glossary),
                *(item.explanation for item in self.glossary),
            ]
        )
        if contains_forbidden_investment_advice(text):
            raise ValueError("investment-advice language is forbidden")
        return self


class ResearchReaderEdition(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    one_sentence_takeaway: str = Field(min_length=1, max_length=500)
    how_the_company_makes_money: list[str] = Field(min_length=1, max_length=8)
    why_this_score: list[str] = Field(min_length=1, max_length=8)
    main_strengths: list[str] = Field(min_length=1, max_length=8)
    main_risks: list[str] = Field(min_length=1, max_length=8)
    public_analysis_steps: list[str] = Field(min_length=7, max_length=7)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    glossary: list[ResearchGlossaryItem] = Field(default_factory=list, max_length=30)
    claim_ids: list[UUID] = Field(min_length=1, max_length=500)
    safe_markdown: str = Field(min_length=1, max_length=50000)
    compiler_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def markdown_is_safe_for_the_locked_renderer(self) -> Self:
        if _RAW_HTML.search(self.safe_markdown):
            raise ValueError("reader Markdown cannot contain raw HTML")
        if _IMAGE_MARKDOWN.search(self.safe_markdown):
            raise ValueError("reader Markdown cannot contain images")
        if _DANGEROUS_LINK.search(self.safe_markdown):
            raise ValueError("reader Markdown contains a forbidden link protocol")
        return self


class ResearchWebRetrievalDisclosure(ContractModel):
    _retained_report_validator_version: str | None = PrivateAttr(default=None)

    schema_version: Literal["1.0"] = "1.0"
    model_assisted_item_count: int = Field(default=0, ge=0, le=500)
    not_independently_verified_count: int = Field(default=0, ge=0, le=500)
    source_levels: list[str] = Field(default_factory=list, max_length=20)
    receipt_ids: list[UUID] = Field(default_factory=list, max_length=100)
    warning: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unverified_count_cannot_exceed_total(self, info: ValidationInfo) -> Self:
        if self.not_independently_verified_count > self.model_assisted_item_count:
            raise ValueError("unverified web items cannot exceed all web items")
        compatibility_version = (
            self._retained_report_validator_version or stored_report_compatibility_version(info)
        )
        if compatibility_version not in {
            "r2-publication-validator.v1",
            "r2-publication-validator.v2",
        }:
            if len(set(self.receipt_ids)) != len(self.receipt_ids):
                raise ValueError("web retrieval receipt ids must be unique")
            if bool(self.model_assisted_item_count) != bool(self.receipt_ids):
                raise ValueError("web retrieval disclosure must link its audit receipts")
        return self


__all__ = [
    "MODEL_AUTHORED_NUMERIC_CHARACTERS",
    "MODEL_AUTHORED_PROSE_PATTERN",
    "ModelAuthoredProse",
    "ModelAuthoredScoreReason",
    "ModelAuthoredTerm",
    "ResearchGlossaryDraftItem",
    "ResearchGlossaryItem",
    "ResearchReaderDraft",
    "ResearchReaderEdition",
    "ResearchScoreAssessmentDimensionDraft",
    "ResearchScoreAssessmentDraft",
    "ResearchScoreAssessmentGrade",
    "ResearchScoreBasis",
    "ResearchScoreDimension",
    "ResearchScoreDimensionId",
    "ResearchScoreStatus",
    "ResearchScorecard",
    "ResearchWebRetrievalDisclosure",
    "contains_forbidden_investment_advice",
    "contains_numeric_expression",
    "is_han_dominant_user_prose",
    "retained_report_compatibility_version",
    "score_band_for",
    "stored_report_compatibility_version",
]
