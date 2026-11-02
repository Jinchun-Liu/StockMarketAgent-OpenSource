"""PostgreSQL implementation of the frozen PortfolioRepositoryPort 1.1 boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Final, cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.contracts.persistence import (
    ActiveTargetLookupRequest,
    ActiveTargetLookupResult,
    ActiveTargetWriteRequest,
    ActiveTargetWriteResult,
    PortfolioLookupRequest,
    PortfolioLookupResult,
    PortfolioSnapshotWriteRequest,
    PortfolioSnapshotWriteResult,
)
from stock_assistant.contracts.portfolio import ActiveTargetRevisionRef, Watchlist

_ADVISORY_LOCK_NAMESPACE: Final = 17_042_731
_IDEMPOTENCY_LOCK_NAMESPACE: Final = 17_042_732


class PortfolioRepositoryError(RuntimeError):
    """Base class for stable repository failures without database details."""


class PortfolioRevisionConflictError(PortfolioRepositoryError):
    """The requested optimistic revision does not match stored state."""


class PortfolioIdempotencyConflictError(PortfolioRepositoryError):
    """An idempotency key was reused for a different logical write."""


def _json_payload(value: Watchlist | ActiveTargetRevisionRef) -> dict[str, object]:
    return cast(dict[str, object], value.model_dump(mode="json"))


def _request_digest(request: ActiveTargetWriteRequest) -> str:
    canonical = json.dumps(
        {
            "watchlist": _json_payload(request.watchlist),
            "active_target": _json_payload(request.active_target),
            "expected_revision": request.expected_revision,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _watchlist(row: RowMapping) -> Watchlist:
    return Watchlist.model_validate(row["watchlist_payload"])


def _active_target(row: RowMapping) -> ActiveTargetRevisionRef:
    return ActiveTargetRevisionRef.model_validate(row["active_target_payload"])


class PostgresPortfolioRepository:
    """Persist active-target revisions using bounded, point-in-time-safe queries.

    Portfolio snapshot methods remain deliberately unavailable: this adapter is the
    registered fixed Active Target provider, not a claim that the whole portfolio
    persistence surface is implemented.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_latest(self, request: PortfolioLookupRequest) -> PortfolioLookupResult:
        raise NotImplementedError(
            "portfolio snapshot persistence is not implemented by this adapter"
        )

    async def save_snapshot(
        self, request: PortfolioSnapshotWriteRequest
    ) -> PortfolioSnapshotWriteResult:
        raise NotImplementedError(
            "portfolio snapshot persistence is not implemented by this adapter"
        )

    async def get_active_target(
        self, request: ActiveTargetLookupRequest
    ) -> ActiveTargetLookupResult:
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT watchlist_payload, active_target_payload
                          FROM active_target_revisions
                         WHERE watchlist_id = :watchlist_id
                           AND updated_at <= :as_of
                           AND activated_at <= :as_of
                           AND recorded_at <= :as_of
                         ORDER BY revision DESC
                         LIMIT 1
                        """
                        ),
                        {
                            "watchlist_id": request.watchlist_id,
                            "as_of": request.context.as_of,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return ActiveTargetLookupResult(
                schema_version="1.0",
                found=False,
                as_of=request.context.as_of,
            )
        return ActiveTargetLookupResult(
            schema_version="1.0",
            found=True,
            watchlist=_watchlist(row),
            active_target=_active_target(row),
            as_of=request.context.as_of,
        )

    async def save_active_target(
        self, request: ActiveTargetWriteRequest
    ) -> ActiveTargetWriteResult:
        request_sha256 = _request_digest(request)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:idempotency_key))"),
                    {
                        "namespace": _IDEMPOTENCY_LOCK_NAMESPACE,
                        "idempotency_key": request.context.idempotency_key,
                    },
                )
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:aggregate_id))"),
                    {
                        "namespace": _ADVISORY_LOCK_NAMESPACE,
                        "aggregate_id": str(request.watchlist.watchlist_id),
                    },
                )
                replay = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT receipt.request_sha256,
                                   receipt.created,
                                   receipt.changed,
                                   receipt.stored_at,
                                   revision.watchlist_payload,
                                   revision.active_target_payload
                              FROM active_target_write_receipts AS receipt
                              JOIN active_target_revisions AS revision
                                ON revision.watchlist_id = receipt.watchlist_id
                               AND revision.revision = receipt.revision
                             WHERE receipt.idempotency_key = :idempotency_key
                            """
                            ),
                            {"idempotency_key": request.context.idempotency_key},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if replay is not None:
                    if replay["request_sha256"] != request_sha256:
                        raise PortfolioIdempotencyConflictError(
                            "idempotency key was reused for a different active-target write"
                        )
                    return ActiveTargetWriteResult(
                        schema_version="1.0",
                        watchlist=_watchlist(replay),
                        active_target=_active_target(replay),
                        created=bool(replay["created"]),
                        changed=bool(replay["changed"]),
                        stored_at=cast(datetime, replay["stored_at"]),
                        idempotent_replay=True,
                    )

                latest = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT revision, watchlist_payload, active_target_payload
                              FROM active_target_revisions
                             WHERE watchlist_id = :watchlist_id
                             ORDER BY revision DESC
                             LIMIT 1
                            """
                            ),
                            {"watchlist_id": request.watchlist.watchlist_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if latest is None:
                    created = True
                    if request.expected_revision is not None:
                        raise PortfolioRevisionConflictError(
                            "active-target aggregate does not exist at expected revision"
                        )
                    changed = True
                else:
                    created = False
                    latest_revision = int(latest["revision"])
                    if request.expected_revision != latest_revision:
                        raise PortfolioRevisionConflictError(
                            "active-target expected revision does not match stored revision"
                        )
                    if request.watchlist.revision == latest_revision:
                        same_value = latest["watchlist_payload"] == _json_payload(
                            request.watchlist
                        ) and latest["active_target_payload"] == _json_payload(
                            request.active_target
                        )
                        if not same_value:
                            raise PortfolioRevisionConflictError(
                                "an existing active-target revision is immutable"
                            )
                        changed = False
                    else:
                        changed = True

                if changed:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO active_target_revisions (
                                watchlist_id,
                                revision,
                                watchlist_payload,
                                active_target_payload,
                                instrument_id,
                                activated_at,
                                updated_at
                            )
                            VALUES (
                                :watchlist_id,
                                :revision,
                                CAST(:watchlist_payload AS jsonb),
                                CAST(:active_target_payload AS jsonb),
                                :instrument_id,
                                :activated_at,
                                :updated_at
                            )
                            """
                        ),
                        {
                            "watchlist_id": request.watchlist.watchlist_id,
                            "revision": request.watchlist.revision,
                            "watchlist_payload": json.dumps(
                                _json_payload(request.watchlist), separators=(",", ":")
                            ),
                            "active_target_payload": json.dumps(
                                _json_payload(request.active_target), separators=(",", ":")
                            ),
                            "instrument_id": request.active_target.instrument.instrument_id,
                            "activated_at": request.active_target.activated_at,
                            "updated_at": request.watchlist.updated_at,
                        },
                    )

                receipt = (
                    (
                        await connection.execute(
                            text(
                                """
                            INSERT INTO active_target_write_receipts (
                                idempotency_key,
                                request_sha256,
                                watchlist_id,
                                revision,
                                created,
                                changed
                            )
                            VALUES (
                                :idempotency_key,
                                :request_sha256,
                                :watchlist_id,
                                :revision,
                                :created,
                                :changed
                            )
                            RETURNING stored_at
                            """
                            ),
                            {
                                "idempotency_key": request.context.idempotency_key,
                                "request_sha256": request_sha256,
                                "watchlist_id": request.watchlist.watchlist_id,
                                "revision": request.watchlist.revision,
                                "created": created,
                                "changed": changed,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
        except IntegrityError as exc:
            raise PortfolioRevisionConflictError(
                "active-target write conflicted with stored state"
            ) from exc

        return ActiveTargetWriteResult(
            schema_version="1.0",
            watchlist=request.watchlist,
            active_target=request.active_target,
            created=created,
            changed=changed,
            stored_at=cast(datetime, receipt["stored_at"]),
        )
