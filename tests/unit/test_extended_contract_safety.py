"""High-value regression tests for contract safety and point-in-time semantics."""

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import (
    CursorPage,
    InstrumentRef,
    MoneyValue,
    MutationContext,
    OperationContext,
)
from stock_assistant.contracts.content import (
    AuditOpinion,
    BrokerResearchReport,
    ContentNormalizationRequest,
    ContentNormalizationResult,
    FinancialMetric,
    FinancialReport,
    NormalizedContentKind,
    ReportingPeriodKind,
    ResearchRating,
    SocialSentimentAggregate,
    StatementScope,
)
from stock_assistant.contracts.events import EvidenceRef
from stock_assistant.contracts.forecasting import (
    FeatureObservation,
    ForecastContribution,
    ForecastOutcome,
    ForecastOutcomeObservationRequest,
    ForecastOutcomeObservationResult,
    ForecastRequest,
    FusedForecast,
    FusionRequest,
    MarketBar,
    OutcomeStatus,
    QuantitativeForecast,
    ReturnQuantiles,
    SignalDirection,
    TextSignal,
    TextSignalBatch,
    TextSignalRequest,
)
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
from stock_assistant.contracts.jobs import (
    JobAccepted,
    JobFailure,
    JobRequest,
    JobResult,
    JobStatus,
    ScheduleCreateRequest,
    ScheduledJob,
    ScheduledJobTemplate,
    ScheduleKind,
    ScheduleTrigger,
    ScheduleUpdateRequest,
)
from stock_assistant.contracts.models import (
    DatasetPurpose,
    DatasetSplitManifest,
    DatasetSplitName,
    EvaluationMetric,
    EvaluationOutcome,
    LoRAConfig,
    ModelCapability,
    ModelEvaluationReport,
    ModelKind,
    ModelManifest,
    ModelPromotionRequest,
    ModelPromotionResult,
    ModelRegistrationRequest,
    ModelRegistryRecord,
    ModelStage,
    QuantizationFormat,
    TrainingArtifact,
    TrainingArtifactKind,
    TrainingDatasetManifest,
    TrainingRunRecord,
    TrainingRunStatus,
)
from stock_assistant.contracts.quality import (
    ContentDisposition,
    QualityAssessment,
    QualityAssessmentRequest,
    QualityAssessmentResult,
    QualityFactor,
    QualityFactorName,
)
from stock_assistant.contracts.storage import RetentionClass, StoredObjectRef
from stock_assistant.ports.quality import ContentQualityPort

NOW = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)
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


class _JobPayload(ContractModel):
    operation: str


def _operation_context(as_of: datetime = NOW) -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        as_of=as_of,
        requested_at=as_of,
    )


def _mutation_context(as_of: datetime = NOW) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=as_of,
        requested_at=as_of,
        idempotency_key="extended-contract-test-0001",
    )


def _evidence(
    *,
    content_tier: ContentTier,
    fact_eligibility: FactEligibility,
    source_id: str,
    title: str = "Synthetic evidence",
) -> EvidenceRef:
    return EvidenceRef(
        source_id=source_id,
        source_url=f"https://example.invalid/{source_id}/1",
        title=title,
        published_at=NOW - timedelta(hours=2),
        first_seen_at=NOW - timedelta(minutes=50),
        content_sha256="a" * 64,
        license_id="synthetic-test-license",
        rights_snapshot_hash="b" * 64,
        content_tier=content_tier,
        fact_eligibility=fact_eligibility,
    )


def _content_fields(
    *,
    source_record_id: UUID,
    source_id: str,
    content_kind: NormalizedContentKind,
    content_tier: ContentTier,
    fact_eligibility: FactEligibility,
    evidence: EvidenceRef,
    instruments: list[InstrumentRef] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "source_record_id": source_record_id,
        "source_id": source_id,
        "external_id": f"{source_id}-external-1",
        "content_kind": content_kind,
        "content_tier": content_tier,
        "fact_eligibility": fact_eligibility,
        "authority": 0.8,
        "title": "Synthetic normalized content",
        "published_at": NOW - timedelta(hours=2),
        "first_seen_at": NOW - timedelta(minutes=50),
        "revision_known_at": NOW - timedelta(minutes=40),
        "revision": 1,
        "instruments": instruments if instruments is not None else [INSTRUMENT],
        "entities": [],
        "evidence": [evidence],
        "content_sha256": "c" * 64,
        "rights_snapshot_hash": "b" * 64,
    }


def _social_content(
    *,
    source_record_id: UUID | None = None,
    instruments: list[InstrumentRef] | None = None,
) -> SocialSentimentAggregate:
    evidence = _evidence(
        content_tier=ContentTier.SOCIAL_AGGREGATE,
        fact_eligibility=FactEligibility.SENTIMENT_ONLY,
        source_id="social_test",
    )
    return SocialSentimentAggregate(
        **_content_fields(
            source_record_id=source_record_id or uuid4(),
            source_id="social_test",
            content_kind=NormalizedContentKind.SOCIAL_SENTIMENT_AGGREGATE,
            content_tier=ContentTier.SOCIAL_AGGREGATE,
            fact_eligibility=FactEligibility.SENTIMENT_ONLY,
            evidence=evidence,
            instruments=instruments,
        ),
        window_started_at=NOW - timedelta(hours=2),
        window_ended_at=NOW - timedelta(hours=1),
        sample_size=200,
        sentiment_score=0.2,
        confidence=0.75,
        positive_share=0.5,
        negative_share=0.2,
        neutral_share=0.3,
        aggregation_method="synthetic_aggregate",
        aggregation_version="1.0.0",
        contains_individual_content=False,
        contains_personal_identifiers=False,
    )


def _market_bar(**updates: object) -> MarketBar:
    values: dict[str, object] = {
        "instrument": INSTRUMENT,
        "interval": "1min",
        "opened_at": NOW - timedelta(minutes=3),
        "closed_at": NOW - timedelta(minutes=2),
        "received_at": NOW - timedelta(minutes=1),
        "open_price": "10.00",
        "high_price": "10.20",
        "low_price": "9.90",
        "close_price": "10.10",
        "volume": "1000",
        "adjusted": False,
        "source_id": "market_test",
        "data_version": "quotes-v1",
        "adjustment_version": "raw-v1",
        "rights_snapshot_hash": "d" * 64,
    }
    values.update(updates)
    return MarketBar.model_validate(values)


def _feature(**updates: object) -> FeatureObservation:
    values: dict[str, object] = {
        "name": "volume_ratio",
        "value": "1.25",
        "observed_at": NOW - timedelta(minutes=10),
        "available_at": NOW - timedelta(minutes=9),
        "feature_version": "features-v1",
        "data_version": "quotes-v1",
    }
    values.update(updates)
    return FeatureObservation.model_validate(values)


def _forecast_request(
    *,
    bars: list[MarketBar] | None = None,
    features: list[FeatureObservation] | None = None,
) -> ForecastRequest:
    return ForecastRequest(
        schema_version="1.0",
        context=_operation_context(),
        instrument=INSTRUMENT,
        horizon_minutes=10,
        bars=bars if bars is not None else [_market_bar()],
        features=features if features is not None else [_feature()],
        data_snapshot_id=uuid4(),
        data_manifest_hash="e" * 64,
        feature_set_version="features-v1",
    )


def _quantitative_forecast(**updates: object) -> QuantitativeForecast:
    values: dict[str, object] = {
        "instrument": INSTRUMENT,
        "as_of": NOW,
        "horizon_minutes": 10,
        "probability_up": 0.55,
        "returns": ReturnQuantiles(q10="-0.02", q50="0.01", q90="0.04"),
        "model_name": "synthetic-quant",
        "model_version": "1.0.0",
        "feature_set_version": "features-v1",
        "data_snapshot_id": uuid4(),
        "training_cutoff": NOW - timedelta(days=1),
        "generated_at": NOW + timedelta(seconds=1),
        "calibration_version": "1.0.0",
        "code_revision": "f" * 40,
        "latency_ms": 1,
        "is_paper_only": True,
    }
    values.update(updates)
    return QuantitativeForecast.model_validate(values)


def _text_signal(*, as_of: datetime = NOW) -> TextSignal:
    return TextSignal(
        instrument=INSTRUMENT,
        as_of=as_of,
        direction=SignalDirection.NEUTRAL,
        signed_score=0,
        confidence=0.6,
        materiality=0.4,
        content_ids=[uuid4()],
        evidence_ids=[uuid4()],
        model_version="text-model-v1",
        rule_version="rules-v1",
        generated_at=NOW + timedelta(seconds=1),
        is_paper_only=True,
    )


