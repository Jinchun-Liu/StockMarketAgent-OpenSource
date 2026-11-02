"""Owner-isolated authenticated HTTP delivery for R2 research sessions."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from apps.api.http import request_ids
from apps.api.identity import PrincipalDependency
from apps.api.routes.active_target import PROBLEM_DETAILS_SCHEMA
from apps.api.routes.research_sessions import (
    _MUTATION_REJECTION_CODES,
    _project_legacy_report,
    _repository,
    _require_synthetic_r2,
    _response,
    _translate_repository_error,
)
from apps.worker.celery_app import celery_app
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.contracts.api import ApiResponse
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.instruments import (
    InstrumentSearchQuery,
    InstrumentSearchResult,
    InstrumentSearchResultV1_1,
)
from stock_assistant.contracts.jobs import JobCancelRequest
from stock_assistant.contracts.research import (
    ResearchGenerateResult,
    ResearchRealSourceMode,
    ResearchReport,
    ResearchReportV1_1,
    ResearchReportV1_2,
    ResearchReportV2,
    ResearchReportV2_1,
    ResearchReportV2_2,
    ResearchSessionAccepted,
    ResearchSessionCreateBody,
    ResearchSessionListQuery,
    ResearchSessionLookupRequest,
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
    ResearchSessionPage,
    ResearchSessionView,
    ResearchSourceMode,
)
from stock_assistant.contracts.research_legacy_sessions import (
    ResearchSessionPage as LegacySessionPage,
)
from stock_assistant.contracts.research_legacy_sessions import (
    ResearchSessionView as LegacySessionView,
)
from stock_assistant.contracts.research_skills import ResearchSkillCatalog
from stock_assistant.contracts.research_v1 import ResearchReport as ResearchReportV1_0
from stock_assistant.modules.research.reporting import project_retained_report_for_reader
from stock_assistant.modules.research.service import ResearchSessionService
from stock_assistant.modules.research.skills import build_skill_catalog
from stock_assistant.platform.db.research import (
    ResearchQuotaExceededError,
    ResearchRepositoryError,
)
from stock_assistant.platform.instruments.synthetic_master import (
    BundledAshareInstrumentMasterData,
)
from stock_assistant.platform.scheduling.celery_dispatcher import CeleryJobDispatcher
from stock_assistant.ports.instruments import InstrumentMasterDataV2Port

router = APIRouter(tags=["research-sessions-v2"])
legacy_report_router = APIRouter(tags=["research-sessions-v2"])

_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    403: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    429: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
}


def _operation(
    request: Request,
    principal: PrincipalDependency,
    *,
    as_of: datetime,
    now: datetime,
) -> OperationContext:
    request_id, correlation_id = request_ids(request)
    return OperationContext(
        schema_version="1.0",
        request_id=request_id,
        correlation_id=correlation_id,
        actor_id=principal.principal_sha256,
        workspace_id="r2-private-preview",
        as_of=as_of,
        requested_at=now,
    )


def _scoped_idempotency(principal_sha256: str, supplied: str) -> str:
    return hashlib.sha256((principal_sha256 + chr(0) + supplied).encode("utf-8")).hexdigest()


def _mutation(
    request: Request,
    principal: PrincipalDependency,
    *,
    supplied_idempotency_key: str,
    now: datetime,
) -> MutationContext:
    return MutationContext.model_validate(
        {
            **_operation(request, principal, as_of=now, now=now).model_dump(mode="python"),
            "idempotency_key": _scoped_idempotency(
                principal.principal_sha256, supplied_idempotency_key
            ),
        }
    )


def _lookup(
    request: Request,
    principal: PrincipalDependency,
    session_id: UUID,
    now: datetime,
) -> ResearchSessionLookupRequest:
    return ResearchSessionLookupRequest(
        schema_version="1.0",
        context=_operation(request, principal, as_of=now, now=now),
        session_id=session_id,
    )


@router.get(
    "/instruments/search",
    response_model=ApiResponse[InstrumentSearchResult],
    responses=_AUTH_RESPONSES,
)
async def search_instruments_v2(
    request: Request,
    principal: PrincipalDependency,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ApiResponse[InstrumentSearchResult]:
    del principal
    _require_synthetic_r2()
    now = utc_now()
    result = await BundledAshareInstrumentMasterData().search(
        InstrumentSearchQuery(schema_version="1.0", query=q, limit=limit, as_of=now)
    )
    return _response(request, result, as_of=result.as_of)


@router.get(
    "/instruments/directory-search",
    response_model=ApiResponse[InstrumentSearchResultV1_1],
    responses=_AUTH_RESPONSES,
)
async def search_instrument_directory_v2(
    request: Request,
    principal: PrincipalDependency,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ApiResponse[InstrumentSearchResultV1_1]:
    del principal
    _require_synthetic_r2()
    now = utc_now()
    directory: InstrumentMasterDataV2Port = BundledAshareInstrumentMasterData()
    result = await directory.search_v1_1(
        InstrumentSearchQuery(schema_version="1.0", query=q, limit=limit, as_of=now)
    )
    return _response(request, result, as_of=result.as_of)


@router.get(
    "/research-skills",
    response_model=ApiResponse[ResearchSkillCatalog],
    responses=_AUTH_RESPONSES,
)
async def list_research_skills_v2(
    request: Request,
    principal: PrincipalDependency,
    instrument_id: Annotated[str, Query(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")],
) -> ApiResponse[ResearchSkillCatalog]:
    del principal
    _require_synthetic_r2()
    now = utc_now()
    catalog = build_skill_catalog(instrument_id=instrument_id)
    return _response(request, catalog, as_of=now)


@router.post(
    "/research-sessions",
    response_model=ApiResponse[ResearchSessionAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    responses=_AUTH_RESPONSES,
)
async def create_research_session_v2(
    request: Request,
    response: Response,
    body: ResearchSessionCreateBody,
    principal: PrincipalDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionAccepted]:
    _require_synthetic_r2(new_research=True)
    now = utc_now()
    request_id, correlation_id = request_ids(request)
    try:
        accepted = await ResearchSessionService(
            BundledAshareInstrumentMasterData(),
            _repository(request),
            source_mode=(
                ResearchRealSourceMode.DEVELOPMENT_ONLY
                if get_settings().enable_r2_real_research
                else ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY
            ),
        ).create(
            body=body,
            idempotency_key=_scoped_idempotency(principal.principal_sha256, idempotency_key),
            request_id=request_id,
            correlation_id=correlation_id,
            now=now,
            actor_id=principal.principal_sha256,
            workspace_id="r2-private-preview",
        )
    except ResearchQuotaExceededError as error:
        raise ApplicationError(ErrorCode.RATE_LIMITED, error.reason_code) from error
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    response.headers["Location"] = f"/api/v2/research-sessions/{accepted.session_id}"
    return _response(request, accepted, as_of=body.as_of or now)


@router.get(
    "/research-sessions/{session_id}/view-v2.0",
    response_model=ApiResponse[ResearchSessionView],
    responses=_AUTH_RESPONSES,
)
async def get_current_research_session_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchSessionView]:
    _require_synthetic_r2()
    now = utc_now()
    lookup = _lookup(request, principal, session_id, now)
    try:
        result = await _repository(request).get(lookup)
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    if not result.found or result.session is None:
        raise ApplicationError(ErrorCode.NOT_FOUND, "RESEARCH_SESSION_NOT_FOUND")
    return _response(request, result.session, as_of=now)


@router.get(
    "/research-sessions-v2.0",
    response_model=ApiResponse[ResearchSessionPage],
    responses=_AUTH_RESPONSES,
)
async def list_current_research_sessions_v2(
    request: Request,
    principal: PrincipalDependency,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[ResearchSessionPage]:
    _require_synthetic_r2()
    now = utc_now()
    try:
        page = await _repository(request).list_page(
            ResearchSessionListQuery(
                schema_version="1.0",
                context=_operation(request, principal, as_of=now, now=now),
                cursor=cursor,
                limit=limit,
            )
        )
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    return _response(request, page, as_of=page.as_of)


@router.get(
    "/research-sessions/{session_id}",
    response_model=ApiResponse[LegacySessionView],
    responses=_AUTH_RESPONSES,
)
async def get_research_session_v2(
    request: Request, session_id: UUID, principal: PrincipalDependency
) -> ApiResponse[LegacySessionView]:
    current = await get_current_research_session_v2(request, session_id, principal)
    try:
        legacy = LegacySessionView.from_current(current.data)
    except ValidationError:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED, "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
        ) from None
    return _response(request, legacy, as_of=current.meta.as_of)


@router.get(
    "/research-sessions", response_model=ApiResponse[LegacySessionPage], responses=_AUTH_RESPONSES
)
async def list_research_sessions_v2(
    request: Request,
    principal: PrincipalDependency,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[LegacySessionPage]:
    current = await list_current_research_sessions_v2(request, principal, cursor, limit)
    try:
        legacy = LegacySessionPage.from_current(current.data)
    except ValidationError:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED, "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
        ) from None
    return _response(request, legacy, as_of=current.meta.as_of)


@legacy_report_router.get(
    "/research-sessions/{session_id}/report",
    response_model=ApiResponse[ResearchReportV1_0],
    responses=_AUTH_RESPONSES,
)
async def get_research_report_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchReportV1_0]:
    _require_synthetic_r2()
    now = utc_now()
    try:
        result = await _repository(request).get_report(_lookup(request, principal, session_id, now))
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    if not result.found or result.report is None:
        if result.reason_code == "REPORT_NOT_READY":
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REPORT_NOT_READY")
        if result.reason_code == "REPORT_COMPATIBILITY_PROJECTION_FAILED":
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "REPORT_COMPATIBILITY_PROJECTION_FAILED",
            )
        raise ApplicationError(ErrorCode.NOT_FOUND, result.reason_code or "REPORT_NOT_FOUND")
    legacy = _project_legacy_report(result.report)
    return _response(request, legacy, as_of=result.as_of)


@router.get(
    "/research-sessions/{session_id}/report-v1.1",
    response_model=ApiResponse[ResearchReportV1_1],
    responses=_AUTH_RESPONSES,
)
async def get_readable_research_report_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchReportV1_1]:
    readable = await _readable_report(request, session_id, principal)
    if readable.schema_version in {"2.0", "2.1", "2.2"}:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED, "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
        )
    if readable.schema_version == "1.2":
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REPORT_VERSION_REQUIRES_V1_2")
    payload = readable.model_dump(mode="python")
    if isinstance(payload.get("scorecard"), dict):
        payload["scorecard"].pop("scoring_basis", None)
    return _response(request, ResearchReportV1_1.model_validate(payload), as_of=readable.as_of)


@router.get(
    "/research-sessions/{session_id}/report-v1.2",
    response_model=ApiResponse[ResearchReportV1_2],
    responses=_AUTH_RESPONSES,
)
async def get_scored_research_report_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchReportV1_2]:
    readable = await _readable_report(request, session_id, principal)
    if readable.schema_version in {"2.0", "2.1", "2.2"}:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED, "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
        )
    payload = readable.model_dump(mode="python")
    if readable.schema_version != "1.2":
        payload.update(
            schema_version="1.2",
            artifact_object_key=None,
            artifact_content_sha256=None,
            artifact_content_length=None,
        )
    return _response(request, ResearchReportV1_2.model_validate(payload), as_of=readable.as_of)


@router.get(
    "/research-sessions/{session_id}/report-v2.0",
    response_model=ApiResponse[ResearchReportV2 | ResearchReportV1_2],
    responses=_AUTH_RESPONSES,
)
async def get_current_research_report_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchReportV2 | ResearchReportV1_2]:
    readable = await _readable_report(request, session_id, principal)
    if readable.schema_version in {"2.1", "2.2"}:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REPORT_REQUIRES_V2_1")
    if readable.schema_version == "2.0":
        report: ResearchReportV2 | ResearchReportV1_2 = ResearchReportV2.model_validate(
            readable.model_dump(mode="python")
        )
    else:
        payload = readable.model_dump(mode="python")
        if readable.schema_version != "1.2":
            payload.update(
                schema_version="1.2",
                artifact_object_key=None,
                artifact_content_sha256=None,
                artifact_content_length=None,
            )
        report = ResearchReportV1_2.model_validate(payload)
    return _response(request, report, as_of=readable.as_of)


@router.get(
    "/research-sessions/{session_id}/report-v2.1",
    response_model=ApiResponse[ResearchReportV2_1 | ResearchReportV2 | ResearchReportV1_2],
    responses=_AUTH_RESPONSES,
)
async def get_real_research_report_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchReportV2_1 | ResearchReportV2 | ResearchReportV1_2]:
    readable = await _readable_report(request, session_id, principal)
    if readable.schema_version == "2.2":
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REPORT_REQUIRES_V2_2")
    if readable.schema_version == "2.1":
        report: ResearchReportV2_1 | ResearchReportV2 | ResearchReportV1_2 = (
            ResearchReportV2_1.model_validate(readable.model_dump(mode="python"))
        )
    elif readable.schema_version == "2.0":
        report = ResearchReportV2.model_validate(readable.model_dump(mode="python"))
    else:
        payload = readable.model_dump(mode="python")
        if readable.schema_version != "1.2":
            payload.update(
                schema_version="1.2",
                artifact_object_key=None,
                artifact_content_sha256=None,
                artifact_content_length=None,
            )
        report = ResearchReportV1_2.model_validate(payload)
    return _response(request, report, as_of=readable.as_of)


@router.get(
    "/research-sessions/{session_id}/report-v2.2",
    response_model=ApiResponse[
        ResearchReportV2_2 | ResearchReportV2_1 | ResearchReportV2 | ResearchReportV1_2
    ],
    responses=_AUTH_RESPONSES,
)
async def get_formal_research_report_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ApiResponse[ResearchReportV2_2 | ResearchReportV2_1 | ResearchReportV2 | ResearchReportV1_2]:
    readable = await _readable_report(request, session_id, principal)
    if readable.schema_version == "2.2":
        report = ResearchReportV2_2.model_validate(readable.model_dump(mode="python"))
        return _response(request, report, as_of=readable.as_of)
    legacy = await get_real_research_report_v2(request, session_id, principal)
    return _response(request, legacy.data, as_of=readable.as_of)


async def _readable_report(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
) -> ResearchReport:
    _require_synthetic_r2()
    now = utc_now()
    try:
        result = await _repository(request).get_report(_lookup(request, principal, session_id, now))
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    if not result.found or result.report is None:
        if result.reason_code == "REPORT_NOT_READY":
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REPORT_NOT_READY")
        if result.reason_code == "REPORT_COMPATIBILITY_PROJECTION_FAILED":
            raise ApplicationError(
                ErrorCode.PRECONDITION_FAILED,
                "REPORT_COMPATIBILITY_PROJECTION_FAILED",
            )
        raise ApplicationError(ErrorCode.NOT_FOUND, result.reason_code or "REPORT_NOT_FOUND")
    try:
        readable = project_retained_report_for_reader(result.report)
    except (ValidationError, ValueError):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "REPORT_COMPATIBILITY_PROJECTION_FAILED",
        ) from None
    return readable


async def _mutate_v2(
    request: Request,
    principal: PrincipalDependency,
    session_id: UUID,
    supplied_idempotency_key: str,
    operation: Literal["cancel", "retry", "delete"],
) -> ApiResponse[ResearchSessionMutationResult]:
    _require_synthetic_r2(new_research=operation == "retry")
    now = utc_now()
    command = ResearchSessionMutationRequest(
        schema_version="1.0",
        context=_mutation(
            request,
            principal,
            supplied_idempotency_key=supplied_idempotency_key,
            now=now,
        ),
        session_id=session_id,
    )
    repository = _repository(request)
    handler = {
        "cancel": repository.cancel,
        "retry": repository.retry,
        "delete": repository.delete,
    }[operation]
    try:
        result = await handler(command)
        if result.reason_code == "RESEARCH_SESSION_NOT_FOUND":
            raise ApplicationError(ErrorCode.NOT_FOUND, "RESEARCH_SESSION_NOT_FOUND")
        rejection = (
            _MUTATION_REJECTION_CODES.get(result.reason_code)
            if result.reason_code is not None
            else None
        )
        if rejection is not None and result.reason_code is not None:
            raise ApplicationError(rejection, result.reason_code)
        if (
            operation == "cancel"
            and result.job_id is not None
            and result.status.value == "cancelled"
        ):
            await CeleryJobDispatcher(
                celery_app,
                result_model=ResearchGenerateResult,
            ).cancel(
                JobCancelRequest(
                    schema_version="1.0",
                    context=command.context,
                    job_id=result.job_id,
                )
            )
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    return _response(request, result, as_of=now)


@router.post(
    "/research-sessions/{session_id}/cancel",
    response_model=ApiResponse[ResearchSessionMutationResult],
    responses=_AUTH_RESPONSES,
)
async def cancel_research_session_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionMutationResult]:
    return await _mutate_v2(request, principal, session_id, idempotency_key, "cancel")


@router.post(
    "/research-sessions/{session_id}/retry",
    response_model=ApiResponse[ResearchSessionMutationResult],
    responses=_AUTH_RESPONSES,
)
async def retry_research_session_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionMutationResult]:
    return await _mutate_v2(request, principal, session_id, idempotency_key, "retry")


@router.delete(
    "/research-sessions/{session_id}",
    response_model=ApiResponse[ResearchSessionMutationResult],
    responses=_AUTH_RESPONSES,
)
async def delete_research_session_v2(
    request: Request,
    session_id: UUID,
    principal: PrincipalDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionMutationResult]:
    return await _mutate_v2(request, principal, session_id, idempotency_key, "delete")
