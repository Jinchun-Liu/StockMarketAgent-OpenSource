"""Build source-quality domain configuration from the versioned YAML policy."""

from functools import lru_cache

from stock_assistant.config.yaml_config import load_versioned_config
from stock_assistant.modules.ingestion import QualityWeights


@lru_cache(maxsize=1)
def get_quality_weights() -> QualityWeights:
    """Return validated weights from the repository's single policy source."""

    config = load_versioned_config("configs/scoring.yaml")
    return QualityWeights.model_validate(config.data["quality_formula"])
