from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.research import (
    ClaimType,
    CoverageGap,
    ResearchClaim,
    ResearchEvidenceAcquisitionMode,
    ResearchEvidenceSourceLevel,
    ResearchEvidenceVerificationStatus,
    ResearchReportSection,
    ResearchSectionKind,
)
from stock_assistant.contracts.research_reader import (
    ResearchReaderDraft,
    ResearchScoreAssessmentDimensionDraft,
    ResearchScoreAssessmentDraft,
    ResearchScoreAssessmentGrade,
    ResearchScorecard,
    ResearchScoreDimensionId,
    ResearchScoreStatus,
    score_band_for,
)
from stock_assistant.modules.research.scoring import (
    build_scorecard,
    build_synthetic_unscored_scorecard,
    build_web_retrieval_disclosure,
    compile_complete_reader_edition,
    compile_reader_edition,
    ordered_unique_coverage_gaps,
)
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet

_GRADES = {
    0: ResearchScoreAssessmentGrade.MATERIAL_WEAKNESS,
    1: ResearchScoreAssessmentGrade.WEAKNESS,
    2: ResearchScoreAssessmentGrade.BALANCED,
    3: ResearchScoreAssessmentGrade.STRENGTH,
    4: ResearchScoreAssessmentGrade.EXCEPTIONAL,
}


def _assessment(
    *,
    grades: int | list[int],
    evidence_ids: UUID | list[UUID],
    abstain: set[ResearchScoreDimensionId] | None = None,
) -> ResearchScoreAssessmentDraft:
    grade_values = [grades] * 6 if isinstance(grades, int) else grades
    selected_ids = [evidence_ids] * 6 if isinstance(evidence_ids, UUID) else evidence_ids
    abstained = abstain or set()
    drafts = {
        dimension: ResearchScoreAssessmentDimensionDraft(
            grade=(
                ResearchScoreAssessmentGrade.ABSTAIN
                if dimension in abstained
                else _GRADES[grade_values[index]]
            ),
            reasons=["测试中的可核验评分理由。"],
            evidence_ids=[] if dimension in abstained else [selected_ids[index]],
        )
        for index, dimension in enumerate(ResearchScoreDimensionId)
    }
    return ResearchScoreAssessmentDraft(
        schema_version="1.0",
        business_quality=drafts[ResearchScoreDimensionId.BUSINESS_QUALITY],
        financial_quality=drafts[ResearchScoreDimensionId.FINANCIAL_QUALITY],
        competitive_durability=drafts[ResearchScoreDimensionId.COMPETITIVE_DURABILITY],
        management_and_capital_allocation=drafts[
            ResearchScoreDimensionId.MANAGEMENT_AND_CAPITAL_ALLOCATION
        ],
        valuation_context=drafts[ResearchScoreDimensionId.VALUATION_CONTEXT],
        risk_resilience=drafts[ResearchScoreDimensionId.RISK_RESILIENCE],
    )


def _allow_all(evidence_id: UUID) -> dict[ResearchScoreDimensionId, frozenset[UUID]]:
    return {dimension: frozenset({evidence_id}) for dimension in ResearchScoreDimensionId}


def _reader_draft() -> ResearchReaderDraft:
    return ResearchReaderDraft(
        schema_version="2.0",
        one_sentence_takeaway="现有证据支持谨慎结论。",
        how_the_company_makes_money=[
            "核心产品或服务仍需核验。",
            "客户到收入链仍需核验。",
            "产品结构调整仍需核验。",
        ],
        why_this_score=[
            "总体结论仍需结合证据判断。",
            "最强支撑来自已核对资料。",
            "主要拖累来自尚未补齐的缺口。",
        ],
        main_strengths=["已核对资料保留可追溯关系。"],
        main_risks=["资料缺口可能影响结论。"],
        unknowns=["仍有信息需要补充。"],
    )


