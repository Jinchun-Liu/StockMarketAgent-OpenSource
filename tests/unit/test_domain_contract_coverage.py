"""Behavioral coverage for ingestion, master-data, policy, portfolio and sector contracts."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from stock_assistant.contracts import ingestion as ing
from stock_assistant.contracts import instruments as ins
from stock_assistant.contracts import policies as pol
from stock_assistant.contracts import portfolio as pf
from stock_assistant.contracts import sectors as sec
from stock_assistant.contracts.common import (
    EntityRef,
    InstrumentRef,
    MoneyValue,
    MutationContext,
    OperationContext,
)
from stock_assistant.contracts.events import Direction, EvidenceRef, ImpactHorizon

NOW = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)
RIGHTS_HASH = "a" * 64
OTHER_RIGHTS_HASH = "b" * 64
CONTENT_HASH = "c" * 64


def _uuid(value: int) -> UUID:
    return UUID(int=value)


EVIDENCE_ID = _uuid(1)
OTHER_EVIDENCE_ID = _uuid(2)
COMPANY_ID = _uuid(3)
INDUSTRY_ID = _uuid(4)
REGION_ID = _uuid(5)
SECTOR_ID = _uuid(6)
OTHER_SECTOR_ID = _uuid(7)
BUSINESS_LINE_ID = _uuid(30)
HEAT_SNAPSHOT_ID = _uuid(63)

INSTRUMENT = InstrumentRef(
    instrument_id="SSE:600000",
    symbol="600000",
    exchange="SSE",
    market="CN",
)
OTHER_INSTRUMENT = InstrumentRef(
    instrument_id="SZSE:000001",
    symbol="000001",
    exchange="SZSE",
    market="CN",
)


def _operation_context(*, as_of: datetime = NOW) -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        request_id=_uuid(10),
        correlation_id=_uuid(11),
        as_of=as_of,
        requested_at=as_of,
    )


def _mutation_context(*, as_of: datetime = NOW) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        request_id=_uuid(12),
        correlation_id=_uuid(13),
        as_of=as_of,
        requested_at=as_of,
        idempotency_key="domain-contract-coverage-0001",
    )


def _source_capability(
    *,
    content_tier: ing.ContentTier = ing.ContentTier.STATUTORY_DISCLOSURE,
    fact_eligibility: ing.FactEligibility = ing.FactEligibility.COMPANY_FACT,
) -> ing.SourceCapability:
    return ing.SourceCapability(
        capability="exchange.announcements",
        content_tier=content_tier,
        fact_eligibility=fact_eligibility,
    )


def _source_descriptor(
    *,
    source_id: str = "exchange_test",
    source_tier: ing.SourceTier = ing.SourceTier.STATUTORY_DISCLOSURE,
    capabilities: list[ing.SourceCapability] | None = None,
) -> ing.SourceDescriptor:
    return ing.SourceDescriptor(
        schema_version="1.0",
        source_id=source_id,
        display_name="Synthetic exchange source",
        adapter_version="1.0.0",
        source_tier=source_tier,
        enabled=True,
        capabilities=capabilities or [_source_capability()],
        authority=0.98,
        rights_registry_id="synthetic-rights-v1",
    )


def _source_record(
    *,
    source_id: str = "exchange_test",
    source_tier: ing.SourceTier = ing.SourceTier.STATUTORY_DISCLOSURE,
    content_tier: ing.ContentTier = ing.ContentTier.STATUTORY_DISCLOSURE,
    fact_eligibility: ing.FactEligibility = ing.FactEligibility.COMPANY_FACT,
    first_seen_at: datetime = NOW - timedelta(minutes=2),
    fetched_at: datetime = NOW - timedelta(minutes=1),
    rights_snapshot_hash: str = RIGHTS_HASH,
    payload: dict[str, object] | None = None,
    payload_namespace: str | None = None,
    payload_owner: str | None = None,
) -> ing.SourceRecord:
    return ing.SourceRecord(
        record_id=_uuid(20),
        source_id=source_id,
        external_id="announcement-1",
        capability="exchange.announcements",
        record_type="exchange.announcement",
        source_tier=source_tier,
        content_tier=content_tier,
        fact_eligibility=fact_eligibility,
        authority=0.98,
        title="Synthetic announcement",
        canonical_url="https://example.invalid/announcement/1",
        event_time=NOW - timedelta(minutes=3),
        published_at=NOW - timedelta(minutes=3),
        first_seen_at=first_seen_at,
        fetched_at=fetched_at,
        content_sha256=CONTENT_HASH,
        payload=payload or {},
        payload_namespace=payload_namespace,
        payload_owner=payload_owner,
        rights_snapshot_hash=rights_snapshot_hash,
        provenance=ing.SourceProvenance(
            schema_version="1.1",
            provider="synthetic_exchange",
            access_channel=ing.SourceAccessChannel.AUTHORIZED_API,
            adapter_name="synthetic_test_adapter",
            adapter_version="1.0.0",
            endpoint_name="exchange_announcements",
            source_identifier="announcement-1",
            runtime_mode=ing.SourceRuntimeMode.AUTHORIZED,
            release_tier=ing.SourceReleaseTier.AUTHORIZED,
            rights_status=ing.SourceRightsStatus.APPROVED,
        ),
    )


def _fetch_batch(
    *,
    source_id: str = "exchange_test",
    records: list[ing.SourceRecord] | None = None,
    rights_snapshot_hash: str = RIGHTS_HASH,
    started_at: datetime = NOW - timedelta(minutes=1),
    completed_at: datetime = NOW,
) -> ing.FetchBatch:
    return ing.FetchBatch(
        schema_version="1.0",
        batch_id=_uuid(21),
        source_id=source_id,
        records=records if records is not None else [_source_record()],
        next_cursor="opaque-source-cursor",
        exhausted=False,
        started_at=started_at,
        completed_at=completed_at,
        source_watermark=NOW - timedelta(minutes=3),
        rights_snapshot_hash=rights_snapshot_hash,
    )


def _industry_membership(
    *,
    first_seen_at: datetime = NOW - timedelta(days=20),
    valid_until: datetime | None = None,
) -> ins.IndustryMembership:
    return ins.IndustryMembership(
        industry_id=INDUSTRY_ID,
        scheme=ins.IndustryScheme.CSRC,
        is_primary=True,
        valid_from=NOW - timedelta(days=365),
        valid_until=valid_until,
        first_seen_at=first_seen_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _region_assignment(
    *,
    first_seen_at: datetime = NOW - timedelta(days=20),
    valid_until: datetime | None = None,
) -> ins.RegionAssignment:
    return ins.RegionAssignment(
        region_id=REGION_ID,
        role=ins.RegionRole.HEADQUARTERS,
        valid_from=NOW - timedelta(days=365),
        valid_until=valid_until,
        first_seen_at=first_seen_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _business_line(
    *,
    business_line_id: UUID = BUSINESS_LINE_ID,
    first_seen_at: datetime = NOW - timedelta(days=20),
    valid_until: datetime | None = None,
) -> ins.BusinessLine:
    return ins.BusinessLine(
        business_line_id=business_line_id,
        name="Commercial banking",
        description="Synthetic business line for contract tests",
        revenue_share="0.625",
        valid_from=NOW - timedelta(days=365),
        valid_until=valid_until,
        first_seen_at=first_seen_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _industry_node(
    *,
    industry_id: UUID = INDUSTRY_ID,
    first_seen_at: datetime = NOW - timedelta(days=20),
) -> ins.IndustryNode:
    return ins.IndustryNode(
        schema_version="1.0",
        industry_id=industry_id,
        scheme=ins.IndustryScheme.CSRC,
        code="J66",
        name="Monetary financial services",
        level=2,
        valid_from=NOW - timedelta(days=365),
        first_seen_at=first_seen_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _region_node(
    *,
    region_id: UUID = REGION_ID,
    first_seen_at: datetime = NOW - timedelta(days=20),
) -> ins.RegionNode:
    return ins.RegionNode(
        schema_version="1.0",
        region_id=region_id,
        standard_code="310000",
        name="Shanghai",
        level=ins.RegionLevel.PROVINCE,
        valid_from=NOW - timedelta(days=365),
        first_seen_at=first_seen_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _company_profile(
    *,
    revision_known_at: datetime = NOW - timedelta(days=10),
    business_lines: list[ins.BusinessLine] | None = None,
    industries: list[ins.IndustryMembership] | None = None,
    regions: list[ins.RegionAssignment] | None = None,
) -> ins.CompanyProfile:
    return ins.CompanyProfile(
        schema_version="1.0",
        company_id=COMPANY_ID,
        legal_name="Synthetic Bank Co., Ltd.",
        short_name="Synthetic Bank",
        unified_credit_code="123456789012345678",
        business_summary="A synthetic issuer used only in deterministic contract tests.",
        business_lines=business_lines if business_lines is not None else [_business_line()],
        industries=industries if industries is not None else [_industry_membership()],
        regions=regions if regions is not None else [_region_assignment()],
        valid_from=NOW - timedelta(days=365),
        first_seen_at=NOW - timedelta(days=30),
        revision=2,
        revision_known_at=revision_known_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _instrument_profile(
    *,
    instrument: InstrumentRef = INSTRUMENT,
    instrument_type: ins.InstrumentType = ins.InstrumentType.EQUITY,
    company_id: UUID | None = COMPANY_ID,
    revision_known_at: datetime = NOW - timedelta(days=10),
) -> ins.InstrumentProfile:
    return ins.InstrumentProfile(
        schema_version="1.0",
        instrument=instrument,
        instrument_type=instrument_type,
        company_id=company_id,
        display_name="Synthetic Bank",
        listing_status=ins.ListingStatus.ACTIVE,
        board="main",
        lot_size="100",
        price_tick="0.01",
        listed_at=NOW - timedelta(days=3650),
        valid_from=NOW - timedelta(days=365),
        first_seen_at=NOW - timedelta(days=30),
        revision=2,
        revision_known_at=revision_known_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _policy_evidence(*, first_seen_at: datetime = NOW - timedelta(days=1)) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=EVIDENCE_ID,
        source_id="national_policy_test",
        source_url="https://example.invalid/policy/1",
        title="Synthetic industrial policy",
        published_at=NOW - timedelta(days=2),
        first_seen_at=first_seen_at,
        content_sha256=CONTENT_HASH,
        license_id="synthetic-test-license",
        rights_snapshot_hash=RIGHTS_HASH,
        content_tier=ing.ContentTier.OFFICIAL_POLICY,
        fact_eligibility=ing.FactEligibility.POLICY_FACT,
    )


def _policy_target(*, evidence_ids: list[UUID] | None = None) -> pol.PolicyTarget:
    return pol.PolicyTarget(
        target=EntityRef(entity_type="industry", entity_id=str(INDUSTRY_ID)),
        target_kind=pol.PolicyTargetKind.INDUSTRY,
        explicit=True,
        evidence_ids=evidence_ids if evidence_ids is not None else [EVIDENCE_ID],
    )


def _policy_measure(
    *,
    evidence_ids: list[UUID] | None = None,
    targets: list[pol.PolicyTarget] | None = None,
) -> pol.PolicyMeasure:
    return pol.PolicyMeasure(
        measure_id=_uuid(40),
        measure_type="subsidy.support",
        summary="Synthetic support measure",
        direction=Direction.POSITIVE,
        impact_horizon=ImpactHorizon.LONG_TERM,
        targets=targets if targets is not None else [_policy_target()],
        effective_from=NOW + timedelta(days=1),
        effective_until=NOW + timedelta(days=365),
        evidence_ids=evidence_ids if evidence_ids is not None else [EVIDENCE_ID],
    )


def _policy_document(
    *,
    first_seen_at: datetime = NOW - timedelta(days=1),
    revision_known_at: datetime = NOW - timedelta(hours=12),
    revised_at: datetime | None = None,
    measures: list[pol.PolicyMeasure] | None = None,
    evidence: list[EvidenceRef] | None = None,
) -> pol.PolicyDocument:
    return pol.PolicyDocument(
        schema_version="1.0",
        policy_id=_uuid(41),
        document_number="SYNTHETIC-2026-1",
        title="Synthetic industrial development policy",
        authority=pol.PolicyAuthority(
            authority_id=_uuid(42),
            name="Synthetic national authority",
            level=pol.PolicyAuthorityLevel.NATIONAL,
        ),
        status=pol.PolicyStatus.PUBLISHED,
        summary="Synthetic policy metadata without unrestricted full text.",
        published_at=NOW - timedelta(days=2),
        effective_from=NOW + timedelta(days=1),
        effective_until=NOW + timedelta(days=365),
        first_seen_at=first_seen_at,
        revised_at=revised_at,
        revision=1 if revised_at is None else 2,
        revision_known_at=revision_known_at,
        topics=["synthetic_industry"],
        measures=measures if measures is not None else [_policy_measure()],
        evidence=evidence if evidence is not None else [_policy_evidence()],
        extractor_version="policy-extractor-1.0",
        rule_version="policy-rules-1.0",
    )


def _position(
    *,
    instrument: InstrumentRef = INSTRUMENT,
    quantity: str = "1000.125",
    available_quantity: str = "800.125",
    cost_currency: str = "CNY",
    current_currency: str = "CNY",
    opened_at: datetime | None = NOW - timedelta(days=30),
    updated_at: datetime = NOW - timedelta(minutes=1),
    tags: list[str] | None = None,
) -> pf.PortfolioPosition:
    return pf.PortfolioPosition(
        position_id=_uuid(50 if instrument == INSTRUMENT else 51),
        instrument=instrument,
        side=pf.PositionSide.LONG,
        quantity=quantity,
        available_quantity=available_quantity,
        average_cost=MoneyValue(amount="9.8765", currency=cost_currency),
        current_price=MoneyValue(amount="10.125", currency=current_currency),
        opened_at=opened_at,
        updated_at=updated_at,
        tags=tags or ["core"],
    )


def _portfolio_snapshot(
    *,
    positions: list[pf.PortfolioPosition] | None = None,
    cash_balances: list[MoneyValue] | None = None,
    as_of: datetime = NOW,
) -> pf.PortfolioSnapshot:
    return pf.PortfolioSnapshot(
        schema_version="1.0",
        portfolio_id=_uuid(52),
        name="Synthetic paper portfolio",
        as_of=as_of,
        positions=positions if positions is not None else [_position()],
        cash_balances=(
            cash_balances
            if cash_balances is not None
            else [MoneyValue(amount="12345.67", currency="CNY")]
        ),
        revision=3,
        source="synthetic-test",
    )


def _trading_segment(
    *,
    phase: sec.TradingPhase = sec.TradingPhase.CONTINUOUS,
    starts_at: datetime = NOW,
    ends_at: datetime = NOW + timedelta(hours=2),
) -> sec.TradingSessionSegment:
    return sec.TradingSessionSegment(phase=phase, starts_at=starts_at, ends_at=ends_at)


def _trading_day(
    *,
    status: sec.TradingDayStatus = sec.TradingDayStatus.OPEN,
    segments: list[sec.TradingSessionSegment] | None = None,
    first_seen_at: datetime = NOW - timedelta(days=30),
    revised_at: datetime | None = None,
) -> sec.TradingDay:
    return sec.TradingDay(
        schema_version="1.0",
        exchange="SSE",
        trading_date=date(2026, 7, 22),
        status=status,
        segments=segments if segments is not None else [_trading_segment()],
        first_seen_at=first_seen_at,
        revised_at=revised_at,
        calendar_version="sse-calendar-2026.07",
    )


def _market_quote(
    *,
    instrument: InstrumentRef = INSTRUMENT,
    quoted_at: datetime = NOW - timedelta(seconds=5),
    received_at: datetime = NOW - timedelta(seconds=4),
    last_price: MoneyValue | None = None,
    previous_close: MoneyValue | None = None,
    high_price: MoneyValue | None = None,
    low_price: MoneyValue | None = None,
    turnover: MoneyValue | None = None,
) -> sec.MarketQuote:
    return sec.MarketQuote(
        schema_version="1.0",
        quote_id=_uuid(60 if instrument == INSTRUMENT else 61),
        instrument=instrument,
        quoted_at=quoted_at,
        received_at=received_at,
        status=sec.QuoteStatus.NORMAL,
        last_price=last_price or MoneyValue(amount="10.125", currency=instrument.currency),
        previous_close=(previous_close or MoneyValue(amount="9.875", currency=instrument.currency)),
        high_price=high_price or MoneyValue(amount="10.25", currency=instrument.currency),
        low_price=low_price or MoneyValue(amount="9.75", currency=instrument.currency),
        volume="123456.75",
        turnover=turnover or MoneyValue(amount="1249999.99", currency=instrument.currency),
        change_rate="0.025316",
        source_id="market-data-test",
        evidence_ids=[EVIDENCE_ID],
    )


def _sector_constituent(
    *,
    instrument: InstrumentRef = INSTRUMENT,
    first_seen_at: datetime = NOW - timedelta(days=2),
) -> sec.SectorConstituent:
    return sec.SectorConstituent(
        instrument=instrument,
        weight="0.125",
        valid_from=NOW - timedelta(days=30),
        first_seen_at=first_seen_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _constituent_snapshot(
    *,
    sector_id: UUID = SECTOR_ID,
    as_of: datetime = NOW,
    revision_known_at: datetime = NOW - timedelta(minutes=1),
) -> sec.SectorConstituentSnapshotRef:
    return sec.SectorConstituentSnapshotRef(
        snapshot_id=_uuid(62),
        sector_id=sector_id,
        as_of=as_of,
        revision_known_at=revision_known_at,
        constituent_count=1,
        content_hash=CONTENT_HASH,
    )


def _sector_definition(
    *,
    sector_id: UUID = SECTOR_ID,
    revision_known_at: datetime = NOW - timedelta(days=1),
) -> sec.SectorDefinition:
    return sec.SectorDefinition(
        schema_version="1.0",
        sector_id=sector_id,
        code="BANKS",
        name="Synthetic banking sector",
        kind=sec.SectorKind.INDUSTRY,
        taxonomy="synthetic-v1",
        valid_from=NOW - timedelta(days=365),
        first_seen_at=NOW - timedelta(days=30),
        revision=2,
        revision_known_at=revision_known_at,
        evidence_ids=[EVIDENCE_ID],
    )


def _heat_metric(
    *,
    name: sec.HeatMetricName = sec.HeatMetricName.TURNOVER,
    weight: float = 1.0,
    observed_at: datetime = NOW - timedelta(minutes=1),
) -> sec.HeatMetric:
    return sec.HeatMetric(
        name=name,
        raw_value="1234567.89",
        unit="CNY",
        normalized_score=0.4,
        weight=weight,
        observed_at=observed_at,
    )


def _heat_snapshot(
    *,
    snapshot_id: UUID = HEAT_SNAPSHOT_ID,
    sector: sec.SectorDefinition | None = None,
    constituent_snapshot: sec.SectorConstituentSnapshotRef | None = None,
    as_of: datetime = NOW,
    metrics: list[sec.HeatMetric] | None = None,
    data_watermark: datetime = NOW - timedelta(seconds=30),
) -> sec.SectorHeatSnapshot:
    selected_sector = sector or _sector_definition()
    return sec.SectorHeatSnapshot(
        schema_version="1.0",
        snapshot_id=snapshot_id,
        sector=selected_sector,
        constituent_snapshot=(
            constituent_snapshot
            if constituent_snapshot is not None
            else _constituent_snapshot(sector_id=selected_sector.sector_id, as_of=as_of)
        ),
        as_of=as_of,
        lookback_minutes=10,
        heat_score=0.4,
        direction=sec.HeatDirection.HEATING,
        rank=1,
        metrics=metrics if metrics is not None else [_heat_metric()],
        leading_instruments=[INSTRUMENT],
        evidence_ids=[EVIDENCE_ID],
        calculator_version="sector-heat-1.0",
        data_watermark=data_watermark,
    )


def _visit_state(
    *,
    sector_id: UUID = SECTOR_ID,
    revision_known_at: datetime = NOW,
) -> sec.SectorVisitState:
    return sec.SectorVisitState(
        sector_id=sector_id,
        active_started_at=NOW - timedelta(minutes=5),
        last_completed_at=NOW - timedelta(minutes=20),
        consecutive_failures=0,
        completed_visits=3,
        state_revision=4,
        revision_known_at=revision_known_at,
    )


def _rotation_policy() -> sec.RotationPolicy:
    return sec.RotationPolicy(
        policy_id="fair_rotation",
        version="1.0",
        interval_seconds=600,
        maximum_starvation_seconds=3600,
        heat_weight=0.4,
        fairness_weight=0.6,
        maximum_slots=20,
    )


def _rotation_request(
    *,
    context: OperationContext | None = None,
    trading_day: sec.TradingDay | None = None,
    candidates: list[sec.SectorHeatSnapshot] | None = None,
    visits: list[sec.SectorVisitState] | None = None,
    schedule_from: datetime = NOW,
) -> sec.SectorRotationRequest:
    return sec.SectorRotationRequest(
        schema_version="1.0",
        context=context or _operation_context(),
        trading_day=trading_day or _trading_day(),
        candidates=candidates if candidates is not None else [_heat_snapshot()],
        visits=visits if visits is not None else [_visit_state()],
        policy=_rotation_policy(),
        schedule_from=schedule_from,
    )


def _rotation_slot(*, sector_id: UUID, offset_minutes: int) -> sec.SectorRotationSlot:
    return sec.SectorRotationSlot(
        sector_id=sector_id,
        scheduled_at=NOW + timedelta(minutes=offset_minutes),
        priority_score=0.7,
        fairness_score=0.6,
        heat_score=0.4,
        reason_code="FAIR_ROTATION",
    )


# Ingestion contracts


@pytest.mark.parametrize(
    ("content_tier", "eligibility", "compatible"),
    [
        (ing.ContentTier.SOCIAL_AGGREGATE, ing.FactEligibility.SENTIMENT_ONLY, True),
        (ing.ContentTier.SOCIAL_AGGREGATE, ing.FactEligibility.COMPANY_FACT, False),
        (ing.ContentTier.DEVELOPMENT_ONLY, ing.FactEligibility.INELIGIBLE, True),
        (ing.ContentTier.DEVELOPMENT_ONLY, ing.FactEligibility.CONTEXT_ONLY, False),
        (ing.ContentTier.STATUTORY_DISCLOSURE, ing.FactEligibility.COMPANY_FACT, True),
        (ing.ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA, ing.FactEligibility.COMPANY_FACT, False),
        (ing.ContentTier.OFFICIAL_POLICY, ing.FactEligibility.POLICY_FACT, True),
        (ing.ContentTier.REGULATOR, ing.FactEligibility.POLICY_FACT, False),
        (ing.ContentTier.MARKET_DATA, ing.FactEligibility.MARKET_OBSERVATION, True),
        (ing.ContentTier.ISSUER_OFFICIAL, ing.FactEligibility.MARKET_OBSERVATION, False),
        (ing.ContentTier.REGULATOR, ing.FactEligibility.SENTIMENT_ONLY, False),
        (ing.ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA, ing.FactEligibility.CONTEXT_ONLY, True),
    ],
)
def test_fact_eligibility_matrix_is_fail_closed(
    content_tier: ing.ContentTier,
    eligibility: ing.FactEligibility,
    compatible: bool,
) -> None:
    assert ing.is_fact_eligibility_compatible(content_tier, eligibility) is compatible


def test_direct_source_tier_mapping_keeps_aggregator_record_specific() -> None:
    assert (
        ing.direct_source_content_tier(ing.SourceTier.STATUTORY_DISCLOSURE)
        is ing.ContentTier.STATUTORY_DISCLOSURE
    )
    assert ing.direct_source_content_tier(ing.SourceTier.LICENSED_AGGREGATOR) is None


def test_source_capability_and_descriptor_accept_valid_authority_metadata() -> None:
    descriptor = _source_descriptor()

    assert descriptor.capabilities[0].fact_eligibility is ing.FactEligibility.COMPANY_FACT
    assert descriptor.authority == pytest.approx(0.98)


def test_source_capability_rejects_incompatible_fact_use() -> None:
    with pytest.raises(ValidationError, match="incompatible with the content tier"):
        _source_capability(
            content_tier=ing.ContentTier.SOCIAL_AGGREGATE,
            fact_eligibility=ing.FactEligibility.COMPANY_FACT,
        )


def test_source_descriptor_rejects_duplicate_or_tier_changed_capabilities() -> None:
    capability = _source_capability()
    with pytest.raises(ValidationError, match="capabilities must be unique"):
        _source_descriptor(capabilities=[capability, capability])

    with pytest.raises(ValidationError, match="must retain the source tier"):
        _source_descriptor(
            capabilities=[
                _source_capability(
                    content_tier=ing.ContentTier.REGULATOR,
                    fact_eligibility=ing.FactEligibility.COMPANY_FACT,
                )
            ]
        )


def test_aggregator_descriptor_may_advertise_record_specific_tiers() -> None:
    descriptor = _source_descriptor(
        source_tier=ing.SourceTier.LICENSED_AGGREGATOR,
        capabilities=[
            _source_capability(
                content_tier=ing.ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA,
                fact_eligibility=ing.FactEligibility.CONTEXT_ONLY,
            )
        ],
    )

    assert descriptor.capabilities[0].content_tier is ing.ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA


def test_source_registry_results_keep_found_and_identity_semantics_consistent() -> None:
    source = _source_descriptor()
    lookup = ing.SourceRegistryLookup(
        schema_version="1.0",
        context=_operation_context(),
        source_id=source.source_id,
    )
    result = ing.SourceRegistryLookupResult(
        schema_version="1.0",
        source=source,
        found=True,
        as_of=NOW,
    )
    query = ing.SourceRegistryQuery(
        schema_version="1.0",
        context=_operation_context(),
        source_tiers=[ing.SourceTier.STATUTORY_DISCLOSURE],
        capabilities=["exchange.announcements"],
    )
    registry = ing.SourceRegistryResult(
        schema_version="1.0",
        sources=[source],
        as_of=NOW,
    )

    assert result.source is not None
    assert lookup.source_id == result.source.source_id
    assert query.include_disabled is False
    assert registry.sources == [source]

    with pytest.raises(ValidationError, match="found must reflect"):
        ing.SourceRegistryLookupResult(
            schema_version="1.0",
            source=source,
            found=False,
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="duplicate IDs"):
        ing.SourceRegistryResult(
            schema_version="1.0",
            sources=[source, source],
            as_of=NOW,
        )


def test_fetch_request_preserves_opaque_cursor_and_namespaced_options() -> None:
    request = ing.FetchRequest(
        schema_version="1.0",
        context=_operation_context(),
        source_id="exchange_test",
        capability="exchange.announcements",
        rights_snapshot_hash=RIGHTS_HASH,
        cursor="opaque:adapter-owned:value",
        since=NOW - timedelta(days=1),
        until=NOW,
        instrument_ids=[INSTRUMENT.instrument_id],
        limit=1000,
        options={"language": "zh-CN"},
        options_namespace="stock_assistant.ingestion",
        options_owner="ingestion_team",
    )

    assert request.cursor == "opaque:adapter-owned:value"
    assert request.limit == 1000
    assert request.until == request.context.as_of


@pytest.mark.parametrize(
    ("since", "until", "message"),
    [
        (NOW, NOW - timedelta(seconds=1), "until cannot be earlier"),
        (NOW - timedelta(days=1), NOW + timedelta(seconds=1), "context.as_of"),
    ],
)
def test_fetch_request_rejects_invalid_point_in_time_ranges(
    since: datetime,
    until: datetime,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ing.FetchRequest(
            schema_version="1.0",
            context=_operation_context(),
            source_id="exchange_test",
            capability="exchange.announcements",
            rights_snapshot_hash=RIGHTS_HASH,
            since=since,
            until=until,
        )


@pytest.mark.parametrize(
    ("options", "namespace", "owner"),
    [
        ({"language": "zh-CN"}, None, None),
        ({"language": "zh-CN"}, "stock_assistant.ingestion", None),
        ({}, "stock_assistant.ingestion", "ingestion_team"),
    ],
)
def test_fetch_request_rejects_partially_declared_extension_ownership(
    options: dict[str, object],
    namespace: str | None,
    owner: str | None,
) -> None:
    with pytest.raises(ValidationError, match="required exactly when values exist"):
        ing.FetchRequest(
            schema_version="1.0",
            context=_operation_context(),
            source_id="exchange_test",
            capability="exchange.announcements",
            rights_snapshot_hash=RIGHTS_HASH,
            options=options,
            options_namespace=namespace,
            options_owner=owner,
        )


def test_fetch_request_rejects_oversized_extensions() -> None:
    common = {
        "schema_version": "1.0",
        "context": _operation_context(),
        "source_id": "exchange_test",
        "capability": "exchange.announcements",
        "rights_snapshot_hash": RIGHTS_HASH,
        "options_namespace": "stock_assistant.ingestion",
        "options_owner": "ingestion_team",
    }
    with pytest.raises(ValidationError, match=r"at most 128 items|more than 128 keys"):
        ing.FetchRequest(**common, options={f"key_{index}": index for index in range(129)})
    with pytest.raises(ValidationError, match="byte limit"):
        ing.FetchRequest(**common, options={"oversized": "x" * (1024 * 1024)})


def test_source_record_accepts_namespaced_payload_and_preserves_rights_snapshot() -> None:
    record = _source_record(
        payload={"announcement_type": "annual_report"},
        payload_namespace="stock_assistant.exchange",
        payload_owner="ingestion_team",
    )

    assert record.payload["announcement_type"] == "annual_report"
    assert record.rights_snapshot_hash == RIGHTS_HASH


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"fetched_at": NOW - timedelta(minutes=3)}, "fetched_at cannot be earlier"),
        (
            {
                "content_tier": ing.ContentTier.REGULATOR,
                "fact_eligibility": ing.FactEligibility.COMPANY_FACT,
            },
            "must retain the source tier",
        ),
        (
            {
                "source_tier": ing.SourceTier.LICENSED_AGGREGATOR,
                "content_tier": ing.ContentTier.SOCIAL_AGGREGATE,
                "fact_eligibility": ing.FactEligibility.COMPANY_FACT,
            },
            "incompatible with the content tier",
        ),
        (
            {
                "payload": {"field": "value"},
                "payload_namespace": None,
                "payload_owner": None,
            },
            "required exactly when values exist",
        ),
    ],
)
def test_source_record_rejects_timeline_tier_and_payload_violations(
    updates: dict[str, object],
    message: str,
) -> None:
    values = _source_record().model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        ing.SourceRecord.model_validate(values)


def test_fetch_batch_is_retry_safe_and_rejects_cross_source_or_rights_mixups() -> None:
    batch = _fetch_batch()

    assert batch.next_cursor == "opaque-source-cursor"
    assert batch.exhausted is False
    assert batch.records[0].rights_snapshot_hash == batch.rights_snapshot_hash

    with pytest.raises(ValidationError, match="completed_at cannot be earlier"):
        _fetch_batch(completed_at=NOW - timedelta(minutes=2))
    with pytest.raises(ValidationError, match="match the batch source_id"):
        _fetch_batch(records=[_source_record(source_id="other_source")])
    with pytest.raises(ValidationError, match="match the batch rights snapshot"):
        _fetch_batch(records=[_source_record(rights_snapshot_hash=OTHER_RIGHTS_HASH)])


def test_rights_contracts_construct_explicit_fail_closed_decisions() -> None:
    check = ing.RightsCheck(
        context=_operation_context(),
        source_id="exchange_test",
        operation=ing.RightsOperation.EMBED,
        rights_snapshot_hash=RIGHTS_HASH,
        content_category="announcement_metadata",
    )
    decision = ing.RightsDecision(
        allowed=False,
        reason_code="EMBED_NOT_LICENSED",
        decided_at=NOW,
        rights_snapshot_hash=RIGHTS_HASH,
    )

    assert check.operation is ing.RightsOperation.EMBED
    assert decision.allowed is False
    assert decision.retention_until is None


# Instrument and issuer master-data contracts


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: _industry_membership(valid_until=NOW - timedelta(days=365)),
            "valid_until must be later",
        ),
        (
            lambda: _region_assignment(valid_until=NOW - timedelta(days=365)),
            "valid_until must be later",
        ),
        (
            lambda: _business_line(valid_until=NOW - timedelta(days=365)),
            "valid_until must be later",
        ),
    ],
)
def test_nested_master_data_periods_reject_non_positive_validity(
    factory: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        factory()


def test_business_line_uses_exact_decimal_strings() -> None:
    line = _business_line()

    assert line.revenue_share == Decimal("0.625")
    assert '"revenue_share":"0.625"' in line.model_dump_json()

    values = line.model_dump()
    values["revenue_share"] = 0.625
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        ins.BusinessLine.model_validate(values)


@pytest.mark.parametrize("node_factory", [_industry_node, _region_node])
def test_master_data_nodes_accept_stable_historical_identity(
    node_factory: Callable[[], ins.IndustryNode | ins.RegionNode],
) -> None:
    node = node_factory()

    assert node.first_seen_at < NOW
    assert node.evidence_ids == [EVIDENCE_ID]


def test_industry_and_region_nodes_reject_self_parenting_and_invalid_periods() -> None:
    industry_values = _industry_node().model_dump()
    industry_values["parent_industry_id"] = INDUSTRY_ID
    with pytest.raises(ValidationError, match="own parent"):
        ins.IndustryNode.model_validate(industry_values)
    industry_values["parent_industry_id"] = None
    industry_values["valid_until"] = industry_values["valid_from"]
    with pytest.raises(ValidationError, match="valid_until must be later"):
        ins.IndustryNode.model_validate(industry_values)

    region_values = _region_node().model_dump()
    region_values["parent_region_id"] = REGION_ID
    with pytest.raises(ValidationError, match="own parent"):
        ins.RegionNode.model_validate(region_values)
    region_values["parent_region_id"] = None
    region_values["valid_until"] = region_values["valid_from"]
    with pytest.raises(ValidationError, match="valid_until must be later"):
        ins.RegionNode.model_validate(region_values)


def test_company_profile_accepts_only_facts_known_by_its_revision() -> None:
    company = _company_profile()

    assert company.revision == 2
    assert all(line.first_seen_at <= company.revision_known_at for line in company.business_lines)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"valid_until": NOW - timedelta(days=365)}, "valid_until must be later"),
        (
            {"revision_known_at": NOW - timedelta(days=31)},
            "revision_known_at cannot be earlier",
        ),
        (
            {"business_lines": [_business_line(), _business_line()]},
            "business lines must be unique",
        ),
        (
            {"business_lines": [_business_line(first_seen_at=NOW - timedelta(days=9))]},
            "cannot contain facts learned later",
        ),
        (
            {"industries": [_industry_membership(first_seen_at=NOW - timedelta(days=9))]},
            "cannot contain facts learned later",
        ),
        (
            {"regions": [_region_assignment(first_seen_at=NOW - timedelta(days=9))]},
            "cannot contain facts learned later",
        ),
    ],
)
def test_company_profile_rejects_invalid_revision_contents(
    updates: dict[str, object],
    message: str,
) -> None:
    values = _company_profile().model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        ins.CompanyProfile.model_validate(values)


def test_instrument_profile_supports_non_equity_without_synthetic_company() -> None:
    equity = _instrument_profile()
    etf = _instrument_profile(
        instrument=OTHER_INSTRUMENT,
        instrument_type=ins.InstrumentType.ETF,
        company_id=None,
    )

    assert equity.lot_size == Decimal("100")
    assert etf.company_id is None

    delisted_values = equity.model_dump()
    delisted_values.update(
        {
            "listing_status": ins.ListingStatus.DELISTED,
            "delisted_at": NOW - timedelta(days=1),
        }
    )
    delisted = ins.InstrumentProfile.model_validate(delisted_values)
    assert delisted.delisted_at is not None
    assert delisted.listed_at is not None
    assert delisted.delisted_at > delisted.listed_at


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"company_id": None, "issuer": None}, "equity instruments require"),
        (
            {
                "listed_at": NOW - timedelta(days=10),
                "delisted_at": NOW - timedelta(days=10),
            },
            "delisted_at must be later",
        ),
        ({"valid_until": NOW - timedelta(days=365)}, "valid_until must be later"),
        (
            {"revision_known_at": NOW - timedelta(days=31)},
            "revision_known_at cannot be earlier",
        ),
    ],
)
def test_instrument_profile_rejects_invalid_identity_or_timelines(
    updates: dict[str, object],
    message: str,
) -> None:
    values = _instrument_profile().model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        ins.InstrumentProfile.model_validate(values)


def test_instrument_lookup_accepts_exactly_one_lookup_shape() -> None:
    by_id = ins.InstrumentLookupRequest(
        schema_version="1.0",
        context=_operation_context(),
        instrument_id=INSTRUMENT.instrument_id,
    )
    by_symbol = ins.InstrumentLookupRequest(
        schema_version="1.0",
        context=_operation_context(),
        symbol=INSTRUMENT.symbol,
        exchange=INSTRUMENT.exchange,
    )

    assert by_id.instrument_id == INSTRUMENT.instrument_id
    assert (by_symbol.symbol, by_symbol.exchange) == ("600000", "SSE")

    for values in (
        {},
        {"instrument_id": INSTRUMENT.instrument_id, "symbol": "600000", "exchange": "SSE"},
        {"symbol": "600000"},
        {"exchange": "SSE"},
    ):
        with pytest.raises(ValidationError, match=r"either instrument_id|provided together"):
            ins.InstrumentLookupRequest(
                schema_version="1.0",
                context=_operation_context(),
                **values,
            )


def test_instrument_lookup_status_and_pit_must_match_results() -> None:
    profile = _instrument_profile()
    resolved = ins.InstrumentLookupResult(
        schema_version="1.0",
        status=ins.ResolutionStatus.RESOLVED,
        matches=[profile],
        resolved_at=NOW,
        as_of=NOW,
    )

    assert resolved.matches == [profile]

    invalid_cases = [
        (ins.ResolutionStatus.RESOLVED, [], "exactly one match"),
        (ins.ResolutionStatus.NOT_FOUND, [profile], "cannot contain matches"),
        (ins.ResolutionStatus.AMBIGUOUS, [profile], "at least two matches"),
    ]
    for status, matches, message in invalid_cases:
        with pytest.raises(ValidationError, match=message):
            ins.InstrumentLookupResult(
                schema_version="1.0",
                status=status,
                matches=matches,
                resolved_at=NOW,
                as_of=NOW,
            )

    future_profile = _instrument_profile(revision_known_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="known after as_of"):
        ins.InstrumentLookupResult(
            schema_version="1.0",
            status=ins.ResolutionStatus.RESOLVED,
            matches=[future_profile],
            resolved_at=NOW,
            as_of=NOW,
        )


def test_master_data_query_and_snapshot_use_one_opaque_union_cursor() -> None:
    query = ins.MasterDataQuery(
        schema_version="1.0",
        context=_operation_context(),
        entity_kinds=list(ins.EntityKind),
        cursor="opaque-union-cursor",
        limit=1000,
    )
    snapshot = ins.MasterDataSnapshot(
        schema_version="1.0",
        as_of=NOW,
        instruments=[_instrument_profile()],
        companies=[_company_profile()],
        industries=[_industry_node()],
        regions=[_region_node()],
        next_cursor="opaque-next-union-cursor",
        has_more=True,
    )

    assert query.cursor == "opaque-union-cursor"
    assert snapshot.next_cursor == "opaque-next-union-cursor"
    assert snapshot.has_more is True

    with pytest.raises(ValidationError, match="next_cursor is required"):
        ins.MasterDataSnapshot(schema_version="1.0", as_of=NOW, has_more=True)


@pytest.mark.parametrize(
    "field",
    ["instruments", "companies", "industries", "regions"],
)
def test_master_data_snapshot_rejects_entities_unknown_at_cutoff(field: str) -> None:
    future = NOW + timedelta(seconds=1)
    items: dict[str, list[object]] = {
        "instruments": [_instrument_profile(revision_known_at=future)],
        "companies": [_company_profile(revision_known_at=future)],
        "industries": [_industry_node(first_seen_at=future)],
        "regions": [_region_node(first_seen_at=future)],
    }
    with pytest.raises(ValidationError, match="known after as_of"):
        ins.MasterDataSnapshot(schema_version="1.0", as_of=NOW, **{field: items[field]})


@pytest.mark.parametrize(
    ("business_lines", "industries", "regions"),
    [
        ([_business_line(first_seen_at=NOW + timedelta(seconds=1))], [], []),
        ([], [_industry_membership(first_seen_at=NOW + timedelta(seconds=1))], []),
        ([], [], [_region_assignment(first_seen_at=NOW + timedelta(seconds=1))]),
    ],
)
def test_master_data_snapshot_revalidates_nested_company_facts_at_cutoff(
    business_lines: list[ins.BusinessLine],
    industries: list[ins.IndustryMembership],
    regions: list[ins.RegionAssignment],
) -> None:
    company = _company_profile(
        revision_known_at=NOW + timedelta(minutes=1),
        business_lines=business_lines,
        industries=industries,
        regions=regions,
    )
    company_at_cutoff = company.model_copy(update={"revision_known_at": NOW})
    with pytest.raises(
        ValidationError, match="company revision cannot contain facts learned later"
    ):
        ins.MasterDataSnapshot(
            schema_version="1.0",
            as_of=NOW,
            companies=[company_at_cutoff],
        )


# Policy contracts


def test_policy_measure_and_document_keep_official_evidence_traceability() -> None:
    document = _policy_document()

    assert document.measures[0].evidence_ids == [document.evidence[0].evidence_id]
    assert document.evidence[0].fact_eligibility is ing.FactEligibility.POLICY_FACT
    assert document.revision_known_at <= NOW


def test_policy_measure_rejects_non_positive_effective_window() -> None:
    open_ended_values = _policy_measure().model_dump()
    open_ended_values["effective_until"] = None
    open_ended = pol.PolicyMeasure.model_validate(open_ended_values)
    assert open_ended.effective_from is not None
    assert open_ended.effective_until is None

    values = _policy_measure().model_dump()
    values["effective_until"] = values["effective_from"]
    with pytest.raises(ValidationError, match="effective_until must be later"):
        pol.PolicyMeasure.model_validate(values)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"first_seen_at": NOW - timedelta(days=3)}, "first_seen_at cannot be earlier"),
        (
            {
                "effective_from": NOW + timedelta(days=2),
                "effective_until": NOW + timedelta(days=1),
            },
            "effective_until must be later",
        ),
        ({"revised_at": NOW - timedelta(days=3)}, "revised_at cannot be earlier"),
        (
            {"revision_known_at": NOW - timedelta(days=1, seconds=1)},
            "revision_known_at cannot be earlier than first_seen_at",
        ),
        (
            {
                "revised_at": NOW - timedelta(hours=6),
                "revision_known_at": NOW - timedelta(hours=12),
            },
            "revision_known_at cannot be earlier than revised_at",
        ),
        (
            {"evidence": [_policy_evidence(first_seen_at=NOW - timedelta(hours=6))]},
            "cannot use evidence learned later",
        ),
        (
            {"measures": [_policy_measure(evidence_ids=[OTHER_EVIDENCE_ID])]},
            "reference unattached evidence",
        ),
        (
            {
                "measures": [
                    _policy_measure(targets=[_policy_target(evidence_ids=[OTHER_EVIDENCE_ID])])
                ]
            },
            "reference unattached evidence",
        ),
    ],
)
def test_policy_document_rejects_temporal_or_unattached_evidence(
    updates: dict[str, object],
    message: str,
) -> None:
    values = _policy_document().model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        pol.PolicyDocument.model_validate(values)


def test_policy_document_accepts_an_open_ended_effective_period() -> None:
    values = _policy_document().model_dump()
    values["effective_until"] = None

    document = pol.PolicyDocument.model_validate(values)

    assert document.effective_from is not None
    assert document.effective_until is None


def test_policy_query_enforces_point_in_time_range_and_opaque_cursor() -> None:
    query = pol.PolicyQuery(
        schema_version="1.0",
        context=_operation_context(),
        authority_levels=[pol.PolicyAuthorityLevel.NATIONAL],
        industry_ids=[INDUSTRY_ID],
        published_since=NOW - timedelta(days=30),
        published_until=NOW,
        cursor="opaque-policy-cursor",
        limit=1000,
    )

    assert query.cursor == "opaque-policy-cursor"
    assert query.published_until == query.context.as_of

    with pytest.raises(ValidationError, match="published_until cannot be earlier"):
        pol.PolicyQuery(
            schema_version="1.0",
            context=_operation_context(),
            published_since=NOW,
            published_until=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match=r"context\.as_of"):
        pol.PolicyQuery(
            schema_version="1.0",
            context=_operation_context(),
            published_until=NOW + timedelta(seconds=1),
        )


def test_policy_search_result_rejects_future_revisions_and_cursorless_continuation() -> None:
    result = pol.PolicySearchResult(
        schema_version="1.0",
        policies=[_policy_document()],
        as_of=NOW,
        next_cursor="opaque-next-policy-cursor",
        has_more=True,
    )

    assert result.next_cursor == "opaque-next-policy-cursor"

    with pytest.raises(ValidationError, match="known after as_of"):
        pol.PolicySearchResult(
            schema_version="1.0",
            policies=[_policy_document(revision_known_at=NOW + timedelta(seconds=1))],
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="next_cursor is required"):
        pol.PolicySearchResult(schema_version="1.0", as_of=NOW, has_more=True)


def test_policy_match_contracts_construct_weighted_explanations() -> None:
    exposure = pol.PolicyExposureTarget(
        target=EntityRef(entity_type="industry", entity_id=str(INDUSTRY_ID)),
        target_kind=pol.PolicyTargetKind.INDUSTRY,
        weight=0.75,
        related_instrument_ids=[INSTRUMENT.instrument_id],
    )
    request = pol.PolicyMatchRequest(
        schema_version="1.0",
        context=_operation_context(),
        targets=[exposure],
        policy_ids=[_uuid(41)],
        minimum_relevance=0.5,
    )
    match = pol.PolicyMatch(
        policy_id=_uuid(41),
        target=exposure,
        matched_measure_ids=[_uuid(40)],
        direction=Direction.POSITIVE,
        impact_horizon=ImpactHorizon.LONG_TERM,
        relevance=0.8,
        confidence=0.7,
        rationale="The policy explicitly targets the held industry.",
        evidence_ids=[EVIDENCE_ID],
    )
    result = pol.PolicyMatchResult(
        schema_version="1.0",
        matches=[match],
        as_of=NOW,
        matcher_version="matcher-1.0",
        rule_version="rules-1.0",
    )

    assert request.targets[0].weight == pytest.approx(0.75)
    assert result.matches[0].evidence_ids == [EVIDENCE_ID]


# Portfolio contracts


def test_portfolio_position_preserves_exact_quantities_and_money_strings() -> None:
    position = _position()

    assert position.quantity == Decimal("1000.125")
    assert position.average_cost.amount == Decimal("9.8765")
    dumped = position.model_dump_json()
    assert '"quantity":"1000.125"' in dumped
    assert '"amount":"9.8765"' in dumped

    values = position.model_dump()
    values["quantity"] = 1000.125
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        pf.PortfolioPosition.model_validate(values)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"available_quantity": "1000.126"}, "cannot exceed quantity"),
        (
            {"average_cost": MoneyValue(amount="9.87", currency="USD")},
            "instrument currency",
        ),
        (
            {"current_price": MoneyValue(amount="10.12", currency="USD")},
            "instrument currency",
        ),
        ({"updated_at": NOW - timedelta(days=31)}, "earlier than opened_at"),
        ({"tags": ["core", "core"]}, "tags must be unique"),
    ],
)
def test_portfolio_position_rejects_inconsistent_values(
    updates: dict[str, object],
    message: str,
) -> None:
    values = _position().model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        pf.PortfolioPosition.model_validate(values)


def test_portfolio_snapshot_accepts_pit_state_and_typed_write_query() -> None:
    snapshot = _portfolio_snapshot()
    write = pf.PortfolioWriteRequest(
        context=_mutation_context(),
        snapshot=snapshot,
        expected_revision=2,
    )
    query = pf.PortfolioQuery(
        context=_operation_context(),
        portfolio_id=snapshot.portfolio_id,
        instrument_ids=[INSTRUMENT.instrument_id],
    )

    assert snapshot.positions[0].updated_at <= snapshot.as_of
    assert write.expected_revision == snapshot.revision - 1
    assert query.portfolio_id == snapshot.portfolio_id


def test_portfolio_snapshot_rejects_duplicates_and_future_position_state() -> None:
    with pytest.raises(ValidationError, match="unique instruments"):
        _portfolio_snapshot(positions=[_position(), _position()])
    with pytest.raises(ValidationError, match="unique currencies"):
        _portfolio_snapshot(
            cash_balances=[
                MoneyValue(amount="1", currency="CNY"),
                MoneyValue(amount="2", currency="CNY"),
            ]
        )
    with pytest.raises(ValidationError, match="later than snapshot as_of"):
        _portfolio_snapshot(positions=[_position(updated_at=NOW + timedelta(seconds=1))])


def test_watchlist_accepts_entries_but_rejects_duplicate_instruments() -> None:
    entry = pf.WatchlistEntry(
        instrument=INSTRUMENT,
        added_at=NOW - timedelta(days=1),
        note="Synthetic watch item",
        tags=["policy"],
    )
    watchlist = pf.Watchlist(
        watchlist_id=_uuid(53),
        name="Synthetic watchlist",
        entries=[entry],
        revision=1,
        updated_at=NOW,
    )

    assert watchlist.entries[0].alert_enabled is True

    with pytest.raises(ValidationError, match="unique instruments"):
        pf.Watchlist(
            watchlist_id=_uuid(53),
            name="Synthetic watchlist",
            entries=[entry, entry],
            revision=1,
            updated_at=NOW,
        )


# Calendar, quote, sector and rotation contracts


def test_trading_day_accepts_non_overlapping_segments() -> None:
    day = _trading_day(
        segments=[
            _trading_segment(
                phase=sec.TradingPhase.OPENING_AUCTION,
                starts_at=NOW,
                ends_at=NOW + timedelta(minutes=15),
            ),
            _trading_segment(
                starts_at=NOW + timedelta(minutes=15),
                ends_at=NOW + timedelta(hours=2),
            ),
        ]
    )

    assert day.status is sec.TradingDayStatus.OPEN
    assert len(day.segments) == 2


def test_trading_session_and_day_reject_invalid_timeline_shapes() -> None:
    with pytest.raises(ValidationError, match="ends_at must be later"):
        _trading_segment(ends_at=NOW)
    with pytest.raises(ValidationError, match="closed trading days"):
        _trading_day(status=sec.TradingDayStatus.CLOSED)
    with pytest.raises(ValidationError, match="cannot overlap"):
        _trading_day(
            segments=[
                _trading_segment(ends_at=NOW + timedelta(hours=2)),
                _trading_segment(
                    starts_at=NOW + timedelta(hours=1),
                    ends_at=NOW + timedelta(hours=3),
                ),
            ]
        )
    with pytest.raises(ValidationError, match="revised_at cannot be earlier"):
        _trading_day(
            first_seen_at=NOW - timedelta(days=1),
            revised_at=NOW - timedelta(days=2),
        )


def test_exchange_calendar_query_and_result_enforce_pit_identity() -> None:
    query = sec.ExchangeCalendarQuery(
        schema_version="1.0",
        context=_operation_context(),
        exchanges=["SSE", "SZSE"],
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 31),
    )
    result = sec.ExchangeCalendarResult(
        schema_version="1.0",
        days=[_trading_day()],
        as_of=NOW,
        calendar_version="cn-calendar-2026.07",
    )

    assert query.date_from <= query.date_to
    assert result.days[0].first_seen_at <= result.as_of

    with pytest.raises(ValidationError, match="date_to cannot be earlier"):
        sec.ExchangeCalendarQuery(
            schema_version="1.0",
            context=_operation_context(),
            exchanges=["SSE"],
            date_from=date(2026, 7, 31),
            date_to=date(2026, 7, 1),
        )
    with pytest.raises(ValidationError, match="first seen after as_of"):
        sec.ExchangeCalendarResult(
            schema_version="1.0",
            days=[_trading_day(first_seen_at=NOW + timedelta(seconds=1))],
            as_of=NOW,
            calendar_version="cn-calendar-2026.07",
        )
    with pytest.raises(ValidationError, match="revisions known after as_of"):
        sec.ExchangeCalendarResult(
            schema_version="1.0",
            days=[_trading_day(revised_at=NOW + timedelta(seconds=1))],
            as_of=NOW,
            calendar_version="cn-calendar-2026.07",
        )
    with pytest.raises(ValidationError, match="duplicate exchange dates"):
        sec.ExchangeCalendarResult(
            schema_version="1.0",
            days=[_trading_day(), _trading_day()],
            as_of=NOW,
            calendar_version="cn-calendar-2026.07",
        )


def test_market_quote_query_and_quote_preserve_exact_market_values() -> None:
    query = sec.MarketQuoteQuery(
        schema_version="1.0",
        context=_operation_context(),
        instruments=[INSTRUMENT],
        maximum_age_seconds=0,
        allow_delayed=False,
    )
    quote = _market_quote()

    assert query.maximum_age_seconds == 0
    assert quote.volume == Decimal("123456.75")
    assert '"volume":"123456.75"' in quote.model_dump_json()

    values = quote.model_dump()
    values["volume"] = 123456.75
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        sec.MarketQuote.model_validate(values)

    quote_without_range_values = quote.model_dump()
    quote_without_range_values.update({"high_price": None, "low_price": None})
    quote_without_range = sec.MarketQuote.model_validate(quote_without_range_values)
    assert quote_without_range.high_price is None
    assert quote_without_range.low_price is None


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"received_at": NOW - timedelta(seconds=6)}, "received_at cannot be earlier"),
        (
            {"previous_close": MoneyValue(amount="9.8", currency="USD")},
            "instrument currency",
        ),
        ({"last_price": MoneyValue(amount="0", currency="CNY")}, "prices must be positive"),
        ({"turnover": MoneyValue(amount="-0.01", currency="CNY")}, "cannot be negative"),
        (
            {
                "high_price": MoneyValue(amount="9", currency="CNY"),
                "low_price": MoneyValue(amount="10", currency="CNY"),
            },
            "high_price cannot be lower",
        ),
    ],
)
def test_market_quote_rejects_temporal_currency_and_price_inconsistency(
    updates: dict[str, object],
    message: str,
) -> None:
    values = _market_quote().model_dump()
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        sec.MarketQuote.model_validate(values)


def test_market_quote_batch_rejects_duplicates_and_future_visibility() -> None:
    quote = _market_quote()
    batch = sec.MarketQuoteBatch(
        schema_version="1.0",
        quotes=[quote],
        as_of=NOW,
        source_watermark=NOW,
    )

    assert batch.quotes == [quote]

    with pytest.raises(ValidationError, match="at most one quote"):
        sec.MarketQuoteBatch(schema_version="1.0", quotes=[quote, quote], as_of=NOW)
    with pytest.raises(ValidationError, match="received after as_of"):
        sec.MarketQuoteBatch(
            schema_version="1.0",
            quotes=[_market_quote(received_at=NOW + timedelta(seconds=1))],
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="watermark cannot be later"):
        sec.MarketQuoteBatch(
            schema_version="1.0",
            quotes=[quote],
            as_of=NOW,
            source_watermark=NOW + timedelta(seconds=1),
        )


def test_sector_constituent_uses_decimal_string_and_positive_validity() -> None:
    constituent = _sector_constituent()

    assert constituent.weight == Decimal("0.125")
    assert '"weight":"0.125"' in constituent.model_dump_json()

    values = constituent.model_dump()
    values["weight"] = 0.125
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        sec.SectorConstituent.model_validate(values)
    values = constituent.model_dump()
    values["valid_until"] = values["valid_from"]
    with pytest.raises(ValidationError, match="valid_until must be later"):
        sec.SectorConstituent.model_validate(values)


def test_sector_snapshot_ref_rejects_future_revision_knowledge() -> None:
    snapshot = _constituent_snapshot()
    assert snapshot.revision_known_at <= snapshot.as_of

    with pytest.raises(ValidationError, match="cannot be known after snapshot as_of"):
        _constituent_snapshot(revision_known_at=NOW + timedelta(seconds=1))


def test_sector_definition_rejects_self_parent_invalid_period_and_revision_order() -> None:
    definition = _sector_definition()
    assert definition.revision_known_at >= definition.first_seen_at

    cases = [
        ({"parent_sector_id": SECTOR_ID}, "own parent"),
        ({"valid_until": definition.valid_from}, "valid_until must be later"),
        (
            {"revision_known_at": definition.first_seen_at - timedelta(seconds=1)},
            "revision_known_at cannot be earlier",
        ),
    ]
    for updates, message in cases:
        values = definition.model_dump()
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            sec.SectorDefinition.model_validate(values)


def test_sector_catalog_uses_opaque_cursor_and_rejects_future_revisions() -> None:
    query = sec.SectorCatalogQuery(
        schema_version="1.0",
        context=_operation_context(),
        kinds=[sec.SectorKind.INDUSTRY],
        cursor="opaque-sector-cursor",
        limit=1000,
    )
    result = sec.SectorCatalogResult(
        schema_version="1.0",
        sectors=[_sector_definition()],
        as_of=NOW,
        next_cursor="opaque-next-sector-cursor",
        has_more=True,
    )

    assert query.cursor == "opaque-sector-cursor"
    assert result.next_cursor == "opaque-next-sector-cursor"

    with pytest.raises(ValidationError, match="known after as_of"):
        sec.SectorCatalogResult(
            schema_version="1.0",
            sectors=[_sector_definition(revision_known_at=NOW + timedelta(seconds=1))],
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="next_cursor is required"):
        sec.SectorCatalogResult(schema_version="1.0", as_of=NOW, has_more=True)


def test_sector_constituent_page_enforces_snapshot_identity_pit_and_pagination() -> None:
    snapshot = _constituent_snapshot()
    page = sec.SectorConstituentPage(
        schema_version="1.0",
        sector_id=SECTOR_ID,
        snapshot=snapshot,
        items=[_sector_constituent()],
        next_cursor="opaque-next-constituent-cursor",
        has_more=True,
    )
    query = sec.SectorConstituentQuery(
        schema_version="1.0",
        context=_operation_context(),
        sector_id=SECTOR_ID,
        cursor="opaque-constituent-cursor",
        limit=1000,
    )

    assert page.next_cursor == "opaque-next-constituent-cursor"
    assert query.cursor == "opaque-constituent-cursor"

    with pytest.raises(ValidationError, match="must match sector_id"):
        sec.SectorConstituentPage(
            schema_version="1.0",
            sector_id=OTHER_SECTOR_ID,
            snapshot=snapshot,
        )
    with pytest.raises(ValidationError, match="learned after as_of"):
        sec.SectorConstituentPage(
            schema_version="1.0",
            sector_id=SECTOR_ID,
            snapshot=snapshot,
            items=[_sector_constituent(first_seen_at=NOW + timedelta(seconds=1))],
        )
    with pytest.raises(ValidationError, match="duplicate instruments"):
        sec.SectorConstituentPage(
            schema_version="1.0",
            sector_id=SECTOR_ID,
            snapshot=snapshot,
            items=[_sector_constituent(), _sector_constituent()],
        )
    with pytest.raises(ValidationError, match="next_cursor is required"):
        sec.SectorConstituentPage(
            schema_version="1.0",
            sector_id=SECTOR_ID,
            snapshot=snapshot,
            has_more=True,
        )


def test_heat_metric_uses_exact_decimal_string() -> None:
    metric = _heat_metric()

    assert metric.raw_value == Decimal("1234567.89")
    assert '"raw_value":"1234567.89"' in metric.model_dump_json()

    values = metric.model_dump()
    values["raw_value"] = 1234567.89
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        sec.HeatMetric.model_validate(values)


def test_sector_heat_snapshot_accepts_one_point_in_time_feature_set() -> None:
    query = sec.SectorHeatQuery(
        schema_version="1.0",
        context=_operation_context(),
        sector_ids=[SECTOR_ID],
        lookback_minutes=10,
        metric_names=[sec.HeatMetricName.TURNOVER],
    )
    snapshot = _heat_snapshot()

    assert snapshot.data_watermark <= snapshot.as_of
    assert sum(metric.weight for metric in snapshot.metrics) == pytest.approx(1.0)
    assert query.lookback_minutes == snapshot.lookback_minutes


def test_sector_heat_snapshot_rejects_each_pit_and_metric_violation() -> None:
    with pytest.raises(ValidationError, match="data_watermark cannot be later"):
        _heat_snapshot(data_watermark=NOW + timedelta(seconds=1))

    future_sector = _sector_definition(revision_known_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="sector revision cannot be known"):
        _heat_snapshot(sector=future_sector)

    with pytest.raises(ValidationError, match="must match the heat sector"):
        _heat_snapshot(constituent_snapshot=_constituent_snapshot(sector_id=OTHER_SECTOR_ID))

    with pytest.raises(ValidationError, match="future constituent snapshot"):
        _heat_snapshot(
            constituent_snapshot=_constituent_snapshot(
                as_of=NOW + timedelta(seconds=1),
                revision_known_at=NOW,
            )
        )

    with pytest.raises(ValidationError, match="metrics cannot be observed after"):
        _heat_snapshot(metrics=[_heat_metric(observed_at=NOW + timedelta(seconds=1))])

    duplicate_metrics = [
        _heat_metric(weight=0.5),
        _heat_metric(weight=0.5),
    ]
    with pytest.raises(ValidationError, match="metrics must be unique"):
        _heat_snapshot(metrics=duplicate_metrics)

    with pytest.raises(ValidationError, match="weights must sum to one"):
        _heat_snapshot(metrics=[_heat_metric(weight=0.75)])


def test_sector_heat_result_requires_shared_cutoff_and_unique_identity() -> None:
    snapshot = _heat_snapshot()
    result = sec.SectorHeatResult(schema_version="1.0", snapshots=[snapshot], as_of=NOW)

    assert result.snapshots == [snapshot]

    with pytest.raises(ValidationError, match="result as_of"):
        sec.SectorHeatResult(
            schema_version="1.0",
            snapshots=[_heat_snapshot(as_of=NOW - timedelta(seconds=1))],
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="at most one snapshot per sector"):
        sec.SectorHeatResult(
            schema_version="1.0",
            snapshots=[snapshot, _heat_snapshot(snapshot_id=_uuid(64))],
            as_of=NOW,
        )

    other_sector = _sector_definition(sector_id=OTHER_SECTOR_ID)
    duplicate_snapshot_id = _heat_snapshot(
        snapshot_id=snapshot.snapshot_id,
        sector=other_sector,
        constituent_snapshot=_constituent_snapshot(sector_id=OTHER_SECTOR_ID),
    )
    with pytest.raises(ValidationError, match="snapshot IDs must be unique"):
        sec.SectorHeatResult(
            schema_version="1.0",
            snapshots=[snapshot, duplicate_snapshot_id],
            as_of=NOW,
        )


def test_sector_visit_state_and_rotation_policy_validate_revision_and_weights() -> None:
    state = _visit_state()
    policy = _rotation_policy()

    assert state.last_completed_at <= state.revision_known_at
    assert policy.heat_weight + policy.fairness_weight == pytest.approx(1.0)

    values = state.model_dump()
    values["active_started_at"] = NOW + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="active_started_at cannot be later"):
        sec.SectorVisitState.model_validate(values)
    values = state.model_dump()
    values["last_completed_at"] = NOW + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="last_completed_at cannot be later"):
        sec.SectorVisitState.model_validate(values)
    with pytest.raises(ValidationError, match="must sum to one"):
        sec.RotationPolicy(
            policy_id="fair_rotation",
            version="1.0",
            heat_weight=0.7,
            fairness_weight=0.4,
        )


def test_sector_rotation_request_accepts_visible_unique_inputs() -> None:
    request = _rotation_request()

    assert request.schedule_from == request.context.as_of
    assert request.candidates[0].as_of <= request.context.as_of
    assert request.visits[0].revision_known_at <= request.context.as_of


def test_sector_rotation_request_rejects_duplicate_and_future_inputs() -> None:
    candidate = _heat_snapshot()
    with pytest.raises(ValidationError, match="candidates must be unique"):
        _rotation_request(candidates=[candidate, candidate])

    visit = _visit_state()
    with pytest.raises(ValidationError, match="visit states must be unique"):
        _rotation_request(visits=[visit, visit])

    with pytest.raises(ValidationError, match="calendar cannot be first seen after"):
        _rotation_request(trading_day=_trading_day(first_seen_at=NOW + timedelta(seconds=1)))

    with pytest.raises(ValidationError, match="calendar revision cannot be known after"):
        _rotation_request(trading_day=_trading_day(revised_at=NOW + timedelta(seconds=1)))

    with pytest.raises(ValidationError, match="candidates cannot use a future cutoff"):
        _rotation_request(
            context=_operation_context(as_of=NOW),
            candidates=[_heat_snapshot(as_of=NOW + timedelta(seconds=1))],
        )

    with pytest.raises(ValidationError, match="visit state revisions cannot be known after"):
        _rotation_request(visits=[_visit_state(revision_known_at=NOW + timedelta(seconds=1))])

    with pytest.raises(ValidationError, match="schedule_from cannot be earlier"):
        _rotation_request(schedule_from=NOW - timedelta(seconds=1))


def test_sector_rotation_plan_accepts_non_adjacent_revisits() -> None:
    plan = sec.SectorRotationPlan(
        schema_version="1.0",
        plan_id=_uuid(70),
        generated_at=NOW,
        valid_until=NOW + timedelta(minutes=40),
        slots=[
            _rotation_slot(sector_id=SECTOR_ID, offset_minutes=0),
            _rotation_slot(sector_id=OTHER_SECTOR_ID, offset_minutes=10),
            _rotation_slot(sector_id=SECTOR_ID, offset_minutes=20),
        ],
        interval_seconds=600,
        policy_id="fair_rotation",
        policy_version="1.0",
    )

    assert [slot.sector_id for slot in plan.slots] == [SECTOR_ID, OTHER_SECTOR_ID, SECTOR_ID]


def test_sector_rotation_plan_rejects_invalid_window_order_spacing_and_repetition() -> None:
    common = {
        "schema_version": "1.0",
        "plan_id": _uuid(70),
        "generated_at": NOW,
        "valid_until": NOW + timedelta(minutes=40),
        "interval_seconds": 600,
        "policy_id": "fair_rotation",
        "policy_version": "1.0",
    }
    with pytest.raises(ValidationError, match="valid_until must be later"):
        sec.SectorRotationPlan(**{**common, "valid_until": NOW})
    with pytest.raises(ValidationError, match="adjacent rotation slots"):
        sec.SectorRotationPlan(
            **common,
            slots=[
                _rotation_slot(sector_id=SECTOR_ID, offset_minutes=0),
                _rotation_slot(sector_id=SECTOR_ID, offset_minutes=10),
            ],
        )
    with pytest.raises(ValidationError, match="ordered by scheduled_at"):
        sec.SectorRotationPlan(
            **common,
            slots=[
                _rotation_slot(sector_id=SECTOR_ID, offset_minutes=10),
                _rotation_slot(sector_id=OTHER_SECTOR_ID, offset_minutes=0),
            ],
        )
    with pytest.raises(ValidationError, match="respect interval_seconds"):
        sec.SectorRotationPlan(
            **common,
            slots=[
                _rotation_slot(sector_id=SECTOR_ID, offset_minutes=0),
                _rotation_slot(sector_id=OTHER_SECTOR_ID, offset_minutes=5),
            ],
        )
    with pytest.raises(ValidationError, match="validity window"):
        sec.SectorRotationPlan(
            **common,
            slots=[_rotation_slot(sector_id=SECTOR_ID, offset_minutes=40)],
        )


def test_sector_visit_write_request_enforces_mutation_timeline() -> None:
    request = sec.SectorVisitWriteRequest(
        schema_version="1.0",
        context=_mutation_context(),
        plan_id=_uuid(70),
        sector_id=SECTOR_ID,
        started_at=NOW - timedelta(minutes=1),
        completed_at=NOW,
        outcome=sec.RotationOutcome.COMPLETED,
        reason_code="COMPLETED",
        expected_state_revision=4,
    )

    assert request.completed_at == request.context.as_of

    with pytest.raises(ValidationError, match="completed_at cannot be earlier"):
        sec.SectorVisitWriteRequest(
            schema_version="1.0",
            context=_mutation_context(),
            plan_id=_uuid(70),
            sector_id=SECTOR_ID,
            started_at=NOW,
            completed_at=NOW - timedelta(seconds=1),
            outcome=sec.RotationOutcome.FAILED,
            reason_code="SOURCE_TIMEOUT",
        )
    with pytest.raises(ValidationError, match="completed_at cannot be later"):
        sec.SectorVisitWriteRequest(
            schema_version="1.0",
            context=_mutation_context(),
            plan_id=_uuid(70),
            sector_id=SECTOR_ID,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
            outcome=sec.RotationOutcome.COMPLETED,
            reason_code="COMPLETED",
        )
