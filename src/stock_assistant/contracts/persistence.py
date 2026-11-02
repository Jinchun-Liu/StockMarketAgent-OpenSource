"""Aggregate-specific persistence requests and results."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.events import EventStatus, MarketEvent
from stock_assistant.contracts.portfolio import (
    ActiveTargetRevisionRef,
    PortfolioSnapshot,
    Watchlist,
    WatchlistEntry,
)


def _alert_enabled_entry(watchlist: Watchlist) -> WatchlistEntry:
    entries = [entry for entry in watchlist.entries if entry.alert_enabled]
    if len(entries) != 1:
        raise ValueError("an active-target aggregate requires exactly one alert-enabled entry")
    return entries[0]


def _validate_active_target_matches_watchlist(
    watchlist: Watchlist,
    active_target: ActiveTargetRevisionRef,
) -> None:
    active_entry = _alert_enabled_entry(watchlist)
    if active_target.watchlist_id != watchlist.watchlist_id:
        raise ValueError("active target must match the watchlist ID")
    if active_target.watchlist_revision != watchlist.revision:
        raise ValueError("active target must match the watchlist revision")
    if active_target.instrument != active_entry.instrument:
        raise ValueError("active target must match the alert-enabled entry")
    if active_target.activated_at < active_entry.added_at:
        raise ValueError("active target cannot be activated before it was added")
    if active_target.activated_at > watchlist.updated_at:
        raise ValueError("active target activation cannot be later than the watchlist update")


class MarketEventSort(StrEnum):
    REVISION_KNOWN_DESC = "revision_known_desc"
    PUBLISHED_DESC = "published_desc"
    FIRST_SEEN_DESC = "first_seen_desc"


class MarketEventLookupRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    event_id: UUID
    revision: int | None = Field(default=None, ge=1)


class MarketEventLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    found: bool
    event: MarketEvent | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_must_match_event(self) -> "MarketEventLookupResult":
        if self.found != (self.event is not None):
            raise ValueError("found must reflect whether an event is present")
        return self


class MarketEventQuery(ContractModel):
    """Bounded event query whose fields are owned by the event aggregate."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    event_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    cluster_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    security_ids: list[str] = Field(default_factory=list, max_length=1000)
    company_ids: list[str] = Field(default_factory=list, max_length=1000)
    region_ids: list[str] = Field(default_factory=list, max_length=1000)
    event_types: list[str] = Field(default_factory=list, max_length=100)
    statuses: list[EventStatus] = Field(default_factory=list, max_length=20)
    first_seen_since: AwareDatetime | None = None
    first_seen_until: AwareDatetime | None = None
    revision_known_since: AwareDatetime | None = None
    revision_known_until: AwareDatetime | None = None
    include_superseded_revisions: bool = False
    sort: MarketEventSort = MarketEventSort.REVISION_KNOWN_DESC
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def time_ranges_must_be_point_in_time_safe(self) -> "MarketEventQuery":
        ranges = (
            (self.first_seen_since, self.first_seen_until, "first_seen"),
            (self.revision_known_since, self.revision_known_until, "revision_known"),
        )
        for since, until, name in ranges:
            if since is not None and until is not None and until < since:
                raise ValueError(f"{name}_until cannot be earlier than {name}_since")
            if until is not None and until > self.context.as_of:
                raise ValueError(f"{name}_until cannot be later than context.as_of")
        return self


class MarketEventQueryV1_1(ContractModel):
    """Compatible query matching any declared R2 exposure identity."""

    schema_version: Literal["1.1"] = "1.1"
    context: OperationContext
    event_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    cluster_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    security_ids: list[str] = Field(default_factory=list, max_length=1000)
    company_ids: list[str] = Field(default_factory=list, max_length=1000)
    region_ids: list[str] = Field(default_factory=list, max_length=1000)
    industry_ids: list[str] = Field(default_factory=list, max_length=1000)
    event_types: list[str] = Field(default_factory=list, max_length=100)
    statuses: list[EventStatus] = Field(default_factory=list, max_length=20)
    first_seen_since: AwareDatetime | None = None
    first_seen_until: AwareDatetime | None = None
    revision_known_since: AwareDatetime | None = None
    revision_known_until: AwareDatetime | None = None
    include_superseded_revisions: bool = False
    sort: MarketEventSort = MarketEventSort.REVISION_KNOWN_DESC
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def time_ranges_must_be_point_in_time_safe(self) -> "MarketEventQueryV1_1":
        ranges = (
            (self.first_seen_since, self.first_seen_until, "first_seen"),
            (self.revision_known_since, self.revision_known_until, "revision_known"),
        )
        for since, until, name in ranges:
            if since is not None and until is not None and until < since:
                raise ValueError(f"{name}_until cannot be earlier than {name}_since")
            if until is not None and until > self.context.as_of:
                raise ValueError(f"{name}_until cannot be later than context.as_of")
        return self


class MarketEventUpsertRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    event: MarketEvent
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def revision_must_be_optimistic(self) -> "MarketEventUpsertRequest":
        if self.expected_revision is None and self.event.revision != 1:
            raise ValueError("new events must begin at revision 1")
        if self.expected_revision is not None and self.event.revision != self.expected_revision + 1:
            raise ValueError("updated events must increment expected_revision exactly once")
        return self


class MarketEventUpsertResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    event: MarketEvent
    created: bool
    stored_at: AwareDatetime
    idempotent_replay: bool = False


class PortfolioLookupRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    portfolio_id: UUID


class PortfolioLookupResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    found: bool
    snapshot: PortfolioSnapshot | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_must_match_snapshot(self) -> "PortfolioLookupResult":
        if self.found != (self.snapshot is not None):
            raise ValueError("found must reflect whether a portfolio snapshot is present")
        return self


class PortfolioSnapshotWriteRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    snapshot: PortfolioSnapshot
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def revision_must_be_optimistic(self) -> "PortfolioSnapshotWriteRequest":
        if self.expected_revision is None and self.snapshot.revision != 1:
            raise ValueError("new portfolios must begin at revision 1")
        if (
            self.expected_revision is not None
            and self.snapshot.revision != self.expected_revision + 1
        ):
            raise ValueError("updated portfolios must increment expected_revision exactly once")
        return self


class PortfolioSnapshotWriteResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot: PortfolioSnapshot
    created: bool
    stored_at: AwareDatetime
    idempotent_replay: bool = False


class ActiveTargetLookupRequest(ContractModel):
    """Point-in-time lookup of the active entry in one Watchlist aggregate."""

    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    watchlist_id: UUID


class ActiveTargetLookupResult(ContractModel):
    """Explicitly distinguishes no active target from a visible aggregate revision."""

    schema_version: Literal["1.0"] = "1.0"
    found: bool
    watchlist: Watchlist | None = None
    active_target: ActiveTargetRevisionRef | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_revision_must_be_consistent_and_visible(self) -> "ActiveTargetLookupResult":
        populated = self.watchlist is not None and self.active_target is not None
        if self.found != populated:
            raise ValueError(
                "found must reflect whether watchlist and active_target are both present"
            )
        if self.watchlist is None or self.active_target is None:
            if self.watchlist is not None or self.active_target is not None:
                raise ValueError("watchlist and active_target must be returned together")
            return self
        _validate_active_target_matches_watchlist(self.watchlist, self.active_target)
        if self.watchlist.updated_at > self.as_of:
            raise ValueError("watchlist revision cannot be after as_of")
        if self.active_target.activated_at > self.as_of:
            raise ValueError("active target activation cannot be after as_of")
        return self


class ActiveTargetWriteRequest(ContractModel):
    """Optimistic write of a complete Watchlist revision and its active target."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    watchlist: Watchlist
    active_target: ActiveTargetRevisionRef
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def revision_and_active_target_must_be_consistent(self) -> "ActiveTargetWriteRequest":
        if self.expected_revision is None and self.watchlist.revision != 1:
            raise ValueError("new watchlists must begin at revision 1")
        if self.expected_revision is not None and self.watchlist.revision not in {
            self.expected_revision,
            self.expected_revision + 1,
        }:
            raise ValueError(
                "updated watchlists must retain expected_revision for a no-op "
                "or increment it exactly once for a target change"
            )
        _validate_active_target_matches_watchlist(self.watchlist, self.active_target)
        if self.watchlist.updated_at > self.context.as_of:
            raise ValueError("watchlist update cannot be later than context.as_of")
        if self.active_target.activated_at > self.context.as_of:
            raise ValueError("active target activation cannot be later than context.as_of")
        return self


class ActiveTargetWriteResult(ContractModel):
    """Stable persistence result for an idempotent active-target write."""

    schema_version: Literal["1.0"] = "1.0"
    watchlist: Watchlist
    active_target: ActiveTargetRevisionRef
    created: bool
    changed: bool
    stored_at: AwareDatetime
    idempotent_replay: bool = False

    @model_validator(mode="after")
    def stored_revision_must_be_consistent(self) -> "ActiveTargetWriteResult":
        _validate_active_target_matches_watchlist(self.watchlist, self.active_target)
        if self.created and not self.changed:
            raise ValueError("a newly created active target must be reported as changed")
        if self.created and self.watchlist.revision != 1:
            raise ValueError("a newly created watchlist must be stored at revision 1")
        if self.stored_at < self.watchlist.updated_at:
            raise ValueError("stored_at cannot be earlier than the watchlist update")
        return self
