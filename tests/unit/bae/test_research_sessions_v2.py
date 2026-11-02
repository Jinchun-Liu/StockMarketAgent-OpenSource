from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from apps.api.http import RequestContextMiddleware, application_error_handler
from apps.api.identity import require_principal
from apps.api.routes import research_sessions, research_sessions_v2
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.research import (
    ClaimType,
    ResearchAnalysisConfig,
    ResearchAvailableResult,
    ResearchClaim,
    ResearchEvidenceAcquisitionMode,
    ResearchEvidenceCitation,
    ResearchEvidenceSourceLevel,
    ResearchEvidenceVerificationStatus,
    ResearchProgress,
    ResearchReport,
    ResearchReportLookupResult,
    ResearchReportSection,
    ResearchReportV1_2,
    ResearchReportV2,
    ResearchReportWriteRequest,
    ResearchSectionKind,
    ResearchSessionListQuery,
    ResearchSessionLookupRequest,
    ResearchSessionLookupResult,
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
    ResearchSessionPage,
    ResearchSessionStatus,
    ResearchSessionView,
    ResearchSourceMode,
    ResearchStage,
    validate_stored_research_report,
)
from stock_assistant.contracts.research import (
    ResearchReaderReportV1_1 as ResearchReportV1_1,
)
from stock_assistant.contracts.research_reader import ResearchScoreBasis
from stock_assistant.contracts.security import AuthenticatedPrincipal
from stock_assistant.modules.research.reporting import (
    project_retained_report_for_reader,
    upgrade_legacy_report_for_reader,
)
from stock_assistant.modules.research.scoring import (
    build_model_unavailable_scorecard,
    build_synthetic_unscored_scorecard,
    build_web_retrieval_disclosure,
)

SESSION_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class _HiddenRepository:
    async def get(self, request: ResearchSessionLookupRequest) -> ResearchSessionLookupResult:
        return ResearchSessionLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=False,
            as_of=request.context.as_of,
        )

    async def delete(
        self,
        request: ResearchSessionMutationRequest,
    ) -> ResearchSessionMutationResult:
        return ResearchSessionMutationResult(
            schema_version="1.0",
            session_id=request.session_id,
            status=ResearchSessionStatus.DELETED,
            processed_at=request.context.requested_at,
            reason_code="RESEARCH_SESSION_NOT_FOUND",
        )

    cancel = delete
    retry = delete

    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=False,
            as_of=request.context.as_of,
            reason_code="RESEARCH_REPORT_NOT_FOUND",
        )


def _legacy_report() -> ResearchReport:
    evidence = ResearchEvidenceCitation(
        evidence_id=uuid4(),
        source_provider_id="synthetic_provider",
        independent_source_group="synthetic_group",
        source_role="synthetic_fixture",
        source_label="历史合成证据",
        published_at=NOW - timedelta(days=2),
        first_seen_at=NOW - timedelta(days=1),
        revision_known_at=NOW - timedelta(days=1),
        locator="synthetic://research/legacy/business-model",
    )
    claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.BUSINESS_MODEL,
        statement="该历史报告使用可追溯的合成证据。",
        evidence=[evidence],
        publication_eligible=True,
    )
    return ResearchReport(
        schema_version="1.0",
        session_id=SESSION_ID,
        instrument_id="XSHG:603986",
        instrument_name="兆易创新(合成测试标的)",
        as_of=NOW,
        analysis_config=ResearchAnalysisConfig(schema_version="1.0"),
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        coverage_start=NOW - timedelta(days=365),
        coverage_end=NOW,
        sections=[
            ResearchReportSection(
                kind=kind,
                title=kind.value,
                summary="历史专业报告的合成摘要, 用于验证兼容阅读。",
                claim_ids=[claim.claim_id] if kind is claim.section else [],
            )
            for kind in ResearchSectionKind
        ],
        claims=[claim],
        model_provider="synthetic_research",
        model_name="legacy-report",
        model_version="1.0",
        generator_version="r2.synthetic.v1",
        validator_version="research-publication-gate.v1",
        rule_version="macro-reference.v1",
        data_manifest_sha256="a" * 64,
        generated_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )


