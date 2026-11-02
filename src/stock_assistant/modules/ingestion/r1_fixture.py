"""Deterministic, development-only R1 fixture catalog."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from pydantic import HttpUrl

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.event_views import (
    DecimalFactValue,
    EventEvidenceSource,
    EventEvidenceView,
    EventFactSlot,
    EventRevisionTimeline,
    EventRevisionTimelineItem,
    TextFactValue,
)
from stock_assistant.contracts.portfolio import (
    R1_FIXED_TARGET_INSTRUMENT,
    R1_FIXED_TARGET_WATCHLIST_ID,
    R1_FIXED_TARGET_WATCHLIST_REVISION,
)
from stock_assistant.contracts.premarket_jobs import (
    PremarketBriefJobRequest,
    PremarketIngestJobRequest,
)
from stock_assistant.contracts.premarket_views import (
    SourceAdvanceState,
    SourceCoverage,
    SourceCoverageStatus,
    SourceDataMode,
    SourceIngestionProgress,
    SourceRole,
)
from stock_assistant.modules.ingestion.source_rights import (
    CHOICE_QUANT_API,
    CNINFO_DATA_SERVICE,
)

CNINFO_SOURCE_ID = CNINFO_DATA_SERVICE
CHOICE_SOURCE_ID = CHOICE_QUANT_API
FIXTURE_LICENSE_ID = "synthetic-development-only-v1"
FIXTURE_RIGHTS_HASH = hashlib.sha256(b"r1-synthetic-development-only").hexdigest()
FIXTURE_NAMESPACE = UUID("37552daa-381c-5aa8-894f-bf653280be06")
FIXTURE_RULESET_VERSION = "r1-fixture-rules-v1"
FIXTURE_EXTRACTION_VERSION = "r1-fixture-extractor-v1"
FIXTURE_ADAPTER_VERSION = "synthetic-fixture-v1"
FIXTURE_SOURCE_IDS = (CNINFO_SOURCE_ID, CHOICE_SOURCE_ID)


@dataclass(frozen=True, slots=True)
class FixtureBatch:
    """One source-shaped fixture batch consumed by ``premarket_ingest``."""

    source_id: str
    coverages: tuple[SourceCoverage, ...]
    events: tuple[EventEvidenceView, ...]


def validate_fixture_batch_source_purity(batch: FixtureBatch) -> None:
    """Reject a source batch that attempts to carry another provider's evidence."""

    if any(coverage.source_id != batch.source_id for coverage in batch.coverages):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_SOURCE_MIXED")
    for event in batch.events:
        evidence_ids = {evidence.evidence_id for evidence in event.evidence}
        if any(evidence.source_id != batch.source_id for evidence in event.evidence):
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_SOURCE_MIXED")
        if any(not set(fact.evidence_ids).issubset(evidence_ids) for fact in event.facts):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_FIXTURE_EVIDENCE_REFERENCE_INVALID",
            )


def _require_fixture_runtime(*, enabled: bool, app_env: str) -> None:
    if app_env == "production":
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_FIXTURE_FORBIDDEN_IN_PRODUCTION",
        )
    if not enabled:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_DISABLED")


def _require_fixed_target(request: PremarketIngestJobRequest | PremarketBriefJobRequest) -> None:
    target = request.payload.active_target
    if (
        target.instrument != R1_FIXED_TARGET_INSTRUMENT
        or target.watchlist_id != R1_FIXED_TARGET_WATCHLIST_ID
        or target.watchlist_revision != R1_FIXED_TARGET_WATCHLIST_REVISION
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")


def fixture_batch_for_request(
    request: PremarketIngestJobRequest,
    *,
    enabled: bool,
    app_env: str,
) -> FixtureBatch:
    """Validate an exact development fixture intent before building source data."""

    _require_fixture_runtime(enabled=enabled, app_env=app_env)
    _require_fixed_target(request)
    payload = request.payload
    if (
        payload.source_id not in FIXTURE_SOURCE_IDS
        or payload.source_adapter_version != FIXTURE_ADAPTER_VERSION
        or payload.rights_snapshot_hash != FIXTURE_RIGHTS_HASH
        or payload.ruleset_version != FIXTURE_RULESET_VERSION
    ):
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "REAL_SOURCE_GRANT_REQUIRED")
    batches = build_fixture_batches(
        window_start=payload.window_start,
        window_end=payload.window_end,
        instrument_id=payload.active_target.instrument.instrument_id,
    )
    batch = next(batch for batch in batches if batch.source_id == payload.source_id)
    validate_fixture_batch_source_purity(batch)
    return batch


