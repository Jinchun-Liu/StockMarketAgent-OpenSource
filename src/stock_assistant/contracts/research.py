"""R2 on-demand single-instrument research contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    Field,
    ValidationInfo,
    WithJsonSchema,
    model_validator,
)

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.formal_disclosure import FormalDocumentCitation
from stock_assistant.contracts.global_events import (
    GlobalEventCatalogStatus,
    ResearchExposureProfile,
    ResearchExternalImpactMatch,
)
from stock_assistant.contracts.research_reader import (
    ModelAuthoredProse,
    ResearchReaderEdition,
    ResearchScoreBasis,
    ResearchScoreCandidate,
    ResearchScorecard,
    ResearchWebRetrievalDisclosure,
    contains_forbidden_investment_advice,
    contains_numeric_expression,
    retained_report_compatibility_version,
)
from stock_assistant.contracts.research_skills import (
    ResearchSkillExecution,
    ResearchSkillId,
    ResearchSkillSelection,
)
from stock_assistant.contracts.research_v1_1 import ResearchScorecard as FrozenResearchScorecard
from stock_assistant.contracts.storage import StoredObjectRef


class ResearchSourceMode(StrEnum):
    SYNTHETIC_DEVELOPMENT_ONLY = "synthetic_development_only"
    PRODUCTION_AUTHORIZED = "production_authorized"


class ResearchRealSourceMode(StrEnum):
    DEVELOPMENT_ONLY = "real_development_only"


ResearchExecutionSourceMode = Annotated[
    ResearchSourceMode | ResearchRealSourceMode,
    WithJsonSchema(
        {
            "type": "string",
            "enum": [mode.value for mode in (*ResearchSourceMode, *ResearchRealSourceMode)],
        }
    ),
]


class ResearchCutoffMode(StrEnum):
    """Internal execution semantics; never exposed as a public report mode."""

    CURRENT_RUN = "current_run"
    HISTORICAL_REPLAY = "historical_replay"


class ResearchEvidenceAcquisitionMode(StrEnum):
    BUILT_IN_API = "built_in_api"
    MODEL_ASSISTED_WEB = "model_assisted_web"


class ResearchEvidenceSourceLevel(StrEnum):
    REGULATORY_OFFICIAL = "regulatory_official"
    COMPANY_OFFICIAL = "company_official"
    TRUSTED_MEDIA_DATA = "trusted_media_data"
    SYNTHETIC = "synthetic"


class ResearchEvidenceVerificationStatus(StrEnum):
    INDEPENDENTLY_VERIFIED = "independently_verified"
    SINGLE_SOURCE = "single_source"
    UNVERIFIED = "unverified"


class ResearchSessionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALLED = "stalled"
    EXPIRED = "expired"
    DELETED = "deleted"


class ResearchResultCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EXECUTION_FAILED = "execution_failed"


class ResearchStage(StrEnum):
    QUEUED = "queued"
    RESOLVING_INSTRUMENT = "resolving_instrument"
    COLLECTING_HISTORY = "collecting_history"
    AUDITING_DATA = "auditing_data"
    REFETCHING_DATA = "refetching_data"
    SCORING = "scoring"
    GENERATING_REPORT = "generating_report"
    ANALYZING_BUSINESS = "analyzing_business"
    ANALYZING_FINANCIALS = "analyzing_financials"
    ANALYZING_VALUATION = "analyzing_valuation"
    ANALYZING_EVENTS = "analyzing_events"
    DEBATING_CASES = "debating_cases"
    VALIDATING_REPORT = "validating_report"
    COMPLETED = "completed"


class ClaimType(StrEnum):
    FACT = "fact"
    SYSTEM_INFERENCE = "system_inference"
    MODEL_HYPOTHESIS = "model_hypothesis"


class ResearchSectionKind(StrEnum):
    TARGET_AND_COVERAGE = "target_and_coverage"
    BUSINESS_MODEL = "business_model"
    FINANCIAL_QUALITY = "financial_quality"
    VALUATION_CONTEXT = "valuation_context"
    MAJOR_EVENTS_AND_MACRO = "major_events_and_macro"
    BULL_CASE = "bull_case"
    BEAR_CASE = "bear_case"
    CONFLICTS_AND_UNKNOWNS = "conflicts_and_unknowns"
    FALSIFICATION_AND_FOLLOW_UP = "falsification_and_follow_up"
    EVIDENCE_AND_VERSIONS = "evidence_and_versions"


class CoverageGap(ContractModel):
    area: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    detail: str = Field(min_length=1, max_length=1000)
    requested_start: AwareDatetime | None = None
    actual_start: AwareDatetime | None = None
    actual_end: AwareDatetime | None = None
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def time_range_must_be_ordered(self) -> Self:
        if self.actual_start is not None and self.actual_end is not None:
            if self.actual_end < self.actual_start:
                raise ValueError("actual_end cannot be earlier than actual_start")
        return self


class ResearchAnalysisConfig(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    primary_years: Literal[5] = 5
    comparison_quarters: Literal[20] = 20
    scan_listing_history: Literal[True] = True
    report_retention_days: Literal[7] = 7
    stalled_after_minutes: Literal[30] = 30
    skill_selection: ResearchSkillSelection | None = None


class ResearchProgress(ContractModel):
    stage: ResearchStage
    message: str = Field(min_length=1, max_length=300)
    latest_progress_at: AwareDatetime
    checkpoint: str | None = Field(default=None, max_length=200)


class ResearchSessionView(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    job_id: UUID
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    instrument_name: str = Field(min_length=1, max_length=300)
    as_of: AwareDatetime
    analysis_config: ResearchAnalysisConfig
    source_mode: ResearchExecutionSourceMode
    status: ResearchSessionStatus
    progress: ResearchProgress
    coverage_gaps: list[CoverageGap] = Field(default_factory=list, max_length=100)
    failure_reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    retryable: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    expires_at: AwareDatetime
    deleted_at: AwareDatetime | None = None
    result_completeness: ResearchResultCompleteness | None = None
    available_result: ResearchAvailableResult | None = None

    @model_validator(mode="after")
    def state_and_times_must_be_consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.status is ResearchSessionStatus.SUCCEEDED:
            if self.completed_at is None or self.progress.stage is not ResearchStage.COMPLETED:
                raise ValueError("succeeded sessions require completed progress")
        if self.status is ResearchSessionStatus.FAILED and self.failure_reason_code is None:
            raise ValueError("failed sessions require failure_reason_code")
        if self.status is ResearchSessionStatus.DELETED and self.deleted_at is None:
            raise ValueError("deleted sessions require deleted_at")
        if self.result_completeness is ResearchResultCompleteness.COMPLETE:
            if self.status is not ResearchSessionStatus.SUCCEEDED:
                raise ValueError("only succeeded sessions can have a complete report")
        if self.available_result is not None:
            if self.available_result.source_mode is not self.source_mode:
                raise ValueError("available result must retain the session data mode")
            if self.status in {ResearchSessionStatus.DELETED, ResearchSessionStatus.EXPIRED}:
                raise ValueError("inaccessible sessions cannot expose available content")
        return self


class ResearchSessionCreateBody(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    as_of: AwareDatetime | None = None
    analysis_config: ResearchAnalysisConfig = Field(
        default_factory=lambda: ResearchAnalysisConfig(schema_version="1.0")
    )
    skill_selection: ResearchSkillSelection = Field(
        default_factory=lambda: ResearchSkillSelection(schema_version="1.0")
    )


class ResearchSessionCreateRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    session_id: UUID = Field(default_factory=uuid4)
    job_id: UUID = Field(default_factory=uuid4)
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    instrument_name: str = Field(min_length=1, max_length=300)
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    analysis_config: ResearchAnalysisConfig = Field(
        default_factory=lambda: ResearchAnalysisConfig(schema_version="1.0")
    )
    source_mode: ResearchExecutionSourceMode = ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY
    cutoff_mode: ResearchCutoffMode = ResearchCutoffMode.HISTORICAL_REPLAY


class ResearchSessionAccepted(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    job_id: UUID
    status: Literal[ResearchSessionStatus.QUEUED] = ResearchSessionStatus.QUEUED
    accepted_at: AwareDatetime
    idempotent_replay: bool = False


class ResearchSessionLookupRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    session_id: UUID


class ResearchSessionLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    found: bool
    session: ResearchSessionView | None = None
    cutoff_mode: ResearchCutoffMode = ResearchCutoffMode.HISTORICAL_REPLAY
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_must_match_session(self) -> Self:
        if self.found != (self.session is not None):
            raise ValueError("found must reflect whether a session is present")
        if self.session is not None and self.session.session_id != self.session_id:
            raise ValueError("session must match session_id")
        return self


class ResearchSessionListQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)


class ResearchSessionPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    items: Sequence[ResearchSessionView] = Field(default_factory=list, max_length=100)
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False
    as_of: AwareDatetime

    @model_validator(mode="after")
    def cursor_must_match_page(self) -> Self:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("has_more must reflect whether next_cursor is present")
        return self


class ResearchEvidenceCitation(ContractModel):
    formal_document: FormalDocumentCitation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    evidence_id: UUID
    source_provider_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    independent_source_group: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    source_role: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    source_label: str = Field(min_length=1, max_length=300)
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime
    locator: str = Field(min_length=1, max_length=2048)
    acquisition_mode: ResearchEvidenceAcquisitionMode = ResearchEvidenceAcquisitionMode.BUILT_IN_API
    source_level: ResearchEvidenceSourceLevel = ResearchEvidenceSourceLevel.SYNTHETIC
    verification_status: ResearchEvidenceVerificationStatus = (
        ResearchEvidenceVerificationStatus.INDEPENDENTLY_VERIFIED
    )
    reliability_factor: DecimalString = Field(default_factory=lambda: Decimal("1.0"), ge=0, le=1)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    rights_snapshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def knowledge_times_must_be_ordered(self) -> Self:
        if self.formal_document is not None:
            formal = self.formal_document
            if (
                formal.evidence_id != self.evidence_id
                or formal.verification.source_id != self.source_provider_id
                or formal.verification.normalized_original_sha256 != self.content_sha256
                or formal.verification.rights_snapshot_sha256 != self.rights_snapshot_sha256
                or formal.verification.published_at != self.published_at
                or formal.verification.first_seen_at != self.first_seen_at
                or formal.verification.identity.canonical_url != self.locator
            ):
                raise ValueError("formal citation must bind the exact evidence observation")
        if self.acquisition_mode is ResearchEvidenceAcquisitionMode.BUILT_IN_API:
            if self.revision_known_at < self.first_seen_at:
                raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        else:
            if self.revision_known_at > self.first_seen_at:
                raise ValueError("web revision time cannot be later than first discovery")
            if self.content_sha256 is None or self.rights_snapshot_sha256 is None:
                raise ValueError("model-assisted web citations require content and rights hashes")
        return self


class ResearchEvidenceFact(ContractModel):
    """One PIT-visible input used by deterministic rules and model narration."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    statement: str = Field(min_length=1, max_length=2000)
    value: DecimalString | str | None = None
    unit: str | None = Field(default=None, max_length=50)
    unit_verified: Literal[True] = True
    evidence_id: UUID
    data_audit_status: Literal["not_reviewed", "passed", "api_validated"] = "not_reviewed"
    source_location: str | None = Field(default=None, max_length=300)
    period: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def numeric_values_require_a_verified_unit(self) -> Self:
        if self.value is not None and self.unit is None:
            raise ValueError("numeric evidence values require a verified unit")
        return self