class _LegacyReportRepository(_HiddenRepository):
    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=True,
            report=_legacy_report(),
            as_of=NOW,
        )


def _web_report() -> ResearchReport:
    report = upgrade_legacy_report_for_reader(_legacy_report())
    source = report.claims[0].evidence[0]
    web_source = ResearchEvidenceCitation.model_validate(
        {
            **source.model_dump(mode="python"),
            "acquisition_mode": ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB,
            "source_level": ResearchEvidenceSourceLevel.COMPANY_OFFICIAL,
            "verification_status": ResearchEvidenceVerificationStatus.SINGLE_SOURCE,
            "revision_known_at": source.published_at,
            "content_sha256": "b" * 64,
            "rights_snapshot_sha256": "c" * 64,
        }
    )
    claim = report.claims[0].model_copy(update={"evidence": [web_source]})
    receipt_id = uuid4()
    return ResearchReport.model_validate(
        report.model_copy(
            update={
                "claims": [claim],
                "web_retrieval_disclosure": build_web_retrieval_disclosure(
                    citations=[web_source],
                    receipt_ids=[receipt_id],
                ),
            }
        ).model_dump(mode="python")
    )


class _WebReportRepository(_HiddenRepository):
    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=True,
            report=_web_report(),
            as_of=NOW,
        )


def _retained_report_payload(
    *,
    schema_version: str,
    validator_version: str | None = None,
) -> dict[str, object]:
    legacy = _legacy_report()
    if schema_version == "1.1":
        payload = upgrade_legacy_report_for_reader(legacy).model_dump(mode="python")
        payload["validator_version"] = validator_version or "r2-publication-validator.v2"
    else:
        payload = legacy.model_dump(mode="python")
        payload["validator_version"] = "r2-publication-validator.v1"
    sections = payload["sections"]
    assert isinstance(sections, list)
    first_section = sections[0]
    assert isinstance(first_section, dict)
    first_section["summary"] = "一方面保留原始专业结论, 另一方面继续核对证据。"
    return payload


class _RetainedReportRepository(_HiddenRepository):
    def __init__(self, *, schema_version: str, validator_version: str) -> None:
        self._schema_version = schema_version
        self._validator_version = validator_version

    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=True,
            report=validate_stored_research_report(
                _retained_report_payload(
                    schema_version=self._schema_version,
                    validator_version=self._validator_version,
                ),
                trusted_validator_version=self._validator_version,
            ),
            as_of=NOW,
        )


class _ExistingReportRepository(_HiddenRepository):
    def __init__(self, report: ResearchReport) -> None:
        self.report = report

    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=True,
            report=self.report,
            as_of=NOW,
        )


class _HashMismatchStoredReportRepository(_HiddenRepository):
    """Model the repository's fail-closed result after stored hash verification fails."""

    async def get_report(self, request: ResearchSessionLookupRequest) -> ResearchReportLookupResult:
        return ResearchReportLookupResult(
            schema_version="1.0",
            session_id=request.session_id,
            found=False,
            as_of=NOW,
            reason_code="REPORT_COMPATIBILITY_PROJECTION_FAILED",
        )


