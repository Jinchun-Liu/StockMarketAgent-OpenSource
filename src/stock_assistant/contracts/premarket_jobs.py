"""Concrete R1 wire contracts for the two enabled premarket jobs."""

from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.ingestion import (
    CapabilityName,
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.jobs import JobRequest, JobResult, JobStatus
from stock_assistant.contracts.portfolio import ActiveTargetRevisionRef

PREMARKET_INGEST_JOB_NAME: Final = "stock_assistant.ingestion.premarket_ingest.v1"
PREMARKET_BRIEF_JOB_NAME: Final = "stock_assistant.briefings.premarket_generate.v1"

Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
ReasonCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]


def _validate_window(window_start: AwareDatetime, window_end: AwareDatetime) -> None:
    if window_end <= window_start:
        raise ValueError("window_end must be later than window_start")


class PremarketIngestJobPayload(ContractModel):
    """Small, replay-safe ingestion intent; source content never crosses the queue."""

    schema_version: Literal["1.0"] = "1.0"
    active_target: ActiveTargetRevisionRef
    window_start: AwareDatetime
    window_end: AwareDatetime
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    source_adapter_version: str = Field(min_length=1, max_length=100)
    rights_snapshot_hash: Sha256Hex
    ruleset_version: str = Field(min_length=1, max_length=200)
    max_records: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def observation_window_must_be_valid(self) -> Self:
        _validate_window(self.window_start, self.window_end)
        return self


class PremarketIngestTaskResult(ContractModel):
    """Bounded ingestion summary; evidence and source bodies remain in storage."""

    schema_version: Literal["1.0"] = "1.0"
    active_target: ActiveTargetRevisionRef
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    window_start: AwareDatetime
    window_end: AwareDatetime
    records_fetched: int = Field(ge=0)
    evidence_stored: int = Field(ge=0)
    event_revisions_stored: int = Field(ge=0)
    data_manifest_hash: Sha256Hex | None = None
    next_cursor: str | None = Field(default=None, max_length=4096)
    source_watermark: AwareDatetime | None = None
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=100)
    idempotent_replay: bool = False

    @model_validator(mode="after")
    def summary_must_be_bounded_and_consistent(self) -> Self:
        _validate_window(self.window_start, self.window_end)
        if self.source_watermark is not None and self.source_watermark > self.window_end:
            raise ValueError("source_watermark cannot be later than window_end")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be unique")
        return self


class PremarketIngestJobRequest(JobRequest[PremarketIngestJobPayload]):
    """Fully specialized queue request for ``premarket_ingest`` v1."""

    job_name: Literal["stock_assistant.ingestion.premarket_ingest.v1"] = PREMARKET_INGEST_JOB_NAME

    @model_validator(mode="after")
    def window_must_be_visible_at_job_cutoff(self) -> Self:
        if self.payload.window_end > self.context.as_of:
            raise ValueError("window_end cannot be later than context.as_of")
        return self


class PremarketIngestJobResult(JobResult[PremarketIngestTaskResult]):
    """Terminal and in-progress result with explicit degraded/skip semantics."""

    @model_validator(mode="after")
    def outcome_must_match_status(self) -> Self:
        if self.result is None:
            return self
        if self.status is JobStatus.SUCCEEDED:
            if self.result.reason_codes:
                raise ValueError("succeeded results cannot contain reason codes")
            if self.result.data_manifest_hash is None:
                raise ValueError("succeeded results require a data manifest")
        elif self.status is JobStatus.DEGRADED:
            if not self.result.reason_codes:
                raise ValueError("degraded results require a reason code")
            if self.result.data_manifest_hash is None:
                raise ValueError("degraded results require a data manifest")
        elif self.status is JobStatus.SKIPPED:
            if not self.result.reason_codes:
                raise ValueError("skipped results require a reason code")
            if any(
                (
                    self.result.records_fetched,
                    self.result.evidence_stored,
                    self.result.event_revisions_stored,
                )
            ):
                raise ValueError("skipped results cannot report persisted input")
            if any(
                value is not None
                for value in (
                    self.result.data_manifest_hash,
                    self.result.next_cursor,
                    self.result.source_watermark,
                )
            ):
                raise ValueError("skipped results cannot advance data state")
        return self


class PremarketIngestJobPayloadV1_1(PremarketIngestJobPayload):
    """R1 1.1 ingestion intent with capability and runtime-mode cursor isolation."""

    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    capability: CapabilityName
    runtime_mode: SourceRuntimeMode
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "capability and runtime_mode select an isolated source cursor stream"
            ]
        }
    )


