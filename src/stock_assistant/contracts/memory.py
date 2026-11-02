"""Contracts for short-term, episodic and semantic memory."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import EntityRef, MutationContext, OperationContext


class MemoryTier(StrEnum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class MemoryChangeType(StrEnum):
    NEW = "new"
    UNCHANGED = "unchanged"
    UPDATE = "update"
    CORRECTION = "correction"
    CONTRADICTION = "contradiction"


class MemoryAttribute(ContractModel):
    """Typed extension field that avoids unbounded metadata dictionaries."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: JsonValue


class MemoryRecord(ContractModel):
    """Normalized memory unit; large originals remain in the object store."""

    schema_version: Literal["1.0"] = "1.0"
    memory_id: UUID = Field(default_factory=uuid4)
    tier: MemoryTier
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    content: str = Field(min_length=1, max_length=200_000)
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    object_key: str | None = Field(default=None, max_length=1024)
    recorded_at: AwareDatetime
    source_time: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    revision: int = Field(default=1, ge=1)
    entities: list[EntityRef] = Field(default_factory=list, max_length=500)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=500)
    attributes: list[MemoryAttribute] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def lifetime_and_attributes_must_be_valid(self) -> "MemoryRecord":
        if self.expires_at is not None and self.expires_at <= self.recorded_at:
            raise ValueError("expires_at must be later than recorded_at")
        if len({item.name for item in self.attributes}) != len(self.attributes):
            raise ValueError("memory attribute names must be unique")
        return self


class MemoryWriteRequest(ContractModel):
    context: MutationContext
    record: MemoryRecord
    expected_revision: int | None = Field(default=None, ge=1)


class ShortTermMemoryWriteRequest(MemoryWriteRequest):
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def record_must_be_short_term(self) -> "ShortTermMemoryWriteRequest":
        if self.record.tier is not MemoryTier.SHORT_TERM:
            raise ValueError("short-term memory port accepts only short_term records")
        return self


class EpisodicMemoryWriteRequest(MemoryWriteRequest):
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def record_must_be_episodic(self) -> "EpisodicMemoryWriteRequest":
        if self.record.tier is not MemoryTier.EPISODIC:
            raise ValueError("episodic memory port accepts only episodic records")
        return self


class MemoryLookupRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    memory_id: UUID


class MemoryLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    record: MemoryRecord | None = None
    found: bool
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_and_visibility_must_be_consistent(self) -> "MemoryLookupResult":
        if self.found != (self.record is not None):
            raise ValueError("found must reflect whether record is present")
        if self.record is not None and self.record.recorded_at > self.as_of:
            raise ValueError("lookup cannot return a record written after as_of")
        return self


class MemoryClearNamespaceRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")


class MemoryClearNamespaceResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    namespace: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    cleared_count: int = Field(ge=0)
    completed_at: AwareDatetime


class MemoryDeleteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    memory_id: UUID


class MemoryDeleteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    memory_id: UUID
    deleted: bool
    completed_at: AwareDatetime


class MemoryQuery(ContractModel):
    """Hybrid retrieval query shared by vector and lexical implementations."""

    context: OperationContext
    query_text: str = Field(min_length=1, max_length=20_000)
    tiers: list[MemoryTier] = Field(default_factory=lambda: list(MemoryTier), min_length=1)
    namespaces: list[str] = Field(default_factory=list, max_length=100)
    entities: list[EntityRef] = Field(default_factory=list, max_length=500)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    exclude_memory_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    limit: int = Field(default=20, ge=1, le=500)
    minimum_score: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def query_time_range_must_be_ordered(self) -> "MemoryQuery":
        if self.since is not None and self.until is not None and self.until < self.since:
            raise ValueError("until cannot be earlier than since")
        if self.until is not None and self.until > self.context.as_of:
            raise ValueError("until cannot be later than context.as_of")
        if self.since is not None and self.since > self.context.as_of:
            raise ValueError("since cannot be later than context.as_of")
        return self


class MemoryHit(ContractModel):
    record: MemoryRecord
    score: float = Field(ge=0, le=1)
    vector_score: float | None = Field(default=None, ge=0, le=1)
    lexical_score: float | None = Field(default=None, ge=0, le=1)
    recency_score: float | None = Field(default=None, ge=0, le=1)


class MemoryQueryResult(ContractModel):
    hits: list[MemoryHit] = Field(default_factory=list, max_length=500)
    as_of: AwareDatetime
    embedding_model: str | None = Field(default=None, max_length=200)
    index_version: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def records_must_be_visible_at_cutoff(self) -> "MemoryQueryResult":
        if any(item.record.recorded_at > self.as_of for item in self.hits):
            raise ValueError("memory result cannot contain records recorded after as_of")
        return self


class MemoryCompareRequest(ContractModel):
    context: OperationContext
    current: MemoryRecord
    candidate_ids: list[UUID] = Field(default_factory=list, max_length=500)
    similarity_threshold: float = Field(default=0.9, ge=0, le=1)
    materiality_threshold: float = Field(default=0.6, ge=0, le=1)


class MemoryComparison(ContractModel):
    current_memory_id: UUID
    previous_memory_id: UUID | None = None
    change_type: MemoryChangeType
    similarity: float = Field(ge=0, le=1)
    materiality: float = Field(ge=0, le=1)
    changed_fields: list[str] = Field(default_factory=list, max_length=100)
    summary: str = Field(min_length=1, max_length=2000)
    should_notify: bool


class RulePolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SemanticRule(ContractModel):
    """Versioned, auditable semantic-memory rule."""

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,199}$")
    version: int = Field(ge=1)
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    polarity: RulePolarity
    keywords: list[str] = Field(min_length=1, max_length=100)
    event_types: list[str] = Field(default_factory=list, max_length=100)
    weight: float = Field(ge=0, le=1)
    enabled: bool = True
    effective_from: AwareDatetime
    effective_until: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime

    @model_validator(mode="after")
    def effective_range_must_be_ordered(self) -> "SemanticRule":
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be later than effective_from")
        if self.revision_known_at < self.first_seen_at:
            raise ValueError("revision_known_at cannot be earlier than first_seen_at")
        return self


class SemanticRuleQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    rule_ids: list[str] = Field(default_factory=list, max_length=1000)
    enabled_only: bool = True


class SemanticRuleQueryResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    rules: list[SemanticRule] = Field(default_factory=list, max_length=10_000)
    as_of: AwareDatetime

    @model_validator(mode="after")
    def rules_must_be_known_at_cutoff(self) -> "SemanticRuleQueryResult":
        if any(item.revision_known_at > self.as_of for item in self.rules):
            raise ValueError("semantic result cannot contain rule revisions known after as_of")
        rule_keys = [(item.rule_id, item.version) for item in self.rules]
        if len(set(rule_keys)) != len(rule_keys):
            raise ValueError("semantic result cannot contain duplicate rule versions")
        return self


class SemanticRuleMatchRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    record: MemoryRecord

    @model_validator(mode="after")
    def record_must_be_visible_at_cutoff(self) -> "SemanticRuleMatchRequest":
        if self.record.recorded_at > self.context.as_of:
            raise ValueError("semantic matching cannot use a future memory record")
        return self


class RuleMatch(ContractModel):
    rule_id: str
    rule_version: int = Field(ge=1)
    matched_terms: list[str] = Field(min_length=1, max_length=100)
    score: float = Field(ge=-1, le=1)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=500)


class SemanticRuleMatchResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    matches: list[RuleMatch] = Field(default_factory=list, max_length=10_000)
    as_of: AwareDatetime
    rule_set_version: str = Field(min_length=1, max_length=200)