def _old_scored_english_report(
    *,
    source_mode: ResearchSourceMode,
) -> ResearchReportV1_1:
    payload = upgrade_legacy_report_for_reader(_legacy_report()).model_dump(mode="python")
    payload.update(
        {
            "source_mode": source_mode,
            "generator_version": "r2-readable-research-generator.v1.5",
            "validator_version": "r2-publication-validator.v2.3",
        }
    )

    claims = payload["claims"]
    assert isinstance(claims, list)
    first_claim = ResearchClaim.model_validate(claims[0])
    second_evidence = first_claim.evidence[0].model_copy(
        update={
            "evidence_id": uuid4(),
            "locator": "synthetic://research/legacy/follow-up",
        }
    )
    second_claim = ResearchClaim(
        schema_version="1.0",
        claim_type=ClaimType.FACT,
        section=ResearchSectionKind.FALSIFICATION_AND_FOLLOW_UP,
        statement="该历史报告还保留了一条独立的后续核对主张。",
        evidence=[second_evidence],
        publication_eligible=True,
    )
    claims.append(second_claim.model_dump(mode="python"))
    sections = payload["sections"]
    assert isinstance(sections, list)
    follow_up_section = next(
        item
        for item in sections
        if isinstance(item, dict)
        and item["kind"] == ResearchSectionKind.FALSIFICATION_AND_FOLLOW_UP
    )
    follow_up_section["claim_ids"] = [second_claim.claim_id]
    payload["coverage_gaps"] = [
        {
            "area": "business.product_structure_change",
            "reason_code": "WEB_DISCOVERY_LIMITED",
            "detail": "现有历史资料未说明产品结构调整方向。",
        }
    ]

    scorecard = payload["scorecard"]
    assert isinstance(scorecard, dict)
    dimensions = scorecard["dimensions"]
    assert isinstance(dimensions, list)
    for dimension in dimensions:
        assert isinstance(dimension, dict)
        dimension.update(
            {
                "evidence_grade": 2,
                "raw_score": "50.0",
                "reliability_factor": "1.0",
                "adjusted_score": "50.0",
                "reasons": ["Legacy model score explanation."],
                "unscored_reason": None,
            }
        )
    scorecard.update(
        {
            "status": "scored",
            "overall_score": "50.0",
            "score_band": "优劣并存",
            "score_meaning": "Legacy numeric company-quality score.",
            "evidence_confidence": "100.0",
            "evidence_confidence_meaning": "Legacy evidence confidence.",
            "scoring_rule_version": "company-quality-score.v1",
        }
    )

    reader = payload["reader_edition"]
    assert isinstance(reader, dict)
    reader.update(
        {
            "one_sentence_takeaway": "Legacy English reader takeaway.",
            "how_the_company_makes_money": ["Legacy business model explanation."],
            "why_this_score": ["Legacy numeric score explanation."],
            "main_strengths": ["Legacy strength."],
            "main_risks": ["Legacy risk."],
            "unknowns": ["Legacy unknown."],
            "glossary": [],
            "safe_markdown": "# Legacy reader\n\nThis is the retained English reader.",
        }
    )
    return ResearchReportV1_1.model_validate(payload)


def _current_synthetic_report() -> ResearchReportV1_1:
    retained = project_retained_report_for_reader(
        _old_scored_english_report(source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY)
    )
    payload = retained.model_dump(mode="python")
    reader = payload["reader_edition"]
    assert isinstance(reader, dict)
    reader["one_sentence_takeaway"] = "这是当前模型生成且必须原样保留的中文结论。"
    payload.update(
        {
            "generator_version": "r2-readable-research-generator.v1.6",
            "validator_version": "r2-publication-validator.v2.4",
            "scorecard": build_synthetic_unscored_scorecard().model_dump(mode="python"),
            "artifact_object_key": f"research/{SESSION_ID}/current-report.json",
            "artifact_content_sha256": "d" * 64,
            "artifact_content_length": 4096,
        }
    )
    return ResearchReportV1_1.model_validate(payload)


