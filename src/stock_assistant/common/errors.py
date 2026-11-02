"""Stable, safe error taxonomy shared across use cases and adapters."""

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    RIGHTS_BLOCKED = "RIGHTS_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    MODEL_REJECTED = "MODEL_REJECTED"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApplicationError(Exception):
    """An application-safe error; sensitive causes remain in protected observability."""

    def __init__(self, code: ErrorCode, safe_message: str, *, retryable: bool = False) -> None:
        if not safe_message.strip():
            raise ValueError("safe_message must not be empty")
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


__all__ = ["ApplicationError", "ErrorCode"]
