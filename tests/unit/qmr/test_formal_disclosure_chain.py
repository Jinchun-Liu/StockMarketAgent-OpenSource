"""Adoption evidence uses the actual coordinator, governed HTTP/PDF and audits."""

from datetime import UTC, datetime

from tests.integration.next_source_fixture import official_run
from tests.unit.qmr import test_research_fallback as helpers

from stock_assistant.contracts.research import ResearchAvailableResult
from stock_assistant.modules.research.reporting import ResearchReportGenerator

NOW = datetime(2026, 9, 19, tzinfo=UTC)


class FrozenTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


async def test_undated_parent_to_formal_pdf_gains_verified_facts(monkeypatch):
    monkeypatch.setattr(helpers.fallback_module, "datetime", FrozenTime)
    session = helpers._session(NOW).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "source_mode": helpers.ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    snapshots = []

    async def save(state, packet):
        snapshots.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    result, metrics = await official_run(
        session, None, helpers.empty_real_evidence_packet(session), save, formal_attachment=True
    )
    final = snapshots[-1][0]
    assert {"identity.company", "business.core_products", "financial.quality"} == {
        fact.key for fact in result.facts
    }, [(g.area, g.detail) for g in result.coverage_gaps]
    assert all(fact.data_audit_status == "passed" for fact in result.facts)
    assert metrics["requests"].count(("www.zmax-opt.com", "/files/annual.pdf")) == 1
    assert all(
        c.formal_document is not None and c.locator.endswith("/files/annual.pdf")
        for c in result.citations
    )
    assert all(n <= 4 for n in final.attempts.values())
    assert all(len(row) <= 5 for row in final.round_sources.values())
    assert any(len(row) >= 2 for row in final.round_sources.values())
    assert any(
        o.attachment_candidates and o.stage != "verified" for o in final.verification_outcomes
    )
    assert all(d.formal_document is not None for d in final.documents)
    assert all("annual.pdf" in str(d.candidate.url) for d in final.documents)
    for citation in result.citations:
        assert citation.formal_document is not None
        assert set(citation.formal_document.fact_locations) == {
            fact.key for fact in result.facts if fact.evidence_id == citation.evidence_id
        }
    available = ResearchReportGenerator.acquisition_result(session, result)
    restored = ResearchAvailableResult.model_validate_json(available.model_dump_json())
    assert restored.schema_version == "2.1"
    assert len(restored.claims) == 3
    assert all(c.formal_document is not None for claim in restored.claims for c in claim.evidence)


def test_financial_repeated_spans_share_the_original_context_budget():
    text = "合并利润表\n单位: 人民币元\n项目 2025年度 2024年度\n营业收入 100 90\n" + "资料" * 10000
    spans = helpers.fallback_module._financial_source_spans(text)
    assert spans
    assert helpers.fallback_module._source_payload_chars(text, ["financial.quality"]) == (
        len(text) + sum(len(value) for value in spans.values())
    )
    assert helpers.fallback_module._source_payload_chars(text, ["identity.company"]) == len(text)
