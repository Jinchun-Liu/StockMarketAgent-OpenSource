from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from stock_assistant.contracts.behavior import (
    HistoricalAnalogState,
    IndexAvailability,
    PressureBand,
    PressureClusterName,
)
from stock_assistant.modules.behavior.analogs import AnalogObservation, select_analogs
from stock_assistant.modules.behavior.scoring import compose_behavior_pressure


def _scores() -> dict[PressureClusterName, int]:
    return {name: index * 10 for index, name in enumerate(PressureClusterName)}


def test_ten_clusters_form_five_equal_facets_and_empirical_mid_rank() -> None:
    result = compose_behavior_pressure(
        cluster_scores=_scores(),
        reference_raw_totals=[*([10] * 400), *([45] * 200), *([80] * 400)],
        reference_population_hash="a" * 64,
    )

    assert result.availability is IndexAvailability.READY
    assert [facet.score for facet in result.facets] == [5, 25, 45, 65, 85]
    assert result.score_mapping is not None
    assert result.score_mapping.raw_total == 45
    assert result.score_mapping.mapped_score == 50
    assert result.score == 50
    assert result.band is PressureBand.WARMING
    assert result.completeness_percent == 100
    assert [item.code for item in result.drivers] == [
        "IDIOSYNCRATIC_DIVERGENCE",
        "BREADTH_BACKGROUND",
        "DOWNSIDE_TAIL_ASYMMETRY",
    ]
    assert [item.code for item in result.mitigators] == [
        "ACTIVITY_ANOMALY",
        "CLOSE_PATH_REJECTION",
    ]


def test_missing_cluster_or_small_reference_population_abstains_without_reweighting() -> None:
    missing = _scores()
    missing[PressureClusterName.SPREAD_PROXY] = None  # type: ignore[assignment]
    incomplete = compose_behavior_pressure(
        cluster_scores=missing,
        reference_raw_totals=[50] * 1000,
        reference_population_hash="b" * 64,
    )
    too_small = compose_behavior_pressure(
        cluster_scores=_scores(),
        reference_raw_totals=[50] * 999,
        reference_population_hash="c" * 64,
    )

    assert incomplete.availability is IndexAvailability.ABSTAINED
    assert incomplete.score is None
    assert incomplete.score_mapping is None
    assert incomplete.completeness_percent == 90
    assert too_small.availability is IndexAvailability.ABSTAINED
    assert too_small.score is None
    assert too_small.completeness_percent == 100


def _analog(
    *,
    instrument_id: str,
    trading_day_index: int,
    distance: float,
    selection_as_of: datetime,
) -> AnalogObservation:
    observed_at = selection_as_of - timedelta(days=trading_day_index + 5)
    return AnalogObservation(
        assessment_id=uuid4(),
        instrument_id=instrument_id,
        trading_day_index=trading_day_index,
        as_of=observed_at,
        assessment_revision_known_at=observed_at + timedelta(hours=1),
        label_available_at=observed_at + timedelta(days=4),
        vector=(distance, *(0.0 for _ in range(13))),
        outcomes={
            "return_1d": Decimal("0.01"),
            "return_3d": Decimal("0.02"),
            "industry_excess": Decimal("0.005"),
            "mae": Decimal("-0.03"),
            "mfe": Decimal("0.04"),
        },
    )


def test_analog_selection_exactly_rechecks_l2_pit_twenty_day_spacing_and_cap() -> None:
    selection_as_of = datetime(2026, 8, 7, tzinfo=UTC)
    observations = [
        _analog(
            instrument_id=f"XSHG:PEER{index:04d}",
            trading_day_index=0,
            distance=float(index) / 1000,
            selection_as_of=selection_as_of,
        )
        for index in range(250)
    ]
    observations.append(
        replace(
            observations[0],
            assessment_id=uuid4(),
            assessment_revision_known_at=selection_as_of + timedelta(seconds=1),
            vector=(0.0, *(0.0 for _ in range(13))),
        )
    )

    result = select_analogs(
        query_vector=(0.0, *(0.0 for _ in range(13))),
        observations=observations,
        selection_as_of=selection_as_of,
        selection_semantics_hash="d" * 64,
    )

    assert len(result.selected) == 200
    assert result.summary.state is HistoricalAnalogState.READY
    assert result.summary.sample_count == 200
    assert len(result.summary.ranges) == 5
    assert all(item.assessment_revision_known_at <= selection_as_of for item in result.selected)


def test_analog_selection_hides_ranges_below_fifty_samples() -> None:
    selection_as_of = datetime(2026, 8, 7, tzinfo=UTC)
    observations = [
        _analog(
            instrument_id="XSHG:ONE",
            trading_day_index=index,
            distance=float(index),
            selection_as_of=selection_as_of,
        )
        for index in range(980)
    ]

    result = select_analogs(
        query_vector=(0.0, *(0.0 for _ in range(13))),
        observations=observations,
        selection_as_of=selection_as_of,
        selection_semantics_hash="e" * 64,
    )

    assert len(result.selected) == 49
    assert result.summary.state is HistoricalAnalogState.INSUFFICIENT
    assert result.summary.ranges == []


def test_analog_vectors_are_exactly_fourteen_dimensions() -> None:
    with pytest.raises(ValueError, match="14 dimensions"):
        select_analogs(
            query_vector=(0.0,) * 13,
            observations=[],
            selection_as_of=datetime(2026, 8, 7, tzinfo=UTC),
            selection_semantics_hash="f" * 64,
        )
