from __future__ import annotations

import ast
from pathlib import Path

MIGRATION = Path("infra/migrations/versions/20260807_0013_notification_inbox.py")


def test_notification_inbox_is_additive_resumable_and_forward_only() -> None:
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
        "revision": "20260807_0013",
        "down_revision": "20260807_0012",
    }
    assert "CREATE TABLE notification_inbox_items" in source
    assert "PRIMARY KEY (recipient_key, message_id)" in source
    assert "recipient_key, idempotency_key" in source
    assert "recipient_key, event_name, projected_at, message_id" in source
    assert "CHECK (recipient_key = 'public:r1')" in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source
