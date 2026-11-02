"""Bounded formal-document provenance shared by ingestion and research contracts."""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, ConfigDict, Field, model_validator

from stock_assistant.contracts.base import ContractModel

DocumentKind = Literal["annual", "interim", "announcement", "regulatory", "IR"]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("formal provenance requires a canonical HTTPS URL")
    return value


CanonicalURL = Annotated[
    str, Field(min_length=1, max_length=2048, pattern=r"^https://"), AfterValidator(_canonical_url)
]


class FormalDocumentLocation(ContractModel):
    """An original normalized character interval or an explicit metadata field."""

    field: str = Field(min_length=1, max_length=256)
    start: int | None = Field(default=None, ge=0, le=100000)
    end: int | None = Field(default=None, gt=0, le=100000)
    page_number: int | None = Field(default=None, ge=1, le=350)

    @model_validator(mode="after")
    def interval_is_complete(self) -> Self:
        if (self.start is None) != (self.end is None) or (
            self.start is not None and self.end is not None and self.end <= self.start
        ):
            raise ValueError("formal document location requires a complete nonempty interval")
        return self


class PublicationDateEvidence(ContractModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[
        "official_index",
        "official_file_metadata",
        "document_disclosure",
        "official_page_structured",
    ]
    raw_value: str = Field(min_length=1, max_length=128)
    published_at: AwareDatetime
    precision: Literal["day", "instant"]
    timezone: Literal["Asia/Shanghai", "UTC"]
    canonical_url: CanonicalURL
    location: FormalDocumentLocation
    observation_sha256: Sha256
    receipt_id: UUID


class FormalDocumentIdentity(ContractModel):
    model_config = ConfigDict(frozen=True)

    document_kind: DocumentKind
    issuer_name: str = Field(min_length=1, max_length=256)
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    title: str = Field(min_length=1, max_length=512)
    native_document_id: str = Field(min_length=1, max_length=256)
    canonical_url: CanonicalURL
    binding_basis: Literal["official_index", "document_fields"]
    identity_anchors: list[FormalDocumentLocation] = Field(min_length=1, max_length=4)


class OfficialDocumentIndexBinding(ContractModel):
    """Adapter-produced exact row binding; validation alone does not grant trust."""

    identity: FormalDocumentIdentity
    publication: PublicationDateEvidence
    index_url: Literal["https://www.cninfo.com.cn/new/hisAnnouncement/query"]
    row_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def exact_file_binding(self) -> Self:
        if self.identity.canonical_url != self.publication.canonical_url:
            raise ValueError("index identity and publication must bind the same file")
        if (
            self.publication.kind != "official_index"
            or self.identity.binding_basis != "official_index"
        ):
            raise ValueError("official index binding must retain its proof kind")
        return self


class FormalDocumentVerification(ContractModel):
    model_config = ConfigDict(frozen=True)

    identity: FormalDocumentIdentity
    publication_evidence: list[PublicationDateEvidence] = Field(min_length=1, max_length=8)
    published_at: AwareDatetime
    source_id: str = Field(min_length=1, max_length=200)
    normalized_original_sha256: Sha256
    rights_snapshot_sha256: Sha256
    first_seen_at: AwareDatetime
    pit_cutoff: AwareDatetime
    media_type: Literal["application/pdf", "text/html", "text/plain"]

    @model_validator(mode="after")
    def binding_and_cutoff(self) -> Self:
        if self.published_at > self.pit_cutoff or self.published_at > self.first_seen_at:
            raise ValueError("formal document publication exceeds visibility cutoff")
        if any(
            proof.canonical_url != self.identity.canonical_url
            for proof in self.publication_evidence
        ):
            raise ValueError("publication proof must refer to the exact formal file")
        return self


class DirectAttachmentCandidate(ContractModel):
    parent_candidate_id: UUID
    canonical_url: CanonicalURL
    href: str = Field(min_length=1, max_length=2048)
    href_ordinal: int = Field(ge=0, le=11)
    parent_receipt_id: UUID
    official_index: OfficialDocumentIndexBinding | None = None


class CandidateVerificationOutcome(ContractModel):
    candidate_id: UUID
    source_id: str = Field(min_length=1, max_length=200)
    canonical_url: CanonicalURL
    source_category: Literal["regulatory_official", "company_official", "other"]
    stage: Literal["discovery", "identity", "publication", "pit", "extraction", "verified"]
    reason: str = Field(pattern=r"^WEB_[A-Z0-9_]{1,100}$")
    formal_metadata_checked: bool
    receipt_id: UUID
    observed_at: AwareDatetime
    attachment_candidates: list[DirectAttachmentCandidate] = Field(
        default_factory=list, max_length=4
    )
    attachment_capacity_exceeded: bool = False

    @model_validator(mode="after")
    def attachment_binding(self) -> Self:
        if len({item.canonical_url for item in self.attachment_candidates}) != len(
            self.attachment_candidates
        ):
            raise ValueError("direct attachment URLs must be unique")
        for item in self.attachment_candidates:
            if (
                item.parent_candidate_id != self.candidate_id
                or item.parent_receipt_id != self.receipt_id
            ):
                raise ValueError("attachment must bind its actual parent read")
        return self


class FormalDocumentCitation(ContractModel):
    document_id: UUID
    evidence_id: UUID
    verification: FormalDocumentVerification
    retrieval_receipt_id: UUID
    extraction_scope: Literal["complete", "prefix_truncated", "selected_pages", "unknown"]
    # A document can support all nine existing topics across multiple five-fact batches.
    fact_locations: dict[str, FormalDocumentLocation] = Field(min_length=1, max_length=9)
