from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

VERSIONS = Path("infra/migrations/versions")
INITIAL = VERSIONS / "20260721_0001_initial.py"
EXPAND = VERSIONS / "20260723_0002_r0p_expand.py"
BACKFILL = VERSIONS / "20260723_0003_r0p_backfill.py"
VALIDATE = VERSIONS / "20260723_0004_r0p_validate.py"
CONTRACT = VERSIONS / "20260723_0005_r0p_contract.py"
KNOWLEDGE_TIME = VERSIONS / "20260723_0006_job_transition_knowledge_time.py"
ACTIVE_TARGET = VERSIONS / "20260728_0007_active_target_repository.py"
SOURCE_CURSOR_CAS = VERSIONS / "20260730_0008_source_cursor_lease_cas.py"
SOURCE_PROVENANCE = VERSIONS / "20260801_0009_r1_source_provenance.py"
SOURCE_PROVENANCE_V1_2 = VERSIONS / "20260802_0010_r1_source_provenance_v1_2.py"
SOURCE_REFRESH_STATE = VERSIONS / "20260804_0011_source_cursor_refresh_state.py"
BEHAVIOR_PRESSURE = VERSIONS / "20260807_0012_behavior_pressure.py"
NOTIFICATION_INBOX = VERSIONS / "20260807_0013_notification_inbox.py"
R2_RESEARCH = VERSIONS / "20260827_0014_r2_research_sessions.py"
R2_PURGE_COMPLETION = VERSIONS / "20260827_0015_research_session_purge_completion.py"
R2_OWNERSHIP = VERSIONS / "20260828_0016_research_session_ownership.py"
R2_ARTIFACT_VERSIONS = VERSIONS / "20260828_0017_research_artifact_versions.py"
R2_WEB_RETRIEVAL = VERSIONS / "20260830_0018_research_web_retrieval_receipts.py"
R2_CUTOFF_MODE = VERSIONS / "20260904_0019_research_cutoff_mode.py"
R2_CHECKPOINT = VERSIONS / "20260905_0020_research_checkpoint.py"
R2_REAL_MODE = VERSIONS / "20260908_0021_real_research_mode.py"

INITIAL_SHA256 = "f9d260ec0e3fd08b796fd60b41387ca2cc7eea8a9aa411ba21d773d2fd6909d2"


def _migration_metadata(path: Path) -> tuple[str, str | None]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str | None] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id not in {"revision", "down_revision"}:
            continue
        if node.value is None:
            raise AssertionError(f"{node.target.id} must have a literal value in {path}")
        value = ast.literal_eval(node.value)
        if value is not None and not isinstance(value, str):
            raise AssertionError(f"{node.target.id} must be a string or null in {path}")
        values[node.target.id] = value
    revision, parent = values["revision"], values["down_revision"]
    if revision is None:
        raise AssertionError(f"revision cannot be null in {path}")
    return revision, parent


@pytest.mark.contract
def test_published_initial_migration_is_byte_immutable() -> None:
    assert hashlib.sha256(INITIAL.read_bytes()).hexdigest() == INITIAL_SHA256