def _app(monkeypatch: pytest.MonkeyPatch, *, authenticated: bool) -> FastAPI:
    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.include_router(research_sessions_v2.router, prefix="/api/v2")
    app.include_router(research_sessions_v2.legacy_report_router, prefix="/api/v2")
    monkeypatch.setattr(research_sessions_v2, "_require_synthetic_r2", lambda **_kwargs: None)
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: _HiddenRepository())
    if authenticated:
        principal = AuthenticatedPrincipal(
            schema_version="1.0",
            principal_sha256="a" * 64,
            issuer="https://cognito-idp.ap-northeast-1.amazonaws.com/pool",
            username="owner",
            authenticated_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
        app.dependency_overrides[require_principal] = lambda: principal
    return app


def test_v2_requires_bearer_and_publishes_auth_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = TestClient(_app(monkeypatch, authenticated=False)).get(
        f"/api/v2/research-sessions/{SESSION_ID}"
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_v2_hides_non_owned_session_for_read_and_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_app(monkeypatch, authenticated=True))

    read = client.get(f"/api/v2/research-sessions/{SESSION_ID}")
    legacy_report = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report")
    readable_report = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")
    deleted = client.delete(
        f"/api/v2/research-sessions/{SESSION_ID}",
        headers={"Idempotency-Key": "owner-isolation-delete-0001"},
    )

    assert read.status_code == 404
    assert legacy_report.status_code == 404
    assert readable_report.status_code == 404
    assert deleted.status_code == 404
    assert read.json()["detail"] == "RESEARCH_SESSION_NOT_FOUND"
    assert legacy_report.json()["detail"] == "RESEARCH_REPORT_NOT_FOUND"
    assert readable_report.json()["detail"] == "RESEARCH_REPORT_NOT_FOUND"
    assert deleted.json()["detail"] == "RESEARCH_SESSION_NOT_FOUND"


def test_v2_lists_the_owner_authenticated_21_method_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = TestClient(_app(monkeypatch, authenticated=True)).get(
        "/api/v2/research-skills",
        params={"instrument_id": "XSHG:603986"},
    )

    assert response.status_code == 200
    catalog = response.json()["data"]
    assert catalog["instrument_id"] == "XSHG:603986"
    assert len(catalog["items"]) == 21
    assert sum(item["is_core"] for item in catalog["items"]) == 9


def test_search_keeps_v1_strict_and_exposes_verified_names_only_on_directory_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    application = _app(monkeypatch, authenticated=True)
    application.include_router(research_sessions.router, prefix="/api/v1")
    monkeypatch.setattr(research_sessions, "_require_synthetic_r2", lambda **_kwargs: None)
    monkeypatch.setattr(research_sessions, "utc_now", lambda: search_now)
    monkeypatch.setattr(research_sessions_v2, "utc_now", lambda: search_now)
    client = TestClient(application)

    legacy = client.get("/api/v1/instruments/search", params={"q": "603986"})
    compatible_v2 = client.get("/api/v2/instruments/search", params={"q": "603986"})
    directory = client.get("/api/v2/instruments/directory-search", params={"q": "603986"})

    assert legacy.status_code == compatible_v2.status_code == directory.status_code == 200
    schema = json.loads(
        Path("schemas/instruments/search-result.v1.0.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for response in (legacy, compatible_v2):
        result = response.json()["data"]
        validator.validate(result)
        assert result["schema_version"] == "1.0"
        assert "legal_name_verified" not in result["matches"][0]
    verified_result = directory.json()["data"]
    assert verified_result["schema_version"] == "1.1"
    assert verified_result["matches"][0]["legal_name_verified"] is True


def test_v2_projects_retained_legacy_report_into_reader_edition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(
        research_sessions_v2,
        "_repository",
        lambda _request: _LegacyReportRepository(),
    )

    response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["schema_version"] == "1.1"
    assert report["scorecard"]["status"] == "not_scored"
    assert report["scorecard"]["overall_score"] is None
    assert len(report["skill_executions"]) == 21


def test_v2_projects_old_synthetic_v1_1_to_chinese_unscored_view_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_report = _old_scored_english_report(
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY
    )
    original = old_report.model_dump(mode="json")
    repository = _ExistingReportRepository(old_report)
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: repository)

    response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["schema_version"] == "1.1"
    assert report["scorecard"]["status"] == "not_scored"
    assert report["scorecard"]["overall_score"] is None
    assert report["scorecard"]["score_band"] is None
    assert report["reader_edition"]["one_sentence_takeaway"] == (
        "这是保留期内的合成研究, 仅用于验证研究流程。"
    )
    assert report["reader_edition"]["how_the_company_makes_money"] == [
        "核心产品或服务: 当前证据未说明具体产品或服务。",
        "客户到收入链: 当前证据未说明客户如何转化为收入和利润。",
        "产品结构调整: 当前证据未说明具体产品或调整方向。",
    ]
    assert report["reader_edition"]["claim_ids"] == [
        str(claim.claim_id) for claim in old_report.claims
    ]
    assert report["coverage_gaps"] == original["coverage_gaps"]
    assert "现有历史资料未说明产品结构调整方向。" in report["reader_edition"]["safe_markdown"]
    assert "Legacy English reader" not in report["reader_edition"]["safe_markdown"]
    assert old_report.model_dump(mode="json") == original


def test_v2_projects_previous_synthetic_reader_without_score_or_artifact_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_report = _current_synthetic_report()
    original = current_report.model_dump(mode="json")
    repository = _ExistingReportRepository(current_report)
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: repository)

    response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["reader_edition"]["one_sentence_takeaway"] == (
        "这是保留期内的合成研究, 仅用于验证研究流程。"
    )
    assert report["scorecard"]["status"] == "not_scored"
    assert report["scorecard"]["overall_score"] is None
    assert report["scorecard"]["score_band"] is None
    assert report["reader_edition"]["claim_ids"] == [
        str(claim.claim_id) for claim in current_report.claims
    ]
    assert report["coverage_gaps"] == original["coverage_gaps"]
    assert report["artifact_object_key"] is None
    assert report["artifact_content_sha256"] is None
    assert report["artifact_content_length"] is None
    assert current_report.model_dump(mode="json") == original


def test_reader_projection_checks_every_prose_field_not_only_the_takeaway() -> None:
    current_report = _current_synthetic_report()
    payload = current_report.model_dump(mode="python")
    reader = payload["reader_edition"]
    assert isinstance(reader, dict)
    reader["main_risks"] = ["Legacy English text hidden below a Chinese headline."]
    payload.update(
        {
            "generator_version": "r2-readable-research-generator.v1.7",
            "validator_version": "r2-publication-validator.v2.5",
        }
    )

    projected = project_retained_report_for_reader(ResearchReportV1_1.model_validate(payload))

    assert projected.reader_edition.one_sentence_takeaway == (
        "这是保留期内的合成研究, 仅用于验证研究流程。"
    )
    assert projected.reader_edition.main_risks == [
        "历史易读文字不符合当前规则, 本视图不补造新事实。"
    ]
    assert "Legacy" not in projected.reader_edition.safe_markdown


def test_v2_projects_production_reader_to_chinese_but_preserves_legal_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    production_report = _old_scored_english_report(
        source_mode=ResearchSourceMode.PRODUCTION_AUTHORIZED
    )
    original = production_report.model_dump(mode="json")
    repository = _ExistingReportRepository(production_report)
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: repository)

    response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["source_mode"] == "production_authorized"
    assert report["scorecard"]["status"] == "scored"
    assert report["scorecard"]["overall_score"] == "50.0"
    assert report["scorecard"] == {
        name: value for name, value in original["scorecard"].items() if name != "scoring_basis"
    }
    assert report["reader_edition"]["one_sentence_takeaway"] == (
        "这是保留期内的研究结果, 已按当前易读规则整理。"
    )
    assert report["reader_edition"]["claim_ids"] == [
        str(claim.claim_id) for claim in production_report.claims
    ]
    assert report["coverage_gaps"] == original["coverage_gaps"]
    assert "现有历史资料未说明产品结构调整方向。" in report["reader_edition"]["safe_markdown"]
    assert "Legacy" not in report["reader_edition"]["safe_markdown"]
    assert all(
        "合成资料" not in explanation for explanation in report["reader_edition"]["why_this_score"]
    )
    assert report["artifact_object_key"] is None
    assert report["artifact_content_sha256"] is None
    assert report["artifact_content_length"] is None
    assert production_report.model_dump(mode="json") == original


