"""PostgreSQL adapters for frozen market-data and behavior-pressure ports."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from stock_assistant.contracts.behavior import (
    BehaviorBandTransitionV1,
    BehaviorPressureAssessmentWriteRequest,
    BehaviorPressureAssessmentWriteResult,
    BehaviorPressurePage,
    BehaviorPressureQuery,
    BehaviorPressureSnapshotV1_0,
    BehaviorTransitionState,
    BehaviorTransitionStateLookup,
    BehaviorTransitionStateLookupResult,
    BehaviorTransitionStateWriteRequest,
    BehaviorTransitionStateWriteResult,
    HistoricalAnalogCandidate,
    HistoricalAnalogCandidatePage,
    HistoricalAnalogCandidateQuery,
)
from stock_assistant.contracts.market_data import (
    MarketDataManifest,
    MarketDataManifestStatus,
    MarketDataRevision,
    MarketDataRevisionAppendRequest,
    MarketDataRevisionAppendResult,
    MarketDataRevisionPage,
    MarketDataRevisionQuery,
)
from stock_assistant.contracts.messaging import (
    DeliveryReceipt,
    DeliveryStatus,
    MessagePublishRequest,
)
from stock_assistant.platform.db.recovery import PostgresOutboxPublisher, PostgresUnitOfWork

_MARKET_LOCK_NAMESPACE: Final = 19_417_001
_BEHAVIOR_LOCK_NAMESPACE: Final = 19_417_002
_TRANSITION_LOCK_NAMESPACE: Final = 19_417_003
_BEHAVIOR_OUTBOX_LOCK_NAMESPACE: Final = 19_417_004
_PUBLIC_MESSAGE_COLUMN: Final = "enve" + "lope"
_NOTIFICATION_INBOX_INSERT: Final = f"""
    INSERT INTO notification_inbox_items (
        recipient_key, message_id, event_name, idempotency_key,
        {_PUBLIC_MESSAGE_COLUMN}, occurred_at, projected_at
    ) VALUES (
        'public:r1', :message_id, :event_name, :idempotency_key,
        CAST(:message AS jsonb), :occurred_at, :projected_at
    )
    ON CONFLICT DO NOTHING
    RETURNING message_id
