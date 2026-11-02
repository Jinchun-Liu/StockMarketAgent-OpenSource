"""Deterministic zero-hop/one-hop matching of global events into research packets."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Final
from uuid import UUID

from stock_assistant.contracts.events import Direction, MarketEvent
from stock_assistant.contracts.global_events import (
    GlobalEventCatalogSlice,
    GlobalEventCatalogStatus,
    ResearchExposureDirectness,
    ResearchExternalImpactDirection,
    ResearchExternalImpactMatch,
    ResearchImpactChannel,
)
from stock_assistant.contracts.research import (
    CoverageGap,
    ResearchEvidenceCitation,
    ResearchEvidenceFact,
    ResearchEvidencePacket,
)

MATCH_RULE_VERSION: Final = "r2-global-event-match.v1"


def _fact_values(event: MarketEvent, name: str) -> set[str]:
    return {fact.value for fact in event.facts if fact.name == name}


def _direction(event: MarketEvent) -> ResearchExternalImpactDirection:
    declared = _fact_values(event, "impact_direction")
    if len(declared) == 1:
        try:
            return ResearchExternalImpactDirection(next(iter(declared)))
        except ValueError:
            return ResearchExternalImpactDirection.UNKNOWN
    return {
        Direction.POSITIVE: ResearchExternalImpactDirection.SUPPORTIVE,
        Direction.NEGATIVE: ResearchExternalImpactDirection.ADVERSE,
        Direction.MIXED: ResearchExternalImpactDirection.MIXED,
    }.get(event.direction, ResearchExternalImpactDirection.UNKNOWN)


def _channel(event: MarketEvent) -> ResearchImpactChannel | None:
    values = _fact_values(event, "impact_channel")
    if len(values) != 1:
        return None
    try:
        return ResearchImpactChannel(next(iter(values)))
    except ValueError:
        return None


def match_external_events(
    packet: ResearchEvidencePacket,
    events: list[MarketEvent],
) -> list[ResearchExternalImpactMatch]:
    """Match only structured identities and evidence-backed exposure edges."""

    profile = packet.exposure_profile
    if profile is None:
        return []
    matches: list[ResearchExternalImpactMatch] = []
    for event in events:
        if event.revision_known_at > packet.as_of:
            continue
        channel = _channel(event)
        if channel is None:
            continue
        event_evidence_ids = [item.evidence_id for item in event.evidence]
        directness: ResearchExposureDirectness | None = None
        hop_count = 0
        path: list[str] = []
        exposure_evidence_ids: list[UUID] = []
        if packet.instrument_id in event.security_ids:
            directness = ResearchExposureDirectness.OFFICIAL_DIRECT_NAMING
            path = [str(event.event_id), packet.instrument_id]
        elif profile.company_id is not None and profile.company_id in event.company_ids:
            directness = ResearchExposureDirectness.COMPANY_DISCLOSED
            path = [str(event.event_id), profile.company_id]
        else:
            event_industries = _fact_values(event, "industry_id")
            event_regions = set(event.region_ids)
            event_exposure_entities = event_industries | event_regions
            matching_edges = [
                edge
                for edge in profile.edges
                if edge.to_entity_id in event_exposure_entities and edge.hop_count == 1
            ]
            if matching_edges:
                edge = sorted(matching_edges, key=lambda item: item.to_entity_id)[0]
                directness = ResearchExposureDirectness.ONE_HOP_EVIDENCE
                hop_count = 1
                path = [str(event.event_id), edge.to_entity_id, packet.instrument_id]
                exposure_evidence_ids = list(edge.evidence_ids)
            elif event_industries & set(profile.industry_ids) or event_regions & set(
                profile.region_ids
            ):
                directness = ResearchExposureDirectness.INDUSTRY_BACKGROUND
                target = (
                    sorted(event_industries & set(profile.industry_ids))[0]
                    if (event_industries & set(profile.industry_ids))
                    else sorted(event_regions & set(profile.region_ids))[0]
                )
                path = [str(event.event_id), target]
        if directness is None:
            continue
        matches.append(
            ResearchExternalImpactMatch(
                event_id=event.event_id,
                event_revision=event.revision,
                event_revision_known_at=event.revision_known_at,
                directness=directness,
                impact_channel=channel,
                direction=_direction(event),
                path=path,
                hop_count=hop_count,
                supporting_evidence_ids=list(
                    dict.fromkeys([*event_evidence_ids, *exposure_evidence_ids])
                ),
                rule_version=MATCH_RULE_VERSION,
                uncertainty=(
                    "行业背景不构成公司特定影响。"
                    if directness is ResearchExposureDirectness.INDUSTRY_BACKGROUND
                    else "影响方向仅在已披露暴露和当前事件修订范围内成立。"
                ),
            )
        )
    by_channel: dict[ResearchImpactChannel, list[ResearchExternalImpactMatch]] = defaultdict(list)
    for match in matches:
        by_channel[match.impact_channel].append(match)
    enriched: list[ResearchExternalImpactMatch] = []
    for match in matches:
        opposite = next(
            (
                candidate
                for candidate in by_channel[match.impact_channel]
                if candidate.event_id != match.event_id
                and candidate.direction != match.direction
                and candidate.direction is not ResearchExternalImpactDirection.UNKNOWN
            ),
            None,
        )
        counter = [] if opposite is None else opposite.supporting_evidence_ids[:1]
        enriched.append(match.model_copy(update={"counter_evidence_ids": counter}))
    return enriched


def enrich_packet_with_global_events(
    packet: ResearchEvidencePacket,
    catalog: GlobalEventCatalogSlice,
) -> ResearchEvidencePacket:
    """Attach PIT-visible event facts, deterministic matches, status and a new manifest."""

    citations = list(packet.citations)
    facts = list(packet.facts)
    gaps = list(packet.coverage_gaps)
    events = catalog.events if catalog.status is GlobalEventCatalogStatus.READY_WITH_MATCHES else []
    for event in events:
        for evidence in event.evidence:
            if any(item.evidence_id == evidence.evidence_id for item in citations):
                continue
            citations.append(
                ResearchEvidenceCitation(
                    evidence_id=evidence.evidence_id,
                    source_provider_id=evidence.source_id,
                    independent_source_group=f"official.{evidence.source_id}",
                    source_role="international_official_event",
                    source_label=evidence.title,
                    published_at=evidence.published_at,
                    first_seen_at=evidence.first_seen_at,
                    revision_known_at=event.revision_known_at,
                    locator=evidence.source_url,
                )
            )
    packet_with_citations = packet.model_copy(update={"citations": citations})
    matches = match_external_events(packet_with_citations, events)
    event_by_id = {(item.event_id, item.revision): item for item in events}
    for match in matches:
        event = event_by_id[(match.event_id, match.event_revision)]
        evidence = event.evidence[0]
        summaries = _fact_values(event, "event_summary")
        summary = next(iter(summaries), evidence.title)
        facts.append(
            ResearchEvidenceFact(
                key=f"external_event.{event.event_id.hex}.{event.revision}",
                statement=f"国际官方事件(合成): {summary}",
                evidence_id=evidence.evidence_id,
            )
        )
    unavailable = {
        GlobalEventCatalogStatus.UNAVAILABLE: "EVENT_CATALOG_UNAVAILABLE",
        GlobalEventCatalogStatus.STALE: "EVENT_CATALOG_STALE",
        GlobalEventCatalogStatus.RIGHTS_BLOCKED: "EVENT_CATALOG_RIGHTS_BLOCKED",
    }
    if catalog.status in unavailable:
        gaps.append(
            CoverageGap(
                area="global_events.catalog",
                reason_code=unavailable[catalog.status],
                detail="国际事件库当前不可用于该会话, 模型不得补全相关事实。",
            )
        )
    elif not matches:
        gaps.append(
            CoverageGap(
                area="global_events.matching",
                reason_code="NO_RELEVANT_GLOBAL_EVENTS",
                detail="国际事件库已刷新, 但没有满足零跳或一跳证据规则的相关事件。",
            )
        )
    manifest = {
        "base": packet.data_manifest_sha256,
        "catalog_status": catalog.status,
        "catalog_watermark": catalog.watermark,
        "matches": [item.model_dump(mode="json") for item in matches],
    }
    manifest_sha = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return ResearchEvidencePacket.model_validate(
        {
            **packet.model_dump(mode="python"),
            "facts": facts,
            "citations": citations,
            "coverage_gaps": gaps,
            "external_event_catalog_status": catalog.status,
            "external_event_catalog_watermark": catalog.watermark,
            "external_impact_matches": matches,
            "data_manifest_sha256": manifest_sha,
        }
    )


__all__ = ["MATCH_RULE_VERSION", "enrich_packet_with_global_events", "match_external_events"]
