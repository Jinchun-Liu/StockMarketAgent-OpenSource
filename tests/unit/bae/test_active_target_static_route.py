from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from apps.api.http import RequestContextMiddleware, application_error_handler
from apps.api.routes.active_target import load_active_target, router
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.active_targets import ActiveTargetState, ActiveTargetView
from stock_assistant.contracts.api import ProblemDetails
from stock_assistant.contracts.common import InstrumentRef

FIXED_NOW = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def _client(state: ActiveTargetState | None = None) -> TestClient:
    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.include_router(router, prefix="/api/v1")
    if state is not None:

        async def overridden_loader() -> ActiveTargetState:
            return state

        app.dependency_overrides[load_active_target] = overridden_loader
    return TestClient(app)


def test_module_router_returns_overridden_found_state() -> None:
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

    response = _client(expected).get("/api/v1/active-target")

    assert response.status_code == 200
    assert response.json()["data"] == expected.model_dump(mode="json")


def test_module_router_preserves_explicit_uninitialized_state() -> None:
    expected = ActiveTargetState(
        schema_version="1.0",
        found=False,
        active_target=None,
        as_of=FIXED_NOW,
    )

    response = _client(expected).get("/api/v1/active-target")

    assert response.status_code == 200
    assert response.json()["data"] == expected.model_dump(mode="json")


def test_module_router_fails_closed_without_composed_dependency() -> None:
    response = _client().get("/api/v1/active-target")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert response.json()["retryable"] is True


def test_module_router_declares_problem_json_for_dependency_unavailable() -> None:
    app = FastAPI(lifespan=_lifespan)
    app.include_router(router, prefix="/api/v1")

    unavailable_response = app.openapi()["paths"]["/api/v1/active-target"]["get"]["responses"][
        "503"
    ]

    assert set(unavailable_response["content"]) == {"application/problem+json"}
    problem_schema = unavailable_response["content"]["application/problem+json"]["schema"]
    assert problem_schema["title"] == ProblemDetails.model_json_schema()["title"]
    assert problem_schema["properties"]["errors"]["items"]["title"] == "FieldViolation"
    assert "$defs" not in problem_schema
    assert "$ref" not in str(problem_schema)


def test_module_router_is_static_and_read_only() -> None:
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert [(route.path, route.methods) for route in routes] == [("/active-target", {"GET"})]
