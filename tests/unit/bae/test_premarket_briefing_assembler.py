from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.common import InstrumentRef
from stock_assistant.contracts.premarket_views import (
    BriefingIngestionProgress,
    PremarketBriefingItem,
    SourceAdvanceState,
    SourceCoverage,
    SourceCoverageStatus,
    SourceDataMode,
    SourceIngestionProgress,
    SourceRole,
)
from stock_assistant.modules.briefings.assembler import PremarketBriefingAssembler

AS_OF = datetime(2026, 7, 28, 1, tzinfo=UTC)
GENERATED_AT = AS_OF + timedelta(minutes=1)
INSTRUMENT = InstrumentRef(
    instrument_id="XSHG:603986",
    symbol="603986.SH",
    exchange="XSHG",
    market="CN",
    asset_class="equity",
    currency="CNY",
)


def _item(
    suffix: int,
    *,
    first_seen_at: datetime = AS_OF - timedelta(minutes=5),
    change_type: BriefingChangeType = BriefingChangeType.NEW,
) -> PremarketBriefingItem:
    return PremarketBriefingItem(
        event_id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
        event_revision=1,
        headline=f"Fact {suffix}",
        fact_summary=f"Evidence-backed fact {suffix}.",
        inference=None,
        change_type=change_type,
        published_at=first_seen_at - timedelta(minutes=1),
        first_seen_at=first_seen_at,
        source_roles=[SourceRole.STATUTORY],
        evidence_ids=[UUID(f"10000000-0000-0000-0000-{suffix:012d}")],
    )


def _coverage(
    source_id: str,
    capability: str,
    *,
    audit_suffix: int,
) -> SourceCoverage:
    progress = SourceIngestionProgress(
        business_watermark=AS_OF - timedelta(minutes=2),
        batch_audit_id=UUID(f"20000000-0000-0000-0000-{audit_suffix:012d}"),
        advance_state=SourceAdvanceState.ADVANCED,
        data_mode=SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY,
    )
    return SourceCoverage(
        source_id=source_id,
        capability=capability,
        source_role=SourceRole.PROFESSIONAL_INFORMATION,
        status=SourceCoverageStatus.OK,
        observed_at=AS_OF,
        last_success_at=AS_OF - timedelta(minutes=2),
        license_id="synthetic-test-license-v1",
        progress=progress,
    )


def _assemble(
    *,
    statutory: tuple[PremarketBriefingItem, ...] = (),
    financial: tuple[PremarketBriefingItem, ...] = (),
    market: tuple[PremarketBriefingItem, ...] = (),
    news: tuple[PremarketBriefingItem, ...] = (),
    macro: tuple[PremarketBriefingItem, ...] = (),
):
    primary = _coverage("cninfo_data_service", "statutory.announcements", audit_suffix=1)
    secondary = _coverage("choice_quant_api", "market.snapshot", audit_suffix=2)
    ingestion_progress = BriefingIngestionProgress(
        source_id=primary.source_id,
        capability=primary.capability,
        **primary.progress.model_dump(),
    )
    return PremarketBriefingAssembler().assemble(
        briefing_id=UUID("30000000-0000-0000-0000-000000000001"),
        instrument=INSTRUMENT,
        comparison_since=AS_OF - timedelta(days=1),
        as_of=AS_OF,
        generated_at=GENERATED_AT,
        ingestion_progress=ingestion_progress,
        source_coverage=(secondary, primary),
        statutory_announcements=statutory,
        financial_changes=financial,
        market_snapshot=market,
        company_industry_news=news,
        macro_flashes=macro,
        ruleset_version="r1-deterministic-v1",
    )


def test_assembler_always_emits_five_sections_and_stable_ordering() -> None:
    newer = _item(2, first_seen_at=AS_OF - timedelta(minutes=2))
    older = _item(1, first_seen_at=AS_OF - timedelta(minutes=10))

    view = _assemble(statutory=(newer, older), market=(older,))

    assert list(type(view.sections).model_fields) == [
        "statutory_announcements",
        "financial_changes",
        "market_snapshot",
        "company_industry_news",
        "macro_flashes",
    ]
    assert view.sections.financial_changes.items == []
    assert view.sections.company_industry_news.items == []
    assert view.sections.macro_flashes.items == []
    assert [item.event_id for item in view.sections.statutory_announcements.items] == [
        older.event_id,
        newer.event_id,
    ]
    assert [(item.source_id, item.capability) for item in view.source_coverage] == [
        ("choice_quant_api", "market.snapshot"),
        ("cninfo_data_service", "statutory.announcements"),
    ]


