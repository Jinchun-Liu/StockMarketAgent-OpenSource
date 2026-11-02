"""Premarket briefing read model with fixed coverage sections."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.behavior import (
    R1_INSTRUMENT_ID,
    AttentionRiskIndex,
    BehaviorPressureSummary,
    InterpretationOrigin,
    ModelInterpretation,
    PlainLanguageUpdate,
)
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.common import InstrumentRef
from stock_assistant.contracts.ingestion import (
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceReleaseTier,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)


class SourceRole(StrEnum):
    """Permitted public source roles."""

    STATUTORY = "statutory"
    PROFESSIONAL_MARKET = "professional_market"
    PROFESSIONAL_INFORMATION = "professional_information"
    DISCOVERY = "discovery"


class SourceCoverageStatus(StrEnum):
    """Public source capability coverage states."""

    OK = "ok"
    DELAYED = "delayed"
    UNAVAILABLE = "unavailable"
    RIGHTS_BLOCKED = "rights_blocked"
    NOT_QUALIFIED = "not_qualified"
    TIMELINESS_UNVERIFIED = "timeliness_unverified"


class SourceAdvanceState(StrEnum):
    """Provider-neutral result of the most recent source batch."""

    ADVANCED = "advanced"
    NOT_ADVANCED = "not_advanced"


class SourceDataMode(StrEnum):
    """Whether source evidence is real or restricted to development fixtures."""

    REAL = "real"
    SYNTHETIC_DEVELOPMENT_ONLY = "synthetic_development_only"


def source_data_mode_for_runtime(
    runtime_mode: SourceRuntimeMode | SourceRuntimeModeV1_2,
) -> SourceDataMode:
    """Map the explicit runtime mode onto the backward-compatible public data mode."""

    if runtime_mode.value in {
        SourceRuntimeMode.DEMO_REPLAY.value,
        SourceRuntimeModeV1_2.PUBLIC_STATIC.value,
    }:
        return SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY
    return SourceDataMode.REAL


class SourceIngestionProgress(ContractModel):
    """Public provider-neutral ingestion progress metadata."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "advance_state=advanced requires business_watermark",
            ]
        }
    )

    business_watermark: AwareDatetime | None = None
    batch_audit_id: UUID
    advance_state: SourceAdvanceState
    data_mode: SourceDataMode

    @model_validator(mode="after")
    def advanced_batches_require_a_business_watermark(self) -> "SourceIngestionProgress":
        if self.advance_state is SourceAdvanceState.ADVANCED and self.business_watermark is None:
            raise ValueError("advanced source progress requires business_watermark")
        return self


class BriefingIngestionProgress(SourceIngestionProgress):
    """The exact source progress mirrored as the briefing-level ingestion state."""

    source_id: str = Field(min_length=1, max_length=100)
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")