@pytest.mark.parametrize(
    ("schema_version", "validator_version"),
    [
        ("1.0", "r2-publication-validator.v1"),
        ("1.1", "r2-publication-validator.v2"),
        ("1.1", "r2-publication-validator.v2.1"),
    ],
)
def test_v2_reads_retained_reports_with_their_published_validator_semantics(
    monkeypatch: pytest.MonkeyPatch,
    schema_version: str,
    validator_version: str,
) -> None:
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(
        research_sessions_v2,
        "_repository",
        lambda _request: _RetainedReportRepository(
            schema_version=schema_version,
            validator_version=validator_version,
        ),
    )

    response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["schema_version"] == "1.1"
    assert report["sections"][0]["summary"] == ("一方面保留原始专业结论, 另一方面继续核对证据。")
    round_tripped = ResearchReportV1_1.model_validate(report)
    assert round_tripped.sections[0].summary == report["sections"][0]["summary"]
    if schema_version == "1.0":
        assert report["scorecard"]["status"] == "not_scored"
        assert all(not item["evidence_ids"] for item in report["skill_executions"])
        assert all(not item["claim_ids"] for item in report["skill_executions"])


def test_stored_report_compatibility_never_weakens_current_evidence_cutoff() -> None:
    validator_version = "r2-publication-validator.v2.10"
    payload = _retained_report_payload(
        schema_version="1.1",
        validator_version="r2-publication-validator.v2.1",
    )
    payload["validator_version"] = validator_version
    claims = payload["claims"]
    assert isinstance(claims, list)
    first_claim = claims[0]
    assert isinstance(first_claim, dict)
    evidence = first_claim["evidence"]
    assert isinstance(evidence, list)
    first_evidence = evidence[0]
    assert isinstance(first_evidence, dict)
    first_evidence["revision_known_at"] = NOW + timedelta(seconds=1)

    with pytest.raises(ValidationError, match="future revision"):
        validate_stored_research_report(
            payload,
            trusted_validator_version=validator_version,
        )


