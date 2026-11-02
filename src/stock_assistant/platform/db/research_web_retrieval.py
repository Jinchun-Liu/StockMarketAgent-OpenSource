"""PostgreSQL persistence for minimal model-assisted retrieval receipts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.contracts.discovery import (
    ResearchWebRetrievalReceipt,
    ResearchWebRetrievalReceiptWriteResult,
)


class PostgresResearchWebRetrievalReceiptRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(
        self, receipt: ResearchWebRetrievalReceipt
    ) -> ResearchWebRetrievalReceiptWriteResult:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO research_web_retrieval_receipts (
                        receipt_id, session_id, query_sha256, candidate_url_sha256,
                        selected_source_id, selected_url_sha256, status, credits_used,
                        rule_version, created_at, expires_at
                    )
                    VALUES (
                        :receipt_id, :session_id, :query_sha256,
                        CAST(:candidate_url_sha256 AS char(64)[]), :selected_source_id,
                        :selected_url_sha256, :status, :credits_used, :rule_version,
                        :created_at, :expires_at
                    )
                    ON CONFLICT (receipt_id) DO NOTHING
                    """
                ),
                {
                    "receipt_id": receipt.receipt_id,
                    "session_id": receipt.session_id,
                    "query_sha256": receipt.query_sha256,
                    "candidate_url_sha256": receipt.candidate_url_sha256,
                    "selected_source_id": receipt.selected_source_id,
                    "selected_url_sha256": receipt.selected_url_sha256,
                    "status": receipt.status.value,
                    "credits_used": receipt.credits_used,
                    "rule_version": receipt.rule_version,
                    "created_at": receipt.created_at,
                    "expires_at": receipt.expires_at,
                },
            )
        stored = bool(result.rowcount)
        return ResearchWebRetrievalReceiptWriteResult(
            schema_version="1.0",
            receipt_id=receipt.receipt_id,
            stored=stored,
            idempotent_replay=not stored,
            processed_at=receipt.created_at,
        )

    async def purge_expired(self, *, now: datetime) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE research_web_retrieval_receipts
                       SET candidate_url_sha256 = ARRAY[]::char(64)[],
                           selected_source_id = NULL,
                           selected_url_sha256 = NULL,
                           purged_at = :now
                     WHERE expires_at <= :now
                       AND purged_at IS NULL
                    """
                ),
                {"now": now},
            )
            return int(result.rowcount or 0)


__all__ = ["PostgresResearchWebRetrievalReceiptRepository"]
