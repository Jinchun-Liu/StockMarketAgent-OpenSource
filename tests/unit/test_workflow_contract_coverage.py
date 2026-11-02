"""Semantic coverage for briefing, chat, content, and event workflow contracts."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.briefing_workflows import (
    BriefingGenerationRequest,
    BriefingGenerationResult,
    BriefingInputSummary,
    BriefingPage,
    BriefingQuery,
    BriefingStorageRecord,
    BriefingStoreRequest,
    BriefingValidationIssue,
    BriefingValidationRequest,
    BriefingValidationResult,
    BriefingValidationSeverity,
)
from stock_assistant.contracts.briefings import Briefing, BriefingKind
from stock_assistant.contracts.chat import (
    ChatCitation,
    ChatEvidenceHit,
    ChatGroundingRequest,
    ChatGroundingResult,
    ChatHistoryPage,
    ChatHistoryQuery,
    ChatIntent,
    ChatIntentRequest,
    ChatIntentResult,
    ChatRequest,
    ChatResponse,
    ChatRetrievalRequest,
    ChatRetrievalResult,
    ChatRole,
    ChatTurn,
    ChatTurnWriteRequest,
    ClaimNature,
    DraftClaim,
    GroundedClaim,
    GroundingStatus,
    IntentCandidate,
)
from stock_assistant.contracts.common import (
    CursorPage,
    MoneyValue,
    MutationContext,
    OperationContext,
)
from stock_assistant.contracts.content import (
    AuditOpinion,
    BrokerResearchReport,
    ContentNormalizationRequest,
    ContentNormalizationResult,
    ExchangeAnnouncement,
    FinancialMetric,
    FinancialNewsArticle,
    FinancialReport,
    NormalizedContentKind,
    ReportingPeriodKind,
    ResearchRating,
    SocialSentimentAggregate,
    StatementScope,
)
from stock_assistant.contracts.event_workflows import (
    EventExtractionPurpose,
    EventExtractionRequest,
    EventExtractionResult,
    EventRevisionOutcome,
    EventRevisionRequest,
    EventRevisionResult,
    EventValidationIssue,
    EventValidationRequest,
    EventValidationResult,
    ValidationSeverity,
)
from stock_assistant.contracts.events import (
    Direction,
    EventFact,
    EventStatus,
    EvidenceRef,
    ImpactHorizon,
    MarketEvent,
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

NOW = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)
PUBLISHED = NOW - timedelta(hours=2)
FIRST_SEEN = NOW - timedelta(hours=1)
REVISION_KNOWN = NOW - timedelta(minutes=30)


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
        idempotency_key="workflow-contract-test-0001",
    )


def _model_values(model: ContractModel, **updates: object) -> dict[str, object]:
    values: dict[str, object] = model.model_dump(mode="python")
    values.update(updates)
    return values


def _evidence(
    *,
    evidence_id: UUID | None = None,
    source_id: str = "synthetic_exchange",
    content_tier: ContentTier = ContentTier.STATUTORY_DISCLOSURE,
    fact_eligibility: FactEligibility = FactEligibility.COMPANY_FACT,
    published_at: datetime = PUBLISHED,
    first_seen_at: datetime = FIRST_SEEN,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id or uuid4(),
        source_id=source_id,
        source_url=f"https://example.invalid/{source_id}/record-1",
        title="Synthetic public evidence",
        published_at=published_at,
        first_seen_at=first_seen_at,
        content_sha256="a" * 64,
        license_id="synthetic-test-license",
        rights_snapshot_hash="b" * 64,
        content_tier=content_tier,
        fact_eligibility=fact_eligibility,
    )


def _source_record(
    *,
    record_id: UUID | None = None,
    first_seen_at: datetime = FIRST_SEEN,
    fetched_at: datetime | None = None,
) -> SourceRecord:
    return SourceRecord(
        record_id=record_id or uuid4(),
        source_id="synthetic_exchange",
        external_id="announcement-1",
        capability="announcements",
        record_type="announcement",
        source_tier=SourceTier.STATUTORY_DISCLOSURE,
        content_tier=ContentTier.STATUTORY_DISCLOSURE,
        fact_eligibility=FactEligibility.COMPANY_FACT,
        authority=0.95,
        title="Synthetic announcement",
        canonical_url="https://example.invalid/announcement-1",
        event_time=PUBLISHED,
        published_at=PUBLISHED,
        first_seen_at=first_seen_at,
        fetched_at=fetched_at or first_seen_at,
        content_sha256="a" * 64,
        payload={},
        rights_snapshot_hash="b" * 64,
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


def _content_base(
    *,
    source_record_id: UUID,
    source_id: str,
    content_kind: NormalizedContentKind,
    content_tier: ContentTier,
    fact_eligibility: FactEligibility,
    evidence: list[EvidenceRef],
    published_at: datetime = PUBLISHED,
    first_seen_at: datetime = FIRST_SEEN,
    revision_known_at: datetime = REVISION_KNOWN,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "source_record_id": source_record_id,
        "source_id": source_id,
        "external_id": f"{source_id}-external-1",
        "content_kind": content_kind,
        "content_tier": content_tier,
        "fact_eligibility": fact_eligibility,
        "authority": 0.9,
        "title": "Synthetic normalized content",
        "published_at": published_at,
        "first_seen_at": first_seen_at,
        "revision_known_at": revision_known_at,
        "revision": 1,
        "instruments": [],
        "entities": [],
        "evidence": evidence,
        "content_sha256": "c" * 64,
        "rights_snapshot_hash": "b" * 64,
    }


def _announcement(
    *,
    source_record_id: UUID | None = None,
    evidence: list[EvidenceRef] | None = None,
    published_at: datetime = PUBLISHED,
    first_seen_at: datetime = FIRST_SEEN,
    revision_known_at: datetime = REVISION_KNOWN,
) -> ExchangeAnnouncement:
    record_id = source_record_id or uuid4()
    attached = evidence or [_evidence()]
    return ExchangeAnnouncement(
        **_content_base(
            source_record_id=record_id,
            source_id="synthetic_exchange",
            content_kind=NormalizedContentKind.EXCHANGE_ANNOUNCEMENT,
            content_tier=ContentTier.STATUTORY_DISCLOSURE,
            fact_eligibility=FactEligibility.COMPANY_FACT,
            evidence=attached,
            published_at=published_at,
            first_seen_at=first_seen_at,
            revision_known_at=revision_known_at,
        ),
        exchange_announcement_id="SSE-2026-0001",
        exchange="SSE",
        announcement_category="company.material_event",
        document_number="SSE-DOC-1",
    )


def _news(*, source_record_id: UUID | None = None) -> FinancialNewsArticle:
    record_id = source_record_id or uuid4()
    evidence = _evidence(
        source_id="licensed_media",
        content_tier=ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA,
        fact_eligibility=FactEligibility.CONTEXT_ONLY,
    )
    return FinancialNewsArticle(
        **_content_base(
            source_record_id=record_id,
            source_id="licensed_media",
            content_kind=NormalizedContentKind.FINANCIAL_NEWS,
            content_tier=ContentTier.AUTHORITATIVE_FINANCIAL_MEDIA,
            fact_eligibility=FactEligibility.CONTEXT_ONLY,
            evidence=[evidence],
        ),
        publisher_id="licensed-media",
        publisher_name="Synthetic Licensed Media",
        byline="Synthetic Desk",
        normalized_summary="A synthetic and non-actionable news summary.",
        topics=["earnings"],
        canonical_cluster_key="cluster-key-0001",
    )


def _broker_report(
    *,
    target_price_low: MoneyValue | None = None,
    target_price_high: MoneyValue | None = None,
) -> BrokerResearchReport:
    evidence = _evidence(
        source_id="licensed_broker",
        content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
        fact_eligibility=FactEligibility.CONTEXT_ONLY,
    )
    return BrokerResearchReport(
        **_content_base(
            source_record_id=uuid4(),
            source_id="licensed_broker",
            content_kind=NormalizedContentKind.BROKER_RESEARCH,
            content_tier=ContentTier.LICENSED_BROKER_RESEARCH,
            fact_eligibility=FactEligibility.CONTEXT_ONLY,
            evidence=[evidence],
        ),
        institution_id="broker-1",
        institution_name="Synthetic Broker",
        analyst_names=["Synthetic Analyst"],
        rating=ResearchRating.NEUTRAL,
        previous_rating=ResearchRating.BUY,
        target_price_low=target_price_low,
        target_price_high=target_price_high,
        forecast_horizon_end=date(2026, 12, 31),
        summary="Synthetic licensed research context.",
    )


def _metric(
    *,
    evidence_id: UUID,
    metric_id: str = "revenue",
    period_start: date = date(2026, 1, 1),
    period_end: date = date(2026, 6, 30),
) -> FinancialMetric:
    return FinancialMetric(
        metric_id=metric_id,
        label="Revenue",
        value="1000000.00",
        unit="currency",
        currency="CNY",
        period_start=period_start,
        period_end=period_end,
        evidence_ids=[evidence_id],
    )


def _financial_report(
    *,
    evidence: list[EvidenceRef] | None = None,
    metrics: list[FinancialMetric] | None = None,
    period_start: date = date(2026, 1, 1),
    period_end: date = date(2026, 6, 30),
) -> FinancialReport:
    attached = evidence or [_evidence()]
    report_metrics = metrics or [_metric(evidence_id=attached[0].evidence_id)]
    return FinancialReport(
        **_content_base(
            source_record_id=uuid4(),
            source_id="synthetic_exchange",
            content_kind=NormalizedContentKind.FINANCIAL_REPORT,
            content_tier=ContentTier.STATUTORY_DISCLOSURE,
            fact_eligibility=FactEligibility.COMPANY_FACT,
            evidence=attached,
        ),
        company_id=uuid4(),
        reporting_period=ReportingPeriodKind.HALF_YEAR,
        period_start=period_start,
        period_end=period_end,
        fiscal_year=2026,
        statement_scope=StatementScope.CONSOLIDATED,
        audit_opinion=AuditOpinion.UNAUDITED,
        presentation_currency="CNY",
        metrics=report_metrics,
    )


def _social_content(
    *,
    source_record_id: UUID | None = None,
    window_started_at: datetime = NOW - timedelta(hours=2),
    window_ended_at: datetime = NOW - timedelta(hours=1),
    revision_known_at: datetime = REVISION_KNOWN,
    positive_share: float = 0.5,
    negative_share: float = 0.2,
    neutral_share: float = 0.3,
) -> SocialSentimentAggregate:
    evidence = _evidence(
        source_id="social_aggregate",
        content_tier=ContentTier.SOCIAL_AGGREGATE,
        fact_eligibility=FactEligibility.SENTIMENT_ONLY,
    )
    return SocialSentimentAggregate(
        **_content_base(
            source_record_id=source_record_id or uuid4(),
            source_id="social_aggregate",
            content_kind=NormalizedContentKind.SOCIAL_SENTIMENT_AGGREGATE,
            content_tier=ContentTier.SOCIAL_AGGREGATE,
            fact_eligibility=FactEligibility.SENTIMENT_ONLY,
            evidence=[evidence],
            revision_known_at=revision_known_at,
        ),
        window_started_at=window_started_at,
        window_ended_at=window_ended_at,
        sample_size=200,
        sentiment_score=0.2,
        confidence=0.8,
        positive_share=positive_share,
        negative_share=negative_share,
        neutral_share=neutral_share,
        aggregation_method="aggregate_only",
        aggregation_version="1.0.0",
        contains_individual_content=False,
        contains_personal_identifiers=False,
    )


def _briefing(
    *,
    briefing_id: UUID | None = None,
    kind: BriefingKind = BriefingKind.ON_DEMAND,
    as_of: datetime = NOW - timedelta(minutes=2),
    generated_at: datetime = NOW - timedelta(minutes=1),
) -> Briefing:
    return Briefing(
        schema_version="1.0",
        briefing_id=briefing_id or uuid4(),
        kind=kind,
        generated_at=generated_at,
        as_of=as_of,
        data_delay_seconds=60,
        model_version="briefing-model-v1",
        prompt_hash="d" * 64,
        rule_version="briefing-rules-v1",
    )


def _briefing_inputs() -> BriefingInputSummary:
    return BriefingInputSummary(
        event_count=1,
        evidence_count=1,
        policy_count=0,
        sector_count=0,
        duplicate_count=0,
        unavailable_source_count=0,
    )


def _briefing_error_issue() -> BriefingValidationIssue:
    return BriefingValidationIssue(
        severity=BriefingValidationSeverity.ERROR,
        code="MISSING_EVIDENCE",
        field_path="items.0.evidence_ids",
        safe_message="The synthetic item has no attached evidence.",
    )


def _market_event(
    *,
    event_id: UUID | None = None,
    cluster_id: UUID | None = None,
    revision: int = 1,
    revision_known_at: datetime = REVISION_KNOWN,
) -> MarketEvent:
    evidence = _evidence()
    return MarketEvent(
        schema_version="1.0",
        event_id=event_id or uuid4(),
        cluster_id=cluster_id or uuid4(),
        revision=revision,
        security_ids=["SSE:600000"],
        company_ids=["synthetic-company"],
        event_type="earnings_change",
        event_status=EventStatus.ANNOUNCED,
        direction=Direction.NEUTRAL,
        materiality=0.7,
        impact_horizon=ImpactHorizon.DAYS,
        confidence=0.9,
        published_at=PUBLISHED,
        first_seen_at=FIRST_SEEN,
        revision_known_at=revision_known_at,
        facts=[
            EventFact(
                name="revenue",
                value="1000000.00",
                unit="CNY",
                period="2026H1",
                evidence_id=evidence.evidence_id,
                evidence_span="Synthetic revenue value.",
            )
        ],
        evidence=[evidence],
        extractor_version="event-extractor-v1",
        rule_version="event-rules-v1",
    )


def _event_error_issue() -> EventValidationIssue:
    return EventValidationIssue(
        severity=ValidationSeverity.ERROR,
        code="INVALID_EVIDENCE",
        field_path="evidence",
        safe_message="The synthetic evidence pack is invalid.",
    )


def _chat_hit(
    *,
    evidence: EvidenceRef | None = None,
    relevance: float = 0.9,
) -> ChatEvidenceHit:
    return ChatEvidenceHit(
        evidence=evidence or _evidence(),
        excerpt="Synthetic evidence excerpt.",
        relevance=relevance,
        rerank_score=0.85,
        authority_score=0.95,
    )


def _draft_claim(
    *,
    evidence_ids: list[UUID],
    nature: ClaimNature = ClaimNature.FACT,
    prediction_horizon_minutes: int | None = None,
) -> DraftClaim:
    return DraftClaim(
        nature=nature,
        statement="A synthetic evidence-grounded statement.",
        evidence_ids=evidence_ids,
        confidence=0.8,
        prediction_horizon_minutes=prediction_horizon_minutes,
    )


def _grounded_claim(*, evidence_ids: list[UUID], supported: bool) -> GroundedClaim:
    return GroundedClaim(
        claim=_draft_claim(evidence_ids=evidence_ids),
        supported=supported,
        support_score=0.9 if supported else 0.1,
        validator_codes=[] if supported else ["INSUFFICIENT_SUPPORT"],
    )


def test_briefing_workflow_valid_construction_preserves_scope_and_pit() -> None:
    portfolio_id = uuid4()
    request = BriefingGenerationRequest(
        schema_version="1.0",
        context=_operation_context(),
        kind=BriefingKind.PREMARKET,
        portfolio_id=portfolio_id,
    )
    policy_request = BriefingGenerationRequest(
        schema_version="1.0",
        context=_operation_context(),
        kind=BriefingKind.POLICY,
        policy_ids=[uuid4()],
    )
    briefing = _briefing()
    generated = BriefingGenerationResult(
        schema_version="1.0",
        briefing=briefing,
        inputs=_briefing_inputs(),
        generated_at=briefing.generated_at,
        generator_version="briefing-generator-v1",
        degraded=True,
        degradation_codes=["SOURCE_DELAYED"],
    )
    validation_request = BriefingValidationRequest(
        schema_version="1.0",
        context=_operation_context(),
        briefing=briefing,
        ruleset_version="briefing-rules-v1",
    )
    validation_result = BriefingValidationResult(
        schema_version="1.0",
        briefing_id=briefing.briefing_id,
        valid=True,
        validated_briefing=briefing,
        issues=[],
        ruleset_version="briefing-rules-v1",
        validated_at=NOW,
    )
    invalid_result = BriefingValidationResult(
        schema_version="1.0",
        briefing_id=briefing.briefing_id,
        valid=False,
        issues=[_briefing_error_issue()],
        ruleset_version="briefing-rules-v1",
        validated_at=NOW,
    )
    store_request = BriefingStoreRequest(
        schema_version="1.0",
        context=_mutation_context(),
        briefing=briefing,
    )
    record = BriefingStorageRecord(
        schema_version="1.0",
        briefing=briefing,
        stored_at=NOW,
        content_hash="e" * 64,
    )
    query = BriefingQuery(
        schema_version="1.0",
        context=_operation_context(),
        briefing_ids=[briefing.briefing_id],
        generated_since=NOW - timedelta(days=1),
        generated_until=NOW,
    )
    page = BriefingPage(
        schema_version="1.0",
        page=CursorPage[BriefingStorageRecord](
            schema_version="1.0",
            items=[record],
            as_of=NOW,
        ),
    )

    assert request.portfolio_id == portfolio_id
    assert policy_request.policy_ids
    assert generated.degradation_codes == ["SOURCE_DELAYED"]
    assert validation_request.briefing.as_of <= validation_request.context.as_of
    assert validation_result.validated_briefing == briefing
    assert invalid_result.issues[0].severity is BriefingValidationSeverity.ERROR
    assert store_request.context.idempotency_key == "workflow-contract-test-0001"
    assert query.generated_until == page.page.as_of


@pytest.mark.parametrize("kind", [BriefingKind.PREMARKET, BriefingKind.POLICY])
def test_briefing_generation_requires_a_target_for_scoped_kinds(kind: BriefingKind) -> None:
    expected = (
        "premarket briefings require"
        if kind is BriefingKind.PREMARKET
        else "policy briefings require"
    )
    with pytest.raises(ValidationError, match=expected):
        BriefingGenerationRequest(
            schema_version="1.0",
            context=_operation_context(),
            kind=kind,
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"generated_at": NOW}, "must match briefing.generated_at"),
        ({"degraded": True, "degradation_codes": []}, "degraded results require"),
        ({"degraded": False, "degradation_codes": ["SOURCE_DELAYED"]}, "only valid for degraded"),
    ],
)
def test_briefing_generation_result_rejects_inconsistent_state(
    updates: dict[str, object], message: str
) -> None:
    briefing = _briefing()
    values: dict[str, object] = {
        "schema_version": "1.0",
        "briefing": briefing,
        "inputs": _briefing_inputs(),
        "generated_at": briefing.generated_at,
        "generator_version": "briefing-generator-v1",
        "degraded": False,
        "degradation_codes": [],
    }
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        BriefingGenerationResult.model_validate(values)


def test_briefing_validation_request_rejects_future_cutoffs() -> None:
    future_as_of = _briefing(
        as_of=NOW + timedelta(minutes=1), generated_at=NOW + timedelta(minutes=2)
    )
    with pytest.raises(ValidationError, match=r"briefing\.as_of cannot be later"):
        BriefingValidationRequest(
            schema_version="1.0",
            context=_operation_context(),
            briefing=future_as_of,
            ruleset_version="briefing-rules-v1",
        )

    future_generation = _briefing(as_of=NOW, generated_at=NOW + timedelta(minutes=1))
    with pytest.raises(ValidationError, match="generated in the future"):
        BriefingValidationRequest(
            schema_version="1.0",
            context=_operation_context(),
            briefing=future_generation,
            ruleset_version="briefing-rules-v1",
        )


def test_briefing_validation_result_rejects_each_invalid_state() -> None:
    briefing = _briefing()
    base: dict[str, object] = {
        "schema_version": "1.0",
        "briefing_id": briefing.briefing_id,
        "valid": True,
        "validated_briefing": briefing,
        "issues": [],
        "ruleset_version": "briefing-rules-v1",
        "validated_at": NOW,
    }
    invalid_cases = [
        ({"validated_briefing": None}, "valid results require"),
        ({"issues": [_briefing_error_issue()]}, "valid results require"),
        ({"valid": False, "validated_briefing": None}, "invalid results require"),
        ({"briefing_id": uuid4()}, "must retain the requested briefing ID"),
        ({"validated_at": briefing.generated_at - timedelta(seconds=1)}, "cannot precede"),
    ]
    for updates, message in invalid_cases:
        values = dict(base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            BriefingValidationResult.model_validate(values)


def test_briefing_storage_query_and_page_reject_unsafe_timelines() -> None:
    briefing = _briefing()
    with pytest.raises(ValidationError, match="storage cannot accept"):
        BriefingStoreRequest(
            schema_version="1.0",
            context=_mutation_context(briefing.generated_at - timedelta(seconds=1)),
            briefing=briefing,
        )
    with pytest.raises(ValidationError, match="stored_at cannot be earlier"):
        BriefingStorageRecord(
            schema_version="1.0",
            briefing=briefing,
            stored_at=briefing.generated_at - timedelta(seconds=1),
            content_hash="e" * 64,
        )

    query_base: dict[str, object] = {
        "schema_version": "1.0",
        "context": _operation_context(),
    }
    query_cases = [
        (
            {
                "generated_since": NOW - timedelta(hours=1),
                "generated_until": NOW - timedelta(hours=2),
            },
            "generated_until cannot be earlier",
        ),
        ({"generated_until": NOW + timedelta(seconds=1)}, "generated_until cannot be later"),
        ({"generated_since": NOW + timedelta(seconds=1)}, "generated_since cannot be later"),
    ]
    for updates, message in query_cases:
        values = dict(query_base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            BriefingQuery.model_validate(values)

    record = BriefingStorageRecord(
        schema_version="1.0",
        briefing=briefing,
        stored_at=NOW,
        content_hash="e" * 64,
    )
    with pytest.raises(ValidationError, match="future storage records"):
        BriefingPage(
            schema_version="1.0",
            page=CursorPage[BriefingStorageRecord](
                schema_version="1.0",
                items=[record],
                as_of=NOW - timedelta(seconds=1),
            ),
        )


def test_chat_workflow_valid_construction_keeps_evidence_grounded() -> None:
    session_id = uuid4()
    hit = _chat_hit()
    evidence_id = hit.evidence.evidence_id
    intent_candidate = IntentCandidate(
        intent=ChatIntent.COMPANY_RESEARCH,
        confidence=0.9,
        rationale_code="COMPANY_QUERY",
    )
    other_candidate = IntentCandidate(
        intent=ChatIntent.GENERAL_RESEARCH,
        confidence=0.1,
        rationale_code="FALLBACK_QUERY",
    )
    intent_request = ChatIntentRequest(
        schema_version="1.0",
        context=_operation_context(),
        session_id=session_id,
        message="Summarize the synthetic announcement.",
        recent_user_messages=["Show the latest company disclosure."],
    )
    intent_result = ChatIntentResult(
        schema_version="1.0",
        selected_intent=ChatIntent.COMPANY_RESEARCH,
        candidates=[intent_candidate, other_candidate],
        classifier_version="intent-v1",
        classified_at=NOW,
    )
    chat_request = ChatRequest(
        schema_version="1.0",
        context=_operation_context(),
        session_id=session_id,
        message="Summarize the synthetic announcement.",
        prior_turn_ids=[uuid4()],
        intent_hint=intent_result.selected_intent,
    )
    retrieval_request = ChatRetrievalRequest(
        schema_version="1.0",
        context=_operation_context(),
        session_id=session_id,
        query_text=chat_request.message,
        intent=intent_result.selected_intent,
        since=NOW - timedelta(days=1),
        until=NOW,
    )
    retrieval_result = ChatRetrievalResult(
        schema_version="1.0",
        hits=[hit],
        as_of=NOW,
        index_version="episodic-v1",
        embedding_model="embedding-v1",
        reranker_model="reranker-v1",
    )
    claim = _draft_claim(evidence_ids=[evidence_id])
    grounding_request = ChatGroundingRequest(
        schema_version="1.0",
        context=_operation_context(),
        session_id=session_id,
        draft_text="A synthetic evidence-grounded statement.",
        claims=[claim],
        available_evidence=[hit],
    )
    grounded_claim = GroundedClaim(
        claim=claim,
        supported=True,
        support_score=0.95,
        validator_codes=["SUPPORTED"],
    )
    grounding_result = ChatGroundingResult(
        schema_version="1.0",
        status=GroundingStatus.GROUNDED,
        safe_text="A synthetic evidence-grounded statement [1].",
        claims=[grounded_claim],
        validator_version="grounding-v1",
        validated_at=NOW,
    )
    citation = ChatCitation(marker="[1]", evidence_id=evidence_id)
    response = ChatResponse(
        schema_version="1.0",
        session_id=session_id,
        intent=intent_result.selected_intent,
        status=grounding_result.status,
        answer=grounding_result.safe_text,
        claims=grounding_result.claims,
        citations=[citation],
        as_of=NOW,
        generated_at=NOW + timedelta(seconds=1),
        model_version="chat-model-v1",
        prompt_hash="f" * 64,
        is_paper_only=True,
    )
    turn = ChatTurn(
        schema_version="1.0",
        session_id=session_id,
        role=ChatRole.ASSISTANT,
        content=response.answer,
        created_at=response.generated_at,
        intent=response.intent,
        evidence_ids=[evidence_id],
        response_id=response.response_id,
    )
    turn_write = ChatTurnWriteRequest(
        schema_version="1.0",
        context=_mutation_context(),
        turn=turn,
    )
    history_query = ChatHistoryQuery(
        schema_version="1.0",
        context=_operation_context(),
        session_id=session_id,
    )
    history = ChatHistoryPage(
        schema_version="1.0",
        page=CursorPage[ChatTurn](schema_version="1.0", items=[turn], as_of=NOW),
    )

    assert intent_request.session_id == chat_request.session_id
    assert retrieval_request.until == retrieval_result.as_of
    assert grounding_request.claims[0].evidence_ids == [evidence_id]
    assert response.citations[0].evidence_id == evidence_id
    assert response.is_paper_only is True
    assert turn_write.context.idempotency_key == "workflow-contract-test-0001"
    assert history_query.session_id == history.page.items[0].session_id


def test_chat_intent_result_rejects_absent_and_duplicate_candidates() -> None:
    company = IntentCandidate(
        intent=ChatIntent.COMPANY_RESEARCH,
        confidence=0.9,
        rationale_code="COMPANY_QUERY",
    )
    with pytest.raises(ValidationError, match="must appear in candidates"):
        ChatIntentResult(
            schema_version="1.0",
            selected_intent=ChatIntent.POLICY_IMPACT,
            candidates=[company],
            classifier_version="intent-v1",
            classified_at=NOW,
        )
    with pytest.raises(ValidationError, match="candidates must be unique"):
        ChatIntentResult(
            schema_version="1.0",
            selected_intent=ChatIntent.COMPANY_RESEARCH,
            candidates=[company, company.model_copy()],
            classifier_version="intent-v1",
            classified_at=NOW,
        )


def test_chat_retrieval_rejects_invalid_windows_and_evidence_visibility() -> None:
    request_base: dict[str, object] = {
        "schema_version": "1.0",
        "context": _operation_context(),
        "session_id": uuid4(),
        "query_text": "Synthetic query",
        "intent": ChatIntent.GENERAL_RESEARCH,
    }
    for updates, message in [
        (
            {"since": NOW - timedelta(hours=1), "until": NOW - timedelta(hours=2)},
            "until cannot be earlier",
        ),
        ({"until": NOW + timedelta(seconds=1)}, "until cannot be later"),
    ]:
        values = dict(request_base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            ChatRetrievalRequest.model_validate(values)

    hit = _chat_hit()
    with pytest.raises(ValidationError, match="unique evidence IDs"):
        ChatRetrievalResult(
            schema_version="1.0",
            hits=[hit, hit.model_copy()],
            as_of=NOW,
            index_version="episodic-v1",
        )
    future_hit = _chat_hit(
        evidence=_evidence(first_seen_at=NOW + timedelta(seconds=1)),
    )
    with pytest.raises(ValidationError, match="first seen after as_of"):
        ChatRetrievalResult(
            schema_version="1.0",
            hits=[future_hit],
            as_of=NOW,
            index_version="episodic-v1",
        )


def test_draft_claim_rejects_ungrounded_or_misshaped_claims() -> None:
    for nature in [ClaimNature.FACT, ClaimNature.INFERENCE, ClaimNature.PROBABILITY_PREDICTION]:
        with pytest.raises(ValidationError, match="claims require evidence"):
            DraftClaim(
                nature=nature,
                statement="Unsupported synthetic claim.",
                confidence=0.5,
            )

    with pytest.raises(ValidationError, match="predictions require prediction_horizon_minutes"):
        _draft_claim(
            evidence_ids=[uuid4()],
            nature=ClaimNature.PROBABILITY_PREDICTION,
        )
    with pytest.raises(ValidationError, match="only valid for predictions"):
        _draft_claim(
            evidence_ids=[],
            nature=ClaimNature.SAFETY_NOTICE,
            prediction_horizon_minutes=10,
        )

    prediction = _draft_claim(
        evidence_ids=[uuid4()],
        nature=ClaimNature.PROBABILITY_PREDICTION,
        prediction_horizon_minutes=10,
    )
    safety_notice = _draft_claim(evidence_ids=[], nature=ClaimNature.SAFETY_NOTICE)
    assert prediction.prediction_horizon_minutes == 10
    assert safety_notice.evidence_ids == []


def test_chat_grounding_request_rejects_unavailable_evidence() -> None:
    hit = _chat_hit()
    claim = _draft_claim(evidence_ids=[uuid4()])
    with pytest.raises(ValidationError, match="outside the available evidence pack"):
        ChatGroundingRequest(
            schema_version="1.0",
            context=_operation_context(),
            session_id=uuid4(),
            draft_text=claim.statement,
            claims=[claim],
            available_evidence=[hit],
        )


@pytest.mark.parametrize(
    ("status", "supports", "message"),
    [
        (GroundingStatus.GROUNDED, [], "grounded status requires"),
        (GroundingStatus.GROUNDED, [False], "grounded status requires"),
        (GroundingStatus.PARTIALLY_GROUNDED, [], "partially_grounded requires"),
        (GroundingStatus.PARTIALLY_GROUNDED, [True], "partially_grounded requires"),
        (GroundingStatus.PARTIALLY_GROUNDED, [False], "partially_grounded requires"),
        (GroundingStatus.ABSTAINED, [True], "cannot contain supported claims"),
        (GroundingStatus.REJECTED, [True], "cannot contain supported claims"),
    ],
)
def test_chat_grounding_result_rejects_status_claim_mismatch(
    status: GroundingStatus, supports: list[bool], message: str
) -> None:
    evidence_id = uuid4()
    claims = [_grounded_claim(evidence_ids=[evidence_id], supported=value) for value in supports]
    with pytest.raises(ValidationError, match=message):
        ChatGroundingResult(
            schema_version="1.0",
            status=status,
            safe_text="A safe synthetic response.",
            claims=claims,
            validator_version="grounding-v1",
            validated_at=NOW,
        )


def test_chat_grounding_result_accepts_partial_and_abstained_states() -> None:
    evidence_id = uuid4()
    supported = _grounded_claim(evidence_ids=[evidence_id], supported=True)
    unsupported = _grounded_claim(evidence_ids=[evidence_id], supported=False)
    partial = ChatGroundingResult(
        schema_version="1.0",
        status=GroundingStatus.PARTIALLY_GROUNDED,
        safe_text="One supported and one unsupported synthetic claim.",
        claims=[supported, unsupported],
        validator_version="grounding-v1",
        validated_at=NOW,
    )
    abstained = ChatGroundingResult(
        schema_version="1.0",
        status=GroundingStatus.ABSTAINED,
        safe_text="Insufficient evidence; abstaining.",
        claims=[unsupported],
        validator_version="grounding-v1",
        validated_at=NOW,
    )
    assert [item.supported for item in partial.claims] == [True, False]
    assert not abstained.claims[0].supported


def _chat_response_values(
    *,
    status: GroundingStatus = GroundingStatus.GROUNDED,
    claims: list[GroundedClaim] | None = None,
    citations: list[ChatCitation] | None = None,
) -> dict[str, object]:
    if claims is None:
        evidence_id = uuid4()
        claims = [_grounded_claim(evidence_ids=[evidence_id], supported=True)]
        citations = [ChatCitation(marker="[1]", evidence_id=evidence_id)]
    return {
        "schema_version": "1.0",
        "session_id": uuid4(),
        "intent": ChatIntent.COMPANY_RESEARCH,
        "status": status,
        "answer": "A safe synthetic response.",
        "claims": claims,
        "citations": citations or [],
        "as_of": NOW,
        "generated_at": NOW + timedelta(seconds=1),
        "model_version": "chat-model-v1",
        "prompt_hash": "f" * 64,
        "is_paper_only": True,
    }


def test_chat_response_rejects_timeline_and_citation_inconsistency() -> None:
    values = _chat_response_values()
    with pytest.raises(ValidationError, match="generated_at cannot be earlier"):
        ChatResponse.model_validate({**values, "generated_at": NOW - timedelta(seconds=1)})

    evidence_id = uuid4()
    claim = _grounded_claim(evidence_ids=[evidence_id], supported=True)
    with pytest.raises(ValidationError, match="unique evidence IDs"):
        ChatResponse.model_validate(
            _chat_response_values(
                claims=[claim],
                citations=[
                    ChatCitation(marker="[1]", evidence_id=evidence_id),
                    ChatCitation(marker="[2]", evidence_id=evidence_id),
                ],
            )
        )
    with pytest.raises(ValidationError, match="citation markers must be unique"):
        ChatResponse.model_validate(
            _chat_response_values(
                claims=[claim],
                citations=[
                    ChatCitation(marker="[1]", evidence_id=evidence_id),
                    ChatCitation(marker="[1]", evidence_id=uuid4()),
                ],
            )
        )
    with pytest.raises(ValidationError, match="must have a citation"):
        ChatResponse.model_validate(_chat_response_values(claims=[claim], citations=[]))


@pytest.mark.parametrize(
    ("status", "supports", "message"),
    [
        (GroundingStatus.GROUNDED, [], "grounded responses require"),
        (GroundingStatus.GROUNDED, [False], "grounded responses require"),
        (GroundingStatus.PARTIALLY_GROUNDED, [], "partially grounded responses require"),
        (GroundingStatus.PARTIALLY_GROUNDED, [True], "partially grounded responses require"),
        (GroundingStatus.PARTIALLY_GROUNDED, [False], "partially grounded responses require"),
        (GroundingStatus.ABSTAINED, [True], "cannot contain supported claims"),
        (GroundingStatus.REJECTED, [True], "cannot contain supported claims"),
    ],
)
def test_chat_response_rejects_status_claim_mismatch(
    status: GroundingStatus, supports: list[bool], message: str
) -> None:
    evidence_ids = [uuid4() for _ in supports]
    claims = [
        _grounded_claim(evidence_ids=[evidence_id], supported=supported)
        for evidence_id, supported in zip(evidence_ids, supports, strict=True)
    ]
    citations = [
        ChatCitation(marker=f"[{index}]", evidence_id=evidence_id)
        for index, evidence_id in enumerate(evidence_ids, start=1)
    ]
    with pytest.raises(ValidationError, match=message):
        ChatResponse.model_validate(
            _chat_response_values(status=status, claims=claims, citations=citations)
        )


def test_chat_response_accepts_partial_and_abstained_states() -> None:
    first_evidence = uuid4()
    second_evidence = uuid4()
    partial = ChatResponse.model_validate(
        _chat_response_values(
            status=GroundingStatus.PARTIALLY_GROUNDED,
            claims=[
                _grounded_claim(evidence_ids=[first_evidence], supported=True),
                _grounded_claim(evidence_ids=[second_evidence], supported=False),
            ],
            citations=[
                ChatCitation(marker="[1]", evidence_id=first_evidence),
                ChatCitation(marker="[2]", evidence_id=second_evidence),
            ],
        )
    )
    abstained = ChatResponse.model_validate(
        _chat_response_values(
            status=GroundingStatus.ABSTAINED,
            claims=[],
            citations=[],
        )
    )
    assert partial.status is GroundingStatus.PARTIALLY_GROUNDED
    assert abstained.claims == []


def test_all_normalized_content_shapes_construct_with_strict_rights_and_pit() -> None:
    source_record_id = uuid4()
    announcement = _announcement(source_record_id=source_record_id)
    report = _financial_report()
    low = MoneyValue(amount="10.00", currency="CNY")
    high = MoneyValue(amount="12.00", currency="CNY")
    research = _broker_report(target_price_low=low, target_price_high=high)
    unrated_range = _broker_report()
    news = _news()
    social = _social_content()
    source = _source_record(record_id=source_record_id)
    request = ContentNormalizationRequest(
        schema_version="1.0",
        context=_operation_context(),
        source_record=source,
    )
    result = ContentNormalizationResult(
        schema_version="1.0",
        source_record_id=source_record_id,
        items=[announcement],
        normalized_at=NOW,
        normalizer_version="normalizer-v1",
    )
    ignored = ContentNormalizationResult(
        schema_version="1.0",
        source_record_id=uuid4(),
        items=[],
        normalized_at=NOW,
        normalizer_version="normalizer-v1",
        ignored_reason_code="UNSUPPORTED_RECORD",
    )

    assert request.source_record.rights_snapshot_hash == announcement.rights_snapshot_hash
    assert result.items[0].content_kind is NormalizedContentKind.EXCHANGE_ANNOUNCEMENT
    assert report.metrics[0].value.as_tuple().exponent == -2
    assert research.target_price_low is not None
    assert research.target_price_high is not None
    assert research.target_price_high.amount > research.target_price_low.amount
    assert unrated_range.target_price_low is None
    assert news.fact_eligibility is FactEligibility.CONTEXT_ONLY
    assert social.contains_individual_content is False
    assert ignored.ignored_reason_code == "UNSUPPORTED_RECORD"


def test_normalized_content_base_rejects_each_timeline_and_evidence_violation() -> None:
    evidence = _evidence()
    with pytest.raises(ValidationError, match="first_seen_at cannot be earlier"):
        _announcement(
            evidence=[evidence],
            published_at=PUBLISHED,
            first_seen_at=PUBLISHED - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="revision_known_at cannot be earlier"):
        _announcement(
            evidence=[evidence],
            revision_known_at=FIRST_SEEN - timedelta(seconds=1),
        )
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        _announcement(evidence=[evidence, evidence.model_copy()])
    later_evidence = _evidence(first_seen_at=REVISION_KNOWN + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="cannot use evidence learned later"):
        _announcement(evidence=[later_evidence])


def test_financial_metric_and_report_reject_invalid_periods_and_evidence() -> None:
    evidence = _evidence()
    with pytest.raises(ValidationError, match="period_end cannot be earlier"):
        _metric(
            evidence_id=evidence.evidence_id,
            period_start=date(2026, 6, 30),
            period_end=date(2026, 1, 1),
        )

    metric = _metric(evidence_id=evidence.evidence_id)
    with pytest.raises(ValidationError, match="period_end cannot be earlier"):
        _financial_report(
            evidence=[evidence],
            metrics=[metric],
            period_start=date(2026, 6, 30),
            period_end=date(2026, 1, 1),
        )
    with pytest.raises(ValidationError, match="financial metrics must be unique"):
        _financial_report(evidence=[evidence], metrics=[metric, metric.model_copy()])
    with pytest.raises(ValidationError, match="reference unattached evidence"):
        _financial_report(
            evidence=[evidence],
            metrics=[_metric(evidence_id=uuid4())],
        )


def test_broker_research_rejects_incomplete_or_invalid_target_ranges() -> None:
    cny_low = MoneyValue(amount="10.00", currency="CNY")
    cny_high = MoneyValue(amount="12.00", currency="CNY")
    usd_high = MoneyValue(amount="12.00", currency="USD")
    with pytest.raises(ValidationError, match="bounds must be provided together"):
        _broker_report(target_price_low=cny_low)
    with pytest.raises(ValidationError, match="same currency"):
        _broker_report(target_price_low=cny_low, target_price_high=usd_high)
    with pytest.raises(ValidationError, match="cannot be lower"):
        _broker_report(target_price_low=cny_high, target_price_high=cny_low)


def test_social_aggregate_rejects_invalid_window_knowledge_and_shares() -> None:
    with pytest.raises(ValidationError, match="window_ended_at must be later"):
        _social_content(
            window_started_at=NOW - timedelta(hours=1), window_ended_at=NOW - timedelta(hours=1)
        )
    with pytest.raises(ValidationError, match="cannot be known before"):
        _social_content(
            window_ended_at=NOW - timedelta(minutes=20),
            revision_known_at=NOW - timedelta(minutes=30),
        )
    with pytest.raises(ValidationError, match="shares must sum to one"):
        _social_content(positive_share=0.4, negative_share=0.2, neutral_share=0.3)


def test_content_normalization_rejects_future_or_inconsistent_results() -> None:
    future_source = _source_record(
        first_seen_at=NOW + timedelta(seconds=1),
        fetched_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValidationError, match=r"first seen after context\.as_of"):
        ContentNormalizationRequest(
            schema_version="1.0",
            context=_operation_context(),
            source_record=future_source,
        )

    record_id = uuid4()
    item = _announcement(source_record_id=record_id)
    base: dict[str, object] = {
        "schema_version": "1.0",
        "source_record_id": record_id,
        "items": [item],
        "normalized_at": NOW,
        "normalizer_version": "normalizer-v1",
    }
    invalid_cases = [
        ({"items": []}, "empty normalization results require"),
        ({"ignored_reason_code": "IGNORED_WITH_ITEMS"}, "only valid for empty results"),
        ({"source_record_id": uuid4()}, "must retain source_record_id"),
        (
            {"normalized_at": item.revision_known_at - timedelta(seconds=1)},
            "known after normalized_at",
        ),
    ]
    for updates, message in invalid_cases:
        values = dict(base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            ContentNormalizationResult.model_validate(values)


def test_event_workflow_valid_construction_preserves_identity_and_revision() -> None:
    source_record_id = uuid4()
    announcement = _announcement(source_record_id=source_record_id)
    extraction_request = EventExtractionRequest(
        schema_version="1.0",
        context=_operation_context(),
        content=announcement,
        purpose=EventExtractionPurpose.COMPANY_FACT,
    )
    context_request = EventExtractionRequest(
        schema_version="1.0",
        context=_operation_context(),
        content=_news(),
        purpose=EventExtractionPurpose.CONTEXT_EVENT,
    )
    current = _market_event()
    candidate = _market_event(
        event_id=current.event_id,
        cluster_id=current.cluster_id,
        revision=2,
        revision_known_at=NOW - timedelta(minutes=10),
    )
    extraction_result = EventExtractionResult(
        schema_version="1.0",
        source_record_id=source_record_id,
        events=[current],
        extractor_version="event-extractor-v1",
        extracted_at=NOW,
    )
    ignored_result = EventExtractionResult(
        schema_version="1.0",
        source_record_id=source_record_id,
        events=[],
        extractor_version="event-extractor-v1",
        extracted_at=NOW,
        ignored_reason_code="NO_MATERIAL_EVENT",
    )
    validation_request = EventValidationRequest(
        schema_version="1.0",
        context=_operation_context(),
        event=current,
        ruleset_version="event-rules-v1",
    )
    validation_result = EventValidationResult(
        schema_version="1.0",
        event_id=current.event_id,
        valid=True,
        validated_event=current,
        issues=[],
        ruleset_version="event-rules-v1",
        validated_at=NOW,
    )
    invalid_validation = EventValidationResult(
        schema_version="1.0",
        event_id=current.event_id,
        valid=False,
        issues=[_event_error_issue()],
        ruleset_version="event-rules-v1",
        validated_at=NOW,
    )
    revision_request = EventRevisionRequest(
        schema_version="1.0",
        context=_mutation_context(),
        current=current,
        candidate=candidate,
        expected_revision=1,
        comparison_rule_version="event-comparison-v1",
    )
    revision_result = EventRevisionResult(
        schema_version="1.0",
        outcome=EventRevisionOutcome.REVISED,
        previous_revision=1,
        resolved_event=candidate,
        changed_fields=["direction"],
        decided_at=NOW,
        reason_code="MATERIAL_CHANGE",
        comparison_rule_version="event-comparison-v1",
    )
    unchanged_result = EventRevisionResult(
        schema_version="1.0",
        outcome=EventRevisionOutcome.UNCHANGED,
        previous_revision=1,
        resolved_event=current,
        changed_fields=[],
        decided_at=NOW,
        reason_code="NO_CHANGE",
        comparison_rule_version="event-comparison-v1",
    )

    assert extraction_request.content.source_record_id == extraction_result.source_record_id
    assert context_request.content.fact_eligibility is FactEligibility.CONTEXT_ONLY
    assert ignored_result.ignored_reason_code == "NO_MATERIAL_EVENT"
    assert validation_request.event.event_id == validation_result.event_id
    assert invalid_validation.issues[0].severity is ValidationSeverity.ERROR
    assert revision_request.current.event_id == revision_request.candidate.event_id
    assert revision_result.resolved_event.revision == 2
    assert unchanged_result.resolved_event.revision == 1


def test_event_extraction_request_rejects_future_social_and_incompatible_content() -> None:
    future = _announcement().model_copy(update={"revision_known_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValidationError, match=r"cannot be known after context\.as_of"):
        EventExtractionRequest(
            schema_version="1.0",
            context=_operation_context(),
            content=future,
            purpose=EventExtractionPurpose.COMPANY_FACT,
        )
    with pytest.raises(ValidationError, match="social sentiment aggregates cannot enter"):
        EventExtractionRequest(
            schema_version="1.0",
            context=_operation_context(),
            content=_social_content(),
            purpose=EventExtractionPurpose.CONTEXT_EVENT,
        )
    with pytest.raises(ValidationError, match="purpose is incompatible"):
        EventExtractionRequest(
            schema_version="1.0",
            context=_operation_context(),
            content=_announcement(),
            purpose=EventExtractionPurpose.CONTEXT_EVENT,
        )


def test_event_extraction_result_rejects_empty_conflicting_and_future_events() -> None:
    source_record_id = uuid4()
    event = _market_event()
    base: dict[str, object] = {
        "schema_version": "1.0",
        "source_record_id": source_record_id,
        "events": [event],
        "extractor_version": "event-extractor-v1",
        "extracted_at": NOW,
    }
    invalid_cases = [
        ({"events": []}, "empty extraction result requires"),
        ({"ignored_reason_code": "IGNORED_WITH_EVENTS"}, "only valid for an empty result"),
        (
            {"extracted_at": event.revision_known_at - timedelta(seconds=1)},
            "known after extracted_at",
        ),
    ]
    for updates, message in invalid_cases:
        values = dict(base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            EventExtractionResult.model_validate(values)


def test_event_validation_request_and_result_reject_each_invalid_state() -> None:
    event = _market_event()
    future_event = _market_event(revision_known_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="revision cannot be known after"):
        EventValidationRequest(
            schema_version="1.0",
            context=_operation_context(),
            event=future_event,
            ruleset_version="event-rules-v1",
        )

    base: dict[str, object] = {
        "schema_version": "1.0",
        "event_id": event.event_id,
        "valid": True,
        "validated_event": event,
        "issues": [],
        "ruleset_version": "event-rules-v1",
        "validated_at": NOW,
    }
    invalid_cases = [
        ({"validated_event": None}, "valid results require"),
        ({"issues": [_event_error_issue()]}, "valid results require"),
        ({"valid": False, "validated_event": None}, "invalid results require"),
        ({"event_id": uuid4()}, "retain the requested event ID"),
        ({"validated_at": event.revision_known_at - timedelta(seconds=1)}, "cannot precede"),
    ]
    for updates, message in invalid_cases:
        values = dict(base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            EventValidationResult.model_validate(values)


def test_event_revision_request_rejects_each_identity_revision_and_pit_violation() -> None:
    current = _market_event()
    candidate = _market_event(
        event_id=current.event_id,
        cluster_id=current.cluster_id,
        revision=2,
        revision_known_at=NOW - timedelta(minutes=10),
    )
    base: dict[str, object] = {
        "schema_version": "1.0",
        "context": _mutation_context(),
        "current": current,
        "candidate": candidate,
        "expected_revision": 1,
        "comparison_rule_version": "event-comparison-v1",
    }
    different_cluster = _market_event(
        event_id=current.event_id,
        revision=2,
        revision_known_at=NOW - timedelta(minutes=10),
    )
    different_event = _market_event(
        cluster_id=current.cluster_id,
        revision=2,
        revision_known_at=NOW - timedelta(minutes=10),
    )
    future_current = _market_event(
        event_id=current.event_id,
        cluster_id=current.cluster_id,
        revision_known_at=NOW + timedelta(seconds=1),
    )
    future_candidate = _market_event(
        event_id=current.event_id,
        cluster_id=current.cluster_id,
        revision=2,
        revision_known_at=NOW + timedelta(seconds=1),
    )
    earlier_candidate = _market_event(
        event_id=current.event_id,
        cluster_id=current.cluster_id,
        revision=2,
        revision_known_at=current.revision_known_at - timedelta(seconds=1),
    )
    invalid_cases = [
        ({"candidate": different_cluster}, "share a cluster ID"),
        ({"candidate": different_event}, "retain the stable event ID"),
        ({"expected_revision": 2}, "match expected_revision"),
        (
            {"current": future_current, "candidate": future_candidate},
            "current revision cannot be known",
        ),
        ({"candidate": future_candidate}, "candidate revision cannot be known"),
        ({"candidate": earlier_candidate}, "cannot predate the current revision"),
    ]
    for updates, message in invalid_cases:
        values = dict(base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            EventRevisionRequest.model_validate(values)


def test_event_revision_result_rejects_each_outcome_revision_and_timeline_violation() -> None:
    current = _market_event()
    revised = _market_event(
        event_id=current.event_id,
        cluster_id=current.cluster_id,
        revision=2,
        revision_known_at=NOW - timedelta(minutes=10),
    )
    base: dict[str, object] = {
        "schema_version": "1.0",
        "outcome": EventRevisionOutcome.REVISED,
        "previous_revision": 1,
        "resolved_event": revised,
        "changed_fields": ["direction"],
        "decided_at": NOW,
        "reason_code": "MATERIAL_CHANGE",
        "comparison_rule_version": "event-comparison-v1",
    }
    invalid_cases = [
        ({"resolved_event": current}, "increment the revision exactly once"),
        (
            {
                "outcome": EventRevisionOutcome.UNCHANGED,
                "resolved_event": revised,
                "changed_fields": [],
            },
            "must retain the previous revision",
        ),
        ({"changed_fields": []}, "changed events require changed_fields"),
        (
            {"outcome": EventRevisionOutcome.REJECTED, "resolved_event": current},
            "cannot list changed_fields",
        ),
        ({"decided_at": revised.revision_known_at - timedelta(seconds=1)}, "cannot precede"),
    ]
    for updates, message in invalid_cases:
        values = dict(base)
        values.update(updates)
        with pytest.raises(ValidationError, match=message):
            EventRevisionResult.model_validate(values)