class ResearchEvidencePacket(ContractModel):
    """Bounded source packet assembled before model use."""

    schema_version: Literal["1.0", "1.1"] = "1.1"
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    instrument_name: str = Field(min_length=1, max_length=300)
    as_of: AwareDatetime
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime
    coverage_gaps: list[CoverageGap] = Field(default_factory=list, max_length=100)
    facts: list[ResearchEvidenceFact] = Field(default_factory=list, max_length=500)
    citations: list[ResearchEvidenceCitation] = Field(default_factory=list, max_length=500)
    web_retrieval_receipt_ids: list[UUID] = Field(default_factory=list, max_length=100)
    exposure_profile: ResearchExposureProfile | None = None
    external_event_catalog_status: GlobalEventCatalogStatus = GlobalEventCatalogStatus.UNAVAILABLE
    external_event_catalog_watermark: AwareDatetime | None = None
    external_impact_matches: list[ResearchExternalImpactMatch] = Field(
        default_factory=list, max_length=500
    )
    data_manifest_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def packet_is_pit_consistent(self) -> Self:
        if self.schema_version == "1.1" and any(
            citation.formal_document is None for citation in self.citations
        ):
            raise ValueError("packet 1.1 requires formal document citations")
        if self.coverage_end < self.coverage_start or self.coverage_end > self.as_of:
            raise ValueError("evidence coverage must end within the PIT cutoff")
        citation_ids = {item.evidence_id for item in self.citations}
        if len(citation_ids) != len(self.citations):
            raise ValueError("evidence citations must be unique")
        if len(set(self.web_retrieval_receipt_ids)) != len(self.web_retrieval_receipt_ids):
            raise ValueError("web retrieval receipt ids must be unique")
        if any(item.evidence_id not in citation_ids for item in self.facts):
            raise ValueError("every fact must resolve to a citation")
        fact_keys = [item.key for item in self.facts]
        if len(set(fact_keys)) != len(fact_keys):
            raise ValueError("evidence fact keys must be unique after unit normalization")
        if any(item.revision_known_at > self.as_of for item in self.citations):
            raise ValueError("future revisions cannot enter an evidence packet")
        if any(
            item.acquisition_mode is ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB
            and item.first_seen_at > self.as_of
            for item in self.citations
        ):
            raise ValueError("web evidence first seen after as_of cannot enter an evidence packet")
        if self.exposure_profile is not None:
            if self.exposure_profile.instrument_id != self.instrument_id:
                raise ValueError("exposure profile must match the packet instrument")
            exposure_ids = {
                evidence_id
                for edge in self.exposure_profile.edges
                for evidence_id in edge.evidence_ids
            }
            if not exposure_ids.issubset(citation_ids):
                raise ValueError("exposure evidence must resolve to packet citations")
        if any(
            match.event_revision_known_at > self.as_of for match in self.external_impact_matches
        ):
            raise ValueError("future event revisions cannot enter an evidence packet")
        match_ids = {
            evidence_id
            for match in self.external_impact_matches
            for evidence_id in (
                *match.supporting_evidence_ids,
                *match.counter_evidence_ids,
            )
        }
        if not match_ids.issubset(citation_ids):
            raise ValueError("external impact evidence must resolve to packet citations")
        return self