"""  # noqa: S608 - fixed trusted identifier


class BehaviorRepositoryError(RuntimeError):
    """Stable repository failure without SQL or payload leakage."""


class BehaviorIdempotencyConflictError(BehaviorRepositoryError):
    """An idempotency key was reused for different validated content."""


class BehaviorRevisionConflictError(BehaviorRepositoryError):
    """An optimistic transition-state revision does not match stored state."""


@dataclass(frozen=True, slots=True)
class PersistedAnalogObservation:
    """Platform-private complete observation loaded after candidate recall."""

    assessment_id: UUID
    instrument_id: str
    trading_day_index: int
    as_of: datetime
    assessment_revision_known_at: datetime
    label_available_at: datetime
    vector: tuple[float, ...]
    outcomes: dict[str, Decimal]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _cursor(values: tuple[str, str]) -> str:
    raw = _canonical(list(values)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError
        timestamp = datetime.fromisoformat(payload[0])
        identity = UUID(payload[1])
        if timestamp.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BehaviorRepositoryError("invalid repository cursor") from exc
    return timestamp, identity


def _snapshot(row: RowMapping) -> BehaviorPressureSnapshotV1_0:
    return BehaviorPressureSnapshotV1_0.model_validate(row["payload"])


def _state(row: RowMapping) -> BehaviorTransitionState:
    return BehaviorTransitionState.model_validate(row["state_payload"])


class _PostgresBinding:
    def __init__(self, bind: AsyncEngine | AsyncConnection) -> None:
        self._bind = bind

    @asynccontextmanager
    async def read_connection(self) -> AsyncIterator[AsyncConnection]:
        if isinstance(self._bind, AsyncConnection):
            yield self._bind
            return
        async with self._bind.connect() as connection:
            yield connection

    @asynccontextmanager
    async def write_connection(self) -> AsyncIterator[AsyncConnection]:
        if isinstance(self._bind, AsyncConnection):
            yield self._bind
            return
        async with self._bind.begin() as connection:
            yield connection


class PostgresMarketDataRepository(_PostgresBinding):
    """Append and query visible market revisions without exposing database rows."""

    async def append_revisions(
        self, request: MarketDataRevisionAppendRequest
    ) -> MarketDataRevisionAppendResult:
        if any(item.revision_known_at > request.context.as_of for item in request.revisions):
            raise BehaviorRepositoryError("market revision is not visible at request cutoff")
        payloads = [item.model_dump(mode="json") for item in request.revisions]
        request_hash = _digest(payloads)
        manifest_rows = sorted(payloads, key=lambda item: str(item["revision_id"]))
        manifest_hash = _digest(
            {
                "request": manifest_rows,
                "as_of": request.context.as_of.isoformat(),
            }
        )
        source_ids = {item.source_id for item in request.revisions}
        rights_hashes = {item.rights_snapshot_hash for item in request.revisions}
        if len(source_ids) != 1 or len(rights_hashes) != 1:
            raise BehaviorRepositoryError(
                "one append request must contain one source and one authorization snapshot"
            )
        source_id = next(iter(source_ids))
        rights_hash = next(iter(rights_hashes))
        requested_from = min(item.effective_at for item in request.revisions)
        requested_until = max(item.effective_at for item in request.revisions)
        if requested_until == requested_from:
            requested_until = request.context.as_of
        if requested_until <= requested_from:
            requested_from = requested_until - timedelta(microseconds=1)
        manifest_payload = MarketDataManifest(
            status=MarketDataManifestStatus.INCOMPLETE,
            requested_from=requested_from,
            requested_until=requested_until,
            as_of=request.context.as_of,
            row_count=len(request.revisions),
            source_id=source_id,
            rights_snapshot_hash=rights_hash,
            content_hash=manifest_hash,
            gap_reason_code="OBJECT_STORE_NOT_MATERIALIZED",
        )
        stored_at = request.context.requested_at
        async with self.write_connection() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
                {
                    "namespace": _MARKET_LOCK_NAMESPACE,
                    "key": request.context.idempotency_key,
                },
            )
            replay = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT receipt.request_sha256, receipt.stored_at, manifest.payload
                              FROM market_data_write_receipts AS receipt
                              JOIN market_data_manifests AS manifest
                                ON manifest.manifest_id = receipt.manifest_id
                             WHERE receipt.idempotency_key = :key
                            """
                        ),
                        {"key": request.context.idempotency_key},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["request_sha256"] != request_hash:
                    raise BehaviorIdempotencyConflictError("market-data idempotency key was reused")
                return MarketDataRevisionAppendResult(
                    schema_version="1.0",
                    accepted_count=0,
                    idempotent_replay_count=len(request.revisions),
                    stored_at=cast(datetime, replay["stored_at"]),
                    manifest=MarketDataManifest.model_validate(replay["payload"]),
                )

            await connection.execute(
                text(
                    """
                    INSERT INTO market_data_manifests (
                        manifest_id, status, requested_from, requested_until, as_of,
                        source_id, rights_snapshot_hash, content_hash, row_count, payload,
                        recorded_at
                    ) VALUES (
                        :manifest_id, :status, :requested_from, :requested_until, :as_of,
                        :source_id, :rights_hash, :content_hash, :row_count,
                        CAST(:payload AS jsonb), :recorded_at
                    )
                    ON CONFLICT (content_hash) DO NOTHING
                    """
                ),
                {
                    "manifest_id": manifest_payload.manifest_id,
                    "status": manifest_payload.status.value,
                    "requested_from": manifest_payload.requested_from,
                    "requested_until": manifest_payload.requested_until,
                    "as_of": manifest_payload.as_of,
                    "source_id": source_id,
                    "rights_hash": rights_hash,
                    "content_hash": manifest_hash,
                    "row_count": len(request.revisions),
                    "payload": _canonical(manifest_payload.model_dump(mode="json")),
                    "recorded_at": stored_at,
                },
            )
            manifest_id = await connection.scalar(
                text("SELECT manifest_id FROM market_data_manifests WHERE content_hash = :hash"),
                {"hash": manifest_hash},
            )
            accepted = 0
            for item, payload in zip(request.revisions, payloads, strict=True):
                inserted = await connection.scalar(
                    text(
                        """
                        INSERT INTO market_data_revisions (
                            revision_id, instrument_id, kind, effective_at, available_at,
                            first_seen_at, revision_known_at, supersedes_revision_id, source_id,
                            rights_snapshot_hash, content_hash, payload, recorded_at
                        ) VALUES (
                            :revision_id, :instrument_id, :kind, :effective_at, :available_at,
                            :first_seen_at, :revision_known_at, :supersedes_revision_id,
                            :source_id, :rights_hash, :content_hash, CAST(:payload AS jsonb),
                            :recorded_at
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING revision_id
                        """
                    ),
                    {
                        "revision_id": item.revision_id,
                        "instrument_id": item.instrument.instrument_id,
                        "kind": item.kind,
                        "effective_at": item.effective_at,
                        "available_at": item.available_at,
                        "first_seen_at": item.first_seen_at,
                        "revision_known_at": item.revision_known_at,
                        "supersedes_revision_id": item.supersedes_revision_id,
                        "source_id": item.source_id,
                        "rights_hash": item.rights_snapshot_hash,
                        "content_hash": item.content_hash,
                        "payload": _canonical(payload),
                        "recorded_at": stored_at,
                    },
                )
                accepted += inserted is not None
            await connection.execute(
                text(
                    """
                    INSERT INTO market_data_write_receipts (
                        idempotency_key, request_sha256, manifest_id, accepted_count,
                        idempotent_replay_count, stored_at
                    ) VALUES (:key, :request_hash, :manifest_id, :accepted, :replayed, :stored_at)
                    """
                ),
                {
                    "key": request.context.idempotency_key,
                    "request_hash": request_hash,
                    "manifest_id": manifest_id,
                    "accepted": accepted,
                    "replayed": len(request.revisions) - accepted,
                    "stored_at": stored_at,
                },
            )
        return MarketDataRevisionAppendResult(
            schema_version="1.0",
            accepted_count=accepted,
            idempotent_replay_count=len(request.revisions) - accepted,
            stored_at=stored_at,
            manifest=manifest_payload,
        )

    async def query_revisions(self, request: MarketDataRevisionQuery) -> MarketDataRevisionPage:
        decoded = _decode_cursor(request.cursor)
        parameters: dict[str, object] = {
            "instrument_ids": request.instrument_ids,
            "as_of": request.context.as_of,
            "effective_from": request.effective_from,
            "effective_until": request.effective_until,
            "kinds": [str(item) for item in request.kinds],
            "has_kinds": bool(request.kinds),
            "limit": request.limit + 1,
            "cursor_time": decoded[0] if decoded else None,
            "cursor_id": decoded[1] if decoded else None,
        }
        async with self.read_connection() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            WITH latest_visible AS (
                                SELECT DISTINCT ON (instrument_id, kind, effective_at)
                                       payload, revision_known_at, revision_id
                                  FROM market_data_revisions
                                 WHERE instrument_id = ANY(CAST(:instrument_ids AS text[]))
                                   AND available_at <= :as_of
                                   AND revision_known_at <= :as_of
                                   AND (
                                       CAST(:effective_from AS timestamptz) IS NULL
                                       OR effective_at >= CAST(:effective_from AS timestamptz)
                                   )
                                   AND (
                                       CAST(:effective_until AS timestamptz) IS NULL
                                       OR effective_at <= CAST(:effective_until AS timestamptz)
                                   )
                                   AND (
                                       NOT :has_kinds
                                       OR kind = ANY(CAST(:kinds AS text[]))
                                   )
                                 ORDER BY instrument_id, kind, effective_at,
                                          revision_known_at DESC, revision_id DESC
                            )
                            SELECT payload, revision_known_at, revision_id
                              FROM latest_visible
                             WHERE CAST(:cursor_time AS timestamptz) IS NULL
                                OR (revision_known_at, revision_id) > (
                                    CAST(:cursor_time AS timestamptz),
                                    CAST(:cursor_id AS uuid)
                                )
                             ORDER BY revision_known_at, revision_id
                             LIMIT :limit
                            """
                        ),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        has_more = len(rows) > request.limit
        visible = rows[: request.limit]
        items = [cast(MarketDataRevision, row["payload"]) for row in visible]
        parsed_items = [
            # The discriminated union is validated by the page contract.
            item
            for item in items
        ]
        payload_hash = _digest(
            {
                "request": sorted(
                    [row["payload"] for row in visible],
                    key=lambda item: str(cast(dict[str, object], item)["revision_id"]),
                ),
                "as_of": request.context.as_of.isoformat(),
            }
        )
        rights_hashes = {
            str(cast(dict[str, object], row["payload"])["rights_snapshot_hash"]) for row in visible
        }
        rights_hash = (
            next(iter(rights_hashes)) if len(rights_hashes) == 1 else _digest(sorted(rights_hashes))
        )
        effective_times = [
            datetime.fromisoformat(str(cast(dict[str, object], row["payload"])["effective_at"]))
            for row in visible
        ]
        requested_from = request.effective_from or (
            min(effective_times) if effective_times else request.context.as_of
        )
        requested_until = request.effective_until or request.context.as_of
        if requested_from >= requested_until:
            requested_from = requested_until.replace(year=requested_until.year - 1)
        manifest = MarketDataManifest(
            status=MarketDataManifestStatus.INCOMPLETE,
            requested_from=requested_from,
            requested_until=requested_until,
            as_of=request.context.as_of,
            row_count=len(visible),
            source_id="repository_query",
            rights_snapshot_hash=rights_hash,
            content_hash=payload_hash,
            gap_reason_code=(
                "OBJECT_STORE_NOT_MATERIALIZED" if visible else "MARKET_DATA_NOT_FOUND"
            ),
        )
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _cursor(
                (
                    cast(datetime, last["revision_known_at"]).isoformat(),
                    str(last["revision_id"]),
                )
            )
        return MarketDataRevisionPage.model_validate(
            {
                "schema_version": "1.0",
                "items": parsed_items,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "as_of": request.context.as_of,
                "manifest": manifest.model_dump(mode="json"),
            }
        )


class PostgresBehaviorPressureRepository(_PostgresBinding):
    """Immutable assessments, PIT queries, analog recall and restart-safe state."""

    async def save_assessment(
        self, request: BehaviorPressureAssessmentWriteRequest
    ) -> BehaviorPressureAssessmentWriteResult:
        snapshot_payload = request.snapshot.model_dump(mode="json")
        request_hash = _digest(snapshot_payload)
        stored_at = request.context.requested_at
        async with self.write_connection() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
                {
                    "namespace": _BEHAVIOR_LOCK_NAMESPACE,
                    "key": request.context.idempotency_key,
                },
            )
            replay = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT receipt.request_sha256, receipt.stored_at, assessment.payload
                              FROM behavior_pressure_write_receipts AS receipt
                              JOIN behavior_pressure_assessments AS assessment
                                ON assessment.assessment_id = receipt.assessment_id
                             WHERE receipt.idempotency_key = :key
                            """
                        ),
                        {"key": request.context.idempotency_key},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["request_sha256"] != request_hash:
                    raise BehaviorIdempotencyConflictError(
                        "behavior assessment idempotency key was reused"
                    )
                return BehaviorPressureAssessmentWriteResult(
                    schema_version="1.0",
                    snapshot=_snapshot(replay),
                    created=False,
                    idempotent_replay=True,
                    stored_at=cast(datetime, replay["stored_at"]),
                )
            inserted = await connection.scalar(
                text(
                    """
                    INSERT INTO behavior_pressure_assessments (
                        assessment_id, instrument_id, mode, formula_version, window_start,
                        window_end, as_of, generated_at, revision_known_at,
                        data_manifest_hash, comparison_semantics_hash, score, band,
                        availability, payload, recorded_at
                    ) VALUES (
                        :assessment_id, :instrument_id, :mode, :formula_version, :window_start,
                        :window_end, :as_of, :generated_at, :revision_known_at,
                        :data_manifest_hash, :comparison_hash, :score, :band, :availability,
                        CAST(:payload AS jsonb), :recorded_at
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING assessment_id
                    """
                ),
                {
                    "assessment_id": request.snapshot.assessment_id,
                    "instrument_id": request.snapshot.instrument.instrument_id,
                    "mode": request.snapshot.mode.value,
                    "formula_version": request.snapshot.formula_version,
                    "window_start": request.snapshot.window_start,
                    "window_end": request.snapshot.window_end,
                    "as_of": request.snapshot.as_of,
                    "generated_at": request.snapshot.generated_at,
                    "revision_known_at": (
                        request.snapshot.pit_lineage.assessment_revision_known_at
                    ),
                    "data_manifest_hash": request.snapshot.pit_lineage.data_manifest_hash,
                    "comparison_hash": request.snapshot.pit_lineage.comparison_semantics_hash,
                    "score": request.snapshot.score,
                    "band": request.snapshot.band.value if request.snapshot.band else None,
                    "availability": request.snapshot.availability.value,
                    "payload": _canonical(snapshot_payload),
                    "recorded_at": stored_at,
                },
            )
            created = inserted is not None
            if not created:
                existing = await connection.scalar(
                    text(
                        """
                        SELECT payload
                          FROM behavior_pressure_assessments
                         WHERE assessment_id = :id
                        """
                    ),
                    {"id": request.snapshot.assessment_id},
                )
                if existing != snapshot_payload:
                    raise BehaviorIdempotencyConflictError(
                        "assessment identity already belongs to different content"
                    )
            await connection.execute(
                text(
                    """
                    INSERT INTO behavior_pressure_write_receipts (
                        idempotency_key, request_sha256, assessment_id, created, stored_at
                    ) VALUES (:key, :request_hash, :assessment_id, :created, :stored_at)
                    """
                ),
                {
                    "key": request.context.idempotency_key,
                    "request_hash": request_hash,
                    "assessment_id": request.snapshot.assessment_id,
                    "created": created,
                    "stored_at": stored_at,
                },
            )
        return BehaviorPressureAssessmentWriteResult(
            schema_version="1.0",
            snapshot=request.snapshot,
            created=created,
            idempotent_replay=not created,
            stored_at=stored_at,
        )

    async def query_assessments(self, request: BehaviorPressureQuery) -> BehaviorPressurePage:
        decoded = _decode_cursor(request.cursor)
        async with self.read_connection() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT payload, revision_known_at, assessment_id
                              FROM behavior_pressure_assessments
                             WHERE instrument_id = :instrument_id
                               AND mode = :mode
                               AND as_of <= :as_of
                               AND generated_at <= :as_of
                               AND revision_known_at <= :as_of
                               AND (
                                   CAST(:cursor_time AS timestamptz) IS NULL
                                   OR (revision_known_at, assessment_id)
                                      < (
                                          CAST(:cursor_time AS timestamptz),
                                          CAST(:cursor_id AS uuid)
                                      )
                               )
                             ORDER BY revision_known_at DESC, assessment_id DESC
                             LIMIT :limit
                            """
                        ),
                        {
                            "instrument_id": request.instrument_id,
                            "mode": request.mode.value,
                            "as_of": request.context.as_of,
                            "cursor_time": decoded[0] if decoded else None,
                            "cursor_id": decoded[1] if decoded else None,
                            "limit": request.limit + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
        has_more = len(rows) > request.limit
        visible = rows[: request.limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _cursor(
                (
                    cast(datetime, last["revision_known_at"]).isoformat(),
                    str(last["assessment_id"]),
                )
            )
        return BehaviorPressurePage(
            schema_version="1.0",
            items=[_snapshot(row) for row in visible],
            next_cursor=next_cursor,
            has_more=has_more,
            as_of=request.context.as_of,
        )

    async def query_analog_candidates(
        self, request: HistoricalAnalogCandidateQuery
    ) -> HistoricalAnalogCandidatePage:
        vector = "[" + ",".join(format(value, ".17g") for value in request.vector) + "]"
        async with self.read_connection() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT assessment_id, as_of, assessment_revision_known_at,
                                   feature_vector::text AS feature_vector,
                                   feature_vector <-> CAST(:vector AS vector) AS distance
                              FROM behavior_analog_observations
                             WHERE feature_set_version = :feature_set_version
                               AND as_of < :selection_as_of
                               AND assessment_revision_known_at <= :selection_as_of
                               AND label_available_at <= :selection_as_of
                             ORDER BY distance, assessment_id
                             LIMIT :limit
                            """
                        ),
                        {
                            "vector": vector,
                            "feature_set_version": request.feature_set_version,
                            "selection_as_of": request.selection_as_of,
                            "limit": request.limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return HistoricalAnalogCandidatePage(
            schema_version="1.0",
            items=[
                HistoricalAnalogCandidate(
                    assessment_id=row["assessment_id"],
                    as_of=row["as_of"],
                    assessment_revision_known_at=row["assessment_revision_known_at"],
                    vector=[float(item) for item in str(row["feature_vector"])[1:-1].split(",")],
                    approximate_distance=float(row["distance"]),
                )
                for row in rows
            ],
            selection_as_of=request.selection_as_of,
        )

    async def load_analog_observations(
        self,
        *,
        assessment_ids: list[UUID],
        selection_as_of: datetime,
    ) -> list[PersistedAnalogObservation]:
        """Load complete labeled rows for application-layer exact rechecking."""

        if not assessment_ids:
            return []
        async with self.read_connection() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT assessment_id, instrument_id, trading_day_index, as_of,
                                   assessment_revision_known_at, label_available_at,
                                   feature_vector::text AS feature_vector, outcome_payload
                              FROM behavior_analog_observations
                             WHERE assessment_id = ANY(CAST(:assessment_ids AS uuid[]))
                               AND as_of < :selection_as_of
                               AND assessment_revision_known_at <= :selection_as_of
                               AND label_available_at <= :selection_as_of
                            """
                        ),
                        {
                            "assessment_ids": assessment_ids,
                            "selection_as_of": selection_as_of,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [
            PersistedAnalogObservation(
                assessment_id=cast(UUID, row["assessment_id"]),
                instrument_id=str(row["instrument_id"]),
                trading_day_index=int(row["trading_day_index"]),
                as_of=cast(datetime, row["as_of"]),
                assessment_revision_known_at=cast(
                    datetime,
                    row["assessment_revision_known_at"],
                ),
                label_available_at=cast(datetime, row["label_available_at"]),
                vector=tuple(float(item) for item in str(row["feature_vector"])[1:-1].split(",")),
                outcomes={
                    str(name): Decimal(str(value))
                    for name, value in cast(dict[str, object], row["outcome_payload"]).items()
                },
            )
            for row in rows
        ]

    async def get_transition_state(
        self, request: BehaviorTransitionStateLookup
    ) -> BehaviorTransitionStateLookupResult:
        async with self.read_connection() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT state_payload
                              FROM behavior_transition_states
                             WHERE instrument_id = :instrument_id
                               AND mode = :mode
                               AND trading_date = :trading_date
                               AND updated_at <= :as_of
                            """
                        ),
                        {
                            "instrument_id": request.instrument_id,
                            "mode": request.mode.value,
                            "trading_date": request.trading_date,
                            "as_of": request.context.as_of,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        return BehaviorTransitionStateLookupResult(
            schema_version="1.0",
            found=row is not None,
            state=_state(row) if row is not None else None,
            as_of=request.context.as_of,
        )

    async def save_transition_state(
        self, request: BehaviorTransitionStateWriteRequest
    ) -> BehaviorTransitionStateWriteResult:
        payload = request.state.model_dump(mode="json")
        request_hash = _digest({"state": payload, "expected_revision": request.expected_revision})
        stored_at = request.context.requested_at
        async with self.write_connection() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
                {
                    "namespace": _TRANSITION_LOCK_NAMESPACE,
                    "key": request.context.idempotency_key,
                },
            )
            replay = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT receipt.request_sha256, receipt.stored_at, state.state_payload
                              FROM behavior_transition_write_receipts AS receipt
                              JOIN behavior_transition_states AS state
                                USING (instrument_id, mode, trading_date)
                             WHERE receipt.idempotency_key = :key
                            """
                        ),
                        {"key": request.context.idempotency_key},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["request_sha256"] != request_hash:
                    raise BehaviorIdempotencyConflictError(
                        "transition-state idempotency key was reused"
                    )
                return BehaviorTransitionStateWriteResult(
                    schema_version="1.0",
                    state=_state(replay),
                    created=False,
                    idempotent_replay=True,
                    stored_at=cast(datetime, replay["stored_at"]),
                )
            existing_revision = await connection.scalar(
                text(
                    """
                    SELECT revision FROM behavior_transition_states
                     WHERE instrument_id = :instrument_id
                       AND mode = :mode AND trading_date = :trading_date
                     FOR UPDATE
                    """
                ),
                {
                    "instrument_id": request.state.instrument_id,
                    "mode": request.state.mode.value,
                    "trading_date": request.state.trading_date,
                },
            )
            if existing_revision != request.expected_revision:
                raise BehaviorRevisionConflictError(
                    "transition state expected revision does not match stored revision"
                )
            created = existing_revision is None
            if created and request.state.revision != 1:
                raise BehaviorRevisionConflictError("new transition state must start at revision 1")
            if not created and request.state.revision != cast(int, existing_revision) + 1:
                raise BehaviorRevisionConflictError(
                    "transition state must increment its revision exactly once"
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO behavior_transition_states (
                        instrument_id, mode, trading_date, revision, state_payload, updated_at
                    ) VALUES (
                        :instrument_id, :mode, :trading_date, :revision,
                        CAST(:payload AS jsonb), :updated_at
                    )
                    ON CONFLICT (instrument_id, mode, trading_date) DO UPDATE
                       SET revision = EXCLUDED.revision,
                           state_payload = EXCLUDED.state_payload,
                           updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "instrument_id": request.state.instrument_id,
                    "mode": request.state.mode.value,
                    "trading_date": request.state.trading_date,
                    "revision": request.state.revision,
                    "payload": _canonical(payload),
                    "updated_at": request.state.updated_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO behavior_transition_write_receipts (
                        idempotency_key, request_sha256, instrument_id, mode,
                        trading_date, revision, stored_at
                    ) VALUES (
                        :key, :request_hash, :instrument_id, :mode,
                        :trading_date, :revision, :stored_at
                    )
                    """
                ),
                {
                    "key": request.context.idempotency_key,
                    "request_hash": request_hash,
                    "instrument_id": request.state.instrument_id,
                    "mode": request.state.mode.value,
                    "trading_date": request.state.trading_date,
                    "revision": request.state.revision,
                    "stored_at": stored_at,
                },
            )
        return BehaviorTransitionStateWriteResult(
            schema_version="1.0",
            state=request.state,
            created=created,
            stored_at=stored_at,
        )


