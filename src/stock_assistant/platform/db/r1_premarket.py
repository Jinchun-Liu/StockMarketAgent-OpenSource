"""PostgreSQL adapter for the fixture-driven R1 premarket read path."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.event_views import (
    EventEvidenceRevisionSelector,
    EventEvidenceView,
    EventEvidenceViewV1_1,
    EventEvidenceViewV1_2,
    EventRevisionTimeline,
    EventRevisionTimelineItem,
)
from stock_assistant.contracts.ingestion import (
    SourceAccessChannelV1_2,
    SourceReleaseTier,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import (
    BriefingIngestionProgress,
    BriefingIngestionProgressV1_1,
    BriefingIngestionProgressV1_2,
    PremarketBriefingItem,
    PremarketBriefingView,
    PremarketBriefingViewV1_1,
    PremarketBriefingViewV1_2,
    SourceAdvanceState,
    SourceCoverage,
    SourceCoverageStatus,
    SourceCoverageV1_1,
    SourceCoverageV1_2,
    SourceDataMode,
    SourceIngestionProgress,
    SourceRole,
    source_data_mode_for_runtime,
)
from stock_assistant.modules.briefings.assembler import PremarketBriefingAssembler
from stock_assistant.modules.briefings.projection import (
    briefing_section_for_event,
    project_briefing_item,
    validate_briefing_evidence_projection,
)
from stock_assistant.modules.ingestion.r1_fixture import (
    CNINFO_SOURCE_ID,
    FIXTURE_LICENSE_ID,
    FIXTURE_RIGHTS_HASH,
    FIXTURE_RULESET_VERSION,
    FixtureBatch,
    build_market_comparison_events,
    fixture_manifest_hash,
    validate_fixture_batch_source_purity,
)
from stock_assistant.modules.ingestion.r1_sources import (
    R1_CAPABILITIES,
    R1SourceBatch,
    r1_source_manifest_hash,
    reconcile_source_event_revision,
    validate_r1_source_batch,
)
from stock_assistant.modules.portfolio.active_target import R1_INSTRUMENT

_FIXTURE_DB_NAMESPACE: Final = UUID("3160aafc-6baa-5e21-846d-4691f5b8f33e")
_LOCAL_OWNER_ID: Final = UUID("52cc6704-6c56-54d0-bddb-05f9004c5177")
_FIXTURE_STREAM: Final = "r1_fixture_premarket"
_MISSING_CAPABILITY_REASON: Final = "R1_NO_ACTIVE_SOURCE_FOR_CAPABILITY"

R1RuntimeMode = SourceRuntimeMode | SourceRuntimeModeV1_2
R1BriefingSchemaVersion = Literal["1.1", "1.2"]
EventEvidenceReadModel = EventEvidenceView | EventEvidenceViewV1_1 | EventEvidenceViewV1_2
PremarketBriefingReadModel = (
    PremarketBriefingView | PremarketBriefingViewV1_1 | PremarketBriefingViewV1_2
)


@dataclass(frozen=True, slots=True)
class FixtureIngestStorageResult:
    manifest_hash: str
    batch_audit_id: UUID
    records_fetched: int
    evidence_stored: int
    event_revisions_stored: int
    source_watermark: datetime | None
    idempotent_replay: bool
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class FixtureSourceLease:
    """One bounded lease bound to a source cursor version."""

    source_id: str
    owner: str
    expected_cursor_version: int
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class FixtureBriefingStorageResult:
    briefing: PremarketBriefingView
    manifest_hash: str
    event_count: int
    evidence_count: int
    idempotent_replay: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class R1IngestStorageResult:
    """Provider-neutral result of one capability-local atomic commit."""

    manifest_hash: str
    batch_audit_id: UUID
    records_fetched: int
    evidence_stored: int
    event_revisions_stored: int
    source_watermark: datetime | None
    idempotent_replay: bool
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class R1BriefingStorageResult:
    """Provider-neutral persisted briefing result for one runtime mode."""

    briefing: PremarketBriefingViewV1_1 | PremarketBriefingViewV1_2
    manifest_hash: str
    event_count: int
    evidence_count: int
    idempotent_replay: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class R1SourceLease:
    """One bounded lease tied to a source, mode, capability, and cursor version."""

    source_id: str
    stream: str
    owner: str
    expected_cursor_version: int
    lease_expires_at: datetime
    provider_cursor: str | None = field(default=None, repr=False)
    business_watermark: datetime | None = field(default=None, repr=False)
    last_success_at: datetime | None = field(default=None, repr=False)


def _uuid(name: str) -> UUID:
    return uuid5(_FIXTURE_DB_NAMESPACE, name)


def _json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse_event_view(payload: object) -> EventEvidenceReadModel:
    raw = cast(dict[str, object], payload)
    if raw.get("schema_version") == "1.2":
        return EventEvidenceViewV1_2.model_validate(raw)
    if raw.get("schema_version") == "1.1":
        return EventEvidenceViewV1_1.model_validate(raw)
    return EventEvidenceView.model_validate(raw)


def _event_query_view_at(
    event: EventEvidenceReadModel,
    *,
    as_of: datetime,
) -> EventEvidenceReadModel:
    """Rebase only the selected query view; persisted knowledge time remains immutable."""

    payload = event.model_dump()
    payload["as_of"] = as_of
    return _parse_event_view(payload)


def _parse_briefing_view(payload: object) -> PremarketBriefingReadModel:
    raw = cast(dict[str, object], payload)
    if raw.get("schema_version") == "1.2":
        return PremarketBriefingViewV1_2.model_validate(raw)
    if raw.get("schema_version") == "1.1":
        return PremarketBriefingViewV1_1.model_validate(raw)
    return PremarketBriefingView.model_validate(raw)


def _event_matches_requested_scope(
    event: EventEvidenceReadModel,
    *,
    source_ids: set[str],
    runtime_mode: R1RuntimeMode,
    schema_version: str,
) -> bool:
    event_mode = (
        event.runtime_mode
        if isinstance(event, (EventEvidenceViewV1_1, EventEvidenceViewV1_2))
        else SourceRuntimeMode.DEMO_REPLAY
    )
    return (
        event.schema_version == schema_version
        and event_mode.value == runtime_mode.value
        and {evidence.source_id for evidence in event.evidence}.issubset(source_ids)
    )


def _resolve_briefing_schema_version(
    provenance_payloads: tuple[object, ...],
    *,
    runtime_mode: R1RuntimeMode,
) -> R1BriefingSchemaVersion:
    """Resolve the briefing schema from persisted source provenance."""

    versions: set[str] = set()
    for payload in provenance_payloads:
        if not isinstance(payload, dict):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_SCHEMA_MISMATCH",
            )
        version = payload.get("schema_version")
        if version not in {"1.1", "1.2"}:
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_SCHEMA_MISMATCH",
            )
        versions.add(cast(str, version))
    if len(versions) != 1:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_SCHEMA_MISMATCH",
        )
    version = versions.pop()
    if isinstance(runtime_mode, SourceRuntimeModeV1_2) and version != "1.2":
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_SCHEMA_MISMATCH",
        )
    if runtime_mode is SourceRuntimeMode.DEV_LIVE and version != "1.1":
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_SCHEMA_MISMATCH",
        )
    return cast("R1BriefingSchemaVersion", version)


def _project_missing_v1_2_capabilities(
    *,
    active_coverages: list[SourceCoverageV1_2],
    primary: SourceCoverageV1_2,
    observed_at: datetime,
) -> tuple[SourceCoverageV1_2, ...]:
    """Expose absent capabilities as gaps without inventing a source execution."""

    present_capabilities = {item.capability for item in active_coverages}
    return tuple(
        SourceCoverageV1_2(
            source_id=primary.source_id,
            capability=capability,
            source_role=SourceRole.DISCOVERY,
            status=SourceCoverageStatus.NOT_QUALIFIED,
            observed_at=observed_at,
            last_success_at=None,
            license_id=primary.license_id,
            gap_reason=_MISSING_CAPABILITY_REASON,
            progress=SourceIngestionProgress(
                business_watermark=None,
                batch_audit_id=primary.progress.batch_audit_id,
                advance_state=SourceAdvanceState.NOT_ADVANCED,
                data_mode=primary.progress.data_mode,
            ),
            provenance=primary.provenance,
        )
        for capability in R1_CAPABILITIES
        if capability not in present_capabilities
    )


def _row_id(event_id: UUID, revision: int) -> UUID:
    return uuid5(event_id, f"revision:{revision}")


async def _register_fixture_source(connection: AsyncConnection, source_id: str) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO data_sources (
                id, name, tier, authority, rights_record, rights_hash,
                rights_expiry, enabled
            )
            VALUES (
                :source_id, :source_id, 'development_only', 1.0,
                CAST(:rights_record AS jsonb), :rights_hash, NULL, false
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "source_id": source_id,
            "rights_record": _json(
                {
                    "data_mode": "synthetic_development_only",
                    "license_id": FIXTURE_LICENSE_ID,
                }
            ),
            "rights_hash": FIXTURE_RIGHTS_HASH,
        },
    )


async def _register_r1_source(connection: AsyncConnection, batch: R1SourceBatch) -> None:
    data_mode = source_data_mode_for_runtime(batch.runtime_mode).value
    await connection.execute(
        text(
            """
            INSERT INTO data_sources (
                id, name, tier, authority, rights_record, rights_hash,
                rights_expiry, enabled
            )
            VALUES (
                :source_id, :source_id, 'development_only', 0.5,
                CAST(:rights_record AS jsonb), :rights_hash, NULL, false
            )
            ON CONFLICT (id) DO UPDATE
            SET rights_record = EXCLUDED.rights_record,
                rights_hash = EXCLUDED.rights_hash,
                updated_at = clock_timestamp()
            WHERE data_sources.enabled = false
            """
        ),
        {
            "source_id": batch.source_id,
            "rights_record": _json(
                {
                    "data_mode": data_mode,
                    "license_id": batch.license_id,
                    "runtime_mode": batch.runtime_mode,
                    "provenance": batch.provenance.model_dump(mode="json"),
                }
            ),
            "rights_hash": batch.rights_snapshot_hash,
        },
    )


async def _persist_fixture_event(
    connection: AsyncConnection,
    event: EventEvidenceView,
) -> tuple[int, int]:
    evidence_inserted = 0
    for evidence in event.evidence:
        provenance = getattr(evidence, "provenance", None)
        inserted = await connection.execute(
            text(
                """
                INSERT INTO raw_documents (
                    id, source_id, source_document_id, canonical_url, title,
                    content_sha256, normalized_sha256, object_key, storage_class,
                    published_at, first_seen_at, fetched_at, license_id, rights_hash,
                    extractor_version, event_time, provenance
                )
                VALUES (
                    :id, :source_id, :source_document_id, :canonical_url, :title,
                    :content_sha256, :content_sha256, NULL, 'metadata_only',
                    :published_at, :first_seen_at, :fetched_at, :license_id,
                    :rights_hash, :extractor_version, :event_time,
                    CAST(:provenance AS jsonb)
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": evidence.evidence_id,
                "source_id": evidence.source_id,
                "source_document_id": str(evidence.evidence_id),
                "canonical_url": str(evidence.source_url),
                "title": evidence.title,
                "content_sha256": evidence.content_sha256,
                "published_at": evidence.published_at,
                "first_seen_at": evidence.first_seen_at,
                "fetched_at": evidence.fetched_at,
                "license_id": evidence.license_id,
                "rights_hash": evidence.rights_snapshot_hash,
                "extractor_version": event.extraction_version,
                "event_time": getattr(evidence, "event_time", None),
                "provenance": (None if provenance is None else _json(provenance)),
            },
        )
        evidence_inserted += max(inserted.rowcount or 0, 0)

    await connection.execute(
        text(
            """
            INSERT INTO event_clusters (id, canonical_event_type, opened_at, last_changed_at)
            VALUES (:id, :event_type, :opened_at, :last_changed_at)
            ON CONFLICT (id) DO UPDATE
            SET last_changed_at = GREATEST(
                event_clusters.last_changed_at,
                EXCLUDED.last_changed_at
            )
            """
        ),
        {
            "id": event.cluster_id,
            "event_type": event.event_type,
            "opened_at": min(item.first_seen_at for item in event.evidence),
            "last_changed_at": event.revision_known_at,
        },
    )
    row_id = _row_id(event.event_id, event.revision)
    predecessor_id = (
        None
        if event.supersedes_revision is None
        else _row_id(event.event_id, event.supersedes_revision)
    )
    inserted_event = await connection.execute(
        text(
            """
            INSERT INTO market_events (
                id, cluster_id, revision, event_type, event_status, direction,
                materiality, impact_horizon, confidence, event_time, published_at,
                first_seen_at, valid_from, valid_to, extractor_version, rule_version,
                payload, logical_event_id, revision_known_at, supersedes_revision_id
            )
            VALUES (
                :id, :cluster_id, :revision, :event_type, 'announced', 'unknown',
                0.5, 'days', 0.8, :published_at, :published_at,
                :first_seen_at, :revision_known_at, NULL, :extractor_version,
                :rule_version, CAST(:payload AS jsonb), :logical_event_id,
                :revision_known_at, :supersedes_revision_id
            )
            ON CONFLICT (logical_event_id, revision) DO NOTHING
            """
        ),
        {
            "id": row_id,
            "cluster_id": event.cluster_id,
            "revision": event.revision,
            "event_type": event.event_type,
            "published_at": min(item.published_at for item in event.evidence),
            "first_seen_at": min(item.first_seen_at for item in event.evidence),
            "revision_known_at": event.revision_known_at,
            "extractor_version": event.extraction_version,
            "rule_version": event.ruleset_version,
            "payload": _json(event),
            "logical_event_id": event.event_id,
            "supersedes_revision_id": predecessor_id,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO event_entities (event_id, entity_type, entity_id)
            VALUES (:event_id, 'instrument', :instrument_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {"event_id": row_id, "instrument_id": R1_INSTRUMENT.instrument_id},
    )
    relation = (
        "conflict"
        if event.change_type is BriefingChangeType.CONTRADICTION
        else "update"
        if event.revision > 1
        else "primary"
    )
    for evidence in event.evidence:
        await connection.execute(
            text(
                """
                INSERT INTO event_documents (event_id, document_id, relation)
                VALUES (:event_id, :document_id, :relation)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "event_id": row_id,
                "document_id": evidence.evidence_id,
                "relation": relation,
            },
        )
    for fact_index, fact in enumerate(event.facts):
        for evidence_id in fact.evidence_ids:
            claim_id = uuid5(row_id, f"claim:{fact_index}:{fact.slot}:{evidence_id}")
            await connection.execute(
                text(
                    """
                    INSERT INTO event_claims (
                        id, event_id, name, value, unit, period,
                        evidence_document_id, evidence_span
                    )
                    VALUES (
                        :id, :event_id, :name, CAST(:value AS jsonb), :unit,
                        :period, :evidence_document_id, :evidence_span
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": claim_id,
                    "event_id": row_id,
                    "name": fact.slot,
                    "value": _json(fact.value),
                    "unit": fact.unit,
                    "period": fact.period,
                    "evidence_document_id": evidence_id,
                    "evidence_span": "synthetic structured fixture",
                },
            )
    return evidence_inserted, max(inserted_event.rowcount or 0, 0)


async def _resolve_source_event_revision(
    connection: AsyncConnection,
    event: EventEvidenceReadModel,
) -> EventEvidenceReadModel:
    if not isinstance(event, EventEvidenceViewV1_2) or not all(
        item.provenance.access_channel is SourceAccessChannelV1_2.OFFICIAL_HTTP
        for item in event.evidence
    ):
        return event
    row = (
        (
            await connection.execute(
                text(
                    """
                    SELECT payload
                      FROM market_events
                     WHERE logical_event_id = :event_id
                     ORDER BY revision DESC
                     LIMIT 1
                     FOR UPDATE
                    """
                ),
                {"event_id": event.event_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return event
    previous = _parse_event_view(row["payload"])
    if not isinstance(previous, EventEvidenceViewV1_2):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "R1_SOURCE_REVISION_MODE_MISMATCH",
        )
    return reconcile_source_event_revision(previous=previous, candidate=event)


async def _release_source_lease(
    connection: AsyncConnection,
    *,
    batch: R1SourceBatch,
    lease: R1SourceLease,
) -> None:
    released = await connection.execute(
        text(
            """
            UPDATE source_cursors
               SET lease_owner = NULL,
                   lease_expires_at = NULL,
                   updated_at = clock_timestamp()
             WHERE source_id = :source_id
               AND stream = :stream
               AND cursor_version = :cursor_version
               AND lease_owner = :owner
               AND lease_expires_at = :lease_expires_at
               AND lease_expires_at > clock_timestamp()
            """
        ),
        {
            "source_id": batch.source_id,
            "stream": batch.stream,
            "cursor_version": lease.expected_cursor_version,
            "owner": lease.owner,
            "lease_expires_at": lease.lease_expires_at,
        },
    )
    if (released.rowcount or 0) != 1:
        raise ApplicationError(ErrorCode.CONFLICT, "R1_CURSOR_CAS_CONFLICT", retryable=True)


class PostgresR1PremarketRepository:
    """Persist normalized fixture evidence and serve the frozen R1 read models."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def acquire_source_lease(
        self,
        *,
        batch: R1SourceBatch,
        owner: str,
        lease_seconds: int = 300,
    ) -> R1SourceLease:
        """Acquire a capability-local lease without advancing the opaque cursor."""

        validate_r1_source_batch(batch)
        if not owner.strip() or lease_seconds < 1:
            raise ValueError("source lease requires a non-empty owner and positive duration")
        empty_cursor = _json({})
        empty_hash = hashlib.sha256(empty_cursor.encode()).hexdigest()
        async with self._engine.begin() as connection:
            await _register_r1_source(connection, batch)
            await connection.execute(
                text(
                    """
                    INSERT INTO source_cursors (
                        source_id, stream, cursor_payload, cursor_hash,
                        business_watermark, last_success_at
                    )
                    VALUES (
                        :source_id, :stream, CAST(:cursor_payload AS jsonb), :cursor_hash,
                        NULL, NULL
                    )
                    ON CONFLICT (source_id, stream) DO NOTHING
                    """
                ),
                {
                    "source_id": batch.source_id,
                    "stream": batch.stream,
                    "cursor_payload": empty_cursor,
                    "cursor_hash": empty_hash,
                },
            )
            leased = (
                (
                    await connection.execute(
                        text(
                            """
                            UPDATE source_cursors
                               SET lease_owner = :owner,
                                   lease_expires_at = clock_timestamp()
                                       + (:lease_seconds * interval '1 second'),
                                   updated_at = clock_timestamp()
                             WHERE source_id = :source_id
                               AND stream = :stream
                               AND (
                                   lease_owner IS NULL
                                   OR lease_expires_at <= clock_timestamp()
                                   OR lease_owner = :owner
                               )
                         RETURNING cursor_payload, cursor_version, lease_expires_at,
                                   business_watermark, last_success_at
                            """
                        ),
                        {
                            "source_id": batch.source_id,
                            "stream": batch.stream,
                            "owner": owner,
                            "lease_seconds": lease_seconds,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if leased is None:
            raise ApplicationError(ErrorCode.CONFLICT, "R1_SOURCE_LEASE_BUSY", retryable=True)
        cursor_payload = leased["cursor_payload"]
        if not isinstance(cursor_payload, dict):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_CURSOR_INVALID",
            )
        provider_cursor = cursor_payload.get("provider_cursor")
        if provider_cursor is not None and not isinstance(provider_cursor, str):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_CURSOR_INVALID",
            )
        return R1SourceLease(
            source_id=batch.source_id,
            stream=batch.stream,
            owner=owner,
            expected_cursor_version=cast(int, leased["cursor_version"]),
            lease_expires_at=cast(datetime, leased["lease_expires_at"]),
            provider_cursor=provider_cursor,
            business_watermark=cast(datetime | None, leased["business_watermark"]),
            last_success_at=cast(datetime | None, leased["last_success_at"]),
        )

    async def persist_source_batch(
        self,
        *,
        batch: R1SourceBatch,
        window_start: datetime,
        window_end: datetime,
        lease: R1SourceLease,
    ) -> R1IngestStorageResult:
        """Atomically persist one normalized capability and advance only its cursor."""

        validate_r1_source_batch(batch)
        if lease.source_id != batch.source_id or lease.stream != batch.stream:
            raise ApplicationError(ErrorCode.CONFLICT, "R1_SOURCE_LEASE_MISMATCH")
        coverage = batch.coverages[0]
        if coverage.status is not SourceCoverageStatus.OK:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_NOT_SUCCESSFUL")
        manifest_hash = r1_source_manifest_hash(batch)
        batch_id = _uuid(
            f"ingest:{batch.source_id}:{batch.stream}:{window_start.isoformat()}:"
            f"{window_end.isoformat()}:{manifest_hash}"
        )
        source_watermark = coverage.progress.business_watermark
        if source_watermark is None:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_WATERMARK_MISSING")
        last_success_at = coverage.last_success_at
        if last_success_at is None:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_SUCCESS_MISSING")
        terminal_at = max(window_end, coverage.observed_at)

        async with self._engine.begin() as connection:
            cursor_state = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT cursor_payload, cursor_version, lease_owner,
                                   lease_expires_at, business_watermark, last_success_at,
                                   lease_expires_at > clock_timestamp() AS lease_valid
                              FROM source_cursors
                             WHERE source_id = :source_id AND stream = :stream
                             FOR UPDATE
                            """
                        ),
                        {"source_id": batch.source_id, "stream": batch.stream},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                cursor_state is None
                or cursor_state["cursor_version"] != lease.expected_cursor_version
                or cursor_state["lease_owner"] != lease.owner
                or cursor_state["lease_expires_at"] != lease.lease_expires_at
                or cursor_state["lease_valid"] is not True
            ):
                raise ApplicationError(ErrorCode.CONFLICT, "R1_CURSOR_CAS_CONFLICT", retryable=True)

            existing = (
                (
                    await connection.execute(
                        text("SELECT status, metrics FROM source_runs WHERE id = :batch_id"),
                        {"batch_id": batch_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and existing["status"] == "succeeded":
                await _release_source_lease(connection, batch=batch, lease=lease)
                metrics = cast(dict[str, object], existing["metrics"])
                return R1IngestStorageResult(
                    manifest_hash=manifest_hash,
                    batch_audit_id=batch_id,
                    records_fetched=int(cast(int, metrics["records_fetched"])),
                    evidence_stored=int(cast(int, metrics["evidence_stored"])),
                    event_revisions_stored=int(cast(int, metrics["event_revisions_stored"])),
                    source_watermark=source_watermark,
                    idempotent_replay=True,
                )

            previous_window = cast(dict[str, object], cursor_state["cursor_payload"]).get(
                "window_end"
            )
            previous_success = cast(datetime | None, cursor_state["last_success_at"])
            if previous_window is not None and (
                datetime.fromisoformat(cast(str, previous_window)) > window_end
                or (
                    datetime.fromisoformat(cast(str, previous_window)) == window_end
                    and previous_success is not None
                    and last_success_at <= previous_success
                )
            ):
                await _release_source_lease(connection, batch=batch, lease=lease)
                return R1IngestStorageResult(
                    manifest_hash=manifest_hash,
                    batch_audit_id=batch_id,
                    records_fetched=0,
                    evidence_stored=0,
                    event_revisions_stored=0,
                    source_watermark=None,
                    idempotent_replay=True,
                    reason_code="R1_CURSOR_WINDOW_NOT_NEWER",
                )

            await _register_r1_source(connection, batch)
            evidence_inserted = 0
            revisions_inserted = 0
            for event in sorted(batch.events, key=lambda item: (str(item.event_id), item.revision)):
                resolved_event = await _resolve_source_event_revision(connection, event)
                evidence_count, revision_count = await _persist_fixture_event(
                    connection,
                    resolved_event,
                )
                evidence_inserted += evidence_count
                revisions_inserted += revision_count

            metrics = {
                "runtime_mode": batch.runtime_mode,
                "capability": batch.capability,
                "manifest_hash": manifest_hash,
                "records_fetched": len(batch.events),
                "evidence_stored": evidence_inserted,
                "event_revisions_stored": revisions_inserted,
                "coverages": [item.model_dump(mode="json") for item in batch.coverages],
                "provenance": batch.provenance.model_dump(mode="json"),
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO source_runs (
                        id, source_id, stream, scheduled_for, started_at, finished_at,
                        status, documents_seen, rights_hash, metrics
                    )
                    VALUES (
                        :id, :source_id, :stream, :scheduled_for, :started_at,
                        :finished_at, 'succeeded', :documents_seen, :rights_hash,
                        CAST(:metrics AS jsonb)
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET finished_at = EXCLUDED.finished_at,
                        status = 'succeeded',
                        documents_seen = EXCLUDED.documents_seen,
                        safe_error_code = NULL,
                        rights_hash = EXCLUDED.rights_hash,
                        metrics = EXCLUDED.metrics
                    """
                ),
                {
                    "id": batch_id,
                    "source_id": batch.source_id,
                    "stream": batch.stream,
                    "scheduled_for": window_end,
                    "started_at": window_end,
                    "finished_at": terminal_at,
                    "documents_seen": len(batch.events),
                    "rights_hash": batch.rights_snapshot_hash,
                    "metrics": _json(metrics),
                },
            )
            cursor_payload = {
                "window_end": window_end.isoformat(),
                "provider_cursor": batch.next_cursor,
            }
            cursor_json = _json(cursor_payload)
            finalized = await connection.execute(
                text(
                    """
                    UPDATE source_cursors
                       SET cursor_payload = CAST(:cursor_payload AS jsonb),
                           cursor_hash = :cursor_hash,
                           business_watermark = :business_watermark,
                           last_success_at = :last_success_at,
                           cursor_version = cursor_version + 1,
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           updated_at = clock_timestamp()
                     WHERE source_id = :source_id
                       AND stream = :stream
                       AND cursor_version = :cursor_version
                       AND lease_owner = :owner
                       AND lease_expires_at = :lease_expires_at
                       AND lease_expires_at > clock_timestamp()
                       AND (
                           business_watermark IS NULL
                           OR business_watermark <= :business_watermark
                       )
                       AND (
                           last_success_at IS NULL
                           OR last_success_at <= :last_success_at
                       )
                    """
                ),
                {
                    "source_id": batch.source_id,
                    "stream": batch.stream,
                    "cursor_payload": cursor_json,
                    "cursor_hash": hashlib.sha256(cursor_json.encode()).hexdigest(),
                    "business_watermark": source_watermark,
                    "last_success_at": last_success_at,
                    "cursor_version": lease.expected_cursor_version,
                    "owner": lease.owner,
                    "lease_expires_at": lease.lease_expires_at,
                },
            )
            if (finalized.rowcount or 0) != 1:
                raise ApplicationError(ErrorCode.CONFLICT, "R1_CURSOR_CAS_CONFLICT", retryable=True)

        return R1IngestStorageResult(
            manifest_hash=manifest_hash,
            batch_audit_id=batch_id,
            records_fetched=len(batch.events),
            evidence_stored=evidence_inserted,
            event_revisions_stored=revisions_inserted,
            source_watermark=source_watermark,
            idempotent_replay=False,
        )

    async def record_source_failure(
        self,
        *,
        batch: R1SourceBatch,
        window_end: datetime,
        lease: R1SourceLease,
        reason_code: str,
    ) -> None:
        """Record a terminal capability failure and release its lease without cursor advance."""

        validate_r1_source_batch(batch)
        if lease.source_id != batch.source_id or lease.stream != batch.stream:
            raise ApplicationError(ErrorCode.CONFLICT, "R1_SOURCE_LEASE_MISMATCH")
        manifest_hash = r1_source_manifest_hash(batch)
        terminal_at = max(window_end, batch.coverages[0].observed_at)
        batch_id = _uuid(
            f"failure:{batch.source_id}:{batch.stream}:{window_end.isoformat()}:{manifest_hash}"
        )
        status = "blocked_rights" if "RIGHTS" in reason_code else "failed"
        async with self._engine.begin() as connection:
            await _register_r1_source(connection, batch)
            await _release_source_lease(connection, batch=batch, lease=lease)
            await connection.execute(
                text(
                    """
                    INSERT INTO source_runs (
                        id, source_id, stream, scheduled_for, started_at, finished_at,
                        status, documents_seen, safe_error_code, rights_hash, metrics
                    )
                    VALUES (
                        :id, :source_id, :stream, :scheduled_for, :started_at,
                        :finished_at, :status, 0, :reason_code, :rights_hash,
                        CAST(:metrics AS jsonb)
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET finished_at = EXCLUDED.finished_at,
                        status = EXCLUDED.status,
                        safe_error_code = EXCLUDED.safe_error_code,
                        metrics = EXCLUDED.metrics
                    WHERE source_runs.status <> 'succeeded'
                    """
                ),
                {
                    "id": batch_id,
                    "source_id": batch.source_id,
                    "stream": batch.stream,
                    "scheduled_for": window_end,
                    "started_at": window_end,
                    "finished_at": terminal_at,
                    "status": status,
                    "reason_code": reason_code,
                    "rights_hash": batch.rights_snapshot_hash,
                    "metrics": _json(
                        {
                            "runtime_mode": batch.runtime_mode,
                            "capability": batch.capability,
                            "manifest_hash": manifest_hash,
                            "coverages": [item.model_dump(mode="json") for item in batch.coverages],
                            "provenance": batch.provenance.model_dump(mode="json"),
                        }
                    ),
                },
            )

    async def generate_and_store_r1_briefing(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        generated_at: datetime,
        source_ids: tuple[str, ...],
        runtime_mode: R1RuntimeMode,
    ) -> R1BriefingStorageResult:
        """Assemble one version-matched briefing after every requested source is terminal."""

        if runtime_mode.value == SourceRuntimeMode.AUTHORIZED.value:
            raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "AUTHORIZED_CONNECTORS_NOT_STARTED")
        if generated_at < window_end:
            raise ValueError("briefing generation cannot precede the completed source window")
        requested_sources = set(source_ids)
        if not requested_sources or len(requested_sources) != len(source_ids):
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                retryable=True,
            )
        stream_prefix = f"r1_premarket:{runtime_mode.value}:"
        async with self._engine.begin() as connection:
            run_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT DISTINCT ON (source_id, stream)
                                   source_id, stream, status, finished_at, metrics
                              FROM source_runs
                             WHERE stream LIKE :stream_prefix
                               AND scheduled_for = :window_end
                               AND source_id = ANY(:source_ids)
                               AND status IN (
                                   'succeeded', 'failed', 'blocked_rights', 'rate_limited'
                               )
                             ORDER BY source_id, stream, finished_at DESC, id DESC
                            """
                        ),
                        {
                            "stream_prefix": f"{stream_prefix}%",
                            "window_end": window_end,
                            "source_ids": list(source_ids),
                        },
                    )
                )
                .mappings()
                .all()
            )
            run_keys: set[tuple[str, str]] = set()
            for row in run_rows:
                metrics = cast(dict[str, object], row["metrics"])
                capability = metrics.get("capability")
                source_id = cast(str, row["source_id"])
                if (
                    not isinstance(capability, str)
                    or capability not in R1_CAPABILITIES
                    or row["stream"] != f"{stream_prefix}{capability}"
                ):
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                        retryable=True,
                    )
                run_keys.add((source_id, capability))
            if {source_id for source_id, _ in run_keys} != requested_sources or len(
                run_keys
            ) != len(requested_sources):
                raise ApplicationError(
                    ErrorCode.PRECONDITION_FAILED,
                    "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                    retryable=True,
                )

            effective_as_of = (
                max(
                    window_end,
                    *(cast(datetime, row["finished_at"]) for row in run_rows),
                )
                if runtime_mode.value == SourceRuntimeMode.DEV_LIVE.value
                else window_end
            )
            effective_generated_at = max(generated_at, effective_as_of)

            briefing_schema_version = _resolve_briefing_schema_version(
                tuple(
                    cast(dict[str, object], row["metrics"]).get("provenance") for row in run_rows
                ),
                runtime_mode=runtime_mode,
            )
            is_v1_2 = briefing_schema_version == "1.2"
            coverage_model = SourceCoverageV1_2 if is_v1_2 else SourceCoverageV1_1
            coverages: list[SourceCoverageV1_1 | SourceCoverageV1_2] = []
            for row in run_rows:
                metrics = cast(dict[str, object], row["metrics"])
                if metrics.get("runtime_mode") != runtime_mode.value:
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_SOURCE_MODE_MISMATCH",
                    )
                raw_coverages = metrics.get("coverages")
                if not isinstance(raw_coverages, list) or len(raw_coverages) != 1:
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                        retryable=True,
                    )
                coverage = coverage_model.model_validate(raw_coverages[0])
                expected_key = (
                    cast(str, row["source_id"]),
                    cast(str, metrics["capability"]),
                )
                if (coverage.source_id, coverage.capability) != expected_key:
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                        retryable=True,
                    )
                coverages.append(coverage)

            primary = min(
                coverages,
                key=lambda item: (
                    item.status is SourceCoverageStatus.OK,
                    item.source_id,
                    item.capability,
                ),
            )
            present_capabilities = {item.capability for item in coverages}
            if present_capabilities != set(R1_CAPABILITIES):
                if not is_v1_2:
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                        retryable=True,
                    )
                v1_2_coverages = cast(list[SourceCoverageV1_2], coverages)
                v1_2_primary = cast(SourceCoverageV1_2, primary)
                coverages.extend(
                    _project_missing_v1_2_capabilities(
                        active_coverages=v1_2_coverages,
                        primary=v1_2_primary,
                        observed_at=effective_as_of,
                    )
                )
            release_tiers = {item.provenance.release_tier for item in coverages}
            if len(release_tiers) != 1:
                raise ApplicationError(
                    ErrorCode.PRECONDITION_FAILED,
                    "R1_SOURCE_RELEASE_TIER_MISMATCH",
                )
            release_tier = next(iter(release_tiers))
            if release_tier is not SourceReleaseTier.DEV_ONLY:
                raise ApplicationError(
                    ErrorCode.RIGHTS_BLOCKED,
                    "AUTHORIZED_CONNECTORS_NOT_STARTED",
                )

            event_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT DISTINCT ON (event.logical_event_id) event.payload
                              FROM market_events AS event
                              JOIN event_entities AS entity ON entity.event_id = event.id
                             WHERE entity.entity_type = 'instrument'
                               AND entity.entity_id = :instrument_id
                               AND event.first_seen_at <= :as_of
                               AND event.revision_known_at > :window_start
                               AND event.revision_known_at <= :as_of
                             ORDER BY event.logical_event_id, event.revision DESC
                            """
                        ),
                        {
                            "instrument_id": R1_INSTRUMENT.instrument_id,
                            "window_start": window_start,
                            "as_of": effective_as_of,
                        },
                    )
                )
                .mappings()
                .all()
            )
            successful_sources = {
                cast(str, row["source_id"]) for row in run_rows if row["status"] == "succeeded"
            }
            events: list[EventEvidenceReadModel] = []
            for row in event_rows:
                event = _parse_event_view(row["payload"])
                if _event_matches_requested_scope(
                    event,
                    source_ids=requested_sources,
                    runtime_mode=runtime_mode,
                    schema_version="1.2" if is_v1_2 else "1.1",
                ) and {item.source_id for item in event.evidence}.issubset(successful_sources):
                    events.append(_event_query_view_at(event, as_of=effective_as_of))
            manifests = sorted(
                f"{row['source_id']}:{row['stream']}:{row['status']}:"
                f"{cast(str, cast(dict[str, object], row['metrics'])['manifest_hash'])}"
                for row in run_rows
            )
            manifest_hash = hashlib.sha256(":".join(manifests).encode()).hexdigest()
            progress_payload = {
                "source_id": primary.source_id,
                "capability": primary.capability,
                "provenance": primary.provenance,
                **primary.progress.model_dump(),
            }
            ingestion_progress = (
                BriefingIngestionProgressV1_2.model_validate(progress_payload)
                if is_v1_2
                else BriefingIngestionProgressV1_1.model_validate(progress_payload)
            )
            sections: dict[str, list[PremarketBriefingItem]] = {
                "statutory_announcements": [],
                "financial_changes": [],
                "market_snapshot": [],
                "company_industry_news": [],
                "macro_flashes": [],
            }
            for event in events:
                sections[briefing_section_for_event(event.event_type)].append(
                    project_briefing_item(event)
                )
            briefing_id = _uuid(
                f"briefing-v{'1.2' if is_v1_2 else '1.1'}:{runtime_mode.value}:"
                f"{R1_INSTRUMENT.instrument_id}:"
                f"{window_start.isoformat()}:{effective_as_of.isoformat()}:{manifest_hash}"
            )
            assembled = PremarketBriefingAssembler().assemble(
                briefing_id=briefing_id,
                instrument=R1_INSTRUMENT,
                comparison_since=window_start,
                as_of=effective_as_of,
                generated_at=effective_generated_at,
                ingestion_progress=ingestion_progress,
                source_coverage=coverages,
                statutory_announcements=sections["statutory_announcements"],
                financial_changes=sections["financial_changes"],
                market_snapshot=sections["market_snapshot"],
                company_industry_news=sections["company_industry_news"],
                macro_flashes=sections["macro_flashes"],
                ruleset_version=FIXTURE_RULESET_VERSION,
            )
            briefing_payload = {
                **assembled.model_dump(),
                "schema_version": "1.2" if is_v1_2 else "1.1",
                "runtime_mode": runtime_mode,
                "release_tier": release_tier,
                "ingestion_progress": ingestion_progress,
                "source_coverage": coverages,
            }
            briefing = (
                PremarketBriefingViewV1_2.model_validate(briefing_payload)
                if is_v1_2
                else PremarketBriefingViewV1_1.model_validate(briefing_payload)
            )
            if isinstance(briefing, PremarketBriefingViewV1_2):
                projected_evidence = {
                    event.event_id: event
                    for event in events
                    if isinstance(event, EventEvidenceViewV1_2)
                }
                if len(projected_evidence) != len(events):
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_BRIEFING_PROJECTION_INVALID",
                    )
                try:
                    validate_briefing_evidence_projection(
                        briefing=briefing,
                        evidence_by_event_id=projected_evidence,
                    )
                except ValueError as error:
                    raise ApplicationError(
                        ErrorCode.PRECONDITION_FAILED,
                        "R1_BRIEFING_PROJECTION_INVALID",
                    ) from error
            await connection.execute(
                text(
                    """
                    INSERT INTO app_users (id, display_name)
                    VALUES (:owner_id, 'Local development user')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"owner_id": _LOCAL_OWNER_ID},
            )
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO briefings (
                        id, owner_id, kind, generated_at, as_of, data_delay_seconds,
                        omitted_duplicate_count, source_health, model_version, prompt_hash,
                        rule_version, payload
                    )
                    VALUES (
                        :id, :owner_id, 'premarket', :generated_at, :as_of, 0,
                        :omitted_duplicate_count, CAST(:source_health AS jsonb),
                        :model_version, :prompt_hash, :rule_version, CAST(:payload AS jsonb)
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": briefing_id,
                    "owner_id": _LOCAL_OWNER_ID,
                    "generated_at": effective_generated_at,
                    "as_of": effective_as_of,
                    "omitted_duplicate_count": briefing.omitted_duplicate_count,
                    "source_health": _json([item.model_dump(mode="json") for item in coverages]),
                    "model_version": f"deterministic-{runtime_mode.value}",
                    "prompt_hash": "0" * 64,
                    "rule_version": briefing.ruleset_version,
                    "payload": _json(briefing),
                },
            )

        evidence_count = len(
            {
                evidence_id
                for items in sections.values()
                for item in items
                for evidence_id in item.evidence_ids
            }
        )
        reason_codes = tuple(
            sorted(
                {
                    item.gap_reason
                    for item in coverages
                    if item.status is not SourceCoverageStatus.OK and item.gap_reason is not None
                }
            )
        )
        return R1BriefingStorageResult(
            briefing=briefing,
            manifest_hash=manifest_hash,
            event_count=sum(len(items) for items in sections.values()),
            evidence_count=evidence_count,
            idempotent_replay=(inserted.rowcount or 0) == 0,
            reason_codes=reason_codes,
        )

    async def acquire_fixture_lease(
        self,
        *,
        batch: FixtureBatch,
        owner: str,
        lease_seconds: int = 300,
    ) -> FixtureSourceLease:
        """Acquire a bounded source-local lease without changing its cursor version."""

        validate_fixture_batch_source_purity(batch)
        if not owner.strip() or lease_seconds < 1:
            raise ValueError("fixture lease requires a non-empty owner and positive duration")
        empty_cursor = _json({})
        empty_hash = hashlib.sha256(empty_cursor.encode()).hexdigest()
        async with self._engine.begin() as connection:
            await _register_fixture_source(connection, batch.source_id)
            await connection.execute(
                text(
                    """
                    INSERT INTO source_cursors (
                        source_id, stream, cursor_payload, cursor_hash,
                        business_watermark, last_success_at
                    )
                    VALUES (
                        :source_id, :stream, CAST(:cursor_payload AS jsonb),
                        :cursor_hash, NULL, NULL
                    )
                    ON CONFLICT (source_id, stream) DO NOTHING
                    """
                ),
                {
                    "source_id": batch.source_id,
                    "stream": _FIXTURE_STREAM,
                    "cursor_payload": empty_cursor,
                    "cursor_hash": empty_hash,
                },
            )
            leased = (
                (
                    await connection.execute(
                        text(
                            """
                            UPDATE source_cursors
                               SET lease_owner = :owner,
                                   lease_expires_at = clock_timestamp()
                                       + (:lease_seconds * interval '1 second'),
                                   updated_at = clock_timestamp()
                             WHERE source_id = :source_id
                               AND stream = :stream
                               AND (
                                   lease_owner IS NULL
                                   OR lease_expires_at <= clock_timestamp()
                                   OR lease_owner = :owner
                               )
                         RETURNING cursor_version, lease_expires_at
                            """
                        ),
                        {
                            "source_id": batch.source_id,
                            "stream": _FIXTURE_STREAM,
                            "owner": owner,
                            "lease_seconds": lease_seconds,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if leased is None:
            raise ApplicationError(
                ErrorCode.CONFLICT,
                "R1_SOURCE_LEASE_BUSY",
                retryable=True,
            )
        return FixtureSourceLease(
            source_id=batch.source_id,
            owner=owner,
            expected_cursor_version=cast(int, leased["cursor_version"]),
            lease_expires_at=cast(datetime, leased["lease_expires_at"]),
        )

    async def persist_fixture_batch(
        self,
        *,
        batch: FixtureBatch,
        window_start: datetime,
        window_end: datetime,
        lease: FixtureSourceLease,
    ) -> FixtureIngestStorageResult:
        validate_fixture_batch_source_purity(batch)
        if lease.source_id != batch.source_id:
            raise ApplicationError(ErrorCode.CONFLICT, "R1_SOURCE_LEASE_MISMATCH")
        manifest_hash = fixture_manifest_hash(batch)
        batch_id = _uuid(
            f"ingest:{batch.source_id}:{window_start.isoformat()}:{window_end.isoformat()}:{manifest_hash}"
        )
        source_watermark = max(
            cast(datetime, item.progress.business_watermark) for item in batch.coverages
        )
        async with self._engine.begin() as connection:
            cursor_state = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT cursor_payload, cursor_version, lease_owner,
                                   lease_expires_at,
                                   lease_expires_at > clock_timestamp() AS lease_valid
                              FROM source_cursors
                             WHERE source_id = :source_id
                               AND stream = :stream
                             FOR UPDATE
                            """
                        ),
                        {"source_id": batch.source_id, "stream": _FIXTURE_STREAM},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                cursor_state is None
                or cursor_state["cursor_version"] != lease.expected_cursor_version
                or cursor_state["lease_owner"] != lease.owner
                or cursor_state["lease_valid"] is not True
            ):
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "R1_CURSOR_CAS_CONFLICT",
                    retryable=True,
                )
            existing = (
                (
                    await connection.execute(
                        text("SELECT status, metrics FROM source_runs WHERE id = :batch_id"),
                        {"batch_id": batch_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and existing["status"] == "succeeded":
                metrics = cast(dict[str, object], existing["metrics"])
                await connection.execute(
                    text(
                        """
                        UPDATE source_cursors
                           SET lease_owner = NULL,
                               lease_expires_at = NULL,
                               updated_at = clock_timestamp()
                         WHERE source_id = :source_id
                           AND stream = :stream
                           AND cursor_version = :cursor_version
                           AND lease_owner = :owner
                        """
                    ),
                    {
                        "source_id": batch.source_id,
                        "stream": _FIXTURE_STREAM,
                        "cursor_version": lease.expected_cursor_version,
                        "owner": lease.owner,
                    },
                )
                return FixtureIngestStorageResult(
                    manifest_hash=manifest_hash,
                    batch_audit_id=batch_id,
                    records_fetched=int(cast(int, metrics["records_fetched"])),
                    evidence_stored=int(cast(int, metrics["evidence_stored"])),
                    event_revisions_stored=int(cast(int, metrics["event_revisions_stored"])),
                    source_watermark=source_watermark,
                    idempotent_replay=True,
                )

            cursor_payload = cast(dict[str, object], cursor_state["cursor_payload"])
            previous_window = cursor_payload.get("fixture_window_end")
            if (
                previous_window is not None
                and datetime.fromisoformat(cast(str, previous_window)) >= window_end
            ):
                await connection.execute(
                    text(
                        """
                        UPDATE source_cursors
                           SET lease_owner = NULL,
                               lease_expires_at = NULL,
                               updated_at = clock_timestamp()
                         WHERE source_id = :source_id
                           AND stream = :stream
                           AND cursor_version = :cursor_version
                           AND lease_owner = :owner
                        """
                    ),
                    {
                        "source_id": batch.source_id,
                        "stream": _FIXTURE_STREAM,
                        "cursor_version": lease.expected_cursor_version,
                        "owner": lease.owner,
                    },
                )
                return FixtureIngestStorageResult(
                    manifest_hash=manifest_hash,
                    batch_audit_id=batch_id,
                    records_fetched=0,
                    evidence_stored=0,
                    event_revisions_stored=0,
                    source_watermark=None,
                    idempotent_replay=True,
                    reason_code="R1_CURSOR_WINDOW_NOT_NEWER",
                )

            await _register_fixture_source(connection, batch.source_id)

            evidence_inserted = 0
            event_revisions_inserted = 0
            for event in sorted(batch.events, key=lambda item: (str(item.event_id), item.revision)):
                for evidence in event.evidence:
                    inserted = await connection.execute(
                        text(
                            """
                            INSERT INTO raw_documents (
                                id, source_id, source_document_id, canonical_url, title,
                                content_sha256, normalized_sha256, object_key, storage_class,
                                published_at, first_seen_at, fetched_at, license_id, rights_hash,
                                extractor_version
                            )
                            VALUES (
                                :id, :source_id, :source_document_id, :canonical_url, :title,
                                :content_sha256, :content_sha256, NULL, 'metadata_only',
                                :published_at, :first_seen_at, :fetched_at, :license_id,
                                :rights_hash, :extractor_version
                            )
                            ON CONFLICT (id) DO NOTHING
                            """
                        ),
                        {
                            "id": evidence.evidence_id,
                            "source_id": evidence.source_id,
                            "source_document_id": str(evidence.evidence_id),
                            "canonical_url": str(evidence.source_url),
                            "title": evidence.title,
                            "content_sha256": evidence.content_sha256,
                            "published_at": evidence.published_at,
                            "first_seen_at": evidence.first_seen_at,
                            "fetched_at": evidence.fetched_at,
                            "license_id": evidence.license_id,
                            "rights_hash": evidence.rights_snapshot_hash,
                            "extractor_version": event.extraction_version,
                        },
                    )
                    evidence_inserted += max(inserted.rowcount or 0, 0)

                await connection.execute(
                    text(
                        """
                        INSERT INTO event_clusters (
                            id, canonical_event_type, opened_at, last_changed_at
                        )
                        VALUES (:id, :event_type, :opened_at, :last_changed_at)
                        ON CONFLICT (id) DO UPDATE
                        SET last_changed_at = GREATEST(
                            event_clusters.last_changed_at,
                            EXCLUDED.last_changed_at
                        )
                        """
                    ),
                    {
                        "id": event.cluster_id,
                        "event_type": event.event_type,
                        "opened_at": min(item.first_seen_at for item in event.evidence),
                        "last_changed_at": event.revision_known_at,
                    },
                )
                row_id = _row_id(event.event_id, event.revision)
                predecessor_id = (
                    None
                    if event.supersedes_revision is None
                    else _row_id(event.event_id, event.supersedes_revision)
                )
                inserted_event = await connection.execute(
                    text(
                        """
                        INSERT INTO market_events (
                            id, cluster_id, revision, event_type, event_status, direction,
                            materiality, impact_horizon, confidence, event_time, published_at,
                            first_seen_at, valid_from, valid_to, extractor_version, rule_version,
                            payload, logical_event_id, revision_known_at, supersedes_revision_id
                        )
                        VALUES (
                            :id, :cluster_id, :revision, :event_type, 'announced', 'unknown',
                            0.5, 'days', 0.8, :published_at, :published_at,
                            :first_seen_at, :revision_known_at, NULL, :extractor_version,
                            :rule_version, CAST(:payload AS jsonb), :logical_event_id,
                            :revision_known_at, :supersedes_revision_id
                        )
                        ON CONFLICT (logical_event_id, revision) DO NOTHING
                        """
                    ),
                    {
                        "id": row_id,
                        "cluster_id": event.cluster_id,
                        "revision": event.revision,
                        "event_type": event.event_type,
                        "published_at": min(item.published_at for item in event.evidence),
                        "first_seen_at": min(item.first_seen_at for item in event.evidence),
                        "revision_known_at": event.revision_known_at,
                        "extractor_version": event.extraction_version,
                        "rule_version": event.ruleset_version,
                        "payload": _json(event),
                        "logical_event_id": event.event_id,
                        "supersedes_revision_id": predecessor_id,
                    },
                )
                event_revisions_inserted += max(inserted_event.rowcount or 0, 0)
                await connection.execute(
                    text(
                        """
                        INSERT INTO event_entities (event_id, entity_type, entity_id)
                        VALUES (:event_id, 'instrument', :instrument_id)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"event_id": row_id, "instrument_id": R1_INSTRUMENT.instrument_id},
                )
                relation = (
                    "conflict"
                    if event.change_type is BriefingChangeType.CONTRADICTION
                    else "update"
                    if event.revision > 1
                    else "primary"
                )
                for evidence in event.evidence:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO event_documents (event_id, document_id, relation)
                            VALUES (:event_id, :document_id, :relation)
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {
                            "event_id": row_id,
                            "document_id": evidence.evidence_id,
                            "relation": relation,
                        },
                    )
                for fact_index, fact in enumerate(event.facts):
                    for evidence_id in fact.evidence_ids:
                        claim_id = uuid5(
                            row_id,
                            f"claim:{fact_index}:{fact.slot}:{evidence_id}",
                        )
                        await connection.execute(
                            text(
                                """
                                INSERT INTO event_claims (
                                    id, event_id, name, value, unit, period,
                                    evidence_document_id, evidence_span
                                )
                                VALUES (
                                    :id, :event_id, :name, CAST(:value AS jsonb), :unit,
                                    :period, :evidence_document_id, :evidence_span
                                )
                                ON CONFLICT (id) DO NOTHING
                                """
                            ),
                            {
                                "id": claim_id,
                                "event_id": row_id,
                                "name": fact.slot,
                                "value": _json(fact.value),
                                "unit": fact.unit,
                                "period": fact.period,
                                "evidence_document_id": evidence_id,
                                "evidence_span": "synthetic structured fixture",
                            },
                        )

            metrics = {
                "data_mode": "synthetic_development_only",
                "manifest_hash": manifest_hash,
                "records_fetched": len(batch.events),
                "evidence_stored": evidence_inserted,
                "event_revisions_stored": event_revisions_inserted,
                "coverages": [item.model_dump(mode="json") for item in batch.coverages],
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO source_runs (
                        id, source_id, stream, scheduled_for, started_at, finished_at,
                        status, documents_seen, rights_hash, metrics
                    )
                    VALUES (
                        :id, :source_id, 'r1_fixture_premarket', :scheduled_for,
                        :started_at, :finished_at, 'succeeded', :documents_seen,
                        :rights_hash, CAST(:metrics AS jsonb)
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        status = 'succeeded',
                        documents_seen = EXCLUDED.documents_seen,
                        safe_error_code = NULL,
                        rights_hash = EXCLUDED.rights_hash,
                        metrics = EXCLUDED.metrics
                    """
                ),
                {
                    "id": batch_id,
                    "source_id": batch.source_id,
                    "scheduled_for": window_end,
                    "started_at": window_end,
                    "finished_at": window_end,
                    "documents_seen": len(batch.events),
                    "rights_hash": FIXTURE_RIGHTS_HASH,
                    "metrics": _json(metrics),
                },
            )
            next_cursor_payload = {"fixture_window_end": window_end.isoformat()}
            cursor_hash = hashlib.sha256(_json(next_cursor_payload).encode()).hexdigest()
            finalized = await connection.execute(
                text(
                    """
                    UPDATE source_cursors
                       SET cursor_payload = CAST(:cursor_payload AS jsonb),
                           cursor_hash = :cursor_hash,
                           business_watermark = :business_watermark,
                           last_success_at = :last_success_at,
                           cursor_version = cursor_version + 1,
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           updated_at = clock_timestamp()
                     WHERE source_id = :source_id
                       AND stream = :stream
                       AND cursor_version = :expected_cursor_version
                       AND lease_owner = :lease_owner
                       AND lease_expires_at > clock_timestamp()
                       AND (
                           cursor_payload->>'fixture_window_end' IS NULL
                           OR (cursor_payload->>'fixture_window_end')::timestamptz
                               < :window_end
                       )
                       AND (
                           business_watermark IS NULL
                           OR business_watermark <= :business_watermark
                       )
                       AND (
                           last_success_at IS NULL
                           OR last_success_at <= :last_success_at
                       )
                    """
                ),
                {
                    "source_id": batch.source_id,
                    "stream": _FIXTURE_STREAM,
                    "cursor_payload": _json(next_cursor_payload),
                    "cursor_hash": cursor_hash,
                    "business_watermark": source_watermark,
                    "last_success_at": source_watermark,
                    "window_end": window_end,
                    "expected_cursor_version": lease.expected_cursor_version,
                    "lease_owner": lease.owner,
                },
            )
            if (finalized.rowcount or 0) != 1:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "R1_CURSOR_CAS_CONFLICT",
                    retryable=True,
                )

        return FixtureIngestStorageResult(
            manifest_hash=manifest_hash,
            batch_audit_id=batch_id,
            records_fetched=len(batch.events),
            evidence_stored=evidence_inserted,
            event_revisions_stored=event_revisions_inserted,
            source_watermark=source_watermark,
            idempotent_replay=False,
        )

    async def record_fixture_failure(
        self,
        *,
        batch: FixtureBatch,
        window_start: datetime,
        window_end: datetime,
        lease: FixtureSourceLease,
        reason_code: str,
    ) -> None:
        """Persist one source terminal failure without advancing its cursor."""

        validate_fixture_batch_source_purity(batch)
        if lease.source_id != batch.source_id:
            raise ApplicationError(ErrorCode.CONFLICT, "R1_SOURCE_LEASE_MISMATCH")
        manifest_hash = fixture_manifest_hash(batch)
        batch_id = _uuid(
            f"ingest:{batch.source_id}:{window_start.isoformat()}:"
            f"{window_end.isoformat()}:{manifest_hash}"
        )
        failed_coverages = [
            SourceCoverage(
                source_id=coverage.source_id,
                capability=coverage.capability,
                source_role=coverage.source_role,
                status=SourceCoverageStatus.UNAVAILABLE,
                observed_at=window_end,
                last_success_at=None,
                license_id=coverage.license_id,
                gap_reason=reason_code,
                progress=SourceIngestionProgress(
                    business_watermark=None,
                    batch_audit_id=coverage.progress.batch_audit_id,
                    advance_state=SourceAdvanceState.NOT_ADVANCED,
                    data_mode=SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY,
                ),
            )
            for coverage in batch.coverages
        ]
        metrics = {
            "data_mode": "synthetic_development_only",
            "manifest_hash": manifest_hash,
            "records_fetched": 0,
            "evidence_stored": 0,
            "event_revisions_stored": 0,
            "coverages": [item.model_dump(mode="json") for item in failed_coverages],
            "reason_code": reason_code,
        }
        async with self._engine.begin() as connection:
            await _register_fixture_source(connection, batch.source_id)
            released = await connection.execute(
                text(
                    """
                    UPDATE source_cursors
                       SET lease_owner = NULL,
                           lease_expires_at = NULL,
                           updated_at = clock_timestamp()
                     WHERE source_id = :source_id
                       AND stream = :stream
                       AND cursor_version = :cursor_version
                       AND lease_owner = :owner
                       AND lease_expires_at > clock_timestamp()
                    """
                ),
                {
                    "source_id": batch.source_id,
                    "stream": _FIXTURE_STREAM,
                    "cursor_version": lease.expected_cursor_version,
                    "owner": lease.owner,
                },
            )
            if (released.rowcount or 0) != 1:
                raise ApplicationError(
                    ErrorCode.CONFLICT,
                    "R1_CURSOR_CAS_CONFLICT",
                    retryable=True,
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO source_runs (
                        id, source_id, stream, scheduled_for, started_at, finished_at,
                        status, documents_seen, safe_error_code, rights_hash, metrics
                    )
                    VALUES (
                        :id, :source_id, :stream, :scheduled_for, :started_at,
                        :finished_at, 'failed', 0, :safe_error_code, :rights_hash,
                        CAST(:metrics AS jsonb)
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET finished_at = EXCLUDED.finished_at,
                        status = 'failed',
                        documents_seen = 0,
                        safe_error_code = EXCLUDED.safe_error_code,
                        metrics = EXCLUDED.metrics
                    WHERE source_runs.status <> 'succeeded'
                    """
                ),
                {
                    "id": batch_id,
                    "source_id": batch.source_id,
                    "stream": _FIXTURE_STREAM,
                    "scheduled_for": window_end,
                    "started_at": window_end,
                    "finished_at": window_end,
                    "safe_error_code": reason_code,
                    "rights_hash": FIXTURE_RIGHTS_HASH,
                    "metrics": _json(metrics),
                },
            )

    async def generate_and_store_briefing(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        generated_at: datetime,
        source_ids: tuple[str, ...],
    ) -> FixtureBriefingStorageResult:
        async with self._engine.begin() as connection:
            event_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT DISTINCT ON (event.logical_event_id) event.payload
                              FROM market_events AS event
                              JOIN event_entities AS entity ON entity.event_id = event.id
                             WHERE entity.entity_type = 'instrument'
                               AND entity.entity_id = :instrument_id
                               AND event.first_seen_at <= :as_of
                               AND event.revision_known_at <= :as_of
                             ORDER BY event.logical_event_id, event.revision DESC
                            """
                        ),
                        {"instrument_id": R1_INSTRUMENT.instrument_id, "as_of": window_end},
                    )
                )
                .mappings()
                .all()
            )
            requested_sources = set(source_ids)
            persisted_events = [
                event
                for row in event_rows
                if _event_matches_requested_scope(
                    (event := _parse_event_view(row["payload"])),
                    source_ids=requested_sources,
                    runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
                    schema_version="1.0",
                )
            ]
            run_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT DISTINCT ON (source_id) source_id, status, metrics
                              FROM source_runs
                             WHERE stream = 'r1_fixture_premarket'
                               AND scheduled_for = :window_end
                               AND source_id = ANY(:source_ids)
                               AND status IN (
                                   'succeeded', 'failed', 'blocked_rights', 'rate_limited'
                               )
                             ORDER BY source_id, finished_at DESC, id DESC
                            """
                        ),
                        {"window_end": window_end, "source_ids": list(source_ids)},
                    )
                )
                .mappings()
                .all()
            )
            found_sources = {cast(str, row["source_id"]) for row in run_rows}
            if found_sources != requested_sources:
                raise ApplicationError(
                    ErrorCode.PRECONDITION_FAILED,
                    "R1_SOURCE_TERMINAL_STATE_INCOMPLETE",
                    retryable=True,
                )
            successful_sources = {
                cast(str, row["source_id"]) for row in run_rows if row["status"] == "succeeded"
            }
            coverages: list[SourceCoverage] = []
            for row in run_rows:
                metrics = cast(dict[str, object], row["metrics"])
                raw_coverages = cast(list[object], metrics["coverages"])
                coverages.extend(
                    SourceCoverage.model_validate(coverage) for coverage in raw_coverages
                )
            manifests = sorted(
                f"{row['source_id']}:{row['status']}:"
                f"{cast(str, cast(dict[str, object], row['metrics'])['manifest_hash'])}"
                for row in run_rows
            )
            manifest_hash = hashlib.sha256(":".join(manifests).encode()).hexdigest()
            source_local_events = [
                event
                for event in persisted_events
                if event.event_type == "source_observation.market_close"
                and {evidence.source_id for evidence in event.evidence}.issubset(successful_sources)
            ]
            comparison_events = build_market_comparison_events(
                observations=tuple(source_local_events),
                as_of=window_end,
            )
            for event in comparison_events:
                await _persist_fixture_event(connection, event)
            events = [
                event
                for event in persisted_events
                if event.event_type not in {"source_observation.market_close", "market.close"}
                and {evidence.source_id for evidence in event.evidence}.issubset(successful_sources)
            ]
            events.extend(comparison_events[-1:])
            primary = next(
                item
                for item in coverages
                if item.source_id == CNINFO_SOURCE_ID
                and item.capability == "statutory.announcements"
            )
            ingestion_progress = BriefingIngestionProgress(
                source_id=primary.source_id,
                capability=primary.capability,
                **primary.progress.model_dump(),
            )
            sections: dict[str, list[PremarketBriefingItem]] = {
                "statutory_announcements": [],
                "financial_changes": [],
                "market_snapshot": [],
                "company_industry_news": [],
                "macro_flashes": [],
            }
            for event in events:
                sections[briefing_section_for_event(event.event_type)].append(
                    project_briefing_item(event)
                )
            briefing_id = _uuid(
                f"briefing:{R1_INSTRUMENT.instrument_id}:{window_start.isoformat()}:"
                f"{window_end.isoformat()}:{manifest_hash}"
            )
            briefing = PremarketBriefingAssembler().assemble(
                briefing_id=briefing_id,
                instrument=R1_INSTRUMENT,
                comparison_since=window_start,
                as_of=window_end,
                generated_at=generated_at,
                ingestion_progress=ingestion_progress,
                source_coverage=coverages,
                statutory_announcements=sections["statutory_announcements"],
                financial_changes=sections["financial_changes"],
                market_snapshot=sections["market_snapshot"],
                company_industry_news=sections["company_industry_news"],
                macro_flashes=sections["macro_flashes"],
                ruleset_version=FIXTURE_RULESET_VERSION,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO app_users (id, display_name)
                    VALUES (:owner_id, 'Local fixture user')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"owner_id": _LOCAL_OWNER_ID},
            )
            inserted_briefing = await connection.execute(
                text(
                    """
                    INSERT INTO briefings (
                        id, owner_id, kind, generated_at, as_of, data_delay_seconds,
                        omitted_duplicate_count, source_health, model_version, prompt_hash,
                        rule_version, payload
                    )
                    VALUES (
                        :id, :owner_id, 'premarket', :generated_at, :as_of, 0,
                        :omitted_duplicate_count, CAST(:source_health AS jsonb),
                        'deterministic-fixture', :prompt_hash, :rule_version,
                        CAST(:payload AS jsonb)
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": briefing_id,
                    "owner_id": _LOCAL_OWNER_ID,
                    "generated_at": generated_at,
                    "as_of": window_end,
                    "omitted_duplicate_count": briefing.omitted_duplicate_count,
                    "source_health": _json([item.model_dump(mode="json") for item in coverages]),
                    "prompt_hash": "0" * 64,
                    "rule_version": briefing.ruleset_version,
                    "payload": _json(briefing),
                },
            )

        evidence_count = len(
            {
                evidence_id
                for section in sections.values()
                for item in section
                for evidence_id in item.evidence_ids
            }
        )
        return FixtureBriefingStorageResult(
            briefing=briefing,
            manifest_hash=manifest_hash,
            event_count=len(events),
            evidence_count=evidence_count,
            idempotent_replay=(inserted_briefing.rowcount or 0) == 0,
            reason_codes=(
                () if successful_sources == requested_sources else ("R1_SOURCE_UNAVAILABLE",)
            ),
        )

    async def get_latest_briefing(
        self,
        *,
        as_of: datetime,
        expected_runtime_mode: R1RuntimeMode | None = None,
        expected_release_tier: SourceReleaseTier | None = None,
    ) -> PremarketBriefingReadModel | None:
        if expected_runtime_mode is None or expected_release_tier is None:
            return None
        async with self._engine.connect() as connection:
            payload = await connection.scalar(
                text(
                    """
                    SELECT payload
                      FROM briefings
                     WHERE kind = 'premarket'
                       AND as_of <= :as_of
                       AND payload->'instrument'->>'instrument_id' = :instrument_id
                       AND CASE
                               WHEN payload->>'schema_version' IN ('1.1', '1.2')
                               THEN payload->>'runtime_mode'
                               ELSE 'demo_replay'
                           END = :runtime_mode
                       AND CASE
                               WHEN payload->>'schema_version' IN ('1.1', '1.2')
                               THEN payload->>'release_tier'
                               ELSE 'dev_only'
                           END = :release_tier
                     ORDER BY as_of DESC, generated_at DESC, id DESC
                     LIMIT 1
                    """
                ),
                {
                    "as_of": as_of,
                    "instrument_id": R1_INSTRUMENT.instrument_id,
                    "runtime_mode": expected_runtime_mode.value,
                    "release_tier": expected_release_tier.value,
                },
            )
        return None if payload is None else _parse_briefing_view(payload)

    async def get_event_evidence(
        self,
        *,
        event_id: UUID,
        selector: EventEvidenceRevisionSelector,
        expected_runtime_mode: R1RuntimeMode,
        expected_release_tier: SourceReleaseTier,
    ) -> EventEvidenceReadModel | None:
        async with self._engine.connect() as connection:
            selected = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT payload
                              FROM market_events
                             WHERE logical_event_id = :event_id
                               AND first_seen_at <= :as_of
                               AND revision_known_at <= :as_of
                               AND (
                                   (
                                       payload->>'schema_version' IN ('1.1', '1.2')
                                       AND payload->>'runtime_mode' = :runtime_mode
                                       AND jsonb_typeof(payload->'evidence') = 'array'
                                       AND jsonb_array_length(payload->'evidence') > 0
                                       AND NOT EXISTS (
                                           SELECT 1
                                             FROM jsonb_array_elements(payload->'evidence') AS item
                                            WHERE item->'provenance'->>'runtime_mode'
                                                      IS DISTINCT FROM :runtime_mode
                                               OR item->'provenance'->>'release_tier'
                                                      IS DISTINCT FROM :release_tier
                                       )
                                   )
                                   OR (
                                       payload->>'schema_version' = '1.0'
                                       AND :runtime_mode = 'demo_replay'
                                       AND :release_tier = 'dev_only'
                                   )
                               )
                               AND (
                                   CAST(:revision AS integer) IS NULL
                                   OR revision = CAST(:revision AS integer)
                               )
                             ORDER BY revision DESC
                             LIMIT 1
                            """
                        ),
                        {
                            "event_id": event_id,
                            "as_of": selector.as_of,
                            "revision": selector.revision,
                            "runtime_mode": expected_runtime_mode.value,
                            "release_tier": expected_release_tier.value,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if selected is None:
                return None
            timeline_rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT payload
                              FROM market_events
                             WHERE logical_event_id = :event_id
                               AND first_seen_at <= :as_of
                               AND revision_known_at <= :as_of
                               AND (
                                   (
                                       payload->>'schema_version' IN ('1.1', '1.2')
                                       AND payload->>'runtime_mode' = :runtime_mode
                                       AND jsonb_typeof(payload->'evidence') = 'array'
                                       AND jsonb_array_length(payload->'evidence') > 0
                                       AND NOT EXISTS (
                                           SELECT 1
                                             FROM jsonb_array_elements(payload->'evidence') AS item
                                            WHERE item->'provenance'->>'runtime_mode'
                                                      IS DISTINCT FROM :runtime_mode
                                               OR item->'provenance'->>'release_tier'
                                                      IS DISTINCT FROM :release_tier
                                       )
                                   )
                                   OR (
                                       payload->>'schema_version' = '1.0'
                                       AND :runtime_mode = 'demo_replay'
                                       AND :release_tier = 'dev_only'
                                   )
                               )
                               AND (
                                   CAST(:before_revision AS integer) IS NULL
                                   OR revision < CAST(:before_revision AS integer)
                               )
                             ORDER BY revision DESC
                             LIMIT :limit
                            """
                        ),
                        {
                            "event_id": event_id,
                            "as_of": selector.as_of,
                            "before_revision": selector.timeline_before_revision,
                            "limit": selector.timeline_limit + 1,
                            "runtime_mode": expected_runtime_mode.value,
                            "release_tier": expected_release_tier.value,
                        },
                    )
                )
                .mappings()
                .all()
            )

        timeline_views = [_parse_event_view(row["payload"]) for row in timeline_rows]
        has_older = len(timeline_views) > selector.timeline_limit
        visible = timeline_views[: selector.timeline_limit]
        timeline_items = [
            EventRevisionTimelineItem(
                revision=item.revision,
                change_type=item.change_type,
                revision_known_at=item.revision_known_at,
                supersedes_revision=item.supersedes_revision,
                extraction_version=item.extraction_version,
                ruleset_version=item.ruleset_version,
                audit_id=item.audit_id,
            )
            for item in visible
        ]
        selected_view = _parse_event_view(selected["payload"])
        payload = selected_view.model_dump()
        payload["as_of"] = selector.as_of
        payload["revision_timeline"] = EventRevisionTimeline(
            before_revision=selector.timeline_before_revision,
            items=timeline_items,
            has_older=has_older,
            next_before_revision=(timeline_items[-1].revision if has_older else None),
        )
        return _parse_event_view(payload)


__all__ = [
    "EventEvidenceReadModel",
    "FixtureBriefingStorageResult",
    "FixtureIngestStorageResult",
    "PostgresR1PremarketRepository",
    "PremarketBriefingReadModel",
]