class PremarketIngestTaskResultV1_1(PremarketIngestTaskResult):
    """R1 1.1 persisted summary with auditable source provenance."""

    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    capability: CapabilityName
    runtime_mode: SourceRuntimeMode
    provenance: SourceProvenance
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "provenance.runtime_mode equals runtime_mode",
            ]
        }
    )

    @model_validator(mode="after")
    def result_provenance_must_match_runtime(self) -> Self:
        if self.provenance.runtime_mode is not self.runtime_mode:
            raise ValueError("result provenance runtime_mode must match the task result")
        return self


class PremarketIngestJobRequestV1_1(JobRequest[PremarketIngestJobPayloadV1_1]):
    """Compatible 1.1 request for the unchanged R1 ingestion task name."""

    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    job_name: Literal["stock_assistant.ingestion.premarket_ingest.v1"] = PREMARKET_INGEST_JOB_NAME
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": ["payload.window_end is not later than context.as_of"]
        }
    )

    @model_validator(mode="after")
    def window_must_be_visible_at_job_cutoff(self) -> Self:
        if self.payload.window_end > self.context.as_of:
            raise ValueError("window_end cannot be later than context.as_of")
        return self


class PremarketIngestJobResultV1_1(JobResult[PremarketIngestTaskResultV1_1]):
    """Compatible 1.1 result for the unchanged R1 ingestion task name."""

    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "status and result or failure fields obey the version 1.0 job outcome rules"
            ]
        }
    )

    @model_validator(mode="after")
    def outcome_must_match_status(self) -> Self:
        if self.result is None:
            return self
        if self.status is JobStatus.SUCCEEDED:
            if self.result.reason_codes:
                raise ValueError("succeeded results cannot contain reason codes")
            if self.result.data_manifest_hash is None:
                raise ValueError("succeeded results require a data manifest")
        elif self.status is JobStatus.DEGRADED:
            if not self.result.reason_codes:
                raise ValueError("degraded results require a reason code")
            if self.result.data_manifest_hash is None:
                raise ValueError("degraded results require a data manifest")
        elif self.status is JobStatus.SKIPPED:
            if not self.result.reason_codes:
                raise ValueError("skipped results require a reason code")
            if any(
                (
                    self.result.records_fetched,
                    self.result.evidence_stored,
                    self.result.event_revisions_stored,
                )
            ):
                raise ValueError("skipped results cannot report persisted input")
            if any(
                value is not None
                for value in (
                    self.result.data_manifest_hash,
                    self.result.next_cursor,
                    self.result.source_watermark,
                )
            ):
                raise ValueError("skipped results cannot advance data state")
        return self


class PremarketIngestJobPayloadV1_2(PremarketIngestJobPayload):
    """R1 1.2 intent with isolated capability mode and optional replay session."""

    schema_version: Literal["1.2"] = "1.2"  # type: ignore[assignment]
    capability: CapabilityName
    runtime_mode: SourceRuntimeModeV1_2
    replay_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$",
    )


class PremarketIngestTaskResultV1_2(PremarketIngestTaskResult):
    """R1 1.2 persisted summary with complete source provenance."""

    schema_version: Literal["1.2"] = "1.2"  # type: ignore[assignment]
    capability: CapabilityName
    runtime_mode: SourceRuntimeModeV1_2
    replay_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$",
    )
    provenance: SourceProvenanceV1_2

    @model_validator(mode="after")
    def result_provenance_must_match_runtime(self) -> Self:
        if self.provenance.runtime_mode is not self.runtime_mode:
            raise ValueError("result provenance runtime_mode must match the task result")
        return self


class PremarketIngestJobRequestV1_2(JobRequest[PremarketIngestJobPayloadV1_2]):
    """Compatible 1.2 request for the unchanged R1 ingestion task name."""

    schema_version: Literal["1.2"] = "1.2"  # type: ignore[assignment]
    job_name: Literal["stock_assistant.ingestion.premarket_ingest.v1"] = PREMARKET_INGEST_JOB_NAME

    @model_validator(mode="after")
    def window_must_be_visible_at_job_cutoff(self) -> Self:
        if self.payload.window_end > self.context.as_of:
            raise ValueError("window_end cannot be later than context.as_of")
        return self


