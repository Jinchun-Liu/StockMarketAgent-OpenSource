"""HTTP delivery for R2 search and independent on-demand research sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from apps.api.http import request_ids
from apps.api.routes.active_target import PROBLEM_DETAILS_SCHEMA, _database_engine
from apps.worker.celery_app import celery_app
from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.contracts.api import ApiResponse, ResponseMeta
from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.instruments import (
    InstrumentSearchQuery,
    InstrumentSearchResult,
)
from stock_assistant.contracts.jobs import JobCancelRequest
from stock_assistant.contracts.research import (
    ResearchEvidenceAcquisitionMode,
    ResearchGenerateResult,
    ResearchReport,
    ResearchSessionAccepted,
    ResearchSessionCreateBody,
    ResearchSessionListQuery,
    ResearchSessionLookupRequest,
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
)
from stock_assistant.contracts.research_legacy_sessions import (
    ResearchSessionPage,
    ResearchSessionView,
)
from stock_assistant.contracts.research_v1 import ResearchReport as ResearchReportV1_0
from stock_assistant.modules.research.service import ResearchSessionService
from stock_assistant.platform.db.research import (
    PostgresResearchRepository,
    ResearchIdempotencyConflictError,
    ResearchRepositoryError,
    ResearchTransitionConflictError,
)
from stock_assistant.platform.instruments.synthetic_master import (
    BundledAshareInstrumentMasterData,
)
from stock_assistant.platform.scheduling.celery_dispatcher import CeleryJobDispatcher

router = APIRouter(tags=["research-sessions"])

_MUTATION_REJECTION_CODES = {
    "RESEARCH_SESSION_NOT_CANCELLABLE": ErrorCode.CONFLICT,
    "RESEARCH_SESSION_NOT_RETRYABLE": ErrorCode.PRECONDITION_FAILED,
    "RESEARCH_RETRY_ATTEMPTS_EXHAUSTED": ErrorCode.PRECONDITION_FAILED,
    "RESEARCH_SESSION_MUST_BE_CANCELLED": ErrorCode.PRECONDITION_FAILED,
}


def _require_synthetic_r2(*, new_research: bool = False) -> None:
    settings = get_settings()
    if settings.app_env == "production" or not (
        settings.enable_r2_synthetic_research
        or settings.enable_r2_real_research
        or settings.enable_r2_private_preview
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R2_SYNTHETIC_RESEARCH_DISABLED")
    if new_research and not (
        settings.enable_r2_real_research
        or (settings.enable_r2_synthetic_research and settings.app_env != "preview")
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R2_NEW_RESEARCH_DISABLED")


def _operation(request: Request, *, as_of: datetime, now: datetime) -> OperationContext:
    request_id, correlation_id = request_ids(request)
    return OperationContext(
        schema_version="1.0",
        request_id=request_id,
        correlation_id=correlation_id,
        actor_id="local-research-user",
        workspace_id="on-demand-research",
        as_of=as_of,
        requested_at=now,
    )


def _mutation(
    request: Request,
    *,
    idempotency_key: str,
    as_of: datetime,
    now: datetime,
) -> MutationContext:
    return MutationContext.model_validate(
        {
            **_operation(request, as_of=as_of, now=now).model_dump(mode="python"),
            "idempotency_key": idempotency_key,
        }
    )


def _response[DataT: ContractModel](
    request: Request,
    data: DataT,
    *,
    as_of: datetime,
) -> ApiResponse[DataT]:
    request_id, correlation_id = request_ids(request)
    return ApiResponse(
        data=data,
        meta=ResponseMeta(
            request_id=request_id,
            correlation_id=correlation_id,
            as_of=as_of,
        ),
    )


def _repository(request: Request) -> PostgresResearchRepository:
    return PostgresResearchRepository(_database_engine(request))


def _translate_repository_error(error: Exception) -> ApplicationError:
    if isinstance(error, ResearchIdempotencyConflictError | ResearchTransitionConflictError):
        return ApplicationError(ErrorCode.CONFLICT, "RESEARCH_SESSION_CONFLICT")
    return ApplicationError(
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "RESEARCH_PERSISTENCE_UNAVAILABLE",
        retryable=True,
    )


def _project_legacy_report(report: ResearchReport) -> ResearchReportV1_0:
    citations = [
        citation
        for claim in report.claims
        for citation in (*claim.evidence, *claim.counter_evidence)
    ]
    if any(
        citation.acquisition_mode is ResearchEvidenceAcquisitionMode.MODEL_ASSISTED_WEB
        for citation in citations
    ):
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "LEGACY_REPORT_WEB_EVIDENCE_UNREPRESENTABLE",
        )
    try:
        return ResearchReportV1_0.from_current(report)
    except (ValidationError, ValueError) as error:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED,
            "LEGACY_REPORT_PROJECTION_UNAVAILABLE",
        ) from error


@router.get(
    "/instruments/search",
    response_model=ApiResponse[InstrumentSearchResult],
)
async def search_instruments(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ApiResponse[InstrumentSearchResult]:
    _require_synthetic_r2()
    now = utc_now()
    result = await BundledAshareInstrumentMasterData().search(
        InstrumentSearchQuery(schema_version="1.0", query=q, limit=limit, as_of=now)
    )
    return _response(request, result, as_of=result.as_of)


@router.post(
    "/research-sessions",
    response_model=ApiResponse[ResearchSessionAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_research_session(
    request: Request,
    response: Response,
    body: ResearchSessionCreateBody,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionAccepted]:
    _require_synthetic_r2(new_research=True)
    if get_settings().enable_r2_real_research:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "REAL_RESEARCH_REQUIRES_V2")
    now = utc_now()
    request_id, correlation_id = request_ids(request)
    try:
        accepted = await ResearchSessionService(
            BundledAshareInstrumentMasterData(),
            _repository(request),
        ).create(
            body=body,
            idempotency_key=idempotency_key,
            request_id=request_id,
            correlation_id=correlation_id,
            now=now,
        )
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    response.headers["Location"] = f"/api/v1/research-sessions/{accepted.session_id}"
    return _response(request, accepted, as_of=body.as_of or now)


@router.get(
    "/research-sessions/{session_id}",
    response_model=ApiResponse[ResearchSessionView],
)
async def get_research_session(
    request: Request,
    session_id: UUID,
) -> ApiResponse[ResearchSessionView]:
    _require_synthetic_r2()
    now = utc_now()
    try:
        lookup = _lookup_request(request, session_id, now)
        result = await _repository(request).get(lookup)
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    if not result.found or result.session is None:
        raise ApplicationError(ErrorCode.NOT_FOUND, "RESEARCH_SESSION_NOT_FOUND")
    try:
        legacy = ResearchSessionView.from_current(result.session)
    except ValidationError:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED, "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
        ) from None
    return _response(request, legacy, as_of=lookup.context.as_of)


def _lookup_request(
    request: Request,
    session_id: UUID,
    now: datetime,
) -> ResearchSessionLookupRequest:
    return ResearchSessionLookupRequest(
        schema_version="1.0",
        context=_operation(request, as_of=now, now=now),
        session_id=session_id,
    )


@router.get(
    "/research-sessions",
    response_model=ApiResponse[ResearchSessionPage],
)
async def list_research_sessions(
    request: Request,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[ResearchSessionPage]:
    _require_synthetic_r2()
    now = utc_now()
    try:
        page = await _repository(request).list_page(
            ResearchSessionListQuery(
                schema_version="1.0",
                context=_operation(request, as_of=now, now=now),
                cursor=cursor,
                limit=limit,
            )
        )
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    try:
        legacy_page = ResearchSessionPage.from_current(page)
    except ValidationError:
        raise ApplicationError(
            ErrorCode.PRECONDITION_FAILED, "LEGACY_REPORT_PROJECTION_UNAVAILABLE"
        ) from None
    return _response(request, legacy_page, as_of=page.as_of)


@router.get(
    "/research-sessions/{session_id}/report",
    response_model=ApiResponse[ResearchReportV1_0],
)
async def get_research_report(
    request: Request,
    session_id: UUID,
) -> ApiResponse[ResearchReportV1_0]:
    _require_synthetic_r2()
    now = utc_now()
    try:
        result = await _repository(request).get_report(_lookup_request(request, session_id, now))
    except (ResearchRepositoryError, SQLAlchemyError, OSError) as error:
        raise _translate_repository_error(error) from error
    if not result.found or result.report is None:
        code = (
            ErrorCode.PRECONDITION_FAILED
            if result.reason_code in {"REPORT_NOT_READY", "REPORT_COMPATIBILITY_PROJECTION_FAILED"}
            else ErrorCode.NOT_FOUND
        )
        raise ApplicationError(code, result.reason_code or "REPORT_NOT_FOUND")
    legacy = _project_legacy_report(result.report)
    return _response(request, legacy, as_of=result.as_of)


async def _mutate_session(
    request: Request,
    session_id: UUID,
    idempotency_key: str,
    operation: Literal["cancel", "retry", "delete"],
) -> ApiResponse[ResearchSessionMutationResult]:
    _require_synthetic_r2(new_research=operation == "retry")
    now = utc_now()
    command = ResearchSessionMutationRequest(
        schema_version="1.0",
        context=_mutation(
            request,
            idempotency_key=idempotency_key,
            as_of=now,
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
        reason_code = result.reason_code
        rejection_code = (
            _MUTATION_REJECTION_CODES.get(reason_code) if reason_code is not None else None
        )
        if rejection_code is not None and reason_code is not None:
            raise ApplicationError(rejection_code, reason_code)
        if (
            operation == "cancel"
            and result.status.value == "cancelled"
            and result.job_id is not None
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
    responses={
        409: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    },
)
async def cancel_research_session(
    request: Request,
    session_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionMutationResult]:
    return await _mutate_session(request, session_id, idempotency_key, "cancel")


@router.post(
    "/research-sessions/{session_id}/retry",
    response_model=ApiResponse[ResearchSessionMutationResult],
    responses={
        412: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    },
)
async def retry_research_session(
    request: Request,
    session_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionMutationResult]:
    return await _mutate_session(request, session_id, idempotency_key, "retry")


@router.delete(
    "/research-sessions/{session_id}",
    response_model=ApiResponse[ResearchSessionMutationResult],
    responses={
        412: {"content": {"application/problem+json": {"schema": PROBLEM_DETAILS_SCHEMA}}},
    },
)
async def delete_research_session(
    request: Request,
    session_id: UUID,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)],
) -> ApiResponse[ResearchSessionMutationResult]:
    return await _mutate_session(request, session_id, idempotency_key, "delete")