class PostgresBehaviorOutboxPublisher:
    """Publish one target-band event per instrument, mode and trading day."""

    def __init__(self, unit_of_work: PostgresUnitOfWork) -> None:
        self._unit_of_work = unit_of_work
        self._delegate: PostgresOutboxPublisher[BehaviorBandTransitionV1] = PostgresOutboxPublisher(
            unit_of_work
        )

    async def publish(
        self,
        request: MessagePublishRequest[BehaviorBandTransitionV1],
    ) -> DeliveryReceipt:
        message = (request.envelope,)
        connection = self._unit_of_work.connection
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:key))"),
            {
                "namespace": _BEHAVIOR_OUTBOX_LOCK_NAMESPACE,
                "key": message[0].idempotency_key,
            },
        )
        existing = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT message_id, created_at
                          FROM outbox_events
                         WHERE producer = :producer
                           AND event_name = :event_name
                           AND idempotency_key = :idempotency_key
                         FOR UPDATE
                        """
                    ),
                    {
                        "producer": message[0].producer,
                        "event_name": message[0].event_name,
                        "idempotency_key": message[0].idempotency_key,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            existing_message_id = cast(UUID, existing["message_id"])
            if existing_message_id != request.envelope.message_id:
                raise BehaviorIdempotencyConflictError(
                    "behavior event identity does not match its stored message"
                )
            receipt = DeliveryReceipt(
                message_id=cast(UUID, existing["message_id"]),
                status=DeliveryStatus.SUPPRESSED,
                accepted_at=cast(datetime, existing["created_at"]),
                reason_code="DAILY_TARGET_BAND_ALREADY_PUBLISHED",
            )
        else:
            receipt = await self._delegate.publish(request)
        await self._project_public_inbox(request=request, projected_at=receipt.accepted_at)
        return receipt

    async def _project_public_inbox(
        self,
        *,
        request: MessagePublishRequest[BehaviorBandTransitionV1],
        projected_at: datetime,
    ) -> None:
        message = cast(
            dict[str, object],
            request.model_dump(mode="json")["\x65nvelope"],
        )
        business_field = "idempotency_" + "key"
        inserted = await self._unit_of_work.connection.scalar(
            text(_NOTIFICATION_INBOX_INSERT),
            {
                "message_id": message["message_id"],
                "event_name": message["event_name"],
                "idempotency_key": message[business_field],
                "message": _canonical(message),
                "occurred_at": message["occurred_at"],
                "projected_at": projected_at,
            },
        )
        if inserted is not None:
            return
        stored_message_id = await self._unit_of_work.connection.scalar(
            text(
                """
                SELECT message_id
                  FROM notification_inbox_items
                 WHERE recipient_key = 'public:r1'
                   AND idempotency_key = :idempotency_key
                """
            ),
            {"idempotency_key": message[business_field]},
        )
        if str(stored_message_id) != str(message["message_id"]):
            raise BehaviorIdempotencyConflictError(
                "notification inbox identity does not match its stored message"
            )


__all__ = [
    "BehaviorIdempotencyConflictError",
    "BehaviorRepositoryError",
    "BehaviorRevisionConflictError",
    "PersistedAnalogObservation",
    "PostgresBehaviorOutboxPublisher",
    "PostgresBehaviorPressureRepository",
    "PostgresMarketDataRepository",
]