@pytest.mark.contract
def test_r0p_migration_graph_has_one_ordered_head() -> None:
    metadata = {
        path: _migration_metadata(path)
        for path in sorted(VERSIONS.glob("*.py"))
        if path.name != "__init__.py"
    }
    revisions = {revision for revision, _ in metadata.values()}
    parents = {parent for _, parent in metadata.values() if parent is not None}
    heads = revisions - parents

    assert heads == {"20260908_0021"}
    assert metadata[EXPAND] == ("20260723_0002", "20260721_0001")
    assert metadata[BACKFILL] == ("20260723_0003", "20260723_0002")
    assert metadata[VALIDATE] == ("20260723_0004", "20260723_0003")
    assert metadata[CONTRACT] == ("20260723_0005", "20260723_0004")
    assert metadata[KNOWLEDGE_TIME] == ("20260723_0006", "20260723_0005")
    assert metadata[ACTIVE_TARGET] == ("20260728_0007", "20260723_0006")
    assert metadata[SOURCE_CURSOR_CAS] == ("20260730_0008", "20260728_0007")
    assert metadata[SOURCE_PROVENANCE] == ("20260801_0009", "20260730_0008")
    assert metadata[SOURCE_PROVENANCE_V1_2] == ("20260802_0010", "20260801_0009")
    assert metadata[SOURCE_REFRESH_STATE] == ("20260804_0011", "20260802_0010")
    assert metadata[BEHAVIOR_PRESSURE] == ("20260807_0012", "20260804_0011")
    assert metadata[NOTIFICATION_INBOX] == ("20260807_0013", "20260807_0012")
    assert metadata[R2_RESEARCH] == ("20260827_0014", "20260807_0013")
    assert metadata[R2_PURGE_COMPLETION] == ("20260827_0015", "20260827_0014")
    assert metadata[R2_OWNERSHIP] == ("20260828_0016", "20260827_0015")
    assert metadata[R2_ARTIFACT_VERSIONS] == ("20260828_0017", "20260828_0016")
    assert metadata[R2_WEB_RETRIEVAL] == ("20260830_0018", "20260828_0017")
    assert metadata[R2_CUTOFF_MODE] == ("20260904_0019", "20260830_0018")
    assert metadata[R2_CHECKPOINT] == ("20260905_0020", "20260904_0019")
    assert metadata[R2_REAL_MODE] == ("20260908_0021", "20260905_0020")


@pytest.mark.contract
def test_source_cursor_migration_adds_monotonic_version_and_lease() -> None:
    sql = SOURCE_CURSOR_CAS.read_text(encoding="utf-8")

    assert "ALTER TABLE source_cursors" in sql
    assert "cursor_version bigint NOT NULL DEFAULT 0" in sql
    assert "lease_owner text" in sql
    assert "lease_expires_at timestamptz" in sql
    assert "source_cursors_lease_pair_ck" in sql
    assert "source_cursors_cursor_version_ck" in sql
    assert "raise RuntimeError(FORWARD_ONLY_REASON)" in sql


@pytest.mark.contract
def test_r1_source_provenance_migration_is_nullable_append_only_and_pit_safe() -> None:
    sql = SOURCE_PROVENANCE.read_text(encoding="utf-8")

    assert "ADD COLUMN event_time timestamptz" in sql
    assert "ADD COLUMN provenance jsonb" in sql
    assert "event_time IS NULL OR event_time <= first_seen_at" in sql
    assert "(event_time IS NULL) = (provenance IS NULL)" in sql
    assert "provenance - ARRAY[" in sql
    assert ") = '{}'::jsonb" in sql
    for required_key in (
        "schema_version",
        "provider",
        "access_channel",
        "adapter_name",
        "adapter_version",
        "endpoint_name",
        "source_identifier",
        "runtime_mode",
        "release_tier",
        "rights_status",
    ):
        assert f"'{required_key}'" in sql
    assert "raw_documents_event_time_pit_idx" in sql
    assert "UPDATE raw_documents" not in sql
    assert "raise RuntimeError(FORWARD_ONLY_REASON)" in sql


@pytest.mark.contract
def test_r1_source_provenance_v1_2_migration_is_add_only_and_exact() -> None:
    sql = SOURCE_PROVENANCE_V1_2.read_text(encoding="utf-8")

    assert "ADD COLUMN" not in sql
    assert "UPDATE raw_documents" not in sql
    assert "20260802_0010" in sql
    assert "20260801_0009" in sql
    assert "quality_tier" in sql
    assert "evidence_role" in sql
    assert "official_http" in sql
    assert "baostock_sdk" in sql
    assert "public_static" in sql
    assert "R1 provenance 1.2 rows require removal or a forward-fix before downgrade" in sql


@pytest.mark.contract
def test_source_refresh_state_separates_watermark_from_success_time() -> None:
    sql = SOURCE_REFRESH_STATE.read_text(encoding="utf-8")

    assert "ADD COLUMN business_watermark timestamptz" in sql
    assert "SET business_watermark = last_success_at" in sql
    assert "source_cursors_refresh_state_ck" in sql
    assert "business_watermark <= last_success_at" in sql
    assert "VALIDATE CONSTRAINT source_cursors_refresh_state_ck" in sql
    assert "raise RuntimeError(FORWARD_ONLY_REASON)" in sql


