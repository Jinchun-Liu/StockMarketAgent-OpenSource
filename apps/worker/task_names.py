"""Single registry of stable, versioned Celery task names."""

from types import MappingProxyType
from typing import Final

TASK_NAMES: Final = MappingProxyType(
    {
        "policy_refresh": "stock_assistant.policies.refresh.v1",
        "premarket_ingest": "stock_assistant.ingestion.premarket_ingest.v1",
        "premarket_brief": "stock_assistant.briefings.premarket_generate.v1",
        "behavior_compute": "stock_assistant.behavior.compute.v1",
        "research_generate": "stock_assistant.research.generate_report.v1",
        "quote_snapshot": "stock_assistant.sectors.quote_snapshot.v1",
        "sector_rotation": "stock_assistant.sectors.rotation_tick.v1",
        "postmarket_outcome": "stock_assistant.forecasting.observe_outcome.v1",
    }
)

__all__ = ["TASK_NAMES"]
