from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.notifications import Notification


def test_notification_contract_is_versioned_and_auditable() -> None:
    notification = Notification(
        schema_version="1.0",
        event_cluster_id=uuid4(),
        event_revision=2,
        change_type="update",
        severity="high",
        title="Synthetic material update",
        evidence_ids=[uuid4()],
        created_at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        idempotency_key="a" * 32,
    )

    assert notification.schema_version == "1.0"
    assert notification.event_revision == 2


def test_notification_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        Notification(
            event_cluster_id=uuid4(),
            event_revision=1,
            change_type="new",
            severity="normal",
            title="Synthetic event",
            evidence_ids=[uuid4()],
            created_at=datetime(2026, 7, 21, 0, 0),
            idempotency_key="b" * 32,
        )