def test_assembler_deduplicates_each_section_and_counts_suppressed_items() -> None:
    duplicate = _item(1)

    view = _assemble(statutory=(duplicate, duplicate), financial=(duplicate,))

    assert view.sections.statutory_announcements.items == [duplicate]
    assert view.sections.financial_changes.items == [duplicate]
    assert view.omitted_duplicate_count == 1


def test_assembler_is_independent_of_input_order() -> None:
    first = _item(1)
    second = _item(2)

    forward = _assemble(statutory=(first, second))
    reverse = _assemble(statutory=(second, first))

    assert forward == reverse


def test_assembler_orders_items_by_change_type_priority_then_time() -> None:
    new = _item(
        1,
        first_seen_at=AS_OF - timedelta(minutes=1),
        change_type=BriefingChangeType.NEW,
    )
    update = _item(
        2,
        first_seen_at=AS_OF - timedelta(minutes=4),
        change_type=BriefingChangeType.UPDATE,
    )
    correction = _item(
        3,
        first_seen_at=AS_OF - timedelta(minutes=3),
        change_type=BriefingChangeType.CORRECTION,
    )
    contradiction = _item(
        4,
        first_seen_at=AS_OF - timedelta(minutes=2),
        change_type=BriefingChangeType.CONTRADICTION,
    )

    view = _assemble(statutory=(contradiction, correction, update, new))

    assert [item.change_type for item in view.sections.statutory_announcements.items] == [
        BriefingChangeType.NEW,
        BriefingChangeType.UPDATE,
        BriefingChangeType.CORRECTION,
        BriefingChangeType.CONTRADICTION,
    ]


def test_assembler_merges_evidence_and_source_roles_for_same_fact() -> None:
    primary = _item(1)
    corroborating = primary.model_copy(
        update={
            "source_roles": [SourceRole.PROFESSIONAL_INFORMATION],
            "evidence_ids": [UUID("10000000-0000-0000-0000-000000000002")],
        }
    )

    view = _assemble(statutory=(corroborating, primary))

    assert view.sections.statutory_announcements.items == [
        primary.model_copy(
            update={
                "source_roles": [
                    SourceRole.STATUTORY,
                    SourceRole.PROFESSIONAL_INFORMATION,
                ],
                "evidence_ids": [
                    UUID("10000000-0000-0000-0000-000000000001"),
                    UUID("10000000-0000-0000-0000-000000000002"),
                ],
            }
        )
    ]
    assert view.omitted_duplicate_count == 1


def test_assembler_preserves_all_evidence_for_merged_contradiction() -> None:
    contradiction = _item(1, change_type=BriefingChangeType.CONTRADICTION)
    additional_evidence = contradiction.model_copy(
        update={
            "source_roles": [SourceRole.PROFESSIONAL_MARKET],
            "evidence_ids": [
                UUID("10000000-0000-0000-0000-000000000003"),
                UUID("10000000-0000-0000-0000-000000000002"),
            ],
        }
    )

    view = _assemble(statutory=(additional_evidence, contradiction))
    merged = view.sections.statutory_announcements.items[0]

    assert merged.change_type is BriefingChangeType.CONTRADICTION
    assert merged.source_roles == [
        SourceRole.STATUTORY,
        SourceRole.PROFESSIONAL_MARKET,
    ]
    assert merged.evidence_ids == [
        UUID("10000000-0000-0000-0000-000000000001"),
        UUID("10000000-0000-0000-0000-000000000002"),
        UUID("10000000-0000-0000-0000-000000000003"),
    ]


def test_assembler_rejects_merged_fact_with_more_than_100_evidence_ids() -> None:
    original = _item(1).model_copy(
        update={
            "evidence_ids": [
                UUID(f"10000000-0000-0000-0000-{suffix:012d}") for suffix in range(1, 101)
            ]
        }
    )
    additional_evidence = original.model_copy(
        update={
            "evidence_ids": [
                UUID("10000000-0000-0000-0000-000000000101"),
            ]
        }
    )

    with pytest.raises(ValidationError, match="List should have at most 100 items"):
        _assemble(statutory=(original, additional_evidence))


def test_assembler_rejects_conflicting_payloads_for_one_event_revision() -> None:
    original = _item(1)
    conflicting = original.model_copy(update={"headline": "Conflicting headline"})

    with pytest.raises(ValueError, match="conflicting items for event revision"):
        _assemble(statutory=(original, conflicting))


def test_assembler_rejects_items_not_visible_at_as_of() -> None:
    future_item = _item(1, first_seen_at=AS_OF + timedelta(seconds=1))

    with pytest.raises(ValueError, match="first_seen_at cannot be later than as_of"):
        _assemble(macro=(future_item,))
