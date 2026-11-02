"""Immutable ResearchReport 1.0 source retained for schema compatibility."""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.research import (
    ClaimType,
    CoverageGap,
    ResearchCalculation,
    ResearchReportSection,
    ResearchSectionKind,
    ResearchSourceMode,
)
from stock_assistant.contracts.research import (
    ResearchReport as CurrentResearchReport,
)


class ResearchAnalysisConfig(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    primary_years: Literal[5] = 5
    comparison_quarters: Literal[20] = 20
    scan_listing_history: Literal[True] = True
    report_retention_days: Literal[7] = 7
    stalled_after_minutes: Literal[30] = 30


class ResearchEvidenceCitation(ContractModel):
    evidence_id: UUID
    source_provider_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    independent_source_group: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    source_role: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    source_label: str = Field(min_length=1, max_length=300)
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime
    locator: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def knowledge_times_must_be_ordered(self) -> Self:
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        return self


class ResearchClaim(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    claim_id: UUID = Field(default_factory=uuid4)
    claim_type: ClaimType
    section: ResearchSectionKind
    statement: str = Field(min_length=1, max_length=4000)
    evidence: list[ResearchEvidenceCitation] = Field(default_factory=list, max_length=100)
    calculation: ResearchCalculation | None = None
    counter_evidence: list[ResearchEvidenceCitation] = Field(default_factory=list, max_length=100)
    falsification_conditions: list[str] = Field(default_factory=list, max_length=20)
    uncertainty: str | None = Field(default=None, max_length=1000)
    publication_eligible: bool

    @model_validator(mode="after")
    def provenance_must_match_claim_type(self) -> Self:
        if not self.publication_eligible:
            raise ValueError("a published report cannot contain ineligible claims")
        if self.claim_type is ClaimType.FACT:
            if not self.evidence:
                raise ValueError("fact claims require evidence")
            if self.calculation is not None:
                raise ValueError("fact claims cannot carry a calculation")
        elif self.claim_type is ClaimType.SYSTEM_INFERENCE:
            if self.calculation is None:
                raise ValueError("system inferences require a calculation")
            if not self.evidence:
                raise ValueError("system inferences require readable evidence")
            readable_evidence_ids = {item.evidence_id for item in self.evidence}
            calculation_evidence_ids = {
                evidence_id for item in self.calculation.inputs for evidence_id in item.evidence_ids
            }
            if not calculation_evidence_ids.issubset(readable_evidence_ids):
                raise ValueError("calculation evidence must resolve to readable claim evidence")
        elif self.claim_type is ClaimType.MODEL_HYPOTHESIS:
            if not self.evidence or not self.counter_evidence or not self.falsification_conditions:
                raise ValueError(
                    "model hypotheses require evidence, counter_evidence and "
                    "falsification_conditions"
                )
        identifiers = [item.evidence_id for item in self.evidence]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("claim evidence must be unique")
        return self


class ResearchReport(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    report_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    instrument_name: str = Field(min_length=1, max_length=300)
    as_of: AwareDatetime
    analysis_config: ResearchAnalysisConfig
    source_mode: ResearchSourceMode
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime
    coverage_gaps: list[CoverageGap] = Field(default_factory=list, max_length=100)
    sections: list[ResearchReportSection] = Field(min_length=10, max_length=10)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=2000)
    model_provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    generator_version: str = Field(min_length=1, max_length=200)
    validator_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)
    data_manifest_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    artifact_object_key: str | None = Field(default=None, max_length=1024)
    artifact_content_sha256: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    artifact_content_length: int | None = Field(default=None, ge=0, le=10 * 1024 * 1024)
    generated_at: AwareDatetime
    expires_at: AwareDatetime
    paper_only: Literal[True] = True
    non_production_notice: str = Field(
        default="合成测试数据; 仅供研究与产品链路验证, 不构成投资建议。",
        min_length=1,
        max_length=300,
    )

    @classmethod
    def from_current(cls, report: CurrentResearchReport) -> Self:
        """Project a 1.1 stored report onto the immutable 1.0 read contract."""

        if not isinstance(report.source_mode, ResearchSourceMode):
            raise ValueError("real source mode cannot be represented by report 1.0")
        analysis_config = ResearchAnalysisConfig(
            schema_version="1.0",
            primary_years=report.analysis_config.primary_years,
            comparison_quarters=report.analysis_config.comparison_quarters,
            scan_listing_history=report.analysis_config.scan_listing_history,
            report_retention_days=report.analysis_config.report_retention_days,
            stalled_after_minutes=report.analysis_config.stalled_after_minutes,
        )
        claims = [
            ResearchClaim(
                schema_version="1.0",
                claim_id=claim.claim_id,
                claim_type=claim.claim_type,
                section=claim.section,
                statement=claim.statement,
                evidence=[
                    ResearchEvidenceCitation(
                        evidence_id=citation.evidence_id,
                        source_provider_id=citation.source_provider_id,
                        independent_source_group=citation.independent_source_group,
                        source_role=citation.source_role,
                        source_label=citation.source_label,
                        published_at=citation.published_at,
                        first_seen_at=citation.first_seen_at,
                        revision_known_at=citation.revision_known_at,
                        locator=citation.locator,
                    )
                    for citation in claim.evidence
                ],
                calculation=claim.calculation,
                counter_evidence=[
                    ResearchEvidenceCitation(
                        evidence_id=citation.evidence_id,
                        source_provider_id=citation.source_provider_id,
                        independent_source_group=citation.independent_source_group,
                        source_role=citation.source_role,
                        source_label=citation.source_label,
                        published_at=citation.published_at,
                        first_seen_at=citation.first_seen_at,
                        revision_known_at=citation.revision_known_at,
                        locator=citation.locator,
                    )
                    for citation in claim.counter_evidence
                ],
                falsification_conditions=claim.falsification_conditions,
                uncertainty=claim.uncertainty,
                publication_eligible=claim.publication_eligible,
            )
            for claim in report.claims
        ]
        return cls(
            schema_version="1.0",
            report_id=report.report_id,
            session_id=report.session_id,
            instrument_id=report.instrument_id,
            instrument_name=report.instrument_name,
            as_of=report.as_of,
            analysis_config=analysis_config,
            source_mode=report.source_mode,
            coverage_start=report.coverage_start,
            coverage_end=report.coverage_end,
            coverage_gaps=report.coverage_gaps,
            sections=report.sections,
            claims=claims,
            model_provider=report.model_provider,
            model_name=report.model_name,
            model_version=report.model_version,
            generator_version=report.generator_version,
            validator_version=report.validator_version,
            rule_version=report.rule_version,
            data_manifest_sha256=report.data_manifest_sha256,
            artifact_object_key=report.artifact_object_key,
            artifact_content_sha256=report.artifact_content_sha256,
            artifact_content_length=report.artifact_content_length,
            generated_at=report.generated_at,
            expires_at=report.expires_at,
            paper_only=True,
            non_production_notice=report.non_production_notice,
        )

    @model_validator(mode="after")
    def report_must_pass_publication_shape(self) -> Self:
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end cannot be earlier than coverage_start")
        if self.coverage_end > self.as_of:
            raise ValueError("coverage cannot extend beyond as_of")
        if self.expires_at <= self.generated_at:
            raise ValueError("expires_at must be later than generated_at")
        section_kinds = [item.kind for item in self.sections]
        if len(set(section_kinds)) != len(section_kinds) or set(section_kinds) != set(
            ResearchSectionKind
        ):
            raise ValueError("report requires each of the ten sections exactly once")
        claims = {item.claim_id: item for item in self.claims}
        if len(claims) != len(self.claims):
            raise ValueError("report claim IDs must be unique")
        referenced: list[UUID] = []
        for section in self.sections:
            for claim_id in section.claim_ids:
                claim = claims.get(claim_id)
                if claim is None or claim.section is not section.kind:
                    raise ValueError(
                        "section claim references must resolve within the same section"
                    )
                referenced.append(claim_id)
        if set(referenced) != set(claims):
            raise ValueError("every report claim must belong to one section")
        cited = [
            citation
            for claim in self.claims
            for citation in (*claim.evidence, *claim.counter_evidence)
        ]
        if any(citation.revision_known_at > self.as_of for citation in cited):
            raise ValueError("report evidence cannot include a future revision")
        narrative_text = [
            *(item.title for item in self.sections),
            *(item.summary for item in self.sections),
            *(item.uncertainty or "" for item in self.claims),
            *(condition for item in self.claims for condition in item.falsification_conditions),
        ]
        publication_text = "\n".join(
            [*narrative_text, *(item.statement for item in self.claims)]
        ).casefold()
        forbidden = ("建议买入", "建议卖出", "目标价", "收益承诺", "buy rating", "sell rating")
        if any(phrase in publication_text for phrase in forbidden):
            raise ValueError("investment-advice language is forbidden")
        if (
            self.source_mode is ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY
            and "合成" not in self.non_production_notice
        ):
            raise ValueError("synthetic reports require an explicit synthetic notice")
        artifact_fields = (
            self.artifact_object_key,
            self.artifact_content_sha256,
            self.artifact_content_length,
        )
        if any(value is not None for value in artifact_fields) and any(
            value is None for value in artifact_fields
        ):
            raise ValueError("artifact metadata must be populated together")
        return self


__all__ = ["ResearchReport"]
