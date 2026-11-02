"""Reproducible LoRA dataset, training-run and model-registry contracts."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import CursorPage, MutationContext, OperationContext
from stock_assistant.contracts.storage import StoredObjectRef


class DatasetPurpose(StrEnum):
    INSTRUCTION_SFT = "instruction_sft"
    FINANCIAL_DOMAIN_SFT = "financial_domain_sft"
    TIME_SERIES = "time_series"
    FUSION = "fusion"
    EVALUATION = "evaluation"


class DatasetSplitName(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class TrainingRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainingArtifactKind(StrEnum):
    LORA_ADAPTER = "lora_adapter"
    MERGED_MODEL = "merged_model"
    QUANTIZED_MODEL = "quantized_model"
    TOKENIZER = "tokenizer"
    TRAINING_CONFIG = "training_config"
    EVALUATION_REPORT = "evaluation_report"
    SAFE_LOG_SUMMARY = "safe_log_summary"


class ModelKind(StrEnum):
    GENERATIVE = "generative"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    TIME_SERIES = "time_series"
    FUSION = "fusion"


class QuantizationFormat(StrEnum):
    NONE = "none"
    INT8 = "int8"
    INT4 = "int4"
    GGUF = "gguf"
    GPTQ = "gptq"
    AWQ = "awq"


class ModelStage(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    SHADOW = "shadow"
    PAPER_RESEARCH = "paper_research"
    ARCHIVED = "archived"


class EvaluationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    DEGRADED = "degraded"


class DatasetSplitManifest(ContractModel):
    split: DatasetSplitName
    record_count: int = Field(ge=0)
    time_from: AwareDatetime
    time_until: AwareDatetime
    knowledge_cutoff: AwareDatetime
    label_available_until: AwareDatetime | None = None
    artifact: StoredObjectRef
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def split_range_and_hash_must_be_consistent(self) -> "DatasetSplitManifest":
        if self.time_until < self.time_from:
            raise ValueError("time_until cannot be earlier than time_from")
        if self.knowledge_cutoff < self.time_until:
            raise ValueError("knowledge_cutoff cannot be earlier than time_until")
        if (
            self.label_available_until is not None
            and self.label_available_until > self.knowledge_cutoff
        ):
            raise ValueError("labels must be available by knowledge_cutoff")
        if self.content_sha256 != self.artifact.content_sha256:
            raise ValueError("split hash must match the stored artifact hash")
        return self


class TrainingDatasetManifest(ContractModel):
    """Metadata-only manifest; raw examples stay in authorized object storage."""

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    purpose: DatasetPurpose
    created_at: AwareDatetime
    data_cutoff: AwareDatetime
    splits: list[DatasetSplitManifest] = Field(min_length=1, max_length=10)
    total_record_count: int = Field(ge=1)
    source_ids: list[str] = Field(min_length=1, max_length=1000)
    evidence_count: int = Field(ge=0)
    evidence_manifest: StoredObjectRef | None = None
    schema_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    rights_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_ids: list[str] = Field(min_length=1, max_length=100)
    preprocessing_version: str = Field(min_length=1, max_length=200)
    deduplication_version: str = Field(min_length=1, max_length=200)
    split_strategy: Literal["temporal"] = "temporal"
    purge_gap_minutes: int = Field(default=0, ge=0, le=525_600)
    embargo_minutes: int = Field(default=0, ge=0, le=525_600)
    training_authorized: Literal[True] = True

    @model_validator(mode="after")
    def manifest_counts_and_splits_must_be_consistent(self) -> "TrainingDatasetManifest":
        split_names = [item.split for item in self.splits]
        if len(set(split_names)) != len(split_names):
            raise ValueError("dataset splits must be unique")
        if sum(item.record_count for item in self.splits) != self.total_record_count:
            raise ValueError("split record counts must equal total_record_count")
        if self.created_at < self.data_cutoff:
            raise ValueError("created_at cannot be earlier than data_cutoff")
        if any(item.knowledge_cutoff > self.data_cutoff for item in self.splits):
            raise ValueError("split time range cannot extend beyond data_cutoff")
        if self.evidence_count > 0 and self.evidence_manifest is None:
            raise ValueError("evidence_manifest is required when evidence_count is positive")
        if self.evidence_count == 0 and self.evidence_manifest is not None:
            raise ValueError("evidence_manifest is only valid when evidence_count is positive")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must be unique")
        if len(set(self.license_ids)) != len(self.license_ids):
            raise ValueError("license_ids must be unique")
        ordered = {item.split: item for item in self.splits}
        train = ordered.get(DatasetSplitName.TRAIN)
        validation = ordered.get(DatasetSplitName.VALIDATION)
        test = ordered.get(DatasetSplitName.TEST)
        if self.purpose is not DatasetPurpose.EVALUATION:
            if train is None or validation is None or test is None:
                raise ValueError("training datasets require train, validation and test splits")
        if train is not None and validation is not None:
            purge_seconds = self.purge_gap_minutes * 60
            if (validation.time_from - train.time_until).total_seconds() < purge_seconds:
                raise ValueError("training and validation splits violate purge_gap_minutes")
            if (
                train.label_available_until is not None
                and train.label_available_until > validation.time_from
            ):
                raise ValueError("training labels cannot become available inside validation")
        if validation is not None and test is not None:
            embargo_seconds = self.embargo_minutes * 60
            if (test.time_from - validation.time_until).total_seconds() < embargo_seconds:
                raise ValueError("validation and test splits violate embargo_minutes")
            if (
                validation.label_available_until is not None
                and validation.label_available_until > test.time_from
            ):
                raise ValueError("validation labels cannot become available inside test")
        return self


class DatasetRegistrationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    manifest: TrainingDatasetManifest


class DatasetRegistryRecord(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    manifest: TrainingDatasetManifest
    registered_at: AwareDatetime
    revision: int = Field(ge=1)


class DatasetQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    dataset_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    purposes: list[DatasetPurpose] = Field(default_factory=list, max_length=20)
    created_since: AwareDatetime | None = None
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)


class DatasetRegistryPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    page: CursorPage[DatasetRegistryRecord]


class LoRAConfig(ContractModel):
    rank: int = Field(ge=1, le=1024)
    alpha: int = Field(ge=1, le=16_384)
    dropout: DecimalString = Field(default=Decimal("0.05"), ge=0, lt=1)
    target_modules: list[str] = Field(min_length=1, max_length=100)
    learning_rate: DecimalString = Field(gt=0, le=1)
    epochs: DecimalString = Field(gt=0, le=1000)
    per_device_batch_size: int = Field(ge=1, le=4096)
    gradient_accumulation_steps: int = Field(default=1, ge=1, le=4096)
    maximum_sequence_length: int = Field(ge=128, le=1_048_576)
    quantization: QuantizationFormat = QuantizationFormat.NONE

    @model_validator(mode="after")
    def target_modules_must_be_unique(self) -> "LoRAConfig":
        if len(set(self.target_modules)) != len(self.target_modules):
            raise ValueError("target_modules must be unique")
        return self


class TrainingRunRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    run_id: UUID = Field(default_factory=uuid4)
    dataset_id: UUID
    dataset_version: str = Field(min_length=1, max_length=200)
    base_model_id: UUID
    base_model_version: str = Field(min_length=1, max_length=200)
    config: LoRAConfig
    requested_at: AwareDatetime
    code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    environment_lock_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    random_seed: int = Field(ge=0, le=4_294_967_295)
    trainer: str = Field(min_length=1, max_length=100)
    trainer_version: str = Field(min_length=1, max_length=100)


class TrainingMetric(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: DecimalString
    split: DatasetSplitName | None = None
    step: int | None = Field(default=None, ge=0)
    observed_at: AwareDatetime


class TrainingArtifact(ContractModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    kind: TrainingArtifactKind
    reference: StoredObjectRef
    role: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    format: str = Field(pattern=r"^[a-z0-9][a-z0-9_.+-]{0,99}$")
    shard_index: int | None = Field(default=None, ge=0)
    shard_count: int | None = Field(default=None, ge=1)
    framework: str = Field(min_length=1, max_length=100)
    framework_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def shard_metadata_must_be_complete(self) -> "TrainingArtifact":
        if (self.shard_index is None) != (self.shard_count is None):
            raise ValueError("shard_index and shard_count must be provided together")
        if self.shard_index is not None and self.shard_count is not None:
            if self.shard_index >= self.shard_count:
                raise ValueError("shard_index must be lower than shard_count")
        return self


class TrainingRunRecord(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    status: TrainingRunStatus
    dataset_id: UUID
    dataset_version: str = Field(min_length=1, max_length=200)
    base_model_id: UUID
    base_model_version: str = Field(min_length=1, max_length=200)
    config: LoRAConfig
    requested_at: AwareDatetime
    random_seed: int = Field(ge=0, le=4_294_967_295)
    trainer: str = Field(min_length=1, max_length=100)
    trainer_version: str = Field(min_length=1, max_length=100)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    metrics: list[TrainingMetric] = Field(default_factory=list, max_length=1000)
    metrics_artifact: StoredObjectRef | None = None
    artifacts: list[TrainingArtifact] = Field(default_factory=list, max_length=100)
    failure_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    safe_failure_message: str | None = Field(default=None, max_length=1000)
    code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    environment_lock_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def training_state_must_be_consistent(self) -> "TrainingRunRecord":
        terminal = {
            TrainingRunStatus.SUCCEEDED,
            TrainingRunStatus.DEGRADED,
            TrainingRunStatus.FAILED,
            TrainingRunStatus.CANCELLED,
        }
        if self.completed_at is not None and self.started_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot be earlier than started_at")
        if self.started_at is not None and self.started_at < self.requested_at:
            raise ValueError("started_at cannot be earlier than requested_at")
        if self.status in terminal and self.completed_at is None:
            raise ValueError("completed_at is required for terminal training states")
        if self.status is TrainingRunStatus.FAILED and self.failure_code is None:
            raise ValueError("failure_code is required for failed training runs")
        if self.status is not TrainingRunStatus.FAILED and self.failure_code is not None:
            raise ValueError("failure_code is only valid for failed training runs")
        return self


class TrainingRunLookup(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    run_id: UUID


class TrainingRunLookupResult(ContractModel):
    """Explicit lookup outcome with point-in-time visibility guarantees."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    found: bool
    run: TrainingRunRecord | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_and_visibility_must_be_consistent(self) -> "TrainingRunLookupResult":
        if self.found != (self.run is not None):
            raise ValueError("found must reflect whether a training run is present")
        if self.run is not None:
            if self.run.run_id != self.run_id:
                raise ValueError("training run must match run_id")
            visible_times = (
                self.run.requested_at,
                self.run.started_at,
                self.run.completed_at,
            )
            if any(value is not None and value > self.as_of for value in visible_times):
                raise ValueError("training lookup cannot expose state recorded after as_of")
        return self


class TrainingRunCancelRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    run_id: UUID
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class ModelCapability(ContractModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    contract_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    maximum_context_tokens: int | None = Field(default=None, ge=1)
    local_only: bool = True


class ModelManifest(ContractModel):
    """Immutable model identity, lineage, artifacts and safety capabilities."""

    schema_version: Literal["1.0"] = "1.0"
    model_id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    model_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    kind: ModelKind
    base_model_id: UUID | None = None
    training_run_id: UUID | None = None
    dataset_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    artifacts: list[TrainingArtifact] = Field(min_length=1, max_length=100)
    capabilities: list[ModelCapability] = Field(min_length=1, max_length=100)
    quantization: QuantizationFormat
    created_at: AwareDatetime
    training_cutoff: AwareDatetime | None = None
    code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    environment_lock_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_ids: list[str] = Field(min_length=1, max_length=100)
    rights_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    paper_only: Literal[True] = True

    @model_validator(mode="after")
    def model_manifest_must_have_unique_members(self) -> "ModelManifest":
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(set(capability_ids)) != len(capability_ids):
            raise ValueError("model capabilities must be unique")
        artifact_ids = [item.artifact_id for item in self.artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("model artifact IDs must be unique")
        artifact_locations = [
            (item.reference.namespace, item.reference.object_key, item.reference.version_id)
            for item in self.artifacts
        ]
        if len(set(artifact_locations)) != len(artifact_locations):
            raise ValueError("model artifact locations must be unique")
        if self.training_run_id is not None and not self.dataset_ids:
            raise ValueError("trained models must reference at least one dataset")
        if self.training_cutoff is not None and self.training_cutoff > self.created_at:
            raise ValueError("training_cutoff cannot be later than created_at")
        return self


class EvaluationMetric(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: DecimalString
    threshold: DecimalString | None = None
    higher_is_better: bool
    passed: bool


class ModelEvaluationReport(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    evaluation_id: UUID = Field(default_factory=uuid4)
    model_id: UUID
    model_version: str = Field(min_length=1, max_length=200)
    evaluation_suite: str = Field(min_length=1, max_length=200)
    suite_version: str = Field(min_length=1, max_length=200)
    dataset_id: UUID
    data_cutoff: AwareDatetime
    evaluated_at: AwareDatetime
    outcome: EvaluationOutcome
    metrics: list[EvaluationMetric] = Field(min_length=1, max_length=1000)
    leakage_check_passed: bool
    schema_compliance_passed: bool
    grounding_check_passed: bool
    evaluator_code_revision: str = Field(pattern=r"^[a-f0-9]{40}$")

    @model_validator(mode="after")
    def evaluation_outcome_must_match_gates(self) -> "ModelEvaluationReport":
        if self.evaluated_at < self.data_cutoff:
            raise ValueError("evaluated_at cannot be earlier than data_cutoff")
        gates = [
            self.leakage_check_passed,
            self.schema_compliance_passed,
            self.grounding_check_passed,
            all(metric.passed for metric in self.metrics),
        ]
        if self.outcome is EvaluationOutcome.PASSED and not all(gates):
            raise ValueError("passed evaluation requires every gate and metric to pass")
        return self


class ModelRegistryRecord(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    manifest: ModelManifest
    stage: ModelStage
    revision: int = Field(ge=1)
    registered_at: AwareDatetime
    stage_changed_at: AwareDatetime
    evaluation_ids: list[UUID] = Field(default_factory=list, max_length=1000)


class ModelRegistrationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    manifest: ModelManifest
    initial_stage: Literal[ModelStage.CANDIDATE] = ModelStage.CANDIDATE


class ModelQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    model_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    kinds: list[ModelKind] = Field(default_factory=list, max_length=20)
    stages: list[ModelStage] = Field(default_factory=list, max_length=20)
    capability_ids: list[str] = Field(default_factory=list, max_length=100)
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)


class ModelRegistryPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    page: CursorPage[ModelRegistryRecord]


class ModelPromotionRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    model_id: UUID
    expected_stage: ModelStage
    target_stage: ModelStage
    expected_revision: int = Field(ge=1)
    evaluation_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def stage_transition_must_be_safe(self) -> "ModelPromotionRequest":
        if self.expected_stage is ModelStage.CANDIDATE:
            allowed_targets = {ModelStage.VALIDATED, ModelStage.ARCHIVED}
        elif self.expected_stage is ModelStage.VALIDATED:
            allowed_targets = {
                ModelStage.SHADOW,
                ModelStage.PAPER_RESEARCH,
                ModelStage.ARCHIVED,
            }
        elif self.expected_stage is ModelStage.SHADOW:
            allowed_targets = {ModelStage.PAPER_RESEARCH, ModelStage.ARCHIVED}
        elif self.expected_stage is ModelStage.PAPER_RESEARCH:
            allowed_targets = {ModelStage.ARCHIVED}
        else:
            allowed_targets = set()
        if self.target_stage not in allowed_targets:
            raise ValueError("requested model stage transition is not allowed")
        gated_stages = {
            ModelStage.VALIDATED,
            ModelStage.SHADOW,
            ModelStage.PAPER_RESEARCH,
        }
        if self.target_stage in gated_stages and not self.evaluation_ids:
            raise ValueError("target stage requires evaluation_ids")
        return self


class ModelPromotionResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    promoted: bool
    previous_stage: ModelStage
    current: ModelRegistryRecord
    decided_at: AwareDatetime
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def promotion_result_must_be_consistent(self) -> "ModelPromotionResult":
        stage_changed = self.previous_stage is not self.current.stage
        if self.promoted != stage_changed:
            raise ValueError("promoted must reflect whether the model stage changed")
        if self.current.stage is ModelStage.PAPER_RESEARCH and not self.current.evaluation_ids:
            raise ValueError("paper_research models require evaluation evidence")
        return self
