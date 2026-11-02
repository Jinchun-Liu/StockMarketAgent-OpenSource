"""Pure composition for the frozen ten-cluster R1 research score."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from stock_assistant.contracts.behavior import (
    EmpiricalRankMapping,
    IndexAvailability,
    IndexExplanation,
    PressureBand,
    PressureCluster,
    PressureClusterName,
    PressureFacet,
    PressureFacetName,
)

_FACET_CLUSTERS: Final = {
    PressureFacetName.ACTIVITY_DIVERGENCE: (
        PressureClusterName.ACTIVITY_ANOMALY,
        PressureClusterName.CLOSE_PATH_REJECTION,
    ),
    PressureFacetName.EXTENSION_CROWDING: (
        PressureClusterName.REFERENCE_COST_EXTENSION,
        PressureClusterName.SHORT_PATH_EXTENSION,
    ),
    PressureFacetName.LIQUIDITY_FRAGILITY: (
        PressureClusterName.PRICE_IMPACT,
        PressureClusterName.SPREAD_PROXY,
    ),
    PressureFacetName.VOLATILITY_TAIL: (
        PressureClusterName.VOLATILITY_EXPANSION,
        PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY,
    ),
    PressureFacetName.MARKET_INDUSTRY_DIVERGENCE: (
        PressureClusterName.BREADTH_BACKGROUND,
        PressureClusterName.IDIOSYNCRATIC_DIVERGENCE,
    ),
}

_CLUSTER_LABELS: Final = {
    PressureClusterName.ACTIVITY_ANOMALY: "交易活跃度异常",
    PressureClusterName.CLOSE_PATH_REJECTION: "收盘路径承压",
    PressureClusterName.REFERENCE_COST_EXTENSION: "参考成本延伸",
    PressureClusterName.SHORT_PATH_EXTENSION: "短期路径延伸",
    PressureClusterName.PRICE_IMPACT: "价格冲击",
    PressureClusterName.SPREAD_PROXY: "低频价差",
    PressureClusterName.VOLATILITY_EXPANSION: "波动扩张",
    PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY: "下行尾部偏重",
    PressureClusterName.BREADTH_BACKGROUND: "市场与行业宽度",
    PressureClusterName.IDIOSYNCRATIC_DIVERGENCE: "个股特异背离",
}


@dataclass(frozen=True, slots=True)
class BehaviorScoreResult:
    """Materialized deterministic outputs before snapshot and persistence concerns."""

    availability: IndexAvailability
    score: int | None
    band: PressureBand | None
    score_mapping: EmpiricalRankMapping | None
    clusters: tuple[PressureCluster, ...]
    facets: tuple[PressureFacet, ...]
    drivers: tuple[IndexExplanation, ...]
    mitigators: tuple[IndexExplanation, ...]
    completeness_percent: int


def _rounded_mean(values: Sequence[int]) -> int:
    return int(
        (
            sum((Decimal(value) for value in values), start=Decimal(0)) / Decimal(len(values))
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _band(score: int) -> PressureBand:
    if score <= 39:
        return PressureBand.STABLE
    if score <= 59:
        return PressureBand.WARMING
    if score <= 74:
        return PressureBand.ATTENTION
    if score <= 89:
        return PressureBand.CROWDED
    return PressureBand.FRAGILE


def _explanations(
    cluster_scores: Mapping[PressureClusterName, int | None],
) -> tuple[tuple[IndexExplanation, ...], tuple[IndexExplanation, ...]]:
    ranked = sorted(
        (
            ((Decimal(score) - Decimal(50)) * Decimal("0.10"), cluster)
            for cluster, score in cluster_scores.items()
            if score is not None and abs((Decimal(score) - Decimal(50)) * Decimal("0.10")) >= 1
        ),
        key=lambda item: (-abs(item[0]), item[1].value),
    )
    drivers = tuple(
        IndexExplanation(
            code=cluster.value.upper(),
            text=f"{_CLUSTER_LABELS[cluster]}推高当前结构压力",
        )
        for contribution, cluster in ranked
        if contribution > 0
    )[:3]
    mitigators = tuple(
        IndexExplanation(
            code=cluster.value.upper(),
            text=f"{_CLUSTER_LABELS[cluster]}对当前压力形成缓和",
        )
        for contribution, cluster in ranked
        if contribution < 0
    )[:2]
    return drivers, mitigators


def compose_behavior_pressure(
    *,
    cluster_scores: Mapping[PressureClusterName, int | None],
    reference_raw_totals: Sequence[int],
    reference_population_hash: str,
    inherited_clusters: frozenset[PressureClusterName] = frozenset(),
) -> BehaviorScoreResult:
    """Compose ten equal clusters, five equal facets and the empirical mid-rank score.

    Missing core clusters and reference populations below 1,000 always abstain. No
    remaining cluster is reweighted. The reference population is bounded to the most
    recent 10,000 values by its caller; this function rejects larger inputs.
    """

    if set(cluster_scores) != set(PressureClusterName):
        raise ValueError("cluster_scores must contain each frozen cluster exactly once")
    if len(reference_raw_totals) > 10_000:
        raise ValueError("reference population cannot exceed 10,000 instrument-days")
    if any(value < 0 or value > 100 for value in reference_raw_totals):
        raise ValueError("reference raw totals must be bounded 0-100 scores")
    if not inherited_clusters <= set(PressureClusterName):
        raise ValueError("inherited clusters must use frozen cluster names")

    clusters = tuple(
        PressureCluster(
            cluster=name,
            score=cluster_scores[name],
            availability=(
                IndexAvailability.READY
                if cluster_scores[name] is not None
                else IndexAvailability.ABSTAINED
            ),
            reason_code=(None if cluster_scores[name] is not None else "CORE_CLUSTER_MISSING"),
            inherited_from_post_close=name in inherited_clusters,
        )
        for name in PressureClusterName
    )
    facets: list[PressureFacet] = []
    for facet_name, members in _FACET_CLUSTERS.items():
        values = [cluster_scores[member] for member in members]
        complete_values = [value for value in values if value is not None]
        score = _rounded_mean(complete_values) if len(complete_values) == 2 else None
        facets.append(
            PressureFacet(
                facet=facet_name,
                score=score,
                availability=(
                    IndexAvailability.READY if score is not None else IndexAvailability.ABSTAINED
                ),
                reason_code=None if score is not None else "CORE_CLUSTER_MISSING",
            )
        )

    available_count = sum(value is not None for value in cluster_scores.values())
    completeness = available_count * 10
    score_mapping: EmpiricalRankMapping | None = None
    mapped_score: int | None = None
    mapped_band: PressureBand | None = None
    if available_count == 10 and len(reference_raw_totals) >= 1000:
        raw_total = _rounded_mean([value for value in cluster_scores.values() if value is not None])
        less_count = sum(value < raw_total for value in reference_raw_totals)
        equal_count = sum(value == raw_total for value in reference_raw_totals)
        if equal_count == 0:
            raise ValueError("reference population must contain the current rounded raw total")
        population_count = len(reference_raw_totals)
        percentile = (
            Decimal(100)
            * (Decimal(less_count) + Decimal("0.5") * Decimal(equal_count))
            / Decimal(population_count)
        )
        mapped_score = int(percentile.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        score_mapping = EmpiricalRankMapping(
            raw_total=raw_total,
            population_count=population_count,
            less_count=less_count,
            equal_count=equal_count,
            mapped_score=mapped_score,
            reference_mapping_version="behavior-mid-rank.v1",
            reference_population_hash=reference_population_hash,
        )
        mapped_band = _band(mapped_score)

    drivers, mitigators = _explanations(cluster_scores)
    return BehaviorScoreResult(
        availability=(
            IndexAvailability.READY if mapped_score is not None else IndexAvailability.ABSTAINED
        ),
        score=mapped_score,
        band=mapped_band,
        score_mapping=score_mapping,
        clusters=clusters,
        facets=tuple(facets),
        drivers=drivers,
        mitigators=mitigators,
        completeness_percent=completeness,
    )
