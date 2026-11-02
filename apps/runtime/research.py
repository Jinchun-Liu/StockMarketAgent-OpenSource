"""Turn one deterministic briefing and behavior snapshot into the public R1 v1.3 view."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Protocol
from uuid import UUID

from apps.runtime.behavior_semantics import BEHAVIOR_SEMANTICS_HASH
from apps.runtime.llm import (
    generate_r1_interpretation,
    generate_r1_pro_interpretation,
)
from stock_assistant.config.settings import Settings
from stock_assistant.contracts.behavior import (
    R1_INSTRUMENT_ID,
    AttentionRiskBand,
    AttentionRiskCategory,
    AttentionRiskCategoryName,
    AttentionRiskIndex,
    BehaviorPressureQuery,
    BehaviorPressureSnapshotV1_0,
    BehaviorPressureSummary,
    IndexAvailability,
    IndexExplanation,
    MarketStructureDimension,
    PlainLanguageUpdate,
    PressureFacet,
    PressureFacetName,
    PressureMode,
    StructureAvailability,
    StructureDimensionName,
)
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.common import InstrumentRef, OperationContext
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingSection,
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
    SourceAdvanceState,
    SourceCoverageStatus,
)
from stock_assistant.ports.persistence import BehaviorPressureRepositoryPort

_ATTENTION_SOURCES: Final = (
    (
        AttentionRiskCategoryName.ANNOUNCEMENTS,
        "statutory.announcements",
        "statutory_announcements",
        Decimal("0.25"),
    ),
    (
        AttentionRiskCategoryName.FINANCIAL,
        "financial.changes",
        "financial_changes",
        Decimal("0.25"),
    ),
    (
        AttentionRiskCategoryName.MARKET,
        "market.snapshot",
        "market_snapshot",
        Decimal("0.20"),
    ),
    (
        AttentionRiskCategoryName.COMPANY_INDUSTRY,
        "company_industry.news",
        "company_industry_news",
        Decimal("0.20"),
    ),
    (
        AttentionRiskCategoryName.MACRO,
        "macro.flashes",
        "macro_flashes",
        Decimal("0.10"),
    ),
)

_R1_RESEARCH_INSTRUMENT: Final = InstrumentRef(
    instrument_id=R1_INSTRUMENT_ID,
    symbol="603986",
    exchange="XSHG",
    market="CN",
    asset_class="equity",
    currency="CNY",
)
_R1_LEGACY_INSTRUMENT: Final = _R1_RESEARCH_INSTRUMENT.model_copy(update={"symbol": "603986.SH"})


class ResearchBriefingEnricherPort(Protocol):
    """Runtime-only composition seam; stable cross-module interfaces remain frozen."""

    async def enrich(
        self,
        *,
        briefing: PremarketBriefingViewV1_2,
        context: OperationContext,
        evidence_ids: list[UUID],
    ) -> PremarketBriefingViewV1_3: ...


def _attention_band(score: int) -> AttentionRiskBand:
    if score <= 19:
        return AttentionRiskBand.CALM
    if score <= 39:
        return AttentionRiskBand.NOTICE
    if score <= 59:
        return AttentionRiskBand.WARMING
    if score <= 79:
        return AttentionRiskBand.TENSE
    return AttentionRiskBand.HIGH_ALERT


def _category(
    *,
    name: AttentionRiskCategoryName,
    section: PremarketBriefingSection,
    available: bool,
) -> AttentionRiskCategory:
    counts = {
        change_type: sum(item.change_type is change_type for item in section.items)
        for change_type in BriefingChangeType
    }
    score = min(
        100,
        10 * counts[BriefingChangeType.NEW]
        + 15 * counts[BriefingChangeType.UPDATE]
        + 25 * counts[BriefingChangeType.CORRECTION]
        + 35 * counts[BriefingChangeType.CONTRADICTION],
    )
    return AttentionRiskCategory(
        category=name,
        score=score if available else None,
        new_count=counts[BriefingChangeType.NEW],
        update_count=counts[BriefingChangeType.UPDATE],
        correction_count=counts[BriefingChangeType.CORRECTION],
        contradiction_count=counts[BriefingChangeType.CONTRADICTION],
        available=available,
    )


def build_attention_risk(briefing: PremarketBriefingViewV1_2) -> AttentionRiskIndex:
    """Apply the frozen category weights only to source-backed available categories."""

    categories: list[AttentionRiskCategory] = []
    weights: dict[AttentionRiskCategoryName, Decimal] = {}
    for name, capability, section_name, weight in _ATTENTION_SOURCES:
        available = any(
            coverage.capability == capability and coverage.status is SourceCoverageStatus.OK
            for coverage in briefing.source_coverage
        )
        categories.append(
            _category(
                name=name,
                section=getattr(briefing.sections, section_name),
                available=available,
            )
        )
        weights[name] = weight
    available_categories = [item for item in categories if item.score is not None]
    score: int | None = None
    band: AttentionRiskBand | None = None
    availability = IndexAvailability.ABSTAINED
    if len(available_categories) >= 3:
        total_weight = sum((weights[item.category] for item in available_categories), Decimal(0))
        total = sum(
            (
                Decimal(item.score) * weights[item.category]
                for item in available_categories
                if item.score is not None
            ),
            Decimal(0),
        )
        score = int((total / total_weight).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        band = _attention_band(score)
        availability = (
            IndexAvailability.READY
            if len(available_categories) == len(_ATTENTION_SOURCES)
            else IndexAvailability.DEGRADED
        )
    return AttentionRiskIndex(
        score=score,
        band=band,
        availability=availability,
        as_of=briefing.as_of,
        categories=categories,
        available_category_count=len(available_categories),
        completeness_percent=len(available_categories) * 20,
    )


def build_plain_language_update(briefing: PremarketBriefingViewV1_2) -> PlainLanguageUpdate:
    """Summarize source progress without exposing internal status labels or raw logs."""

    coverages = briefing.source_coverage
    succeeded = sum(
        item.status is SourceCoverageStatus.OK
        and item.progress.advance_state is SourceAdvanceState.ADVANCED
        for item in coverages
    )
    no_change = sum(
        item.status is SourceCoverageStatus.OK
        and item.progress.advance_state is SourceAdvanceState.NOT_ADVANCED
        for item in coverages
    )
    unavailable = sum(item.status is not SourceCoverageStatus.OK for item in coverages)
    last_successes = [
        item.last_success_at for item in coverages if item.last_success_at is not None
    ]
    reasons = []
    if unavailable:
        reasons.append(
            IndexExplanation(
                code="SOURCE_COVERAGE_INCOMPLETE",
                text=f"有{unavailable}项信息暂未成功更新,页面已保留可核对的已有结果。",
            )
        )
    summary = (
        f"本次共核对{len(coverages)}项信息:{succeeded}项有新内容,"
        f"{no_change}项没有变化,{unavailable}项暂未更新。"
    )
    return PlainLanguageUpdate(
        checked_count=len(coverages),
        succeeded_count=succeeded,
        no_change_count=no_change,
        unavailable_count=unavailable,
        last_success_at=max(last_successes) if last_successes else None,
        summary=summary,
        reasons=reasons,
    )


def _unavailable_behavior(as_of: datetime) -> BehaviorPressureSummary:
    return BehaviorPressureSummary(
        mode=PressureMode.POST_CLOSE,
        score=None,
        band=None,
        availability=IndexAvailability.ABSTAINED,
        as_of=as_of,
        facets=[
            PressureFacet(
                facet=name,
                score=None,
                availability=IndexAvailability.ABSTAINED,
                reason_code="BEHAVIOR_NOT_AVAILABLE",
            )
            for name in PressureFacetName
        ],
        structure_dimensions=[
            MarketStructureDimension(
                dimension=name,
                availability=StructureAvailability.ABSTAINED,
                explanation="当前缺少完整的可比交易结构数据,暂不判断。",
            )
            for name in StructureDimensionName
        ],
        completeness_percent=0,
    )


def behavior_summary(
    snapshot: BehaviorPressureSnapshotV1_0 | None,
    *,
    as_of: datetime,
) -> BehaviorPressureSummary:
    """Project only public behavior fields; lineage remains in the persisted assessment."""

    if snapshot is None:
        return _unavailable_behavior(as_of)
    return BehaviorPressureSummary(
        mode=snapshot.mode,
        score=snapshot.score,
        band=snapshot.band,
        availability=snapshot.availability,
        score_mapping=snapshot.score_mapping,
        as_of=snapshot.as_of,
        facets=snapshot.facets,
        structure_dimensions=snapshot.structure_dimensions,
        drivers=snapshot.drivers,
        mitigators=snapshot.mitigators,
        historical_analogs=snapshot.historical_analogs,
        completeness_percent=snapshot.completeness_percent,
    )


class RuntimeResearchEnricher:
    """Compose deterministic values first, then attach optional model explanations."""

    def __init__(
        self,
        *,
        settings: Settings,
        behavior_repository: BehaviorPressureRepositoryPort | None,
        behavior_semantics_hash: str | None = BEHAVIOR_SEMANTICS_HASH,
    ) -> None:
        self._settings = settings
        self._behavior_repository = behavior_repository
        self._behavior_semantics_hash = behavior_semantics_hash

    async def _latest_behavior(
        self,
        context: OperationContext,
    ) -> BehaviorPressureSnapshotV1_0 | None:
        if self._behavior_repository is None:
            return None
        page = await self._behavior_repository.query_assessments(
            BehaviorPressureQuery(
                schema_version="1.0",
                context=context,
                mode=PressureMode.POST_CLOSE,
                limit=100 if self._behavior_semantics_hash is not None else 1,
            )
        )
        if self._behavior_semantics_hash is None:
            return page.items[0] if page.items else None
        return next(
            (
                item
                for item in page.items
                if item.pit_lineage.comparison_semantics_hash == self._behavior_semantics_hash
            ),
            None,
        )

    async def enrich(
        self,
        *,
        briefing: PremarketBriefingViewV1_2,
        context: OperationContext,
        evidence_ids: list[UUID],
    ) -> PremarketBriefingViewV1_3:
        if (
            briefing.instrument != _R1_RESEARCH_INSTRUMENT
            and briefing.instrument != _R1_LEGACY_INSTRUMENT
        ):
            raise ValueError("R1 research enrichment requires the fixed XSHG:603986 identity")
        attention = build_attention_risk(briefing)
        behavior = behavior_summary(await self._latest_behavior(context), as_of=briefing.as_of)
        update = build_plain_language_update(briefing)
        unique_evidence_ids = list(dict.fromkeys(evidence_ids))
        flash = await generate_r1_interpretation(
            settings=self._settings,
            context=context,
            attention=attention,
            behavior=behavior,
            update=update,
            evidence_ids=unique_evidence_ids,
        )
        pro = await generate_r1_pro_interpretation(
            settings=self._settings,
            context=context,
            attention=attention,
            behavior=behavior,
            update=update,
            evidence_ids=unique_evidence_ids,
        )
        payload = briefing.model_dump(mode="python")
        payload.update(
            {
                "schema_version": "1.3",
                "instrument": _R1_RESEARCH_INSTRUMENT.model_copy(deep=True),
                "attention_risk_index": attention,
                "behavior_pressure_summary": behavior,
                "plain_language_update": update,
                "flash_interpretation": flash,
                "pro_interpretation": pro,
            }
        )
        return PremarketBriefingViewV1_3.model_validate(payload)


def briefing_evidence_ids(briefing: PremarketBriefingViewV1_2) -> list[UUID]:
    sections = briefing.sections
    return [
        evidence_id
        for section in (
            sections.statutory_announcements,
            sections.financial_changes,
            sections.market_snapshot,
            sections.company_industry_news,
            sections.macro_flashes,
        )
        for item in section.items
        for evidence_id in item.evidence_ids
    ]


__all__ = [
    "ResearchBriefingEnricherPort",
    "RuntimeResearchEnricher",
    "behavior_summary",
    "briefing_evidence_ids",
    "build_attention_risk",
    "build_plain_language_update",
]