def test_web_disclosure_links_persisted_retrieval_receipt() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    web_source = packet.citations[0].model_copy(
        update={
            "acquisition_mode": ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB,
            "source_level": ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    receipt_id = uuid4()

    disclosure = build_web_retrieval_disclosure(
        citations=[web_source],
        receipt_ids=[receipt_id],
    )

    assert disclosure.model_assisted_item_count == 1
    assert disclosure.not_independently_verified_count == 1
    assert disclosure.receipt_ids == [receipt_id]
    with pytest.raises(ValueError, match="link its audit receipts"):
        build_web_retrieval_disclosure(citations=[web_source], receipt_ids=[])


def test_company_single_source_score_shrinks_toward_neutral() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claims = [
        ResearchClaim(
            schema_version="1.0",
            claim_type=ClaimType.FACT,
            section=ResearchSectionKind.BUSINESS_MODEL,
            statement="公开证据中的测试事实。",
            evidence=[citation],
            publication_eligible=True,
        )
    ]

    scorecard = build_scorecard(
        assessment=_assessment(grades=4, evidence_ids=citation.evidence_id),
        claims=claims,
        evidence_allowlist=_allow_all(citation.evidence_id),
    )

    assert scorecard.status is ResearchScoreStatus.SCORED
    assert scorecard.overall_score == Decimal("95.0")
    assert all(item.raw_score == Decimal("100.0") for item in scorecard.dimensions)
    assert all(item.adjusted_score == Decimal("95.0") for item in scorecard.dimensions)
    assert sum(item.weight for item in scorecard.dimensions) == Decimal("1.00")


def test_unverified_evidence_suppresses_overall_score() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={"verification_status": ResearchEvidenceVerificationStatus.UNVERIFIED}
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="未通过评分硬门的测试事实。",
        evidence=[citation],
        publication_eligible=True,
    )

    scorecard = build_scorecard(
        assessment=_assessment(grades=4, evidence_ids=citation.evidence_id),
        claims=[claim],
        evidence_allowlist=_allow_all(citation.evidence_id),
    )

    assert scorecard.status is ResearchScoreStatus.NOT_SCORED
    assert scorecard.overall_score is None
    assert all(item.adjusted_score is None for item in scorecard.dimensions)


def test_single_registered_media_source_uses_seventy_percent_reliability() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.TRUSTED_MEDIA_DATA,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="已登记媒体中的测试事实。",
        evidence=[citation],
        publication_eligible=True,
    )

    scorecard = build_scorecard(
        assessment=_assessment(grades=4, evidence_ids=citation.evidence_id),
        claims=[claim],
        evidence_allowlist=_allow_all(citation.evidence_id),
    )

    assert scorecard.overall_score == Decimal("85.0")
    assert all(item.reliability_factor == Decimal("0.70") for item in scorecard.dimensions)


def test_one_unknown_dimension_keeps_verified_partial_scores_but_hides_total() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    verified = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    unverified = packet.citations[1].model_copy(
        update={"verification_status": ResearchEvidenceVerificationStatus.UNVERIFIED}
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="评分覆盖状态测试事实。",
        evidence=[verified, unverified],
        publication_eligible=True,
    )
    scorecard = build_scorecard(
        assessment=_assessment(
            grades=4,
            evidence_ids=[
                unverified.evidence_id,
                *[verified.evidence_id] * 5,
            ],
        ),
        claims=[claim],
        evidence_allowlist={
            dimension: frozenset({unverified.evidence_id if index == 0 else verified.evidence_id})
            for index, dimension in enumerate(ResearchScoreDimensionId)
        },
    )

    assert scorecard.status is ResearchScoreStatus.NOT_SCORED
    assert scorecard.overall_score is None
    assert scorecard.dimensions[0].adjusted_score is None
    assert all(item.adjusted_score == Decimal("95.0") for item in scorecard.dimensions[1:])
    assert scorecard.evidence_confidence == Decimal("72.0")


def test_mixed_grade_vector_recalculates_to_sixty() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="监管来源中的测试事实。",
        evidence=[citation],
        publication_eligible=True,
    )

    scorecard = build_scorecard(
        assessment=_assessment(grades=[3, 3, 2, 2, 2, 2], evidence_ids=citation.evidence_id),
        claims=[claim],
        evidence_allowlist=_allow_all(citation.evidence_id),
    )

    assert [item.adjusted_score for item in scorecard.dimensions] == [
        Decimal("75.0"),
        Decimal("75.0"),
        Decimal("50.0"),
        Decimal("50.0"),
        Decimal("50.0"),
        Decimal("50.0"),
    ]
    assert scorecard.overall_score == Decimal("60.0")
    assert scorecard.score_band == "基础稳健"


def test_cross_dimension_evidence_is_rejected() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="仅允许用于业务维度的测试事实。",
        evidence=[citation],
        publication_eligible=True,
    )
    allowed = {dimension: frozenset() for dimension in ResearchScoreDimensionId}
    allowed[ResearchScoreDimensionId.BUSINESS_QUALITY] = frozenset({citation.evidence_id})

    with pytest.raises(ValueError, match="not allowed for this dimension"):
        build_scorecard(
            assessment=_assessment(grades=2, evidence_ids=citation.evidence_id),
            claims=[claim],
            evidence_allowlist=allowed,
        )


