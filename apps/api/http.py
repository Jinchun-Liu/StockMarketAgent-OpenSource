"""HTTP request context and RFC 9457 error translation."""

import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from uuid import UUID, uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.api import FieldViolation, ProblemDetails

logger = logging.getLogger(__name__)

ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.PRECONDITION_FAILED: HTTPStatus.PRECONDITION_FAILED,
    ErrorCode.UNAUTHENTICATED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorCode.RIGHTS_BLOCKED: HTTPStatus.FORBIDDEN,
    ErrorCode.RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.TIMEOUT: HTTPStatus.GATEWAY_TIMEOUT,
    ErrorCode.DEPENDENCY_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.MODEL_REJECTED: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.EVIDENCE_INSUFFICIENT: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.PERMANENT_FAILURE: HTTPStatus.BAD_GATEWAY,
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
}
HTTP_ERROR_CODES: dict[int, ErrorCode] = {
    HTTPStatus.UNAUTHORIZED: ErrorCode.UNAUTHENTICATED,
    HTTPStatus.FORBIDDEN: ErrorCode.FORBIDDEN,
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.CONFLICT: ErrorCode.CONFLICT,
    HTTPStatus.PRECONDITION_FAILED: ErrorCode.PRECONDITION_FAILED,
    HTTPStatus.UNPROCESSABLE_ENTITY: ErrorCode.VALIDATION_ERROR,
    HTTPStatus.TOO_MANY_REQUESTS: ErrorCode.RATE_LIMITED,
}


def _parse_request_id(value: str | None) -> UUID:
    if value is not None:
        try:
            return UUID(value)
        except ValueError:
            pass
    return uuid4()


def request_ids(request: Request) -> tuple[UUID, UUID]:
    """Return IDs installed by middleware, with safe fallback for isolated tests."""

    request_id_value: object = getattr(request.state, "request_id", None)
    correlation_id_value: object = getattr(request.state, "correlation_id", None)
    request_id = request_id_value if isinstance(request_id_value, UUID) else uuid4()
    correlation_id = correlation_id_value if isinstance(correlation_id_value, UUID) else request_id
    return request_id, correlation_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Install and echo stable request and correlation IDs."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _parse_request_id(request.headers.get("X-Request-ID"))
        correlation_id_header = request.headers.get("X-Correlation-ID")
        correlation_id = (
            _parse_request_id(correlation_id_header)
            if correlation_id_header is not None
            else request_id
        )
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = str(request_id)
        response.headers["X-Correlation-ID"] = str(correlation_id)
        return response


def _problem_response(problem: ProblemDetails) -> JSONResponse:
    response = JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )
    if problem.code == ErrorCode.UNAUTHENTICATED:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


def _status_title(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP Error"


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError("validation_error_handler requires RequestValidationError")
    request_id, correlation_id = request_ids(request)
    violations = [
        FieldViolation(
            field=".".join(str(part) for part in item["loc"]),
            code="INVALID_FIELD",
            message=str(item["msg"]),
        )
        for item in exc.errors()
    ]
    return _problem_response(
        ProblemDetails(
            title="Request validation failed",
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="One or more request fields are invalid.",
            instance=request.url.path,
            code=ErrorCode.VALIDATION_ERROR,
            request_id=request_id,
            correlation_id=correlation_id,
            errors=violations,
        )
    )


async def application_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApplicationError):
        raise TypeError("application_error_handler requires ApplicationError")
    request_id, correlation_id = request_ids(request)
    status = ERROR_STATUS[exc.code]
    return _problem_response(
        ProblemDetails(
            title=exc.code.value.replace("_", " ").title(),
            status=status,
            detail=exc.safe_message,
            instance=request.url.path,
            code=exc.code,
            request_id=request_id,
            correlation_id=correlation_id,
            retryable=exc.retryable,
        )
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError("http_error_handler requires StarletteHTTPException")
    request_id, correlation_id = request_ids(request)
    code = HTTP_ERROR_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    safe_detail = (
        "The requested resource was not found."
        if code is ErrorCode.NOT_FOUND
        else "HTTP request failed."
    )
    return _problem_response(
        ProblemDetails(
            title=_status_title(exc.status_code),
            status=exc.status_code,
            detail=safe_detail,
            instance=request.url.path,
            code=code,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id, correlation_id = request_ids(request)
    logger.exception("unhandled_request_error", extra={"request_id": str(request_id)})
    return _problem_response(
        ProblemDetails(
            title="Internal Server Error",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="The request could not be completed.",
            instance=request.url.path,
            code=ErrorCode.INTERNAL_ERROR,
            request_id=request_id,
            correlation_id=correlation_id,
        )
    )


__all__ = [
    "RequestContextMiddleware",
    "application_error_handler",
    "http_error_handler",
    "request_ids",
    "unhandled_error_handler",
    "validation_error_handler",
]
