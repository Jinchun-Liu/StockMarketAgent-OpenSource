from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.common import InstrumentRef
from stock_assistant.contracts.ingestion import (
    SourceAccessChannelV1_2,
    SourceEvidenceRole,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceReleaseTier,
    SourceRightsStatusV1_2,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import (
    BriefingIngestionProgressV1_2,
    PremarketBriefingItem,
    PremarketBriefingSection,
    PremarketBriefingSections,
    PremarketBriefingViewV1_2,
    SourceAdvanceState,
    SourceCoverageStatus,
    SourceCoverageV1_2,
    SourceDataMode,
    SourceIngestionProgress,
    SourceRole,
)
from stock_assistant.contracts.public_snapshots import (
    PublicRunState,
    PublicSnapshotCompleteness,
)
from stock_assistant.contracts.sectors import (
    ExchangeCalendarResult,
    TradingDay,
    TradingDayStatus,
)
from stock_assistant.modules.briefings.degraded_preview import (
    DegradedPreviewDecision,
    DegradedPreviewPolicy,
    DegradedPreviewReason,
    DegradedPreviewRequest,
    DegradedPreviewState,
)

INSTRUMENT = InstrumentRef(
    instrument_id="XSHG:603986",
    symbol="603986.SH",
    exchange="XSHG",
    market="CN",
    asset_class="equity",
    currency="CNY",
)


def _provenance(source_id: str) -> SourceProvenanceV1_2:
    return SourceProvenanceV1_2(
        schema_version="1.2",
        provider="stock_market_agent",
        access_channel=SourceAccessChannelV1_2.OFFICIAL_HTTP,
        adapter_name="degraded_preview_test",
        adapter_version="1.0.0",
        endpoint_name="fixture",
        source_identifier=source_id,
        runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatusV1_2.UNVERIFIED,
        quality_tier=SourceQualityTier.FREE_OFFICIAL,
        evidence_role=SourceEvidenceRole.DISCOVERY,
    )


def _coverage(
    source_id: str,
    capability: str,
    *,
    status: SourceCoverageStatus,
    observed_at: datetime,
    suffix: int,
) -> SourceCoverageV1_2:
    healthy = status is SourceCoverageStatus.OK
    last_success_at = observed_at - timedelta(minutes=5) if healthy else None
    progress = SourceIngestionProgress(
        business_watermark=last_success_at,
        batch_audit_id=UUID(f"20000000-0000-0000-0000-{suffix:012d}"),
        advance_state=(SourceAdvanceState.ADVANCED if healthy else SourceAdvanceState.NOT_ADVANCED),
        data_mode=SourceDataMode.REAL,
    )
    return SourceCoverageV1_2(
        source_id=source_id,
        capability=capability,
        source_role=SourceRole.DISCOVERY,
        status=status,
        observed_at=observed_at,
        last_success_at=last_success_at,
        license_id="dev-only-unverified",
        gap_reason=None if healthy else "fixture source gap",
        progress=progress,
        provenance=_provenance(source_id),
    )


def _briefing(
    data_as_of: datetime,
    *,
    statuses: tuple[SourceCoverageStatus, ...] = (SourceCoverageStatus.OK,),
) -> PremarketBriefingViewV1_2:
    coverages = tuple(
        _coverage(
            f"source_{index}",
            f"capability.{index}",
            status=status,
            observed_at=data_as_of,
            suffix=index,
        )
        for index, status in enumerate(statuses, start=1)
    )
    primary = coverages[0]
    item = PremarketBriefingItem(
        event_id=UUID("10000000-0000-0000-0000-000000000001"),
        event_revision=1,
        headline="Evidence-backed item",
        fact_summary="One preserved item proves that LKG data is not replaced with zeros.",
        inference=None,
        change_type=BriefingChangeType.NEW,
        published_at=data_as_of - timedelta(minutes=10),
        first_seen_at=data_as_of - timedelta(minutes=5),
        source_roles=[SourceRole.DISCOVERY],
        evidence_ids=[UUID("30000000-0000-0000-0000-000000000001")],
    )
    sections = PremarketBriefingSections(
        statutory_announcements=PremarketBriefingSection(items=[item]),
        financial_changes=PremarketBriefingSection(),
        market_snapshot=PremarketBriefingSection(),
        company_industry_news=PremarketBriefingSection(),
        macro_flashes=PremarketBriefingSection(),
    )
    return PremarketBriefingViewV1_2(
        schema_version="1.2",
        briefing_id=UUID("40000000-0000-0000-0000-000000000001"),
        instrument=INSTRUMENT,
        comparison_since=data_as_of - timedelta(days=1),
        as_of=data_as_of,
        generated_at=data_as_of + timedelta(minutes=1),
        sections=sections,
        ingestion_progress=BriefingIngestionProgressV1_2(
            source_id=primary.source_id,
            capability=primary.capability,
            **primary.progress.model_dump(),
            provenance=primary.provenance,
        ),
        source_coverage=list(coverages),
        ruleset_version="degraded-preview-test-v1",
        runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        release_tier=SourceReleaseTier.DEV_ONLY,
    )


def _day(
    trading_date: date,
    *,
    as_of: datetime,
    status: TradingDayStatus = TradingDayStatus.OPEN,
) -> TradingDay:
    return TradingDay(
        schema_version="1.0",
        exchange="XSHG",
        trading_date=trading_date,
        status=status,
        first_seen_at=as_of - timedelta(days=365),
        calendar_version="fixture-v1",
    )


def _calendar(
    as_of: datetime,
    *days: tuple[date, TradingDayStatus] | date,
    degraded: bool = False,
) -> ExchangeCalendarResult:
    values = [
        _day(
            value[0] if isinstance(value, tuple) else value,
            as_of=as_of,
            status=value[1] if isinstance(value, tuple) else TradingDayStatus.OPEN,
        )
        for value in days
    ]
    return ExchangeCalendarResult(
        schema_version="1.0",
        days=values,
        as_of=as_of,
        calendar_version="fixture-v1",
        degraded=degraded,
        source_gaps=["CALENDAR_GAP"] if degraded else [],
    )


def _calendar_range(
    as_of: datetime,
    date_from: date,
    date_to: date,
    *,
    open_dates: set[date],
    omitted_dates: set[date] | None = None,
    degraded: bool = False,
) -> ExchangeCalendarResult:
    omitted = omitted_dates or set()
    day_count = (date_to - date_from).days + 1
    rows: list[tuple[date, TradingDayStatus]] = []
    for offset in range(day_count):
        calendar_date = date_from + timedelta(days=offset)
        if calendar_date in omitted:
            continue
        rows.append(
            (
                calendar_date,
                (TradingDayStatus.OPEN if calendar_date in open_dates else TradingDayStatus.CLOSED),
            )
        )
    return _calendar(as_of, *rows, degraded=degraded)


def _request(
    *,
    as_of: datetime,
    calendar: ExchangeCalendarResult,
    current: PremarketBriefingViewV1_2 | None = None,
    last_known_good: PremarketBriefingViewV1_2 | None = None,
) -> DegradedPreviewRequest:
    return DegradedPreviewRequest(
        as_of=as_of,
        calendar=calendar,
        current_briefing=current,
        last_known_good=last_known_good,
    )


def _assert_five_sections(decision_state: DegradedPreviewDecision) -> None:
    sections = decision_state.sections
    assert list(type(sections).model_fields) == [
        "statutory_announcements",
        "financial_changes",
        "market_snapshot",
        "company_industry_news",
        "macro_flashes",
    ]


def test_all_healthy_current_briefing_is_complete_and_fresh() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    briefing = _briefing(target - timedelta(minutes=5))

    decision = DegradedPreviewPolicy().decide(
        _request(as_of=target, calendar=_calendar(target), current=briefing)
    )

    assert decision.state is DegradedPreviewState.FRESH
    assert decision.completeness is PublicSnapshotCompleteness.COMPLETE
    assert decision.run_state is PublicRunState.SUCCEEDED
    assert decision.briefing is briefing
    assert decision.trading_sessions_old == 0
    assert decision.reason is None
    assert decision.source_gaps == ()
    _assert_five_sections(decision)


def test_one_source_gap_keeps_other_results_and_becomes_partial() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    briefing = _briefing(
        target - timedelta(minutes=5),
        statuses=(SourceCoverageStatus.OK, SourceCoverageStatus.UNAVAILABLE),
    )

    decision = DegradedPreviewPolicy().decide(
        _request(as_of=target, calendar=_calendar(target), current=briefing)
    )

    assert decision.state is DegradedPreviewState.PARTIAL
    assert decision.completeness is PublicSnapshotCompleteness.PARTIAL
    assert decision.run_state is PublicRunState.DEGRADED
    assert decision.briefing is briefing
    assert decision.sections.statutory_announcements.items
    assert [(gap.source_id, gap.capability) for gap in decision.source_gaps] == [
        ("source_2", "capability.2")
    ]
    assert decision.reason is DegradedPreviewReason.CURRENT_SOURCE_GAPS


def test_partial_current_briefing_is_not_hidden_by_an_older_lkg() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    current = _briefing(
        target - timedelta(minutes=5),
        statuses=(SourceCoverageStatus.OK, SourceCoverageStatus.RIGHTS_BLOCKED),
    )
    older_lkg = _briefing(datetime(2026, 8, 7, 1, 5, tzinfo=UTC))

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar(target, date(2026, 8, 10)),
            current=current,
            last_known_good=older_lkg,
        )
    )

    assert decision.state is DegradedPreviewState.PARTIAL
    assert decision.briefing is current