class ResearchCalculationInput(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: DecimalString | str
    unit: str | None = Field(default=None, max_length=50)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)


class ResearchCalculation(ContractModel):
    formula: str = Field(min_length=1, max_length=1000)
    rule_version: str = Field(min_length=1, max_length=200)
    inputs: list[ResearchCalculationInput] = Field(min_length=1, max_length=100)
    cutoff: AwareDatetime


class ResearchClaim(ContractModel):
    schema_version: Literal["1.0", "2.0"] = "1.0"
    claim_id: UUID = Field(default_factory=uuid4)
    claim_type: ClaimType
    section: ResearchSectionKind
    statement: str = Field(min_length=1, max_length=4000)
    evidence: Sequence[ResearchEvidenceCitation] = Field(default_factory=list, max_length=100)
    calculation: ResearchCalculation | None = None
    counter_evidence: Sequence[ResearchEvidenceCitation] = Field(
        default_factory=list, max_length=100
    )
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
            if (
                not self.evidence
                or not self.falsification_conditions
                or (self.schema_version == "1.0" and not self.counter_evidence)
            ):
                raise ValueError(
                    "model hypotheses require evidence, counter_evidence and "
                    "falsification_conditions"
                )
        identifiers = [item.evidence_id for item in self.evidence]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("claim evidence must be unique")
        return self


class HistoricalResearchPreFormal:
    """Read-only citation shape before Report 2.2; no invented formal provenance."""

    class ResearchEvidenceCitation(ResearchEvidenceCitation):
        formal_document: None = Field(default=None, exclude=True)

    class ResearchClaim(ResearchClaim):
        evidence: Sequence[HistoricalResearchPreFormal.ResearchEvidenceCitation] = Field(
            default_factory=list, max_length=100
        )
        counter_evidence: Sequence[HistoricalResearchPreFormal.ResearchEvidenceCitation] = Field(
            default_factory=list, max_length=100
        )


HistoricalResearchPreFormal.ResearchClaim.model_rebuild()


class HistoricalResearchV1:
    """Namespace preserving the published nested class name without an import cycle."""

    class ResearchClaim(HistoricalResearchPreFormal.ResearchClaim):
        schema_version: Literal["1.0"] = "1.0"


ResearchClaimV1 = HistoricalResearchV1.ResearchClaim


class ResearchClaimV2(ResearchClaim):
    schema_version: Literal["2.0"] = "2.0"


class ResearchReportSection(ContractModel):
    kind: ResearchSectionKind
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=500)


