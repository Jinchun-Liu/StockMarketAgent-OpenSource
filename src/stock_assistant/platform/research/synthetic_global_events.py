"""Original fixed global official-event fixtures for zero-network R2 verification."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid5

from stock_assistant.contracts.events import (
    Direction,
    EventFact,
    EventStatus,
    EvidenceRef,
    ImpactHorizon,
    MarketEvent,
)
from stock_assistant.contracts.ingestion import ContentTier, FactEligibility

GLOBAL_EVENT_CATALOG_SOURCE_ID: Final = "r2_global_official_synthetic"
GLOBAL_EVENT_FIXTURE_VERSION: Final = "r2-global-events.v1"
GLOBAL_EVENT_RIGHTS_HASH: Final = hashlib.sha256(
    b"stock-market-agent:r2-global-events:synthetic-rights:v1"
).hexdigest()
GLOBAL_EVENT_RULE_VERSION: Final = "r2-global-event-normalization.v1"
_NAMESPACE: Final = UUID("ea67feac-4d9f-5f1d-8d84-5f1924f412d6")


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _evidence(source_id: str, slug: str, title: str, known_at: datetime) -> EvidenceRef:
    content_hash = hashlib.sha256(f"{source_id}:{slug}:{title}".encode()).hexdigest()
    return EvidenceRef(
        evidence_id=uuid5(_NAMESPACE, f"evidence:{source_id}:{slug}"),
        source_id=source_id,
        source_url=f"synthetic://r2-global/{source_id}/{slug}",
        title=title,
        published_at=known_at,
        first_seen_at=known_at,
        content_sha256=content_hash,
        license_id="sma-original-synthetic-v1",
        rights_snapshot_hash=GLOBAL_EVENT_RIGHTS_HASH,
        content_tier=ContentTier.OFFICIAL_POLICY,
        fact_eligibility=FactEligibility.POLICY_FACT,
    )


def _facts(
    evidence: EvidenceRef,
    *,
    industry_id: str,
    channel: str,
    direction: str,
    summary: str,
) -> list[EventFact]:
    return [
        EventFact(
            name="industry_id",
            value=industry_id,
            evidence_id=evidence.evidence_id,
            evidence_span="synthetic structured industry scope",
        ),
        EventFact(
            name="impact_channel",
            value=channel,
            evidence_id=evidence.evidence_id,
            evidence_span="synthetic structured impact channel",
        ),
        EventFact(
            name="impact_direction",
            value=direction,
            evidence_id=evidence.evidence_id,
            evidence_span="synthetic structured conditional direction",
        ),
        EventFact(
            name="event_summary",
            value=summary,
            evidence_id=evidence.evidence_id,
            evidence_span="synthetic official-event summary",
        ),
    ]


def synthetic_global_events() -> tuple[MarketEvent, ...]:
    """Return revisions in append order; none names a user or watched instrument."""

    export_event_id = uuid5(_NAMESPACE, "event:technology-control")
    export_cluster_id = uuid5(_NAMESPACE, "cluster:technology-control")
    original_known = _at("2024-04-18T08:00:00")
    original_evidence = _evidence(
        "r2_synthetic_multilateral_trade",
        "technology-control-original",
        "原创合成国际技术管制公告",
        original_known,
    )
    original = MarketEvent(
        schema_version="1.0",
        event_id=export_event_id,
        cluster_id=export_cluster_id,
        revision=1,
        region_ids=["region:global"],
        event_type="international.technology_control",
        event_status=EventStatus.ANNOUNCED,
        direction=Direction.NEGATIVE,
        materiality=0.75,
        impact_horizon=ImpactHorizon.LONG_TERM,
        confidence=0.86,
        event_time=original_known,
        published_at=original_known,
        first_seen_at=original_known,
        revision_known_at=original_known,
        facts=_facts(
            original_evidence,
            industry_id="industry:semiconductor",
            channel="technology_control",
            direction="adverse",
            summary="合成公告收紧了特定技术类别的跨境供应条件。",
        ),
        evidence=[original_evidence],
        extractor_version="r2-global-synthetic-extractor.v1",
        rule_version=GLOBAL_EVENT_RULE_VERSION,
    )
    correction_known = _at("2024-05-02T09:30:00")
    correction_evidence = _evidence(
        "r2_synthetic_multilateral_trade",
        "technology-control-correction",
        "原创合成国际技术管制更正公告",
        correction_known,
    )
    correction = MarketEvent(
        schema_version="1.0",
        event_id=export_event_id,
        cluster_id=export_cluster_id,
        revision=2,
        region_ids=["region:global"],
        event_type="international.technology_control",
        event_status=EventStatus.CORRECTED,
        direction=Direction.MIXED,
        materiality=0.68,
        impact_horizon=ImpactHorizon.LONG_TERM,
        confidence=0.91,
        event_time=original_known,
        published_at=correction_known,
        first_seen_at=correction_known,
        revision_known_at=correction_known,
        facts=_facts(
            correction_evidence,
            industry_id="industry:semiconductor",
            channel="technology_control",
            direction="mixed",
            summary="合成更正缩小了受限技术范围, 但保留部分约束。",
        ),
        evidence=[correction_evidence],
        extractor_version="r2-global-synthetic-extractor.v1",
        rule_version=GLOBAL_EVENT_RULE_VERSION,
    )
    subsidy_known = _at("2024-06-12T07:45:00")
    subsidy_evidence = _evidence(
        "r2_synthetic_development_agency",
        "semiconductor-subsidy",
        "原创合成国际产业补贴决定",
        subsidy_known,
    )
    subsidy = MarketEvent(
        schema_version="1.0",
        event_id=uuid5(_NAMESPACE, "event:semiconductor-subsidy"),
        cluster_id=uuid5(_NAMESPACE, "cluster:semiconductor-subsidy"),
        revision=1,
        region_ids=["region:global"],
        event_type="international.industrial_subsidy",
        event_status=EventStatus.ANNOUNCED,
        direction=Direction.POSITIVE,
        materiality=0.61,
        impact_horizon=ImpactHorizon.LONG_TERM,
        confidence=0.83,
        event_time=subsidy_known,
        published_at=subsidy_known,
        first_seen_at=subsidy_known,
        revision_known_at=subsidy_known,
        facts=_facts(
            subsidy_evidence,
            industry_id="industry:semiconductor",
            channel="subsidy",
            direction="supportive",
            summary="合成决定扩大了半导体产业研发支持范围。",
        ),
        evidence=[subsidy_evidence],
        extractor_version="r2-global-synthetic-extractor.v1",
        rule_version=GLOBAL_EVENT_RULE_VERSION,
    )
    subsidy_offset_known = _at("2024-06-20T08:20:00")
    subsidy_offset_evidence = _evidence(
        "r2_synthetic_trade_review_board",
        "semiconductor-subsidy-review",
        "原创合成国际产业补贴复核决定",
        subsidy_offset_known,
    )
    subsidy_offset = MarketEvent(
        schema_version="1.0",
        event_id=uuid5(_NAMESPACE, "event:semiconductor-subsidy-review"),
        cluster_id=uuid5(_NAMESPACE, "cluster:semiconductor-subsidy-review"),
        revision=1,
        region_ids=["region:global"],
        event_type="international.industrial_subsidy_review",
        event_status=EventStatus.ANNOUNCED,
        direction=Direction.NEGATIVE,
        materiality=0.49,
        impact_horizon=ImpactHorizon.WEEKS,
        confidence=0.81,
        event_time=subsidy_offset_known,
        published_at=subsidy_offset_known,
        first_seen_at=subsidy_offset_known,
        revision_known_at=subsidy_offset_known,
        facts=_facts(
            subsidy_offset_evidence,
            industry_id="industry:semiconductor",
            channel="subsidy",
            direction="adverse",
            summary="合成复核决定增加了部分补贴资格的不确定性。",
        ),
        evidence=[subsidy_offset_evidence],
        extractor_version="r2-global-synthetic-extractor.v1",
        rule_version=GLOBAL_EVENT_RULE_VERSION,
    )
    agriculture_known = _at("2024-07-08T06:00:00")
    agriculture_evidence = _evidence(
        "r2_synthetic_food_agency",
        "agriculture-logistics",
        "原创合成国际农业物流公告",
        agriculture_known,
    )
    irrelevant = MarketEvent(
        schema_version="1.0",
        event_id=uuid5(_NAMESPACE, "event:agriculture-logistics"),
        cluster_id=uuid5(_NAMESPACE, "cluster:agriculture-logistics"),
        revision=1,
        region_ids=["region:global"],
        event_type="international.logistics",
        event_status=EventStatus.ANNOUNCED,
        direction=Direction.NEUTRAL,
        materiality=0.42,
        impact_horizon=ImpactHorizon.WEEKS,
        confidence=0.79,
        event_time=agriculture_known,
        published_at=agriculture_known,
        first_seen_at=agriculture_known,
        revision_known_at=agriculture_known,
        facts=_facts(
            agriculture_evidence,
            industry_id="industry:agriculture",
            channel="logistics",
            direction="unknown",
            summary="合成公告调整了农业物流检验流程。",
        ),
        evidence=[agriculture_evidence],
        extractor_version="r2-global-synthetic-extractor.v1",
        rule_version=GLOBAL_EVENT_RULE_VERSION,
    )
    return original, correction, subsidy, subsidy_offset, irrelevant


def synthetic_global_event_manifest_sha256(events: tuple[MarketEvent, ...]) -> str:
    payload = {
        "fixture_version": GLOBAL_EVENT_FIXTURE_VERSION,
        "events": [event.model_dump(mode="json") for event in events],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GLOBAL_EVENT_CATALOG_SOURCE_ID",
    "GLOBAL_EVENT_FIXTURE_VERSION",
    "GLOBAL_EVENT_RIGHTS_HASH",
    "GLOBAL_EVENT_RULE_VERSION",
    "synthetic_global_event_manifest_sha256",
    "synthetic_global_events",
]
