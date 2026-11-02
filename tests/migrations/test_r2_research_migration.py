from __future__ import annotations

import ast
import hashlib
from pathlib import Path

MIGRATION = Path("infra/migrations/versions/20260827_0014_r2_research_sessions.py")
PURGE_MIGRATION = Path(
    "infra/migrations/versions/20260827_0015_research_session_purge_completion.py"
)
OWNERSHIP_MIGRATION = Path("infra/migrations/versions/20260828_0016_research_session_ownership.py")
ARTIFACT_VERSION_MIGRATION = Path(
    "infra/migrations/versions/20260828_0017_research_artifact_versions.py"
)
WEB_RETRIEVAL_MIGRATION = Path(
    "infra/migrations/versions/20260830_0018_research_web_retrieval_receipts.py"
)
CUTOFF_MODE_MIGRATION = Path("infra/migrations/versions/20260904_0019_research_cutoff_mode.py")


def test_r2_research_migration_is_additive_bounded_and_forward_only() -> None:
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
        "revision": "20260827_0014",
        "down_revision": "20260807_0013",
    }
    for table in (
        "research_sessions",
        "research_session_transitions",
        "research_job_outbox",
        "research_session_mutation_receipts",
        "research_reports",
        "research_report_claims",
        "research_session_contexts",
        "macro_reference_facts",
    ):
        assert f"CREATE TABLE {table}" in source
    assert "interval '7 days'" in source
    assert "interval '24 hours'" in source
    assert "stock_assistant.research.generate_report.v1" in source
    assert "outbox_events_wire_identity_ck" in source
    assert "(\\\\.[a-z][a-z0-9_]*)+" in source
    assert "current_macro_value" not in source
    assert (
        "subject_scope IN ('macro', 'institutional', 'taxonomy', 'historical_reference')" in source
    )
    assert "temporal_stability IN ('stable', 'historical_final')" in source
    assert "origin_type = 'deterministic_reference'" in source
    assert "SET first_seen_at = valid_from" in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source


def test_r2_purge_completion_migration_is_additive_and_forward_only() -> None:
    source = PURGE_MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "20260827_0015",
        "down_revision": "20260827_0014",
    }
    assert "ALTER TABLE research_sessions" in source
    assert "ADD COLUMN purged_at timestamptz" in source
    assert "research_sessions_purge_completion_ck" in source
    assert "research_sessions_purge_pending_idx" in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source


def test_r2_owner_migration_is_additive_indexed_and_forward_only() -> None:
    source = OWNERSHIP_MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "20260828_0016",
        "down_revision": "20260827_0015",
    }
    assert "ADD COLUMN owner_principal_sha256 char(64)" in source
    assert "owner_principal_sha256 SET NOT NULL" in source
    legacy_owner = hashlib.sha256(b"local-research-user").hexdigest()
    assert legacy_owner in source
    assert "research_sessions_owner_history_idx" in source
    assert "research_sessions_owner_active_idx" in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source


def test_r2_artifact_version_migration_is_additive_and_forward_only() -> None:
    source = ARTIFACT_VERSION_MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "20260828_0017",
        "down_revision": "20260828_0016",
    }
    assert "ALTER TABLE research_reports" in source
    assert "ADD COLUMN artifact_object_version_id text" in source
    assert "research_reports_artifact_version_ck" in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source


def test_r2_web_retrieval_migration_is_minimal_expiring_and_forward_only() -> None:
    source = WEB_RETRIEVAL_MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "20260830_0018",
        "down_revision": "20260828_0017",
    }
    assert "CREATE TABLE research_web_retrieval_receipts" in source
    assert "query_sha256 char(64)" in source
    assert "candidate_url_sha256 char(64)[]" in source
    assert "selected_url_sha256 char(64)" in source
    assert "expires_at <= created_at + interval '7 days'" in source
    assert "Tavily" not in source
    assert "summary" not in source.casefold()
    assert "raw_content" not in source
    assert "DROP TABLE" not in source
    assert "FORWARD_ONLY_REASON" in source


def test_r2_cutoff_mode_migration_backfills_legacy_rows_and_is_forward_only() -> None:
    source = CUTOFF_MODE_MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in {"revision", "down_revision"}
    }

    assert assignments == {
        "revision": "20260904_0019",
        "down_revision": "20260830_0018",
    }
    assert "ALTER TABLE research_sessions" in source
    assert "ADD COLUMN cutoff_mode text NOT NULL DEFAULT 'historical_replay'" in source
    assert "cutoff_mode IN ('current_run', 'historical_replay')" in source
    assert "UPDATE research_sessions" not in source
    assert "DROP COLUMN" not in source
    assert "DROP TABLE" not in source
    assert "raise RuntimeError(FORWARD_ONLY_REASON)" in source