def require_fixture_brief_request(
    request: PremarketBriefJobRequest,
    *,
    enabled: bool,
    app_env: str,
) -> None:
    """Fail closed unless the brief consumes the exact two-source fixture window."""

    _require_fixture_runtime(enabled=enabled, app_env=app_env)
    _require_fixed_target(request)
    if (
        tuple(request.payload.source_ids) != FIXTURE_SOURCE_IDS
        or request.payload.ruleset_version != FIXTURE_RULESET_VERSION
    ):
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "REAL_SOURCE_GRANT_REQUIRED")


def _uuid(name: str) -> UUID:
    return uuid5(FIXTURE_NAMESPACE, name)


def _timeline_item(
    *,
    event_key: str,
    revision: int,
    change_type: BriefingChangeType,
    known_at: datetime,
) -> EventRevisionTimelineItem:
    return EventRevisionTimelineItem(
        revision=revision,
        change_type=change_type,
        revision_known_at=known_at,
        supersedes_revision=None if revision == 1 else revision - 1,
        extraction_version=FIXTURE_EXTRACTION_VERSION,
        ruleset_version=FIXTURE_RULESET_VERSION,
        audit_id=_uuid(f"{event_key}:audit:{revision}"),
    )


def _evidence(
    *,
    key: str,
    source_id: str,
    role: SourceRole,
    title: str,
    published_at: datetime,
    first_seen_at: datetime,
    fetched_at: datetime,
    known_at: datetime,
) -> EventEvidenceSource:
    return EventEvidenceSource(
        evidence_id=_uuid(f"evidence:{key}"),
        source_id=source_id,
        source_role=role,
        source_url=HttpUrl(f"https://fixture.invalid/{source_id}/{key}"),
        title=title,
        published_at=published_at,
        first_seen_at=first_seen_at,
        fetched_at=fetched_at,
        revision_known_at=known_at,
        content_sha256=hashlib.sha256(f"{source_id}:{key}".encode()).hexdigest(),
        license_id=FIXTURE_LICENSE_ID,
        rights_snapshot_hash=FIXTURE_RIGHTS_HASH,
    )


def _new_event(
    *,
    key: str,
    event_type: str,
    title: str,
    slot: str,
    value: str,
    source_id: str,
    role: SourceRole,
    published_at: datetime,
    first_seen_at: datetime,
    fetched_at: datetime,
    known_at: datetime,
    as_of: datetime,
    instrument_id: str,
) -> EventEvidenceView:
    evidence = _evidence(
        key=key,
        source_id=source_id,
        role=role,
        title=f"{title} (合成证据)",
        published_at=published_at,
        first_seen_at=first_seen_at,
        fetched_at=fetched_at,
        known_at=known_at,
    )
    timeline_item = _timeline_item(
        event_key=key,
        revision=1,
        change_type=BriefingChangeType.NEW,
        known_at=known_at,
    )
    return EventEvidenceView(
        schema_version="1.0",
        event_id=_uuid(f"event:{key}"),
        cluster_id=_uuid(f"cluster:{key}"),
        revision=1,
        change_type=BriefingChangeType.NEW,
        instrument_ids=[instrument_id],
        event_type=event_type,
        title=title,
        as_of=as_of,
        revision_known_at=known_at,
        extraction_version=FIXTURE_EXTRACTION_VERSION,
        ruleset_version=FIXTURE_RULESET_VERSION,
        audit_id=timeline_item.audit_id,
        revision_timeline=EventRevisionTimeline(items=[timeline_item], has_older=False),
        facts=[
            EventFactSlot(
                slot=slot,
                value=TextFactValue(kind="text", value=value),
                evidence_ids=[evidence.evidence_id],
            )
        ],
        evidence=[evidence],
    )


