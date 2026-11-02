"""Transparent source-quality scoring; authority is only one feature."""

from pydantic import BaseModel, Field


class QualityInputs(BaseModel):
    authority: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    corroboration: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    conflict_penalty: float = Field(default=0, ge=0, le=1)


class QualityWeights(BaseModel):
    authority: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    corroboration: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    conflict_penalty: float = Field(le=0)


def quality_score(inputs: QualityInputs, *, weights: QualityWeights) -> float:
    """Return a stable 0..1 score using explicitly injected versioned weights."""

    score = (
        inputs.authority * weights.authority
        + inputs.relevance * weights.relevance
        + inputs.freshness * weights.freshness
        + inputs.corroboration * weights.corroboration
        + inputs.completeness * weights.completeness
        + inputs.conflict_penalty * weights.conflict_penalty
    )
    return round(max(0.0, min(1.0, score)), 4)
