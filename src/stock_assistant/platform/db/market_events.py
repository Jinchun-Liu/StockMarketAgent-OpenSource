"""PostgreSQL MarketEventRepository 1.1 adapter and atomic global-catalog refresh."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from stock_assistant.common.time import utc_now
from stock_assistant.contracts.common import CursorPage
from stock_assistant.contracts.events import EventStatus, MarketEvent
from stock_assistant.contracts.global_events import (
    GlobalEventCatalogSlice,
    GlobalEventCatalogStatus,
    PolicyRefreshRequest,
    PolicyRefreshResult,
)
from stock_assistant.contracts.persistence import (
    MarketEventLookupRequest,
    MarketEventLookupResult,
    MarketEventQuery,
    MarketEventQueryV1_1,
    MarketEventUpsertRequest,
    MarketEventUpsertResult,
)
from stock_assistant.platform.research.synthetic_global_events import (
    GLOBAL_EVENT_CATALOG_SOURCE_ID,
    GLOBAL_EVENT_RIGHTS_HASH,
    GLOBAL_EVENT_RULE_VERSION,
    synthetic_global_event_manifest_sha256,
    synthetic_global_events,
)

_ROW_NAMESPACE: Final = UUID("c6216ec7-8a34-56f2-a028-221042f2a6e4")
_RUN_NAMESPACE: Final = UUID("beab50eb-44ce-5a46-b343-29635bb6c8cd")
_STREAM: Final = "global_official_events"


class MarketEventRepositoryError(RuntimeError):
    """Stable adapter error that never exposes SQL or vendor details."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _row_id(event_id: UUID, revision: int) -> UUID:
    return uuid5(_ROW_NAMESPACE, f"{event_id}:{revision}")


