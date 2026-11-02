"""Shared synthetic fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from stock_assistant.contracts.events import (
    Direction,
    EventFact,
    EventStatus,
    EvidenceRef,
    ImpactHorizon,
    MarketEvent,
)
from stock_assistant.contracts.ingestion import ContentTier, FactEligibility


@pytest.fixture
def event_factory():
    def build(
        *,
        cluster_id: UUID | None = None,
        value: str = "1",
        direction: Direction = Direction.NEGATIVE,
        status: EventStatus = EventStatus.ANNOUNCED,
        revision: int = 1,
        materiality: float = 0.8,
    ) -> MarketEvent:
        published = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
        evidence_id = uuid4()
        first_seen = published + timedelta(seconds=30)
        evidence = EvidenceRef(
            evidence_id=evidence_id,
            source_id="synthetic_exchange",
            source_url="https://example.invalid/announcement/1",
            title="Synthetic announcement",
            published_at=published,
            first_seen_at=first_seen,
            content_sha256="a" * 64,
            license_id="synthetic-test-data",
            rights_snapshot_hash="b" * 64,
            content_tier=ContentTier.STATUTORY_DISCLOSURE,
            fact_eligibility=FactEligibility.COMPANY_FACT,
        )
        fact = EventFact(
            name="planned_reduction_percentage",
            value=value,
            unit="percent",
            period="2026Q3",
            evidence_id=evidence_id,
            evidence_span=f"synthetic value {value}%",
        )
        return MarketEvent(
            schema_version="1.0",
            cluster_id=cluster_id or uuid4(),
            revision=revision,
            security_ids=["SSE:600000"],
            company_ids=["synthetic-company"],
            event_type="shareholder_reduction",
            event_status=status,
            direction=direction,
            materiality=materiality,
            impact_horizon=ImpactHorizon.DAYS,
            confidence=0.95,
            published_at=published,
            first_seen_at=first_seen,
            revision_known_at=first_seen,
            facts=[fact],
            evidence=[evidence],
            extractor_version="test-extractor",
            rule_version="test-rules",
        )

    return build
