"""Typed generation, embedding, reranking and provider-capability contracts."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import OperationContext


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class PrivacyLevel(StrEnum):
    LOCAL_ONLY = "local_only"
    REDACTED_EXTERNAL = "redacted_external"
    PUBLIC = "public"


class ModelOperation(StrEnum):
    """Provider-neutral model operations used during routing and startup checks."""

    GENERATION = "generation"
    EMBEDDING = "embedding"
    RERANKING = "reranking"


class ModelProviderCapability(ContractModel):
    """Structured capability declaration; callers never probe adapters with ``hasattr``."""

    schema_version: Literal["1.0"] = "1.0"
    provider_name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    model: str = Field(min_length=1, max_length=200)
    adapter_version: str = Field(min_length=1, max_length=200)
    operations: list[ModelOperation] = Field(min_length=1, max_length=3)
    supported_privacy_levels: list[PrivacyLevel] = Field(min_length=1, max_length=3)
    external_processing: bool
    structured_output: bool = False
    maximum_batch_size: int = Field(default=1, ge=1, le=10_000)
    maximum_input_tokens: int | None = Field(default=None, ge=1)
    maximum_output_tokens: int | None = Field(default=None, ge=1)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=65_536)

    @model_validator(mode="after")
    def declaration_must_be_consistent(self) -> "ModelProviderCapability":
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("model operations must be unique")
        if len(set(self.supported_privacy_levels)) != len(self.supported_privacy_levels):
            raise ValueError("supported privacy levels must be unique")
        if self.external_processing and PrivacyLevel.LOCAL_ONLY in self.supported_privacy_levels:
            raise ValueError("external providers cannot declare local_only support")
        if ModelOperation.EMBEDDING in self.operations and self.embedding_dimensions is None:
            raise ValueError("embedding providers must declare embedding_dimensions")
        if (
            ModelOperation.EMBEDDING not in self.operations
            and self.embedding_dimensions is not None
        ):
            raise ValueError("embedding_dimensions is only valid for embedding providers")
        return self


_SCHEMA_MAX_KEYS = 128
_SCHEMA_MAX_BYTES = 64 * 1024
_PAYLOAD_MAX_KEYS = 256
_PAYLOAD_MAX_BYTES = 1024 * 1024


def _canonical_json(value: dict[str, JsonValue]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_json_object_size(
    value: dict[str, JsonValue], *, maximum_keys: int, maximum_bytes: int, label: str
) -> None:
    if len(value) > maximum_keys:
        raise ValueError(f"{label} cannot contain more than {maximum_keys} top-level keys")
    if len(_canonical_json(value)) > maximum_bytes:
        raise ValueError(f"{label} exceeds the {maximum_bytes}-byte serialized limit")


class PromptMessage(ContractModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=100_000)
    name: str | None = Field(default=None, max_length=100)


class GenerationRequest(ContractModel):
    """Schema-constrained model request with explicit evidence and privacy."""

    schema_version: Literal["1.0"] = "1.0"
    task_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    messages: list[PromptMessage] = Field(min_length=1, max_length=100)
    response_schema: dict[str, JsonValue] = Field(min_length=1, max_length=_SCHEMA_MAX_KEYS)
    response_schema_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    response_schema_namespace: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    response_schema_owner: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=500)
    privacy_level: PrivacyLevel = PrivacyLevel.LOCAL_ONLY
    context: OperationContext
    timeout_seconds: float = Field(default=30, gt=0, le=300)

    @model_validator(mode="after")
    def schema_must_be_bounded_and_match_hash(self) -> "GenerationRequest":
        _validate_json_object_size(
            self.response_schema,
            maximum_keys=_SCHEMA_MAX_KEYS,
            maximum_bytes=_SCHEMA_MAX_BYTES,
            label="response_schema",
        )
        actual_hash = hashlib.sha256(_canonical_json(self.response_schema)).hexdigest()
        if actual_hash != self.response_schema_hash:
            raise ValueError("response_schema_hash must match the canonical response_schema")
        return self


class GenerationResponse(ContractModel):
    """Provider-neutral result retaining reproducibility metadata."""

    schema_version: Literal["1.0"] = "1.0"
    payload: dict[str, JsonValue] = Field(max_length=_PAYLOAD_MAX_KEYS)
    payload_namespace: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    payload_owner: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    response_schema_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    adapter_version: str = Field(min_length=1, max_length=200)
    prompt_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    raw_response_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    finish_reason: str = Field(default="stop", min_length=1, max_length=100)
    schema_validated: Literal[True]
    degraded: bool = False

    @model_validator(mode="after")
    def payload_must_be_bounded(self) -> "GenerationResponse":
        _validate_json_object_size(
            self.payload,
            maximum_keys=_PAYLOAD_MAX_KEYS,
            maximum_bytes=_PAYLOAD_MAX_BYTES,
            label="payload",
        )
        return self


class EmbeddingRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    texts: list[str] = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    input_type: str = Field(default="document", pattern=r"^(document|query)$")
    privacy_level: PrivacyLevel = PrivacyLevel.LOCAL_ONLY
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    context: OperationContext


class EmbeddingResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    vectors: list[list[float]] = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    dimensions: int = Field(ge=1, le=65_536)
    input_hashes: list[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]]

    @model_validator(mode="after")
    def vectors_must_match_declared_shape(self) -> "EmbeddingResponse":
        if len(self.vectors) != len(self.input_hashes):
            raise ValueError("vectors and input_hashes must have equal length")
        if any(len(vector) != self.dimensions for vector in self.vectors):
            raise ValueError("every vector must match dimensions")
        return self


class RerankRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    query: str = Field(min_length=1, max_length=20_000)
    documents: list[str] = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=20, ge=1, le=500)
    privacy_level: PrivacyLevel = PrivacyLevel.LOCAL_ONLY
    rights_snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    context: OperationContext


class RerankHit(ContractModel):
    index: int = Field(ge=0)
    score: float


class RerankResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    hits: list[RerankHit] = Field(default_factory=list, max_length=500)
    model: str = Field(min_length=1, max_length=200)
