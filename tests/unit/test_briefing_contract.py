from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.briefings import (
    Briefing,
    BriefingItem,
    BriefingKind,
    ForecastSummary,
)


def _item(**overrides: object) -> BriefingItem:
    values: dict[str, object] = {
        "headline": "Synthetic material update",
        "fact_summary": "A deterministic test fact.",
        "change_type": "update",
        "source_ids": ["synthetic_exchange"],
        "published_at": datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        "first_seen_at": datetime(2026, 7, 21, 0, 1, tzinfo=UTC),
        "event_ids": [uuid4()],
        "evidence_ids": [uuid4()],
        "confidence": 0.9,
        "is_material_change": True,
    }
    values.update(overrides)
    return BriefingItem.model_validate(values)


def test_briefing_contract_records_provenance() -> None:
    now = datetime(2026, 7, 21, 0, 20, tzinfo=UTC)
    briefing = Briefing(
        schema_version="1.0",
        kind=BriefingKind.PREMARKET,
        generated_at=now,
        as_of=now,
        items=[_item()],
        data_delay_seconds=30,
        model_version="test-model",
        prompt_hash="a" * 64,
        rule_version="test-rules",
    )

    assert briefing.items[0].evidence_ids
    assert briefing.disclaimer


def test_briefing_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        _item(confidence=1.1)


def test_briefing_rejects_naive_timestamps_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _item(published_at=datetime(2026, 7, 21, 0, 0))
    with pytest.raises(ValidationError):
        _item(unexpected="silent contract drift")


def test_forecast_summary_requires_ordered_quantiles() -> None:
    now = datetime(2026, 7, 21, 0, 20, tzinfo=UTC)
    with pytest.raises(ValidationError, match="quantiles"):
        ForecastSummary(
            prediction_id=uuid4(),
            probability_up=0.6,
            return_q10=0.02,
            return_q50=0.01,
            return_q90=0.03,
            model_version="test-fusion",
            as_of=now,
        )
