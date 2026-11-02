"""Pure event-to-briefing projection and cross-document validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from uuid import UUID

from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.event_views import EventEvidenceView, EventEvidenceViewV1_2
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingItem,
    PremarketBriefingViewV1_2,
)

_SECTION_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("statutory.", "statutory_announcements"),
    ("financial.", "financial_changes"),
    ("market.", "market_snapshot"),
    ("company.", "company_industry_news"),
    ("macro.", "macro_flashes"),
)


def briefing_section_for_event(event_type: str) -> str:
    """Map one registered R1 event type to its fixed briefing section."""

    for prefix, section in _SECTION_PREFIXES:
        if event_type.startswith(prefix):
            return section
    raise ValueError("unsupported R1 briefing event type")


def project_briefing_item(event: EventEvidenceView) -> PremarketBriefingItem:
    """Project every public item field from one selected evidence revision."""

    published_at = min(item.published_at for item in event.evidence)
    first_seen_at = min(item.first_seen_at for item in event.evidence)
    source_roles = sorted(
        {item.source_role for item in event.evidence},
        key=lambda item: item.value,
    )
    evidence_ids = sorted({item.evidence_id for item in event.evidence}, key=str)
    fact_values = [f"{fact.slot}={fact.value.value}" for fact in event.facts]
    fact_summary = "; ".join(fact_values) if fact_values else "该修订只包含更正或撤回证据。"
    inference = (
        "来源数值存在差异, 系统保留全部证据且不做平均。"
        if event.change_type is BriefingChangeType.CONTRADICTION
        else None
    )
    return PremarketBriefingItem(
        event_id=event.event_id,
        event_revision=event.revision,
        headline=event.title,
        fact_summary=fact_summary,
        inference=inference,
        change_type=event.change_type,
        published_at=published_at,
        first_seen_at=first_seen_at,
        source_roles=source_roles,
        evidence_ids=evidence_ids,
    )


def validate_briefing_evidence_projection(
    *,
    briefing: PremarketBriefingViewV1_2,
    evidence_by_event_id: Mapping[UUID, EventEvidenceViewV1_2],
) -> None:
    """Reject orphaned, conflicting, or point-in-time-invalid public projections."""

    section_items = {
        "statutory_announcements": briefing.sections.statutory_announcements.items,
        "financial_changes": briefing.sections.financial_changes.items,
        "market_snapshot": briefing.sections.market_snapshot.items,
        "company_industry_news": briefing.sections.company_industry_news.items,
        "macro_flashes": briefing.sections.macro_flashes.items,
    }
    located: dict[UUID, tuple[str, PremarketBriefingItem]] = {}
    for section_name, items in section_items.items():
        for item in items:
            if item.event_id in located:
                raise ValueError("briefing event_id appears in more than one section")
            located[item.event_id] = (section_name, item)
    if set(located) != set(evidence_by_event_id):
        raise ValueError("briefing and evidence event sets must match exactly")

    expected_instrument = briefing.instrument.instrument_id
    for event_id, evidence in evidence_by_event_id.items():
        if evidence.event_id != event_id:
            raise ValueError("evidence mapping key does not match event_id")
        if evidence.instrument_ids != [expected_instrument]:
            raise ValueError("evidence instrument does not match the briefing instrument")
        if evidence.as_of != briefing.as_of:
            raise ValueError("evidence as_of does not match the briefing as_of")
        if evidence.runtime_mode is not briefing.runtime_mode:
            raise ValueError("evidence runtime_mode does not match the briefing")
        if not briefing.comparison_since < evidence.revision_known_at <= briefing.as_of:
            raise ValueError("evidence revision falls outside the briefing knowledge window")
        if any(
            source.provenance.release_tier is not briefing.release_tier
            for source in evidence.evidence
        ):
            raise ValueError("evidence release_tier does not match the briefing")

        actual_section, actual_item = located[event_id]
        if actual_section != briefing_section_for_event(evidence.event_type):
            raise ValueError("briefing item appears in the wrong section")
        if actual_item != project_briefing_item(evidence):
            raise ValueError("briefing item is not the exact evidence projection")


__all__ = [
    "briefing_section_for_event",
    "project_briefing_item",
    "validate_briefing_evidence_projection",
]
