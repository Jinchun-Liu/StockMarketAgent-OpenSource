"""Provider-neutral R1 source batches and deterministic demo replay catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from pydantic import HttpUrl

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.event_views import (
    EventEvidenceSource,
    EventEvidenceSourceV1_1,
    EventEvidenceSourceV1_2,
    EventEvidenceView,
    EventEvidenceViewV1_1,
    EventEvidenceViewV1_2,
    EventFactSlot,
    EventRevisionTimeline,
    EventRevisionTimelineItem,
    ReplacementCorrection,
    ReplacementEvidenceRelation,
    TextFactValue,
)
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    FetchBatch,
    SourceAccessChannel,
    SourceAccessChannelV1_2,
    SourceEvidenceRole,
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceRecord,
    SourceReleaseTier,
    SourceRightsStatus,
    SourceRightsStatusV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import (
    SourceAdvanceState,
    SourceCoverageStatus,
    SourceCoverageV1_1,
    SourceCoverageV1_2,
    SourceDataMode,
    SourceIngestionProgress,
    SourceRole,
)
from stock_assistant.modules.ingestion.r1_fixture import (
    FIXTURE_EXTRACTION_VERSION,
    FIXTURE_RULESET_VERSION,
    build_fixture_batches,
    build_market_comparison_events,
)

R1RuntimeMode = SourceRuntimeMode | SourceRuntimeModeV1_2
R1Provenance = SourceProvenance | SourceProvenanceV1_2
R1Coverage = SourceCoverageV1_1 | SourceCoverageV1_2
R1Event = EventEvidenceViewV1_1 | EventEvidenceViewV1_2

DEMO_REPLAY_LICENSE_ID: Final = "synthetic-development-only-v2"
DEMO_REPLAY_RIGHTS_HASH: Final = hashlib.sha256(b"r1-demo-replay-v2").hexdigest()
DEMO_REPLAY_ADAPTER_VERSION: Final = "demo-replay-v2"
DEMO_REPLAY_NAMESPACE: Final = UUID("ed80ca0d-c72a-5d9b-b38d-82dd4c9d6394")
_DEV_LIVE_RIGHTS_HASH: Final = hashlib.sha256(b"akshare-dev-live-metadata-profile-v1").hexdigest()
_FREE_SOURCE_RIGHTS_HASH: Final = hashlib.sha256(
    b"r1-free-metadata-display-summary-nondisplay-v1"
).hexdigest()

R1_CAPABILITIES: Final = (
    "statutory.announcements",
    "financial.changes",
    "market.snapshot",
    "company_industry.news",
    "macro.flashes",
)
R1_FUNCTIONAL_REPLAY_SESSIONS: Final = (
    "2026-07-27",
    "2026-07-28",
    "2026-07-29",
    "2026-07-30",
    "2026-07-31",
)
DEMO_REPLAY_SOURCE_IDS: Final = {
    "statutory.announcements": "demo_replay_statutory",
    "financial.changes": "demo_replay_financial",
    "market.snapshot": "demo_replay_market",
    "company_industry.news": "demo_replay_company_news",
    "macro.flashes": "demo_replay_macro",
}
_CAPABILITY_ROLES: Final = {
    "statutory.announcements": SourceRole.STATUTORY,
    "financial.changes": SourceRole.STATUTORY,
    "market.snapshot": SourceRole.PROFESSIONAL_MARKET,
    "company_industry.news": SourceRole.PROFESSIONAL_INFORMATION,
    "macro.flashes": SourceRole.PROFESSIONAL_INFORMATION,
}
_DISCOVERY_EVENT_TYPES: Final = {
    "statutory.announcements": "statutory.announcement.discovery",
    "company_industry.news": "company.news.discovery",
    "macro.flashes": "macro.flash.discovery",
}


class ReplayScenario(StrEnum):
    """Deterministic replay outcomes used by runtime and failure-path tests."""

    SUCCESS = "success"
    NO_CHANGE = "no_change"
    SOURCE_FAILURE = "source_failure"
    RIGHTS_BLOCKED = "rights_blocked"
    MEDIA_UNCONFIRMED = "media_unconfirmed"
    MACRO_INSUFFICIENT = "macro_insufficient"


@dataclass(frozen=True, slots=True)
class R1SourceBatch:
    """One provider/capability atomic persistence unit."""

    source_id: str
    capability: str
    runtime_mode: R1RuntimeMode
    provenance: R1Provenance
    rights_snapshot_hash: str
    license_id: str
    coverages: tuple[R1Coverage, ...]
    events: tuple[R1Event, ...]
    next_cursor: str | None = None

    @property
    def stream(self) -> str:
        """Return the source-local cursor stream isolated by mode and capability."""

        return f"r1_premarket:{self.runtime_mode.value}:{self.capability}"


def validate_r1_source_batch(batch: R1SourceBatch) -> None:
    """Fail closed on mixed provider, mode, capability, or evidence references."""

    if batch.provenance.runtime_mode.value != batch.runtime_mode.value:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_MODE_MISMATCH")
    if len(batch.coverages) != 1:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_CAPABILITY_BATCH_INVALID")
    coverage = batch.coverages[0]
    if (
        coverage.source_id != batch.source_id
        or coverage.capability != batch.capability
        or coverage.provenance != batch.provenance
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_CAPABILITY_BATCH_MIXED")
    for event in batch.events:
        evidence_ids = {item.evidence_id for item in event.evidence}
        if event.runtime_mode.value != batch.runtime_mode.value:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_MODE_MISMATCH")
        if any(
            item.source_id != batch.source_id
            or item.provenance != batch.provenance
            or item.rights_snapshot_hash != batch.rights_snapshot_hash
            for item in event.evidence
        ):
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_CAPABILITY_BATCH_MIXED")
        if any(not set(fact.evidence_ids).issubset(evidence_ids) for fact in event.facts):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_EVIDENCE_REFERENCE_INVALID",
            )


def r1_source_manifest_hash(batch: R1SourceBatch) -> str:
    """Return a stable identity for one normalized capability batch."""

    validate_r1_source_batch(batch)
    canonical = json.dumps(
        {
            "source_id": batch.source_id,
            "capability": batch.capability,
            "runtime_mode": batch.runtime_mode,
            "provenance": batch.provenance.model_dump(mode="json"),
            "rights_snapshot_hash": batch.rights_snapshot_hash,
            "license_id": batch.license_id,
            "coverages": [item.model_dump(mode="json") for item in batch.coverages],
            "events": [item.model_dump(mode="json") for item in batch.events],
            "next_cursor": batch.next_cursor,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def demo_replay_provenance(capability: str) -> SourceProvenance:
    """Return the development-only lineage for one replay capability."""

    source_id = _source_id(capability)
    return SourceProvenance(
        schema_version="1.1",
        provider="stock_market_agent",
        access_channel=SourceAccessChannel.DEMO_REPLAY,
        adapter_name="r1_demo_replay",
        adapter_version=DEMO_REPLAY_ADAPTER_VERSION,
        endpoint_name=capability.replace(".", "_"),
        source_identifier=source_id,
        runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatus.UNVERIFIED,
    )


def demo_replay_provenance_v1_2(capability: str) -> SourceProvenanceV1_2:
    """Return complete 1.2 lineage for one deterministic replay capability."""

    source_id = _source_id(capability)
    evidence_role = {
        "statutory.announcements": SourceEvidenceRole.AUTHORITATIVE,
        "financial.changes": SourceEvidenceRole.AUTHORITATIVE,
        "market.snapshot": SourceEvidenceRole.STRUCTURED_HELPER,
        "company_industry.news": SourceEvidenceRole.DISCOVERY,
        "macro.flashes": SourceEvidenceRole.AUTHORITATIVE,
    }[capability]
    return SourceProvenanceV1_2(
        schema_version="1.2",
        provider="stock_market_agent",
        access_channel=SourceAccessChannelV1_2.DEMO_REPLAY,
        adapter_name="r1_demo_replay",
        adapter_version=DEMO_REPLAY_ADAPTER_VERSION,
        endpoint_name=capability.replace(".", "_"),
        source_identifier=source_id,
        runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatusV1_2.NOT_APPLICABLE,
        quality_tier=SourceQualityTier.DEMO_REPLAY,
        evidence_role=evidence_role,
    )


def build_demo_replay_batches(
    *,
    window_start: datetime,
    window_end: datetime,
    instrument_id: str,
) -> tuple[R1SourceBatch, ...]:
    """Build the five independent successful replay capabilities."""

    return tuple(
        build_demo_replay_batch(
            capability=capability,
            scenario=ReplayScenario.SUCCESS,
            window_start=window_start,
            window_end=window_end,
            instrument_id=instrument_id,
        )
        for capability in R1_CAPABILITIES
    )


def build_demo_replay_batch(
    *,
    capability: str,
    scenario: ReplayScenario,
    window_start: datetime,
    window_end: datetime,
    instrument_id: str,
) -> R1SourceBatch:
    """Build one deterministic replay batch, including explicit gap scenarios."""

    if capability not in DEMO_REPLAY_SOURCE_IDS:
        raise ValueError(f"unsupported R1 replay capability: {capability}")
    if window_end <= window_start or window_end - window_start < timedelta(hours=7):
        raise ValueError("replay window must be at least seven hours")
    if scenario is ReplayScenario.MEDIA_UNCONFIRMED and capability != "company_industry.news":
        raise ValueError("media_unconfirmed applies only to company_industry.news")
    if scenario is ReplayScenario.MACRO_INSUFFICIENT and capability != "macro.flashes":
        raise ValueError("macro_insufficient applies only to macro.flashes")

    source_id = _source_id(capability)
    provenance = demo_replay_provenance(capability)
    events = _events_for_capability(
        capability=capability,
        source_id=source_id,
        provenance=provenance,
        window_start=window_start,
        window_end=window_end,
        instrument_id=instrument_id,
    )
    status = SourceCoverageStatus.OK
    gap_reason: str | None = None
    advance_state = SourceAdvanceState.ADVANCED
    watermark: datetime | None = max(
        (item.revision_known_at for item in events),
        default=window_end,
    )
    if scenario is ReplayScenario.NO_CHANGE:
        events = ()
    elif scenario is ReplayScenario.SOURCE_FAILURE:
        events = ()
        status = SourceCoverageStatus.UNAVAILABLE
        gap_reason = "R1_REPLAY_SOURCE_FAILURE"
        advance_state = SourceAdvanceState.NOT_ADVANCED
        watermark = None
    elif scenario is ReplayScenario.RIGHTS_BLOCKED:
        events = ()
        status = SourceCoverageStatus.RIGHTS_BLOCKED
        gap_reason = "R1_REPLAY_RIGHTS_BLOCKED"
        advance_state = SourceAdvanceState.NOT_ADVANCED
        watermark = None
    elif scenario is ReplayScenario.MEDIA_UNCONFIRMED:
        status = SourceCoverageStatus.NOT_QUALIFIED
        gap_reason = "R1_MEDIA_UNCONFIRMED"
    elif scenario is ReplayScenario.MACRO_INSUFFICIENT:
        status = SourceCoverageStatus.NOT_QUALIFIED
        gap_reason = "R1_MACRO_RELATION_INSUFFICIENT"

    audit_id = uuid5(
        DEMO_REPLAY_NAMESPACE,
        f"batch:{source_id}:{capability}:{scenario}:{window_end.isoformat()}",
    )
    coverage = SourceCoverageV1_1(
        source_id=source_id,
        capability=capability,
        source_role=_CAPABILITY_ROLES[capability],
        status=status,
        observed_at=window_end,
        last_success_at=watermark,
        license_id=DEMO_REPLAY_LICENSE_ID,
        gap_reason=gap_reason,
        progress=SourceIngestionProgress(
            business_watermark=watermark,
            batch_audit_id=audit_id,
            advance_state=advance_state,
            data_mode=SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY,
        ),
        provenance=provenance,
    )
    batch = R1SourceBatch(
        source_id=source_id,
        capability=capability,
        runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        provenance=provenance,
        rights_snapshot_hash=DEMO_REPLAY_RIGHTS_HASH,
        license_id=DEMO_REPLAY_LICENSE_ID,
        coverages=(coverage,),
        events=events,
        next_cursor=f"replay:{capability}:{window_end.isoformat()}",
    )
    validate_r1_source_batch(batch)
    return batch


def build_demo_replay_batch_v1_2(
    *,
    capability: str,
    scenario: ReplayScenario,
    window_start: datetime,
    window_end: datetime,
    instrument_id: str,
    replay_session_id: str | None = None,
) -> R1SourceBatch:
    """Build a 1.2 replay batch, optionally selecting one fixed five-session outcome."""

    if replay_session_id is not None:
        if replay_session_id not in R1_FUNCTIONAL_REPLAY_SESSIONS:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_REPLAY_SESSION_NOT_ALLOWED",
            )
        if replay_session_id != window_end.date().isoformat():
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_REPLAY_SESSION_WINDOW_MISMATCH",
            )
        scenario = _functional_replay_scenario(replay_session_id, capability)

    legacy = build_demo_replay_batch(
        capability=capability,
        scenario=scenario,
        window_start=window_start,
        window_end=window_end,
        instrument_id=instrument_id,
    )
    provenance = demo_replay_provenance_v1_2(capability)
    events = _functional_replay_events(
        capability=capability,
        replay_session_id=replay_session_id,
        events=legacy.events,
    )
    coverage_payload = legacy.coverages[0].model_dump()
    coverage_payload["provenance"] = provenance
    coverage = SourceCoverageV1_2.model_validate(coverage_payload)
    batch = R1SourceBatch(
        source_id=legacy.source_id,
        capability=capability,
        runtime_mode=SourceRuntimeModeV1_2.DEMO_REPLAY,
        provenance=provenance,
        rights_snapshot_hash=legacy.rights_snapshot_hash,
        license_id=legacy.license_id,
        coverages=(coverage,),
        events=tuple(_replay_event_v1_2(event, provenance) for event in events),
        next_cursor=(
            None
            if coverage.progress.advance_state is SourceAdvanceState.NOT_ADVANCED
            or legacy.next_cursor is None
            else f"{legacy.next_cursor}:{replay_session_id or 'single'}"
        ),
    )
    validate_r1_source_batch(batch)
    return batch


def _functional_replay_scenario(session_id: str, capability: str) -> ReplayScenario:
    if session_id == "2026-07-31" and capability == "macro.flashes":
        return ReplayScenario.SOURCE_FAILURE
    if session_id in {"2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"}:
        active = {
            "2026-07-28": "market.snapshot",
            "2026-07-29": "financial.changes",
            "2026-07-30": "statutory.announcements",
        }.get(session_id)
        if capability != active:
            return ReplayScenario.NO_CHANGE
    return ReplayScenario.SUCCESS


def _functional_replay_events(
    *,
    capability: str,
    replay_session_id: str | None,
    events: tuple[R1Event, ...],
) -> tuple[R1Event, ...]:
    if replay_session_id is None or not events:
        return events
    if replay_session_id == "2026-07-27":
        return (events[0],)
    if replay_session_id == "2026-07-28" and capability == "market.snapshot":
        return (_as_market_update(events[0], events[-1]),)
    if replay_session_id == "2026-07-29" and capability == "financial.changes":
        return (_as_financial_contradiction(events[0], events[-1]),)
    if replay_session_id == "2026-07-30" and capability == "statutory.announcements":
        return (events[-1],)
    return events


def _as_market_update(prior: R1Event, current: R1Event) -> EventEvidenceViewV1_1:
    payload = current.model_dump()
    timeline = current.revision_timeline.model_copy(deep=True)
    timeline.items[0] = timeline.items[0].model_copy(
        update={"change_type": BriefingChangeType.UPDATE}
    )
    payload.update(
        {
            "change_type": BriefingChangeType.UPDATE,
            "title": "兆易创新收盘行情更新",
            "revision_timeline": timeline,
            "facts": [current.facts[-1]],
            "evidence": [prior.evidence[0], current.evidence[-1]],
        }
    )
    return EventEvidenceViewV1_1.model_validate(payload)


def _as_financial_contradiction(prior: R1Event, current: R1Event) -> EventEvidenceViewV1_1:
    payload = current.model_dump()
    timeline = current.revision_timeline.model_copy(deep=True)
    prior_fact = prior.facts[0].model_copy(
        update={
            "period": current.facts[0].period,
            "unit": current.facts[0].unit,
        }
    )
    timeline.items[0] = timeline.items[0].model_copy(
        update={"change_type": BriefingChangeType.CONTRADICTION}
    )
    payload.update(
        {
            "change_type": BriefingChangeType.CONTRADICTION,
            "title": "财务字段来源存在差异",
            "revision_timeline": timeline,
            "facts": [prior_fact, current.facts[0]],
            "evidence": [prior.evidence[0], current.evidence[0]],
        }
    )
    return EventEvidenceViewV1_1.model_validate(payload)


def _replay_event_v1_2(
    event: R1Event,
    provenance: SourceProvenanceV1_2,
) -> EventEvidenceViewV1_2:
    evidence = []
    for item in event.evidence:
        item_payload = item.model_dump()
        item_payload["provenance"] = provenance
        evidence.append(EventEvidenceSourceV1_2.model_validate(item_payload))
    payload = event.model_dump()
    payload.update(
        {
            "schema_version": "1.2",
            "runtime_mode": SourceRuntimeModeV1_2.DEMO_REPLAY,
            "evidence": evidence,
        }
    )
    return EventEvidenceViewV1_2.model_validate(payload)


def source_batch_from_fetch(
    *,
    fetch_batch: FetchBatch,
    capability: str,
    provenance: R1Provenance,
    license_id: str,
) -> R1SourceBatch:
    """Convert one rights-gated provider response into the common R1 batch."""

    if provenance.runtime_mode.value != SourceRuntimeMode.DEV_LIVE.value:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_MODE_MISMATCH")
    expected_source_identifier = (
        f"603986.SH:{fetch_batch.source_id}:{capability}"
        if isinstance(provenance, SourceProvenanceV1_2)
        else f"603986.SH:{capability}"
    )
    if provenance.source_identifier != expected_source_identifier:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    expected_rights_hash = (
        _FREE_SOURCE_RIGHTS_HASH
        if isinstance(provenance, SourceProvenanceV1_2)
        else _DEV_LIVE_RIGHTS_HASH
    )
    if fetch_batch.rights_snapshot_hash != expected_rights_hash:
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "R1_SOURCE_RIGHTS_MISMATCH")
    if any(
        record.capability != capability or record.provenance != provenance
        for record in fetch_batch.records
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_CAPABILITY_BATCH_MIXED")
    watermark = fetch_batch.source_watermark
    if watermark is None:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_WATERMARK_MISSING")
    if any(
        record.event_time is not None
        and (
            record.event_time > watermark
            or (record.published_at is not None and record.published_at > watermark)
        )
        for record in fetch_batch.records
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_FUTURE_RECORD")
    eligible_records = tuple(
        record
        for record in fetch_batch.records
        if _record_is_fact_eligible(record) or _record_is_discovery(record)
    )
    if fetch_batch.records and not eligible_records:
        return terminal_gap_batch(
            source_id=fetch_batch.source_id,
            capability=capability,
            runtime_mode=provenance.runtime_mode,
            provenance=provenance,
            rights_snapshot_hash=fetch_batch.rights_snapshot_hash,
            license_id=license_id,
            observed_at=fetch_batch.completed_at,
            status=SourceCoverageStatus.NOT_QUALIFIED,
            reason_code="R1_SOURCE_FACT_INELIGIBLE",
        )
    events = tuple(
        _event_from_source_record(
            record=record,
            capability=capability,
            provenance=provenance,
            license_id=license_id,
        )
        for record in eligible_records
    )
    coverage = _coverage_from_provenance(
        provenance=provenance,
        payload={
            "source_id": fetch_batch.source_id,
            "capability": capability,
            "source_role": _CAPABILITY_ROLES[capability],
            "status": SourceCoverageStatus.OK,
            "observed_at": fetch_batch.completed_at,
            "last_success_at": fetch_batch.completed_at,
            "license_id": license_id,
            "progress": SourceIngestionProgress(
                business_watermark=watermark,
                batch_audit_id=fetch_batch.batch_id,
                advance_state=SourceAdvanceState.ADVANCED,
                data_mode=SourceDataMode.REAL,
            ),
        },
    )
    batch = R1SourceBatch(
        source_id=fetch_batch.source_id,
        capability=capability,
        runtime_mode=provenance.runtime_mode,
        provenance=provenance,
        rights_snapshot_hash=fetch_batch.rights_snapshot_hash,
        license_id=license_id,
        coverages=(coverage,),
        events=events,
        next_cursor=fetch_batch.next_cursor,
    )
    validate_r1_source_batch(batch)
    return batch


def terminal_gap_batch(
    *,
    source_id: str,
    capability: str,
    runtime_mode: R1RuntimeMode,
    provenance: R1Provenance,
    rights_snapshot_hash: str,
    license_id: str,
    observed_at: datetime,
    status: SourceCoverageStatus,
    reason_code: str,
) -> R1SourceBatch:
    """Build a terminal, non-advancing capability state without fabricated data."""

    if status is SourceCoverageStatus.OK:
        raise ValueError("terminal gap cannot use ok coverage")
    coverage = _coverage_from_provenance(
        provenance=provenance,
        payload={
            "source_id": source_id,
            "capability": capability,
            "source_role": _CAPABILITY_ROLES[capability],
            "status": status,
            "observed_at": observed_at,
            "last_success_at": None,
            "license_id": license_id,
            "gap_reason": reason_code,
            "progress": SourceIngestionProgress(
                business_watermark=None,
                batch_audit_id=uuid5(
                    DEMO_REPLAY_NAMESPACE,
                    f"gap:{source_id}:{runtime_mode}:{capability}:"
                    f"{observed_at.isoformat()}:{reason_code}",
                ),
                advance_state=SourceAdvanceState.NOT_ADVANCED,
                data_mode=(
                    SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY
                    if runtime_mode.value == SourceRuntimeMode.DEMO_REPLAY.value
                    else SourceDataMode.REAL
                ),
            ),
        },
    )
    batch = R1SourceBatch(
        source_id=source_id,
        capability=capability,
        runtime_mode=runtime_mode,
        provenance=provenance,
        rights_snapshot_hash=rights_snapshot_hash,
        license_id=license_id,
        coverages=(coverage,),
        events=(),
        next_cursor=None,
    )
    validate_r1_source_batch(batch)
    return batch


def _event_from_source_record(
    *,
    record: SourceRecord,
    capability: str,
    provenance: R1Provenance,
    license_id: str,
) -> R1Event:
    is_discovery = _record_is_discovery(record)
    if not _record_is_fact_eligible(record) and not is_discovery:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_FACT_INELIGIBLE")
    event_time = record.event_time
    if event_time is None or record.provenance is None:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_PROVENANCE_MISSING")
    published_at = record.published_at or event_time
    known_at = max(record.fetched_at, record.first_seen_at)
    is_official_http = (
        isinstance(provenance, SourceProvenanceV1_2)
        and provenance.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
    )
    logical_identity = f"{record.source_id}:{capability}:{record.external_id}"
    if is_official_http:
        content_identity = f"{logical_identity}:{record.content_sha256}"
    else:
        logical_identity = f"{logical_identity}:{record.content_sha256}"
        content_identity = logical_identity
    evidence_id = uuid5(DEMO_REPLAY_NAMESPACE, f"dev-evidence:{content_identity}")
    event_id = uuid5(DEMO_REPLAY_NAMESPACE, f"dev-event:{logical_identity}")
    audit_id = uuid5(DEMO_REPLAY_NAMESPACE, f"dev-audit:{content_identity}")
    canonical_payload = json.dumps(
        record.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    title = record.title or f"603986.SH {capability}"
    source_url = record.canonical_url or {
        "baostock": "https://www.baostock.com/",
        "cninfo": "https://www.cninfo.com.cn/",
        "cninfo_public": "https://www.cninfo.com.cn/",
        "eastmoney": "https://www.eastmoney.com/",
        "eastmoney_public": "https://www.eastmoney.com/",
        "gov_cn": "https://www.gov.cn/",
        "promax_tushare_compatible": (
            "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71"
        ),
        "quantdata888_tushare_compatible": "https://quantdata888.duckdns.org",
        "miit": "https://www.miit.gov.cn/",
        "qinghong_finance_flash": ("https://my.feishu.cn/wiki/T7XWwCxtIiOcLIkkXQbc5I1Tntc"),
        "sse_public": "https://www.sse.com.cn/",
    }.get(provenance.provider, "https://www.gov.cn/")
    evidence_payload = {
        "evidence_id": evidence_id,
        "source_id": record.source_id,
        "source_role": _CAPABILITY_ROLES[capability],
        "source_url": HttpUrl(source_url),
        "title": title,
        "event_time": event_time,
        "published_at": published_at,
        "first_seen_at": record.first_seen_at,
        "fetched_at": record.fetched_at,
        "revision_known_at": known_at,
        "content_sha256": record.content_sha256
        or hashlib.sha256(canonical_payload.encode()).hexdigest(),
        "license_id": license_id,
        "rights_snapshot_hash": record.rights_snapshot_hash,
        "provenance": provenance,
    }
    evidence: EventEvidenceSourceV1_1 | EventEvidenceSourceV1_2
    if isinstance(provenance, SourceProvenanceV1_2):
        evidence = EventEvidenceSourceV1_2.model_validate(evidence_payload)
    else:
        evidence = EventEvidenceSourceV1_1.model_validate(evidence_payload)
    extraction_version = f"r1-dev-live-normalizer-v{provenance.schema_version}"
    timeline_item = EventRevisionTimelineItem(
        revision=1,
        change_type=BriefingChangeType.NEW,
        revision_known_at=known_at,
        extraction_version=extraction_version,
        ruleset_version=FIXTURE_RULESET_VERSION,
        audit_id=audit_id,
    )
    event_payload = {
        "schema_version": provenance.schema_version,
        "runtime_mode": provenance.runtime_mode,
        "event_id": event_id,
        "cluster_id": uuid5(DEMO_REPLAY_NAMESPACE, f"dev-cluster:{logical_identity}"),
        "revision": 1,
        "change_type": BriefingChangeType.NEW,
        "instrument_ids": ["XSHG:603986"],
        "event_type": (
            _DISCOVERY_EVENT_TYPES[capability]
            if is_discovery
            else {
                "statutory.announcements": "statutory.announcement",
                "financial.changes": "financial.change",
                "market.snapshot": "market.snapshot",
                "company_industry.news": "company.news",
                "macro.flashes": "macro.policy",
            }[capability]
        ),
        "title": title,
        "as_of": known_at,
        "revision_known_at": known_at,
        "extraction_version": extraction_version,
        "ruleset_version": FIXTURE_RULESET_VERSION,
        "audit_id": audit_id,
        "revision_timeline": EventRevisionTimeline(items=[timeline_item], has_older=False),
        "facts": [
            EventFactSlot(
                slot="publication.headline" if is_discovery else capability,
                value=TextFactValue(
                    kind="text",
                    value=title if is_discovery else canonical_payload[:500] or "无新增字段",
                ),
                evidence_ids=[evidence_id],
            )
        ],
        "evidence": [evidence],
    }
    if isinstance(provenance, SourceProvenanceV1_2):
        return EventEvidenceViewV1_2.model_validate(event_payload)
    return EventEvidenceViewV1_1.model_validate(event_payload)


def reconcile_source_event_revision(
    *,
    previous: R1Event,
    candidate: R1Event,
) -> R1Event:
    """Turn changed official metadata for one stable document into a correction revision."""

    if not _is_official_http_event(candidate):
        return candidate
    if not isinstance(previous, EventEvidenceViewV1_2) or not isinstance(
        candidate,
        EventEvidenceViewV1_2,
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REVISION_MODE_MISMATCH")
    if (
        previous.event_id != candidate.event_id
        or previous.event_type != candidate.event_type
        or previous.instrument_ids != candidate.instrument_ids
        or previous.runtime_mode is not candidate.runtime_mode
        or candidate.revision != 1
        or candidate.change_type is not BriefingChangeType.NEW
    ):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_REVISION_IDENTITY_MISMATCH",
        )
    if len(previous.facts) != 1 or len(candidate.facts) != 1:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_REVISION_FACT_SHAPE_INVALID",
        )
    previous_fact = previous.facts[0]
    current_fact = candidate.facts[0]
    if (previous_fact.slot, previous_fact.period, previous_fact.unit) != (
        current_fact.slot,
        current_fact.period,
        current_fact.unit,
    ):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_REVISION_FACT_IDENTITY_MISMATCH",
        )
    previous_evidence_ids = list(previous_fact.evidence_ids)
    current_evidence_ids = list(current_fact.evidence_ids)
    previous_by_id = {item.evidence_id: item for item in previous.evidence}
    candidate_by_id = {item.evidence_id: item for item in candidate.evidence}
    if any(evidence_id not in previous_by_id for evidence_id in previous_evidence_ids) or any(
        evidence_id not in candidate_by_id for evidence_id in current_evidence_ids
    ):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_REVISION_EVIDENCE_MISSING",
        )
    previous_current_evidence = [
        previous_by_id[evidence_id] for evidence_id in previous_evidence_ids
    ]
    candidate_current_evidence = [
        candidate_by_id[evidence_id] for evidence_id in current_evidence_ids
    ]
    previous_hashes = {item.content_sha256 for item in previous_current_evidence}
    candidate_hashes = {item.content_sha256 for item in candidate_current_evidence}
    if previous_hashes == candidate_hashes:
        return previous
    if candidate.revision_known_at <= previous.revision_known_at:
        raise ApplicationError(
            ErrorCode.CONFLICT,
            "R1_SOURCE_REVISION_OUT_OF_ORDER",
            retryable=True,
        )

    prior_evidence = [
        item.model_copy(update={"correction_relation": None}) for item in previous_current_evidence
    ]
    revision = previous.revision + 1
    change_type = (
        BriefingChangeType.UPDATE
        if previous_fact.value == current_fact.value
        else BriefingChangeType.CORRECTION
    )
    timeline_item = EventRevisionTimelineItem(
        revision=revision,
        change_type=change_type,
        revision_known_at=candidate.revision_known_at,
        supersedes_revision=previous.revision,
        extraction_version=candidate.extraction_version,
        ruleset_version=candidate.ruleset_version,
        audit_id=candidate.audit_id,
    )
    previous_timeline = list(previous.revision_timeline.items)
    timeline_items = [timeline_item, *previous_timeline[:49]]
    has_older = previous.revision_timeline.has_older or len(previous_timeline) > 49
    timeline = EventRevisionTimeline(
        items=timeline_items,
        has_older=has_older,
        next_before_revision=timeline_items[-1].revision if has_older else None,
    )
    payload = candidate.model_dump()
    if change_type is BriefingChangeType.UPDATE:
        payload.update(
            {
                "cluster_id": previous.cluster_id,
                "revision": revision,
                "change_type": change_type,
                "supersedes_revision": previous.revision,
                "revision_timeline": timeline,
                "facts": list(candidate.facts),
                "evidence": [
                    item.model_copy(update={"correction_relation": None})
                    for item in candidate_current_evidence
                ],
                "correction": None,
            }
        )
        return EventEvidenceViewV1_2.model_validate(payload)

    current_evidence = [
        item.model_copy(
            update={
                "correction_relation": ReplacementEvidenceRelation(
                    kind="replacement",
                    prior_evidence_id=previous_evidence_ids[0],
                )
            }
        )
        for item in candidate_current_evidence
    ]
    payload.update(
        {
            "cluster_id": previous.cluster_id,
            "revision": revision,
            "change_type": change_type,
            "supersedes_revision": previous.revision,
            "revision_timeline": timeline,
            "facts": list(candidate.facts),
            "evidence": [*prior_evidence, *current_evidence],
            "correction": ReplacementCorrection(
                kind="replacement",
                slot=current_fact.slot,
                unit=current_fact.unit,
                period=current_fact.period,
                previous_value=previous_fact.value,
                current_value=current_fact.value,
                previous_evidence_ids=previous_evidence_ids,
                current_evidence_ids=[item.evidence_id for item in current_evidence],
            ),
        }
    )
    return EventEvidenceViewV1_2.model_validate(payload)


def _is_official_http_event(event: R1Event) -> bool:
    return isinstance(event, EventEvidenceViewV1_2) and all(
        item.provenance.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
        for item in event.evidence
    )


def _record_is_fact_eligible(record: SourceRecord) -> bool:
    if record.content_tier is ContentTier.DEVELOPMENT_ONLY:
        return False
    if record.fact_eligibility not in {
        FactEligibility.COMPANY_FACT,
        FactEligibility.POLICY_FACT,
        FactEligibility.MARKET_OBSERVATION,
    }:
        return False
    provenance = record.provenance
    return not (
        isinstance(provenance, SourceProvenanceV1_2)
        and provenance.evidence_role
        in {SourceEvidenceRole.DISCOVERY, SourceEvidenceRole.CROSS_CHECK}
    )


def _record_is_discovery(record: SourceRecord) -> bool:
    """Allow only the three headline-only DEV_ONLY discovery lanes."""

    provenance = record.provenance
    expected_record_type = _DISCOVERY_EVENT_TYPES.get(record.capability)
    return (
        record.capability in _DISCOVERY_EVENT_TYPES
        and record.record_type == expected_record_type
        and record.fact_eligibility is FactEligibility.CONTEXT_ONLY
        and isinstance(provenance, SourceProvenanceV1_2)
        and provenance.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
        and provenance.release_tier is SourceReleaseTier.DEV_ONLY
        and provenance.rights_status is SourceRightsStatusV1_2.UNVERIFIED
        and provenance.evidence_role is SourceEvidenceRole.DISCOVERY
    )


def _coverage_from_provenance(
    *,
    provenance: R1Provenance,
    payload: dict[str, object],
) -> R1Coverage:
    complete = {**payload, "provenance": provenance}
    if isinstance(provenance, SourceProvenanceV1_2):
        return SourceCoverageV1_2.model_validate(complete)
    return SourceCoverageV1_1.model_validate(complete)


def _source_id(capability: str) -> str:
    try:
        return DEMO_REPLAY_SOURCE_IDS[capability]
    except KeyError as error:
        raise ValueError(f"unsupported R1 replay capability: {capability}") from error


def _events_for_capability(
    *,
    capability: str,
    source_id: str,
    provenance: SourceProvenance,
    window_start: datetime,
    window_end: datetime,
    instrument_id: str,
) -> tuple[EventEvidenceViewV1_1, ...]:
    legacy_batches = build_fixture_batches(
        window_start=window_start,
        window_end=window_end,
        instrument_id=instrument_id,
    )
    legacy_events = tuple(event for batch in legacy_batches for event in batch.events)
    if capability == "statutory.announcements":
        selected = tuple(
            event for event in legacy_events if event.event_type.startswith("statutory.")
        )
    elif capability == "financial.changes":
        selected = tuple(
            event for event in legacy_events if event.event_type.startswith("financial.")
        )
    elif capability == "market.snapshot":
        observations = tuple(
            event
            for event in legacy_events
            if event.event_type == "source_observation.market_close"
        )
        selected = build_market_comparison_events(observations=observations, as_of=window_end)
    elif capability == "company_industry.news":
        selected = tuple(
            event for event in legacy_events if event.event_type.startswith("company.")
        )
    else:
        selected = tuple(event for event in legacy_events if event.event_type.startswith("macro."))
    cloned = tuple(
        _clone_event_for_replay(
            event=event,
            capability=capability,
            source_id=source_id,
            provenance=provenance,
        )
        for event in selected
    )
    if capability == "statutory.announcements":
        return (*cloned, _statutory_correction(cloned[0]))
    return cloned


def _statutory_correction(revision_one: EventEvidenceViewV1_1) -> EventEvidenceViewV1_1:
    prior = revision_one.evidence[0]
    published_at = prior.revision_known_at + timedelta(minutes=1)
    first_seen_at = published_at + timedelta(minutes=1)
    fetched_at = first_seen_at + timedelta(minutes=1)
    known_at = fetched_at + timedelta(minutes=1)
    current_id = uuid5(DEMO_REPLAY_NAMESPACE, f"statutory:correction:{revision_one.event_id}")
    current = EventEvidenceSourceV1_1(
        evidence_id=current_id,
        source_id=prior.source_id,
        source_role=prior.source_role,
        source_url=HttpUrl(f"https://fixture.invalid/{prior.source_id}/{current_id}"),
        title="兆易创新董事会决议更正公告 (合成证据)",
        event_time=published_at,
        published_at=published_at,
        first_seen_at=first_seen_at,
        fetched_at=fetched_at,
        revision_known_at=known_at,
        content_sha256=hashlib.sha256(f"{prior.source_id}:{current_id}".encode()).hexdigest(),
        license_id=prior.license_id,
        rights_snapshot_hash=prior.rights_snapshot_hash,
        correction_relation=ReplacementEvidenceRelation(
            kind="replacement",
            prior_evidence_id=prior.evidence_id,
        ),
        provenance=prior.provenance,
    )
    timeline_item = EventRevisionTimelineItem(
        revision=2,
        change_type=BriefingChangeType.CORRECTION,
        revision_known_at=known_at,
        supersedes_revision=1,
        extraction_version=revision_one.extraction_version,
        ruleset_version=revision_one.ruleset_version,
        audit_id=uuid5(DEMO_REPLAY_NAMESPACE, f"statutory:audit:2:{revision_one.event_id}"),
    )
    previous_value = TextFactValue(kind="text", value="审议通过研发投入计划")
    current_value = TextFactValue(kind="text", value="更正为研发投入上限计划")
    return EventEvidenceViewV1_1(
        schema_version="1.1",
        runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        event_id=revision_one.event_id,
        cluster_id=revision_one.cluster_id,
        revision=2,
        change_type=BriefingChangeType.CORRECTION,
        instrument_ids=list(revision_one.instrument_ids),
        event_type=revision_one.event_type,
        title="兆易创新董事会决议更正公告",
        as_of=revision_one.as_of,
        revision_known_at=known_at,
        supersedes_revision=1,
        extraction_version=revision_one.extraction_version,
        ruleset_version=revision_one.ruleset_version,
        audit_id=timeline_item.audit_id,
        revision_timeline=EventRevisionTimeline(
            items=[timeline_item, revision_one.revision_timeline.items[0]],
            has_older=False,
        ),
        facts=[
            EventFactSlot(
                slot="announcement.subject",
                value=current_value,
                evidence_ids=[current.evidence_id],
            )
        ],
        evidence=[prior, current],
        correction=ReplacementCorrection(
            kind="replacement",
            slot="announcement.subject",
            previous_value=previous_value,
            current_value=current_value,
            previous_evidence_ids=[prior.evidence_id],
            current_evidence_ids=[current.evidence_id],
        ),
    )


def _clone_event_for_replay(
    *,
    event: EventEvidenceView,
    capability: str,
    source_id: str,
    provenance: SourceProvenance,
) -> EventEvidenceViewV1_1:
    evidence_mapping: dict[UUID, UUID] = {}
    evidence: list[EventEvidenceSourceV1_1] = []
    for item in event.evidence:
        evidence_id = uuid5(
            DEMO_REPLAY_NAMESPACE,
            f"{capability}:{event.event_id}:{event.revision}:{item.evidence_id}",
        )
        evidence_mapping[item.evidence_id] = evidence_id
        evidence.append(
            _clone_evidence(
                item=item,
                evidence_id=evidence_id,
                source_id=source_id,
                capability=capability,
                provenance=provenance,
            )
        )
    facts = [
        EventFactSlot(
            slot=fact.slot,
            value=fact.value,
            unit=fact.unit,
            period=fact.period,
            evidence_ids=[evidence_mapping[item] for item in fact.evidence_ids],
        )
        for fact in event.facts
    ]
    timeline = EventRevisionTimeline(
        items=[
            EventRevisionTimelineItem(
                revision=item.revision,
                change_type=item.change_type,
                revision_known_at=item.revision_known_at,
                supersedes_revision=item.supersedes_revision,
                extraction_version=FIXTURE_EXTRACTION_VERSION,
                ruleset_version=FIXTURE_RULESET_VERSION,
                audit_id=uuid5(
                    DEMO_REPLAY_NAMESPACE,
                    f"{capability}:{event.event_id}:audit:{item.revision}",
                ),
            )
            for item in event.revision_timeline.items
        ],
        has_older=event.revision_timeline.has_older,
        next_before_revision=event.revision_timeline.next_before_revision,
    )
    selected_timeline = next(item for item in timeline.items if item.revision == event.revision)
    payload = event.model_dump()
    payload.update(
        {
            "schema_version": "1.1",
            "runtime_mode": SourceRuntimeMode.DEMO_REPLAY,
            "event_id": uuid5(DEMO_REPLAY_NAMESPACE, f"{capability}:event:{event.event_id}"),
            "cluster_id": uuid5(DEMO_REPLAY_NAMESPACE, f"{capability}:cluster:{event.cluster_id}"),
            "audit_id": selected_timeline.audit_id,
            "revision_timeline": timeline,
            "facts": facts,
            "evidence": evidence,
        }
    )
    if event.correction is not None:
        correction = event.correction.model_dump()
        correction["previous_evidence_ids"] = [
            evidence_mapping[item] for item in event.correction.previous_evidence_ids
        ]
        correction["current_evidence_ids"] = [
            evidence_mapping[item] for item in event.correction.current_evidence_ids
        ]
        payload["correction"] = correction
    return EventEvidenceViewV1_1.model_validate(payload)


def _clone_evidence(
    *,
    item: EventEvidenceSource,
    evidence_id: UUID,
    source_id: str,
    capability: str,
    provenance: SourceProvenance,
) -> EventEvidenceSourceV1_1:
    return EventEvidenceSourceV1_1(
        evidence_id=evidence_id,
        source_id=source_id,
        source_role=_CAPABILITY_ROLES[capability],
        source_url=HttpUrl(f"https://fixture.invalid/{source_id}/{evidence_id}"),
        title=item.title,
        event_time=item.published_at,
        published_at=item.published_at,
        first_seen_at=item.first_seen_at,
        fetched_at=item.fetched_at,
        revision_known_at=item.revision_known_at,
        content_sha256=hashlib.sha256(f"{source_id}:{evidence_id}".encode()).hexdigest(),
        license_id=DEMO_REPLAY_LICENSE_ID,
        rights_snapshot_hash=DEMO_REPLAY_RIGHTS_HASH,
        correction_relation=item.correction_relation,
        provenance=provenance,
    )


__all__ = [
    "DEMO_REPLAY_ADAPTER_VERSION",
    "DEMO_REPLAY_LICENSE_ID",
    "DEMO_REPLAY_RIGHTS_HASH",
    "DEMO_REPLAY_SOURCE_IDS",
    "R1_CAPABILITIES",
    "R1_FUNCTIONAL_REPLAY_SESSIONS",
    "R1SourceBatch",
    "ReplayScenario",
    "build_demo_replay_batch",
    "build_demo_replay_batch_v1_2",
    "build_demo_replay_batches",
    "demo_replay_provenance",
    "demo_replay_provenance_v1_2",
    "r1_source_manifest_hash",
    "reconcile_source_event_revision",
    "source_batch_from_fetch",
    "terminal_gap_batch",
    "validate_r1_source_batch",
]
