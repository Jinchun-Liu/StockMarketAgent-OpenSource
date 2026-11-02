"""Focused regression tests for point-in-time and source-eligibility boundaries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from stock_assistant.contracts.briefing_workflows import BriefingValidationRequest
from stock_assistant.contracts.briefings import Briefing, BriefingKind
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.event_workflows import EventValidationRequest
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    SourceAccessChannel,
    SourceProvenance,
    SourceRecord,
    SourceReleaseTier,
    SourceRightsStatus,
    SourceRuntimeMode,
    SourceTier,
)
from stock_assistant.contracts.memory import (
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    SemanticRule,
    SemanticRuleQueryResult,
    ShortTermMemoryWriteRequest,
)
from stock_assistant.contracts.sectors import (
    HeatDirection,
    HeatMetric,
    HeatMetricName,
    RotationOutcome,
    SectorConstituentSnapshotRef,
    SectorDefinition,
    SectorHeatSnapshot,
    SectorKind,
    SectorRotationPlan,
    SectorRotationSlot,
    SectorVisitState,
    SectorVisitWriteRequest,
)

NOW = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)


def operation_context(as_of: datetime = NOW) -> OperationContext:
    return OperationContext(schema_version="1.0", as_of=as_of)


def mutation_context(as_of: datetime = NOW) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=as_of,
        idempotency_key="domain-safety-test-0001",
    )


def sector_definition(sector_id=None) -> SectorDefinition:
    return SectorDefinition(
        schema_version="1.0",
        sector_id=sector_id or uuid4(),
        code="TEST",
        name="Synthetic sector",
        kind=SectorKind.INDUSTRY,
        taxonomy="synthetic",
        valid_from=NOW - timedelta(days=30),
        first_seen_at=NOW - timedelta(days=29),
        revision=1,
        revision_known_at=NOW - timedelta(days=29),
        evidence_ids=[uuid4()],
    )


def constituent_snapshot(sector_id) -> SectorConstituentSnapshotRef:
    return SectorConstituentSnapshotRef(
        snapshot_id=uuid4(),
        sector_id=sector_id,
        as_of=NOW,
        revision_known_at=NOW,
        constituent_count=0,
        content_hash="c" * 64,
    )


def test_configured_source_tiers_and_authority_are_contract_compatible() -> None:
    root = Path(__file__).resolve().parents[2]
    source_config = yaml.safe_load((root / "configs/sources.yaml").read_text())
    for source in source_config["sources"]:
        SourceTier(source["tier"])
        assert 0 <= float(source["authority"]) <= 1
        for content_tier in source.get("capability_tiers", {}).values():
            ContentTier(content_tier)

    scoring_config = yaml.safe_load((root / "configs/scoring.yaml").read_text())
    supported = {item.value for item in SourceTier} | {item.value for item in ContentTier}
    assert set(scoring_config["authority_priors"]).issubset(supported)


def test_social_aggregate_cannot_claim_company_fact_eligibility() -> None:
    with pytest.raises(ValidationError, match="incompatible with the content tier"):
        SourceRecord(
            source_id="social_test",
            external_id="post-cluster-1",
            capability="aggregated_sentiment",
            record_type="sentiment",
            source_tier=SourceTier.SOCIAL_AGGREGATE,
            content_tier=ContentTier.SOCIAL_AGGREGATE,
            fact_eligibility=FactEligibility.COMPANY_FACT,
            authority=0.15,
            event_time=NOW,
            first_seen_at=NOW,
            fetched_at=NOW,
            payload={},
            rights_snapshot_hash="a" * 64,
            provenance=SourceProvenance(
                schema_version="1.1",
                provider="synthetic_social",
                access_channel=SourceAccessChannel.DEMO_REPLAY,
                adapter_name="synthetic_test_adapter",
                adapter_version="1.0.0",
                endpoint_name="aggregated_sentiment",
                source_identifier="post-cluster-1",
                runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
                release_tier=SourceReleaseTier.DEV_ONLY,
                rights_status=SourceRightsStatus.UNVERIFIED,
            ),
        )


def test_event_validation_rejects_a_revision_known_after_cutoff(event_factory) -> None:
    event = event_factory().model_copy(
        update={"revision": 2, "revision_known_at": NOW + timedelta(minutes=1)}
    )
    with pytest.raises(ValidationError, match="revision cannot be known after"):
        EventValidationRequest(
            schema_version="1.0",
            context=operation_context(),
            event=event,
            ruleset_version="test-rules",
        )


def test_memory_queries_and_tier_writes_fail_closed_on_future_or_wrong_tier() -> None:
    with pytest.raises(ValidationError, match="until cannot be later"):
        MemoryQuery(
            context=operation_context(),
            query_text="synthetic query",
            until=NOW + timedelta(seconds=1),
        )

    episodic = MemoryRecord(
        schema_version="1.0",
        tier=MemoryTier.EPISODIC,
        namespace="events",
        content="synthetic memory",
        content_sha256="d" * 64,
        recorded_at=NOW,
    )
    with pytest.raises(ValidationError, match="only short_term records"):
        ShortTermMemoryWriteRequest(
            schema_version="1.0",
            context=mutation_context(),
            record=episodic,
        )


def test_semantic_rule_result_rejects_future_revision() -> None:
    rule = SemanticRule(
        rule_id="sentiment.negative",
        version=1,
        revision=2,
        name="Negative sentiment",
        polarity="negative",
        keywords=["synthetic"],
        weight=0.5,
        effective_from=NOW - timedelta(days=1),
        first_seen_at=NOW - timedelta(days=1),
        revision_known_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="known after as_of"):
        SemanticRuleQueryResult(schema_version="1.0", rules=[rule], as_of=NOW)


def test_briefing_validation_rejects_a_future_generated_briefing() -> None:
    briefing = Briefing(
        schema_version="1.0",
        kind=BriefingKind.ON_DEMAND,
        generated_at=NOW + timedelta(seconds=1),
        as_of=NOW,
        data_delay_seconds=0,
        model_version="test-model",
        prompt_hash="e" * 64,
        rule_version="test-rules",
    )
    with pytest.raises(ValidationError, match="generated in the future"):
        BriefingValidationRequest(
            schema_version="1.0",
            context=operation_context(),
            briefing=briefing,
            ruleset_version="test-rules",
        )


def test_sector_heat_rejects_metrics_observed_after_snapshot_cutoff() -> None:
    sector = sector_definition()
    metric = HeatMetric(
        name=HeatMetricName.TURNOVER,
        raw_value=Decimal("1"),
        unit="CNY",
        normalized_score=0.5,
        weight=1,
        observed_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match="metrics cannot be observed after"):
        SectorHeatSnapshot(
            schema_version="1.0",
            sector=sector,
            constituent_snapshot=constituent_snapshot(sector.sector_id),
            as_of=NOW,
            lookback_minutes=10,
            heat_score=0.5,
            direction=HeatDirection.HEATING,
            metrics=[metric],
            evidence_ids=[uuid4()],
            calculator_version="test-calculator",
            data_watermark=NOW,
        )


def test_rotation_allows_later_cycle_revisit_but_rejects_adjacent_repeat() -> None:
    first_sector = uuid4()
    second_sector = uuid4()

    def slot(sector_id, minute: int) -> SectorRotationSlot:
        return SectorRotationSlot(
            sector_id=sector_id,
            scheduled_at=NOW + timedelta(minutes=minute),
            priority_score=0.5,
            fairness_score=0.5,
            heat_score=0,
            reason_code="FAIR_ROTATION",
        )

    SectorRotationPlan(
        schema_version="1.0",
        generated_at=NOW,
        valid_until=NOW + timedelta(minutes=40),
        slots=[slot(first_sector, 0), slot(second_sector, 10), slot(first_sector, 20)],
        interval_seconds=600,
        policy_id="fair_rotation",
        policy_version="1.0",
    )
    with pytest.raises(ValidationError, match="adjacent rotation slots"):
        SectorRotationPlan(
            schema_version="1.0",
            generated_at=NOW,
            valid_until=NOW + timedelta(minutes=40),
            slots=[slot(first_sector, 0), slot(first_sector, 10)],
            interval_seconds=600,
            policy_id="fair_rotation",
            policy_version="1.0",
        )


def test_visit_state_distinguishes_active_start_from_previous_completion() -> None:
    SectorVisitState(
        sector_id=uuid4(),
        active_started_at=NOW - timedelta(minutes=5),
        last_completed_at=NOW - timedelta(minutes=20),
        completed_visits=3,
        state_revision=4,
        revision_known_at=NOW,
    )
    with pytest.raises(ValidationError, match="completed_at"):
        SectorVisitWriteRequest(
            schema_version="1.0",
            context=mutation_context(),
            plan_id=uuid4(),
            sector_id=uuid4(),
            started_at=NOW - timedelta(minutes=1),
            outcome=RotationOutcome.FAILED,
            reason_code="SOURCE_TIMEOUT",
        )
