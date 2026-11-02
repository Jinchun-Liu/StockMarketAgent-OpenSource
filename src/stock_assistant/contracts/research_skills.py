"""Strongly typed project-owned research-method contracts.

The upstream ai-berkshire texts are retained as provenance-only resources. Runtime
providers receive only the adapted profiles represented by these contracts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from stock_assistant.contracts.base import ContractModel


class ResearchSkillId(StrEnum):
    BOTTLENECK_HUNTER = "bottleneck-hunter"
    DEEP_COMPANY_SERIES = "deep-company-series"
    DYP_ASK = "dyp-ask"
    EARNINGS_REVIEW = "earnings-review"
    EARNINGS_TEAM = "earnings-team"
    ERA_ALPHA = "era-alpha"
    FINANCIAL_DATA = "financial-data"
    INCOME_INVESTMENT = "income-investment"
    INDUSTRY_FUNNEL = "industry-funnel"
    INDUSTRY_RESEARCH = "industry-research"
    INVESTMENT_CHECKLIST = "investment-checklist"
    INVESTMENT_RESEARCH = "investment-research"
    INVESTMENT_TEAM = "investment-team"
    MANAGEMENT_DEEP_DIVE = "management-deep-dive"
    NEWS_PULSE = "news-pulse"
    PORTFOLIO_REVIEW = "portfolio-review"
    PRIVATE_COMPANY_RESEARCH = "private-company-research"
    QUALITY_SCREEN = "quality-screen"
    THESIS_DRIFT = "thesis-drift"
    THESIS_TRACKER = "thesis-tracker"
    INVESTMENT_MEMO_CRAFT = "investment-memo-craft"


class ResearchSkillMode(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class ResearchSkillGroup(StrEnum):
    CORE = "core"
    INDUSTRY_AND_EVENTS = "industry_and_events"
    READER = "reader"
    CONDITIONAL = "conditional"
    BOUNDARY_BLOCKED = "boundary_blocked"


class ResearchSkillAvailability(StrEnum):
    AVAILABLE = "available"
    NOT_APPLICABLE = "not_applicable"
    BOUNDARY_BLOCKED = "boundary_blocked"


class ResearchSkillExecutionStatus(StrEnum):
    APPLIED = "applied"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BOUNDARY_BLOCKED = "boundary_blocked"
    FAILED = "failed"


class ResearchSkillSelection(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    mode: ResearchSkillMode = ResearchSkillMode.AUTO
    selected_skill_ids: list[ResearchSkillId] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def selection_must_be_unambiguous(self) -> Self:
        if len(set(self.selected_skill_ids)) != len(self.selected_skill_ids):
            raise ValueError("selected skill IDs must be unique")
        if self.mode is ResearchSkillMode.AUTO and self.selected_skill_ids:
            raise ValueError("auto mode cannot carry manually selected skills")
        return self


class ResearchSkillCatalogItem(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    skill_id: ResearchSkillId
    display_name: str = Field(min_length=1, max_length=100)
    plain_language_purpose: str = Field(min_length=1, max_length=500)
    group: ResearchSkillGroup
    is_core: bool
    availability: ResearchSkillAvailability
    disabled_reason: str | None = Field(default=None, max_length=500)
    method_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def disabled_items_require_a_reason(self) -> Self:
        if self.availability is not ResearchSkillAvailability.AVAILABLE:
            if self.disabled_reason is None:
                raise ValueError("unavailable skills require a disabled reason")
        elif self.disabled_reason is not None:
            raise ValueError("available skills cannot carry a disabled reason")
        if self.is_core and self.group is not ResearchSkillGroup.CORE:
            raise ValueError("core skills must belong to the core group")
        return self


class ResearchSkillCatalog(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    instrument_id: str = Field(pattern=r"^(XSHG|XSHE|XBSE):[0-9]{6}$")
    method_library_version: str = Field(min_length=1, max_length=100)
    items: list[ResearchSkillCatalogItem] = Field(min_length=21, max_length=21)

    @model_validator(mode="after")
    def catalog_contains_every_method_once(self) -> Self:
        identifiers = [item.skill_id for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("research skill catalog IDs must be unique")
        if set(identifiers) != set(ResearchSkillId):
            raise ValueError("research skill catalog must contain all 21 methods")
        return self


class ResearchSkillExecution(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    skill_id: ResearchSkillId
    status: ResearchSkillExecutionStatus
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=100)
    template_version: str = Field(min_length=1, max_length=100)
    detail: str | None = Field(default=None, max_length=500)


__all__ = [
    "ResearchSkillAvailability",
    "ResearchSkillCatalog",
    "ResearchSkillCatalogItem",
    "ResearchSkillExecution",
    "ResearchSkillExecutionStatus",
    "ResearchSkillGroup",
    "ResearchSkillId",
    "ResearchSkillMode",
    "ResearchSkillSelection",
]
