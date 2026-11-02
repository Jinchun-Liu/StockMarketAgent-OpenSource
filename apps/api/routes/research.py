"""R1 research-product HTTP reads for the fixed GigaDevice instrument."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AwareDatetime
from sqlalchemy.exc import SQLAlchemyError

from apps.api.http import request_ids
from apps.api.routes.active_target import _database_engine
from apps.api.routes.premarket import load_latest_briefing
from apps.runtime.research import RuntimeResearchEnricher, briefing_evidence_ids
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config.settings import get_settings
from stock_assistant.contracts.api import ApiResponse, ResponseMeta
from stock_assistant.contracts.behavior import (
    BehaviorPressurePage,
    BehaviorPressureQuery,
    PressureMode,
)
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)
from stock_assistant.contracts.r1_http import PremarketBriefingHttpData
from stock_assistant.platform.db.behavior import (
    BehaviorRepositoryError,
    PostgresBehaviorPressureRepository,
)

_CURSOR_VERSION = 1
_CURSOR_TTL = timedelta(minutes=15)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as error:
        raise ApplicationError(
            ErrorCode.VALIDATION_ERROR,
            "The page cursor is invalid.",
        ) from error


def _cursor_signing_key(request: Request) -> bytes:
    value: object = getattr(request.app.state, "cursor_signing_key", None)
    if not isinstance(value, bytes) or len(value) < 32:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Page continuation is temporarily unavailable.",
            retryable=True,
        )
    return value


def _sign_cursor(
    *,
    inner_cursor: str,
    mode: PressureMode,
    as_of: datetime,
    signing_key: bytes,
    issued_at: datetime,
) -> str:
    body = json.dumps(
        {
            "as_of": as_of.isoformat(),
            "expires_at": int((issued_at + _CURSOR_TTL).timestamp()),
            "inner": inner_cursor,
            "mode": mode.value,
            "version": _CURSOR_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(signing_key, body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(signature)}"


def _decode_cursor(
    value: str,
    *,
    expected_mode: PressureMode,
    expected_as_of: datetime | None,
    signing_key: bytes,
    now: datetime,
) -> tuple[str, datetime]:
    try:
        body_part, signature_part = value.split(".", maxsplit=1)
        body = _b64decode(body_part)
        signature = _b64decode(signature_part)
        expected_signature = hmac.new(signing_key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("version") != _CURSOR_VERSION:
            raise ValueError
        mode = PressureMode(payload["mode"])
        as_of = datetime.fromisoformat(payload["as_of"])
        expires_at = datetime.fromtimestamp(payload["expires_at"], tz=UTC)
        inner = payload["inner"]
        if not isinstance(inner, str) or not inner or as_of.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ApplicationError(
            ErrorCode.VALIDATION_ERROR,
            "The page cursor is invalid.",
        ) from error
    if mode is not expected_mode:
        raise ApplicationError(
            ErrorCode.VALIDATION_ERROR,
            "The page cursor belongs to a different research mode.",
        )
    if expected_as_of is not None and as_of != expected_as_of:
        raise ApplicationError(
            ErrorCode.VALIDATION_ERROR,
            "The page cursor belongs to a different data cutoff.",
        )
    if expires_at <= now:
        raise ApplicationError(
            ErrorCode.VALIDATION_ERROR,
            "The page cursor has expired.",
        )
    return inner, as_of


router = APIRouter(tags=["today", "behavior"])


@router.get(
    "/today",
    response_model=ApiResponse[PremarketBriefingViewV1_3],
)
async def get_today(
    request: Request,
    briefing: Annotated[PremarketBriefingHttpData, Depends(load_latest_briefing)],
) -> ApiResponse[PremarketBriefingViewV1_3]:
    if isinstance(briefing, PremarketBriefingViewV1_2) and not isinstance(
        briefing, PremarketBriefingViewV1_3
    ):
        settings = get_settings().model_copy(
            update={
                "external_llm_enabled": False,
                "external_llm_api_key": None,
                "deepseek_enabled": False,
                "deepseek_api_key": None,
            }
        )
        try:
            briefing = await RuntimeResearchEnricher(
                settings=settings,
                behavior_repository=PostgresBehaviorPressureRepository(_database_engine(request)),
            ).enrich(
                briefing=briefing,
                context=OperationContext(
                    schema_version="1.0",
                    as_of=briefing.as_of,
                    requested_at=utc_now(),
                ),
                evidence_ids=briefing_evidence_ids(briefing),
            )
        except (BehaviorRepositoryError, SQLAlchemyError, OSError) as error:
            raise ApplicationError(
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "The current research summary is temporarily unavailable.",
                retryable=True,
            ) from error
    if not isinstance(briefing, PremarketBriefingViewV1_3):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "The current research summary has not reached version 1.3 yet.",
            retryable=True,
        )
    request_id, correlation_id = request_ids(request)
    return ApiResponse(
        data=briefing,
        meta=ResponseMeta(
            request_id=request_id,
            correlation_id=correlation_id,
            as_of=briefing.as_of,
        ),
    )


@router.get(
    "/instruments/{instrument_id}/behavior-pressure-snapshots",
    response_model=ApiResponse[BehaviorPressurePage],
)
async def get_behavior_pressure_snapshots(
    request: Request,
    instrument_id: Annotated[Literal["XSHG:603986"], Path()],
    mode: Annotated[PressureMode, Query()],
    as_of: Annotated[AwareDatetime | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApiResponse[BehaviorPressurePage]:
    now = utc_now()
    signing_key = _cursor_signing_key(request)
    inner_cursor = None
    query_as_of = as_of
    if cursor is not None:
        inner_cursor, query_as_of = _decode_cursor(
            cursor,
            expected_mode=mode,
            expected_as_of=query_as_of,
            signing_key=signing_key,
            now=now,
        )
    query_as_of = query_as_of or now
    request_id, correlation_id = request_ids(request)
    context = OperationContext(
        schema_version="1.0",
        request_id=request_id,
        correlation_id=correlation_id,
        actor_id="public-research-reader",
        workspace_id="public-r1",
        as_of=query_as_of,
        requested_at=now,
        locale="zh-CN",
        timezone="Asia/Shanghai",
    )
    try:
        page = await PostgresBehaviorPressureRepository(
            _database_engine(request)
        ).query_assessments(
            BehaviorPressureQuery(
                schema_version="1.0",
                context=context,
                instrument_id=instrument_id,
                mode=mode,
                cursor=inner_cursor,
                limit=limit,
            )
        )
    except (BehaviorRepositoryError, SQLAlchemyError, OSError) as error:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Behavior research history is temporarily unavailable.",
            retryable=True,
        ) from error
    public_cursor = (
        _sign_cursor(
            inner_cursor=page.next_cursor,
            mode=mode,
            as_of=query_as_of,
            signing_key=signing_key,
            issued_at=now,
        )
        if page.next_cursor is not None
        else None
    )
    public_page = page.model_copy(update={"next_cursor": public_cursor})
    return ApiResponse(
        data=public_page,
        meta=ResponseMeta(
            request_id=request_id,
            correlation_id=correlation_id,
            as_of=query_as_of,
        ),
    )


__all__ = ["get_behavior_pressure_snapshots", "get_today", "router"]
