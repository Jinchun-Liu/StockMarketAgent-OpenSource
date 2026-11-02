"""Requests and results for extracting, validating and revising market events."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.content import NormalizedContent, NormalizedContentKind
from stock_assistant.contracts.events import MarketEvent
from stock_assistant.contracts.ingestion import FactEligibility


class ValidationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class EventRevisionOutcome(StrEnum):
    UNCHANGED = "unchanged"
    REVISED = "revised"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class EventExtractionPurpose(StrEnum):
    """Policies and market bars use their dedicated typed workflows, not this extractor."""

    COMPANY_FACT = "company_fact"
    CONTEXT_EVENT = "context_event"


class EventExtractionRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    content: NormalizedContent
    purpose: EventExtractionPurpose
    maximum_events: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def content_must_be_eligible_and_visible(self) -> "EventExtractionRequest":
        if self.content.revision_known_at > self.context.as_of:
            raise ValueError("content revision cannot be known after context.as_of")
        if self.content.content_kind is NormalizedContentKind.SOCIAL_SENTIMENT_AGGREGATE:
            raise ValueError("social sentiment aggregates cannot enter event extraction")
        eligibility = self.content.fact_eligibility
        if self.purpose.value != eligibility.value:
            context_pair = (
                self.purpose is EventExtractionPurpose.CONTEXT_EVENT
                and eligibility is FactEligibility.CONTEXT_ONLY
            )
            if not context_pair:
                raise ValueError("extraction purpose is incompatible with source fact eligibility")
        return self


class EventExtractionResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    source_record_id: UUID
    events: list[MarketEvent] = Field(default_factory=list, max_length=100)
    extractor_version: str = Field(min_length=1, max_length=200)
    extracted_at: AwareDatetime
    ignored_reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def empty_result_requires_a_reason(self) -> "EventExtractionResult":
        if not self.events and self.ignored_reason_code is None:
            raise ValueError("an empty extraction result requires ignored_reason_code")
        if self.events and self.ignored_reason_code is not None:
            raise ValueError("ignored_reason_code is only valid for an empty result")
        if any(item.revision_known_at > self.extracted_at for item in self.events):
            raise ValueError("extracted events cannot be known after extracted_at")
        return self


class EventValidationIssue(ContractModel):
    severity: ValidationSeverity
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    field_path: str | None = Field(default=None, max_length=500)
    safe_message: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)


class EventValidationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    event: MarketEvent
    ruleset_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def event_must_be_visible_at_cutoff(self) -> "EventValidationRequest":
        if self.event.revision_known_at > self.context.as_of:
            raise ValueError("event revision cannot be known after context.as_of")
        return self


class EventValidationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    valid: bool
    validated_event: MarketEvent | None = None
    issues: list[EventValidationIssue] = Field(default_factory=list, max_length=500)
    ruleset_version: str = Field(min_length=1, max_length=200)
    validated_at: AwareDatetime

    @model_validator(mode="after")
    def validation_state_must_be_consistent(self) -> "EventValidationResult":
        has_errors = any(item.severity is ValidationSeverity.ERROR for item in self.issues)
        if self.valid and (has_errors or self.validated_event is None):
            raise ValueError("valid results require an event and cannot contain errors")
        if not self.valid and not has_errors:
            raise ValueError("invalid results require at least one error issue")
        if self.validated_event is not None and self.validated_event.event_id != self.event_id:
            raise ValueError("validated_event must retain the requested event ID")
        if self.validated_event is not None:
            if self.validated_at < self.validated_event.revision_known_at:
                raise ValueError("validated_at cannot precede the validated event revision")
        return self


class EventRevisionRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    current: MarketEvent
    candidate: MarketEvent
    expected_revision: int = Field(ge=1)
    comparison_rule_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def revisions_must_refer_to_the_same_cluster(self) -> "EventRevisionRequest":
        if self.current.cluster_id != self.candidate.cluster_id:
            raise ValueError("current and candidate events must share a cluster ID")
        if self.current.event_id != self.candidate.event_id:
            raise ValueError("event revision must retain the stable event ID")
        if self.current.revision != self.expected_revision:
            raise ValueError("current revision must match expected_revision")
        if self.current.revision_known_at > self.context.as_of:
            raise ValueError("current revision cannot be known after context.as_of")
        if self.candidate.revision_known_at > self.context.as_of:
            raise ValueError("candidate revision cannot be known after context.as_of")
        if self.candidate.revision_known_at < self.current.revision_known_at:
            raise ValueError("candidate revision cannot predate the current revision")
        return self


class EventRevisionResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    outcome: EventRevisionOutcome
    previous_revision: int = Field(ge=1)
    resolved_event: MarketEvent
    changed_fields: list[str] = Field(default_factory=list, max_length=100)
    decided_at: AwareDatetime
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    comparison_rule_version: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def outcome_and_revision_must_be_consistent(self) -> "EventRevisionResult":
        changed = self.outcome in {
            EventRevisionOutcome.REVISED,
            EventRevisionOutcome.CORRECTED,
        }
        if changed and self.resolved_event.revision != self.previous_revision + 1:
            raise ValueError("changed events must increment the revision exactly once")
        if not changed and self.resolved_event.revision != self.previous_revision:
            raise ValueError("unchanged or rejected events must retain the previous revision")
        if changed and not self.changed_fields:
            raise ValueError("changed events require changed_fields")
        if not changed and self.changed_fields:
            raise ValueError("unchanged or rejected events cannot list changed_fields")
        if self.decided_at < self.resolved_event.revision_known_at:
            raise ValueError("decided_at cannot precede the resolved event revision")
        return self