def test_model_abstention_hides_total_and_counts_missing_weight_as_zero_confidence() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="评分弃权测试事实。",
        evidence=[citation],
        publication_eligible=True,
    )

    scorecard = build_scorecard(
        assessment=_assessment(
            grades=2,
            evidence_ids=citation.evidence_id,
            abstain={ResearchScoreDimensionId.BUSINESS_QUALITY},
        ),
        claims=[claim],
        evidence_allowlist=_allow_all(citation.evidence_id),
    )

    assert scorecard.status is ResearchScoreStatus.NOT_SCORED
    assert scorecard.overall_score is None
    assert scorecard.dimensions[0].evidence_grade is None
    assert scorecard.evidence_confidence == Decimal("80.0")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("dimension", "raw_score", "49.9"), "raw score"),
        (("dimension", "adjusted_score", "49.9"), "adjusted score"),
        (("scorecard", "overall_score", "49.9"), "overall score"),
        (("scorecard", "score_band", "错误分档"), "score band"),
        (("scorecard", "evidence_confidence", "99.9"), "evidence confidence"),
    ],
)
def test_scorecard_contract_rejects_formula_tampering(
    mutation: tuple[str, str, str],
    message: str,
) -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="兆易创新",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.REGULATORY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="公式一致性测试事实。",
        evidence=[citation],
        publication_eligible=True,
    )
    scorecard = build_scorecard(
        assessment=_assessment(grades=2, evidence_ids=citation.evidence_id),
        claims=[claim],
        evidence_allowlist=_allow_all(citation.evidence_id),
    )
    payload = scorecard.model_dump(mode="python")
    target, field, value = mutation
    if target == "dimension":
        payload["dimensions"][0][field] = value
    else:
        payload[field] = value

    with pytest.raises(ValidationError, match=message):
        ResearchScorecard.model_validate(payload)


@pytest.mark.parametrize(
    ("score", "band"),
    [
        ("39.9", "短板明显"),
        ("40", "优劣并存"),
        ("59.9", "优劣并存"),
        ("60", "基础稳健"),
        ("74.9", "基础稳健"),
        ("75", "综合较强"),
        ("89.9", "综合较强"),
        ("90", "高质量证据下表现突出"),
    ],
)
def test_score_band_boundaries(score: str, band: str) -> None:
    assert score_band_for(Decimal(score))[0] == band


def test_synthetic_scorecard_publishes_no_numeric_assessment() -> None:
    scorecard = build_synthetic_unscored_scorecard()

    assert scorecard.status is ResearchScoreStatus.NOT_SCORED
    assert scorecard.overall_score is None
    assert scorecard.score_band is None
    assert scorecard.evidence_confidence == 0
    assert all(item.evidence_grade is None for item in scorecard.dimensions)
    assert all(item.adjusted_score is None for item in scorecard.dimensions)


def test_internal_assessment_requires_six_fixed_dimensions_and_known_grade() -> None:
    evidence_id = uuid4()
    payload = _assessment(grades=2, evidence_ids=evidence_id).model_dump(mode="python")
    payload.pop("risk_resilience")
    with pytest.raises(ValidationError):
        ResearchScoreAssessmentDraft.model_validate(payload)

    payload = _assessment(grades=2, evidence_ids=evidence_id).model_dump(mode="python")
    payload["business_quality"]["grade"] = "invented"
    with pytest.raises(ValidationError):
        ResearchScoreAssessmentDraft.model_validate(payload)