class ResearchAvailableResult(ContractModel):
    """Validated content retained before a complete report is published."""

    schema_version: Literal["1.0", "1.1", "2.0", "2.1"] = "2.1"
    as_of: AwareDatetime
    source_mode: ResearchExecutionSourceMode
    claims: Sequence[ResearchClaim] = Field(default_factory=list, max_length=2000)
    sections: list[ResearchReportSection] = Field(default_factory=list, max_length=10)
    coverage_gaps: list[CoverageGap] = Field(default_factory=list, max_length=100)
    non_production_notice: str = Field(min_length=1, max_length=300)
    scorecard: ResearchScorecard | None = None
    reader_edition: ResearchReaderEdition | None = None

    @model_validator(mode="after")
    def retained_content_is_traceable(self) -> Self:
        if self.schema_version == "2.1" and any(
            citation.formal_document is None
            for claim in self.claims
            for citation in (*claim.evidence, *claim.counter_evidence)
        ):
            raise ValueError("available result 2.1 requires formal document proof")
        claims = {claim.claim_id: claim for claim in self.claims}
        if len(claims) != len(self.claims):
            raise ValueError("available claim IDs must be unique")
        for section in self.sections:
            if any(identifier not in claims for identifier in section.claim_ids):
                raise ValueError("available section claims must resolve")
        if self.reader_edition is not None and self.reader_edition.claim_ids != [
            claim.claim_id for claim in self.claims
        ]:
            raise ValueError("available reader must include every retained claim in order")
        if self.scorecard is not None:
            _validate_scorecard_sources(self.scorecard, self.claims, self.source_mode)
        if any(
            citation.revision_known_at > self.as_of
            or (
                citation.acquisition_mode is ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB
                and citation.first_seen_at > self.as_of
            )
            for claim in self.claims
            for citation in (*claim.evidence, *claim.counter_evidence)
        ):
            raise ValueError("available evidence cannot exceed the cutoff")
        if (
            self.source_mode is ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY
            and "合成" not in self.non_production_notice
        ):
            raise ValueError("available synthetic content requires a synthetic notice")
        return self


def _validate_scorecard_sources(
    scorecard: ResearchScorecard,
    claims: Sequence[ResearchClaim],
    source_mode: ResearchExecutionSourceMode,
    *,
    strict_synthetic_basis: bool = True,
) -> None:
    citations = {
        citation.evidence_id: citation
        for claim in claims
        for citation in (*claim.evidence, *claim.counter_evidence)
    }
    selected = {
        evidence_id
        for dimension in scorecard.dimensions
        for evidence_id in (*dimension.evidence_ids, *dimension.counter_evidence_ids)
    }
    if not selected.issubset(citations):
        raise ValueError("score evidence must resolve to published claim citations")
    if scorecard.scoring_basis is ResearchScoreBasis.SYNTHETIC_SCENARIO:
        if source_mode is not ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY:
            raise ValueError("synthetic scoring requires a synthetic report")
        if any(
            citations[identifier].source_level is not ResearchEvidenceSourceLevel.SYNTHETIC
            or citations[identifier].acquisition_mode
            is ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB
            for identifier in selected
        ):
            raise ValueError("synthetic scoring may only cite synthetic snapshot evidence")
    elif (
        strict_synthetic_basis
        and source_mode is ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY
        and (
            scorecard.evidence_confidence != 0
            or any(dimension.adjusted_score is not None for dimension in scorecard.dimensions)
        )
    ):
        raise ValueError("synthetic numeric scores require an explicit scenario basis")


class ResearchEvidenceBasis(ContractModel):
    """An exact source anchor; support reasoning is checked separately."""

    evidence_id: UUID
    fact_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    excerpt: str = Field(min_length=1, max_length=2000)


class ResearchNarrativeHypothesis(ContractModel):
    """Model-authored hypothesis that still carries deterministic publication hooks."""

    section: Literal[ResearchSectionKind.BULL_CASE, ResearchSectionKind.BEAR_CASE]
    statement: ModelAuthoredProse = Field(min_length=1, max_length=4000)
    supporting_evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    # Draft candidates may lack counter-evidence. The support/publication step
    # decides their eligibility without discarding the other model candidates.
    counter_evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    falsification_conditions: list[ModelAuthoredProse] = Field(min_length=1, max_length=20)
    uncertainty: ModelAuthoredProse = Field(min_length=1, max_length=1000)
    supporting_basis: list[ResearchEvidenceBasis] = Field(default_factory=list, max_length=100)
    counter_basis: list[ResearchEvidenceBasis] = Field(default_factory=list, max_length=100)


class ResearchNarrativeSection(ContractModel):
    kind: ResearchSectionKind
    title: str = Field(min_length=1, max_length=200)
    summary: ModelAuthoredProse = Field(min_length=1, max_length=4000)


class ResearchNarrativeDraft(ContractModel):
    """Schema-constrained model output; never publish this object directly."""

    schema_version: Literal["1.0"] = "1.0"
    sections: list[ResearchNarrativeSection] = Field(default_factory=list, max_length=10)
    hypotheses: list[ResearchNarrativeHypothesis] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def contains_every_section_once(self) -> Self:
        kinds = [item.kind for item in self.sections]
        if len(set(kinds)) != len(kinds):
            raise ValueError("narrative section kinds must be unique")
        return self


class ResearchHypothesisSelection(ContractModel):
    section: Literal[ResearchSectionKind.BULL_CASE, ResearchSectionKind.BEAR_CASE]
    statement: ModelAuthoredProse = Field(min_length=1, max_length=4000)
    supporting_fact_keys: list[str] = Field(min_length=1, max_length=100)
    counter_fact_keys: list[str] = Field(default_factory=list, max_length=100)
    falsification_conditions: list[ModelAuthoredProse] = Field(min_length=1, max_length=20)
    uncertainty: ModelAuthoredProse = Field(min_length=1, max_length=1000)


