from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from stock_assistant.contracts.behavior import (
    BehaviorComputeRequest,
    BehaviorPressureAssessmentWriteResult,
    BehaviorTransitionState,
    PressureBand,
    PressureClusterName,
    PressureMode,
)
from stock_assistant.contracts.common import MutationContext
from stock_assistant.modules.behavior.scoring import compose_behavior_pressure
from stock_assistant.modules.behavior.service import (
    BehaviorPressureApplicationService,
    materialize_snapshot,
)
from stock_assistant.modules.behavior.transitions import (
    TransitionObservation,
    advance_transition,
)

NOW = datetime(2026, 8, 7, 2, 0, tzinfo=UTC)
TRADING_DATE = date(2026, 8, 7)


def _observation(band: PressureBand, *, offset: int = 0) -> TransitionObservation:
    return TransitionObservation(
        assessment_id=uuid4(),
        band=band,
        mode=PressureMode.INTRADAY_10M,
        effective_at=NOW + timedelta(minutes=offset * 10),
        comparison_semantics_hash="a" * 64,
        data_manifest_hash="b" * 64,
        rights_snapshot_hash="c" * 64,
        max_input_revision_known_at=NOW + timedelta(minutes=offset * 10),
    )


def test_intraday_transition_requires_two_complete_windows_and_reuses_daily_identity() -> None:
    baseline_observation = _observation(PressureBand.STABLE)
    baseline = advance_transition(
        previous_state=None,
        previous_observation=None,
        current=baseline_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW,
    )
    first_observation = _observation(PressureBand.CROWDED, offset=1)
    first = advance_transition(
        previous_state=baseline.state,
        previous_observation=baseline_observation,
        current=first_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=10),
    )
    second_observation = _observation(PressureBand.CROWDED, offset=2)
    second = advance_transition(
        previous_state=first.state,
        previous_observation=first_observation,
        transition_anchor=baseline_observation,
        current=second_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=20),
    )

    assert baseline.event is None
    assert first.event is None
    assert first.state.candidate_band is PressureBand.CROWDED
    assert first.state.consecutive_complete_windows == 1
    assert second.event is not None
    assert second.event.consecutive_complete_windows == 2
    assert second.state.current_band is PressureBand.CROWDED
    assert second.state.notified_band is PressureBand.CROWDED

    away_1_observation = _observation(PressureBand.STABLE, offset=3)
    away_1 = advance_transition(
        previous_state=second.state,
        previous_observation=second_observation,
        current=away_1_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=30),
    )
    away_2_observation = _observation(PressureBand.STABLE, offset=4)
    away_2 = advance_transition(
        previous_state=away_1.state,
        previous_observation=away_1_observation,
        transition_anchor=second_observation,
        current=away_2_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=40),
    )
    back_1_observation = _observation(PressureBand.CROWDED, offset=5)
    back_1 = advance_transition(
        previous_state=away_2.state,
        previous_observation=away_2_observation,
        current=back_1_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=50),
    )
    back_2_observation = _observation(PressureBand.CROWDED, offset=6)
    back_2 = advance_transition(
        previous_state=back_1.state,
        previous_observation=back_1_observation,
        transition_anchor=away_2_observation,
        current=back_2_observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=60),
    )

    assert away_2.event is not None
    assert back_2.event is not None
    assert back_2.event.idempotency_key == second.event.idempotency_key
    assert back_2.state.current_band is PressureBand.CROWDED
    assert back_2.state.notified_band is PressureBand.CROWDED


def test_replayed_assessment_does_not_advance_the_streak() -> None:
    observation = _observation(PressureBand.CROWDED)
    state = BehaviorTransitionState(
        instrument_id="XSHG:603986",
        mode=PressureMode.INTRADAY_10M,
        trading_date=TRADING_DATE,
        current_band=PressureBand.STABLE,
        candidate_band=PressureBand.CROWDED,
        consecutive_complete_windows=1,
        last_assessment_id=observation.assessment_id,
        revision=2,
        updated_at=NOW,
    )

    replay = advance_transition(
        previous_state=state,
        previous_observation=observation,
        current=observation,
        trading_date=TRADING_DATE,
        updated_at=NOW + timedelta(minutes=1),
    )

    assert replay.idempotent_replay is True
    assert replay.state == state
    assert replay.event is None


def test_operator_backfill_persists_snapshot_without_rewriting_live_transition_state() -> None:
    request = BehaviorComputeRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW + timedelta(days=1),
            idempotency_key="behavior-backfill-snapshot-only-0001",
        ),
        mode=PressureMode.POST_CLOSE,
        window_start=NOW - timedelta(minutes=10),
        window_end=NOW,
        diagnostic_semantics_hash="a" * 64,
        data_manifest_hash="b" * 64,
        rights_snapshot_hash="c" * 64,
    )
    score = compose_behavior_pressure(
        cluster_scores={name: 50 for name in PressureClusterName},
        reference_raw_totals=[50] * 1000,
        reference_population_hash="d" * 64,
    )
    snapshot = materialize_snapshot(
        request=request,
        score_result=score,
        max_input_revision_known_at=NOW,
    )

    class SnapshotOnlyRepository:
        async def save_assessment(self, _request: object) -> BehaviorPressureAssessmentWriteResult:
            return BehaviorPressureAssessmentWriteResult(
                schema_version="1.0",
                snapshot=snapshot,
                created=True,
                stored_at=request.context.requested_at,
            )

        async def get_transition_state(self, _request: object) -> None:
            raise AssertionError("operator backfill must not read live transition state")

    class NoEventPublisher:
        async def publish(self, _request: object) -> None:
            raise AssertionError("operator backfill must not publish a transition")

    service = BehaviorPressureApplicationService(
        SnapshotOnlyRepository(),  # type: ignore[arg-type]
        NoEventPublisher(),  # type: ignore[arg-type]
    )

    stored = asyncio.run(
        service.persist(
            request=request,
            snapshot=snapshot,
            trading_date=TRADING_DATE,
            maintain_transition_state=False,
        )
    )

    assert stored == snapshot