def test_weekend_lkg_is_explicitly_stale_without_aging_a_session() -> None:
    friday = datetime(2026, 8, 7, 1, 5, tzinfo=UTC)
    sunday = datetime(2026, 8, 9, 1, 10, tzinfo=UTC)

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=sunday,
            calendar=_calendar_range(
                sunday,
                date(2026, 8, 7),
                date(2026, 8, 9),
                open_dates={date(2026, 8, 7)},
            ),
            last_known_good=_briefing(friday),
        )
    )

    assert decision.state is DegradedPreviewState.STALE
    assert decision.completeness is PublicSnapshotCompleteness.PARTIAL
    assert decision.run_state is PublicRunState.DEGRADED
    assert decision.trading_sessions_old == 0
    assert decision.reason is DegradedPreviewReason.LKG_WITHIN_THREE_SESSIONS


def test_same_session_lkg_is_explicitly_stale() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    last_known_good = _briefing(target - timedelta(minutes=5))

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar_range(
                target,
                date(2026, 8, 10),
                date(2026, 8, 10),
                open_dates={date(2026, 8, 10)},
            ),
            last_known_good=last_known_good,
        )
    )

    assert decision.state is DegradedPreviewState.STALE
    assert decision.trading_sessions_old == 0
    assert decision.briefing is last_known_good


