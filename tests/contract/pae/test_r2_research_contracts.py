from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.instruments import (
    InstrumentSearchMatch,
    InstrumentSearchMatchType,
    InstrumentSearchQuery,
    InstrumentSearchResult,
    ListingStatus,
)
from stock_assistant.contracts.research import (
    ClaimType,
    CoverageGap,
    ResearchAnalysisConfig,
    ResearchCalculation,
    ResearchCalculationInput,
    ResearchClaim,
    ResearchEvidenceCitation,
    ResearchRealSourceMode,
    ResearchReport,
    ResearchReportSection,
    ResearchReportWriteRequest,
    ResearchSectionKind,
    ResearchSourceMode,
)
from stock_assistant.contracts.research_v1 import ResearchReport as ResearchReportV1

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
EVIDENCE_ID = UUID("10000000-0000-4000-8000-000000000001")


def _citation() -> ResearchEvidenceCitation:
    return ResearchEvidenceCitation(
        evidence_id=EVIDENCE_ID,
        source_provider_id="synthetic_provider",
        independent_source_group="synthetic_group_a",
        source_role="synthetic_fixture",
        source_label="R2 deterministic synthetic evidence",
        published_at=NOW - timedelta(days=1),
        first_seen_at=NOW - timedelta(hours=12),
        revision_known_at=NOW - timedelta(hours=12),
        locator="synthetic://research/603986/business-model",
    )


def test_instrument_search_contract_keeps_ambiguity_and_pit_explicit() -> None:
    query = InstrumentSearchQuery(schema_version="1.0", query=" 兆易创新 ", limit=10, as_of=NOW)
    result = InstrumentSearchResult(
        schema_version="1.0",
        query=query.normalized_query,
        matches=[
            InstrumentSearchMatch(
                schema_version="1.0",
                instrument_id="XSHG:603986",
                symbol="603986",
                exchange="XSHG",
                short_name="兆易创新",
                legal_name="北京兆易创新科技股份有限公司",
                listing_status=ListingStatus.ACTIVE,
                match_type=InstrumentSearchMatchType.EXACT_SHORT_NAME,
                revision_known_at=NOW - timedelta(days=1),
            )
        ],
        as_of=NOW,
        searched_at=NOW,
    )

    assert query.normalized_query == "兆易创新"
    assert result.requires_confirmation is False
    assert result.matches[0].instrument_id == "XSHG:603986"

    with pytest.raises(ValidationError, match="known after as_of"):
        InstrumentSearchResult(
            schema_version="1.0",
            query="603986",
            matches=[
                result.matches[0].model_copy(
                    update={"revision_known_at": NOW + timedelta(seconds=1)}
                )
            ],
            as_of=NOW,
            searched_at=NOW,
        )


def test_report_claim_types_enforce_deterministic_evidence_rules() -> None:
    with pytest.raises(ValidationError, match="fact claims require evidence"):
        ResearchClaim(
            schema_version="1.0",
            claim_id=uuid4(),
            claim_type=ClaimType.FACT,
            section=ResearchSectionKind.BUSINESS_MODEL,
            statement="公司业务模式发生变化。",
            publication_eligible=True,
        )


def test_provenance_validators_reject_invalid_times_and_claim_shapes() -> None:
    citation = _citation()
    with pytest.raises(ValidationError, match="actual_end cannot be earlier"):
        CoverageGap(
            area="quarterly_financials",
            reason_code="SOURCE_GAP",
            detail="合成缺口",
            actual_start=NOW,
            actual_end=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="revision_known_at cannot be earlier"):
        ResearchEvidenceCitation.model_validate(
            {
                **citation.model_dump(mode="python"),
                "revision_known_at": citation.first_seen_at - timedelta(seconds=1),
            }
        )

    calculation = ResearchCalculation(
        formula="input",
        rule_version="synthetic.v1",
        inputs=[
            ResearchCalculationInput(
                name="input",
                value="1",
                unit="ratio",
                evidence_ids=[EVIDENCE_ID],
            )
        ],
        cutoff=NOW,
    )
    mismatched_calculation = calculation.model_copy(
        update={
            "inputs": [
                calculation.inputs[0].model_copy(
                    update={"evidence_ids": [UUID("10000000-0000-4000-8000-000000000099")]}
                )
            ]
        }
    )
    invalid_claims: tuple[dict[str, object], ...] = (
        {
            "claim_type": ClaimType.FACT,
            "evidence": [citation],
            "calculation": calculation,
            "publication_eligible": True,
        },
        {
            "claim_type": ClaimType.SYSTEM_INFERENCE,
            "evidence": [citation],
            "publication_eligible": True,
        },
        {
            "claim_type": ClaimType.SYSTEM_INFERENCE,
            "calculation": calculation,
            "publication_eligible": True,
        },
        {
            "claim_type": ClaimType.SYSTEM_INFERENCE,
            "evidence": [citation],
            "calculation": mismatched_calculation,
            "publication_eligible": True,
        },
        {
            "claim_type": ClaimType.FACT,
            "evidence": [citation, citation],
            "publication_eligible": True,
        },
        {
            "claim_type": ClaimType.FACT,
            "evidence": [citation],
            "publication_eligible": False,
        },
    )
    for fields in invalid_claims:
        with pytest.raises(ValidationError):
            ResearchClaim.model_validate(
                {
                    "schema_version": "1.0",
                    "claim_id": uuid4(),
                    "section": ResearchSectionKind.BUSINESS_MODEL,
                    "statement": "合成主张",
                    **fields,
                }
            )

    with pytest.raises(ValidationError, match="model hypotheses require"):
        ResearchClaim(
            schema_version="1.0",
            claim_id=uuid4(),
            claim_type=ClaimType.MODEL_HYPOTHESIS,
            section=ResearchSectionKind.BULL_CASE,
            statement="需求结构可能改善。",
            evidence=[_citation()],
            publication_eligible=True,
        )


