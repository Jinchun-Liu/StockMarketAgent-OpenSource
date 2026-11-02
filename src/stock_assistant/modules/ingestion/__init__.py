"""Source connectors, normalization, quality scoring and rights gates."""

from stock_assistant.modules.ingestion.scoring import (
    QualityInputs,
    QualityWeights,
    quality_score,
)

__all__ = ["QualityInputs", "QualityWeights", "quality_score"]