class PremarketIngestJobResultV1_2(JobResult[PremarketIngestTaskResultV1_2]):
    """Compatible 1.2 result for the unchanged R1 ingestion task name."""

    schema_version: Literal["1.2"] = "1.2"  # type: ignore[assignment]

    @model_validator(mode="after")
    def outcome_must_match_status(self) -> Self:
        if self.result is None:
            return self
        if self.status is JobStatus.SUCCEEDED:
            if self.result.reason_codes:
                raise ValueError("succeeded results cannot contain reason codes")
            if self.result.data_manifest_hash is None:
                raise ValueError("succeeded results require a data manifest")
        elif self.status is JobStatus.DEGRADED:
            if not self.result.reason_codes:
                raise ValueError("degraded results require a reason code")
            if self.result.data_manifest_hash is None:
                raise ValueError("degraded results require a data manifest")
        elif self.status is JobStatus.SKIPPED:
            if not self.result.reason_codes:
                raise ValueError("skipped results require a reason code")
            if any(
                (
                    self.result.records_fetched,
                    self.result.evidence_stored,
                    self.result.event_revisions_stored,
                )
            ):
                raise ValueError("skipped results cannot report persisted input")
            if any(
                value is not None
                for value in (
                    self.result.data_manifest_hash,
                    self.result.next_cursor,
                    self.result.source_watermark,
                )
            ):
                raise ValueError("skipped results cannot advance data state")
        return self


class PremarketBriefJobPayload(ContractModel):
    """Deterministic briefing intent resolved from persisted R1 evidence."""

    schema_version: Literal["1.0"] = "1.0"
    active_target: ActiveTargetRevisionRef
    window_start: AwareDatetime
    window_end: AwareDatetime
    source_ids: list[str] = Field(min_length=1, max_length=100)
    ruleset_version: str = Field(min_length=1, max_length=200)
    comparison_briefing_id: UUID | None = None
    maximum_items: int = Field(default=100, ge=1, le=200)
    minimum_materiality: float = Field(default=0.4, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def scope_and_window_must_be_valid(self) -> Self:
        _validate_window(self.window_start, self.window_end)
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must be unique")
        if any(
            not source_id
            or len(source_id) > 100
            or not source_id.replace("_", "").replace("-", "").isalnum()
            for source_id in self.source_ids
        ):
            raise ValueError("source_ids must contain bounded source identifiers")
        return self


class PremarketBriefTaskResult(ContractModel):
    """Reference-only summary of a persisted deterministic briefing."""

    schema_version: Literal["1.0"] = "1.0"
    active_target: ActiveTargetRevisionRef
    window_start: AwareDatetime
    window_end: AwareDatetime
    briefing_id: UUID | None = None
    generated_at: AwareDatetime | None = None
    event_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    data_manifest_hash: Sha256Hex | None = None
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=100)
    idempotent_replay: bool = False

    @model_validator(mode="after")
    def summary_must_be_consistent(self) -> Self:
        _validate_window(self.window_start, self.window_end)
        if (self.briefing_id is None) != (self.generated_at is None):
            raise ValueError("briefing_id and generated_at must be populated together")
        if self.generated_at is not None and self.generated_at < self.window_end:
            raise ValueError("generated_at cannot be earlier than window_end")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be unique")
        return self


class PremarketBriefJobRequest(JobRequest[PremarketBriefJobPayload]):
    """Fully specialized queue request for ``premarket_generate`` v1."""

    job_name: Literal["stock_assistant.briefings.premarket_generate.v1"] = PREMARKET_BRIEF_JOB_NAME

    @model_validator(mode="after")
    def window_must_be_visible_at_job_cutoff(self) -> Self:
        if self.payload.window_end > self.context.as_of:
            raise ValueError("window_end cannot be later than context.as_of")
        return self


class PremarketBriefJobResult(JobResult[PremarketBriefTaskResult]):
    """Briefing job result without embedding the briefing body in queue state."""

    @model_validator(mode="after")
    def outcome_must_match_status(self) -> Self:
        if self.result is None:
            return self
        if self.status is JobStatus.SUCCEEDED:
            if self.result.reason_codes:
                raise ValueError("succeeded results cannot contain reason codes")
            if self.result.briefing_id is None or self.result.data_manifest_hash is None:
                raise ValueError("succeeded results require a briefing and data manifest")
        elif self.status is JobStatus.DEGRADED:
            if not self.result.reason_codes:
                raise ValueError("degraded results require a reason code")
            if self.result.briefing_id is None or self.result.data_manifest_hash is None:
                raise ValueError("degraded results require a briefing and data manifest")
        elif self.status is JobStatus.SKIPPED:
            if not self.result.reason_codes:
                raise ValueError("skipped results require a reason code")
            if self.result.briefing_id is not None or self.result.data_manifest_hash is not None:
                raise ValueError("skipped results cannot publish a briefing")
            if self.result.event_count or self.result.evidence_count:
                raise ValueError("skipped results cannot report consumed evidence")
        if (
            self.result.generated_at is not None
            and self.completed_at is not None
            and self.result.generated_at > self.completed_at
        ):
            raise ValueError("briefing generation cannot be later than job completion")
        return self