def _financial_update(
    *,
    revision_one: EventEvidenceView,
    published_at: datetime,
    first_seen_at: datetime,
    fetched_at: datetime,
    known_at: datetime,
    as_of: datetime,
) -> EventEvidenceView:
    evidence = _evidence(
        key="financial-update",
        source_id=CNINFO_SOURCE_ID,
        role=SourceRole.STATUTORY,
        title="兆易创新财务数据补充披露 (合成证据)",
        published_at=published_at,
        first_seen_at=first_seen_at,
        fetched_at=fetched_at,
        known_at=known_at,
    )
    current = _timeline_item(
        event_key="financial",
        revision=2,
        change_type=BriefingChangeType.UPDATE,
        known_at=known_at,
    )
    return EventEvidenceView(
        schema_version="1.0",
        event_id=revision_one.event_id,
        cluster_id=revision_one.cluster_id,
        revision=2,
        change_type=BriefingChangeType.UPDATE,
        instrument_ids=list(revision_one.instrument_ids),
        event_type="financial.revenue",
        title="兆易创新财务数据补充披露",
        as_of=as_of,
        revision_known_at=known_at,
        supersedes_revision=1,
        extraction_version=FIXTURE_EXTRACTION_VERSION,
        ruleset_version=FIXTURE_RULESET_VERSION,
        audit_id=current.audit_id,
        revision_timeline=EventRevisionTimeline(
            items=[current, revision_one.revision_timeline.items[0]],
            has_older=False,
        ),
        facts=[
            EventFactSlot(
                slot="financial.revenue",
                value=DecimalFactValue(kind="decimal", value=Decimal("780000000.00")),
                unit="CNY",
                period="2026-H1",
                evidence_ids=[evidence.evidence_id],
            )
        ],
        evidence=[evidence],
    )


def build_market_comparison_events(
    *,
    observations: tuple[EventEvidenceView, ...],
    as_of: datetime,
) -> tuple[EventEvidenceView, ...]:
    """Create the cross-source market fact only after source-local persistence."""

    by_source = {
        event.evidence[0].source_id: event
        for event in observations
        if event.event_type == "source_observation.market_close" and len(event.evidence) == 1
    }
    if not by_source:
        return ()
    if not set(by_source).issubset(set(FIXTURE_SOURCE_IDS)):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_FIXTURE_SOURCE_MIXED")
    primary = by_source.get(CNINFO_SOURCE_ID) or by_source[CHOICE_SOURCE_ID]
    primary_evidence = primary.evidence[0]
    primary_value = Decimal("91.20" if primary_evidence.source_id == CNINFO_SOURCE_ID else "91.35")
    first = _timeline_item(
        event_key="market",
        revision=1,
        change_type=BriefingChangeType.NEW,
        known_at=primary.revision_known_at,
    )
    revision_one = EventEvidenceView(
        schema_version="1.0",
        event_id=_uuid("event:market"),
        cluster_id=_uuid("cluster:market"),
        revision=1,
        change_type=BriefingChangeType.NEW,
        instrument_ids=list(primary.instrument_ids),
        event_type="market.close",
        title="兆易创新收盘行情",
        as_of=as_of,
        revision_known_at=primary.revision_known_at,
        extraction_version=FIXTURE_EXTRACTION_VERSION,
        ruleset_version=FIXTURE_RULESET_VERSION,
        audit_id=first.audit_id,
        revision_timeline=EventRevisionTimeline(items=[first], has_older=False),
        facts=[
            EventFactSlot(
                slot="market.close",
                value=DecimalFactValue(kind="decimal", value=primary_value),
                unit="CNY",
                period=as_of.date().isoformat(),
                evidence_ids=[primary_evidence.evidence_id],
            )
        ],
        evidence=[primary_evidence],
    )
    if set(by_source) != set(FIXTURE_SOURCE_IDS):
        return (revision_one,)

    choice_evidence = by_source[CHOICE_SOURCE_ID].evidence[0]
    known_at = max(event.revision_known_at for event in by_source.values())
    current = _timeline_item(
        event_key="market",
        revision=2,
        change_type=BriefingChangeType.CONTRADICTION,
        known_at=known_at,
    )
    period = as_of.date().isoformat()
    contradiction = EventEvidenceView(
        schema_version="1.0",
        event_id=revision_one.event_id,
        cluster_id=revision_one.cluster_id,
        revision=2,
        change_type=BriefingChangeType.CONTRADICTION,
        instrument_ids=list(revision_one.instrument_ids),
        event_type="market.close",
        title="收盘价来源存在差异",
        as_of=as_of,
        revision_known_at=known_at,
        supersedes_revision=1,
        extraction_version=FIXTURE_EXTRACTION_VERSION,
        ruleset_version=FIXTURE_RULESET_VERSION,
        audit_id=current.audit_id,
        revision_timeline=EventRevisionTimeline(
            items=[current, revision_one.revision_timeline.items[0]],
            has_older=False,
        ),
        facts=[
            EventFactSlot(
                slot="market.close",
                value=DecimalFactValue(kind="decimal", value=Decimal("91.20")),
                unit="CNY",
                period=period,
                evidence_ids=[primary_evidence.evidence_id],
            ),
            EventFactSlot(
                slot="market.close",
                value=DecimalFactValue(kind="decimal", value=Decimal("91.35")),
                unit="CNY",
                period=period,
                evidence_ids=[choice_evidence.evidence_id],
            ),
        ],
        evidence=[primary_evidence, choice_evidence],
    )
    return revision_one, contradiction


