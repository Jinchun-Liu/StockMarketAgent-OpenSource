"""Fail-closed unit checks for the stock-agnostic global event refresh."""

from datetime import UTC, datetime

import pytest

from stock_assistant.contracts.common import MutationContext
from stock_assistant.contracts.global_events import PolicyRefreshRequest
from stock_assistant.platform.db.market_events import (
    MarketEventRepositoryError,
    PostgresMarketEventRepository,
)

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_manifest_mismatch_fails_before_database_access() -> None:
    request = PolicyRefreshRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW,
            idempotency_key=f"global-event-refresh-{NOW.isoformat()}-{'0' * 64}",
        ),
        scheduled_for=NOW,
        fixture_manifest_sha256="0" * 64,
    )
    repository = PostgresMarketEventRepository(object())  # type: ignore[arg-type]

    with pytest.raises(
        MarketEventRepositoryError,
        match="GLOBAL_EVENT_MANIFEST_MISMATCH",
    ):
        await repository.refresh_synthetic_catalog(request)
