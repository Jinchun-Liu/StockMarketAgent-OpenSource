"""Vendor-neutral contracts for controlled research source discovery."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import AwareDatetime, ConfigDict, Field, HttpUrl, field_validator, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.formal_disclosure import (
    CandidateVerificationOutcome,
    DirectAttachmentCandidate,
    FormalDocumentVerification,
    OfficialDocumentIndexBinding,
)

RESEARCH_WEB_SOURCE_ID = "governed_web"
RESEARCH_WEB_SOURCE_CAPABILITY = "research_web_page"
RESEARCH_CONTEXT_OMISSION = (
    "\n[Intervening source text omitted; original PDF page numbers retained]\n"
)


class SourceDiscoveryStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NO_RESULTS = "no_results"
    QUOTA_EXHAUSTED = "quota_exhausted"
    FAILED = "failed"


class ResearchWebQueryPlanItem(ContractModel):
    query: str = Field(min_length=3, max_length=300)
    missing_fact_keys: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def query_cannot_contain_a_url(self) -> Self:
        if "://" in self.query or any(
            not value or len(value) > 100 for value in self.missing_fact_keys
        ):
            raise ValueError("web plan queries cannot contain URLs or invalid fact keys")
        return self


class ResearchWebQueryPlanDraft(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    queries: list[ResearchWebQueryPlanItem] = Field(min_length=1, max_length=3)


class ResearchWebNormalizedFactDraft(ContractModel):
    missing_fact_key: str = Field(min_length=1, max_length=100)
    source_excerpt: str = Field(min_length=1, max_length=2000)
    value: str = Field(default="", max_length=200)
    unit: str = Field(default="", max_length=50)
    source_location: str = Field(default="", max_length=300)
    period: str = Field(default="", max_length=100)


class ResearchWebNormalizationDraft(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    facts: list[ResearchWebNormalizedFactDraft] = Field(max_length=5)


class ResearchDataAuditVerdict(ContractModel):
    fact_key: str = Field(min_length=1, max_length=100)
    outcome: Literal["passed", "rejected", "uncertain"]
    reason_code: Literal[
        "SUPPORTED",
        "SUBJECT_MISMATCH",
        "BUSINESS_MISMATCH",
        "VALUE_MISMATCH",
        "UNIT_MISMATCH",
        "PERIOD_MISMATCH",
        "EXCERPT_MISMATCH",
        "SOURCE_CONFLICT",
        "INSUFFICIENT_EVIDENCE",
    ]
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def outcome_matches_reason(self) -> Self:
        if (self.outcome == "passed") != (self.reason_code == "SUPPORTED"):
            raise ValueError("data audit outcome and reason conflict")
        return self


class ResearchDataAuditDraft(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    verdicts: list[ResearchDataAuditVerdict] = Field(min_length=1, max_length=5)


class SourceDiscoveryQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    query: str = Field(min_length=3, max_length=300)
    missing_fact_keys: list[str] = Field(min_length=1, max_length=20)
    include_domains: list[str] = Field(min_length=1, max_length=20)
    start_date: AwareDatetime | None = None
    end_date: AwareDatetime
    max_results: int = Field(default=5, ge=1, le=5)

    @model_validator(mode="after")
    def query_is_bounded_by_the_pit_cutoff(self) -> Self:
        if self.end_date > self.context.as_of:
            raise ValueError("discovery end_date cannot exceed the PIT cutoff")
        if self.start_date is not None and self.start_date > self.end_date:
            raise ValueError("discovery start_date cannot exceed end_date")
        if len(set(self.include_domains)) != len(self.include_domains):
            raise ValueError("discovery domains must be unique")
        if any(
            domain != domain.casefold()
            or domain.startswith(".")
            or domain.endswith(".")
            or "/" in domain
            for domain in self.include_domains
        ):
            raise ValueError("discovery domains must be normalized DNS names")
        return self


class SourceDiscoveryCandidate(ContractModel):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    candidate_id: UUID = Field(default_factory=uuid4)
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    domain: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
    published_at: AwareDatetime | None = None
    discovery_score: DecimalString = Field(ge=0, le=1)
    official_index: OfficialDocumentIndexBinding | None = None
    direct_attachment: DirectAttachmentCandidate | None = None

    @model_validator(mode="after")
    def formal_metadata_requires_new_version(self) -> Self:
        if self.schema_version == "1.0" and (
            self.official_index is not None or self.direct_attachment is not None
        ):
            raise ValueError("formal discovery metadata requires candidate 1.1")
        if self.official_index is not None and self.official_index.identity.canonical_url != str(
            self.url
        ):
            raise ValueError("index metadata must bind the candidate URL")
        if self.direct_attachment is not None and self.direct_attachment.canonical_url != str(
            self.url
        ):
            raise ValueError("attachment must bind the candidate URL")
        return self


class SourceDiscoveryDiagnostics(ContractModel):
    raw_count: int | None = Field(default=None, ge=0)
    protocol_filtered: int = Field(default=0, ge=0)
    domain_filtered: int = Field(default=0, ge=0)
    date_filtered: int = Field(default=0, ge=0)
    selected_count: int = Field(default=0, ge=0, le=5)


class RobotsOriginStop(ContractModel):
    origin: str = Field(max_length=300)
    http_status: Literal[401, 403, 429]
    observed_at: AwareDatetime

    @field_validator("origin")
    @classmethod
    def canonical_https_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", parsed.hostname or "")
            is None
        ):
            raise ValueError("robots stop requires an HTTPS root origin on port 443")
        return f"https://{parsed.hostname}"


class SourceDiscoveryResult(ContractModel):
    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    status: SourceDiscoveryStatus
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidates: list[SourceDiscoveryCandidate] = Field(default_factory=list, max_length=5)
    searched_at: AwareDatetime
    credits_used: int = Field(default=0, ge=0, le=100)
    retryable: bool = False
    diagnostics: SourceDiscoveryDiagnostics = Field(default_factory=SourceDiscoveryDiagnostics)
    robots_origin_stop: RobotsOriginStop | None = None

    @model_validator(mode="after")
    def status_matches_candidates(self) -> Self:
        if self.robots_origin_stop is not None and (
            self.schema_version not in {"1.2", "1.3"}
            or self.status is not SourceDiscoveryStatus.FAILED
        ):
            raise ValueError("only failed discovery 1.2 may carry a robots origin stop")
        if self.status is SourceDiscoveryStatus.SUCCEEDED and not self.candidates:
            raise ValueError("successful discovery requires candidates")
        if self.status is not SourceDiscoveryStatus.SUCCEEDED and self.candidates:
            raise ValueError("non-successful discovery cannot return candidates")
        return self


class ResearchSourceSpan(ContractModel):
    start: int = Field(ge=0, le=100000)
    end: int = Field(gt=0, le=100000)

    @model_validator(mode="after")
    def nonempty_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("source intervals must be nonempty")
        return self


class ResearchSourcePage(ResearchSourceSpan):
    page_number: int = Field(ge=1, le=350)


class ResearchAcquisitionDocument(ContractModel):
    """An original observation, never merged with another source or visible time."""

    model_config = ConfigDict(frozen=True)

    document_id: UUID = Field(default_factory=uuid4)
    candidate: SourceDiscoveryCandidate
    source_id: str = Field(min_length=1, max_length=200)
    rights_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hash_basis: Literal["normalized_original"] = "normalized_original"
    body: str = Field(min_length=1, max_length=100000)
    extraction_scope: Literal["complete", "prefix_truncated", "selected_pages", "unknown"] = (
        "unknown"
    )
    pages: list[ResearchSourcePage] = Field(default_factory=list, max_length=350)
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime
    publication_basis: str = Field(default="", max_length=1000)
    discovery_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    retrieval_receipt_id: UUID
    formal_document: FormalDocumentVerification | None = None

    @model_validator(mode="after")
    def pages_reference_original_offsets(self) -> Self:
        proof = self.formal_document
        if proof is not None and (
            proof.normalized_original_sha256 != self.content_sha256
            or proof.source_id != self.source_id
            or proof.rights_snapshot_sha256 != self.rights_snapshot_sha256
            or proof.first_seen_at != self.first_seen_at
            or proof.identity.canonical_url != str(self.candidate.url)
            or proof.published_at != self.candidate.published_at
            or any(
                anchor.end is not None and anchor.end > len(self.body)
                for anchor in proof.identity.identity_anchors
            )
        ):
            raise ValueError("formal verification must bind this exact original observation")
        if hashlib.sha256(self.body.encode()).hexdigest() != self.content_sha256:
            raise ValueError("normalized original must match its retained content hash")
        previous_end = 0
        previous_page = 0
        for page in self.pages:
            if (
                page.start < previous_end
                or page.end > len(self.body)
                or page.page_number <= previous_page
            ):
                raise ValueError("page locations must reference ordered original intervals")
            previous_end, previous_page = page.end, page.page_number
        return self


class ResearchWindowCheck(ContractModel):
    document_id: UUID
    fact_key: str = Field(min_length=1, max_length=100)
    outcome: Literal["not_located", "window_unavailable_by_budget"]
    spans: list[ResearchSourceSpan] = Field(default_factory=list, max_length=350)


class ResearchAcquisitionCandidate(ContractModel):
    """Private, bounded source excerpts; never a published or eligible fact."""

    candidate: SourceDiscoveryCandidate
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    first_seen_at: AwareDatetime
    revision_known_at: AwareDatetime
    source_context: str | None = Field(default=None, min_length=1, max_length=40000)
    document_id: UUID | None = None
    context_spans: list[ResearchSourceSpan] = Field(default_factory=list, max_length=350)
    fact_spans: dict[str, list[ResearchSourceSpan]] = Field(default_factory=dict)
    requested_fact_keys: list[str] = Field(default_factory=list, max_length=5)
    attempt_numbers: dict[str, int] = Field(default_factory=dict)
    correction_keys: list[str] = Field(default_factory=list, max_length=5)
    extraction_complete: bool = True
    facts: list[ResearchWebNormalizedFactDraft] = Field(max_length=5)
    verdicts: list[ResearchDataAuditVerdict] = Field(default_factory=list, max_length=5)
    publication_basis: str = Field(default="", max_length=1000)
    discovery_request_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def context_has_one_representation(self) -> Self:
        if self.document_id is None:
            if self.source_context is None or self.context_spans:
                raise ValueError("legacy candidates require inline source context")
        elif self.source_context is not None or not self.context_spans:
            raise ValueError("document references and inline context are mutually exclusive")
        if len(set(self.requested_fact_keys)) != len(self.requested_fact_keys):
            raise ValueError("batch fact keys must be unique")
        if self.document_id is not None and (
            not self.requested_fact_keys
            or set(self.attempt_numbers) != set(self.requested_fact_keys)
            or set(self.fact_spans) != set(self.requested_fact_keys)
            or any(not spans or len(spans) > 350 for spans in self.fact_spans.values())
            or any(value < 1 or value > 4 for value in self.attempt_numbers.values())
            or not set(self.correction_keys) <= set(self.requested_fact_keys)
            or any(fact.missing_fact_key not in self.requested_fact_keys for fact in self.facts)
        ):
            raise ValueError("document batch must match its reserved fact keys")
        return self


class ResearchAcquisitionState(ContractModel):
    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    rule_version: str
    instrument_id: str
    requested_as_of: AwareDatetime
    deadline_at: AwareDatetime
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    attempts: dict[str, int] = Field(default_factory=dict)
    attempted_sources: dict[str, list[str]] = Field(default_factory=dict)
    attempted_queries: dict[str, list[str]] = Field(default_factory=dict)
    attempted_contents: dict[str, list[str]] = Field(default_factory=dict)
    discovery_diagnostics: dict[str, list[SourceDiscoveryDiagnostics]] = Field(default_factory=dict)
    active_gap: str | None = Field(default=None, max_length=100)
    active_fact_keys: list[str] = Field(default_factory=list, max_length=5)
    deferred_fact_keys: list[str] = Field(default_factory=list, max_length=20)
    round_sources: dict[str, list[str]] = Field(default_factory=dict)
    pending_query: SourceDiscoveryQuery | None = None
    pending_discovery: SourceDiscoveryResult | None = None
    discovery_exhausted: bool = False
    robots_origin_stops: list[RobotsOriginStop] = Field(default_factory=list, max_length=15)
    candidates: list[ResearchAcquisitionCandidate] = Field(default_factory=list, max_length=100)
    documents: list[ResearchAcquisitionDocument] = Field(default_factory=list, max_length=100)
    window_checks: list[ResearchWindowCheck] = Field(default_factory=list, max_length=900)
    verification_outcomes: list[CandidateVerificationOutcome] = Field(
        default_factory=list, max_length=100
    )
    attachment_queue: list[SourceDiscoveryCandidate] = Field(default_factory=list, max_length=100)
    complete: bool = False
    provider_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def attempts_are_bounded(self) -> Self:
        if self.robots_origin_stops and self.schema_version not in {"1.2", "1.3"}:
            raise ValueError("robots stops require acquisition 1.2")
        if self.schema_version != "1.3" and (
            self.verification_outcomes
            or self.attachment_queue
            or any(doc.formal_document is not None for doc in self.documents)
        ):
            raise ValueError("formal acquisition metadata requires state 1.3")
        if len({outcome.candidate_id for outcome in self.verification_outcomes}) != len(
            self.verification_outcomes
        ):
            raise ValueError("candidate outcomes must be unique")
        if len({str(candidate.url) for candidate in self.attachment_queue}) != len(
            self.attachment_queue
        ):
            raise ValueError("attachment queue URLs must be unique")
        if len({stop.origin for stop in self.robots_origin_stops}) != len(self.robots_origin_stops):
            raise ValueError("robots stop origins must be unique")
        known_ids = (
            {item.candidate_id for item in self.verification_outcomes}
            | {item.candidate_id for item in self.attachment_queue}
            | {item.candidate.candidate_id for item in self.documents}
            | {item.candidate.candidate_id for item in self.candidates}
        )
        if len(known_ids) > 100:
            raise ValueError("formal candidate union exceeds existing capacity")
        outcomes = {item.candidate_id: item for item in self.verification_outcomes}
        for item in self.attachment_queue:
            attachment = item.direct_attachment
            parent = outcomes.get(attachment.parent_candidate_id) if attachment else None
            if parent is None or attachment not in parent.attachment_candidates:
                raise ValueError("queued attachment requires its persisted parent outcome")
        documents = {item.document_id: item for item in self.documents}
        if len(self.round_sources) > 80 or any(
            len(values) > 5 or len(set(values)) != len(values)
            for values in self.round_sources.values()
        ):
            raise ValueError("an acquisition round permits at most five source candidates")
        if len(documents) != len(self.documents):
            raise ValueError("checkpoint document IDs must be unique")
        if (
            sum(len(item.body) for item in self.documents)
            + sum(len(item.source_context or "") for item in self.candidates)
            > 4000000
        ):
            raise ValueError("checkpoint source capacity exceeded")
        for candidate in self.candidates:
            if candidate.document_id is None:
                continue
            document = documents.get(candidate.document_id)
            if document is None or (
                candidate.candidate != document.candidate
                or candidate.content_sha256 != document.content_sha256
                or candidate.first_seen_at != document.first_seen_at
                or candidate.revision_known_at != document.revision_known_at
                or candidate.publication_basis != document.publication_basis
                or candidate.discovery_request_sha256 != document.discovery_request_sha256
            ):
                raise ValueError("batch must reference its original observation")
            previous_end = -1
            for span in candidate.context_spans:
                if span.start <= previous_end or span.end > len(document.body):
                    raise ValueError("batch intervals must be merged original positions")
                previous_end = span.end
            if (
                len(document.formal_document.model_dump_json()) if document.formal_document else 0
            ) + sum(span.end - span.start for span in candidate.context_spans) + (
                len(candidate.context_spans) - 1
            ) * len(RESEARCH_CONTEXT_OMISSION) > 40000:
                raise ValueError("loaded original windows exceed the model context budget")
            for spans in candidate.fact_spans.values():
                previous_end = -1
                for span in spans:
                    if span.start <= previous_end or not any(
                        context.start <= span.start < span.end <= context.end
                        for context in candidate.context_spans
                    ):
                        raise ValueError(
                            "fact windows must be complete merged subsets of the loaded context"
                        )
                    previous_end = span.end
        for check in self.window_checks:
            if check.document_id not in documents or any(
                span.end > len(documents[check.document_id].body) for span in check.spans
            ):
                raise ValueError("window check must reference its checkpoint document")
        if len(self.attempts) > 20 or any(
            count < 0 or count > 4 for count in self.attempts.values()
        ):
            raise ValueError("acquisition permits one initial and three additional attempts")
        if len(self.attempted_sources) > 20 or any(
            len(values) > 20 for values in self.attempted_sources.values()
        ):
            raise ValueError("acquisition source history must be bounded")
        if len(self.attempted_contents) > 20 or any(
            len(values) > 20 for values in self.attempted_contents.values()
        ):
            raise ValueError("acquisition content history must be bounded")
        if len(self.attempted_queries) > 20 or any(
            len(values) > 4
            or any(
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                for value in values
            )
            for values in self.attempted_queries.values()
        ):
            raise ValueError("acquisition query history must be bounded SHA-256 values")
        if len(self.discovery_diagnostics) > 20 or any(
            len(values) > 4 for values in self.discovery_diagnostics.values()
        ):
            raise ValueError("acquisition discovery diagnostics must be bounded")
        return self


class ResearchWebRetrievalReceiptStatus(StrEnum):
    DISCOVERED = "discovered"
    SELECTED = "selected"
    REJECTED = "rejected"
    FETCHED = "fetched"
    FAILED = "failed"


class ResearchWebRetrievalReceipt(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    receipt_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    query_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_url_sha256: list[str] = Field(default_factory=list, max_length=5)
    selected_source_id: str | None = Field(default=None, max_length=200)
    selected_url_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: ResearchWebRetrievalReceiptStatus
    credits_used: int = Field(default=0, ge=0, le=100)
    rule_version: str = Field(min_length=1, max_length=100)
    created_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def receipt_is_minimal_and_expiring(self) -> Self:
        if any(len(value) != 64 for value in self.candidate_url_sha256):
            raise ValueError("candidate URL hashes must be SHA-256 values")
        if self.expires_at <= self.created_at:
            raise ValueError("retrieval receipt must expire after creation")
        if bool(self.selected_source_id) != bool(self.selected_url_sha256):
            raise ValueError("selected source and URL hash must be populated together")
        return self


class ResearchWebRetrievalReceiptWriteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    receipt_id: UUID
    stored: bool
    idempotent_replay: bool
    processed_at: AwareDatetime


__all__ = [
    "RESEARCH_WEB_SOURCE_CAPABILITY",
    "RESEARCH_WEB_SOURCE_ID",
    "ResearchWebNormalizationDraft",
    "ResearchWebNormalizedFactDraft",
    "ResearchWebQueryPlanDraft",
    "ResearchWebQueryPlanItem",
    "ResearchWebRetrievalReceipt",
    "ResearchWebRetrievalReceiptStatus",
    "ResearchWebRetrievalReceiptWriteResult",
    "SourceDiscoveryCandidate",
    "SourceDiscoveryQuery",
    "SourceDiscoveryResult",
    "SourceDiscoveryStatus",
]
