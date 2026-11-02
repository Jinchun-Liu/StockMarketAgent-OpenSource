from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_fact_must_reference_attached_evidence(event_factory) -> None:
    event = event_factory()
    payload = event.model_dump()
    payload["facts"][0]["evidence_id"] = uuid4()
    with pytest.raises(ValidationError, match="missing evidence"):
        type(event).model_validate(payload)


def test_first_seen_cannot_precede_publication(event_factory) -> None:
    event = event_factory()
    payload = event.model_dump()
    payload["first_seen_at"] = event.published_at - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="first_seen_at"):
        type(event).model_validate(payload)


def test_event_rejects_naive_timestamp_and_unknown_field(event_factory) -> None:
    event = event_factory()
    payload = event.model_dump()
    payload["published_at"] = datetime(2026, 7, 20, 8, 0)
    with pytest.raises(ValidationError):
        type(event).model_validate(payload)

    payload = event.model_dump()
    payload["unexpected"] = "silent contract drift"
    with pytest.raises(ValidationError):
        type(event).model_validate(payload)