def test_stored_report_compatibility_cannot_cross_the_strict_write_boundary() -> None:
    retained = _old_scored_english_report(source_mode=ResearchSourceMode.PRODUCTION_AUTHORIZED)
    assert retained.reader_edition.claim_ids != [claim.claim_id for claim in retained.claims]

    with pytest.raises(
        ValidationError,
        match="new reader editions must include every report claim in order",
    ):
        ResearchReportWriteRequest(
            schema_version="1.0",
            context=MutationContext(
                schema_version="1.0",
                as_of=NOW,
                requested_at=NOW,
                idempotency_key="retained-report-must-not-be-republished",
            ),
            job_id=uuid4(),
            report=retained,
        )


def test_stored_report_compatibility_keeps_safety_validation_strict() -> None:
    payload = _retained_report_payload(
        schema_version="1.1",
        validator_version="r2-publication-validator.v2.1",
    )
    sections = payload["sections"]
    assert isinstance(sections, list)
    first_section = sections[0]
    assert isinstance(first_section, dict)
    first_section["summary"] = "建议买入并给出目标价。"

    with pytest.raises(ValidationError, match="investment-advice language"):
        validate_stored_research_report(
            payload,
            trusted_validator_version="r2-publication-validator.v2.1",
        )


def test_retained_v2_wire_round_trip_preserves_historical_web_semantics() -> None:
    payload = _web_report().model_dump(mode="python")
    payload["validator_version"] = "r2-publication-validator.v2"
    claims = payload["claims"]
    assert isinstance(claims, list)
    claim = claims[0]
    assert isinstance(claim, dict)
    evidence = claim["evidence"]
    assert isinstance(evidence, list)
    citation = evidence[0]
    assert isinstance(citation, dict)
    citation["first_seen_at"] = NOW + timedelta(minutes=1)
    disclosure = payload["web_retrieval_disclosure"]
    assert isinstance(disclosure, dict)
    disclosure["receipt_ids"] = []

    retained = ResearchReportV1_1.model_validate(payload)
    wire = retained.model_dump(mode="json")
    round_tripped = ResearchReportV1_1.model_validate(wire)

    assert round_tripped.web_retrieval_disclosure.receipt_ids == []
    assert round_tripped.claims[0].evidence[0].first_seen_at > round_tripped.as_of

    payload["validator_version"] = "r2-publication-validator.v2.1"
    with pytest.raises(ValidationError):
        ResearchReportV1_1.model_validate(payload)


def test_stored_report_requires_independently_trusted_validator_metadata() -> None:
    payload = _retained_report_payload(
        schema_version="1.1",
        validator_version="r2-publication-validator.v2.1",
    )

    with pytest.raises(ValueError, match="trusted metadata"):
        validate_stored_research_report(
            payload,
            trusted_validator_version="r2-publication-validator.v2",
        )


