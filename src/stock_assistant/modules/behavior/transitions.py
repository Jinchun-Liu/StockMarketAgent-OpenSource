"""Restart-safe band hysteresis for materialized behavior assessments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid5

from stock_assistant.contracts.behavior import (
    BandTransitionDirection,
    BehaviorAssessmentLineageRef,
    BehaviorBandTransitionV1,
    BehaviorTransitionState,
    PressureBand,
    PressureMode,
    behavior_transition_idempotency_key,
)

_TRANSITION_EVENT_NAMESPACE = UUID("3c411684-cd16-557f-8ca9-c5b6476d798f")


@dataclass(frozen=True, slots=True)
class TransitionObservation:
    assessment_id: UUID
    band: PressureBand
    mode: PressureMode
    effective_at: datetime
    comparison_semantics_hash: str
    data_manifest_hash: str
    rights_snapshot_hash: str
    max_input_revision_known_at: datetime


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    state: BehaviorTransitionState
    event: BehaviorBandTransitionV1 | None
    idempotent_replay: bool = False


def _event(
    *,
    previous: TransitionObservation,
    previous_band: PressureBand,
    current: TransitionObservation,
    trading_date: date,
    consecutive_windows: int,
) -> BehaviorBandTransitionV1:
    order = list(PressureBand)
    direction = (
        BandTransitionDirection.HIGHER
        if order.index(current.band) > order.index(previous.band)
        else BandTransitionDirection.LOWER
    )
    idempotency_key = behavior_transition_idempotency_key(
        instrument_id="XSHG:603986",
        mode=current.mode,
        trading_date=trading_date,
        target_band=current.band,
    )
    return BehaviorBandTransitionV1(
        schema_version="1.0",
        event_id=uuid5(_TRANSITION_EVENT_NAMESPACE, idempotency_key),
        instrument_id="XSHG:603986",
        mode=current.mode,
        previous_band=previous_band,
        current_band=current.band,
        previous_assessment_id=previous.assessment_id,
        current_assessment_id=current.assessment_id,
        direction=direction,
        comparison_semantics_hash=current.comparison_semantics_hash,
        market_data_lineage_refs=[
            BehaviorAssessmentLineageRef(
                assessment_id=item.assessment_id,
                data_manifest_hash=item.data_manifest_hash,
                rights_snapshot_hash=item.rights_snapshot_hash,
                max_input_revision_known_at=item.max_input_revision_known_at,
            )
            for item in (previous, current)
        ],
        effective_at=current.effective_at,
        consecutive_complete_windows=consecutive_windows,
        trading_date=trading_date,
        idempotency_key=idempotency_key,
    )


def advance_transition(
    *,
    previous_state: BehaviorTransitionState | None,
    previous_observation: TransitionObservation | None,
    transition_anchor: TransitionObservation | None = None,
    current: TransitionObservation,
    trading_date: date,
    updated_at: datetime,
) -> TransitionDecision:
    """Advance persisted hysteresis state; duplicate assessments are exact no-ops."""

    if previous_state is None:
        return TransitionDecision(
            state=BehaviorTransitionState(
                instrument_id="XSHG:603986",
                mode=current.mode,
                trading_date=trading_date,
                current_band=current.band,
                revision=1,
                last_assessment_id=current.assessment_id,
                updated_at=updated_at,
            ),
            event=None,
        )
    if previous_state.mode is not current.mode or previous_state.trading_date != trading_date:
        raise ValueError("transition state must match current mode and trading date")
    if previous_state.last_assessment_id == current.assessment_id:
        return TransitionDecision(
            state=previous_state,
            event=None,
            idempotent_replay=True,
        )
    if previous_observation is None:
        raise ValueError("an existing transition state requires its previous observation")
    if previous_state.last_assessment_id != previous_observation.assessment_id:
        raise ValueError("previous observation must match persisted transition state")
    if previous_observation.comparison_semantics_hash != current.comparison_semantics_hash:
        return TransitionDecision(
            state=previous_state.model_copy(
                update={
                    "current_band": current.band,
                    "candidate_band": None,
                    "consecutive_complete_windows": 0,
                    "last_assessment_id": current.assessment_id,
                    "revision": previous_state.revision + 1,
                    "updated_at": updated_at,
                }
            ),
            event=None,
        )
    if previous_state.current_band is current.band:
        return TransitionDecision(
            state=previous_state.model_copy(
                update={
                    "candidate_band": None,
                    "consecutive_complete_windows": 0,
                    "last_assessment_id": current.assessment_id,
                    "revision": previous_state.revision + 1,
                    "updated_at": updated_at,
                }
            ),
            event=None,
        )

    required_windows = 2 if current.mode is PressureMode.INTRADAY_10M else 1
    streak = (
        previous_state.consecutive_complete_windows + 1
        if previous_state.candidate_band is current.band
        else 1
    )
    if streak < required_windows:
        return TransitionDecision(
            state=previous_state.model_copy(
                update={
                    "candidate_band": current.band,
                    "consecutive_complete_windows": streak,
                    "last_assessment_id": current.assessment_id,
                    "revision": previous_state.revision + 1,
                    "updated_at": updated_at,
                }
            ),
            event=None,
        )

    previous_band = previous_state.current_band
    if previous_band is None:
        raise ValueError("a scored transition state requires a current band")
    event_anchor = transition_anchor or previous_observation
    if event_anchor.band is not previous_band:
        raise ValueError("transition anchor must represent the previous stable band")
    transition = _event(
        previous=event_anchor,
        previous_band=previous_band,
        current=current,
        trading_date=trading_date,
        consecutive_windows=required_windows,
    )
    return TransitionDecision(
        state=previous_state.model_copy(
            update={
                "current_band": current.band,
                "candidate_band": None,
                "consecutive_complete_windows": 0,
                "last_assessment_id": current.assessment_id,
                "notified_band": current.band,
                "revision": previous_state.revision + 1,
                "updated_at": updated_at,
            }
        ),
        event=transition,
    )