def _cursor(sort_time: object, row_id: UUID) -> str:
    payload = _json({"sort_time": str(sort_time), "row_id": str(row_id)}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[str, UUID]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
        return str(payload["sort_time"]), UUID(str(payload["row_id"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid market-event cursor") from error


async def _register_source(
    connection: AsyncConnection,
    *,
    source_id: str,
    rights_hash: str,
    license_id: str,
    catalog_read_allowed: bool = False,
) -> None:
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
            "source_id": source_id,
            "rights_record": _json(
                {
                    "source_mode": "synthetic_development_only",
                    "license_id": license_id,
                    "network_access": False,
                    "catalog_read_allowed": catalog_read_allowed,
                    "permitted_uses": ["display", "summary", "ai_use"],
                }
            ),
            "rights_hash": rights_hash,
        },
    )


async def _persist_event(
    connection: AsyncConnection,
    event: MarketEvent,
    *,
    expected_revision: int | None,
) -> tuple[bool, int]:
    existing = (
        (
            await connection.execute(
                text(
                    """
                    SELECT payload
                      FROM market_events
                     WHERE logical_event_id = :event_id AND revision = :revision
                    """
                ),
                {"event_id": event.event_id, "revision": event.revision},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        stored = MarketEvent.model_validate(existing["payload"])
        if stored != event:
            raise MarketEventRepositoryError("MARKET_EVENT_REVISION_CONFLICT")
        return False, 0
    latest_revision = cast(
        int | None,
        await connection.scalar(
            text(
                """
                SELECT max(revision)
                  FROM market_events
                 WHERE logical_event_id = :event_id
                """
            ),
            {"event_id": event.event_id},
        ),
    )
    if latest_revision != expected_revision:
        raise MarketEventRepositoryError("MARKET_EVENT_EXPECTED_REVISION_MISMATCH")
    if expected_revision is None and event.revision != 1:
        raise MarketEventRepositoryError("MARKET_EVENT_REVISION_GAP")
    if expected_revision is not None and event.revision != expected_revision + 1:
        raise MarketEventRepositoryError("MARKET_EVENT_REVISION_GAP")

    evidence_created = 0
    for evidence in event.evidence:
        await _register_source(
            connection,
            source_id=evidence.source_id,
            rights_hash=evidence.rights_snapshot_hash,
            license_id=evidence.license_id,
        )
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
                    :content_sha256, :content_sha256, :object_key, 'metadata_only',
                    :published_at, :first_seen_at, :first_seen_at, :license_id,
                    :rights_hash, :extractor_version, NULL, NULL
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": evidence.evidence_id,
                "source_id": evidence.source_id,
                "source_document_id": str(evidence.evidence_id),
                "canonical_url": evidence.source_url,
                "title": evidence.title,
                "content_sha256": evidence.content_sha256,
                "object_key": evidence.object_key,
                "published_at": evidence.published_at,
                "first_seen_at": evidence.first_seen_at,
                "license_id": evidence.license_id,
                "rights_hash": evidence.rights_snapshot_hash,
                "extractor_version": event.extractor_version,
            },
        )
        evidence_created += max(inserted.rowcount or 0, 0)
        persisted_hash = await connection.scalar(
            text("SELECT content_sha256 FROM raw_documents WHERE id = :id"),
            {"id": evidence.evidence_id},
        )
        if persisted_hash != evidence.content_sha256:
            raise MarketEventRepositoryError("MARKET_EVENT_EVIDENCE_CONFLICT")

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
            "opened_at": event.first_seen_at,
            "last_changed_at": event.revision_known_at,
        },
    )
    row_id = _row_id(event.event_id, event.revision)
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
                :id, :cluster_id, :revision, :event_type, :event_status, :direction,
                :materiality, :impact_horizon, :confidence, :event_time, :published_at,
                :first_seen_at, :revision_known_at, NULL, :extractor_version,
                :rule_version, CAST(:payload AS jsonb), :event_id,
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
            "event_status": event.event_status,
            "direction": event.direction,
            "materiality": event.materiality,
            "impact_horizon": event.impact_horizon,
            "confidence": event.confidence,
            "event_time": event.event_time,
            "published_at": event.published_at,
            "first_seen_at": event.first_seen_at,
            "revision_known_at": event.revision_known_at,
            "extractor_version": event.extractor_version,
            "rule_version": event.rule_version,
            "payload": _json(event.model_dump(mode="json")),
            "event_id": event.event_id,
            "supersedes_revision_id": (
                None if expected_revision is None else _row_id(event.event_id, expected_revision)
            ),
        },
    )
    if (inserted_event.rowcount or 0) != 1:
        raise MarketEventRepositoryError("MARKET_EVENT_CONCURRENT_REVISION_CONFLICT")
    entities = [
        *(("instrument", value) for value in event.security_ids),
        *(("company", value) for value in event.company_ids),
        *(("region", value) for value in event.region_ids),
        *(("industry", fact.value) for fact in event.facts if fact.name == "industry_id"),
    ]
    for entity_type, entity_id in entities:
        await connection.execute(
            text(
                """
                INSERT INTO event_entities (event_id, entity_type, entity_id)
                VALUES (:event_id, :entity_type, :entity_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"event_id": row_id, "entity_type": entity_type, "entity_id": entity_id},
        )
    relation = (
        "correction"
        if event.event_status is EventStatus.CORRECTED
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
            {"event_id": row_id, "document_id": evidence.evidence_id, "relation": relation},
        )
    for index, fact in enumerate(event.facts):
        await connection.execute(
            text(
                """
                INSERT INTO event_claims (
                    id, event_id, name, value, unit, period,
                    evidence_document_id, evidence_span
                )
                VALUES (
                    :id, :event_id, :name, CAST(:value AS jsonb), :unit, :period,
                    :evidence_document_id, :evidence_span
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": uuid5(row_id, f"claim:{index}:{fact.evidence_id}"),
                "event_id": row_id,
                "name": fact.name,
                "value": _json(fact.value),
                "unit": fact.unit,
                "period": fact.period,
                "evidence_document_id": fact.evidence_id,
                "evidence_span": fact.evidence_span,
            },
        )
    return True, evidence_created


class PostgresMarketEventRepository:
    """Append-only PIT event adapter; v1.0 reads remain source compatible."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_event(self, request: MarketEventLookupRequest) -> MarketEventLookupResult:
        statement = (
            text(
                """
                SELECT payload
                  FROM market_events
                 WHERE logical_event_id = :event_id
                   AND revision_known_at <= :as_of
                 ORDER BY revision DESC
                 LIMIT 1
                """
            )
            if request.revision is None
            else text(
                """
                SELECT payload
                  FROM market_events
                 WHERE logical_event_id = :event_id
                   AND revision_known_at <= :as_of
                   AND revision = :revision
                 LIMIT 1
                """
            )
        )
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "event_id": request.event_id,
                            "revision": request.revision,
                            "as_of": request.context.as_of,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        event = None if row is None else MarketEvent.model_validate(row["payload"])
        return MarketEventLookupResult(
            schema_version="1.0",
            found=event is not None,
            event=event,
            as_of=request.context.as_of,
        )

    async def upsert_event(self, request: MarketEventUpsertRequest) -> MarketEventUpsertResult:
        if request.event.revision_known_at > request.context.as_of:
            raise MarketEventRepositoryError("MARKET_EVENT_FUTURE_REVISION")
        async with self._engine.begin() as connection:
            created, _ = await _persist_event(
                connection,
                request.event,
                expected_revision=request.expected_revision,
            )
        return MarketEventUpsertResult(
            schema_version="1.0",
            event=request.event,
            created=created,
            stored_at=utc_now(),
            idempotent_replay=not created,
        )

    async def list_events(self, request: MarketEventQuery) -> CursorPage[MarketEvent]:
        return await self._list_events(
            request,
            industry_ids=[],
            match_any_exposure=False,
            allowed_rule_versions=[],
        )

    async def list_events_by_industry(
        self, request: MarketEventQueryV1_1
    ) -> CursorPage[MarketEvent]:
        # The append-only table also retains frozen R1 EventView payloads. R2
        # catalog reads admit only the currently authorized MarketEvent
        # normalizer; future production normalizers must be added through a
        # separately reviewed rights/source activation.
        return await self._list_events(
            request,
            industry_ids=request.industry_ids,
            match_any_exposure=True,
            allowed_rule_versions=[GLOBAL_EVENT_RULE_VERSION],
        )

    async def _list_events(
        self,
        request: MarketEventQuery | MarketEventQueryV1_1,
        *,
        industry_ids: list[str],
        match_any_exposure: bool,
        allowed_rule_versions: list[str],
    ) -> CursorPage[MarketEvent]:
        cursor_time: str | None = None
        cursor_id: UUID | None = None
        if request.cursor is not None:
            cursor_time, cursor_id = _decode_cursor(request.cursor)
        parameters: dict[str, object] = {
            "as_of": request.context.as_of,
            "limit": request.limit + 1,
            "sort": request.sort.value,
            "include_superseded": request.include_superseded_revisions,
            "event_ids": [str(item) for item in request.event_ids],
            "event_ids_empty": not request.event_ids,
            "cluster_ids": [str(item) for item in request.cluster_ids],
            "cluster_ids_empty": not request.cluster_ids,
            "event_types": request.event_types,
            "event_types_empty": not request.event_types,
            "statuses": [item.value for item in request.statuses],
            "statuses_empty": not request.statuses,
            "security_ids": request.security_ids,
            "security_ids_empty": not request.security_ids,
            "company_ids": request.company_ids,
            "company_ids_empty": not request.company_ids,
            "region_ids": request.region_ids,
            "region_ids_empty": not request.region_ids,
            "industry_ids": industry_ids,
            "industry_ids_empty": not industry_ids,
            "exposure_filters_empty": not (
                request.security_ids or request.company_ids or request.region_ids or industry_ids
            ),
            "match_any_exposure": match_any_exposure,
            "allowed_rule_versions": allowed_rule_versions,
            "allowed_rule_versions_empty": not allowed_rule_versions,
            "first_seen_since": request.first_seen_since,
            "first_seen_until": request.first_seen_until,
            "revision_known_since": request.revision_known_since,
            "revision_known_until": request.revision_known_until,
            "cursor_empty": request.cursor is None,
            "cursor_time": cursor_time,
            "cursor_id": cursor_id,
        }
        query = text(
            """
            WITH filtered AS (
                SELECT id, payload, logical_event_id, revision,
                       CASE CAST(:sort AS text)
                           WHEN 'published_desc' THEN published_at
                           WHEN 'first_seen_desc' THEN first_seen_at
                           ELSE revision_known_at
                       END AS sort_time,
                       row_number() OVER (
                           PARTITION BY logical_event_id ORDER BY revision DESC
                       ) AS revision_rank
                  FROM market_events
                 WHERE revision_known_at <= :as_of
                   AND (:event_ids_empty OR logical_event_id = ANY(CAST(:event_ids AS uuid[])))
                   AND (:cluster_ids_empty OR cluster_id = ANY(CAST(:cluster_ids AS uuid[])))
                   AND (:event_types_empty OR event_type = ANY(CAST(:event_types AS text[])))
                   AND (:statuses_empty OR event_status = ANY(CAST(:statuses AS text[])))
                   AND (
                       :allowed_rule_versions_empty
                       OR rule_version = ANY(CAST(:allowed_rule_versions AS text[]))
                   )
                   AND (
                       :exposure_filters_empty
                       OR (
                           :match_any_exposure
                           AND EXISTS (
                               SELECT 1 FROM event_entities AS entity
                                WHERE entity.event_id = market_events.id
                                  AND (
                                      (
                                          NOT :security_ids_empty
                                          AND entity.entity_type = 'instrument'
                                          AND entity.entity_id = ANY(
                                              CAST(:security_ids AS text[])
                                          )
                                      )
                                      OR (
                                          NOT :company_ids_empty
                                          AND entity.entity_type = 'company'
                                          AND entity.entity_id = ANY(
                                              CAST(:company_ids AS text[])
                                          )
                                      )
                                      OR (
                                          NOT :region_ids_empty
                                          AND entity.entity_type = 'region'
                                          AND entity.entity_id = ANY(
                                              CAST(:region_ids AS text[])
                                          )
                                      )
                                      OR (
                                          NOT :industry_ids_empty
                                          AND entity.entity_type = 'industry'
                                          AND entity.entity_id = ANY(
                                              CAST(:industry_ids AS text[])
                                          )
                                      )
                                  )
                           )
                       )
                       OR (
                           NOT :match_any_exposure
                           AND (
                               :security_ids_empty OR EXISTS (
                                   SELECT 1 FROM event_entities AS entity
                                    WHERE entity.event_id = market_events.id
                                      AND entity.entity_type = 'instrument'
                                      AND entity.entity_id = ANY(
                                          CAST(:security_ids AS text[])
                                      )
                               )
                           )
                           AND (
                               :company_ids_empty OR EXISTS (
                                   SELECT 1 FROM event_entities AS entity
                                    WHERE entity.event_id = market_events.id
                                      AND entity.entity_type = 'company'
                                      AND entity.entity_id = ANY(
                                          CAST(:company_ids AS text[])
                                      )
                               )
                           )
                           AND (
                               :region_ids_empty OR EXISTS (
                                   SELECT 1 FROM event_entities AS entity
                                    WHERE entity.event_id = market_events.id
                                      AND entity.entity_type = 'region'
                                      AND entity.entity_id = ANY(
                                          CAST(:region_ids AS text[])
                                      )
                               )
                           )
                       )
                   )
                   AND (
                       CAST(:first_seen_since AS timestamptz) IS NULL
                       OR first_seen_at >= CAST(:first_seen_since AS timestamptz)
                   )
                   AND (
                       CAST(:first_seen_until AS timestamptz) IS NULL
                       OR first_seen_at <= CAST(:first_seen_until AS timestamptz)
                   )
                   AND (
                       CAST(:revision_known_since AS timestamptz) IS NULL
                       OR revision_known_at >= CAST(:revision_known_since AS timestamptz)
                   )
                   AND (
                       CAST(:revision_known_until AS timestamptz) IS NULL
                       OR revision_known_at <= CAST(:revision_known_until AS timestamptz)
                   )
            ), visible AS (
                SELECT id, payload, sort_time
                  FROM filtered
                 WHERE (:include_superseded OR revision_rank = 1)
            )
            SELECT id, payload, sort_time
              FROM visible
             WHERE :cursor_empty
                OR sort_time < CAST(:cursor_time AS timestamptz)
                OR (sort_time = CAST(:cursor_time AS timestamptz) AND id < :cursor_id)
             ORDER BY sort_time DESC, id DESC
             LIMIT :limit
            """
        )
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        query,
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        has_more = len(rows) > request.limit
        visible_rows = rows[: request.limit]
        next_cursor = (
            _cursor(visible_rows[-1]["sort_time"], cast(UUID, visible_rows[-1]["id"]))
            if has_more and visible_rows
            else None
        )
        return CursorPage[MarketEvent](
            schema_version="1.0",
            items=[MarketEvent.model_validate(row["payload"]) for row in visible_rows],
            next_cursor=next_cursor,
            has_more=has_more,
            as_of=request.context.as_of,
        )

    async def get_catalog_slice(self, request: MarketEventQueryV1_1) -> GlobalEventCatalogSlice:
        async with self._engine.connect() as connection:
            state = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT cursor.business_watermark,
                                   cursor.last_success_at,
                                   source.rights_record,
                                   source.rights_hash,
                                   source.rights_expiry
                              FROM data_sources AS source
                              LEFT JOIN source_cursors AS cursor
                                ON cursor.source_id = source.id
                               AND cursor.stream = :stream
                             WHERE source.id = :source_id
                            """
                        ),
                        {"source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID, "stream": _STREAM},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if state is None:
            return GlobalEventCatalogSlice(
                schema_version="1.0",
                status=GlobalEventCatalogStatus.UNAVAILABLE,
                reason_code="EVENT_CATALOG_NOT_REFRESHED",
            )
        # ``data_sources.enabled`` controls future external-network activation.
        # Reading the retained synthetic catalog is authorized independently by
        # its immutable rights snapshot so a disabled network source can still
        # serve the zero-network R2 fixture safely.
        rights_record = state["rights_record"]
        rights_expiry = cast(datetime | None, state["rights_expiry"])
        rights_allowed = (
            isinstance(rights_record, dict)
            and rights_record.get("catalog_read_allowed") is True
            and rights_record.get("license_id") == "sma-original-synthetic-v1"
            and state["rights_hash"] == GLOBAL_EVENT_RIGHTS_HASH
            and (rights_expiry is None or rights_expiry > request.context.requested_at)
        )
        if not rights_allowed:
            return GlobalEventCatalogSlice(
                schema_version="1.0",
                status=GlobalEventCatalogStatus.RIGHTS_BLOCKED,
                reason_code="EVENT_CATALOG_RIGHTS_BLOCKED",
            )
        if state["last_success_at"] is None:
            return GlobalEventCatalogSlice(
                schema_version="1.0",
                status=GlobalEventCatalogStatus.UNAVAILABLE,
                reason_code="EVENT_CATALOG_NOT_REFRESHED",
            )
        last_success_at = cast(datetime, state["last_success_at"])
        if request.context.requested_at - last_success_at > timedelta(hours=2):
            return GlobalEventCatalogSlice(
                schema_version="1.0",
                status=GlobalEventCatalogStatus.STALE,
                watermark=state["business_watermark"],
                last_success_at=last_success_at,
                reason_code="EVENT_CATALOG_STALE",
            )
        page = await self.list_events_by_industry(request)
        status = (
            GlobalEventCatalogStatus.READY_WITH_MATCHES
            if page.items
            else GlobalEventCatalogStatus.READY_NO_MATCHES
        )
        return GlobalEventCatalogSlice(
            schema_version="1.0",
            status=status,
            events=page.items,
            watermark=state["business_watermark"],
            last_success_at=last_success_at,
        )

    async def refresh_synthetic_catalog(self, request: PolicyRefreshRequest) -> PolicyRefreshResult:
        events = synthetic_global_events()
        manifest_hash = synthetic_global_event_manifest_sha256(events)
        if request.fixture_manifest_sha256 != manifest_hash:
            raise MarketEventRepositoryError("GLOBAL_EVENT_MANIFEST_MISMATCH")
        bucket = request.scheduled_for.astimezone(UTC).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        run_id = uuid5(_RUN_NAMESPACE, f"{GLOBAL_EVENT_CATALOG_SOURCE_ID}:{bucket}:{manifest_hash}")
        watermark = max(event.revision_known_at for event in events)
        async with self._engine.begin() as connection:
            await _register_source(
                connection,
                source_id=GLOBAL_EVENT_CATALOG_SOURCE_ID,
                rights_hash=GLOBAL_EVENT_RIGHTS_HASH,
                license_id="sma-original-synthetic-v1",
                catalog_read_allowed=True,
            )
            empty_payload = _json({})
            await connection.execute(
                text(
                    """
                    INSERT INTO source_cursors (
                        source_id, stream, cursor_payload, cursor_hash,
                        business_watermark, last_success_at
                    )
                    VALUES (
                        :source_id, :stream, CAST(:payload AS jsonb), :cursor_hash,
                        NULL, NULL
                    )
                    ON CONFLICT (source_id, stream) DO NOTHING
                    """
                ),
                {
                    "source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID,
                    "stream": _STREAM,
                    "payload": empty_payload,
                    "cursor_hash": hashlib.sha256(empty_payload.encode()).hexdigest(),
                },
            )
            state = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT cursor_version, last_success_at
                              FROM source_cursors
                             WHERE source_id = :source_id AND stream = :stream
                             FOR UPDATE
                            """
                        ),
                        {"source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID, "stream": _STREAM},
                    )
                )
                .mappings()
                .one()
            )
            existing_run = (
                (
                    await connection.execute(
                        text("SELECT status, metrics FROM source_runs WHERE id = :run_id"),
                        {"run_id": run_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing_run is not None and existing_run["status"] == "succeeded":
                metrics = cast(dict[str, object], existing_run["metrics"])
                return PolicyRefreshResult(
                    schema_version="1.0",
                    scheduled_for=request.scheduled_for,
                    completed_at=request.context.requested_at,
                    events_seen=cast(int, metrics["events_seen"]),
                    event_revisions_created=cast(int, metrics["event_revisions_created"]),
                    evidence_created=cast(int, metrics["evidence_created"]),
                    watermark=watermark,
                    manifest_sha256=manifest_hash,
                    cursor_advanced=False,
                    idempotent_replay=True,
                )
            if existing_run is not None:
                raise MarketEventRepositoryError("GLOBAL_EVENT_REFRESH_RUN_CONFLICT")
            last_success = cast(datetime | None, state["last_success_at"])
            if last_success is not None and last_success > request.scheduled_for:
                return PolicyRefreshResult(
                    schema_version="1.0",
                    scheduled_for=request.scheduled_for,
                    completed_at=request.context.requested_at,
                    events_seen=len(events),
                    event_revisions_created=0,
                    evidence_created=0,
                    watermark=watermark,
                    manifest_sha256=manifest_hash,
                    cursor_advanced=False,
                    idempotent_replay=True,
                )
            revisions_created = 0
            evidence_created = 0
            expected: dict[UUID, int | None] = {}
            for event in sorted(events, key=lambda item: (str(item.event_id), item.revision)):
                current_expected = expected.get(event.event_id)
                if event.event_id not in expected:
                    current_expected = cast(
                        int | None,
                        await connection.scalar(
                            text(
                                """
                                SELECT max(revision) FROM market_events
                                 WHERE logical_event_id = :event_id
                                """
                            ),
                            {"event_id": event.event_id},
                        ),
                    )
                if current_expected is not None and current_expected >= event.revision:
                    current_expected = event.revision - 1
                created, evidence_count = await _persist_event(
                    connection,
                    event,
                    expected_revision=current_expected,
                )
                revisions_created += int(created)
                evidence_created += evidence_count
                expected[event.event_id] = max(event.revision, current_expected or 0)
            metrics = {
                "events_seen": len(events),
                "event_revisions_created": revisions_created,
                "evidence_created": evidence_created,
                "manifest_sha256": manifest_hash,
                "source_mode": request.source_mode,
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO source_runs (
                        id, source_id, stream, scheduled_for, started_at, finished_at,
                        status, documents_seen, rights_hash, metrics,
                        input_cursor_hash, output_cursor_hash
                    )
                    VALUES (
                        :id, :source_id, :stream, :scheduled_for, :started_at,
                        :finished_at, 'succeeded', :documents_seen, :rights_hash,
                        CAST(:metrics AS jsonb), NULL, :output_cursor_hash
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": run_id,
                    "source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID,
                    "stream": _STREAM,
                    "scheduled_for": request.scheduled_for,
                    "started_at": request.context.requested_at,
                    "finished_at": request.context.requested_at,
                    "documents_seen": len(events),
                    "rights_hash": GLOBAL_EVENT_RIGHTS_HASH,
                    "metrics": _json(metrics),
                    "output_cursor_hash": manifest_hash,
                },
            )
            cursor_payload = _json({"bucket": bucket.isoformat(), "manifest_sha256": manifest_hash})
            updated = await connection.execute(
                text(
                    """
                    UPDATE source_cursors
                       SET cursor_payload = CAST(:payload AS jsonb),
                           cursor_hash = :cursor_hash,
                           business_watermark = GREATEST(
                               COALESCE(business_watermark, :watermark), :watermark
                           ),
                           last_success_at = :last_success_at,
                           cursor_version = cursor_version + 1,
                           updated_at = clock_timestamp()
                     WHERE source_id = :source_id AND stream = :stream
                       AND cursor_version = :cursor_version
                    """
                ),
                {
                    "payload": cursor_payload,
                    "cursor_hash": hashlib.sha256(cursor_payload.encode()).hexdigest(),
                    "watermark": watermark,
                    "last_success_at": request.scheduled_for,
                    "source_id": GLOBAL_EVENT_CATALOG_SOURCE_ID,
                    "stream": _STREAM,
                    "cursor_version": state["cursor_version"],
                },
            )
            if (updated.rowcount or 0) != 1:
                raise MarketEventRepositoryError("GLOBAL_EVENT_CURSOR_CAS_CONFLICT")
        return PolicyRefreshResult(
            schema_version="1.0",
            scheduled_for=request.scheduled_for,
            completed_at=request.context.requested_at,
            events_seen=len(events),
            event_revisions_created=revisions_created,
            evidence_created=evidence_created,
            watermark=watermark,
            manifest_sha256=manifest_hash,
            cursor_advanced=True,
        )


__all__ = ["MarketEventRepositoryError", "PostgresMarketEventRepository"]
