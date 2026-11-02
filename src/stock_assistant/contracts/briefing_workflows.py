"""Requests and results for briefing generation, validation and storage."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.briefings import Briefing, BriefingKind
from stock_assistant.contracts.common import CursorPage, MutationContext, OperationContext


class BriefingValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class BriefingGenerationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    kind: BriefingKind
    portfolio_id: UUID | None = None
    instrument_ids: list[str] = Field(default_factory=list, max_length=10_000)
    sector_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    policy_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    event_ids: list[UUID] = Field(default_factory=list, max_length=10_000)
    comparison_briefing_id: UUID | None = None
    maximum_items: int = Field(default=100, ge=1, le=200)
    minimum_materiality: float = Field(default=0.4, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def targeted_kinds_require_a_scope(self) -> "BriefingGenerationRequest":
        if self.kind is BriefingKind.PREMARKET:
            if self.portfolio_id is None and not self.instrument_ids:
                raise ValueError("premarket briefings require a portfolio or instruments")
        if self.kind is BriefingKind.POLICY:
            scopes = [
                self.portfolio_id is not None,
                bool(self.instrument_ids),
                bool(self.sector_ids),
                bool(self.policy_ids),
            ]
            if not any(scopes):
                raise ValueError("policy briefings require an exposure or policy scope")
        return self


class BriefingInputSummary(ContractModel):
    event_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    policy_count: int = Field(ge=0)
    sector_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    unavailable_source_count: int = Field(ge=0)


class BriefingGenerationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    briefing: Briefing
    inputs: BriefingInputSummary
    generated_at: AwareDatetime
    generator_version: str = Field(min_length=1, max_length=200)
    degraded: bool = False
    degradation_codes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def generated_result_must_be_consistent(self) -> "BriefingGenerationResult":
        if self.generated_at != self.briefing.generated_at:
            raise ValueError("result generated_at must match briefing.generated_at")
        if self.degraded and not self.degradation_codes:
            raise ValueError("degraded results require degradation_codes")
        if not self.degraded and self.degradation_codes:
            raise ValueError("degradation_codes are only valid for degraded results")
        return self


class BriefingValidationIssue(ContractModel):
    severity: BriefingValidationSeverity
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    item_index: int | None = Field(default=None, ge=0, le=199)
    field_path: str | None = Field(default=None, max_length=500)
    safe_message: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)


class BriefingValidationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    briefing: Briefing
    ruleset_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def briefing_must_be_known_at_validation_cutoff(self) -> "BriefingValidationRequest":
        if self.briefing.as_of > self.context.as_of:
            raise ValueError("briefing.as_of cannot be later than context.as_of")
        if self.briefing.generated_at > self.context.as_of:
            raise ValueError("validation cannot accept a briefing generated in the future")
        return self


class BriefingValidationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    briefing_id: UUID
    valid: bool
    validated_briefing: Briefing | None = None
    issues: list[BriefingValidationIssue] = Field(default_factory=list, max_length=1000)
    ruleset_version: str = Field(min_length=1, max_length=200)
    validated_at: AwareDatetime

    @model_validator(mode="after")
    def validation_state_must_be_consistent(self) -> "BriefingValidationResult":
        has_errors = any(item.severity is BriefingValidationSeverity.ERROR for item in self.issues)
        if self.valid and (has_errors or self.validated_briefing is None):
            raise ValueError("valid results require a briefing and cannot contain errors")
        if not self.valid and not has_errors:
            raise ValueError("invalid results require at least one error issue")
        if (
            self.validated_briefing is not None
            and self.validated_briefing.briefing_id != self.briefing_id
        ):
            raise ValueError("validated briefing must retain the requested briefing ID")
        if self.validated_briefing is not None:
            if self.validated_at < self.validated_briefing.generated_at:
                raise ValueError("validated_at cannot precede briefing generation")
        return self


class BriefingStoreRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    briefing: Briefing

    @model_validator(mode="after")
    def briefing_must_not_be_from_the_future(self) -> "BriefingStoreRequest":
        if self.briefing.generated_at > self.context.as_of:
            raise ValueError("storage cannot accept a briefing generated in the future")
        return self


class BriefingStorageRecord(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    briefing: Briefing
    stored_at: AwareDatetime
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def storage_must_follow_generation(self) -> "BriefingStorageRecord":
        if self.stored_at < self.briefing.generated_at:
            raise ValueError("stored_at cannot be earlier than briefing.generated_at")
        return self


class BriefingQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    briefing_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    kinds: list[BriefingKind] = Field(default_factory=list, max_length=20)
    generated_since: AwareDatetime | None = None
    generated_until: AwareDatetime | None = None
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def generated_range_must_be_point_in_time_safe(self) -> "BriefingQuery":
        if self.generated_since is not None and self.generated_until is not None:
            if self.generated_until < self.generated_since:
                raise ValueError("generated_until cannot be earlier than generated_since")
        if self.generated_until is not None and self.generated_until > self.context.as_of:
            raise ValueError("generated_until cannot be later than context.as_of")
        if self.generated_since is not None and self.generated_since > self.context.as_of:
            raise ValueError("generated_since cannot be later than context.as_of")
        return self


class BriefingPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    page: CursorPage[BriefingStorageRecord]

    @model_validator(mode="after")
    def records_must_be_visible_at_page_cutoff(self) -> "BriefingPage":
        if any(item.stored_at > self.page.as_of for item in self.page.items):
            raise ValueError("briefing page cannot contain future storage records")
        return self