def test_monday_after_friday_is_one_stale_xshg_session() -> None:
    friday = datetime(2026, 8, 7, 1, 5, tzinfo=UTC)
    monday = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=monday,
            calendar=_calendar_range(
                monday,
                date(2026, 8, 7),
                date(2026, 8, 10),
                open_dates={date(2026, 8, 7), date(2026, 8, 10)},
            ),
            last_known_good=_briefing(friday),
        )
    )

    assert decision.state is DegradedPreviewState.STALE
    assert decision.completeness is PublicSnapshotCompleteness.PARTIAL
    assert decision.run_state is PublicRunState.DEGRADED
    assert decision.trading_sessions_old == 1
    assert decision.reason is DegradedPreviewReason.LKG_WITHIN_THREE_SESSIONS
    assert decision.sections.statutory_announcements.items


def test_closed_holiday_is_not_counted_as_a_session() -> None:
    friday = datetime(2026, 9, 25, 1, 5, tzinfo=UTC)
    tuesday = datetime(2026, 9, 29, 1, 10, tzinfo=UTC)

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=tuesday,
            calendar=_calendar_range(
                tuesday,
                date(2026, 9, 25),
                date(2026, 9, 29),
                open_dates={date(2026, 9, 25), date(2026, 9, 29)},
            ),
            last_known_good=_briefing(friday),
        )
    )

    assert decision.state is DegradedPreviewState.STALE
    assert decision.trading_sessions_old == 1


