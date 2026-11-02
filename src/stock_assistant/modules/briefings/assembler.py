"""Deterministic assembly of the frozen R1 premarket briefing view."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.common import InstrumentRef
from stock_assistant.contracts.premarket_views import (
    BriefingIngestionProgress,
    PremarketBriefingItem,
    PremarketBriefingSection,
    PremarketBriefingSections,
    PremarketBriefingView,
    SourceCoverage,
    SourceRole,
)

_CHANGE_TYPE_PRIORITY = {
    BriefingChangeType.NEW: 0,
    BriefingChangeType.UPDATE: 1,
    BriefingChangeType.CORRECTION: 2,
    BriefingChangeType.CONTRADICTION: 3,
}
_SOURCE_ROLE_PRIORITY = {
    SourceRole.STATUTORY: 0,
    SourceRole.PROFESSIONAL_MARKET: 1,
    SourceRole.PROFESSIONAL_INFORMATION: 2,
    SourceRole.DISCOVERY: 3,
}


class PremarketBriefingAssembler:
    """Build the five fixed R1 sections without probabilistic or LLM behavior."""

    def assemble(
        self,
        *,
        briefing_id: UUID,
        instrument: InstrumentRef,
        comparison_since: datetime,
        as_of: datetime,
        generated_at: datetime,
        ingestion_progress: BriefingIngestionProgress,
        source_coverage: Sequence[SourceCoverage],
        statutory_announcements: Sequence[PremarketBriefingItem] = (),
        financial_changes: Sequence[PremarketBriefingItem] = (),
        market_snapshot: Sequence[PremarketBriefingItem] = (),
        company_industry_news: Sequence[PremarketBriefingItem] = (),
        macro_flashes: Sequence[PremarketBriefingItem] = (),
        omitted_duplicate_count: int = 0,
        ruleset_version: str,
    ) -> PremarketBriefingView:
        """Assemble, validate, deduplicate, and canonically order one briefing."""
        section_inputs = (
            statutory_announcements,
            financial_changes,
            market_snapshot,
            company_industry_news,
            macro_flashes,
        )
        assembled_sections: list[PremarketBriefingSection] = []
        suppressed_count = omitted_duplicate_count
        for items in section_inputs:
            section, section_suppressed = self._assemble_section(items=items, as_of=as_of)
            assembled_sections.append(section)
            suppressed_count += section_suppressed
        (
            statutory_section,
            financial_section,
            market_section,
            news_section,
            macro_section,
        ) = assembled_sections

        return PremarketBriefingView(
            schema_version="1.0",
            briefing_id=briefing_id,
            instrument=instrument,
            comparison_since=comparison_since,
            as_of=as_of,
            generated_at=generated_at,
            ingestion_progress=ingestion_progress,
            source_coverage=sorted(
                source_coverage,
                key=lambda coverage: (coverage.source_id, coverage.capability),
            ),
            sections=PremarketBriefingSections(
                statutory_announcements=statutory_section,
                financial_changes=financial_section,
                market_snapshot=market_section,
                company_industry_news=news_section,
                macro_flashes=macro_section,
            ),
            omitted_duplicate_count=suppressed_count,
            ruleset_version=ruleset_version,
        )

    @staticmethod
    def _assemble_section(
        *,
        items: Sequence[PremarketBriefingItem],
        as_of: datetime,
    ) -> tuple[PremarketBriefingSection, int]:
        unique_items: dict[tuple[UUID, int], PremarketBriefingItem] = {}
        suppressed_count = 0
        for item in items:
            if item.first_seen_at > as_of:
                raise ValueError("briefing item first_seen_at cannot be later than as_of")
            identity = (item.event_id, item.event_revision)
            existing = unique_items.get(identity)
            if existing is None:
                unique_items[identity] = item
            else:
                unique_items[identity] = PremarketBriefingAssembler._merge_same_fact(
                    existing=existing,
                    candidate=item,
                )
                suppressed_count += 1

        ordered_items = sorted(
            unique_items.values(),
            key=lambda item: (
                _CHANGE_TYPE_PRIORITY[item.change_type],
                item.first_seen_at,
                item.published_at,
                str(item.event_id),
                item.event_revision,
            ),
        )
        return PremarketBriefingSection(items=ordered_items), suppressed_count

    @staticmethod
    def _merge_same_fact(
        *,
        existing: PremarketBriefingItem,
        candidate: PremarketBriefingItem,
    ) -> PremarketBriefingItem:
        semantic_fields = (
            "event_id",
            "event_revision",
            "headline",
            "fact_summary",
            "inference",
            "change_type",
            "published_at",
            "first_seen_at",
        )
        if any(getattr(existing, field) != getattr(candidate, field) for field in semantic_fields):
            raise ValueError(
                "conflicting items for event revision "
                f"{candidate.event_id}:{candidate.event_revision}"
            )

        return PremarketBriefingItem.model_validate(
            {
                **existing.model_dump(),
                "source_roles": sorted(
                    {*existing.source_roles, *candidate.source_roles},
                    key=_SOURCE_ROLE_PRIORITY.__getitem__,
                ),
                "evidence_ids": sorted(
                    {*existing.evidence_ids, *candidate.evidence_ids},
                    key=str,
                ),
            }
        )
