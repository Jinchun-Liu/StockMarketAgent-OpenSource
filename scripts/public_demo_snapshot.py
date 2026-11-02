"""Export a deterministic, strictly typed public snapshot from the R1 replay API."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ValidationError

from stock_assistant.contracts.active_targets import ActiveTargetState
from stock_assistant.contracts.api import ApiResponse, ResponseMeta
from stock_assistant.contracts.event_views import (
    EventEvidenceSourceV1_2,
    EventEvidenceViewV1_2,
)
from stock_assistant.contracts.ingestion import (
    SourceAccessChannelV1_2,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceReleaseTier,
    SourceRightsStatusV1_2,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import (
    BriefingIngestionProgressV1_2,
    PremarketBriefingViewV1_2,
    SourceCoverageV1_2,
)

FIXED_GENERATED_AT: Final = "2026-07-31T01:05:00Z"
FIXED_AS_OF: Final = "2026-07-31T00:55:00Z"
FIXED_INSTRUMENT_ID: Final = "XSHG:603986"
FIXED_SYMBOL: Final = "603986.SH"
PRODUCT_SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_PUBLIC_SOURCE_URL_PREFIX: Final = "https://example.com/stockmarketagent/synthetic-evidence/"


class PublicSnapshotError(ValueError):
    """The replay output cannot be safely published."""


def _stable_uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"stockmarketagent-public-demo:{label}")


def _validate_envelope[EnvelopeT: BaseModel](
    payload: dict[str, object],
    envelope_type: type[EnvelopeT],
    *,
    label: str,
) -> EnvelopeT:
    """Reject unknown input keys before rebuilding the exact public wire model."""

    try:
        return envelope_type.model_validate(payload)
    except ValidationError as exc:
        raise PublicSnapshotError(f"{label} is not a valid strict API envelope") from exc


def _public_meta(label: str) -> ResponseMeta:
    return ResponseMeta(
        request_id=_stable_uuid(f"{label}:request"),
        correlation_id=_stable_uuid(f"{label}:correlation"),
        as_of=datetime.fromisoformat(FIXED_AS_OF.replace("Z", "+00:00")),
        generated_at=datetime.fromisoformat(FIXED_GENERATED_AT.replace("Z", "+00:00")),
        contract_version="1.0",
    )


def _public_provenance(
    provenance: SourceProvenanceV1_2,
    *,
    public_identifier: str,
) -> SourceProvenanceV1_2:
    """Rebuild only allowlisted lineage fields and remove private source identifiers."""

    return SourceProvenanceV1_2(
        schema_version="1.2",
        provider=provenance.provider,
        access_channel=SourceAccessChannelV1_2.DEMO_REPLAY,
        adapter_name=provenance.adapter_name,
        adapter_version=provenance.adapter_version,
        endpoint_name=provenance.endpoint_name,
        source_identifier=f"public-static:{public_identifier}",
        runtime_mode=SourceRuntimeModeV1_2.PUBLIC_STATIC,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatusV1_2.NOT_APPLICABLE,
        quality_tier=SourceQualityTier.DEMO_REPLAY,
        evidence_role=provenance.evidence_role,
    )


def _public_active_target(payload: dict[str, object]) -> ApiResponse[ActiveTargetState]:
    source = _validate_envelope(
        payload,
        ApiResponse[ActiveTargetState],
        label="active-target",
    )
    target = source.data.active_target
    if (
        not source.data.found
        or target is None
        or target.instrument.instrument_id != FIXED_INSTRUMENT_ID
        or target.instrument.symbol != FIXED_SYMBOL
    ):
        raise PublicSnapshotError("public snapshot is not the fixed R1 target")
    data = ActiveTargetState.model_validate(
        {
            **source.data.model_dump(mode="json"),
            "as_of": FIXED_AS_OF,
            "active_target": {
                **target.model_dump(mode="json"),
                "activated_at": "2026-07-27T00:00:00Z",
            },
        }
    )
    return ApiResponse[ActiveTargetState](data=data, meta=_public_meta("active-target"))


def _public_briefing(
    payload: dict[str, object],
) -> ApiResponse[PremarketBriefingViewV1_2]:
    source = _validate_envelope(
        payload,
        ApiResponse[PremarketBriefingViewV1_2],
        label="latest-briefing",
    )
    if (
        source.data.runtime_mode is not SourceRuntimeModeV1_2.DEMO_REPLAY
        or source.data.instrument.instrument_id != FIXED_INSTRUMENT_ID
        or source.data.instrument.symbol != FIXED_SYMBOL
    ):
        raise PublicSnapshotError("briefing is not the fixed-target replay payload")
    progress = BriefingIngestionProgressV1_2.model_validate(
        {
            **source.data.ingestion_progress.model_dump(mode="json", exclude={"provenance"}),
            "provenance": _public_provenance(
                source.data.ingestion_progress.provenance,
                public_identifier=(
                    f"primary:{source.data.ingestion_progress.source_id}:"
                    f"{source.data.ingestion_progress.capability}"
                ),
            ).model_dump(mode="json"),
        }
    )
    coverage = [
        SourceCoverageV1_2.model_validate(
            {
                **item.model_dump(mode="json", exclude={"provenance"}),
                "provenance": _public_provenance(
                    item.provenance,
                    public_identifier=f"coverage:{item.source_id}:{item.capability}",
                ).model_dump(mode="json"),
            }
        )
        for item in source.data.source_coverage
    ]
    data = PremarketBriefingViewV1_2.model_validate(
        {
            **source.data.model_dump(
                mode="json", exclude={"ingestion_progress", "source_coverage"}
            ),
            "runtime_mode": SourceRuntimeModeV1_2.PUBLIC_STATIC.value,
            "release_tier": SourceReleaseTier.DEV_ONLY.value,
            "as_of": FIXED_AS_OF,
            "generated_at": FIXED_GENERATED_AT,
            "ingestion_progress": progress.model_dump(mode="json"),
            "source_coverage": [item.model_dump(mode="json") for item in coverage],
        }
    )
    return ApiResponse[PremarketBriefingViewV1_2](
        data=data,
        meta=_public_meta("latest-briefing"),
    )


def _briefing_event_ids(briefing: PremarketBriefingViewV1_2) -> list[str]:
    sections = (
        briefing.sections.statutory_announcements,
        briefing.sections.financial_changes,
        briefing.sections.market_snapshot,
        briefing.sections.company_industry_news,
        briefing.sections.macro_flashes,
    )
    event_ids = {str(item.event_id) for section in sections for item in section.items}
    return sorted(event_ids)


def _public_evidence(
    payload: dict[str, object],
    *,
    event_id: str,
) -> ApiResponse[EventEvidenceViewV1_2]:
    source = _validate_envelope(
        payload,
        ApiResponse[EventEvidenceViewV1_2],
        label=f"evidence.{event_id}",
    )
    if (
        source.data.runtime_mode is not SourceRuntimeModeV1_2.DEMO_REPLAY
        or str(source.data.event_id) != event_id
        or FIXED_INSTRUMENT_ID not in source.data.instrument_ids
    ):
        raise PublicSnapshotError("evidence payload does not match its public index entry")
    evidence: list[EventEvidenceSourceV1_2] = []
    for item in source.data.evidence:
        evidence.append(
            EventEvidenceSourceV1_2.model_validate(
                {
                    **item.model_dump(mode="json", exclude={"source_url", "provenance"}),
                    "source_url": f"{_PUBLIC_SOURCE_URL_PREFIX}{item.evidence_id}",
                    "provenance": _public_provenance(
                        item.provenance,
                        public_identifier=f"evidence:{item.evidence_id}",
                    ).model_dump(mode="json"),
                }
            )
        )
    data = EventEvidenceViewV1_2.model_validate(
        {
            **source.data.model_dump(mode="json", exclude={"evidence"}),
            "runtime_mode": SourceRuntimeModeV1_2.PUBLIC_STATIC.value,
            "as_of": FIXED_AS_OF,
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
    )
    return ApiResponse[EventEvidenceViewV1_2](
        data=data,
        meta=_public_meta(f"evidence:{event_id}"),
    )


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def export_public_snapshot(
    *,
    active_target: dict[str, object],
    latest_briefing: dict[str, object],
    evidence_by_event_id: dict[str, dict[str, object]],
    product_sha: str,
    output_dir: Path,
) -> dict[str, object]:
    """Write the only publishable snapshot shape from strict replay API envelopes."""

    if PRODUCT_SHA_PATTERN.fullmatch(product_sha) is None:
        raise PublicSnapshotError("PRODUCT_SHA must be a full lowercase Git SHA")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PublicSnapshotError("public snapshot output directory must be empty")
    active = _public_active_target(active_target)
    briefing = _public_briefing(latest_briefing)
    event_ids = _briefing_event_ids(briefing.data)
    if set(evidence_by_event_id) != set(event_ids):
        raise PublicSnapshotError("evidence responses do not exactly match briefing events")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evidence").mkdir()
    files: dict[str, bytes] = {
        "active-target.json": _json_bytes(active.model_dump(mode="json")),
        "latest-briefing.json": _json_bytes(briefing.model_dump(mode="json")),
    }
    evidence_index: list[dict[str, str]] = []
    for event_id in event_ids:
        evidence = _public_evidence(evidence_by_event_id[event_id], event_id=event_id)
        relative = f"evidence/{event_id}.json"
        files[relative] = _json_bytes(evidence.model_dump(mode="json"))
        evidence_index.append({"event_id": event_id, "path": relative})
    files["evidence-index.json"] = _json_bytes({"schema_version": "1.0", "events": evidence_index})
    content_manifest = {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
    }
    metadata: dict[str, object] = {
        "schema_version": "1.0",
        "project": "StockMarketAgent",
        "product_commit": product_sha,
        "generated_at": FIXED_GENERATED_AT,
        "runtime_mode": "PUBLIC_STATIC",
        "release_tier": "DEV_ONLY",
        "data_mode": "DEMO_REPLAY",
        "instrument_id": FIXED_INSTRUMENT_ID,
        "replay_sessions": [
            "2026-07-27",
            "2026-07-28",
            "2026-07-29",
            "2026-07-30",
            "2026-07-31",
        ],
        "content_sha256": content_manifest,
    }
    files["build-metadata.json"] = _json_bytes(metadata)
    for relative, content in sorted(files.items()):
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return metadata


__all__ = [
    "FIXED_AS_OF",
    "FIXED_GENERATED_AT",
    "PublicSnapshotError",
    "export_public_snapshot",
]
