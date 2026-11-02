from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from apps.api.http import RequestContextMiddleware, application_error_handler
from apps.api.routes import research_sessions
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.research import (
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
    ResearchSessionStatus,
)

SESSION_ID = UUID("20000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class _RejectingRepository:
    async def cancel(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult:
        return self._result(
            request,
            status=ResearchSessionStatus.SUCCEEDED,
            reason_code="RESEARCH_SESSION_NOT_CANCELLABLE",
        )

    async def retry(self, request: ResearchSessionMutationRequest) -> ResearchSessionMutationResult:
        return self._result(
            request,
            status=ResearchSessionStatus.FAILED,
            reason_code="RESEARCH_SESSION_NOT_RETRYABLE",
        )

    async def delete(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult:
        return self._result(
            request,
            status=ResearchSessionStatus.RUNNING,
            reason_code="RESEARCH_SESSION_MUST_BE_CANCELLED",
        )

    @staticmethod
    def _result(
        request: ResearchSessionMutationRequest,
        *,
        status: ResearchSessionStatus,
        reason_code: str,
    ) -> ResearchSessionMutationResult:
        return ResearchSessionMutationResult(
            schema_version="1.0",
            session_id=request.session_id,
            status=status,
            processed_at=NOW,
            reason_code=reason_code,
        )


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.include_router(research_sessions.router, prefix="/api/v1")
    repository = _RejectingRepository()
    monkeypatch.setattr(research_sessions, "_require_synthetic_r2", lambda **_kwargs: None)
    monkeypatch.setattr(research_sessions, "_repository", lambda _request: repository)
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "suffix", "expected_status", "expected_code", "expected_detail"),
    (
        (
            "post",
            "cancel",
            409,
            "CONFLICT",
            "RESEARCH_SESSION_NOT_CANCELLABLE",
        ),
        (
            "post",
            "retry",
            412,
            "PRECONDITION_FAILED",
            "RESEARCH_SESSION_NOT_RETRYABLE",
        ),
        (
            "delete",
            "",
            412,
            "PRECONDITION_FAILED",
            "RESEARCH_SESSION_MUST_BE_CANCELLED",
        ),
    ),
)
def test_rejected_research_mutations_return_problem_details(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    suffix: str,
    expected_status: int,
    expected_code: str,
    expected_detail: str,
) -> None:
    path = f"/api/v1/research-sessions/{SESSION_ID}"
    if suffix:
        path = f"{path}/{suffix}"
    response = _client(monkeypatch).request(
        method,
        path,
        headers={"Idempotency-Key": f"research-route-{suffix or 'delete'}-0001"},
    )

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == expected_code
    assert response.json()["detail"] == expected_detail


def test_research_mutation_routes_publish_problem_details_contracts() -> None:
    app = FastAPI(lifespan=_lifespan)
    app.include_router(research_sessions.router, prefix="/api/v1")
    paths = app.openapi()["paths"]

    expected = (
        ("/api/v1/research-sessions/{session_id}/cancel", "post", "409"),
        ("/api/v1/research-sessions/{session_id}/retry", "post", "412"),
        ("/api/v1/research-sessions/{session_id}", "delete", "412"),
    )
    for path, method, status_code in expected:
        content = paths[path][method]["responses"][status_code]["content"]
        assert set(content) == {"application/problem+json"}
        assert content["application/problem+json"]["schema"]["title"] == "ProblemDetails"


def test_retry_attempt_exhaustion_is_a_precondition_failure() -> None:
    assert (
        research_sessions._MUTATION_REJECTION_CODES["RESEARCH_RETRY_ATTEMPTS_EXHAUSTED"]
        is research_sessions.ErrorCode.PRECONDITION_FAILED
    )
