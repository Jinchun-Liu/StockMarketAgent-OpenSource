from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from apps.api.http import RequestContextMiddleware, application_error_handler
from apps.api.routes.active_target import create_active_target_router
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.active_targets import ActiveTargetState, ActiveTargetView
from stock_assistant.contracts.common import InstrumentRef

FIXED_NOW = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)


def _client(
    load_active_target: Callable[[], Awaitable[ActiveTargetState]],
) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.include_router(
        create_active_target_router(load_active_target),
        prefix="/api/v1",
    )
    return TestClient(app)


@pytest.mark.contract
def test_get_active_target_returns_the_injected_frozen_contract() -> None:
    expected = ActiveTargetState(
        schema_version="1.0",
        found=True,
        active_target=ActiveTargetView(
            instrument=InstrumentRef(
                instrument_id="XSHG:603986",
                symbol="603986.SH",
                exchange="XSHG",
                market="CN",
            ),
            revision=1,
            activated_at=FIXED_NOW,
        ),
        as_of=FIXED_NOW,
    )

    async def load_active_target() -> ActiveTargetState:
        return expected

    response = _client(load_active_target).get(
        "/api/v1/active-target",
        headers={
            "X-Request-ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "X-Correlation-ID": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == expected.model_dump(mode="json")
    assert response.json()["meta"]["request_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert response.json()["meta"]["correlation_id"] == ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    assert response.json()["meta"]["as_of"] == "2026-07-28T01:05:00Z"


@pytest.mark.contract
def test_get_active_target_preserves_explicit_uninitialized_state() -> None:
    expected = ActiveTargetState(
        schema_version="1.0",
        found=False,
        active_target=None,
        as_of=FIXED_NOW,
    )

    async def load_active_target() -> ActiveTargetState:
        return expected

    response = _client(load_active_target).get("/api/v1/active-target")

    assert response.status_code == 200
    assert response.json()["data"] == expected.model_dump(mode="json")


@pytest.mark.contract
def test_get_active_target_fails_closed_when_dependency_is_unavailable() -> None:
    async def unavailable() -> ActiveTargetState:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Active-target state is temporarily unavailable.",
            retryable=True,
        )

    response = _client(unavailable).get("/api/v1/active-target")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["retryable"] is True
    UUID(response.json()["request_id"])
    UUID(response.json()["correlation_id"])


@pytest.mark.contract
def test_active_target_router_exposes_no_mutation_route() -> None:
    async def load_active_target() -> ActiveTargetState:
        return ActiveTargetState(
            schema_version="1.0",
            found=False,
            active_target=None,
            as_of=FIXED_NOW,
        )

    methods = {
        method
        for route in create_active_target_router(load_active_target).routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
    }

    assert methods == {"GET"}
