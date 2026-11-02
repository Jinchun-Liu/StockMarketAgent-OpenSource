from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = Path("infra/migrations/versions/20260807_0012_behavior_pressure.py")


def test_behavior_migration_is_single_head_additive_and_idempotency_safe() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "20260807_0012",
        "down_revision": "20260804_0011",
    }
    assert "CREATE TABLE market_data_revisions" in source
    assert "CREATE TABLE market_data_write_receipts" in source
    assert "CREATE TABLE behavior_pressure_assessments" in source
    assert "CREATE TABLE behavior_pressure_write_receipts" in source
    assert "CREATE TABLE behavior_transition_states" in source
    assert "revision_known_at" in source
    assert "idempotency_key" in source
    assert "UNIQUE" in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source