class ResearchNarrativeCandidate(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    sections: list[ResearchNarrativeSection] = Field(default_factory=list, max_length=10)
    hypotheses: list[ResearchHypothesisSelection] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def section_kinds_are_unique(self) -> Self:
        kinds = [item.kind for item in self.sections]
        if len(kinds) != len(set(kinds)):
            raise ValueError("narrative section kinds must be unique")
        return self


class ResearchHypothesisPatch(ContractModel):
    item_id: str = Field(pattern=r"^hypothesis:(?:0|[1-9][0-9]{0,2})$")
    replacement: ResearchHypothesisSelection | None


class ResearchSectionPatch(ContractModel):
    item_id: str = Field(pattern=r"^section:(?:0|[1-9][0-9]{0,2})$")
    replacement: ResearchNarrativeSection | None


class ResearchNarrativePatch(ContractModel):
    """One bounded correction referencing immutable first-draft slots."""

    schema_version: Literal["1.0"] = "1.0"
    hypotheses: list[ResearchHypothesisPatch] = Field(default_factory=list, max_length=100)
    sections: list[ResearchSectionPatch] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def item_ids_are_unique(self) -> Self:
        ids = [item.item_id for item in self.hypotheses] + [item.item_id for item in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("narrative patch IDs must be unique")
        return self


ResearchSupportCriterion = Literal[
    "source_support",
    "directional_mechanism",
    "counter_meaning",
    "summary_consistency",
    "assessment_support",
]
ResearchSupportReason = Literal[
    "SUPPORTED",
    "IRRELEVANT_FACT",
    "INFERENCE_UNSUPPORTED",
    "VALUE_MISMATCH",
    "UNIT_MISMATCH",
    "PERIOD_MISMATCH",
    "SUBJECT_MISMATCH",
    "COUNTER_MEANING_INVALID",
    "INSUFFICIENT_EVIDENCE",
]


class ResearchSupportLeaf(ContractModel):
    field_path: str = Field(
        pattern=r"^(statement|uncertainty|summary|grade|falsification_conditions\[[0-9]{1,2}\]|reasons\[[0-9]\])$"
    )
    criterion: ResearchSupportCriterion
    reason_code: ResearchSupportReason
    target_quote: str = Field(min_length=1, max_length=400)
    reason: str = Field(min_length=1, max_length=400)


class ResearchSupportLeafItem(ContractModel):
    item_id: str = Field(pattern=r"^(hypothesis|section|score):[0-9]{1,3}$")
    field_checks: list[ResearchSupportLeaf] = Field(min_length=1, max_length=24)


class ResearchSupportLeafAssessment(ContractModel):
    """Model wire response; outcomes are computed by the research consumer."""

    schema_version: Literal["4.0"]
    verdicts: list[ResearchSupportLeafItem] = Field(min_length=1, max_length=116)


class ResearchSupportFieldVerdict(ContractModel):
    field_path: str = Field(
        pattern=r"^(statement|uncertainty|summary|grade|falsification_conditions\[[0-9]{1,2}\]|reasons\[[0-9]\])$"
    )
    criterion: ResearchSupportCriterion = "source_support"
    reason_code: ResearchSupportReason | None = None
    target_quote: str | None = Field(default=None, min_length=1, max_length=400)
    supported: bool
    reason: str = Field(min_length=1, max_length=400)


class ResearchSupportVerdict(ContractModel):
    item_id: str = Field(pattern=r"^(hypothesis|section|score):[0-9]{1,3}$")
    supported: bool
    reason: str = Field(min_length=1, max_length=1000)
    reason_code: Literal[
        "SUPPORTED",
        "IRRELEVANT_FACT",
        "INFERENCE_UNSUPPORTED",
        "VALUE_MISMATCH",
        "UNIT_MISMATCH",
        "PERIOD_MISMATCH",
        "SUBJECT_MISMATCH",
        "COUNTER_MEANING_INVALID",
        "INSUFFICIENT_EVIDENCE",
    ] = "INSUFFICIENT_EVIDENCE"
    field_checks: list[ResearchSupportFieldVerdict] = Field(default_factory=list, max_length=24)
    summary_context_hypothesis_ids: (
        list[Annotated[str, Field(pattern=r"^hypothesis:[0-9]{1,3}$")]] | None
    ) = Field(default=None, max_length=100)


class ResearchSupportAssessment(ContractModel):
    schema_version: Literal["1.0", "2.0", "3.0", "3.1", "3.2"] = "3.1"
    verdicts: list[ResearchSupportVerdict] = Field(max_length=116)

    @model_validator(mode="after")
    def outcome_and_reason_must_agree(self) -> Self:
        if self.schema_version in {"2.0", "3.0", "3.1", "3.2"} and any(
            item.supported != (item.reason_code == "SUPPORTED") for item in self.verdicts
        ):
            raise ValueError("support verdict outcome and reason code conflict")
        return self


class ResearchReport(ContractModel):
    schema_version: Literal["1.0", "1.1", "1.2", "2.0", "2.1", "2.2"] = "1.0"
    report_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    instrument_name: str = Field(min_length=1, max_length=300)
    as_of: AwareDatetime
    analysis_config: ResearchAnalysisConfig
    source_mode: ResearchExecutionSourceMode
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime
    coverage_gaps: list[CoverageGap] = Field(default_factory=list, max_length=100)
    sections: list[ResearchReportSection] = Field(min_length=10, max_length=10)
    claims: Sequence[ResearchClaim] = Field(default_factory=list, max_length=2000)
    reader_edition: ResearchReaderEdition | None = None
    scorecard: ResearchScorecard | None = None
    skill_executions: list[ResearchSkillExecution] = Field(default_factory=list, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure | None = None
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

    @model_validator(mode="before")
    @classmethod
    def retained_wire_data_uses_its_published_semantics(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if not isinstance(value, Mapping):
            return value
        if isinstance(info.context, dict) and info.context.get("strict_report_write") is True:
            return value
        compatibility_version = retained_report_compatibility_version(
            value.get("validator_version")
        )
        if compatibility_version is None:
            return value
        prepared = dict(value)
        compatibility_context = {
            "retained_report_validator_version": compatibility_version,
        }
        disclosure = value.get("web_retrieval_disclosure")
        if disclosure is not None and not isinstance(disclosure, ResearchWebRetrievalDisclosure):
            retained_disclosure = ResearchWebRetrievalDisclosure.model_validate(
                disclosure,
                context=compatibility_context,
            )
            retained_disclosure._retained_report_validator_version = compatibility_version
            prepared["web_retrieval_disclosure"] = retained_disclosure
        elif isinstance(disclosure, ResearchWebRetrievalDisclosure):
            retained_disclosure = disclosure.model_copy(deep=True)
            retained_disclosure._retained_report_validator_version = compatibility_version
            prepared["web_retrieval_disclosure"] = retained_disclosure
        return prepared

    @model_validator(mode="after")
    def report_must_pass_publication_shape(self, info: ValidationInfo) -> Self:
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
        if self.schema_version not in {"2.0", "2.1", "2.2"} and any(
            item.schema_version != "1.0" for item in self.claims
        ):
            raise ValueError("legacy report requires legacy claim semantics")
        if self.schema_version == "2.2":
            for formal_claim in self.claims:
                for citation in (*formal_claim.evidence, *formal_claim.counter_evidence):
                    if citation.formal_document is None:
                        raise ValueError(
                            "report 2.2 requires formal document proof for every citation"
                        )
                    if (
                        citation.formal_document.verification.identity.instrument_id
                        != self.instrument_id
                    ):
                        raise ValueError("formal report citation belongs to another issuer")
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
        if self.schema_version in {"1.1", "1.2", "2.0", "2.1", "2.2"}:
            if self.reader_edition is None or self.scorecard is None:
                raise ValueError("report 1.1 requires a reader edition and scorecard")
            if self.web_retrieval_disclosure is None:
                raise ValueError("report 1.1 requires a web retrieval disclosure")
            if not self.skill_executions:
                raise ValueError("report 1.1 requires method execution receipts")
            execution_ids = [item.skill_id for item in self.skill_executions]
            if len(set(execution_ids)) != len(execution_ids) or set(execution_ids) != set(
                ResearchSkillId
            ):
                raise ValueError("report 1.1 requires one receipt for each catalog method")
        if self.reader_edition is not None:
            if not set(self.reader_edition.claim_ids).issubset(claims):
                raise ValueError("reader edition claim IDs must resolve to report claims")
            if (
                isinstance(info.context, dict)
                and info.context.get("strict_report_write") is True
                and self.reader_edition.claim_ids != [item.claim_id for item in self.claims]
            ):
                raise ValueError("new reader editions must include every report claim in order")
        if self.scorecard is not None:
            _validate_scorecard_sources(
                self.scorecard,
                self.claims,
                self.source_mode,
                strict_synthetic_basis=(
                    self.scorecard.schema_version == "1.1"
                    or (
                        isinstance(info.context, dict)
                        and info.context.get("strict_report_write") is True
                    )
                ),
            )
        report_claim_ids = set(claims)
        if any(
            not set(execution.claim_ids).issubset(report_claim_ids)
            for execution in self.skill_executions
        ):
            raise ValueError("method execution claims must resolve to published report claims")
        cited = [
            citation
            for claim in self.claims
            for citation in (*claim.evidence, *claim.counter_evidence)
        ]
        if any(citation.revision_known_at > self.as_of for citation in cited):
            raise ValueError("report evidence cannot include a future revision")
        compatibility_version = (
            None
            if isinstance(info.context, dict) and info.context.get("strict_report_write") is True
            else retained_report_compatibility_version(self.validator_version)
        )
        if compatibility_version not in {
            "r2-publication-validator.v1",
            "r2-publication-validator.v2",
        } and any(
            citation.acquisition_mode is ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB
            and citation.first_seen_at > self.as_of
            for citation in cited
        ):
            raise ValueError("report cannot include web evidence first seen after as_of")
        narrative_text = [
            *(item.title for item in self.sections),
            *(item.summary for item in self.sections),
            *(item.uncertainty or "" for item in self.claims),
            *(condition for item in self.claims for condition in item.falsification_conditions),
        ]
        publication_text = "\n".join(
            [
                *narrative_text,
                *(item.statement for item in self.claims),
                *(self.reader_edition.how_the_company_makes_money if self.reader_edition else []),
                *(self.reader_edition.why_this_score if self.reader_edition else []),
                *(self.reader_edition.main_strengths if self.reader_edition else []),
                *(self.reader_edition.main_risks if self.reader_edition else []),
                *(self.reader_edition.unknowns if self.reader_edition else []),
                *(
                    item.term
                    for item in (self.reader_edition.glossary if self.reader_edition else [])
                ),
                *(
                    item.explanation
                    for item in (self.reader_edition.glossary if self.reader_edition else [])
                ),
                *(
                    reason
                    for dimension in (self.scorecard.dimensions if self.scorecard else [])
                    for reason in dimension.reasons
                ),
                self.reader_edition.one_sentence_takeaway if self.reader_edition else "",
                self.reader_edition.safe_markdown if self.reader_edition else "",
            ]
        ).casefold()
        if contains_forbidden_investment_advice(publication_text):
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


class ResearchReaderReportV1_1(ResearchReport):
    """Published 1.1 producer shape with readable scoring fields required in schema."""

    schema_version: Literal["1.1"] = "1.1"
    source_mode: ResearchSourceMode
    reader_edition: ResearchReaderEdition
    scorecard: ResearchScorecard
    skill_executions: list[ResearchSkillExecution] = Field(min_length=1, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure


class ResearchReportV1_1(ContractModel):
    """Published 1.1 producer shape with readable scoring fields required in schema."""

    schema_version: Literal["1.1"] = "1.1"
    report_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    instrument_id: str = Field(pattern="^(XSHG|XSHE|XBSE):[0-9]{6}$")
    instrument_name: str = Field(min_length=1, max_length=300)
    as_of: AwareDatetime
    analysis_config: ResearchAnalysisConfig
    source_mode: ResearchSourceMode
    coverage_start: AwareDatetime
    coverage_end: AwareDatetime
    coverage_gaps: list[CoverageGap] = Field(default_factory=list, max_length=100)
    sections: list[ResearchReportSection] = Field(min_length=10, max_length=10)
    claims: list[ResearchClaimV1] = Field(default_factory=list, max_length=2000)
    reader_edition: ResearchReaderEdition
    scorecard: FrozenResearchScorecard
    skill_executions: list[ResearchSkillExecution] = Field(min_length=1, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure
    model_provider: str = Field(pattern="^[a-z][a-z0-9_.-]{1,99}$")
    model_name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    generator_version: str = Field(min_length=1, max_length=200)
    validator_version: str = Field(min_length=1, max_length=200)
    rule_version: str = Field(min_length=1, max_length=200)
    data_manifest_sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    artifact_object_key: str | None = Field(default=None, max_length=1024)
    artifact_content_sha256: Annotated[str | None, Field(pattern="^[a-f0-9]{64}$")] = None
    artifact_content_length: int | None = Field(default=None, ge=0, le=10 * 1024 * 1024)
    generated_at: AwareDatetime
    expires_at: AwareDatetime
    paper_only: Literal[True] = True
    non_production_notice: str = Field(
        default="合成测试数据; 仅供研究与产品链路验证, 不构成投资建议。",
        min_length=1,
        max_length=300,
    )


class ResearchReportV1_2(ResearchReport):
    """Report 1.2 adds explicit scoring basis without rewriting published report 1.1."""

    schema_version: Literal["1.2"] = "1.2"
    source_mode: ResearchSourceMode
    claims: list[ResearchClaimV1] = Field(default_factory=list, max_length=2000)
    reader_edition: ResearchReaderEdition
    scorecard: ResearchScorecard
    skill_executions: list[ResearchSkillExecution] = Field(min_length=1, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure


class ResearchReportV2(ResearchReport):
    """Grounded hypotheses may disclose the absence of direct counter-evidence."""

    schema_version: Literal["2.0"] = "2.0"
    claims: Sequence[HistoricalResearchPreFormal.ResearchClaim] = Field(
        default_factory=list, max_length=2000
    )
    source_mode: ResearchSourceMode
    reader_edition: ResearchReaderEdition
    scorecard: ResearchScorecard
    skill_executions: list[ResearchSkillExecution] = Field(min_length=1, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure


class ResearchReportV2_1(ResearchReport):
    """Real preview report; historical source-mode schemas remain frozen."""

    schema_version: Literal["2.1"] = "2.1"
    claims: Sequence[HistoricalResearchPreFormal.ResearchClaim] = Field(
        default_factory=list, max_length=2000
    )
    source_mode: ResearchRealSourceMode
    reader_edition: ResearchReaderEdition
    scorecard: ResearchScorecard
    skill_executions: list[ResearchSkillExecution] = Field(min_length=1, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure


class ResearchReportV2_2(ResearchReport):
    """Formal-disclosure research with original document proof in each citation."""

    schema_version: Literal["2.2"] = "2.2"
    source_mode: ResearchRealSourceMode
    reader_edition: ResearchReaderEdition
    scorecard: ResearchScorecard
    skill_executions: list[ResearchSkillExecution] = Field(min_length=1, max_length=21)
    web_retrieval_disclosure: ResearchWebRetrievalDisclosure


def validate_stored_research_report(
    value: object,
    *,
    trusted_validator_version: str,
) -> ResearchReport:
    """Decode retained wire data only when independent validator metadata agrees.

    The write contract always revalidates with current strict semantics. Remove retained
    read semantics after validator v1 through v2.1 leave the report retention window.
    """

    if (
        not isinstance(value, Mapping)
        or value.get("validator_version") != trusted_validator_version
    ):
        raise ValueError("stored report validator version does not match trusted metadata")
    report_type: type[ResearchReport]
    if value.get("schema_version") == "2.2":
        report_type = ResearchReportV2_2
    elif value.get("schema_version") == "2.1":
        report_type = ResearchReportV2_1
    elif value.get("schema_version") == "2.0":
        report_type = ResearchReportV2
    elif value.get("schema_version") == "1.2":
        report_type = ResearchReportV1_2
    elif value.get("schema_version") == "1.1":
        report_type = ResearchReaderReportV1_1
    else:
        report_type = ResearchReport
    return report_type.model_validate(value)


class ResearchReportLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    found: bool
    report: ResearchReport | None = None
    as_of: AwareDatetime
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def result_must_be_explicit(self) -> Self:
        if self.found != (self.report is not None):
            raise ValueError("found must reflect whether report is present")
        if not self.found and self.reason_code is None:
            raise ValueError("a missing report requires reason_code")
        return self


class ResearchSessionMutationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    session_id: UUID


class ResearchSessionMutationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    status: ResearchSessionStatus
    processed_at: AwareDatetime
    idempotent_replay: bool = False
    job_id: UUID | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class ResearchStageTransitionRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    session_id: UUID
    job_id: UUID
    expected_statuses: list[ResearchSessionStatus] = Field(min_length=1, max_length=8)
    status: ResearchSessionStatus
    stage: ResearchStage
    message: str = Field(min_length=1, max_length=300)
    checkpoint: str | None = Field(default=None, max_length=200)
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    retryable: bool = False
    result_completeness: ResearchResultCompleteness | None = None


class ResearchReportWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    job_id: UUID
    cutoff_mode: ResearchCutoffMode = ResearchCutoffMode.HISTORICAL_REPLAY
    report: ResearchReport
    artifact_object_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def report_write_is_strict_and_artifact_version_is_consistent(self) -> Self:
        report_type = (
            ResearchReportV2_2
            if self.report.schema_version == "2.2"
            else ResearchReportV1_2
            if self.report.schema_version == "1.2"
            else ResearchReaderReportV1_1
            if self.report.schema_version == "1.1"
            else ResearchReport
        )
        report_type.model_validate(
            self.report.model_dump(mode="python"),
            context={"strict_report_write": True},
        )
        if self.artifact_object_version_id is not None and self.report.artifact_object_key is None:
            raise ValueError("artifact version requires an artifact object key")
        return self


class ResearchCheckpoint(ContractModel):
    """One resumable, version-bound snapshot, owned by the existing session."""

    schema_version: Literal[
        "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9"
    ] = "1.9"
    acquisition: ResearchAcquisitionState | None = None
    generator_version: str = Field(min_length=1, max_length=200)
    validator_version: str | None = Field(default=None, max_length=200)
    narrative_candidate: ResearchNarrativeCandidate | None = None
    narrative_repair_reserved: bool = False
    support_rounds_reserved: int = Field(default=0, ge=0, le=2)
    support_pending_ids: list[
        Annotated[str, Field(pattern=r"^(hypothesis|section|score):[0-9]{1,3}$")]
    ] = Field(default_factory=list, max_length=116)

    narrative_patch: ResearchNarrativePatch | None = None
    score_candidate: ResearchScoreCandidate | None = None
    support_assessment: ResearchSupportAssessment | None = None
    stage: ResearchStage
    packet: ResearchEvidencePacket
    narrative: ResearchNarrativeDraft | None = None
    available_result: ResearchAvailableResult | None = None
    scorecard: ResearchScorecard | None = None
    report: ResearchReport | None = None
    provider_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def patch_references_original_slots(self) -> Self:
        if (
            self.acquisition is not None
            and self.acquisition.schema_version == "1.2"
            and self.schema_version != "1.8"
        ):
            raise ValueError("acquisition 1.2 requires checkpoint 1.8")
        if (
            self.acquisition is not None
            and self.acquisition.schema_version == "1.3"
            and self.schema_version != "1.9"
        ):
            raise ValueError("acquisition 1.3 requires checkpoint 1.9")
        patch, original = self.narrative_patch, self.narrative_candidate
        if patch is None:
            return self
        if (
            self.schema_version not in {"1.6", "1.7", "1.8", "1.9"}
            or not self.narrative_repair_reserved
            or original is None
        ):
            raise ValueError("a stored patch requires a reserved correction and its first draft")
        for hypothesis in patch.hypotheses:
            index = int(hypothesis.item_id.split(":")[1])
            if index >= len(original.hypotheses) or (
                hypothesis.replacement is not None
                and hypothesis.replacement.section is not original.hypotheses[index].section
            ):
                raise ValueError("stored hypothesis patch changed its stable identity")
        for section in patch.sections:
            index = int(section.item_id.split(":")[1])
            if index >= len(original.sections) or (
                section.replacement is not None
                and section.replacement.kind is not original.sections[index].kind
            ):
                raise ValueError("stored section patch changed its stable identity")
        return self


class ResearchCheckpointWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    session_id: UUID
    job_id: UUID
    checkpoint: ResearchCheckpoint


class ResearchCheckpointLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    found: bool
    checkpoint: ResearchCheckpoint | None = None

    @model_validator(mode="after")
    def found_reflects_checkpoint(self) -> Self:
        if self.found != (self.checkpoint is not None):
            raise ValueError("found must reflect the saved checkpoint")
        return self


class ResearchGenerateRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    session_id: UUID
    job_id: UUID


class ResearchGenerateRequestV1_1(ContractModel):
    schema_version: Literal["1.1"] = "1.1"
    context: MutationContext
    session_id: UUID
    job_id: UUID
    cutoff_mode: ResearchCutoffMode

    @model_validator(mode="after")
    def task_attempt_requires_an_absolute_deadline(self) -> Self:
        if self.context.deadline_at is None:
            raise ValueError("research task 1.1 requires an absolute deadline")
        return self


ResearchGenerateTaskRequest = ResearchGenerateRequest | ResearchGenerateRequestV1_1


def parse_research_generate_request(value: object) -> ResearchGenerateTaskRequest:
    """Parse only the two registered wire versions without implicit defaults."""

    if isinstance(value, ResearchGenerateRequest | ResearchGenerateRequestV1_1):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("research generate request must be an object")
    version = value.get("schema_version")
    if version == "1.0":
        return ResearchGenerateRequest.model_validate(value)
    if version == "1.1":
        return ResearchGenerateRequestV1_1.model_validate(value)
    raise ValueError("unsupported research generate request schema version")


def effective_research_cutoff_mode(
    request: ResearchGenerateTaskRequest,
) -> ResearchCutoffMode:
    """Fail old immutable jobs closed as historical replays."""

    if isinstance(request, ResearchGenerateRequestV1_1):
        return request.cutoff_mode
    return ResearchCutoffMode.HISTORICAL_REPLAY


class ResearchGenerateResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    job_id: UUID
    status: ResearchSessionStatus
    report_id: UUID | None = None
    completed_at: AwareDatetime
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class ResearchPendingJob(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID
    task_name: Literal["stock_assistant.research.generate_report.v1"]
    payload: ResearchGenerateTaskRequest
    attempt: int = Field(ge=1, le=100)
    created_at: AwareDatetime


class ResearchPurgeCandidate(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    reference: StoredObjectRef | None = None


class ResearchReportReadyV1(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    report_id: UUID
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    as_of: AwareDatetime
    expires_at: AwareDatetime
    report_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


__all__ = [
    "ClaimType",
    "CoverageGap",
    "ResearchAnalysisConfig",
    "ResearchAvailableResult",
    "ResearchCalculation",
    "ResearchCalculationInput",
    "ResearchCheckpoint",
    "ResearchCheckpointLookupResult",
    "ResearchCheckpointWriteRequest",
    "ResearchClaim",
    "ResearchClaimV1",
    "ResearchCutoffMode",
    "ResearchEvidenceAcquisitionMode",
    "ResearchEvidenceBasis",
    "ResearchEvidenceCitation",
    "ResearchEvidenceFact",
    "ResearchEvidencePacket",
    "ResearchEvidenceSourceLevel",
    "ResearchEvidenceVerificationStatus",
    "ResearchGenerateRequest",
    "ResearchGenerateRequestV1_1",
    "ResearchGenerateResult",
    "ResearchGenerateTaskRequest",
    "ResearchNarrativeDraft",
    "ResearchNarrativeHypothesis",
    "ResearchNarrativeSection",
    "ResearchPendingJob",
    "ResearchProgress",
    "ResearchPurgeCandidate",
    "ResearchReaderReportV1_1",
    "ResearchReport",
    "ResearchReportLookupResult",
    "ResearchReportReadyV1",
    "ResearchReportSection",
    "ResearchReportV1_1",
    "ResearchReportV1_2",
    "ResearchReportWriteRequest",
    "ResearchResultCompleteness",
    "ResearchSectionKind",
    "ResearchSessionAccepted",
    "ResearchSessionCreateBody",
    "ResearchSessionCreateRequest",
    "ResearchSessionListQuery",
    "ResearchSessionLookupRequest",
    "ResearchSessionLookupResult",
    "ResearchSessionMutationRequest",
    "ResearchSessionMutationResult",
    "ResearchSessionPage",
    "ResearchSessionStatus",
    "ResearchSessionView",
    "ResearchSourceMode",
    "ResearchStage",
    "ResearchStageTransitionRequest",
    "ResearchSupportAssessment",
    "ResearchSupportVerdict",
    "contains_numeric_expression",
    "effective_research_cutoff_mode",
    "parse_research_generate_request",
    "validate_stored_research_report",
]
