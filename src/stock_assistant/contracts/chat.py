"""Evidence-grounded chat intent, retrieval and answer contracts."""

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import (
    CursorPage,
    EntityRef,
    MutationContext,
    OperationContext,
)
from stock_assistant.contracts.events import EvidenceRef
from stock_assistant.contracts.intelligence import PrivacyLevel


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM_NOTICE = "system_notice"


class ChatIntent(StrEnum):
    PREMARKET_SUMMARY = "premarket_summary"
    HOLDING_NEWS = "holding_news"
    COMPANY_RESEARCH = "company_research"
    POLICY_IMPACT = "policy_impact"
    SECTOR_ROTATION = "sector_rotation"
    EVENT_EXPLANATION = "event_explanation"
    FORECAST_EXPLANATION = "forecast_explanation"
    MEMORY_COMPARISON = "memory_comparison"
    SYSTEM_STATUS = "system_status"
    GENERAL_RESEARCH = "general_research"
    UNSUPPORTED = "unsupported"


class ClaimNature(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    PROBABILITY_PREDICTION = "probability_prediction"
    SAFETY_NOTICE = "safety_notice"


class GroundingStatus(StrEnum):
    GROUNDED = "grounded"
    PARTIALLY_GROUNDED = "partially_grounded"
    ABSTAINED = "abstained"
    REJECTED = "rejected"


class ChatTurn(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    turn_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    role: ChatRole
    content: str = Field(min_length=1, max_length=100_000)
    created_at: AwareDatetime
    intent: ChatIntent | None = None
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    response_id: UUID | None = None


class ChatRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    session_id: UUID
    message_id: UUID = Field(default_factory=uuid4)
    message: str = Field(min_length=1, max_length=20_000)
    prior_turn_ids: list[UUID] = Field(default_factory=list, max_length=100)
    intent_hint: ChatIntent | None = None
    privacy_level: PrivacyLevel = PrivacyLevel.LOCAL_ONLY
    maximum_evidence_hits: int = Field(default=20, ge=1, le=100)


class IntentCandidate(ContractModel):
    intent: ChatIntent
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    rationale_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


class ChatIntentRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    session_id: UUID
    message: str = Field(min_length=1, max_length=20_000)
    recent_user_messages: list[str] = Field(default_factory=list, max_length=20)


class ChatIntentResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    selected_intent: ChatIntent
    candidates: list[IntentCandidate] = Field(min_length=1, max_length=20)
    classifier_version: str = Field(min_length=1, max_length=200)
    classified_at: AwareDatetime

    @model_validator(mode="after")
    def selected_intent_must_be_a_candidate(self) -> "ChatIntentResult":
        intents = [item.intent for item in self.candidates]
        if self.selected_intent not in intents:
            raise ValueError("selected_intent must appear in candidates")
        if len(set(intents)) != len(intents):
            raise ValueError("intent candidates must be unique")
        return self


class ChatRetrievalRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    session_id: UUID
    query_text: str = Field(min_length=1, max_length=20_000)
    intent: ChatIntent
    entities: list[EntityRef] = Field(default_factory=list, max_length=1000)
    event_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    policy_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    maximum_hits: int = Field(default=20, ge=1, le=100)
    minimum_relevance: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def retrieval_window_must_be_point_in_time_safe(self) -> "ChatRetrievalRequest":
        if self.since is not None and self.until is not None and self.until < self.since:
            raise ValueError("until cannot be earlier than since")
        if self.until is not None and self.until > self.context.as_of:
            raise ValueError("until cannot be later than context.as_of")
        return self


class ChatEvidenceHit(ContractModel):
    evidence: EvidenceRef
    excerpt: str = Field(min_length=1, max_length=4000)
    relevance: float = Field(ge=0, le=1, allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    authority_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    entities: list[EntityRef] = Field(default_factory=list, max_length=500)
    event_ids: list[UUID] = Field(default_factory=list, max_length=500)
    policy_ids: list[UUID] = Field(default_factory=list, max_length=500)


class ChatRetrievalResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    hits: list[ChatEvidenceHit] = Field(default_factory=list, max_length=100)
    as_of: AwareDatetime
    index_version: str = Field(min_length=1, max_length=200)
    embedding_model: str | None = Field(default=None, max_length=200)
    reranker_model: str | None = Field(default=None, max_length=200)
    degraded: bool = False
    source_gaps: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def hits_must_be_unique_and_visible(self) -> "ChatRetrievalResult":
        evidence_ids = [item.evidence.evidence_id for item in self.hits]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("retrieval hits must have unique evidence IDs")
        if any(item.evidence.first_seen_at > self.as_of for item in self.hits):
            raise ValueError("retrieval cannot expose evidence first seen after as_of")
        return self


class DraftClaim(ContractModel):
    claim_id: UUID = Field(default_factory=uuid4)
    nature: ClaimNature
    statement: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    prediction_horizon_minutes: int | None = Field(default=None, ge=1, le=525_600)

    @model_validator(mode="after")
    def claim_shape_must_match_nature(self) -> "DraftClaim":
        evidence_required = {
            ClaimNature.FACT,
            ClaimNature.INFERENCE,
            ClaimNature.PROBABILITY_PREDICTION,
        }
        if self.nature in evidence_required and not self.evidence_ids:
            raise ValueError("factual, inferential and predictive claims require evidence")
        if self.nature is ClaimNature.PROBABILITY_PREDICTION:
            if self.prediction_horizon_minutes is None:
                raise ValueError("predictions require prediction_horizon_minutes")
        elif self.prediction_horizon_minutes is not None:
            raise ValueError("prediction_horizon_minutes is only valid for predictions")
        return self


class ChatGroundingRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    session_id: UUID
    draft_text: str = Field(min_length=1, max_length=100_000)
    claims: list[DraftClaim] = Field(default_factory=list, max_length=500)
    available_evidence: list[ChatEvidenceHit] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def claim_evidence_must_be_available(self) -> "ChatGroundingRequest":
        available = {item.evidence.evidence_id for item in self.available_evidence}
        referenced = {evidence_id for claim in self.claims for evidence_id in claim.evidence_ids}
        if not referenced.issubset(available):
            raise ValueError("claims reference evidence outside the available evidence pack")
        return self


class GroundedClaim(ContractModel):
    claim: DraftClaim
    supported: bool
    support_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    validator_codes: list[str] = Field(default_factory=list, max_length=100)


class ChatGroundingResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    status: GroundingStatus
    safe_text: str = Field(min_length=1, max_length=100_000)
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=500)
    validator_version: str = Field(min_length=1, max_length=200)
    validated_at: AwareDatetime

    @model_validator(mode="after")
    def grounding_status_must_match_claims(self) -> "ChatGroundingResult":
        supported = [item.supported for item in self.claims]
        if self.status is GroundingStatus.GROUNDED:
            if not supported or not all(supported):
                raise ValueError("grounded status requires at least one fully supported claim")
        if self.status is GroundingStatus.PARTIALLY_GROUNDED:
            if not supported or all(supported) or not any(supported):
                raise ValueError("partially_grounded requires mixed claim support")
        if self.status in {GroundingStatus.ABSTAINED, GroundingStatus.REJECTED}:
            if any(supported):
                raise ValueError("abstained or rejected responses cannot contain supported claims")
        return self


class ChatCitation(ContractModel):
    marker: str = Field(pattern=r"^\[[1-9][0-9]*\]$")
    evidence_id: UUID


class ChatResponse(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    response_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    intent: ChatIntent
    status: GroundingStatus
    answer: str = Field(min_length=1, max_length=100_000)
    claims: list[GroundedClaim] = Field(default_factory=list, max_length=500)
    citations: list[ChatCitation] = Field(default_factory=list, max_length=1000)
    as_of: AwareDatetime
    generated_at: AwareDatetime
    model_version: str = Field(min_length=1, max_length=200)
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    degraded: bool = False
    is_paper_only: Literal[True] = True
    disclaimer: str = "本系统仅供个人信息整理与研究, 不构成证券投资建议、收益承诺或交易指令。"

    @model_validator(mode="after")
    def response_timeline_and_citations_must_be_consistent(self) -> "ChatResponse":
        if self.generated_at < self.as_of:
            raise ValueError("generated_at cannot be earlier than as_of")
        citation_ids = [item.evidence_id for item in self.citations]
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("citations must have unique evidence IDs")
        citation_markers = [item.marker for item in self.citations]
        if len(set(citation_markers)) != len(citation_markers):
            raise ValueError("citation markers must be unique")
        supported = [item.supported for item in self.claims]
        if self.status is GroundingStatus.GROUNDED:
            if not supported or not all(supported):
                raise ValueError("grounded responses require at least one fully supported claim")
        if self.status is GroundingStatus.PARTIALLY_GROUNDED:
            if not supported or all(supported) or not any(supported):
                raise ValueError("partially grounded responses require mixed claim support")
        if self.status in {GroundingStatus.ABSTAINED, GroundingStatus.REJECTED}:
            if any(supported):
                raise ValueError("abstained or rejected responses cannot contain supported claims")
        claim_evidence = {
            evidence_id
            for grounded_claim in self.claims
            for evidence_id in grounded_claim.claim.evidence_ids
        }
        if not claim_evidence.issubset(set(citation_ids)):
            raise ValueError("every claim evidence ID must have a citation")
        return self


class ChatTurnWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    turn: ChatTurn


class ChatHistoryQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    session_id: UUID
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=50, ge=1, le=500)


class ChatHistoryPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    page: CursorPage[ChatTurn]
