"""Deterministic DEV_ONLY score inputs derived from explicit market revisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from statistics import fmean
from typing import Literal

from stock_assistant.contracts.behavior import BehaviorComputeRequest, PressureClusterName
from stock_assistant.contracts.market_data import (
    MarketBarRevision,
    MarketDataManifestStatus,
    MarketDataRevision,
    MarketDataRevisionPage,
)

_MINIMUM_COMPLETE_BARS = 10
_REFERENCE_RAW_TOTALS = tuple((index * 37 + 17) % 101 for index in range(1000))
_REFERENCE_POPULATION_HASH = hashlib.sha256(
    ",".join(str(value) for value in _REFERENCE_RAW_TOTALS).encode("utf-8")
).hexdigest()


@dataclass(frozen=True, slots=True)
class DevOnlyBehaviorInputs:
    """Transparent demo-only inputs; never evidence of an authorized provider."""

    release_tier: Literal["DEV_ONLY"]
    cluster_scores: dict[PressureClusterName, int]
    reference_raw_totals: tuple[int, ...]
    reference_population_hash: str


def _bounded(value: float) -> int:
    return min(100, max(0, round(value)))


def build_dev_only_behavior_inputs(
    *,
    revisions: list[MarketDataRevision],
) -> DevOnlyBehaviorInputs | None:
    """Derive demo proxies only from ten explicit, compatible DEV_ONLY market bars."""

    bars = [item for item in revisions if isinstance(item, MarketBarRevision)]
    if len(bars) < _MINIMUM_COMPLETE_BARS or len(bars) != len(revisions):
        return None
    if any(item.source_id != "dev_only_fixture" for item in bars):
        return None
    if len({item.instrument.instrument_id for item in bars}) != 1:
        return None
    ordered = sorted(bars, key=lambda item: (item.effective_at, item.revision_id))
    if len({item.effective_at for item in ordered}) != len(ordered):
        return None

    opens = [float(item.bar.open_price) for item in ordered]
    highs = [float(item.bar.high_price) for item in ordered]
    lows = [float(item.bar.low_price) for item in ordered]
    closes = [float(item.bar.close_price) for item in ordered]
    volumes = [float(item.bar.volume) for item in ordered]
    returns = [
        (close - open_price) / open_price for open_price, close in zip(opens, closes, strict=True)
    ]
    ranges = [
        (high - low) / open_price for open_price, high, low in zip(opens, highs, lows, strict=True)
    ]
    mean_volume = fmean(volumes)
    mean_range = fmean(ranges)
    total_return = (closes[-1] - opens[0]) / opens[0]
    latest_position = (
        (closes[-1] - lows[-1]) / (highs[-1] - lows[-1]) if highs[-1] > lows[-1] else 0.5
    )
    downside = sum(abs(value) for value in returns if value < 0)
    upside = sum(value for value in returns if value > 0)
    close_mean = fmean(closes)
    return_mean = fmean(returns)
    return_dispersion = fmean(abs(value - return_mean) for value in returns)
    activity_ratio = volumes[-1] / mean_volume if mean_volume > 0 else 1.0
    range_ratio = ranges[-1] / mean_range if mean_range > 0 else 1.0

    cluster_scores = {
        PressureClusterName.ACTIVITY_ANOMALY: _bounded(50 + (activity_ratio - 1) * 40),
        PressureClusterName.CLOSE_PATH_REJECTION: _bounded(100 * (1 - latest_position)),
        PressureClusterName.REFERENCE_COST_EXTENSION: _bounded(
            1000 * abs(closes[-1] / close_mean - 1)
        ),
        PressureClusterName.SHORT_PATH_EXTENSION: _bounded(800 * abs(total_return)),
        PressureClusterName.PRICE_IMPACT: _bounded(
            50 + 500 * abs(total_return) / max(activity_ratio, 0.1)
        ),
        PressureClusterName.SPREAD_PROXY: _bounded(2500 * mean_range),
        PressureClusterName.VOLATILITY_EXPANSION: _bounded(50 * range_ratio),
        PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY: _bounded(
            100 * downside / max(downside + upside, 0.000001)
        ),
        PressureClusterName.BREADTH_BACKGROUND: _bounded(
            100 * sum(value > 0 for value in returns) / len(returns)
        ),
        PressureClusterName.IDIOSYNCRATIC_DIVERGENCE: _bounded(5000 * return_dispersion),
    }
    raw_total = round(sum(Decimal(value) for value in cluster_scores.values()) / Decimal(10))
    if raw_total not in _REFERENCE_RAW_TOTALS:
        raise AssertionError("DEV_ONLY reference universe must cover every 0-100 raw total")
    return DevOnlyBehaviorInputs(
        release_tier="DEV_ONLY",
        cluster_scores=cluster_scores,
        reference_raw_totals=_REFERENCE_RAW_TOTALS,
        reference_population_hash=_REFERENCE_POPULATION_HASH,
    )


def validated_dev_only_behavior_inputs(
    *,
    request: BehaviorComputeRequest,
    page: MarketDataRevisionPage,
) -> DevOnlyBehaviorInputs | None:
    """Validate manifest, authorization and PIT lineage before deriving a score."""

    if (
        page.has_more
        or page.manifest.status is not MarketDataManifestStatus.INCOMPLETE
        or page.manifest.gap_reason_code != "OBJECT_STORE_NOT_MATERIALIZED"
        or page.manifest.row_count != len(page.items)
        or page.manifest.content_hash != request.data_manifest_hash
        or page.manifest.rights_snapshot_hash != request.rights_snapshot_hash
    ):
        return None
    if any(
        item.instrument.instrument_id != request.instrument_id
        or item.rights_snapshot_hash != request.rights_snapshot_hash
        or item.available_at > request.context.as_of
        or item.revision_known_at > request.context.as_of
        or item.effective_at < request.window_start
        or item.effective_at > request.window_end
        for item in page.items
    ):
        return None
    return build_dev_only_behavior_inputs(revisions=list(page.items))


__all__ = [
    "DevOnlyBehaviorInputs",
    "build_dev_only_behavior_inputs",
    "validated_dev_only_behavior_inputs",
]