def test_report_routes_map_stored_hash_mismatch_to_safe_stable_412(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = _app(monkeypatch, authenticated=True)
    application.include_router(research_sessions.router, prefix="/api/v1")
    monkeypatch.setattr(research_sessions, "_require_synthetic_r2", lambda **_kwargs: None)
    monkeypatch.setattr(
        research_sessions,
        "_repository",
        lambda _request: _HashMismatchStoredReportRepository(),
    )
    monkeypatch.setattr(
        research_sessions_v2,
        "_repository",
        lambda _request: _HashMismatchStoredReportRepository(),
    )
    client = TestClient(application)

    with caplog.at_level("DEBUG"):
        responses = [
            client.get(f"/api/v1/research-sessions/{SESSION_ID}/report"),
            client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1"),
        ]

    for response in responses:
        assert response.status_code == 412
        assert response.json()["code"] == "PRECONDITION_FAILED"
        assert response.json()["detail"] == "REPORT_COMPATIBILITY_PROJECTION_FAILED"
    assert "一方面保留原始专业结论" not in caplog.text
    assert "input_value" not in caplog.text


def test_legacy_routes_fail_closed_for_web_evidence_that_v1_cannot_represent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _app(monkeypatch, authenticated=True)
    application.include_router(research_sessions.router, prefix="/api/v1")
    repository = _WebReportRepository()
    monkeypatch.setattr(research_sessions, "_require_synthetic_r2", lambda **_kwargs: None)
    monkeypatch.setattr(research_sessions, "_repository", lambda _request: repository)
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: repository)
    client = TestClient(application)

    for path in (
        f"/api/v1/research-sessions/{SESSION_ID}/report",
        f"/api/v2/research-sessions/{SESSION_ID}/report",
    ):
        response = client.get(path)
        assert response.status_code == 412
        assert response.json()["code"] == "PRECONDITION_FAILED"
        assert response.json()["detail"] == "LEGACY_REPORT_WEB_EVIDENCE_UNREPRESENTABLE"


def test_v2_openapi_declares_pkce_oauth_and_owner_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _app(monkeypatch, authenticated=False).openapi()

    scheme = document["components"]["securitySchemes"]["CognitoAuthorizationCodePKCE"]
    assert scheme["type"] == "oauth2"
    assert scheme["flows"]["authorizationCode"]["scopes"] == {
        "openid": "Authenticate the private R2 preview user."
    }
    assert "/api/v2/research-sessions" in document["paths"]
    assert "/api/v2/research-skills" in document["paths"]
    assert "/api/v2/instruments/search" in document["paths"]
    assert "/api/v2/instruments/directory-search" in document["paths"]
    assert "/api/v2/research-sessions/{session_id}/report-v1.1" in document["paths"]


@pytest.mark.parametrize("wire_version", ["1.1", "1.2"])
def test_v1_2_reader_preserves_score_and_old_version_route_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    wire_version: str,
) -> None:
    source: ResearchReport = _old_scored_english_report(
        source_mode=ResearchSourceMode.PRODUCTION_AUTHORIZED
    )
    if wire_version == "1.2":
        source = ResearchReportV1_2.model_validate(
            source.model_dump(mode="python") | {"schema_version": "1.2"}
        )
    original = source.model_dump(mode="json")
    repository = _ExistingReportRepository(source)
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: repository)

    response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.2")
    assert response.status_code == 200
    report = response.json()["data"]
    assert report["schema_version"] == "1.2"
    assert report["scorecard"] == original["scorecard"]
    assert source.model_dump(mode="json") == original
    assert report["artifact_object_key"] is None
    old_response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v1.1")
    assert old_response.status_code == (412 if wire_version == "1.2" else 200)
    if wire_version == "1.2":
        assert "REPORT_VERSION_REQUIRES_V1_2" in old_response.text


