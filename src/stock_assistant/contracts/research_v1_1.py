"""Immutable nested scorecard 1.0 used by the published report 1.1."""

from typing import Literal

from pydantic import Field

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.research_reader import ResearchScoreDimension, ResearchScoreStatus


class ResearchScorecard(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    status: ResearchScoreStatus
    overall_score: DecimalString | None = None
    score_band: str | None = Field(default=None, max_length=100)
    score_meaning: str = Field(min_length=1, max_length=500)
    evidence_confidence: DecimalString
    evidence_confidence_meaning: str = Field(min_length=1, max_length=500)
    dimensions: list[ResearchScoreDimension] = Field(min_length=6, max_length=6)
    scoring_rule_version: str = Field(min_length=1, max_length=100)
    recommendation_notice: Literal["公司质量评分不构成投资建议, 也不表示推荐概率。"] = (
        "公司质量评分不构成投资建议, 也不表示推荐概率。"
    )
