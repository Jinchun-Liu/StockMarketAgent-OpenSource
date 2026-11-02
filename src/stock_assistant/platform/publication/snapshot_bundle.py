"""Construct one canonical, cross-document-valid public snapshot bundle."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final
from uuid import UUID

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.event_views import EventEvidenceViewV1_2
from stock_assistant.contracts.ingestion import SourceQualityTier, SourceRuntimeModeV1_2
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_3,
    SourceAdvanceState,
    SourceCoverageStatus,
    SourceCoverageV1_2,
)
from stock_assistant.contracts.public_snapshots import (
    PublicCapabilityRunState,
    PublicCapabilityRunStatus,
    PublicEvidenceIndexEntryV1,
    PublicEvidenceIndexV1,
    PublicRunStatusV1_1,
    PublicSnapshotAsset,
    PublicSnapshotAssetKind,
    PublicSnapshotDeliveryMode,
    PublicSnapshotManifestV1_1,
    PublicSnapshotOriginMode,
    PublicSnapshotPointerV1,
    PublicSourceGrade,
    validate_public_snapshot_bundle,
)
from stock_assistant.modules.briefings.degraded_preview import (
    DegradedPreviewDecision,
    DegradedPreviewState,
)
from stock_assistant.modules.briefings.projection import (
    validate_briefing_evidence_projection,
)
from stock_assistant.platform.publication.public_safety import scan_public_json

_STATUS_REASON: Final = {
    SourceCoverageStatus.DELAYED: "R1_SOURCE_DELAYED",
    SourceCoverageStatus.UNAVAILABLE: "R1_SOURCE_UNAVAILABLE",
    SourceCoverageStatus.RIGHTS_BLOCKED: "R1_SOURCE_RIGHTS_BLOCKED",
    SourceCoverageStatus.NOT_QUALIFIED: "R1_SOURCE_NOT_QUALIFIED",
    SourceCoverageStatus.TIMELINESS_UNVERIFIED: "R1_SOURCE_TIMELINESS_UNVERIFIED",
}
_QUALITY_GRADE: Final = {
    SourceQualityTier.FREE_OFFICIAL: PublicSourceGrade.FREE_OFFICIAL_UNVERIFIED,
    SourceQualityTier.FREE_STRUCTURED: PublicSourceGrade.FREE_STRUCTURED_UNVERIFIED,
    SourceQualityTier.DEV_WEB: PublicSourceGrade.DEV_WEB_UNVERIFIED,
    SourceQualityTier.DEMO_REPLAY: PublicSourceGrade.DEMO_REPLAY,
}
_LEGACY_R1_INSTRUMENT_PAYLOAD: Final = {
    "instrument_id": "XSHG:603986",
    "symbol": "603986.SH",
    "exchange": "XSHG",
    "market": "CN",
    "asset_class": "equity",
    "currency": "CNY",
}
_CANONICAL_R1_INSTRUMENT_PAYLOAD: Final = {
    **_LEGACY_R1_INSTRUMENT_PAYLOAD,
    "symbol": "603986",
}


class SnapshotBuildError(ValueError):
    """The selected briefing cannot be published without violating a public invariant."""


@dataclass(frozen=True, slots=True)
class SnapshotDocuments:
    """Canonical bytes written by the S3 publisher; serialization occurs exactly once."""

    pointer: PublicSnapshotPointerV1
    manifest: PublicSnapshotManifestV1_1
    pointer_document: bytes
    manifest_document: bytes
    asset_documents: MappingProxyType[str, bytes]

    def __post_init__(self) -> None:
        copied_assets = MappingProxyType(dict(self.asset_documents))
        object.__setattr__(self, "asset_documents", copied_assets)
        parsed_pointer = PublicSnapshotPointerV1.model_validate_json(self.pointer_document)
        parsed_manifest = PublicSnapshotManifestV1_1.model_validate_json(self.manifest_document)
        if self.pointer != parsed_pointer or self.manifest != parsed_manifest:
            raise SnapshotBuildError("SNAPSHOT_MODEL_BYTES_SPLIT_BRAIN")


def canonical_json(model: ContractModel) -> bytes:
    """Return deterministic UTF-8 JSON bytes with a single trailing newline."""

    payload = model.model_dump(mode="json")
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def build_snapshot_documents(
    *,
    decision: DegradedPreviewDecision,
    research_briefing: PremarketBriefingViewV1_3,
    evidence_by_event_id: dict[UUID, EventEvidenceViewV1_2],
    snapshot_id: UUID,
    run_id: UUID,
    product_commit: str,
    run_started_at: datetime,
    run_completed_at: datetime,
    published_at: datetime,
    fresh_until: datetime,
    previous_last_successful_run_at: datetime | None = None,
) -> SnapshotDocuments:
    """Build, validate and scan every byte before any remote object may be written."""

    selected_briefing = decision.briefing
    if selected_briefing is None or decision.state is DegradedPreviewState.UNAVAILABLE:
        raise SnapshotBuildError("NO_USABLE_BRIEFING_POINTER_SWITCH_FORBIDDEN")
    if not isinstance(research_briefing, PremarketBriefingViewV1_3):
        raise SnapshotBuildError("RESEARCH_BRIEFING_V1_3_REQUIRED")
    research_fields = {
        "attention_risk_index",
        "behavior_pressure_summary",
        "plain_language_update",
        "flash_interpretation",
        "pro_interpretation",
    }
    selected_payload = selected_briefing.model_dump(
        mode="json",
        exclude={"schema_version"},
    )
    research_payload = research_briefing.model_dump(
        mode="json",
        exclude={"schema_version", *research_fields},
    )
    if (
        selected_payload.get("instrument") == _LEGACY_R1_INSTRUMENT_PAYLOAD
        and research_payload.get("instrument") == _CANONICAL_R1_INSTRUMENT_PAYLOAD
    ):
        selected_payload["instrument"] = dict(_CANONICAL_R1_INSTRUMENT_PAYLOAD)
    if research_payload != selected_payload:
        raise SnapshotBuildError("RESEARCH_BRIEFING_BASE_MISMATCH")
    briefing = research_briefing
    if briefing.runtime_mode not in {
        SourceRuntimeModeV1_2.DEV_LIVE,
        SourceRuntimeModeV1_2.DEMO_REPLAY,
    }:
        raise SnapshotBuildError("PUBLIC_SNAPSHOT_ORIGIN_NOT_ALLOWED")

    try:
        validate_briefing_evidence_projection(
            briefing=briefing,
            evidence_by_event_id=evidence_by_event_id,
        )
    except ValueError as error:
        raise SnapshotBuildError("PUBLIC_EVIDENCE_POINT_IN_TIME_JOIN_MISMATCH") from error

    prefix = f"data/snapshots/{snapshot_id}/"
    evidence_index = PublicEvidenceIndexV1(
        schema_version="1.0",
        events=[
            PublicEvidenceIndexEntryV1(
                event_id=event_id,
                path=f"evidence/{event_id}.json",
            )
            for event_id in sorted(evidence_by_event_id, key=str)
        ],
    )
    run_status = PublicRunStatusV1_1(
        schema_version="1.1",
        run_id=run_id,
        snapshot_id=snapshot_id,
        started_at=run_started_at,
        completed_at=run_completed_at,
        status=decision.run_state,
        capabilities=_public_capabilities(decision),
    )

    documents: dict[str, bytes] = {
        f"{prefix}briefing.json": canonical_json(briefing),
        f"{prefix}evidence-index.json": canonical_json(evidence_index),
        f"{prefix}run-status.json": canonical_json(run_status),
    }
    for event_id, evidence in sorted(evidence_by_event_id.items(), key=lambda item: str(item[0])):
        documents[f"{prefix}evidence/{event_id}.json"] = canonical_json(evidence)

    assets = [
        PublicSnapshotAsset(
            kind=_asset_kind(path),
            path=path,
            sha256=hashlib.sha256(document).hexdigest(),
            media_type="application/json",
            size_bytes=len(document),
        )
        for path, document in sorted(documents.items())
    ]
    generated_at = max(briefing.generated_at, run_completed_at)
    origin_mode = PublicSnapshotOriginMode(briefing.runtime_mode.value)
    manifest = PublicSnapshotManifestV1_1(
        schema_version="1.1",
        snapshot_id=snapshot_id,
        product_commit=product_commit,
        delivery_mode=PublicSnapshotDeliveryMode.PUBLIC_STATIC,
        origin_mode=origin_mode,
        release_tier="dev_only",
        data_as_of=briefing.as_of,
        generated_at=generated_at,
        published_at=published_at,
        fresh_until=fresh_until,
        last_successful_run_at=_last_success(
            decision,
            previous=previous_last_successful_run_at,
        ),
        completeness=decision.completeness,
        disclaimer_code=(
            "DEV_ONLY_UNVERIFIED_SOURCE"
            if origin_mode is PublicSnapshotOriginMode.DEV_LIVE
            else "HISTORICAL_DEMO_REPLAY"
        ),
        assets=assets,
    )
    manifest_document = canonical_json(manifest)
    pointer = PublicSnapshotPointerV1(
        schema_version="1.0",
        snapshot_id=snapshot_id,
        manifest_path=f"{prefix}manifest.json",
        manifest_sha256=hashlib.sha256(manifest_document).hexdigest(),
        product_commit=product_commit,
        published_at=published_at,
    )
    pointer_document = canonical_json(pointer)
    validate_public_snapshot_bundle(
        pointer_document=pointer_document,
        manifest_document=manifest_document,
        asset_documents=documents,
    )
    scan_public_json(pointer_document, label="pointer")
    scan_public_json(manifest_document, label="manifest")
    for path, document in documents.items():
        scan_public_json(document, label=path)
    return SnapshotDocuments(
        pointer=pointer,
        manifest=manifest,
        pointer_document=pointer_document,
        manifest_document=manifest_document,
        asset_documents=MappingProxyType(documents),
    )


def _asset_kind(path: str) -> PublicSnapshotAssetKind:
    if path.endswith("/briefing.json"):
        return PublicSnapshotAssetKind.BRIEFING
    if path.endswith("/evidence-index.json"):
        return PublicSnapshotAssetKind.EVIDENCE_INDEX
    if path.endswith("/run-status.json"):
        return PublicSnapshotAssetKind.RUN_STATUS
    return PublicSnapshotAssetKind.EVIDENCE


def _public_capabilities(
    decision: DegradedPreviewDecision,
) -> list[PublicCapabilityRunStatus]:
    briefing = decision.briefing
    if briefing is None:
        raise SnapshotBuildError("NO_USABLE_BRIEFING_POINTER_SWITCH_FORBIDDEN")
    statuses: list[PublicCapabilityRunStatus] = []
    for coverage in sorted(
        briefing.source_coverage,
        key=lambda item: (item.source_id, item.capability),
    ):
        grade = _QUALITY_GRADE.get(coverage.provenance.quality_tier)
        if grade is None:
            raise SnapshotBuildError("PUBLIC_SOURCE_GRADE_NOT_ALLOWED")
        state, reason = _capability_state(decision, coverage)
        statuses.append(
            PublicCapabilityRunStatus(
                source_id=coverage.source_id,
                capability=coverage.capability,
                source_grade=grade,
                state=state,
                safe_reason_code=reason,
            )
        )
    if not statuses:
        raise SnapshotBuildError("PUBLIC_RUN_STATUS_REQUIRES_CAPABILITIES")
    return statuses


def _capability_state(
    decision: DegradedPreviewDecision,
    coverage: SourceCoverageV1_2,
) -> tuple[PublicCapabilityRunState, str | None]:
    if decision.state is DegradedPreviewState.STALE:
        return PublicCapabilityRunState.STALE, "LKG_WITHIN_THREE_SESSIONS"
    if coverage.status is SourceCoverageStatus.OK:
        state = (
            PublicCapabilityRunState.SUCCEEDED
            if coverage.progress.advance_state is SourceAdvanceState.ADVANCED
            else PublicCapabilityRunState.NO_CHANGE
        )
        return state, None
    state = {
        SourceCoverageStatus.RIGHTS_BLOCKED: PublicCapabilityRunState.BLOCKED,
        SourceCoverageStatus.NOT_QUALIFIED: PublicCapabilityRunState.BLOCKED,
        SourceCoverageStatus.UNAVAILABLE: PublicCapabilityRunState.FAILED,
        SourceCoverageStatus.DELAYED: PublicCapabilityRunState.GAP,
        SourceCoverageStatus.TIMELINESS_UNVERIFIED: PublicCapabilityRunState.GAP,
    }[coverage.status]
    return state, _STATUS_REASON[coverage.status]


def _last_success(
    decision: DegradedPreviewDecision,
    *,
    previous: datetime | None,
) -> datetime | None:
    briefing = decision.briefing
    if briefing is None:
        return previous
    values = [
        item.last_success_at
        for item in briefing.source_coverage
        if item.last_success_at is not None
    ]
    if previous is not None:
        values.append(previous)
    return max(values) if values else None


__all__ = [
    "SnapshotBuildError",
    "SnapshotDocuments",
    "build_snapshot_documents",
    "canonical_json",
]