@pytest.mark.contract
def test_active_target_migration_is_append_only_pit_and_idempotency_safe() -> None:
    sql = ACTIVE_TARGET.read_text(encoding="utf-8")

    assert "CREATE TABLE active_target_revisions" in sql
    assert "PRIMARY KEY (watchlist_id, revision)" in sql
    assert "recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()" in sql
    assert "active_target_revisions_pit_lookup_idx" in sql
    assert "CREATE TABLE active_target_write_receipts" in sql
    assert "idempotency_key text PRIMARY KEY" in sql
    assert "request_sha256 char(64) NOT NULL" in sql
    assert "raise RuntimeError(FORWARD_ONLY_REASON)" in sql


@pytest.mark.contract
def test_event_revision_migration_preserves_row_identity_and_pit_history() -> None:
    initial_sql = INITIAL.read_text(encoding="utf-8")
    expand_sql = EXPAND.read_text(encoding="utf-8")
    backfill_sql = BACKFILL.read_text(encoding="utf-8")
    validate_sql = VALIDATE.read_text(encoding="utf-8")
    contract_sql = CONTRACT.read_text(encoding="utf-8")

    assert "id uuid PRIMARY KEY" in initial_sql
    assert "DROP COLUMN id" not in expand_sql + backfill_sql + validate_sql + contract_sql
    assert "logical_event_id uuid" in expand_sql
    assert "revision_known_at timestamptz" in expand_sql
    assert "supersedes_revision_id uuid" in expand_sql
    assert "market_events_logical_pit_idx" in expand_sql
    assert "market_events_knowledge_cutoff_idx" in expand_sql
    assert "market_events_publication_visibility_ck" in expand_sql
    assert "first_revision.id" in backfill_sql
    assert "predecessor.revision = current.revision - 1" in backfill_sql
    assert "revision_known_at < first_seen_at" in validate_sql
    assert "market_events_logical_revision_uidx" in contract_sql
    assert "market_events_supersedes_uidx" in contract_sql
    assert "market_events_validate_revision_chain_trg" in contract_sql


@pytest.mark.contract
def test_backfill_does_not_invent_ambiguous_wire_identity() -> None:
    sql = BACKFILL.read_text(encoding="utf-8")
    ambiguous_fields = (
        "request_id",
        "correlation_id",
        "request_schema_version",
        "result_schema_version",
        "producer",
        "idempotency_key",
    )

    assert "gen_random_uuid" not in sql
    assert "uuid_generate" not in sql
    assert "JOB_WIRE_CONTEXT_UNRECOVERABLE" in sql
    assert "OUTBOX_ENVELOPE_IDENTITY_UNRECOVERABLE" in sql
    assert "migration_repair_queue" in sql
    for field in ambiguous_fields:
        assert f"OR {field} IS NULL" in sql or f"WHERE {field} IS NULL" in sql


@pytest.mark.contract
def test_missing_event_predecessor_is_quarantined_not_reselected_forever() -> None:
    sql = BACKFILL.read_text(encoding="utf-8")
    selection_start = sql.index("WITH batch AS")
    selection_end = sql.index("),\n            changed AS", selection_start)
    batch_selection = sql[selection_start:selection_end]

    assert "supersedes_revision_id IS NULL" in batch_selection
    assert "AND EXISTS (" in batch_selection
    assert "direct_predecessor.revision =" in batch_selection
    assert "market_events.revision - 1" in batch_selection
    assert "MARKET_EVENT_REVISION_CHAIN_INVALID" in sql