@pytest.mark.parametrize("gap_count", [0, 1, 20, 21, 100])
def test_reader_markdown_bounds_gap_summary_without_hiding_page_data(gap_count: int) -> None:
    gaps = [
        CoverageGap(
            area=f"business.gap_{index}",
            reason_code="TEST_EVIDENCE_GAP",
            detail=f"待补充证据缺口 {index}",
        )
        for index in range(gap_count)
    ]

    edition = compile_reader_edition(
        instrument_name="兆易创新",
        draft=_reader_draft(),
        scorecard=build_synthetic_unscored_scorecard(),
        claim_ids=[UUID("9712aec6-ce75-55be-ae18-13ee648f85dd")],
        coverage_gaps=gaps,
    )
    gap_markdown = edition.safe_markdown.split("## 证据缺口与补充状态\n\n", maxsplit=1)[1].split(
        "\n\n## 主要优势", maxsplit=1
    )[0]
    assert (
        edition.safe_markdown.index("## 为什么得到这个分")
        < edition.safe_markdown.index("## 证据缺口与补充状态")
        < edition.safe_markdown.index("## 主要优势")
    )

    if gap_count == 0:
        assert gap_markdown == "当前未记录额外证据缺口。"
        return

    visible_count = min(gap_count, 20)
    expected_lines = visible_count + (1 if gap_count > 20 else 0)
    assert len(gap_markdown.splitlines()) == expected_lines
    for index in range(visible_count):
        assert f"待补充证据缺口 {index}" in gap_markdown
    if gap_count > 20:
        assert "其余缺口请在页面的完整缺口列表中核对。" in gap_markdown
        assert f"待补充证据缺口 {gap_count - 1}" not in gap_markdown
    else:
        assert "其余缺口请在页面的完整缺口列表中核对。" not in gap_markdown


def test_reader_compiler_deduplicates_complete_gaps_in_first_seen_order() -> None:
    first = CoverageGap(
        area="business.core_products",
        reason_code="WEB_DISCOVERY_NO_RESULT",
        detail="核心产品证据仍待补充。",
    )
    second = CoverageGap(
        area="business.customer_revenue_chain",
        reason_code="WEB_DISCOVERY_LIMITED",
        detail="客户到收入链证据仍待补充。",
    )

    normalized = ordered_unique_coverage_gaps([first, second, first])
    edition = compile_reader_edition(
        instrument_name="兆易创新",
        draft=_reader_draft(),
        scorecard=build_synthetic_unscored_scorecard(),
        claim_ids=[UUID("9712aec6-ce75-55be-ae18-13ee648f85dd")],
        coverage_gaps=[first, second, first],
    )

    assert normalized == [first, second]
    assert edition.safe_markdown.count(first.detail) == 1
    assert edition.safe_markdown.index(first.detail) < edition.safe_markdown.index(second.detail)


def test_reader_draft_cannot_choose_claim_visibility() -> None:
    first_claim_id = UUID("9712aec6-ce75-55be-ae18-13ee648f85dd")
    second_claim_id = UUID("127d9d65-8356-5368-b22a-b85dabf12ea8")
    payload = _reader_draft().model_dump(mode="python")
    payload["claim_ids"] = [first_claim_id]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResearchReaderDraft.model_validate(payload)

    edition = compile_reader_edition(
        instrument_name="兆易创新",
        draft=_reader_draft(),
        scorecard=build_synthetic_unscored_scorecard(),
        claim_ids=[first_claim_id, second_claim_id, first_claim_id],
        coverage_gaps=[],
    )

    assert edition.claim_ids == [first_claim_id, second_claim_id]
    assert edition.compiler_version == "safe-reader-markdown.v6"


def test_complete_reader_score_links_reference_shared_source_not_arbitrary_claim() -> None:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986",
        instrument_name="原创测试公司",
        as_of=datetime(2026, 8, 27, tzinfo=UTC),
    )
    citation = packet.citations[0].model_copy(
        update={
            "source_level": ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
        }
    )
    claims = [
        ResearchClaim(
            schema_version="1.0",
            claim_type=ClaimType.FACT,
            section=ResearchSectionKind.BUSINESS_MODEL,
            statement=statement,
            evidence=[citation],
            publication_eligible=True,
        )
        for statement in ("原创身份事实。", "原创业务事实。", "原创财务事实。")
    ]
    scorecard = build_scorecard(
        assessment=_assessment(grades=3, evidence_ids=citation.evidence_id),
        claims=claims,
        evidence_allowlist=_allow_all(citation.evidence_id),
    )
    edition = compile_complete_reader_edition(
        instrument_name="原创测试公司",
        sections=[
            ResearchReportSection(
                kind=kind, title="原创测试章节", summary="原创已核对摘要。", claim_ids=[]
            )
            for kind in ResearchSectionKind
        ],
        claims=claims,
        scorecard=scorecard,
        coverage_gaps=[],
    )
    scores = edition.safe_markdown.split("## 核心结论", 1)[1].split("## 关键事实", 1)[0]
    assert f"#source-{citation.evidence_id}" in scores
    assert "#claim-" not in scores
    assert all(claim.claim_id in edition.claim_ids for claim in claims)
