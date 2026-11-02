from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.research import (
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
    ResearchSessionStatus,
)
from stock_assistant.platform.db.research import PostgresResearchRepository

SESSION_ID = UUID("10000000-0000-4000-8000-000000000088")
JOB_ID = UUID("20000000-0000-4000-8000-000000000088")
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


class _CurrentResult:
    def mappings(self) -> _CurrentResult:
        return self

    def one_or_none(self) -> dict[str, object]:
        return {
            "status": "failed",
            "retryable": True,
            "job_id": JOB_ID,
            "attempt": 3,
        }


class _WriteResult:
    pass


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: object, _parameters: object = None) -> object:
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT status, retryable, job_id" in sql:
            return _CurrentResult()
        return _WriteResult()


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[_Connection]:
        yield self._connection


@pytest.mark.asyncio
async def test_repository_rejects_a_fourth_research_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    repository = PostgresResearchRepository(cast(AsyncEngine, _Engine(connection)))
    monkeypatch.setattr(repository, "_mutation_replay", AsyncMock(return_value=None))

    async def record_mutation(
        _connection: object,
        _request: ResearchSessionMutationRequest,
        *,
        operation: str,
        result: ResearchSessionMutationResult,
    ) -> ResearchSessionMutationResult:
        assert operation == "retry"
        return result

    monkeypatch.setattr(repository, "_record_mutation", record_mutation)
    request = ResearchSessionMutationRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW,
            idempotency_key="research-unit-retry-exhausted-0001",
        ),
        session_id=SESSION_ID,
    )

    result = await repository.retry(request)

    assert result.status is ResearchSessionStatus.FAILED
    assert result.job_id == JOB_ID
    assert result.reason_code == "RESEARCH_RETRY_ATTEMPTS_EXHAUSTED"
    assert any("UPDATE research_sessions" in statement for statement in connection.statements)
    assert any(
        "INSERT INTO research_session_transitions" in statement
        for statement in connection.statements
    )