def test_counterless_v2_report_is_readable_without_lossy_legacy_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _current_synthetic_report().model_dump(mode="python")
    payload.update(
        schema_version="2.0",
        artifact_object_key=None,
        artifact_content_sha256=None,
        artifact_content_length=None,
    )
    claims = payload["claims"]
    assert isinstance(claims, list)
    claims[0].update(
        schema_version="2.0",
        claim_type="model_hypothesis",
        falsification_conditions=["若后续原始记录与本次依据不符\uff0c则需重新研究。"],
        counter_evidence=[],
    )
    payload["scorecard"] = build_model_unavailable_scorecard(
        scoring_basis=ResearchScoreBasis.SYNTHETIC_SCENARIO
    ).model_dump(mode="python")
    report = ResearchReportV2.model_validate(payload)
    repository = _ExistingReportRepository(report)
    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: repository)
    current = client.get(f"/api/v2/research-sessions/{SESSION_ID}/report-v2.0")
    assert current.status_code == 200
    data = current.json()["data"]
    assert data["schema_version"] == "2.0"
    assert data["claims"][0]["counter_evidence"] == []
    assert "当前资料未找到直接反证" in data["reader_edition"]["safe_markdown"]
    assert len(data["claims"]) == len(report.claims)
    for suffix in ("report", "report-v1.1", "report-v1.2"):
        legacy = client.get(f"/api/v2/research-sessions/{SESSION_ID}/{suffix}")
        assert legacy.status_code == 412
        assert legacy.json()["detail"] == "LEGACY_REPORT_PROJECTION_UNAVAILABLE"


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v2/research-sessions/{SESSION_ID}/view-v2.0",
        f"/api/v2/research-sessions/{SESSION_ID}/report-v2.0",
    ],
)
def test_new_read_versions_preserve_owner_isolation(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    client = TestClient(_app(monkeypatch, authenticated=True))
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "hypothesis,counter,expected", [(False, False, 200), (True, True, 200), (True, False, 412)]
)
def test_legacy_session_projects_only_losslessly_representable_current_content(
    monkeypatch: pytest.MonkeyPatch,
    hypothesis: bool,
    counter: bool,
    expected: int,
) -> None:
    claim = (
        _legacy_report()
        .claims[0]
        .model_copy(
            update={
                "schema_version": "2.0",
                "claim_type": ClaimType.MODEL_HYPOTHESIS if hypothesis else ClaimType.FACT,
                "counter_evidence": list(_legacy_report().claims[0].evidence) if counter else [],
                "falsification_conditions": ["若后续原始记录与依据不符则重新研究。"]
                if hypothesis
                else [],
            }
        )
    )
    current = ResearchSessionView(
        schema_version="1.0",
        session_id=SESSION_ID,
        job_id=uuid4(),
        instrument_id="XSHG:603986",
        instrument_name="合成兼容案例",
        as_of=NOW,
        analysis_config=ResearchAnalysisConfig(schema_version="1.0"),
        source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
        status=ResearchSessionStatus.RUNNING,
        progress=ResearchProgress(
            stage=ResearchStage.VALIDATING_REPORT,
            message="正在核验。",
            latest_progress_at=NOW,
            checkpoint="evidence-ready",
        ),
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=7),
        available_result=ResearchAvailableResult(
            schema_version="2.0",
            non_production_notice="合成测试数据\uff0c不代表真实公司。",
            source_mode=ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
            as_of=NOW,
            claims=[claim],
        ),
    )
    original = current.model_dump(mode="json")

    class Repository(_HiddenRepository):
        async def get(self, request: ResearchSessionLookupRequest) -> ResearchSessionLookupResult:
            return ResearchSessionLookupResult(
                schema_version="1.0",
                session_id=request.session_id,
                found=True,
                session=current,
                as_of=NOW,
            )

        async def list_page(self, request: ResearchSessionListQuery) -> ResearchSessionPage:
            return ResearchSessionPage(
                schema_version="1.0", items=[current], as_of=NOW, has_more=False
            )

    client = TestClient(_app(monkeypatch, authenticated=True))
    monkeypatch.setattr(research_sessions_v2, "_repository", lambda _request: Repository())
    for path in [f"/api/v2/research-sessions/{SESSION_ID}", "/api/v2/research-sessions"]:
        response = client.get(path)
        assert response.status_code == expected
        if expected == 200:
            data = response.json()["data"]
            view = data["items"][0] if "items" in data else data
            available = view["available_result"]
            assert available["schema_version"] == "1.1"
            assert len(available["claims"]) == 1
            assert available["claims"][0] == {
                **original["available_result"]["claims"][0],
                "schema_version": "1.0",
            }
        else:
            assert response.json()["detail"] == "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
    assert current.model_dump(mode="json") == original
    current_response = client.get(f"/api/v2/research-sessions/{SESSION_ID}/view-v2.0")
    assert current_response.status_code == 200
    assert current_response.json()["data"]["available_result"]["schema_version"] == "2.0"
