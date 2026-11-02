"""Compose a deterministic synthetic briefing for onboarding and regression checks."""

from datetime import UTC, datetime
from uuid import UUID

from stock_assistant.contracts.briefings import (
    Briefing,
    BriefingChangeType,
    BriefingItem,
    BriefingKind,
    SourceHealth,
    SourceHealthStatus,
)
from stock_assistant.contracts.events import (
    Direction,
    EventFact,
    EventStatus,
    EvidenceRef,
    ImpactHorizon,
    MarketEvent,
)
from stock_assistant.contracts.ingestion import ContentTier, FactEligibility
from stock_assistant.modules.events import compare_event_revision

_CLUSTER_ID = UUID("00000000-0000-4000-8000-000000000101")
_PREVIOUS_EVENT_ID = UUID("00000000-0000-4000-8000-000000000102")
_CURRENT_EVENT_ID = UUID("00000000-0000-4000-8000-000000000103")
_PREVIOUS_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000104")
_CURRENT_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000105")
_BRIEFING_ID = UUID("00000000-0000-4000-8000-000000000106")


def _evidence(
    *,
    evidence_id: UUID,
    title: str,
    published_at: datetime,
    first_seen_at: datetime,
    content_hash: str,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        source_id="synthetic_exchange",
        source_url=f"https://example.invalid/announcement/{evidence_id}",
        title=title,
        published_at=published_at,
        first_seen_at=first_seen_at,
        content_sha256=content_hash,
        license_id="synthetic-test-data",
        rights_snapshot_hash="b" * 64,
        content_tier=ContentTier.STATUTORY_DISCLOSURE,
        fact_eligibility=FactEligibility.COMPANY_FACT,
    )


def _event(
    *,
    event_id: UUID,
    revision: int,
    status: EventStatus,
    value: str,
    evidence: EvidenceRef,
) -> MarketEvent:
    return MarketEvent(
        schema_version="1.0",
        event_id=event_id,
        cluster_id=_CLUSTER_ID,
        revision=revision,
        security_ids=["SSE:600000"],
        company_ids=["synthetic-company"],
        event_type="shareholder_reduction",
        event_status=status,
        direction=Direction.NEGATIVE,
        materiality=0.82,
        impact_horizon=ImpactHorizon.DAYS,
        confidence=0.99,
        event_time=evidence.published_at,
        published_at=evidence.published_at,
        first_seen_at=evidence.first_seen_at,
        revision_known_at=evidence.first_seen_at,
        facts=[
            EventFact(
                name="planned_reduction_percentage",
                value=value,
                unit="percent",
                period="2026Q3",
                evidence_id=evidence.evidence_id,
                evidence_span=f"合成样例: 拟减持比例由原值更正为 {value}%",
            )
        ],
        evidence=[evidence],
        extractor_version="synthetic-demo-v1",
        rule_version="demo-rules-v1",
    )


def build_synthetic_premarket_demo() -> Briefing:
    """Build a network-free correction briefing with an explicit rights gap."""

    previous_evidence = _evidence(
        evidence_id=_PREVIOUS_EVIDENCE_ID,
        title="合成公告: 股东减持计划",
        published_at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        first_seen_at=datetime(2026, 7, 21, 0, 1, tzinfo=UTC),
        content_hash="a" * 64,
    )
    current_evidence = _evidence(
        evidence_id=_CURRENT_EVIDENCE_ID,
        title="合成更正公告: 股东减持计划",
        published_at=datetime(2026, 7, 22, 0, 30, tzinfo=UTC),
        first_seen_at=datetime(2026, 7, 22, 0, 35, tzinfo=UTC),
        content_hash="c" * 64,
    )
    previous = _event(
        event_id=_PREVIOUS_EVENT_ID,
        revision=1,
        status=EventStatus.ANNOUNCED,
        value="5",
        evidence=previous_evidence,
    )
    current = _event(
        event_id=_CURRENT_EVENT_ID,
        revision=2,
        status=EventStatus.CORRECTED,
        value="3",
        evidence=current_evidence,
    )
    change_type = BriefingChangeType(compare_event_revision(previous, current).value)
    as_of = datetime(2026, 7, 22, 1, 5, tzinfo=UTC)

    return Briefing(
        schema_version="1.0",
        briefing_id=_BRIEFING_ID,
        kind=BriefingKind.PREMARKET,
        generated_at=as_of,
        as_of=as_of,
        items=[
            BriefingItem(
                headline="合成更正: 拟减持比例由 5% 更正为 3%",
                fact_summary="合成更正公告把 2026Q3 拟减持比例从 5% 更正为 3%。",
                inference=None,
                change_type=change_type,
                source_ids=["synthetic_exchange"],
                published_at=current.published_at,
                first_seen_at=current.first_seen_at,
                event_ids=[current.event_id],
                evidence_ids=[current_evidence.evidence_id],
                confidence=current.confidence,
                is_material_change=True,
                forecast=None,
            )
        ],
        source_health=[
            SourceHealth(
                source_id="synthetic_exchange",
                status=SourceHealthStatus.OK,
                last_success_at=datetime(2026, 7, 22, 1, 3, tzinfo=UTC),
                delay_seconds=120,
            ),
            SourceHealth(
                source_id="cninfo",
                status=SourceHealthStatus.BLOCKED_RIGHTS,
                gap_reason="未配置授权; 真实连接器保持禁用, 未尝试抓取。",
            ),
        ],
        omitted_duplicate_count=2,
        data_delay_seconds=120,
        model_version="deterministic-evidence-only",
        prompt_hash="0" * 64,
        rule_version="demo-rules-v1",
    )
