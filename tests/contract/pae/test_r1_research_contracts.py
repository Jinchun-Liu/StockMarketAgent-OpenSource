"""Frozen contract invariants for the R1 single-stock research product."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.behavior import (
    AttentionRiskBand,
    AttentionRiskCategory,
    AttentionRiskCategoryName,
    AttentionRiskIndex,
    BandTransitionDirection,
    BehaviorAssessmentLineageRef,
    BehaviorBandTransitionV1,
    BehaviorPressureSnapshotV1_0,
    BehaviorPressureSummary,
    EmpiricalRankMapping,
    HistoricalAnalogCandidate,
    HistoricalAnalogCandidatePage,
    HistoricalAnalogState,
    HistoricalAnalogSummary,
    IndexAvailability,
    MarketStructureDimension,
    PitLineage,
    PressureBand,
    PressureCluster,
    PressureClusterName,
    PressureFacet,
    PressureFacetName,
    PressureMode,
    StructureAvailability,
    StructureDimensionName,
    behavior_transition_idempotency_key,
)
from stock_assistant.contracts.common import InstrumentRef
from stock_assistant.contracts.forecasting import MarketBar
from stock_assistant.contracts.market_data import (
    MarketBarRevision,
    MarketDataManifest,
    MarketDataManifestStatus,
    MarketDataRevisionPage,
)
from stock_assistant.contracts.premarket_views import PremarketBriefingViewV1_3

AS_OF = datetime(2026, 8, 7, 7, 10, tzinfo=UTC)
HASH = "a" * 64


def _instrument(instrument_id: str = "XSHG:603986") -> InstrumentRef:
    return InstrumentRef(
        instrument_id=instrument_id,
        symbol="603986",
        exchange="XSHG",
        market="CN",
        asset_class="equity",
        currency="CNY",
    )


def _categories(*, available: int = 5) -> list[AttentionRiskCategory]:
    return [
        AttentionRiskCategory(
            category=name,
            score=20 if index < available else None,
            new_count=2 if index < available else 0,
            available=index < available,
        )
        for index, name in enumerate(AttentionRiskCategoryName)
    ]


def _dimensions(mode: PressureMode) -> list[MarketStructureDimension]:
    items: list[MarketStructureDimension] = []
    for name in StructureDimensionName:
        not_applicable = (
            mode is PressureMode.POST_CLOSE and name is StructureDimensionName.INTRADAY_ACCEPTANCE
        )
        items.append(
            MarketStructureDimension(
                dimension=name,
                availability=(
                    StructureAvailability.NOT_APPLICABLE
                    if not_applicable
                    else StructureAvailability.READY
                ),
                state=None if not_applicable else "状态正常",
                explanation="按冻结规则确定性计算。",
            )
        )
    return items


def _snapshot(
    *,
    mode: PressureMode = PressureMode.POST_CLOSE,
    comparable_days: int = 1000,
    score: int | None = 50,
) -> BehaviorPressureSnapshotV1_0:
    return BehaviorPressureSnapshotV1_0(
        schema_version="1.0",
        instrument=_instrument(),
        mode=mode,
        score=score,
        band=PressureBand.WARMING if score is not None else None,
        availability=IndexAvailability.READY if score is not None else IndexAvailability.ABSTAINED,
        score_mapping=(
            EmpiricalRankMapping(
                raw_total=50,
                population_count=comparable_days,
                less_count=499,
                equal_count=2,
                mapped_score=50,
                reference_mapping_version="behavior-mid-rank.v1",
                reference_population_hash=HASH,
            )
            if score is not None
            else None
        ),
        window_start=AS_OF - timedelta(minutes=10),
        window_end=AS_OF,
        as_of=AS_OF,
        generated_at=AS_OF + timedelta(seconds=5),
        facets=[
            PressureFacet(facet=name, score=50, availability=IndexAvailability.READY)
            for name in PressureFacetName
        ],
        clusters=[
            PressureCluster(
                cluster=name,
                score=50,
                availability=IndexAvailability.READY,
                inherited_from_post_close=(
                    mode is PressureMode.INTRADAY_10M
                    and name
                    in {
                        PressureClusterName.REFERENCE_COST_EXTENSION,
                        PressureClusterName.SHORT_PATH_EXTENSION,
                        PressureClusterName.PRICE_IMPACT,
                        PressureClusterName.SPREAD_PROXY,
                        PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY,
                    }
                ),
            )
            for name in PressureClusterName
        ],
        structure_dimensions=_dimensions(mode),
        historical_analogs=(
            HistoricalAnalogSummary(
                state=HistoricalAnalogState.INSUFFICIENT,
                sample_count=49,
                reason_code="INSUFFICIENT_SAMPLES",
                reason="样本不足，暂不展示区间。",  # noqa: RUF001
                selection_as_of=AS_OF,
                selection_semantics_hash=HASH,
            )
            if mode is PressureMode.POST_CLOSE
            else None
        ),
        completeness_percent=100,
        pit_lineage=PitLineage(
            data_manifest_hash=HASH,
            rights_snapshot_hash=HASH,
            comparison_semantics_hash=HASH,
            max_input_revision_known_at=AS_OF,
            assessment_revision_known_at=AS_OF + timedelta(seconds=5),
            comparable_instrument_day_count=comparable_days,
        ),
    )


def test_attention_risk_requires_three_available_categories() -> None:
    index = AttentionRiskIndex(
        score=None,
        band=None,
        availability=IndexAvailability.ABSTAINED,
        as_of=AS_OF,
        categories=_categories(available=2),
        available_category_count=2,
        completeness_percent=40,
    )
    assert index.score is None

    with pytest.raises(ValidationError, match="fewer than three"):
        AttentionRiskIndex(
            score=20,
            band=AttentionRiskBand.NOTICE,
            availability=IndexAvailability.READY,
            as_of=AS_OF,
            categories=_categories(available=2),
            available_category_count=2,
            completeness_percent=40,
        )


def test_behavior_score_requires_one_thousand_comparable_days() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1000"):
        _snapshot(comparable_days=999)
    assert _snapshot(comparable_days=999, score=None).score is None


def test_score_band_and_availability_cannot_contradict() -> None:
    with pytest.raises(ValidationError, match="band must match"):
        payload = _snapshot().model_dump(mode="python")
        BehaviorPressureSnapshotV1_0.model_validate(
            {**payload, "score": 0, "band": PressureBand.FRAGILE}
        )

    with pytest.raises(ValidationError, match="abstained behavior"):
        payload = _snapshot().model_dump(mode="python")
        BehaviorPressureSnapshotV1_0.model_validate(
            {**payload, "availability": IndexAvailability.ABSTAINED}
        )

    with pytest.raises(ValidationError, match="empirical mapped score"):
        payload = _snapshot().model_dump(mode="python")
        BehaviorPressureSnapshotV1_0.model_validate(
            {**payload, "score": 100, "band": PressureBand.FRAGILE}
        )


def test_post_close_and_intraday_contracts_keep_distinct_content() -> None:
    post_close = _snapshot()
    intraday = _snapshot(mode=PressureMode.INTRADAY_10M)

    assert post_close.historical_analogs is not None
    assert intraday.historical_analogs is None
    assert (
        next(
            item
            for item in post_close.structure_dimensions
            if item.dimension is StructureDimensionName.INTRADAY_ACCEPTANCE
        ).availability
        is StructureAvailability.NOT_APPLICABLE
    )


def test_historical_analogs_cannot_cross_assessment_cutoffs() -> None:
    snapshot = _snapshot()
    future_selection = HistoricalAnalogSummary.model_validate(
        {
            **snapshot.historical_analogs.model_dump(mode="python"),
            "selection_as_of": AS_OF + timedelta(seconds=1),
        }
    )

    with pytest.raises(ValidationError, match="selection cannot be later than snapshot"):
        BehaviorPressureSnapshotV1_0.model_validate(
            {
                **snapshot.model_dump(mode="python"),
                "historical_analogs": future_selection,
            }
        )

    with pytest.raises(ValidationError, match="selection cannot be later than summary"):
        BehaviorPressureSummary(
            mode=snapshot.mode,
            score=snapshot.score,
            band=snapshot.band,
            availability=snapshot.availability,
            score_mapping=snapshot.score_mapping,
            as_of=snapshot.as_of,
            facets=snapshot.facets,
            structure_dimensions=snapshot.structure_dimensions,
            historical_analogs=future_selection,
            completeness_percent=snapshot.completeness_percent,
        )


def test_historical_analog_candidates_must_predate_selection_cutoff() -> None:
    with pytest.raises(ValidationError, match="cannot occur after selection cutoff"):
        HistoricalAnalogCandidatePage(
            schema_version="1.0",
            items=[
                HistoricalAnalogCandidate(
                    assessment_id=uuid4(),
                    as_of=AS_OF + timedelta(seconds=1),
                    assessment_revision_known_at=AS_OF,
                    vector=[0.0] * 14,
                    approximate_distance=0,
                )
            ],
            selection_as_of=AS_OF,
        )


def test_band_transition_binds_lineage_assessments_and_business_key() -> None:
    previous_id = uuid4()
    current_id = uuid4()
    business_key = behavior_transition_idempotency_key(
        instrument_id="XSHG:603986",
        mode=PressureMode.INTRADAY_10M,
        trading_date=AS_OF.date(),
        target_band=PressureBand.ATTENTION,
    )
    payload = {
        "schema_version": "1.0",
        "mode": PressureMode.INTRADAY_10M,
        "previous_band": PressureBand.WARMING,
        "current_band": PressureBand.ATTENTION,
        "previous_assessment_id": previous_id,
        "current_assessment_id": current_id,
        "direction": BandTransitionDirection.HIGHER,
        "comparison_semantics_hash": HASH,
        "market_data_lineage_refs": [
            BehaviorAssessmentLineageRef(
                assessment_id=assessment_id,
                data_manifest_hash=HASH,
                rights_snapshot_hash=HASH,
                max_input_revision_known_at=AS_OF,
            )
            for assessment_id in (previous_id, current_id)
        ],
        "effective_at": AS_OF,
        "consecutive_complete_windows": 2,
        "trading_date": AS_OF.date(),
        "idempotency_key": business_key,
    }
    assert BehaviorBandTransitionV1.model_validate(payload).idempotency_key == business_key

    with pytest.raises(ValidationError, match="distinct assessments"):
        BehaviorBandTransitionV1.model_validate(
            {
                **payload,
                "current_assessment_id": previous_id,
                "market_data_lineage_refs": [payload["market_data_lineage_refs"][0]] * 2,
            }
        )
    with pytest.raises(ValidationError, match="frozen business key"):
        BehaviorBandTransitionV1.model_validate({**payload, "idempotency_key": "b" * 64})
    with pytest.raises(ValidationError, match="cover both"):
        BehaviorBandTransitionV1.model_validate(
            {
                **payload,
                "market_data_lineage_refs": [
                    payload["market_data_lineage_refs"][0],
                    BehaviorAssessmentLineageRef(
                        assessment_id=uuid4(),
                        data_manifest_hash=HASH,
                        rights_snapshot_hash=HASH,
                        max_input_revision_known_at=AS_OF,
                    ),
                ],
            }
        )


def test_public_contract_is_fixed_to_gigadevice_and_exposes_v13_fields() -> None:
    with pytest.raises(ValidationError, match="canonical XSHG:603986"):
        BehaviorPressureSnapshotV1_0.model_validate(
            {**_snapshot().model_dump(mode="python"), "instrument": _instrument("XSHG:600000")}
        )

    assert {
        "attention_risk_index",
        "behavior_pressure_summary",
        "plain_language_update",
        "flash_interpretation",
        "pro_interpretation",
    } <= set(PremarketBriefingViewV1_3.model_fields)


def test_market_bar_revision_enforces_available_received_known_order() -> None:
    bar = MarketBar(
        instrument=_instrument(),
        interval="1d",
        opened_at=AS_OF - timedelta(days=1),
        closed_at=AS_OF - timedelta(hours=1),
        received_at=AS_OF - timedelta(minutes=50),
        open_price="100",
        high_price="110",
        low_price="90",
        close_price="105",
        volume="1000",
        adjusted=False,
        source_id="demo_source",
        data_version="v1",
        adjustment_version="raw-v1",
        rights_snapshot_hash=HASH,
    )
    with pytest.raises(ValidationError, match="available <= received <= known"):
        MarketBarRevision(
            instrument=_instrument(),
            effective_at=bar.closed_at,
            available_at=AS_OF - timedelta(minutes=40),
            revision_known_at=AS_OF,
            first_seen_at=AS_OF - timedelta(minutes=30),
            source_id="demo_source",
            rights_snapshot_hash=HASH,
            content_hash=HASH,
            bar=bar,
        )


def test_market_data_page_rejects_rows_not_yet_available_at_cutoff() -> None:
    bar = MarketBar(
        instrument=_instrument(),
        interval="1d",
        opened_at=AS_OF - timedelta(days=1),
        closed_at=AS_OF - timedelta(hours=1),
        received_at=AS_OF + timedelta(seconds=2),
        open_price="100",
        high_price="110",
        low_price="90",
        close_price="105",
        volume="1000",
        adjusted=False,
        source_id="demo_source",
        data_version="v1",
        adjustment_version="raw-v1",
        rights_snapshot_hash=HASH,
    )
    revision = MarketBarRevision(
        instrument=_instrument(),
        effective_at=bar.closed_at,
        available_at=AS_OF + timedelta(seconds=1),
        revision_known_at=AS_OF + timedelta(seconds=3),
        first_seen_at=AS_OF + timedelta(seconds=2),
        source_id="demo_source",
        rights_snapshot_hash=HASH,
        content_hash=HASH,
        bar=bar,
    )
    manifest = MarketDataManifest(
        status=MarketDataManifestStatus.INCOMPLETE,
        requested_from=AS_OF - timedelta(days=1),
        requested_until=AS_OF,
        as_of=AS_OF,
        row_count=1,
        source_id="demo_source",
        rights_snapshot_hash=HASH,
        content_hash=HASH,
        gap_reason_code="PARTIAL_DATA",
    )

    with pytest.raises(ValidationError, match="available after as_of"):
        MarketDataRevisionPage(
            schema_version="1.0",
            items=[revision],
            as_of=AS_OF,
            manifest=manifest,
        )
