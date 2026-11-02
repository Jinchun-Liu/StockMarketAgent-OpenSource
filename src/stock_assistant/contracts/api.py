"""Uniform HTTP success, error and pagination contracts."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from stock_assistant.contracts.base import ContractModel


class ResponseMeta(ContractModel):
    """Metadata returned with every non-health API response."""

    request_id: UUID
    correlation_id: UUID
    as_of: AwareDatetime
    generated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    contract_version: Literal["1.0"] = "1.0"


class ApiResponse[DataT](ContractModel):
    """Success envelope; HTTP status still carries transport semantics."""

    data: DataT
    meta: ResponseMeta


class FieldViolation(ContractModel):
    """Safe field-level validation detail."""

    field: str = Field(min_length=1, max_length=300)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    message: str = Field(min_length=1, max_length=500)


class ProblemDetails(ContractModel):
    """RFC 9457-compatible error with stable project extensions."""

    type: str = Field(default="about:blank", min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=200)
    status: int = Field(ge=400, le=599)
    detail: str = Field(min_length=1, max_length=2000)
    instance: str | None = Field(default=None, max_length=2048)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    request_id: UUID
    correlation_id: UUID
    retryable: bool = False
    errors: list[FieldViolation] = Field(default_factory=list, max_length=100)