def test_report_requires_all_ten_sections_and_rejects_advice_language() -> None:
    claim = ResearchClaim(
        schema_version="1.0",
        claim_id=uuid4(),
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="合成资料记载收入 126.40 百万元, 历史覆盖五年, 仅用于验证产品链路。",
        evidence=[_citation()],
        publication_eligible=True,
    )
    sections = [
        ResearchReportSection(
            kind=kind,
            title=kind.value,
            summary="本区块基于合成证据说明覆盖、未知项与可核验边界。",
            claim_ids=[claim.claim_id] if kind is claim.section else [],
        )
        for kind in ResearchSectionKind
    ]
    report = ResearchReport(
        schema_version="1.0",
        report_id=uuid4(),
        session_id=uuid4(),
        instrument_id="XSHG:603986",
        instrument_name="兆易创新(合成测试标的)",
        as_of=NOW,
        analysis_config=ResearchAnalysisConfig(schema_version="1.0"),
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        coverage_start=NOW - timedelta(days=365 * 5),
        coverage_end=NOW,
        sections=sections,
        claims=[claim],
        model_provider="synthetic_research",
        model_name="deterministic-structured-report-v1",
        model_version="1.0",
        generator_version="r2.synthetic.v1",
        validator_version="research-publication-gate.v1",
        rule_version="macro-reference.v1",
        data_manifest_sha256="a" * 64,
        generated_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )

    assert len(report.sections) == 10
    assert report.paper_only is True

    numeric_payload = report.model_dump(mode="python")
    numeric_payload["sections"][0]["summary"] = claim.statement
    hypothesis = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.MODEL_HYPOTHESIS,
        section=ResearchSectionKind.BULL_CASE,
        statement="收入 126.40 百万元仅是现有规模的依据, 持续性仍需核对。",
        evidence=[_citation()],
        counter_evidence=[_citation()],
        falsification_conditions=["若五年历史口径无法比较, 则不能据此判断持续性。"],
        uncertainty="五年历史覆盖不代表未来收入预测。",
        publication_eligible=True,
    )
    numeric_payload["claims"].append(hypothesis.model_dump(mode="python"))
    for section in numeric_payload["sections"]:
        if section["kind"] is ResearchSectionKind.BULL_CASE:
            section["claim_ids"].append(hypothesis.claim_id)
    numeric_report = ResearchReport.model_validate(numeric_payload)
    assert numeric_report.sections[0].summary == claim.statement
    legacy_report = ResearchReportV1.from_current(numeric_report)
    assert legacy_report.sections[0].summary == claim.statement
    assert legacy_report.claims[-1].statement == hypothesis.statement
    assert legacy_report.claims[-1].falsification_conditions == hypothesis.falsification_conditions
    assert legacy_report.claims[-1].uncertainty == hypothesis.uncertainty
    with pytest.raises(ValueError, match="real source mode"):
        ResearchReportV1.from_current(
            numeric_report.model_copy(
                update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY}
            )
        )

    advice_payload = report.model_dump(mode="python")
    advice_payload["claims"][0]["statement"] = "建议买入并给出目标价。"
    with pytest.raises(ValidationError, match="investment-advice language"):
        ResearchReport.model_validate(advice_payload)

    context = MutationContext(
        schema_version="1.0",
        as_of=NOW,
        requested_at=NOW,
        idempotency_key="research-artifact-write-contract-0001",
    )
    with pytest.raises(ValidationError, match="version requires an artifact"):
        ResearchReportWriteRequest(
            schema_version="1.0",
            context=context,
            job_id=uuid4(),
            report=report,
            artifact_object_version_id="version-1",
        )

    artifact_report = ResearchReport.model_validate(
        {
            **report.model_dump(mode="python"),
            "artifact_object_key": "research/session/report.json",
            "artifact_content_sha256": "b" * 64,
            "artifact_content_length": 1024,
        }
    )
    write_request = ResearchReportWriteRequest(
        schema_version="1.0",
        context=context,
        job_id=uuid4(),
        report=artifact_report,
        artifact_object_version_id="version-1",
    )
    assert write_request.artifact_object_version_id == "version-1"
    with pytest.raises(ValidationError, match="at least 1 character"):
        ResearchReportWriteRequest(
            schema_version="1.0",
            context=context,
            job_id=uuid4(),
            report=artifact_report,
            artifact_object_version_id="",
        )