class SourceCoverage(ContractModel):
    """One source capability observation."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "last_success_at is <= observed_at when present",
                "status=ok requires last_success_at",
                "status other than ok requires gap_reason",
                "progress.business_watermark is <= observed_at when present",
                "progress.advance_state=advanced requires last_success_at",
            ]
        }
    )

    source_id: str = Field(min_length=1, max_length=100)
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    source_role: SourceRole
    status: SourceCoverageStatus
    observed_at: AwareDatetime
    last_success_at: AwareDatetime | None = None
    license_id: str = Field(min_length=1, max_length=100)
    gap_reason: str | None = Field(default=None, min_length=1, max_length=500)
    progress: SourceIngestionProgress

    @model_validator(mode="after")
    def coverage_status_must_be_explainable(self) -> "SourceCoverage":
        if self.last_success_at is not None and self.last_success_at > self.observed_at:
            raise ValueError("last_success_at cannot be later than observed_at")
        if self.status is SourceCoverageStatus.OK and self.last_success_at is None:
            raise ValueError("ok coverage requires last_success_at")
        if self.status is not SourceCoverageStatus.OK and self.gap_reason is None:
            raise ValueError("non-ok coverage requires gap_reason")
        if (
            self.progress.business_watermark is not None
            and self.progress.business_watermark > self.observed_at
        ):
            raise ValueError("business_watermark cannot be later than observed_at")
        if (
            self.progress.advance_state is SourceAdvanceState.ADVANCED
            and self.last_success_at is None
        ):
            raise ValueError("advanced coverage requires last_success_at")
        return self


class PremarketBriefingItem(ContractModel):
    """One evidence-linked public briefing item."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "first_seen_at is >= published_at",
                "source_roles entries are unique",
                "evidence_ids entries are unique",
            ]
        }
    )

    event_id: UUID
    event_revision: int = Field(ge=1)
    headline: str = Field(min_length=1, max_length=300)
    fact_summary: str = Field(min_length=1, max_length=2000)
    inference: str | None = Field(default=None, max_length=2000)
    change_type: BriefingChangeType
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    source_roles: list[SourceRole] = Field(min_length=1, max_length=4)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def item_evidence_must_be_ordered_and_unique(self) -> "PremarketBriefingItem":
        if self.first_seen_at < self.published_at:
            raise ValueError("first_seen_at cannot be earlier than published_at")
        if len(set(self.source_roles)) != len(self.source_roles):
            raise ValueError("source_roles must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
        return self


class PremarketBriefingSection(ContractModel):
    """One fixed briefing coverage block."""

    items: list[PremarketBriefingItem] = Field(default_factory=list, max_length=100)


class PremarketBriefingSections(ContractModel):
    """Exactly the five R1 coverage blocks, each present once."""

    statutory_announcements: PremarketBriefingSection
    financial_changes: PremarketBriefingSection
    market_snapshot: PremarketBriefingSection
    company_industry_news: PremarketBriefingSection
    macro_flashes: PremarketBriefingSection


class PremarketBriefingView(ContractModel):
    """Public premarket read model independent from Briefing 1.0."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "comparison_since is <= as_of",
                "generated_at is >= as_of",
                "source_coverage entries are unique by source_id + capability",
                "all source_coverage.observed_at values are <= generated_at",
                "ingestion_progress.business_watermark is <= as_of when present",
                "all source_coverage.progress.data_mode values equal ingestion_progress.data_mode",
                (
                    "ingestion_progress exactly mirrors the progress for its registered "
                    "source_id + capability"
                ),
                "all section item first_seen_at values are <= as_of",
            ]
        }
    )

    schema_version: Literal["1.0"] = "1.0"
    briefing_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    comparison_since: AwareDatetime
    as_of: AwareDatetime
    generated_at: AwareDatetime
    sections: PremarketBriefingSections
    ingestion_progress: BriefingIngestionProgress
    source_coverage: list[SourceCoverage] = Field(default_factory=list, max_length=200)
    omitted_duplicate_count: int = Field(default=0, ge=0)
    ruleset_version: str = Field(min_length=1, max_length=100)
    disclaimer: str = "本系统仅供个人信息整理与研究，不构成证券投资建议、收益承诺或交易指令。"  # noqa: RUF001

    @model_validator(mode="after")
    def briefing_must_be_complete_and_point_in_time_safe(self) -> "PremarketBriefingView":
        if self.comparison_since > self.as_of:
            raise ValueError("comparison_since cannot be later than as_of")
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        coverage_keys = [(item.source_id, item.capability) for item in self.source_coverage]
        if len(set(coverage_keys)) != len(coverage_keys):
            raise ValueError("source coverage must be unique by source_id and capability")
        if any(item.observed_at > self.generated_at for item in self.source_coverage):
            raise ValueError("source coverage cannot be observed after generated_at")
        if (
            self.ingestion_progress.business_watermark is not None
            and self.ingestion_progress.business_watermark > self.as_of
        ):
            raise ValueError("briefing business_watermark cannot be later than as_of")
        if any(
            item.progress.data_mode is not self.ingestion_progress.data_mode
            for item in self.source_coverage
        ):
            raise ValueError("all source coverage data_mode values must match the briefing")
        primary_coverage = [
            item
            for item in self.source_coverage
            if (
                item.source_id,
                item.capability,
            )
            == (
                self.ingestion_progress.source_id,
                self.ingestion_progress.capability,
            )
        ]
        if len(primary_coverage) != 1:
            raise ValueError(
                "briefing ingestion_progress must identify exactly one source coverage"
            )
        primary_progress = primary_coverage[0].progress
        if (
            primary_progress.business_watermark,
            primary_progress.batch_audit_id,
            primary_progress.advance_state,
            primary_progress.data_mode,
        ) != (
            self.ingestion_progress.business_watermark,
            self.ingestion_progress.batch_audit_id,
            self.ingestion_progress.advance_state,
            self.ingestion_progress.data_mode,
        ):
            raise ValueError("briefing ingestion_progress must mirror its source coverage progress")
        sections = (
            self.sections.statutory_announcements,
            self.sections.financial_changes,
            self.sections.market_snapshot,
            self.sections.company_industry_news,
            self.sections.macro_flashes,
        )
        if any(item.first_seen_at > self.as_of for section in sections for item in section.items):
            raise ValueError("briefing cannot contain evidence first seen after as_of")
        return self


class BriefingIngestionProgressV1_1(BriefingIngestionProgress):
    """Primary-source progress with explicit runtime provenance."""

    provenance: SourceProvenance


class SourceCoverageV1_1(SourceCoverage):
    """Source capability coverage with explicit provider lineage."""

    provenance: SourceProvenance


class PremarketBriefingViewV1_1(PremarketBriefingView):
    """R1 1.1 read model that makes source mode and provenance explicit."""

    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    runtime_mode: SourceRuntimeMode
    release_tier: SourceReleaseTier
    ingestion_progress: BriefingIngestionProgressV1_1
    source_coverage: list[SourceCoverageV1_1] = Field(  # type: ignore[assignment]
        default_factory=list,
        max_length=200,
    )
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "runtime_mode determines ingestion_progress.data_mode",
                "all provenance runtime_mode values equal runtime_mode",
                "all provenance release_tier values equal release_tier",
                "source coverage data_mode matches runtime_mode",
            ]
        }
    )

    @model_validator(mode="after")
    def public_provenance_must_match_briefing_mode(self) -> "PremarketBriefingViewV1_1":
        progress = self.ingestion_progress
        expected_data_mode = source_data_mode_for_runtime(self.runtime_mode)
        if progress.data_mode is not expected_data_mode:
            raise ValueError("primary data_mode must match the briefing runtime_mode")
        if progress.provenance.runtime_mode is not self.runtime_mode:
            raise ValueError("primary provenance runtime_mode must match the briefing")
        if progress.provenance.release_tier is not self.release_tier:
            raise ValueError("primary provenance release_tier must match the briefing")
        if any(
            item.provenance.runtime_mode is not self.runtime_mode for item in self.source_coverage
        ):
            raise ValueError("source coverage runtime_mode must match the briefing")
        if any(item.progress.data_mode is not expected_data_mode for item in self.source_coverage):
            raise ValueError("source coverage data_mode must match the briefing runtime_mode")
        if any(
            item.provenance.release_tier is not self.release_tier for item in self.source_coverage
        ):
            raise ValueError("source coverage release_tier must match the briefing")
        return self


class BriefingIngestionProgressV1_2(BriefingIngestionProgress):
    """Primary-source progress carrying complete R1 1.2 lineage."""

    provenance: SourceProvenanceV1_2


class SourceCoverageV1_2(SourceCoverage):
    """Source capability coverage carrying complete R1 1.2 lineage."""

    provenance: SourceProvenanceV1_2


class PremarketBriefingViewV1_2(PremarketBriefingView):
    """R1 1.2 read model with explicit static/free source provenance."""

    schema_version: Literal["1.2"] = "1.2"  # type: ignore[assignment]
    runtime_mode: SourceRuntimeModeV1_2
    release_tier: SourceReleaseTier
    ingestion_progress: BriefingIngestionProgressV1_2
    source_coverage: list[SourceCoverageV1_2] = Field(  # type: ignore[assignment]
        default_factory=list,
        max_length=200,
    )
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "runtime_mode determines ingestion_progress.data_mode",
                "all 1.2 provenance runtime modes and release tiers match the briefing",
            ]
        }
    )

    @model_validator(mode="after")
    def public_provenance_must_match_briefing_mode(self) -> "PremarketBriefingViewV1_2":
        expected_data_mode = source_data_mode_for_runtime(self.runtime_mode)
        if self.ingestion_progress.data_mode is not expected_data_mode:
            raise ValueError("primary data_mode must match the briefing runtime_mode")
        provenance_items = [
            self.ingestion_progress.provenance,
            *(item.provenance for item in self.source_coverage),
        ]
        if any(item.runtime_mode is not self.runtime_mode for item in provenance_items):
            raise ValueError("all provenance runtime_mode values must match the briefing")
        if any(item.release_tier is not self.release_tier for item in provenance_items):
            raise ValueError("all provenance release_tier values must match the briefing")
        if any(item.progress.data_mode is not expected_data_mode for item in self.source_coverage):
            raise ValueError("source coverage data_mode must match the briefing runtime_mode")
        return self


class PremarketBriefingViewV1_3(PremarketBriefingViewV1_2):
    """R1 single-stock research view with two deterministic 0-100 indices."""

    schema_version: Literal["1.3"] = "1.3"  # type: ignore[assignment]
    attention_risk_index: AttentionRiskIndex
    behavior_pressure_summary: BehaviorPressureSummary
    plain_language_update: PlainLanguageUpdate
    flash_interpretation: ModelInterpretation
    pro_interpretation: ModelInterpretation | None = None
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "instrument is fixed to XSHG:603986",
                "attention and behavior indices are deterministic inputs to model interpretation",
                "pro_interpretation may be absent without degrading deterministic content",
            ]
        }
    )

    @model_validator(mode="after")
    def research_view_must_be_fixed_target_and_time_safe(self) -> "PremarketBriefingViewV1_3":
        identity = (
            self.instrument.instrument_id,
            self.instrument.symbol,
            self.instrument.exchange,
            self.instrument.market,
            self.instrument.asset_class,
            self.instrument.currency,
        )
        if identity != (R1_INSTRUMENT_ID, "603986", "XSHG", "CN", "equity", "CNY"):
            raise ValueError("R1 research view requires the canonical XSHG:603986 identity")
        if self.attention_risk_index.as_of > self.as_of:
            raise ValueError("attention risk cannot be newer than the briefing cutoff")
        if self.behavior_pressure_summary.as_of > self.as_of:
            raise ValueError("behavior pressure cannot be newer than the briefing cutoff")
        if self.flash_interpretation.origin is InterpretationOrigin.DEEPSEEK_PRO:
            raise ValueError("primary interpretation cannot claim the Pro model")
        if (
            self.pro_interpretation is not None
            and self.pro_interpretation.origin is not InterpretationOrigin.DEEPSEEK_PRO
        ):
            raise ValueError("pro_interpretation must truthfully identify the Pro model")
        return self