@pytest.mark.parametrize(
    ("target_day", "session_days", "expected_state", "expected_age"),
    [
        (
            12,
            (date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)),
            DegradedPreviewState.STALE,
            3,
        ),
        (
            13,
            (
                date(2026, 8, 10),
                date(2026, 8, 11),
                date(2026, 8, 12),
                date(2026, 8, 13),
            ),
            DegradedPreviewState.UNAVAILABLE,
            4,
        ),
    ],
)
def test_lkg_expires_after_three_xshg_sessions(
    target_day: int,
    session_days: tuple[date, ...],
    expected_state: DegradedPreviewState,
    expected_age: int,
) -> None:
    friday = datetime(2026, 8, 7, 1, 5, tzinfo=UTC)
    target = datetime(2026, 8, target_day, 1, 10, tzinfo=UTC)

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar_range(
                target,
                date(2026, 8, 7),
                target.date(),
                open_dates={date(2026, 8, 7), *session_days},
            ),
            last_known_good=_briefing(friday),
        )
    )

    assert decision.state is expected_state
    assert decision.trading_sessions_old == expected_age
    if expected_state is DegradedPreviewState.UNAVAILABLE:
        assert decision.completeness is PublicSnapshotCompleteness.UNAVAILABLE
        assert decision.run_state is PublicRunState.FAILED
        assert decision.briefing is None
        assert decision.reason is DegradedPreviewReason.LKG_EXPIRED
        assert decision.display_message == "暂无可用数据"
        assert all(not section.items for section in decision.sections_in_order)
    else:
        assert decision.briefing is not None


def test_no_candidate_returns_unavailable_with_five_empty_sections() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)

    decision = DegradedPreviewPolicy().decide(_request(as_of=target, calendar=_calendar(target)))

    assert decision.state is DegradedPreviewState.UNAVAILABLE
    assert decision.completeness is PublicSnapshotCompleteness.UNAVAILABLE
    assert decision.run_state is PublicRunState.FAILED
    assert decision.reason is DegradedPreviewReason.NO_USABLE_BRIEFING
    assert decision.display_message == "暂无可用数据"
    assert decision.briefing is None
    assert all(not section.items for section in decision.sections_in_order)
    _assert_five_sections(decision)


def test_all_current_sources_unhealthy_falls_back_to_lkg() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    current = _briefing(
        target - timedelta(minutes=5),
        statuses=(SourceCoverageStatus.UNAVAILABLE, SourceCoverageStatus.RIGHTS_BLOCKED),
    )
    lkg = _briefing(datetime(2026, 8, 7, 1, 5, tzinfo=UTC))

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar_range(
                target,
                date(2026, 8, 7),
                date(2026, 8, 10),
                open_dates={date(2026, 8, 7), date(2026, 8, 10)},
            ),
            current=current,
            last_known_good=lkg,
        )
    )

    assert decision.state is DegradedPreviewState.STALE
    assert decision.briefing is lkg
    assert len(decision.source_gaps) == 2


def test_all_current_sources_unhealthy_without_lkg_remains_publishable_partial() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    current = _briefing(
        target - timedelta(minutes=5),
        statuses=(SourceCoverageStatus.UNAVAILABLE, SourceCoverageStatus.RIGHTS_BLOCKED),
    )

    decision = DegradedPreviewPolicy().decide(
        _request(as_of=target, calendar=_calendar(target), current=current)
    )

    assert decision.state is DegradedPreviewState.PARTIAL
    assert decision.completeness is PublicSnapshotCompleteness.PARTIAL
    assert decision.run_state is PublicRunState.DEGRADED
    assert decision.briefing is current
    assert decision.trading_sessions_old == 0
    assert decision.reason is DegradedPreviewReason.CURRENT_ALL_SOURCES_UNHEALTHY
    assert len(decision.source_gaps) == 2
    _assert_five_sections(decision)


def test_unhealthy_lkg_does_not_hide_publishable_current_gaps() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    current = _briefing(
        target - timedelta(minutes=5),
        statuses=(SourceCoverageStatus.UNAVAILABLE, SourceCoverageStatus.RIGHTS_BLOCKED),
    )
    unhealthy_lkg = _briefing(
        datetime(2026, 8, 7, 1, 5, tzinfo=UTC),
        statuses=(SourceCoverageStatus.UNAVAILABLE, SourceCoverageStatus.DELAYED),
    )

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar(target),
            current=current,
            last_known_good=unhealthy_lkg,
        )
    )

    assert decision.state is DegradedPreviewState.PARTIAL
    assert decision.briefing is current
    assert decision.reason is DegradedPreviewReason.CURRENT_ALL_SOURCES_UNHEALTHY


