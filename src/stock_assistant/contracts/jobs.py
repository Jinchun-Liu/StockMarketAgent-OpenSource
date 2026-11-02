"""Versioned background-job and scheduling contracts."""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class JobRequest[JobPayloadT: ContractModel](ContractModel):
    """Retry-safe job request with an explicit contract version in its name."""

    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID = Field(default_factory=uuid4)
    job_name: str = Field(
        pattern=r"^stock_assistant\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$"
    )
    context: MutationContext
    payload: JobPayloadT
    queued_at: AwareDatetime
    scheduled_for: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    attempt: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def timing_and_attempts_must_be_valid(self) -> "JobRequest[JobPayloadT]":
        effective_start = max(self.queued_at, self.scheduled_for or self.queued_at)
        if self.expires_at is not None and self.expires_at <= effective_start:
            raise ValueError("expires_at must be later than the effective start time")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        return self


class JobAccepted(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID
    status: JobStatus = JobStatus.QUEUED
    accepted_at: AwareDatetime
    queue_name: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def accepted_status_must_be_queued(self) -> "JobAccepted":
        if self.status is not JobStatus.QUEUED:
            raise ValueError("accepted jobs must have queued status")
        return self


class JobResultQuery(ContractModel):
    """Lookup one job without leaking queue-specific result handles."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    job_id: UUID


class JobCancelRequest(ContractModel):
    """Idempotent cancellation request."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    job_id: UUID


class JobCancelResult(ContractModel):
    """Stable cancellation outcome independent of a queue backend."""

    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID
    cancelled: bool
    status: JobStatus
    processed_at: AwareDatetime
    idempotent_replay: bool = False
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def cancellation_state_must_be_consistent(self) -> Self:
        if self.cancelled != (self.status is JobStatus.CANCELLED):
            raise ValueError("cancelled must reflect whether status is cancelled")
        if not self.cancelled and self.reason_code is None:
            raise ValueError("a non-cancelled result requires reason_code")
        return self


class JobFailure(ContractModel):
    """Safe failure details; stack traces stay in observability storage."""

    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    safe_message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    failed_at: AwareDatetime


class JobResult[JobResultT: ContractModel](ContractModel):
    """Terminal or in-progress state without transport-specific result wrappers."""

    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID
    status: JobStatus
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    result: JobResultT | None = None
    failure: JobFailure | None = None
    attempt: int = Field(ge=1)

    @model_validator(mode="after")
    def terminal_state_must_be_consistent(self) -> "JobResult[JobResultT]":
        terminal = {
            JobStatus.SUCCEEDED,
            JobStatus.DEGRADED,
            JobStatus.SKIPPED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXPIRED,
        }
        if self.completed_at is not None and self.started_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot be earlier than started_at")
        if self.status in terminal and self.completed_at is None:
            raise ValueError("completed_at is required for terminal states")
        if self.status in {JobStatus.SUCCEEDED, JobStatus.DEGRADED, JobStatus.SKIPPED}:
            if self.result is None:
                raise ValueError("result is required for successful, degraded or skipped jobs")
        result_states = {JobStatus.SUCCEEDED, JobStatus.DEGRADED, JobStatus.SKIPPED}
        if self.status not in result_states and self.result is not None:
            raise ValueError("result is only valid for successful, degraded or skipped jobs")
        if self.status is JobStatus.FAILED and self.failure is None:
            raise ValueError("failure is required for failed jobs")
        if self.status is not JobStatus.FAILED and self.failure is not None:
            raise ValueError("failure is only valid for failed jobs")
        return self


class JobResultLookup[JobResultT: ContractModel](ContractModel):
    """Explicit job lookup result; absence is data rather than a nullable port return."""

    schema_version: Literal["1.0"] = "1.0"
    job_id: UUID
    found: bool
    job: JobResult[JobResultT] | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_and_visibility_must_be_consistent(self) -> Self:
        if self.found != (self.job is not None):
            raise ValueError("found must reflect whether a job result is present")
        if self.job is not None:
            if self.job.job_id != self.job_id:
                raise ValueError("job result must match job_id")
            visible_times = (self.job.started_at, self.job.completed_at)
            if any(value is not None and value > self.as_of for value in visible_times):
                raise ValueError("job result cannot expose state recorded after as_of")
        return self


class JobExecutionSummary(ContractModel):
    """Small safe result used by task adapters and operational tooling."""

    operation: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    safe_message: str = Field(min_length=1, max_length=1000)
    observed_at: AwareDatetime


class ScheduleKind(StrEnum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduleTrigger(ContractModel):
    """Scheduler-neutral trigger; exactly one kind-specific field is populated."""

    kind: ScheduleKind
    run_at: AwareDatetime | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    cron_expression: str | None = Field(default=None, min_length=9, max_length=200)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)

    @model_validator(mode="after")
    def exactly_one_trigger_shape_is_valid(self) -> "ScheduleTrigger":
        populated = sum(
            value is not None
            for value in (self.run_at, self.interval_seconds, self.cron_expression)
        )
        if populated != 1:
            raise ValueError("exactly one trigger field must be populated")
        expected = {
            ScheduleKind.ONCE: self.run_at,
            ScheduleKind.INTERVAL: self.interval_seconds,
            ScheduleKind.CRON: self.cron_expression,
        }
        if expected[self.kind] is None:
            raise ValueError(f"trigger field does not match schedule kind {self.kind}")
        return self


class ScheduledJobTemplate[JobPayloadT: ContractModel](ContractModel):
    """Recurring-job configuration without per-execution identity or cutoffs.

    The scheduler creates a fresh ``JobRequest`` (including request IDs, ``as_of`` and
    idempotency key) for every firing. Reusing a concrete ``JobRequest`` here would
    leak stale point-in-time context into later runs.
    """

    schema_version: Literal["1.0"] = "1.0"
    job_name: str = Field(
        pattern=r"^stock_assistant\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$"
    )
    payload: JobPayloadT
    actor_id: str = Field(default="local-scheduler", min_length=1, max_length=200)
    workspace_id: str = Field(default="default", min_length=1, max_length=200)
    queue_name: str = Field(default="default", pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    idempotency_scope: str = Field(min_length=8, max_length=200)
    execution_timeout_seconds: int = Field(default=300, ge=1, le=86_400)
    max_attempts: int = Field(default=3, ge=1, le=100)
    locale: str = Field(default="zh-CN", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)


class ScheduleCreateRequest[JobPayloadT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    schedule_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    job_template: ScheduledJobTemplate[JobPayloadT]
    trigger: ScheduleTrigger
    enabled: bool = True


class ScheduleUpdateRequest[JobPayloadT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    schedule_id: UUID
    expected_revision: int = Field(ge=1)
    schedule_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    job_template: ScheduledJobTemplate[JobPayloadT]
    trigger: ScheduleTrigger
    enabled: bool


class ScheduleDeleteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    schedule_id: UUID
    expected_revision: int = Field(ge=1)


class ScheduleDeleteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    schedule_id: UUID
    deleted: bool
    processed_at: AwareDatetime
    idempotent_replay: bool = False


class ScheduleLookup(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    schedule_id: UUID


class ScheduleQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)


class ScheduledJob[JobPayloadT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    schedule_id: UUID = Field(default_factory=uuid4)
    schedule_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    job_template: ScheduledJobTemplate[JobPayloadT]
    trigger: ScheduleTrigger
    enabled: bool
    next_run_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def update_must_follow_creation(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class ScheduleLookupResult[JobPayloadT: ContractModel](ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    schedule_id: UUID
    found: bool
    schedule: ScheduledJob[JobPayloadT] | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_and_visibility_must_be_consistent(self) -> Self:
        if self.found != (self.schedule is not None):
            raise ValueError("found must reflect whether a schedule is present")
        if self.schedule is not None:
            if self.schedule.schedule_id != self.schedule_id:
                raise ValueError("schedule must match schedule_id")
            if self.schedule.updated_at > self.as_of:
                raise ValueError("schedule cannot expose a revision recorded after as_of")
        return self
