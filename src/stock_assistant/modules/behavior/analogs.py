"""PIT-safe exact historical-analog selection and robust distribution summaries."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from stock_assistant.contracts.behavior import (
    HistoricalAnalogCandidate,
    HistoricalAnalogState,
    HistoricalAnalogSummary,
    HistoricalOutcomeRange,
)
from stock_assistant.modules.behavior.scoring import BehaviorScoreResult

_METRICS: Final = ("return_1d", "return_3d", "industry_excess", "mae", "mfe")


@dataclass(frozen=True, slots=True)
class AnalogObservation:
    assessment_id: UUID
    instrument_id: str
    trading_day_index: int
    as_of: datetime
    assessment_revision_known_at: datetime
    label_available_at: datetime
    vector: tuple[float, ...]
    outcomes: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class AnalogSelection:
    selected: tuple[HistoricalAnalogCandidate, ...]
    summary: HistoricalAnalogSummary


def behavior_analog_vector(score_result: BehaviorScoreResult) -> tuple[float, ...]:
    """Freeze the R1 14D vector as ten clusters plus four leading facets."""

    values = [item.score for item in score_result.clusters] + [
        item.score for item in score_result.facets[:4]
    ]
    if len(values) != 14 or any(value is None for value in values):
        raise ValueError("behavior analog vector requires fourteen scored coordinates")
    return tuple(float(value) for value in values if value is not None)


def _quantile(values: Sequence[Decimal], fraction: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - Decimal(lower_index)
    value = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * weight
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def select_analogs(
    *,
    query_vector: Sequence[float],
    observations: Sequence[AnalogObservation],
    selection_as_of: datetime,
    selection_semantics_hash: str,
) -> AnalogSelection:
    """Exact-L2 recheck, PIT filter, 20-trading-day de-correlation and 200 cap."""

    if len(query_vector) != 14 or any(not math.isfinite(value) for value in query_vector):
        raise ValueError("analog query vector must contain exactly 14 dimensions")
    ranked: list[tuple[float, AnalogObservation]] = []
    for item in observations:
        if len(item.vector) != 14 or any(not math.isfinite(value) for value in item.vector):
            raise ValueError("analog observation vector must contain exactly 14 dimensions")
        if set(item.outcomes) != set(_METRICS):
            raise ValueError("analog outcomes must contain the frozen five metrics")
        if (
            item.as_of >= selection_as_of
            or item.assessment_revision_known_at > selection_as_of
            or item.label_available_at > selection_as_of
        ):
            continue
        ranked.append((_distance(query_vector, item.vector), item))
    ranked.sort(key=lambda item: (item[0], str(item[1].assessment_id)))

    selected_rows: list[tuple[float, AnalogObservation]] = []
    selected_indices: dict[str, list[int]] = {}
    for distance, item in ranked:
        previous = selected_indices.setdefault(item.instrument_id, [])
        if any(abs(item.trading_day_index - index) < 20 for index in previous):
            continue
        selected_rows.append((distance, item))
        previous.append(item.trading_day_index)
        if len(selected_rows) == 200:
            break

    selected = tuple(
        HistoricalAnalogCandidate(
            assessment_id=item.assessment_id,
            as_of=item.as_of,
            assessment_revision_known_at=item.assessment_revision_known_at,
            vector=list(item.vector),
            approximate_distance=distance,
        )
        for distance, item in selected_rows
    )
    if len(selected_rows) < 50:
        summary = HistoricalAnalogSummary(
            state=HistoricalAnalogState.INSUFFICIENT,
            sample_count=len(selected_rows),
            reason_code="ANALOG_SAMPLE_INSUFFICIENT",
            reason="可比历史样本不足 50 个; 暂不展示结果区间。",
            selection_as_of=selection_as_of,
            selection_semantics_hash=selection_semantics_hash,
        )
    else:
        ranges = []
        for metric in _METRICS:
            values = [item.outcomes[metric] for _, item in selected_rows]
            ranges.append(
                HistoricalOutcomeRange(
                    metric=metric,
                    q1=_quantile(values, Decimal("0.25")),
                    median=_quantile(values, Decimal("0.50")),
                    q3=_quantile(values, Decimal("0.75")),
                )
            )
        summary = HistoricalAnalogSummary(
            state=HistoricalAnalogState.READY,
            sample_count=len(selected_rows),
            ranges=ranges,
            selection_as_of=selection_as_of,
            selection_semantics_hash=selection_semantics_hash,
        )
    return AnalogSelection(selected=selected, summary=summary)