def test_expired_healthy_lkg_does_not_hide_publishable_current_gaps() -> None:
    target = datetime(2026, 8, 14, 1, 10, tzinfo=UTC)
    current = _briefing(
        target - timedelta(minutes=5),
        statuses=(SourceCoverageStatus.UNAVAILABLE, SourceCoverageStatus.RIGHTS_BLOCKED),
    )
    old_lkg = _briefing(datetime(2026, 8, 7, 1, 5, tzinfo=UTC))

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar_range(
                target,
                date(2026, 8, 7),
                date(2026, 8, 14),
                open_dates={
                    date(2026, 8, 7),
                    date(2026, 8, 10),
                    date(2026, 8, 11),
                    date(2026, 8, 12),
                    date(2026, 8, 13),
                    date(2026, 8, 14),
                },
            ),
            current=current,
            last_known_good=old_lkg,
        )
    )

    assert decision.state is DegradedPreviewState.PARTIAL
    assert decision.briefing is current
    assert decision.reason is DegradedPreviewReason.CURRENT_ALL_SOURCES_UNHEALTHY


def test_degraded_calendar_never_guesses_lkg_age() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=target,
            calendar=_calendar_range(
                target,
                date(2026, 8, 7),
                date(2026, 8, 10),
                open_dates={date(2026, 8, 7), date(2026, 8, 10)},
                degraded=True,
            ),
            last_known_good=_briefing(datetime(2026, 8, 7, 1, 5, tzinfo=UTC)),
        )
    )

    assert decision.state is DegradedPreviewState.UNAVAILABLE
    assert decision.trading_sessions_old is None
    assert decision.reason is DegradedPreviewReason.CALENDAR_UNAVAILABLE


def test_policy_rejects_future_or_cross_mode_fallback_candidates() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    future = _briefing(target + timedelta(minutes=1))
    with pytest.raises(ValueError, match="future information"):
        DegradedPreviewPolicy().decide(
            _request(as_of=target, calendar=_calendar(target), last_known_good=future)
        )

    current = _briefing(target - timedelta(minutes=5))
    cross_mode = current.model_copy(update={"runtime_mode": SourceRuntimeModeV1_2.DEMO_REPLAY})
    with pytest.raises(ValueError, match="runtime mode"):
        DegradedPreviewPolicy().decide(
            _request(
                as_of=target,
                calendar=_calendar(target),
                current=current,
                last_known_good=cross_mode,
            )
        )


def test_missing_calendar_day_fails_closed_even_without_calendar_gap_flags() -> None:
    friday = datetime(2026, 8, 7, 1, 5, tzinfo=UTC)
    tuesday = datetime(2026, 8, 11, 1, 10, tzinfo=UTC)

    calendar = _calendar_range(
        tuesday,
        date(2026, 8, 7),
        date(2026, 8, 11),
        open_dates={date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11)},
        omitted_dates={date(2026, 8, 10)},
    )
    assert not calendar.degraded
    assert calendar.source_gaps == []

    decision = DegradedPreviewPolicy().decide(
        _request(
            as_of=tuesday,
            calendar=calendar,
            last_known_good=_briefing(friday),
        )
    )

    assert decision.state is DegradedPreviewState.UNAVAILABLE
    assert decision.trading_sessions_old is None
    assert decision.reason is DegradedPreviewReason.CALENDAR_UNAVAILABLE


def test_single_wrong_target_candidate_is_rejected() -> None:
    target = datetime(2026, 8, 10, 1, 10, tzinfo=UTC)
    wrong_instrument = InstrumentRef(
        instrument_id="XSHG:600000",
        symbol="600000.SH",
        exchange="XSHG",
        market="CN",
        asset_class="equity",
        currency="CNY",
    )
    wrong_target = _briefing(target - timedelta(minutes=5)).model_copy(
        update={"instrument": wrong_instrument}
    )

    with pytest.raises(ValueError, match="R1 fixed target"):
        DegradedPreviewPolicy().decide(
            _request(
                as_of=target,
                calendar=_calendar(target),
                current=wrong_target,
            )
        )