@pytest.mark.contract
def test_job_storage_has_all_statuses_and_an_immutable_transition_ledger() -> None:
    expand_sql = EXPAND.read_text(encoding="utf-8")
    backfill_sql = BACKFILL.read_text(encoding="utf-8")
    validate_sql = VALIDATE.read_text(encoding="utf-8")

    for status in (
        "queued",
        "running",
        "succeeded",
        "degraded",
        "skipped",
        "failed",
        "cancelled",
        "expired",
    ):
        assert f"'{status}'" in expand_sql

    assert "CHECK (attempt >= 1" in expand_sql
    assert "CREATE TABLE job_run_transitions" in expand_sql
    assert "job_run_transitions_append_only_trg" in expand_sql
    assert "BEFORE UPDATE OR DELETE ON job_run_transitions" in expand_sql
    assert "job_run_transitions_validate_insert_trg" in expand_sql
    assert "job_runs_result_state_ck" in expand_sql
    assert "job_runs_capture_transition_trg" in backfill_sql
    assert "AFTER INSERT OR UPDATE OF status, attempt" in backfill_sql
    assert "job(s) without transition history" in validate_sql
    assert "job/ledger state mismatch(es)" in validate_sql
    assert "non-contiguous transition ledger(s)" in validate_sql


@pytest.mark.contract
def test_job_transition_knowledge_time_is_database_owned_and_queryable() -> None:
    knowledge_time_sql = KNOWLEDGE_TIME.read_text(encoding="utf-8")

    assert "ADD COLUMN recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP" in (
        knowledge_time_sql
    )
    assert "NEW.recorded_at := clock_timestamp()" in knowledge_time_sql
    assert "BEFORE INSERT ON job_run_transitions" in knowledge_time_sql
    assert "job_run_transitions_pit_recovery_idx" in knowledge_time_sql
    assert "recorded_at" in knowledge_time_sql
    assert "occurred_at" in knowledge_time_sql


@pytest.mark.contract
def test_outbox_and_technical_inbox_match_delivery_invariants() -> None:
    expand_sql = EXPAND.read_text(encoding="utf-8")
    contract_sql = CONTRACT.read_text(encoding="utf-8")

    for field in (
        "message_id",
        "event_name",
        "envelope_schema_version",
        "payload_schema_name",
        "payload_schema_version",
        "payload_schema_sha256",
        "producer",
        "occurred_at",
        "produced_at",
        "correlation_id",
        "causation_id",
        "partition_key",
        "idempotency_key",
        "lease_owner",
        "leased_at",
        "lease_expires_at",
    ):
        assert f"ADD COLUMN {field} " in expand_sql

    assert "outbox_events_business_message_uidx" in contract_sql
    assert "(producer, event_name, idempotency_key)" in contract_sql
    assert "outbox_events_event_name_match_ck" in expand_sql
    assert "CREATE TABLE message_inbox_receipts" in expand_sql
    assert "Technical consumer deduplication ledger" in expand_sql
    assert "user-facing notification inbox" in expand_sql
    assert "UNIQUE (consumer_id, event_name, idempotency_key)" in expand_sql


@pytest.mark.contract
def test_validate_fails_closed_before_contract_constraints() -> None:
    validate_sql = VALIDATE.read_text(encoding="utf-8")
    contract_sql = CONTRACT.read_text(encoding="utf-8")

    assert "resolved_at IS NULL" in validate_sql
    assert "incomplete checkpoint(s)" in validate_sql
    assert "required checkpoint(s) missing" in validate_sql
    assert "duplicate logical event revision(s)" in validate_sql
    assert "broken supersedes link(s)" in validate_sql
    assert "duplicate business message(s)" in validate_sql
    assert validate_sql.index("VALIDATION_BLOCK") < validate_sql.index("VALIDATE CONSTRAINT")
    assert "ALTER COLUMN logical_event_id SET NOT NULL" in contract_sql
    assert "ALTER COLUMN request_schema_name SET NOT NULL" in contract_sql
    assert "ALTER COLUMN message_id SET NOT NULL" in contract_sql


@pytest.mark.contract
def test_all_r0p_revisions_declare_a_forward_repair_strategy() -> None:
    for path in (EXPAND, BACKFILL, VALIDATE, CONTRACT, KNOWLEDGE_TIME):
        source = path.read_text(encoding="utf-8")
        assert "FORWARD_ONLY_REASON" in source
        assert "raise RuntimeError(FORWARD_ONLY_REASON)" in source
        assert "forward" in source.lower()