def _coverage(
    *,
    source_id: str,
    capability: str,
    role: SourceRole,
    observed_at: datetime,
    watermark: datetime,
) -> SourceCoverage:
    progress = SourceIngestionProgress(
        business_watermark=watermark,
        batch_audit_id=_uuid(f"batch:{source_id}:{capability}:{observed_at.isoformat()}"),
        advance_state=SourceAdvanceState.ADVANCED,
        data_mode=SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY,
    )
    return SourceCoverage(
        source_id=source_id,
        capability=capability,
        source_role=role,
        status=SourceCoverageStatus.OK,
        observed_at=observed_at,
        last_success_at=watermark,
        license_id=FIXTURE_LICENSE_ID,
        progress=progress,
    )


def build_fixture_batches(
    *,
    window_start: datetime,
    window_end: datetime,
    instrument_id: str,
) -> tuple[FixtureBatch, FixtureBatch]:
    """Build the two deterministic source batches for one complete window."""

    if window_end <= window_start:
        raise ValueError("fixture window_end must be later than window_start")
    if window_end - window_start < timedelta(hours=7):
        raise ValueError("fixture window must be at least seven hours")

    def point(hours: int, minutes: int = 0) -> datetime:
        return window_start + timedelta(hours=hours, minutes=minutes)

    statutory = _new_event(
        key="statutory",
        event_type="statutory.announcement",
        title="兆易创新董事会决议公告",
        slot="announcement.subject",
        value="审议通过研发投入计划",
        source_id=CNINFO_SOURCE_ID,
        role=SourceRole.STATUTORY,
        published_at=point(1),
        first_seen_at=point(1, 5),
        fetched_at=point(1, 6),
        known_at=point(1, 7),
        as_of=window_end,
        instrument_id=instrument_id,
    )
    financial_v1 = _new_event(
        key="financial",
        event_type="financial.revenue",
        title="兆易创新半年度收入披露",
        slot="financial.revenue",
        value="收入初值 7.6 亿元",
        source_id=CNINFO_SOURCE_ID,
        role=SourceRole.STATUTORY,
        published_at=point(2),
        first_seen_at=point(2, 5),
        fetched_at=point(2, 6),
        known_at=point(2, 7),
        as_of=window_end,
        instrument_id=instrument_id,
    )
    financial_v2 = _financial_update(
        revision_one=financial_v1,
        published_at=point(3),
        first_seen_at=point(3, 5),
        fetched_at=point(3, 6),
        known_at=point(3, 7),
        as_of=window_end,
    )
    market_cninfo = _new_event(
        key="market-cninfo",
        event_type="source_observation.market_close",
        title="兆易创新收盘行情",
        slot="market.close",
        value="91.20",
        source_id=CNINFO_SOURCE_ID,
        role=SourceRole.PROFESSIONAL_INFORMATION,
        published_at=point(4),
        first_seen_at=point(4, 1),
        fetched_at=point(4, 2),
        known_at=point(4, 3),
        as_of=window_end,
        instrument_id=instrument_id,
    )
    market_choice = _new_event(
        key="market-choice",
        event_type="source_observation.market_close",
        title="兆易创新收盘行情",
        slot="market.close",
        value="91.35",
        source_id=CHOICE_SOURCE_ID,
        role=SourceRole.PROFESSIONAL_MARKET,
        published_at=point(4),
        first_seen_at=point(4, 4),
        fetched_at=point(4, 5),
        known_at=point(4, 6),
        as_of=window_end,
        instrument_id=instrument_id,
    )
    news = _new_event(
        key="news",
        event_type="company.news",
        title="兆易创新发布新品进展",
        slot="company.news.subject",
        value="新品进入客户验证阶段",
        source_id=CHOICE_SOURCE_ID,
        role=SourceRole.PROFESSIONAL_INFORMATION,
        published_at=point(5),
        first_seen_at=point(5, 2),
        fetched_at=point(5, 3),
        known_at=point(5, 4),
        as_of=window_end,
        instrument_id=instrument_id,
    )
    macro = _new_event(
        key="macro",
        event_type="macro.flash",
        title="半导体产业政策快讯",
        slot="macro.policy.subject",
        value="集成电路研发支持政策发布",
        source_id=CHOICE_SOURCE_ID,
        role=SourceRole.PROFESSIONAL_INFORMATION,
        published_at=point(6),
        first_seen_at=point(6, 2),
        fetched_at=point(6, 3),
        known_at=point(6, 4),
        as_of=window_end,
        instrument_id=instrument_id,
    )

    cninfo_coverages = (
        _coverage(
            source_id=CNINFO_SOURCE_ID,
            capability="statutory.announcements",
            role=SourceRole.STATUTORY,
            observed_at=window_end,
            watermark=point(4, 6),
        ),
        _coverage(
            source_id=CNINFO_SOURCE_ID,
            capability="financial.changes",
            role=SourceRole.STATUTORY,
            observed_at=window_end,
            watermark=point(3, 7),
        ),
    )
    choice_coverages = (
        _coverage(
            source_id=CHOICE_SOURCE_ID,
            capability="market.snapshot",
            role=SourceRole.PROFESSIONAL_MARKET,
            observed_at=window_end,
            watermark=point(4, 6),
        ),
        _coverage(
            source_id=CHOICE_SOURCE_ID,
            capability="company_industry.news",
            role=SourceRole.PROFESSIONAL_INFORMATION,
            observed_at=window_end,
            watermark=point(5, 4),
        ),
        _coverage(
            source_id=CHOICE_SOURCE_ID,
            capability="macro.flashes",
            role=SourceRole.PROFESSIONAL_INFORMATION,
            observed_at=window_end,
            watermark=point(6, 4),
        ),
    )
    batches = (
        FixtureBatch(
            source_id=CNINFO_SOURCE_ID,
            coverages=cninfo_coverages,
            events=(statutory, financial_v1, financial_v2, market_cninfo),
        ),
        FixtureBatch(
            source_id=CHOICE_SOURCE_ID,
            coverages=choice_coverages,
            events=(market_choice, news, macro),
        ),
    )
    for batch in batches:
        validate_fixture_batch_source_purity(batch)
    return batches


def fixture_manifest_hash(batch: FixtureBatch) -> str:
    """Return a stable manifest identity for one normalized fixture batch."""

    canonical = json.dumps(
        {
            "source_id": batch.source_id,
            "coverages": [item.model_dump(mode="json") for item in batch.coverages],
            "events": [item.model_dump(mode="json") for item in batch.events],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = [
    "CHOICE_SOURCE_ID",
    "CNINFO_SOURCE_ID",
    "FIXTURE_ADAPTER_VERSION",
    "FIXTURE_EXTRACTION_VERSION",
    "FIXTURE_LICENSE_ID",
    "FIXTURE_RIGHTS_HASH",
    "FIXTURE_RULESET_VERSION",
    "FIXTURE_SOURCE_IDS",
    "FixtureBatch",
    "build_fixture_batches",
    "build_market_comparison_events",
    "fixture_batch_for_request",
    "fixture_manifest_hash",
    "require_fixture_brief_request",
    "validate_fixture_batch_source_purity",
]
