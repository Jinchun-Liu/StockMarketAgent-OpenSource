"""R2 contracts for a stock-agnostic global event catalog and on-demand matching."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.events import MarketEvent


class GlobalEventCatalogStatus(StrEnum):
    READY_WITH_MATCHES = "ready_with_matches"
    READY_NO_MATCHES = "ready_no_matches"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    RIGHTS_BLOCKED = "rights_blocked"


class ResearchExposureDirectness(StrEnum):
    OFFICIAL_DIRECT_NAMING = "official_direct_naming"
    COMPANY_DISCLOSED = "company_disclosed"
    ONE_HOP_EVIDENCE = "one_hop_evidence"
    INDUSTRY_BACKGROUND = "industry_background"


class ResearchImpactChannel(StrEnum):
    EXPORT_DEMAND = "export_demand"
    INPUT_COST = "input_cost"
    COMMODITY = "commodity"
    FX = "fx"
    INTEREST_RATE = "interest_rate"
    CAPITAL_FLOW = "capital_flow"
    SANCTIONS = "sanctions"
    LOGISTICS = "logistics"
    TECHNOLOGY_CONTROL = "technology_control"
    SUBSIDY = "subsidy"
    DOMESTIC_SUBSTITUTION = "domestic_substitution"
    INFLATION_POLICY = "inflation_policy"


class ResearchExternalImpactDirection(StrEnum):
    SUPPORTIVE = "supportive"
    ADVERSE = "adverse"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ResearchExposureEdge(ContractModel):
    """Evidence-backed company exposure; paths longer than one hop are forbidden."""

    from_entity_id: str = Field(min_length=1, max_length=200)
    to_entity_id: str = Field(min_length=1, max_length=200)
    directness: ResearchExposureDirectness
    hop_count: int = Field(ge=0, le=1)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def directness_must_match_hop_count(self) -> Self:
        if self.directness is ResearchExposureDirectness.ONE_HOP_EVIDENCE:
            if self.hop_count != 1:
                raise ValueError("one-hop exposure requires hop_count=1")
        elif self.hop_count != 0:
            raise ValueError("direct or background exposure requires hop_count=0")
        return self


class ResearchExposureProfile(ContractModel):
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    company_id: str | None = Field(default=None, max_length=200)
    industry_ids: list[str] = Field(default_factory=list, max_length=100)
    region_ids: list[str] = Field(default_factory=list, max_length=100)
    edges: list[ResearchExposureEdge] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def edges_must_terminate_in_declared_exposures(self) -> Self:
        declared = {*self.industry_ids, *self.region_ids}
        if any(edge.to_entity_id not in declared for edge in self.edges):
            raise ValueError("exposure edges must terminate in a declared industry or region")
        return self


class ResearchExternalImpactMatch(ContractModel):
    event_id: UUID
    event_revision: int = Field(ge=1)
    event_revision_known_at: AwareDatetime
    directness: ResearchExposureDirectness
    impact_channel: ResearchImpactChannel
    direction: ResearchExternalImpactDirection
    path: list[str] = Field(min_length=2, max_length=3)
    hop_count: int = Field(ge=0, le=1)
    supporting_evidence_ids: list[UUID] = Field(min_length=1, max_length=100)
    counter_evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    rule_version: str = Field(min_length=1, max_length=200)
    uncertainty: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def path_and_evidence_must_be_bounded(self) -> Self:
        if len(self.path) != self.hop_count + 2:
            raise ValueError("impact path length must reflect zero-hop or one-hop directness")
        if set(self.supporting_evidence_ids) & set(self.counter_evidence_ids):
            raise ValueError("supporting and counter evidence must be distinct")
        return self


class GlobalEventCatalogSlice(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    status: GlobalEventCatalogStatus
    events: list[MarketEvent] = Field(default_factory=list, max_length=1000)
    watermark: AwareDatetime | None = None
    last_success_at: AwareDatetime | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def status_must_match_catalog_state(self) -> Self:
        if self.status is GlobalEventCatalogStatus.READY_WITH_MATCHES and not self.events:
            raise ValueError("ready_with_matches requires at least one event")
        if self.status is GlobalEventCatalogStatus.READY_NO_MATCHES and self.events:
            raise ValueError("ready_no_matches cannot include events")
        unavailable = {
            GlobalEventCatalogStatus.UNAVAILABLE,
            GlobalEventCatalogStatus.STALE,
            GlobalEventCatalogStatus.RIGHTS_BLOCKED,
        }
        if self.status in unavailable and self.reason_code is None:
            raise ValueError("unavailable catalog states require a reason_code")
        if self.status not in unavailable and self.reason_code is not None:
            raise ValueError("ready catalog states cannot carry a failure reason")
        return self


class PolicyRefreshRequest(ContractModel):
    """A stock-agnostic refresh request; instrument and watchlist fields do not exist."""

    schema_version: Literal["1.0"] = "1.0"
    context: MutationContext
    scheduled_for: AwareDatetime
    source_mode: Literal["synthetic_development_only"] = "synthetic_development_only"
    fixture_manifest_version: Literal["r2-global-events.v1"] = "r2-global-events.v1"
    fixture_manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def schedule_must_not_be_in_the_future(self) -> Self:
        if self.scheduled_for > self.context.as_of:
            raise ValueError("scheduled_for cannot be later than context.as_of")
        return self


class PolicyRefreshResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    source_id: Literal["r2_global_official_synthetic"] = "r2_global_official_synthetic"
    scheduled_for: AwareDatetime
    completed_at: AwareDatetime
    events_seen: int = Field(ge=0)
    event_revisions_created: int = Field(ge=0)
    evidence_created: int = Field(ge=0)
    watermark: AwareDatetime | None = None
    manifest_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    cursor_advanced: bool
    idempotent_replay: bool = False


__all__ = [
    "GlobalEventCatalogSlice",
    "GlobalEventCatalogStatus",
    "PolicyRefreshRequest",
    "PolicyRefreshResult",
    "ResearchExposureDirectness",
    "ResearchExposureEdge",
    "ResearchExposureProfile",
    "ResearchExternalImpactDirection",
    "ResearchExternalImpactMatch",
    "ResearchImpactChannel",
]
