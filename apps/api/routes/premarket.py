"""Fixture-backed R1 briefing and evidence HTTP delivery."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AwareDatetime
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse, Response

from apps.api.http import request_ids
from apps.api.routes.active_target import PROBLEM_DETAILS_SCHEMA, _database_engine
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.contracts.api import ApiResponse, ProblemDetails, ResponseMeta
from stock_assistant.contracts.event_views import (
    EventEvidenceRevisionSelector,
    EventEvidenceViewV1_2,
)
from stock_assistant.contracts.ingestion import SourceReleaseTier, SourceRuntimeMode
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingView,
    PremarketBriefingViewV1_1,
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)
from stock_assistant.contracts.r1_http import (
    R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3,
    R1_SOURCE_PROVENANCE_MEDIA_TYPE,
    R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2,
    EventEvidenceHttpData,
    PremarketBriefingHttpData,
    project_event_evidence_v1_0,
    project_event_evidence_v1_1,
    project_premarket_briefing_v1_0,
    project_premarket_briefing_v1_1,
    project_premarket_briefing_v1_2,
)
from stock_assistant.platform.db.r1_premarket import PostgresR1PremarketRepository


def _r1_read_scope() -> tuple[SourceRuntimeMode, SourceReleaseTier]:
    settings = get_settings()
    runtime_mode = (
        SourceRuntimeMode.AUTHORIZED
        if settings.app_env == "production"
        else settings.r1_runtime_mode
    )
    release_tier = (
        SourceReleaseTier.AUTHORIZED
        if runtime_mode is SourceRuntimeMode.AUTHORIZED
        else SourceReleaseTier.DEV_ONLY
    )
    return runtime_mode, release_tier


async def load_latest_briefing(
    request: Request,
) -> PremarketBriefingHttpData:
    runtime_mode, release_tier = _r1_read_scope()
    try:
        briefing = await PostgresR1PremarketRepository(
            _database_engine(request)
        ).get_latest_briefing(
            as_of=utc_now(),
            expected_runtime_mode=runtime_mode,
            expected_release_tier=release_tier,
        )
    except (SQLAlchemyError, OSError) as exc:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Premarket briefing state is temporarily unavailable.",
            retryable=True,
        ) from exc
    if briefing is None:
        raise ApplicationError(
            ErrorCode.NOT_FOUND,
            "No premarket briefing is available for the configured source mode.",
        )
    return briefing


async def load_event_evidence(
    request: Request,
    event_id: Annotated[UUID, Path()],
    as_of: Annotated[AwareDatetime, Query()],
    revision: Annotated[int | None, Query(ge=1)] = None,
    timeline_before_revision: Annotated[int | None, Query(ge=1)] = None,
    timeline_limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> EventEvidenceHttpData:
    runtime_mode, release_tier = _r1_read_scope()
    selector = EventEvidenceRevisionSelector(
        schema_version="1.0",
        as_of=as_of,
        revision=revision,
        timeline_before_revision=timeline_before_revision,
        timeline_limit=timeline_limit,
    )
    try:
        event = await PostgresR1PremarketRepository(_database_engine(request)).get_event_evidence(
            event_id=event_id,
            selector=selector,
            expected_runtime_mode=runtime_mode,
            expected_release_tier=release_tier,
        )
    except (SQLAlchemyError, OSError) as exc:
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Event evidence state is temporarily unavailable.",
            retryable=True,
        ) from exc
    if event is None:
        raise ApplicationError(
            ErrorCode.NOT_FOUND,
            "No visible evidence revision exists for the requested event and cutoff.",
        )
    return event


router = APIRouter(tags=["premarket"])


class _SourceProvenanceJsonResponse(JSONResponse):
    media_type = R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2


def _acceptable_provenance_versions(request: Request) -> tuple[str, ...]:
    header = request.headers.get("accept", "").strip()
    if not header:
        return ("1.0",)
    supported = {
        R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3: ("1.3", 4),
        R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2: ("1.2", 3),
        R1_SOURCE_PROVENANCE_MEDIA_TYPE: ("1.1", 2),
        "application/json": ("1.0", 1),
    }
    accepted_ranges: list[tuple[str, str, float]] = []
    for media_range in header.split(","):
        parts = [part.strip() for part in media_range.split(";")]
        media_type = parts[0].lower()
        type_name, separator, subtype = media_type.partition("/")
        if not separator or not type_name or not subtype:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
                if not 0.0 <= quality <= 1.0:
                    quality = 0.0
                break
        accepted_ranges.append((type_name, subtype, quality))

    candidates: list[tuple[float, int, str]] = []
    quality_by_version: dict[str, float] = {}
    for media_type, (version, preference) in supported.items():
        type_name, subtype = media_type.split("/", maxsplit=1)
        matches: list[tuple[int, float]] = []
        for accepted_type, accepted_subtype, quality in accepted_ranges:
            if accepted_type not in {"*", type_name}:
                continue
            if accepted_subtype not in {"*", subtype}:
                continue
            specificity = int(accepted_type != "*") + int(accepted_subtype != "*")
            matches.append((specificity, quality))
        if not matches:
            continue
        highest_specificity = max(specificity for specificity, _quality in matches)
        quality = max(
            quality for specificity, quality in matches if specificity == highest_specificity
        )
        if quality > 0:
            candidates.append((quality, preference, version))
            quality_by_version[version] = quality
    has_explicit_supported_range = any(
        f"{type_name}/{subtype}" in supported for type_name, subtype, _quality in accepted_ranges
    )
    if not has_explicit_supported_range and quality_by_version.get("1.0", 0.0) > 0:
        return ("1.0",)
    return tuple(version for _quality, _preference, version in sorted(candidates, reverse=True))


def _requested_provenance_version(request: Request) -> str | None:
    versions = _acceptable_provenance_versions(request)
    return versions[0] if versions else None


def _legacy_response_schema(data_schema: str, title: str) -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {
            "data": {"$ref": f"#/components/schemas/{data_schema}"},
            "meta": {"$ref": "#/components/schemas/ResponseMeta"},
        },
        "required": ["data", "meta"],
        "title": title,
        "type": "object",
    }


_VARY_ACCEPT_RESPONSE_HEADER = {
    "Vary": {
        "description": "The selected representation depends on the Accept request header.",
        "schema": {"type": "string", "const": "Accept"},
    }
}


def _source_provenance_unavailable_response(request: Request) -> Response:
    request_id, correlation_id = request_ids(request)
    problem = ProblemDetails(
        title="Precondition Failed",
        status=412,
        detail="R1_SOURCE_PROVENANCE_UNAVAILABLE",
        instance=request.url.path,
        code=ErrorCode.PRECONDITION_FAILED,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
        headers={"Vary": "Accept"},
    )


def _not_acceptable_response(request: Request) -> Response:
    request_id, correlation_id = request_ids(request)
    problem = ProblemDetails(
        title="Not Acceptable",
        status=406,
        detail="R1_REPRESENTATION_NOT_ACCEPTABLE",
        instance=request.url.path,
        code=ErrorCode.PRECONDITION_FAILED,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
        headers={"Vary": "Accept"},
    )


def _response(
    *,
    request: Request,
    data: PremarketBriefingHttpData | EventEvidenceHttpData,
    as_of: AwareDatetime,
) -> Response:
    request_id, correlation_id = request_ids(request)
    acceptable_versions = _acceptable_provenance_versions(request)
    if not acceptable_versions:
        return _not_acceptable_response(request)

    selected: PremarketBriefingHttpData | EventEvidenceHttpData | None = None
    selected_version: str | None = None
    for provenance_version in acceptable_versions:
        if data.schema_version == provenance_version:
            selected = data
        elif provenance_version == "1.2" and isinstance(data, PremarketBriefingViewV1_3):
            selected = project_premarket_briefing_v1_2(data)
        elif provenance_version == "1.1" and isinstance(
            data, (PremarketBriefingViewV1_2, PremarketBriefingViewV1_3)
        ):
            selected = project_premarket_briefing_v1_1(data)
        elif provenance_version == "1.1" and isinstance(data, EventEvidenceViewV1_2):
            selected = project_event_evidence_v1_1(data)
        elif provenance_version == "1.0":
            selected = (
                project_premarket_briefing_v1_0(data)
                if isinstance(
                    data,
                    (
                        PremarketBriefingView,
                        PremarketBriefingViewV1_1,
                        PremarketBriefingViewV1_2,
                        PremarketBriefingViewV1_3,
                    ),
                )
                else project_event_evidence_v1_0(data)
            )
        if selected is not None:
            selected_version = provenance_version
            break

    if selected is None or selected_version is None:
        return _source_provenance_unavailable_response(request)
    if selected_version == "1.3":
        media_type = R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3
    elif selected_version == "1.2":
        media_type = R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2
    elif selected_version == "1.1":
        media_type = R1_SOURCE_PROVENANCE_MEDIA_TYPE
    else:
        media_type = "application/json"
    envelope = ApiResponse(
        data=selected,
        meta=ResponseMeta(
            request_id=request_id,
            correlation_id=correlation_id,
            as_of=as_of,
        ),
    )
    return Response(
        content=envelope.model_dump_json(),
        media_type=media_type,
        headers={"Vary": "Accept"},
    )


@router.get(
    "/briefings/premarket/latest",
    response_model=ApiResponse[PremarketBriefingHttpData],
    response_class=_SourceProvenanceJsonResponse,
    responses={
        200: {
            "model": ApiResponse[PremarketBriefingViewV1_2],
            "headers": _VARY_ACCEPT_RESPONSE_HEADER,
            "content": {
                "application/json": {
                    "schema": _legacy_response_schema(
                        "PremarketBriefingView",
                        "ApiResponse[PremarketBriefingView]",
                    )
                },
                R1_SOURCE_PROVENANCE_MEDIA_TYPE: {
                    "schema": _legacy_response_schema(
                        "PremarketBriefingViewV1_1",
                        "ApiResponse[PremarketBriefingViewV1_1]",
                    )
                },
                R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3: {
                    "schema": _legacy_response_schema(
                        "PremarketBriefingViewV1_3",
                        "ApiResponse[PremarketBriefingViewV1_3]",
                    )
                },
            },
        },
        412: {
            "headers": _VARY_ACCEPT_RESPONSE_HEADER,
            "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
        },
        406: {
            "headers": _VARY_ACCEPT_RESPONSE_HEADER,
            "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
        },
        404: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
        503: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    },
)
async def get_latest_briefing(
    request: Request,
    briefing: Annotated[
        PremarketBriefingHttpData,
        Depends(load_latest_briefing),
    ],
) -> Response:
    return _response(
        request=request,
        data=briefing,
        as_of=briefing.as_of,
    )


@router.get(
    "/events/{event_id}/evidence",
    response_model=ApiResponse[EventEvidenceHttpData],
    response_class=_SourceProvenanceJsonResponse,
    responses={
        200: {
            "model": ApiResponse[EventEvidenceViewV1_2],
            "headers": _VARY_ACCEPT_RESPONSE_HEADER,
            "content": {
                "application/json": {
                    "schema": _legacy_response_schema(
                        "EventEvidenceView",
                        "ApiResponse[EventEvidenceView]",
                    )
                },
                R1_SOURCE_PROVENANCE_MEDIA_TYPE: {
                    "schema": _legacy_response_schema(
                        "EventEvidenceViewV1_1",
                        "ApiResponse[EventEvidenceViewV1_1]",
                    )
                },
            },
        },
        412: {
            "headers": _VARY_ACCEPT_RESPONSE_HEADER,
            "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
        },
        406: {
            "headers": _VARY_ACCEPT_RESPONSE_HEADER,
            "content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}},
        },
        404: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
        503: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    },
)
async def get_event_evidence(
    request: Request,
    event: Annotated[
        EventEvidenceHttpData,
        Depends(load_event_evidence),
    ],
) -> Response:
    return _response(
        request=request,
        data=event,
        as_of=event.as_of,
    )


__all__ = [
    "get_event_evidence",
    "get_latest_briefing",
    "load_event_evidence",
    "load_latest_briefing",
    "router",
]