def _source_record(*, first_seen_at: datetime) -> SourceRecord:
    return SourceRecord(
        source_id="exchange_test",
        external_id="announcement-1",
        capability="announcements",
        record_type="announcement",
        source_tier=SourceTier.STATUTORY_DISCLOSURE,
        content_tier=ContentTier.STATUTORY_DISCLOSURE,
        fact_eligibility=FactEligibility.COMPANY_FACT,
        authority=1.0,
        title="Synthetic announcement",
        event_time=first_seen_at - timedelta(minutes=1),
        published_at=first_seen_at - timedelta(minutes=1),
        first_seen_at=first_seen_at,
        fetched_at=first_seen_at,
        payload={},
        rights_snapshot_hash="a" * 64,
        provenance=SourceProvenance(
            schema_version="1.1",
            provider="synthetic_exchange",
            access_channel=SourceAccessChannel.AUTHORIZED_API,
            adapter_name="synthetic_test_adapter",
            adapter_version="1.0.0",
            endpoint_name="exchange_announcements",
            source_identifier="announcement-1",
            runtime_mode=SourceRuntimeMode.AUTHORIZED,
            release_tier=SourceReleaseTier.AUTHORIZED,
            rights_status=SourceRightsStatus.APPROVED,
        ),
    )


def _forecast_outcome(**updates: object) -> ForecastOutcome:
    values: dict[str, object] = {
        "schema_version": "1.0",
        "prediction_id": uuid4(),
        "instrument": INSTRUMENT,
        "prediction_as_of": NOW - timedelta(hours=1),
        "horizon_ends_at": NOW - timedelta(minutes=30),
        "outcome_observed_at": NOW - timedelta(minutes=20),
        "label_available_at": NOW - timedelta(minutes=10),
        "benchmark_id": "csi300",
        "realized_return": "0.05",
        "benchmark_return": "0.01",
        "excess_return": "0.04",
        "source_id": "market_test",
        "data_version": "settlement-v1",
        "rights_snapshot_hash": "f" * 64,
        "evidence_ids": [uuid4()],
    }
    values.update(updates)
    return ForecastOutcome.model_validate(values)


def _job_template() -> ScheduledJobTemplate[_JobPayload]:
    return ScheduledJobTemplate[_JobPayload](
        schema_version="1.0",
        job_name="stock_assistant.tests.run.v1",
        payload=_JobPayload(operation="synthetic"),
        idempotency_scope="synthetic-schedule",
    )


def _stored_object(
    *,
    object_key: str,
    content_sha256: str = "1" * 64,
) -> StoredObjectRef:
    return StoredObjectRef(
        schema_version="1.0",
        namespace="training",
        object_key=object_key,
        media_type="application/json",
        content_length=128,
        content_sha256=content_sha256,
        stored_at=NOW - timedelta(days=40),
        retention_class=RetentionClass.AUDIT,
        rights_snapshot_hash="2" * 64,
    )


def _dataset_split(
    split: DatasetSplitName,
    *,
    time_from: datetime,
    time_until: datetime,
    object_key: str,
    label_available_until: datetime | None,
) -> DatasetSplitManifest:
    artifact = _stored_object(object_key=object_key)
    return DatasetSplitManifest(
        split=split,
        record_count=10,
        time_from=time_from,
        time_until=time_until,
        knowledge_cutoff=NOW - timedelta(days=1),
        label_available_until=label_available_until,
        artifact=artifact,
        content_sha256=artifact.content_sha256,
    )


def _valid_splits() -> list[DatasetSplitManifest]:
    return [
        _dataset_split(
            DatasetSplitName.TRAIN,
            time_from=NOW - timedelta(days=30),
            time_until=NOW - timedelta(days=20),
            object_key="datasets/train.jsonl",
            label_available_until=NOW - timedelta(days=20),
        ),
        _dataset_split(
            DatasetSplitName.VALIDATION,
            time_from=NOW - timedelta(days=19),
            time_until=NOW - timedelta(days=10),
            object_key="datasets/validation.jsonl",
            label_available_until=NOW - timedelta(days=10),
        ),
        _dataset_split(
            DatasetSplitName.TEST,
            time_from=NOW - timedelta(days=8),
            time_until=NOW - timedelta(days=2),
            object_key="datasets/test.jsonl",
            label_available_until=NOW - timedelta(days=2),
        ),
    ]


def _dataset_manifest(
    splits: list[DatasetSplitManifest],
    *,
    purge_gap_minutes: int = 60,
    embargo_minutes: int = 60,
) -> TrainingDatasetManifest:
    return TrainingDatasetManifest(
        schema_version="1.0",
        name="synthetic-financial-dataset",
        dataset_version="1.0.0",
        purpose=DatasetPurpose.FINANCIAL_DOMAIN_SFT,
        created_at=NOW,
        data_cutoff=NOW - timedelta(days=1),
        splits=splits,
        total_record_count=30,
        source_ids=["synthetic_exchange"],
        evidence_count=0,
        schema_hash="3" * 64,
        content_hash="4" * 64,
        rights_snapshot_hash="5" * 64,
        license_ids=["synthetic-test-license"],
        preprocessing_version="1.0.0",
        deduplication_version="1.0.0",
        split_strategy="temporal",
        purge_gap_minutes=purge_gap_minutes,
        embargo_minutes=embargo_minutes,
        training_authorized=True,
    )


def _lora_config() -> LoRAConfig:
    return LoRAConfig(
        rank=8,
        alpha=16,
        dropout="0.05",
        target_modules=["q_proj", "v_proj"],
        learning_rate="0.0002",
        epochs="1",
        per_device_batch_size=2,
        gradient_accumulation_steps=1,
        maximum_sequence_length=2048,
        quantization=QuantizationFormat.NONE,
    )


