from pathlib import Path

import pytest


@pytest.mark.contract
def test_initial_migration_contains_point_in_time_and_audit_tables() -> None:
    migration = Path("infra/migrations/versions/20260721_0001_initial.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "raw_documents",
        "rights_hash",
        "source_runs",
        "source_cursors",
        "first_seen_at",
        "market_events",
        "event_claims",
        "predictions",
        "prediction_outcomes",
        "notifications",
        "briefings",
        "llm_generations",
        "outbox_events",
        "audit_log",
    ):
        assert required in migration
