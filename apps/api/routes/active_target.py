"""Thin, read-only active-target HTTP delivery."""

from collections.abc import Awaitable, Callable
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import JsonValue
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.http import request_ids
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.contracts.active_targets import ActiveTargetState
from stock_assistant.contracts.api import ApiResponse, ProblemDetails, ResponseMeta
from stock_assistant.contracts.common import OperationContext
from stock_assistant.modules.portfolio.active_target import FixedActiveTargetService
from stock_assistant.platform.db.portfolio import PostgresPortfolioRepository

ActiveTargetLoader = Callable[[], Awaitable[ActiveTargetState]]
type JsonObject = dict[str, JsonValue]


def _inline_problem_details_schema() -> JsonObject:
    schema = cast(JsonObject, ProblemDetails.model_json_schema())
    definitions = cast(JsonObject, schema.pop("$defs", {}))

    def inline(value: JsonValue, resolving: frozenset[str] = frozenset()) -> JsonValue:
        if isinstance(value, list):
            return [inline(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value

        reference = value.get("$ref")
        if isinstance(reference, str):
            prefix = "#/$defs/"
            name = reference.removeprefix(prefix)
            if not reference.startswith(prefix) or name not in definitions:
                raise ValueError(f"unsupported JSON Schema reference: {reference}")
            if name in resolving:
                raise ValueError(f"recursive JSON Schema reference: {reference}")
            resolved = inline(definitions[name], resolving | {name})
            if not isinstance(resolved, dict):
                raise ValueError(f"JSON Schema reference is not an object: {reference}")
            siblings = {
                key: inline(item, resolving) for key, item in value.items() if key != "$ref"
            }
            return {**resolved, **siblings}

        return {key: inline(item, resolving) for key, item in value.items()}

    inlined = inline(schema)
    if not isinstance(inlined, dict):
        raise ValueError("ProblemDetails JSON Schema must be an object")
    return inlined


PROBLEM_DETAILS_SCHEMA = _inline_problem_details_schema()


def _database_engine(request: Request) -> AsyncEngine:
    engine = getattr(request.app.state, "db_engine", None)
    if not isinstance(engine, AsyncEngine):
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Database state is temporarily unavailable.",
            retryable=True,
        )
    return engine


async def load_active_target(request: Request) -> ActiveTargetState:
    """Load the fixed R1 target from the shared PostgreSQL composition root."""

    as_of = utc_now()
    request_id, correlation_id = request_ids(request)
    try:
        return await FixedActiveTargetService(
            PostgresPortfolioRepository(_database_engine(request))
        ).load(
            OperationContext(
                schema_version="1.0",
                request_id=request_id,
                correlation_id=correlation_id,
                as_of=as_of,
                requested_at=as_of,
            )
        )
    except (SQLAlchemyError, OSError) as exc:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Active-target state is temporarily unavailable.",
            retryable=True,
        ) from exc


def _response(request: Request, state: ActiveTargetState) -> ApiResponse[ActiveTargetState]:
    request_id, correlation_id = request_ids(request)
    return ApiResponse(
        data=state,
        meta=ResponseMeta(
            request_id=request_id,
            correlation_id=correlation_id,
            as_of=state.as_of,
        ),
    )


router = APIRouter(tags=["active-target"])


@router.get(
    "/active-target",
    response_model=ApiResponse[ActiveTargetState],
    responses={
        503: {
            "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
        }
    },
)
async def get_active_target(
    request: Request,
    state: Annotated[ActiveTargetState, Depends(load_active_target)],
) -> ApiResponse[ActiveTargetState]:
    return _response(request, state)


def create_active_target_router(load_active_target: ActiveTargetLoader) -> APIRouter:
    """Build a compatibility router with its dependency supplied directly."""

    compatibility_router = APIRouter(tags=["active-target"])

    @compatibility_router.get(
        "/active-target",
        response_model=ApiResponse[ActiveTargetState],
        responses={
            503: {
                "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
            }
        },
    )
    async def get_compatible_active_target(request: Request) -> ApiResponse[ActiveTargetState]:
        state = await load_active_target()
        return _response(request, state)

    return compatibility_router


__all__ = [
    "ActiveTargetLoader",
    "_database_engine",
    "create_active_target_router",
    "get_active_target",
    "load_active_target",
    "router",
]
