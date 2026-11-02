"""Point-in-time market-data revisions used by the R1 research product."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.forecasting import MarketBar
from stock_assistant.contracts.storage import StoredObjectRef

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class MarketDataRevisionBase(ContractModel):
    """Shared append-only lineage without conflating business-effective and known time."""

    revision_id: UUID = Field(default_factory=uuid4)
    instrument: InstrumentRef
    effective_at: AwareDatetime
    available_at: AwareDatetime
    revision_known_at: AwareDatetime
    first_seen_at: AwareDatetime
    supersedes_revision_id: UUID | None = None
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    rights_snapshot_hash: Sha256
    content_hash: Sha256

    @model_validator(mode="after")
    def lineage_must_be_point_in_time_safe(self) -> "MarketDataRevisionBase":
        if not self.available_at <= self.first_seen_at <= self.revision_known_at:
            raise ValueError("market lineage must satisfy available <= first seen <= known")
        return self


class MarketBarRevision(MarketDataRevisionBase):
    kind: Literal["bar"] = "bar"
    bar: MarketBar

    @model_validator(mode="after")
    def bar_lineage_must_match_wrapper(self) -> "MarketBarRevision":
        if self.effective_at != self.bar.closed_at:
            raise ValueError("bar effective_at must equal bar.closed_at")
        if self.bar.instrument != self.instrument:
            raise ValueError("bar instrument must match its revision wrapper")
        if self.bar.source_id != self.source_id:
            raise ValueError("bar source_id must match its revision wrapper")
        if self.bar.rights_snapshot_hash != self.rights_snapshot_hash:
            raise ValueError("bar rights snapshot must match its revision wrapper")
        if not self.available_at <= self.bar.received_at <= self.revision_known_at:
            raise ValueError("bar lineage must satisfy available <= received <= known")
        return self


class ShareCapitalRevision(MarketDataRevisionBase):
    kind: Literal["share_capital"] = "share_capital"
    total_shares: DecimalString = Field(gt=0)
    free_float_shares: DecimalString = Field(gt=0)
    valid_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def capital_values_must_be_consistent(self) -> "ShareCapitalRevision":
        if self.free_float_shares > self.total_shares:
            raise ValueError("free-float shares cannot exceed total shares")
        if self.valid_until is not None and self.valid_until <= self.effective_at:
            raise ValueError("capital valid_until must follow effective_at")
        return self


class CorporateActionType(StrEnum):
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPLIT = "split"
    CONSOLIDATION = "consolidation"
    RIGHTS_ISSUE = "rights_issue"


class CorporateActionRevision(MarketDataRevisionBase):
    kind: Literal["corporate_action"] = "corporate_action"
    action_type: CorporateActionType
    ex_date: date
    ratio: DecimalString | None = Field(default=None, gt=0)
    cash_per_share: DecimalString | None = Field(default=None, ge=0)
    adjustment_factor: DecimalString = Field(gt=0)

    @model_validator(mode="after")
    def action_requires_a_material_term(self) -> "CorporateActionRevision":
        if self.ratio is None and self.cash_per_share is None:
            raise ValueError("corporate action requires ratio or cash_per_share")
        return self


class TradingRuleRevision(MarketDataRevisionBase):
    kind: Literal["trading_rule"] = "trading_rule"
    board: str = Field(min_length=1, max_length=40)
    is_st: bool
    is_suspended: bool
    price_limit_rate: DecimalString | None = Field(default=None, gt=0, le=1)
    valid_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def rule_validity_must_be_ordered(self) -> "TradingRuleRevision":
        if self.valid_until is not None and self.valid_until <= self.effective_at:
            raise ValueError("trading-rule valid_until must follow effective_at")
        return self


MarketDataRevision = Annotated[
    MarketBarRevision | ShareCapitalRevision | CorporateActionRevision | TradingRuleRevision,
    Field(discriminator="kind"),
]


class MarketDataManifestStatus(StrEnum):
    READY = "ready"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


class MarketDataManifest(ContractModel):
    manifest_id: UUID = Field(default_factory=uuid4)
    status: MarketDataManifestStatus
    requested_from: AwareDatetime
    requested_until: AwareDatetime
    as_of: AwareDatetime
    row_count: int = Field(ge=0)
    object_ref: StoredObjectRef | None = None
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    rights_snapshot_hash: Sha256
    content_hash: Sha256
    gap_reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def manifest_state_must_be_consistent(self) -> "MarketDataManifest":
        if not self.requested_from < self.requested_until <= self.as_of:
            raise ValueError("market manifest window must be complete at as_of")
        if self.status is MarketDataManifestStatus.READY:
            if self.row_count < 1 or self.object_ref is None or self.gap_reason_code is not None:
                raise ValueError("ready manifest requires rows and object_ref without a gap")
        elif self.gap_reason_code is None:
            raise ValueError("non-ready manifest requires a stable gap reason")
        if self.status is MarketDataManifestStatus.BLOCKED:
            if self.row_count != 0 or self.object_ref is not None:
                raise ValueError("blocked manifest must contain no rows or object reference")
        if self.object_ref is not None:
            if self.object_ref.content_sha256 != self.content_hash:
                raise ValueError("manifest and object reference content hashes must match")
            if self.object_ref.rights_snapshot_hash != self.rights_snapshot_hash:
                raise ValueError("manifest and object reference rights hashes must match")
        return self


class MarketDataRevisionAppendRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    revisions: list[MarketDataRevision] = Field(min_length=1, max_length=10_000)


class MarketDataRevisionAppendResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    accepted_count: int = Field(ge=0)
    idempotent_replay_count: int = Field(ge=0)
    stored_at: AwareDatetime
    manifest: MarketDataManifest

    @model_validator(mode="after")
    def counts_must_be_nonempty(self) -> "MarketDataRevisionAppendResult":
        if self.accepted_count + self.idempotent_replay_count < 1:
            raise ValueError("append result must account for at least one revision")
        return self


class MarketDataRevisionQuery(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    instrument_ids: list[str] = Field(min_length=1, max_length=10_000)
    kinds: list[Literal["bar", "share_capital", "corporate_action", "trading_rule"]] = Field(
        default_factory=list,
        max_length=4,
    )
    effective_from: AwareDatetime | None = None
    effective_until: AwareDatetime | None = None
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=1000, ge=1, le=10_000)

    @model_validator(mode="after")
    def query_must_respect_cutoff(self) -> "MarketDataRevisionQuery":
        if self.effective_until is not None and self.effective_until > self.context.as_of:
            raise ValueError("effective_until cannot be later than context.as_of")
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise ValueError("effective range is reversed")
        return self


class MarketDataRevisionPage(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[MarketDataRevision] = Field(default_factory=list, max_length=10_000)
    next_cursor: str | None = Field(default=None, max_length=4096)
    has_more: bool = False
    as_of: AwareDatetime
    manifest: MarketDataManifest

    @model_validator(mode="after")
    def page_must_be_visible_and_resumable(self) -> "MarketDataRevisionPage":
        if self.has_more and self.next_cursor is None:
            raise ValueError("next_cursor is required when has_more is true")
        if any(item.available_at > self.as_of for item in self.items):
            raise ValueError("market page cannot expose a revision available after as_of")
        if any(item.revision_known_at > self.as_of for item in self.items):
            raise ValueError("market page cannot expose a revision known after as_of")
        if self.manifest.as_of != self.as_of:
            raise ValueError("market page and manifest must share the same as_of")
        return self