def _training_artifact(
    *,
    object_key: str,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> TrainingArtifact:
    return TrainingArtifact(
        kind=TrainingArtifactKind.LORA_ADAPTER,
        reference=_stored_object(object_key=object_key),
        role="adapter",
        format="safetensors",
        shard_index=shard_index,
        shard_count=shard_count,
        framework="peft",
        framework_version="1.0.0",
    )


def _model_manifest(artifacts: list[TrainingArtifact]) -> ModelManifest:
    return ModelManifest(
        schema_version="1.0",
        name="synthetic-financial-model",
        model_version="1.0.0",
        kind=ModelKind.GENERATIVE,
        artifacts=artifacts,
        capabilities=[
            ModelCapability(
                capability_id="financial_generation",
                contract_version="1.0",
                maximum_context_tokens=4096,
                local_only=True,
            )
        ],
        quantization=QuantizationFormat.NONE,
        created_at=NOW,
        code_revision="6" * 40,
        environment_lock_hash="7" * 64,
        manifest_hash="8" * 64,
        license_ids=["synthetic-model-license"],
        rights_snapshot_hash="9" * 64,
        paper_only=True,
    )


def _model_record(
    *,
    stage: ModelStage,
    evaluation_ids: list[UUID] | None = None,
) -> ModelRegistryRecord:
    manifest = _model_manifest(
        [
            _training_artifact(
                object_key=f"models/{stage.value}.safetensors",
                shard_index=0,
                shard_count=1,
            )
        ]
    )
    return ModelRegistryRecord(
        schema_version="1.0",
        manifest=manifest,
        stage=stage,
        revision=2,
        registered_at=NOW - timedelta(days=1),
        stage_changed_at=NOW,
        evaluation_ids=evaluation_ids or [],
    )


def _quality_factors() -> list[QualityFactor]:
    values = (
        (QualityFactorName.AUTHORITY, 0.9, 0.25),
        (QualityFactorName.RELEVANCE, 0.8, 0.20),
        (QualityFactorName.FRESHNESS, 0.7, 0.15),
        (QualityFactorName.CORROBORATION, 0.6, 0.15),
        (QualityFactorName.COMPLETENESS, 0.8, 0.25),
        (QualityFactorName.CONFLICT_PENALTY, 0.2, -0.10),
    )
    return [
        QualityFactor(name=name, score=score, weight=weight, contribution=score * weight)
        for name, score, weight in values
    ]


def _quality_assessment(
    *,
    content_tier: ContentTier,
    fact_eligibility: FactEligibility,
    fact_eligible: bool,
    may_trigger_notification_alone: bool,
    disposition: ContentDisposition = ContentDisposition.RETAIN_ONLY,
    factors: list[QualityFactor] | None = None,
    total_score: float | None = None,
) -> QualityAssessment:
    selected_factors = factors if factors is not None else _quality_factors()
    expected_total = max(
        0.0,
        min(1.0, sum(item.contribution for item in selected_factors)),
    )
    return QualityAssessment(
        schema_version="1.0",
        content_id=uuid4(),
        content_tier=content_tier,
        fact_eligibility=fact_eligibility,
        factors=selected_factors,
        total_score=expected_total if total_score is None else total_score,
        disposition=disposition,
        fact_eligible=fact_eligible,
        may_trigger_notification_alone=may_trigger_notification_alone,
        scoring_policy_id="quality.default",
        scoring_policy_version="1.0.0",
        assessed_at=NOW,
        reason_codes=["SYNTHETIC_ASSESSMENT"],
    )


def test_versioned_contracts_require_known_explicit_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version is required"):
        OperationContext.model_validate({"as_of": NOW, "requested_at": NOW})
    with pytest.raises(ValidationError):
        OperationContext.model_validate(
            {"schema_version": "2.0", "as_of": NOW, "requested_at": NOW}
        )

    assert "schema_version" in OperationContext.model_json_schema()["required"]


def test_contract_strings_preserve_intentional_boundary_whitespace() -> None:
    evidence = _evidence(
        content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
        fact_eligibility=FactEligibility.CONTEXT_ONLY,
        source_id="broker_test",
        title="  Evidence title with intentional spacing  ",
    )
    report = BrokerResearchReport(
        **_content_fields(
            source_record_id=uuid4(),
            source_id="broker_test",
            content_kind=NormalizedContentKind.BROKER_RESEARCH,
            content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
            fact_eligibility=FactEligibility.CONTEXT_ONLY,
            evidence=evidence,
        ),
        institution_id="broker-1",
        institution_name="Synthetic Broker",
        analyst_names=["Synthetic Analyst"],
        rating=ResearchRating.BUY,
        target_price_low=MoneyValue(amount="10.00", currency="CNY"),
        target_price_high=MoneyValue(amount="12.00", currency="CNY"),
        summary="  Body text with intentional spacing  ",
    )

    assert report.evidence[0].title == "  Evidence title with intentional spacing  "
    assert report.summary == "  Body text with intentional spacing  "


@pytest.mark.parametrize("unsafe_value", [1, 1.0])
def test_decimal_string_rejects_json_numbers(unsafe_value: object) -> None:
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        MoneyValue(amount=unsafe_value, currency="CNY")


def test_decimal_string_accepts_exact_string_and_decimal_values() -> None:
    from_string = MoneyValue(amount="1.2300", currency="CNY")
    from_decimal = MoneyValue(amount=Decimal("2.3400"), currency="CNY")

    assert from_string.amount == Decimal("1.2300")
    assert from_decimal.amount == Decimal("2.3400")
    assert '"amount":"1.2300"' in from_string.model_dump_json()


def test_market_bar_requires_received_order_and_reproducibility_versions() -> None:
    bar = _market_bar()
    assert bar.data_version == "quotes-v1"
    assert bar.adjustment_version == "raw-v1"

    with pytest.raises(ValidationError, match="received_at cannot be earlier"):
        _market_bar(received_at=NOW - timedelta(minutes=4))

    for required_field in ("data_version", "adjustment_version"):
        raw = bar.model_dump()
        raw.pop(required_field)
        with pytest.raises(ValidationError):
            MarketBar.model_validate(raw)


def test_feature_availability_and_forecast_cutoff_reject_future_inputs() -> None:
    valid = _forecast_request()
    assert valid.bars[0].received_at <= valid.context.as_of
    assert valid.features[0].available_at <= valid.context.as_of

    with pytest.raises(ValidationError, match="available_at cannot be earlier"):
        _feature(available_at=NOW - timedelta(minutes=11))

    future_bar = _market_bar(received_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="bars received after"):
        _forecast_request(bars=[future_bar])

    future_feature = _feature(available_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="features after"):
        _forecast_request(features=[future_feature])


def test_social_aggregate_can_feed_text_signal_but_not_cross_pit_or_security_scope() -> None:
    social = _social_content()
    request = TextSignalRequest(
        schema_version="1.0",
        context=_operation_context(),
        instrument=INSTRUMENT,
        contents=[social],
        horizon_minutes=10,
        rule_version="rules-v1",
    )
    assert request.contents[0].fact_eligibility is FactEligibility.SENTIMENT_ONLY

    future_social = social.model_copy(update={"revision_known_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValidationError, match="cannot be known after"):
        TextSignalRequest(
            schema_version="1.0",
            context=_operation_context(),
            instrument=INSTRUMENT,
            contents=[future_social],
            horizon_minutes=10,
            rule_version="rules-v1",
        )

    with pytest.raises(ValidationError, match="must match the request"):
        TextSignalRequest(
            schema_version="1.0",
            context=_operation_context(),
            instrument=OTHER_INSTRUMENT,
            contents=[social],
            horizon_minutes=10,
            rule_version="rules-v1",
        )


def test_text_signal_batch_enforces_one_point_in_time_cutoff() -> None:
    signal = _text_signal()
    batch = TextSignalBatch(
        schema_version="1.0",
        signals=[signal],
        as_of=NOW,
        generated_at=NOW + timedelta(seconds=2),
        analyzer_version="text-analyzer-v1",
    )
    assert batch.signals == [signal]

    mismatched = _text_signal(as_of=NOW - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="must match the batch as_of"):
        TextSignalBatch(
            schema_version="1.0",
            signals=[mismatched],
            as_of=NOW,
            generated_at=NOW + timedelta(seconds=2),
            analyzer_version="text-analyzer-v1",
        )


def test_forecast_outcome_enforces_timeline_and_return_arithmetic() -> None:
    outcome = _forecast_outcome()
    assert outcome.excess_return == Decimal("0.04")

    invalid_updates = (
        {"horizon_ends_at": outcome.prediction_as_of},
        {"outcome_observed_at": outcome.horizon_ends_at - timedelta(seconds=1)},
        {"label_available_at": outcome.outcome_observed_at - timedelta(seconds=1)},
        {"excess_return": "0.03"},
    )
    for updates in invalid_updates:
        with pytest.raises(ValidationError):
            _forecast_outcome(**updates)


def test_forecast_outcome_result_rejects_label_unavailable_at_cutoff() -> None:
    outcome = _forecast_outcome()
    ForecastOutcomeObservationResult(
        schema_version="1.0",
        prediction_id=outcome.prediction_id,
        status=OutcomeStatus.OBSERVED,
        outcome=outcome,
        as_of=outcome.label_available_at,
        reason_code="OUTCOME_OBSERVED",
        settlement_version="1.0.0",
    )

    with pytest.raises(ValidationError, match="label cannot be available after"):
        ForecastOutcomeObservationResult(
            schema_version="1.0",
            prediction_id=outcome.prediction_id,
            status=OutcomeStatus.OBSERVED,
            outcome=outcome,
            as_of=outcome.label_available_at - timedelta(seconds=1),
            reason_code="OUTCOME_OBSERVED",
            settlement_version="1.0.0",
        )


def test_scheduled_template_excludes_execution_identity_and_cutoff() -> None:
    template_type = ScheduledJobTemplate[_JobPayload]
    assert {"job_id", "context", "as_of", "request_id"}.isdisjoint(template_type.model_fields)

    template = _job_template()
    assert template.idempotency_scope == "synthetic-schedule"


def test_schedule_create_and_update_express_optimistic_revision_boundary() -> None:
    template = _job_template()
    trigger = ScheduleTrigger(kind=ScheduleKind.INTERVAL, interval_seconds=600)
    ScheduleCreateRequest[_JobPayload](
        schema_version="1.0",
        context=_mutation_context(),
        schedule_name="synthetic_schedule",
        job_template=template,
        trigger=trigger,
    )
    assert "expected_revision" not in ScheduleCreateRequest[_JobPayload].model_fields

    update_fields: dict[str, object] = {
        "schema_version": "1.0",
        "context": _mutation_context(),
        "schedule_id": uuid4(),
        "schedule_name": "synthetic_schedule",
        "job_template": template,
        "trigger": trigger,
        "enabled": True,
    }
    with pytest.raises(ValidationError):
        ScheduleUpdateRequest[_JobPayload].model_validate(update_fields)
    with pytest.raises(ValidationError):
        ScheduleUpdateRequest[_JobPayload].model_validate(update_fields | {"expected_revision": 0})

    updated = ScheduleUpdateRequest[_JobPayload].model_validate(
        update_fields | {"expected_revision": 1}
    )
    assert updated.expected_revision == 1


def test_financial_report_requires_attached_unique_well_ordered_metrics() -> None:
    evidence = _evidence(
        content_tier=ContentTier.STATUTORY_DISCLOSURE,
        fact_eligibility=FactEligibility.COMPANY_FACT,
        source_id="exchange_test",
    )
    metric = FinancialMetric(
        metric_id="revenue",
        label="Revenue",
        value="100.00",
        unit="CNY",
        currency="CNY",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 30),
        evidence_ids=[evidence.evidence_id],
    )
    base = _content_fields(
        source_record_id=uuid4(),
        source_id="exchange_test",
        content_kind=NormalizedContentKind.FINANCIAL_REPORT,
        content_tier=ContentTier.STATUTORY_DISCLOSURE,
        fact_eligibility=FactEligibility.COMPANY_FACT,
        evidence=evidence,
    )
    report_fields: dict[str, object] = {
        **base,
        "company_id": uuid4(),
        "reporting_period": ReportingPeriodKind.HALF_YEAR,
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 6, 30),
        "fiscal_year": 2026,
        "statement_scope": StatementScope.CONSOLIDATED,
        "audit_opinion": AuditOpinion.UNAUDITED,
        "presentation_currency": "CNY",
        "metrics": [metric],
    }
    report = FinancialReport.model_validate(report_fields)
    assert report.metrics[0].evidence_ids == [evidence.evidence_id]

    unattached = metric.model_copy(update={"evidence_ids": [uuid4()]})
    with pytest.raises(ValidationError, match="unattached evidence"):
        FinancialReport.model_validate(report_fields | {"metrics": [unattached]})
    with pytest.raises(ValidationError, match="unique per period"):
        FinancialReport.model_validate(report_fields | {"metrics": [metric, metric]})
    with pytest.raises(ValidationError, match="period_end cannot be earlier"):
        FinancialMetric(
            metric_id="profit",
            label="Profit",
            value="10.00",
            unit="CNY",
            period_start=date(2026, 6, 30),
            period_end=date(2026, 1, 1),
            evidence_ids=[evidence.evidence_id],
        )


def test_broker_research_target_price_range_is_complete_ordered_and_one_currency() -> None:
    evidence = _evidence(
        content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
        fact_eligibility=FactEligibility.CONTEXT_ONLY,
        source_id="broker_test",
    )
    fields: dict[str, object] = {
        **_content_fields(
            source_record_id=uuid4(),
            source_id="broker_test",
            content_kind=NormalizedContentKind.BROKER_RESEARCH,
            content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
            fact_eligibility=FactEligibility.CONTEXT_ONLY,
            evidence=evidence,
        ),
        "institution_id": "broker-1",
        "institution_name": "Synthetic Broker",
        "rating": ResearchRating.BUY,
        "summary": "Synthetic report summary",
        "target_price_low": MoneyValue(amount="10.00", currency="CNY"),
        "target_price_high": MoneyValue(amount="12.00", currency="CNY"),
    }
    BrokerResearchReport.model_validate(fields)

    invalid_ranges = (
        {"target_price_high": None},
        {"target_price_high": MoneyValue(amount="9.00", currency="CNY")},
        {"target_price_high": MoneyValue(amount="12.00", currency="USD")},
    )
    for updates in invalid_ranges:
        with pytest.raises(ValidationError):
            BrokerResearchReport.model_validate(fields | updates)


@pytest.mark.parametrize(
    "forbidden_field",
    ["contains_individual_content", "contains_personal_identifiers"],
)
def test_social_sentiment_forbids_individual_material(forbidden_field: str) -> None:
    raw = _social_content().model_dump()
    raw[forbidden_field] = True
    with pytest.raises(ValidationError):
        SocialSentimentAggregate.model_validate(raw)


def test_social_sentiment_shares_must_sum_to_one() -> None:
    raw = _social_content().model_dump()
    raw.update(positive_share=0.8, negative_share=0.3, neutral_share=0.1)
    with pytest.raises(ValidationError, match="shares must sum to one"):
        SocialSentimentAggregate.model_validate(raw)


def test_normalization_result_distinguishes_items_from_ignored_records() -> None:
    source_record_id = uuid4()
    social = _social_content(source_record_id=source_record_id)
    result = ContentNormalizationResult(
        schema_version="1.0",
        source_record_id=source_record_id,
        items=[social],
        normalized_at=NOW,
        normalizer_version="1.0.0",
    )
    assert result.items == [social]

    ignored = ContentNormalizationResult(
        schema_version="1.0",
        source_record_id=uuid4(),
        items=[],
        normalized_at=NOW,
        normalizer_version="1.0.0",
        ignored_reason_code="UNSUPPORTED_CONTENT",
    )
    assert ignored.ignored_reason_code == "UNSUPPORTED_CONTENT"

    with pytest.raises(ValidationError, match="require ignored_reason_code"):
        ContentNormalizationResult(
            schema_version="1.0",
            source_record_id=uuid4(),
            items=[],
            normalized_at=NOW,
            normalizer_version="1.0.0",
        )
    with pytest.raises(ValidationError, match="only valid for empty"):
        ContentNormalizationResult(
            schema_version="1.0",
            source_record_id=source_record_id,
            items=[social],
            normalized_at=NOW,
            normalizer_version="1.0.0",
            ignored_reason_code="IGNORED",
        )
    with pytest.raises(ValidationError, match="retain source_record_id"):
        ContentNormalizationResult(
            schema_version="1.0",
            source_record_id=uuid4(),
            items=[social],
            normalized_at=NOW,
            normalizer_version="1.0.0",
        )


def test_dataset_manifest_enforces_temporal_purge_and_label_isolation() -> None:
    splits = _valid_splits()
    manifest = _dataset_manifest(splits)
    assert manifest.split_strategy == "temporal"

    train, _, test = splits
    validation_too_close = _dataset_split(
        DatasetSplitName.VALIDATION,
        time_from=train.time_until + timedelta(minutes=30),
        time_until=NOW - timedelta(days=10),
        object_key="datasets/validation-too-close.jsonl",
        label_available_until=NOW - timedelta(days=10),
    )
    with pytest.raises(ValidationError, match="purge_gap_minutes"):
        _dataset_manifest([train, validation_too_close, test])

    leaking_train = _dataset_split(
        DatasetSplitName.TRAIN,
        time_from=train.time_from,
        time_until=train.time_until,
        object_key="datasets/train-label-leak.jsonl",
        label_available_until=splits[1].time_from + timedelta(minutes=1),
    )
    with pytest.raises(ValidationError, match="labels cannot become available"):
        _dataset_manifest([leaking_train, splits[1], test])


def test_training_artifact_requires_complete_valid_shard_metadata() -> None:
    valid = _training_artifact(
        object_key="models/adapter-00001.safetensors",
        shard_index=0,
        shard_count=2,
    )
    assert (valid.shard_index, valid.shard_count) == (0, 2)

    with pytest.raises(ValidationError, match="must be provided together"):
        _training_artifact(
            object_key="models/incomplete.safetensors",
            shard_index=0,
        )
    with pytest.raises(ValidationError, match="lower than shard_count"):
        _training_artifact(
            object_key="models/out-of-range.safetensors",
            shard_index=2,
            shard_count=2,
        )


def test_training_run_record_enforces_status_timestamps_and_failure_code() -> None:
    common: dict[str, object] = {
        "schema_version": "1.0",
        "run_id": uuid4(),
        "dataset_id": uuid4(),
        "dataset_version": "1.0.0",
        "base_model_id": uuid4(),
        "base_model_version": "1.0.0",
        "config": _lora_config(),
        "requested_at": NOW - timedelta(hours=1),
        "random_seed": 42,
        "trainer": "transformers",
        "trainer_version": "1.0.0",
        "started_at": NOW - timedelta(minutes=50),
        "code_revision": "a" * 40,
        "environment_lock_hash": "b" * 64,
    }
    with pytest.raises(ValidationError, match="completed_at is required"):
        TrainingRunRecord.model_validate(common | {"status": TrainingRunStatus.SUCCEEDED})
    with pytest.raises(ValidationError, match="failure_code is required"):
        TrainingRunRecord.model_validate(
            common
            | {
                "status": TrainingRunStatus.FAILED,
                "completed_at": NOW,
            }
        )
    with pytest.raises(ValidationError, match="only valid for failed"):
        TrainingRunRecord.model_validate(
            common
            | {
                "status": TrainingRunStatus.RUNNING,
                "failure_code": "SYNTHETIC_FAILURE",
            }
        )

    failed = TrainingRunRecord.model_validate(
        common
        | {
            "status": TrainingRunStatus.FAILED,
            "completed_at": NOW,
            "failure_code": "SYNTHETIC_FAILURE",
            "safe_failure_message": "Synthetic safe failure",
        }
    )
    assert failed.status is TrainingRunStatus.FAILED


def test_model_manifest_shards_are_distinct_and_model_remains_paper_only() -> None:
    first = _training_artifact(
        object_key="models/adapter-00001.safetensors",
        shard_index=0,
        shard_count=2,
    )
    second = _training_artifact(
        object_key="models/adapter-00002.safetensors",
        shard_index=1,
        shard_count=2,
    )
    manifest = _model_manifest([first, second])
    assert [item.shard_index for item in manifest.artifacts] == [0, 1]
    assert manifest.paper_only is True

    duplicate_location = TrainingArtifact(
        kind=TrainingArtifactKind.LORA_ADAPTER,
        reference=first.reference,
        role="adapter",
        format="safetensors",
        shard_index=1,
        shard_count=2,
        framework="peft",
        framework_version="1.0.0",
    )
    with pytest.raises(ValidationError, match="locations must be unique"):
        _model_manifest([first, duplicate_location])

    raw = manifest.model_dump()
    raw["paper_only"] = False
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(raw)


def test_model_registration_and_promotion_allow_only_safe_stages() -> None:
    manifest = _model_manifest(
        [
            _training_artifact(
                object_key="models/adapter.safetensors",
                shard_index=0,
                shard_count=1,
            )
        ]
    )
    with pytest.raises(ValidationError):
        ModelRegistrationRequest(
            schema_version="1.0",
            context=_mutation_context(),
            manifest=manifest,
            initial_stage=ModelStage.PAPER_RESEARCH,
        )

    promotion_fields: dict[str, object] = {
        "schema_version": "1.0",
        "context": _mutation_context(),
        "model_id": manifest.model_id,
        "expected_stage": ModelStage.CANDIDATE,
        "target_stage": ModelStage.VALIDATED,
        "expected_revision": 1,
        "rationale": "Synthetic validation evidence",
    }
    with pytest.raises(ValidationError, match="requires evaluation_ids"):
        ModelPromotionRequest.model_validate(promotion_fields | {"evaluation_ids": []})

    evaluation_id = uuid4()
    promotion = ModelPromotionRequest.model_validate(
        promotion_fields | {"evaluation_ids": [evaluation_id]}
    )
    assert promotion.evaluation_ids == [evaluation_id]

    with pytest.raises(ValidationError, match="transition is not allowed"):
        ModelPromotionRequest.model_validate(
            promotion_fields
            | {
                "target_stage": ModelStage.PAPER_RESEARCH,
                "evaluation_ids": [evaluation_id],
            }
        )


def test_quality_request_and_result_enforce_point_in_time_cutoffs() -> None:
    content = _social_content()
    request = QualityAssessmentRequest(
        schema_version="1.0",
        context=_operation_context(),
        content=content,
        scoring_policy_id="quality.default",
        scoring_policy_version="1.0.0",
    )
    assert request.content.content_id == content.content_id

    future = content.model_copy(update={"revision_known_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValidationError, match="learned after"):
        QualityAssessmentRequest(
            schema_version="1.0",
            context=_operation_context(),
            content=future,
            scoring_policy_id="quality.default",
            scoring_policy_version="1.0.0",
        )

    assessment = _quality_assessment(
        content_tier=ContentTier.SOCIAL_AGGREGATE,
        fact_eligibility=FactEligibility.SENTIMENT_ONLY,
        fact_eligible=False,
        may_trigger_notification_alone=False,
    )
    with pytest.raises(ValidationError, match="later than result as_of"):
        QualityAssessmentResult(
            schema_version="1.0",
            assessment=assessment,
            as_of=NOW - timedelta(seconds=1),
        )


def test_quality_requires_six_unique_factors_and_consistent_scores() -> None:
    factors = _quality_factors()
    assessment = _quality_assessment(
        content_tier=ContentTier.SOCIAL_AGGREGATE,
        fact_eligibility=FactEligibility.SENTIMENT_ONLY,
        fact_eligible=False,
        may_trigger_notification_alone=False,
        factors=factors,
    )
    assert {item.name for item in assessment.factors} == set(QualityFactorName)

    with pytest.raises(ValidationError, match="contribution must equal"):
        QualityFactor(
            name=QualityFactorName.AUTHORITY,
            score=0.5,
            weight=0.5,
            contribution=0.1,
        )

    duplicate_factors = [*factors[:-1], factors[0].model_copy()]
    with pytest.raises(ValidationError, match="each quality factor exactly once"):
        _quality_assessment(
            content_tier=ContentTier.SOCIAL_AGGREGATE,
            fact_eligibility=FactEligibility.SENTIMENT_ONLY,
            fact_eligible=False,
            may_trigger_notification_alone=False,
            factors=duplicate_factors,
        )

    with pytest.raises(ValidationError, match="total_score must equal"):
        _quality_assessment(
            content_tier=ContentTier.SOCIAL_AGGREGATE,
            fact_eligibility=FactEligibility.SENTIMENT_ONLY,
            fact_eligible=False,
            may_trigger_notification_alone=False,
            total_score=0.1,
        )


def test_quality_blocks_social_fact_admission_and_standalone_triggering() -> None:
    invalid_social_uses = (
        {
            "fact_eligibility": FactEligibility.COMPANY_FACT,
            "fact_eligible": True,
            "may_trigger_notification_alone": False,
        },
        {
            "fact_eligibility": FactEligibility.SENTIMENT_ONLY,
            "fact_eligible": False,
            "may_trigger_notification_alone": True,
        },
    )
    for values in invalid_social_uses:
        with pytest.raises(ValidationError, match="cannot establish facts or trigger alone"):
            _quality_assessment(
                content_tier=ContentTier.SOCIAL_AGGREGATE,
                fact_eligibility=values["fact_eligibility"],
                fact_eligible=values["fact_eligible"],
                may_trigger_notification_alone=values["may_trigger_notification_alone"],
            )


def test_quality_never_filters_authoritative_facts_only_for_low_score() -> None:
    with pytest.raises(ValidationError, match="cannot be dropped"):
        _quality_assessment(
            content_tier=ContentTier.STATUTORY_DISCLOSURE,
            fact_eligibility=FactEligibility.COMPANY_FACT,
            fact_eligible=True,
            may_trigger_notification_alone=True,
            disposition=ContentDisposition.FILTER_LOW_QUALITY,
        )


def test_content_quality_port_uses_one_request_and_one_result_contract() -> None:
    signature = inspect.signature(ContentQualityPort.assess)
    assert tuple(signature.parameters) == ("self", "request")
    assert signature.return_annotation is QualityAssessmentResult


def test_decimal_string_and_common_context_failure_branches() -> None:
    with pytest.raises(ValidationError, match="valid decimal string"):
        MoneyValue(amount="not-a-decimal", currency="CNY")

    correlation_id = uuid4()
    explicit = OperationContext(
        schema_version="1.0",
        correlation_id=correlation_id,
        as_of=NOW,
        requested_at=NOW,
    )
    assert explicit.correlation_id == correlation_id

    with pytest.raises(ValidationError, match="deadline_at must be later"):
        OperationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW,
            deadline_at=NOW,
        )
    with pytest.raises(ValidationError, match="next_cursor is required"):
        CursorPage[str](
            schema_version="1.0",
            items=["synthetic"],
            has_more=True,
            as_of=NOW,
        )
    continued = CursorPage[str](
        schema_version="1.0",
        items=["synthetic"],
        next_cursor="opaque-cursor",
        has_more=True,
        as_of=NOW,
    )
    assert continued.next_cursor == "opaque-cursor"


def test_normalized_content_timeline_evidence_and_normalization_cutoff_branches() -> None:
    social = _social_content()
    raw = social.model_dump()

    invalid_updates = (
        {"first_seen_at": raw["published_at"] - timedelta(seconds=1)},
        {"revision_known_at": raw["first_seen_at"] - timedelta(seconds=1)},
        {"evidence": [*raw["evidence"], raw["evidence"][0]]},
    )
    for updates in invalid_updates:
        with pytest.raises(ValidationError):
            SocialSentimentAggregate.model_validate(raw | updates)

    late_evidence = social.evidence[0].model_copy(
        update={"first_seen_at": social.revision_known_at + timedelta(seconds=1)}
    )
    with pytest.raises(ValidationError, match="evidence learned later"):
        SocialSentimentAggregate.model_validate(raw | {"evidence": [late_evidence]})

    with pytest.raises(ValidationError, match="window_ended_at must be later"):
        SocialSentimentAggregate.model_validate(raw | {"window_ended_at": social.window_started_at})
    early_evidence = social.evidence[0].model_copy(
        update={"first_seen_at": social.window_ended_at - timedelta(seconds=3)}
    )
    with pytest.raises(ValidationError, match="cannot be known before"):
        SocialSentimentAggregate.model_validate(
            raw
            | {
                "revision_known_at": social.window_ended_at - timedelta(seconds=1),
                "first_seen_at": social.window_ended_at - timedelta(seconds=2),
                "evidence": [early_evidence],
            }
        )

    ContentNormalizationRequest(
        schema_version="1.0",
        context=_operation_context(),
        source_record=_source_record(first_seen_at=NOW),
    )
    with pytest.raises(ValidationError, match="first seen after"):
        ContentNormalizationRequest(
            schema_version="1.0",
            context=_operation_context(),
            source_record=_source_record(first_seen_at=NOW + timedelta(seconds=1)),
        )

    with pytest.raises(ValidationError, match="known after normalized_at"):
        ContentNormalizationResult(
            schema_version="1.0",
            source_record_id=social.source_record_id,
            items=[social],
            normalized_at=social.revision_known_at - timedelta(seconds=1),
            normalizer_version="1.0.0",
        )


def test_financial_report_and_research_optional_range_remaining_branches() -> None:
    evidence = _evidence(
        content_tier=ContentTier.STATUTORY_DISCLOSURE,
        fact_eligibility=FactEligibility.COMPANY_FACT,
        source_id="exchange_test",
    )
    metric = FinancialMetric(
        metric_id="revenue",
        label="Revenue",
        value="100.00",
        unit="CNY",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 6, 30),
        evidence_ids=[evidence.evidence_id],
    )
    report_fields: dict[str, object] = {
        **_content_fields(
            source_record_id=uuid4(),
            source_id="exchange_test",
            content_kind=NormalizedContentKind.FINANCIAL_REPORT,
            content_tier=ContentTier.STATUTORY_DISCLOSURE,
            fact_eligibility=FactEligibility.COMPANY_FACT,
            evidence=evidence,
        ),
        "company_id": uuid4(),
        "reporting_period": ReportingPeriodKind.HALF_YEAR,
        "period_start": date(2026, 7, 1),
        "period_end": date(2026, 6, 30),
        "fiscal_year": 2026,
        "statement_scope": StatementScope.CONSOLIDATED,
        "audit_opinion": AuditOpinion.UNAUDITED,
        "presentation_currency": "CNY",
        "metrics": [metric],
    }
    with pytest.raises(ValidationError, match="period_end cannot be earlier"):
        FinancialReport.model_validate(report_fields)

    research_evidence = _evidence(
        content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
        fact_eligibility=FactEligibility.CONTEXT_ONLY,
        source_id="broker_test",
    )
    no_target = BrokerResearchReport(
        **_content_fields(
            source_record_id=uuid4(),
            source_id="broker_test",
            content_kind=NormalizedContentKind.BROKER_RESEARCH,
            content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
            fact_eligibility=FactEligibility.CONTEXT_ONLY,
            evidence=research_evidence,
        ),
        institution_id="broker-1",
        institution_name="Synthetic Broker",
        rating=ResearchRating.NOT_RATED,
        summary="No target price",
    )
    assert no_target.target_price_low is None


def test_market_bar_and_forecast_request_remaining_consistency_branches() -> None:
    invalid_bars = (
        {"closed_at": NOW - timedelta(minutes=3)},
        {"high_price": "10.05"},
        {"low_price": "10.05"},
        {"turnover": MoneyValue(amount="100", currency="USD")},
    )
    for updates in invalid_bars:
        with pytest.raises(ValidationError):
            _market_bar(**updates)

    wrong_instrument = _market_bar(instrument=OTHER_INSTRUMENT)
    with pytest.raises(ValidationError, match="requested instrument"):
        _forecast_request(bars=[wrong_instrument])

    bar = _market_bar()
    with pytest.raises(ValidationError, match="duplicate market bars"):
        _forecast_request(bars=[bar, bar])

    feature = _feature()
    with pytest.raises(ValidationError, match="duplicate feature"):
        _forecast_request(features=[feature, feature])

    wrong_version = _feature(feature_version="features-v2")
    with pytest.raises(ValidationError, match="feature_set_version"):
        _forecast_request(features=[wrong_version])


def test_quantile_quantitative_and_text_signal_direction_branches() -> None:
    with pytest.raises(ValidationError, match="q10 <= q50 <= q90"):
        ReturnQuantiles(q10="0.02", q50="0.01", q90="0.03")

    with pytest.raises(ValidationError, match="training_cutoff cannot be later"):
        _quantitative_forecast(training_cutoff=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="generated_at cannot be earlier"):
        _quantitative_forecast(generated_at=NOW - timedelta(seconds=1))

    common: dict[str, object] = {
        "instrument": INSTRUMENT,
        "as_of": NOW,
        "confidence": 0.5,
        "materiality": 0.5,
        "content_ids": [uuid4()],
        "evidence_ids": [uuid4()],
        "model_version": "text-model-v1",
        "rule_version": "rules-v1",
        "generated_at": NOW + timedelta(seconds=1),
        "is_paper_only": True,
    }
    invalid_directions = (
        (SignalDirection.POSITIVE, 0),
        (SignalDirection.NEGATIVE, 0),
        (SignalDirection.NEUTRAL, 0.1),
    )
    for direction, score in invalid_directions:
        with pytest.raises(ValidationError):
            TextSignal.model_validate(common | {"direction": direction, "signed_score": score})
    with pytest.raises(ValidationError, match="generated_at cannot be earlier"):
        TextSignal.model_validate(
            common
            | {
                "direction": SignalDirection.NEUTRAL,
                "signed_score": 0,
                "generated_at": NOW - timedelta(seconds=1),
            }
        )

    positive = TextSignal.model_validate(
        common | {"direction": SignalDirection.POSITIVE, "signed_score": 0.4}
    )
    negative = TextSignal.model_validate(
        common | {"direction": SignalDirection.NEGATIVE, "signed_score": -0.4}
    )
    assert positive.signed_score > 0 > negative.signed_score


def test_text_signal_request_and_batch_reject_duplicate_identity() -> None:
    social = _social_content()
    with pytest.raises(ValidationError, match="content IDs must be unique"):
        TextSignalRequest(
            schema_version="1.0",
            context=_operation_context(),
            instrument=INSTRUMENT,
            contents=[social, social],
            horizon_minutes=10,
            rule_version="rules-v1",
        )

    signal = _text_signal()
    with pytest.raises(ValidationError, match="generated_at cannot be earlier"):
        TextSignalBatch(
            schema_version="1.0",
            signals=[],
            as_of=NOW,
            generated_at=NOW - timedelta(seconds=1),
            analyzer_version="text-analyzer-v1",
        )
    with pytest.raises(ValidationError, match="signal IDs must be unique"):
        TextSignalBatch(
            schema_version="1.0",
            signals=[signal, signal],
            as_of=NOW,
            generated_at=NOW + timedelta(seconds=2),
            analyzer_version="text-analyzer-v1",
        )


def test_fusion_request_and_fused_forecast_fail_closed_on_mismatches() -> None:
    quantitative = _quantitative_forecast()
    FusionRequest(
        context=_operation_context(),
        quantitative=quantitative,
        text_signals=[_text_signal()],
    )

    with pytest.raises(ValidationError, match="quantitative forecast cannot be later"):
        FusionRequest(
            context=_operation_context(),
            quantitative=_quantitative_forecast(as_of=NOW + timedelta(seconds=1)),
        )

    wrong_signal = _text_signal().model_copy(update={"instrument": OTHER_INSTRUMENT})
    with pytest.raises(ValidationError, match="must match the quantitative"):
        FusionRequest(
            context=_operation_context(),
            quantitative=quantitative,
            text_signals=[wrong_signal],
        )

    future_signal = _text_signal(as_of=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="text signals cannot be later"):
        FusionRequest(
            context=_operation_context(),
            quantitative=quantitative,
            text_signals=[future_signal],
        )

    contributions = [
        ForecastContribution(
            source="quantitative",
            weight=0.7,
            signed_adjustment=0,
            rationale="Synthetic quantitative contribution",
        ),
        ForecastContribution(
            source="text",
            weight=0.3,
            signed_adjustment=0.1,
            rationale="Synthetic text contribution",
        ),
    ]
    fused_fields: dict[str, object] = {
        "schema_version": "1.0",
        "quantitative_prediction_id": quantitative.prediction_id,
        "instrument": INSTRUMENT,
        "as_of": NOW,
        "horizon_minutes": 10,
        "probability_up": 0.6,
        "returns": ReturnQuantiles(q10="-0.01", q50="0.01", q90="0.03"),
        "confidence": 0.7,
        "contributions": contributions,
        "fusion_model_version": "1.0.0",
        "generated_at": NOW + timedelta(seconds=1),
        "is_paper_only": True,
    }
    FusedForecast.model_validate(fused_fields)
    with pytest.raises(ValidationError, match="weights must sum to one"):
        FusedForecast.model_validate(fused_fields | {"contributions": [contributions[0]]})
    with pytest.raises(ValidationError, match="generated_at cannot be earlier"):
        FusedForecast.model_validate(fused_fields | {"generated_at": NOW - timedelta(seconds=1)})


def test_outcome_observation_request_and_result_status_branches() -> None:
    outcome = _forecast_outcome()
    request_fields: dict[str, object] = {
        "schema_version": "1.0",
        "context": _mutation_context(),
        "prediction_id": outcome.prediction_id,
        "instrument": INSTRUMENT,
        "prediction_as_of": outcome.prediction_as_of,
        "horizon_ends_at": outcome.horizon_ends_at,
        "benchmark_id": "csi300",
        "source_id": "market_test",
    }
    ForecastOutcomeObservationRequest.model_validate(request_fields)
    with pytest.raises(ValidationError, match="horizon_ends_at must be later"):
        ForecastOutcomeObservationRequest.model_validate(
            request_fields | {"horizon_ends_at": outcome.prediction_as_of}
        )

    result_fields: dict[str, object] = {
        "schema_version": "1.0",
        "prediction_id": outcome.prediction_id,
        "as_of": NOW,
        "reason_code": "SYNTHETIC_STATUS",
        "settlement_version": "1.0.0",
    }
    with pytest.raises(ValidationError, match="requires an outcome"):
        ForecastOutcomeObservationResult.model_validate(
            result_fields | {"status": OutcomeStatus.OBSERVED}
        )
    with pytest.raises(ValidationError, match="only observed status"):
        ForecastOutcomeObservationResult.model_validate(
            result_fields | {"status": OutcomeStatus.NOT_YET_AVAILABLE, "outcome": outcome}
        )
    with pytest.raises(ValidationError, match="must match prediction_id"):
        ForecastOutcomeObservationResult.model_validate(
            result_fields
            | {
                "prediction_id": uuid4(),
                "status": OutcomeStatus.OBSERVED,
                "outcome": outcome,
            }
        )
    unavailable = ForecastOutcomeObservationResult.model_validate(
        result_fields | {"status": OutcomeStatus.NOT_YET_AVAILABLE}
    )
    assert unavailable.outcome is None


def test_job_request_acceptance_and_result_state_machine_branches() -> None:
    request_fields: dict[str, object] = {
        "schema_version": "1.0",
        "job_name": "stock_assistant.tests.run.v1",
        "context": _mutation_context(),
        "payload": _JobPayload(operation="synthetic"),
        "queued_at": NOW,
        "scheduled_for": NOW + timedelta(minutes=1),
        "expires_at": NOW + timedelta(minutes=2),
        "attempt": 1,
        "max_attempts": 3,
    }
    JobRequest[_JobPayload].model_validate(request_fields)
    with pytest.raises(ValidationError, match="effective start"):
        JobRequest[_JobPayload].model_validate(
            request_fields | {"expires_at": NOW + timedelta(minutes=1)}
        )
    with pytest.raises(ValidationError, match="attempt cannot exceed"):
        JobRequest[_JobPayload].model_validate(request_fields | {"attempt": 4, "max_attempts": 3})

    JobAccepted(
        schema_version="1.0",
        job_id=uuid4(),
        status=JobStatus.QUEUED,
        accepted_at=NOW,
        queue_name="default",
    )
    with pytest.raises(ValidationError, match="must have queued status"):
        JobAccepted(
            schema_version="1.0",
            job_id=uuid4(),
            status=JobStatus.RUNNING,
            accepted_at=NOW,
            queue_name="default",
        )

    job_id = uuid4()
    result = _JobPayload(operation="done")
    base: dict[str, object] = {
        "schema_version": "1.0",
        "job_id": job_id,
        "started_at": NOW - timedelta(minutes=1),
        "attempt": 1,
    }
    JobResult[_JobPayload].model_validate(base | {"status": JobStatus.RUNNING})
    JobResult[_JobPayload].model_validate(
        base
        | {
            "status": JobStatus.SUCCEEDED,
            "completed_at": NOW,
            "result": result,
        }
    )
    with pytest.raises(ValidationError, match="earlier than started_at"):
        JobResult[_JobPayload].model_validate(
            base
            | {
                "status": JobStatus.SUCCEEDED,
                "completed_at": NOW - timedelta(minutes=2),
                "result": result,
            }
        )
    with pytest.raises(ValidationError, match="completed_at is required"):
        JobResult[_JobPayload].model_validate(base | {"status": JobStatus.CANCELLED})
    with pytest.raises(ValidationError, match="result is required"):
        JobResult[_JobPayload].model_validate(
            base | {"status": JobStatus.SUCCEEDED, "completed_at": NOW}
        )
    with pytest.raises(ValidationError, match="result is only valid"):
        JobResult[_JobPayload].model_validate(
            base | {"status": JobStatus.RUNNING, "result": result}
        )
    with pytest.raises(ValidationError, match="failure is required"):
        JobResult[_JobPayload].model_validate(
            base | {"status": JobStatus.FAILED, "completed_at": NOW}
        )

    failure = JobFailure(
        reason_code="SYNTHETIC_FAILURE",
        safe_message="Synthetic failure",
        retryable=False,
        failed_at=NOW,
    )
    JobResult[_JobPayload].model_validate(
        base
        | {
            "status": JobStatus.FAILED,
            "completed_at": NOW,
            "failure": failure,
        }
    )
    with pytest.raises(ValidationError, match="failure is only valid"):
        JobResult[_JobPayload].model_validate(
            base | {"status": JobStatus.RUNNING, "failure": failure}
        )


def test_schedule_trigger_shape_and_scheduled_job_timeline_branches() -> None:
    with pytest.raises(ValidationError, match="exactly one trigger"):
        ScheduleTrigger(kind=ScheduleKind.INTERVAL)
    with pytest.raises(ValidationError, match="does not match schedule kind"):
        ScheduleTrigger(kind=ScheduleKind.ONCE, interval_seconds=600)

    template = _job_template()
    trigger = ScheduleTrigger(kind=ScheduleKind.INTERVAL, interval_seconds=600)
    with pytest.raises(ValidationError, match="updated_at cannot be earlier"):
        ScheduledJob[_JobPayload](
            schema_version="1.0",
            schedule_name="synthetic_schedule",
            job_template=template,
            trigger=trigger,
            enabled=True,
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
            revision=1,
        )
    scheduled = ScheduledJob[_JobPayload](
        schema_version="1.0",
        schedule_name="synthetic_schedule",
        job_template=template,
        trigger=trigger,
        enabled=True,
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )
    assert scheduled.revision == 1


def test_dataset_split_and_manifest_metadata_failure_branches() -> None:
    artifact = _stored_object(object_key="datasets/split.jsonl")
    base: dict[str, object] = {
        "split": DatasetSplitName.TRAIN,
        "record_count": 10,
        "time_from": NOW - timedelta(days=3),
        "time_until": NOW - timedelta(days=2),
        "knowledge_cutoff": NOW - timedelta(days=1),
        "label_available_until": NOW - timedelta(days=1),
        "artifact": artifact,
        "content_sha256": artifact.content_sha256,
    }
    invalid_split_updates = (
        {"time_until": NOW - timedelta(days=4)},
        {"knowledge_cutoff": NOW - timedelta(days=3)},
        {"label_available_until": NOW},
        {"content_sha256": "f" * 64},
    )
    for updates in invalid_split_updates:
        with pytest.raises(ValidationError):
            DatasetSplitManifest.model_validate(base | updates)

    manifest = _dataset_manifest(_valid_splits())
    raw = manifest.model_dump()
    invalid_manifest_updates = (
        {"splits": [raw["splits"][0], raw["splits"][0]], "total_record_count": 20},
        {"total_record_count": 31},
        {"created_at": raw["data_cutoff"] - timedelta(seconds=1)},
        {"source_ids": ["synthetic_exchange", "synthetic_exchange"]},
        {"license_ids": ["synthetic-test-license", "synthetic-test-license"]},
    )
    for updates in invalid_manifest_updates:
        with pytest.raises(ValidationError):
            TrainingDatasetManifest.model_validate(raw | updates)

    future_cutoff_split = _valid_splits()[0].model_copy(
        update={"knowledge_cutoff": manifest.data_cutoff + timedelta(seconds=1)}
    )
    with pytest.raises(ValidationError, match="beyond data_cutoff"):
        TrainingDatasetManifest.model_validate(
            raw
            | {
                "splits": [future_cutoff_split, *_valid_splits()[1:]],
            }
        )

    with pytest.raises(ValidationError, match="required when evidence_count"):
        TrainingDatasetManifest.model_validate(raw | {"evidence_count": 1})
    with pytest.raises(ValidationError, match="only valid when evidence_count"):
        TrainingDatasetManifest.model_validate(
            raw | {"evidence_manifest": _stored_object(object_key="evidence.jsonl")}
        )

    with pytest.raises(ValidationError, match="require train, validation and test"):
        TrainingDatasetManifest.model_validate(
            raw | {"splits": [raw["splits"][0]], "total_record_count": 10}
        )

    train, validation, _ = _valid_splits()
    test_too_close = _dataset_split(
        DatasetSplitName.TEST,
        time_from=validation.time_until + timedelta(minutes=30),
        time_until=NOW - timedelta(days=2),
        object_key="datasets/test-too-close.jsonl",
        label_available_until=NOW - timedelta(days=2),
    )
    with pytest.raises(ValidationError, match="embargo_minutes"):
        _dataset_manifest([train, validation, test_too_close], embargo_minutes=60)

    leaking_validation = _dataset_split(
        DatasetSplitName.VALIDATION,
        time_from=validation.time_from,
        time_until=validation.time_until,
        object_key="datasets/validation-label-leak.jsonl",
        label_available_until=_valid_splits()[2].time_from + timedelta(minutes=1),
    )
    with pytest.raises(ValidationError, match="inside test"):
        _dataset_manifest([train, leaking_validation, _valid_splits()[2]])

    evaluation_raw = _dataset_manifest(_valid_splits()).model_dump()
    evaluation_only = TrainingDatasetManifest.model_validate(
        evaluation_raw
        | {
            "purpose": DatasetPurpose.EVALUATION,
            "splits": [evaluation_raw["splits"][2]],
            "total_record_count": 10,
        }
    )
    assert evaluation_only.splits[0].split is DatasetSplitName.TEST


def test_lora_training_record_and_model_manifest_remaining_branches() -> None:
    config_raw = _lora_config().model_dump()
    with pytest.raises(ValidationError, match="target_modules must be unique"):
        LoRAConfig.model_validate(config_raw | {"target_modules": ["q_proj", "q_proj"]})

    unsharded = _training_artifact(object_key="models/unsharded.safetensors")
    assert unsharded.shard_index is None and unsharded.shard_count is None

    common: dict[str, object] = {
        "schema_version": "1.0",
        "run_id": uuid4(),
        "status": TrainingRunStatus.RUNNING,
        "dataset_id": uuid4(),
        "dataset_version": "1.0.0",
        "base_model_id": uuid4(),
        "base_model_version": "1.0.0",
        "config": _lora_config(),
        "requested_at": NOW - timedelta(hours=1),
        "random_seed": 42,
        "trainer": "transformers",
        "trainer_version": "1.0.0",
        "code_revision": "a" * 40,
        "environment_lock_hash": "b" * 64,
    }
    with pytest.raises(ValidationError, match="completed_at cannot be earlier"):
        TrainingRunRecord.model_validate(
            common
            | {
                "started_at": NOW,
                "completed_at": NOW - timedelta(seconds=1),
            }
        )
    with pytest.raises(ValidationError, match="started_at cannot be earlier"):
        TrainingRunRecord.model_validate(common | {"started_at": NOW - timedelta(hours=2)})

    first = _training_artifact(object_key="models/first.safetensors", shard_index=0, shard_count=1)
    manifest = _model_manifest([first])
    manifest_raw = manifest.model_dump()
    duplicate_capability = manifest.capabilities[0].model_copy()
    with pytest.raises(ValidationError, match="capabilities must be unique"):
        ModelManifest.model_validate(
            manifest_raw | {"capabilities": [manifest.capabilities[0], duplicate_capability]}
        )

    duplicate_artifact_id = _training_artifact(
        object_key="models/second.safetensors", shard_index=0, shard_count=1
    ).model_copy(update={"artifact_id": first.artifact_id})
    with pytest.raises(ValidationError, match="artifact IDs must be unique"):
        ModelManifest.model_validate(manifest_raw | {"artifacts": [first, duplicate_artifact_id]})
    with pytest.raises(ValidationError, match="reference at least one dataset"):
        ModelManifest.model_validate(manifest_raw | {"training_run_id": uuid4(), "dataset_ids": []})
    with pytest.raises(ValidationError, match="training_cutoff cannot be later"):
        ModelManifest.model_validate(manifest_raw | {"training_cutoff": NOW + timedelta(seconds=1)})


def test_model_evaluation_promotion_paths_and_result_consistency() -> None:
    metric = EvaluationMetric(
        name="schema_compliance",
        value="1",
        threshold="1",
        higher_is_better=True,
        passed=True,
    )
    report_fields: dict[str, object] = {
        "schema_version": "1.0",
        "model_id": uuid4(),
        "model_version": "1.0.0",
        "evaluation_suite": "synthetic-suite",
        "suite_version": "1.0.0",
        "dataset_id": uuid4(),
        "data_cutoff": NOW - timedelta(days=1),
        "evaluated_at": NOW,
        "outcome": EvaluationOutcome.PASSED,
        "metrics": [metric],
        "leakage_check_passed": True,
        "schema_compliance_passed": True,
        "grounding_check_passed": True,
        "evaluator_code_revision": "c" * 40,
    }
    ModelEvaluationReport.model_validate(report_fields)
    with pytest.raises(ValidationError, match="earlier than data_cutoff"):
        ModelEvaluationReport.model_validate(
            report_fields | {"evaluated_at": NOW - timedelta(days=2)}
        )
    with pytest.raises(ValidationError, match="every gate and metric"):
        ModelEvaluationReport.model_validate(report_fields | {"leakage_check_passed": False})

    evaluation_id = uuid4()
    transition_cases = (
        (ModelStage.VALIDATED, ModelStage.SHADOW),
        (ModelStage.SHADOW, ModelStage.PAPER_RESEARCH),
        (ModelStage.PAPER_RESEARCH, ModelStage.ARCHIVED),
    )
    for expected_stage, target_stage in transition_cases:
        request = ModelPromotionRequest(
            schema_version="1.0",
            context=_mutation_context(),
            model_id=uuid4(),
            expected_stage=expected_stage,
            target_stage=target_stage,
            expected_revision=1,
            evaluation_ids=[evaluation_id],
            rationale="Synthetic safe transition",
        )
        assert request.target_stage is target_stage

    with pytest.raises(ValidationError, match="transition is not allowed"):
        ModelPromotionRequest(
            schema_version="1.0",
            context=_mutation_context(),
            model_id=uuid4(),
            expected_stage=ModelStage.ARCHIVED,
            target_stage=ModelStage.CANDIDATE,
            expected_revision=1,
            rationale="Unsafe restore",
        )

    validated = _model_record(stage=ModelStage.VALIDATED, evaluation_ids=[evaluation_id])
    ModelPromotionResult(
        schema_version="1.0",
        promoted=True,
        previous_stage=ModelStage.CANDIDATE,
        current=validated,
        decided_at=NOW,
        reason_code="PROMOTED",
    )
    with pytest.raises(ValidationError, match="promoted must reflect"):
        ModelPromotionResult(
            schema_version="1.0",
            promoted=False,
            previous_stage=ModelStage.CANDIDATE,
            current=validated,
            decided_at=NOW,
            reason_code="INCONSISTENT",
        )

    paper_without_evaluation = _model_record(stage=ModelStage.PAPER_RESEARCH)
    with pytest.raises(ValidationError, match="require evaluation evidence"):
        ModelPromotionResult(
            schema_version="1.0",
            promoted=True,
            previous_stage=ModelStage.SHADOW,
            current=paper_without_evaluation,
            decided_at=NOW,
            reason_code="MISSING_EVALUATION",
        )


def test_quality_remaining_factor_fact_and_success_result_branches() -> None:
    with pytest.raises(ValidationError, match="conflict_penalty weight cannot be positive"):
        QualityFactor(
            name=QualityFactorName.CONFLICT_PENALTY,
            score=0.5,
            weight=0.2,
            contribution=0.1,
        )
    with pytest.raises(ValidationError, match="only conflict_penalty"):
        QualityFactor(
            name=QualityFactorName.AUTHORITY,
            score=0.5,
            weight=-0.2,
            contribution=-0.1,
        )
    with pytest.raises(ValidationError, match="fact_eligible must match"):
        _quality_assessment(
            content_tier=ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA,
            fact_eligibility=FactEligibility.CONTEXT_ONLY,
            fact_eligible=True,
            may_trigger_notification_alone=False,
        )

    assessment = _quality_assessment(
        content_tier=ContentTier.SOCIAL_AGGREGATE,
        fact_eligibility=FactEligibility.SENTIMENT_ONLY,
        fact_eligible=False,
        may_trigger_notification_alone=False,
    )
    result = QualityAssessmentResult(
        schema_version="1.0",
        assessment=assessment,
        as_of=NOW,
    )
    assert result.assessment == assessment
