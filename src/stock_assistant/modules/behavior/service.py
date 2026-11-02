"""Application orchestration for materializing behavior snapshots and transitions."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid5

from stock_assistant.contracts.behavior import (
    BehaviorBandTransitionV1,
    BehaviorComputeRequest,
    BehaviorPressureAssessmentWriteRequest,
    BehaviorPressureQuery,
    BehaviorPressureSnapshotV1_0,
    BehaviorTransitionStateLookup,
    BehaviorTransitionStateWriteRequest,
    HistoricalAnalogState,
    HistoricalAnalogSummary,
    MarketStructureDimension,
    PitLineage,
    PressureClusterName,
    PressureMode,
    StructureAvailability,
    StructureDimensionName,
)
from stock_assistant.contracts.common import InstrumentRef
from stock_assistant.contracts.messaging import MessageEnvelope, MessagePublishRequest
from stock_assistant.modules.behavior.scoring import BehaviorScoreResult
from stock_assistant.modules.behavior.transitions import (
    TransitionObservation,
    advance_transition,
)
from stock_assistant.ports.messaging import MessagePublisherPort
from stock_assistant.ports.persistence import BehaviorPressureRepositoryPort

_ASSESSMENT_NAMESPACE = UUID("eb78a05d-9238-5bc8-84de-96fe7810e3c1")


def _assessment_id(request: BehaviorComputeRequest) -> UUID:
    identity = "\n".join(
        (
            request.instrument_id,
            request.mode.value,
            request.window_end.isoformat(),
            request.formula_version,
            request.diagnostic_semantics_hash,
            request.data_manifest_hash,
        )
    )
    return uuid5(_ASSESSMENT_NAMESPACE, identity)


def _structure_dimensions(
    *, mode: PressureMode, score_result: BehaviorScoreResult
) -> list[MarketStructureDimension]:
    scores = {item.cluster: item.score for item in score_result.clusters}
    source = {
        StructureDimensionName.TREND_ORGANIZATION: scores[PressureClusterName.SHORT_PATH_EXTENSION],
        StructureDimensionName.POSITION_RECOVERY: scores[
            PressureClusterName.REFERENCE_COST_EXTENSION
        ],
        StructureDimensionName.TRADING_PARTICIPATION: scores[PressureClusterName.ACTIVITY_ANOMALY],
        StructureDimensionName.VOLATILITY_ENVIRONMENT: scores[
            PressureClusterName.VOLATILITY_EXPANSION
        ],
        StructureDimensionName.INTRADAY_ACCEPTANCE: scores[
            PressureClusterName.CLOSE_PATH_REJECTION
        ],
        StructureDimensionName.RELATIVE_ENVIRONMENT: scores[PressureClusterName.BREADTH_BACKGROUND],
    }
    result: list[MarketStructureDimension] = []
    for dimension in StructureDimensionName:
        if (
            dimension is StructureDimensionName.INTRADAY_ACCEPTANCE
            and mode is PressureMode.POST_CLOSE
        ):
            result.append(
                MarketStructureDimension(
                    dimension=dimension,
                    availability=StructureAvailability.NOT_APPLICABLE,
                    explanation="盘后确认值不沿用盘中接受状态。",
                )
            )
            continue
        value = source[dimension]
        if value is None:
            result.append(
                MarketStructureDimension(
                    dimension=dimension,
                    availability=StructureAvailability.ABSTAINED,
                    explanation="核心输入不足; 本维度暂不判断。",
                )
            )
            continue
        state = "偏高" if value >= 60 else "常态" if value >= 40 else "偏低"
        result.append(
            MarketStructureDimension(
                dimension=dimension,
                availability=StructureAvailability.READY,
                state=state,
                explanation="由同一窗口的冻结计分簇确定性归纳。",
            )
        )
    return result


def materialize_snapshot(
    *,
    request: BehaviorComputeRequest,
    score_result: BehaviorScoreResult,
    max_input_revision_known_at: datetime,
    historical_analogs: HistoricalAnalogSummary | None = None,
) -> BehaviorPressureSnapshotV1_0:
    """Build one stable snapshot; all non-scored states remain explicit."""

    if max_input_revision_known_at.tzinfo is None:
        raise ValueError("max_input_revision_known_at must be timezone-aware")
    if request.mode is PressureMode.POST_CLOSE and historical_analogs is None:
        historical_analogs = HistoricalAnalogSummary(
            state=HistoricalAnalogState.INSUFFICIENT,
            sample_count=0,
            reason_code="ANALOG_SAMPLE_INSUFFICIENT",
            reason="尚无足够的可比历史样本。",
            selection_as_of=request.context.as_of,
            selection_semantics_hash=request.diagnostic_semantics_hash,
        )
    return BehaviorPressureSnapshotV1_0(
        schema_version="1.0",
        assessment_id=_assessment_id(request),
        instrument=InstrumentRef(
            instrument_id="XSHG:603986",
            symbol="603986",
            exchange="XSHG",
            market="CN",
            asset_class="equity",
            currency="CNY",
        ),
        mode=request.mode,
        score=score_result.score,
        band=score_result.band,
        availability=score_result.availability,
        score_mapping=score_result.score_mapping,
        window_start=request.window_start,
        window_end=request.window_end,
        as_of=request.context.as_of,
        generated_at=request.context.as_of,
        facets=list(score_result.facets),
        clusters=list(score_result.clusters),
        structure_dimensions=_structure_dimensions(mode=request.mode, score_result=score_result),
        drivers=list(score_result.drivers),
        mitigators=list(score_result.mitigators),
        historical_analogs=historical_analogs,
        completeness_percent=score_result.completeness_percent,
        pit_lineage=PitLineage(
            data_manifest_hash=request.data_manifest_hash,
            rights_snapshot_hash=request.rights_snapshot_hash,
            comparison_semantics_hash=request.diagnostic_semantics_hash,
            max_input_revision_known_at=max_input_revision_known_at,
            assessment_revision_known_at=request.context.as_of,
            comparable_instrument_day_count=(
                score_result.score_mapping.population_count
                if score_result.score_mapping is not None
                else 0
            ),
        ),
    )


def _observation(snapshot: BehaviorPressureSnapshotV1_0) -> TransitionObservation | None:
    if snapshot.band is None:
        return None
    return TransitionObservation(
        assessment_id=snapshot.assessment_id,
        band=snapshot.band,
        mode=snapshot.mode,
        effective_at=snapshot.window_end,
        comparison_semantics_hash=snapshot.pit_lineage.comparison_semantics_hash,
        data_manifest_hash=snapshot.pit_lineage.data_manifest_hash,
        rights_snapshot_hash=snapshot.pit_lineage.rights_snapshot_hash,
        max_input_revision_known_at=snapshot.pit_lineage.max_input_revision_known_at,
    )


class BehaviorPressureApplicationService:
    """Persist a snapshot and its transition/outbox effects through frozen ports."""

    def __init__(
        self,
        repository: BehaviorPressureRepositoryPort,
        publisher: MessagePublisherPort[BehaviorBandTransitionV1],
    ) -> None:
        self._repository = repository
        self._publisher = publisher

    async def persist(
        self,
        *,
        request: BehaviorComputeRequest,
        snapshot: BehaviorPressureSnapshotV1_0,
        trading_date: date,
        maintain_transition_state: bool = True,
    ) -> BehaviorPressureSnapshotV1_0:
        stored = await self._repository.save_assessment(
            BehaviorPressureAssessmentWriteRequest(
                schema_version="1.0",
                context=request.context,
                snapshot=snapshot,
            )
        )
        current = _observation(stored.snapshot)
        if current is None or not maintain_transition_state:
            return stored.snapshot
        lookup = await self._repository.get_transition_state(
            BehaviorTransitionStateLookup(
                schema_version="1.0",
                context=request.context,
                mode=snapshot.mode,
                trading_date=trading_date,
            )
        )
        previous_observation: TransitionObservation | None = None
        transition_anchor: TransitionObservation | None = None
        if lookup.state is not None and lookup.state.last_assessment_id is not None:
            page = await self._repository.query_assessments(
                BehaviorPressureQuery(
                    schema_version="1.0",
                    context=request.context,
                    mode=snapshot.mode,
                    limit=100,
                )
            )
            previous = next(
                (
                    item
                    for item in page.items
                    if item.assessment_id == lookup.state.last_assessment_id
                ),
                None,
            )
            if previous is None:
                raise ValueError("transition anchor assessment is not visible at the cutoff")
            previous_observation = _observation(previous)
            if lookup.state.current_band is not None:
                anchor = next(
                    (
                        item
                        for item in page.items
                        if item.assessment_id != stored.snapshot.assessment_id
                        and item.band is lookup.state.current_band
                        and item.pit_lineage.comparison_semantics_hash
                        == current.comparison_semantics_hash
                    ),
                    None,
                )
                transition_anchor = _observation(anchor) if anchor is not None else None
        decision = advance_transition(
            previous_state=lookup.state,
            previous_observation=previous_observation,
            transition_anchor=transition_anchor,
            current=current,
            trading_date=trading_date,
            updated_at=request.context.as_of,
        )
        if decision.idempotent_replay:
            return stored.snapshot
        transition_context = request.context.model_copy(
            update={
                "idempotency_key": (
                    f"{request.context.idempotency_key}:transition:{decision.state.revision}"
                )[:200]
            }
        )
        await self._repository.save_transition_state(
            BehaviorTransitionStateWriteRequest(
                schema_version="1.0",
                context=transition_context,
                state=decision.state,
                expected_revision=lookup.state.revision if lookup.state else None,
            )
        )
        if decision.event is not None:
            event_context = request.context.model_copy(
                update={"idempotency_key": decision.event.idempotency_key}
            )
            await self._publisher.publish(
                MessagePublishRequest(
                    schema_version="1.0",
                    context=event_context,
                    envelope=MessageEnvelope(
                        schema_version="1.0",
                        message_id=decision.event.event_id,
                        event_name="com.stockassistant.behavior.band_transition.v1",
                        producer="behavior_worker",
                        occurred_at=decision.event.effective_at,
                        produced_at=request.context.as_of,
                        correlation_id=request.context.correlation_id,
                        causation_id=request.context.request_id,
                        partition_key=request.instrument_id,
                        idempotency_key=decision.event.idempotency_key,
                        payload=decision.event,
                    ),
                )
            )
        return stored.snapshot
