"""Add R2 on-demand research sessions, reports and stable macro facts.

Revision ID: 20260827_0014
Revises: 20260807_0013
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0014"
down_revision: str | None = "20260807_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Research tombstones, report hashes and session transition history are audit evidence; "
    "reverse this schema with a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE outbox_events
        DROP CONSTRAINT outbox_events_wire_identity_ck,
        ADD CONSTRAINT outbox_events_wire_identity_ck
            CHECK (
                event_name ~
                    '^com\\.stockassistant\\.[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+\\.v[1-9][0-9]*$'
                AND envelope_schema_version ~ '^[1-9][0-9]*\\.[0-9]+$'
                AND payload_schema_name ~ '^[a-z][a-z0-9_.-]{1,199}$'
                AND payload_schema_version ~ '^[1-9][0-9]*\\.[0-9]+$'
                AND payload_schema_sha256 ~ '^[a-f0-9]{64}$'
                AND producer ~ '^[a-z][a-z0-9_-]{1,99}$'
                AND length(idempotency_key) BETWEEN 16 AND 200
                AND length(partition_key) BETWEEN 1 AND 200
            )
    """,
    """
    ALTER TABLE instruments
        ADD COLUMN first_seen_at timestamptz,
        ADD COLUMN revision_known_at timestamptz
    """,
    """
    UPDATE instruments
       SET first_seen_at = valid_from,
           revision_known_at = valid_from
    """,
    """
    ALTER TABLE instruments
        ALTER COLUMN first_seen_at SET NOT NULL,
        ALTER COLUMN revision_known_at SET NOT NULL
    """,
    """
    CREATE INDEX instruments_search_pit_idx
        ON instruments (ticker, name, valid_from, revision_known_at)
    """,
    """
    CREATE TABLE research_sessions (
        session_id uuid PRIMARY KEY,
        job_id uuid NOT NULL UNIQUE,
        instrument_id text NOT NULL CHECK (
            instrument_id ~ '^(XSHG|XSHE|XBSE):[0-9]{6}$'
        ),
        instrument_name text NOT NULL,
        as_of timestamptz NOT NULL,
        analysis_config jsonb NOT NULL,
        source_mode text NOT NULL CHECK (
            source_mode IN ('synthetic_development_only', 'production_authorized')
        ),
        idempotency_key text NOT NULL UNIQUE,
        request_sha256 char(64) NOT NULL,
        status text NOT NULL CHECK (
            status IN (
                'queued', 'running', 'succeeded', 'failed', 'cancelled',
                'stalled', 'expired', 'deleted'
            )
        ),
        stage text NOT NULL CHECK (
            stage IN (
                'queued', 'resolving_instrument', 'collecting_history',
                'analyzing_business', 'analyzing_financials', 'analyzing_valuation',
                'analyzing_events', 'debating_cases', 'validating_report', 'completed'
            )
        ),
        progress_message text NOT NULL,
        heartbeat_at timestamptz NOT NULL,
        checkpoint text,
        coverage_gaps jsonb NOT NULL DEFAULT '[]'::jsonb,
        failure_reason_code text,
        retryable boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        completed_at timestamptz,
        expires_at timestamptz NOT NULL,
        deleted_at timestamptz,
        purge_due_at timestamptz,
        row_version integer NOT NULL DEFAULT 1 CHECK (row_version >= 1),
        CONSTRAINT research_sessions_time_ck CHECK (
            updated_at >= created_at
            AND heartbeat_at >= created_at
            AND expires_at > created_at
            AND (completed_at IS NULL OR completed_at >= created_at)
            AND (deleted_at IS NULL OR purge_due_at <= deleted_at + interval '24 hours')
        )
    )
    """,
    """
    CREATE INDEX research_sessions_history_idx
        ON research_sessions (created_at DESC, session_id DESC)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE INDEX research_sessions_watchdog_idx
        ON research_sessions (heartbeat_at)
        WHERE status IN ('queued', 'running') AND deleted_at IS NULL
    """,
    """
    CREATE TABLE research_session_transitions (
        transition_id bigserial PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES research_sessions(session_id),
        from_status text,
        to_status text NOT NULL,
        stage text NOT NULL,
        message text NOT NULL,
        checkpoint text,
        reason_code text,
        occurred_at timestamptz NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT research_transition_knowledge_ck CHECK (recorded_at >= occurred_at)
    )
    """,
    """
    CREATE INDEX research_session_transitions_pit_idx
        ON research_session_transitions (session_id, occurred_at, recorded_at, transition_id)
    """,
    """
    CREATE TABLE research_job_outbox (
        outbox_id uuid PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES research_sessions(session_id),
        job_id uuid NOT NULL UNIQUE,
        task_name text NOT NULL CHECK (
            task_name = 'stock_assistant.research.generate_report.v1'
        ),
        payload jsonb NOT NULL,
        idempotency_key text NOT NULL UNIQUE,
        status text NOT NULL CHECK (
            status IN ('pending', 'dispatched', 'completed', 'cancelled')
        ),
        attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 100),
        created_at timestamptz NOT NULL,
        dispatched_at timestamptz,
        completed_at timestamptz
    )
    """,
    """
    CREATE INDEX research_job_outbox_pending_idx
        ON research_job_outbox (created_at, outbox_id)
        WHERE status = 'pending'
    """,
    """
    CREATE TABLE research_session_mutation_receipts (
        idempotency_key text PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES research_sessions(session_id),
        operation text NOT NULL CHECK (operation IN ('cancel', 'retry', 'delete')),
        result jsonb NOT NULL,
        created_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE research_reports (
        report_id uuid PRIMARY KEY,
        session_id uuid NOT NULL UNIQUE REFERENCES research_sessions(session_id),
        report jsonb,
        generator_version text NOT NULL,
        model_provider text NOT NULL,
        model_name text NOT NULL,
        model_version text NOT NULL,
        validator_version text NOT NULL,
        rule_version text NOT NULL,
        data_manifest_sha256 char(64) NOT NULL,
        report_sha256 char(64) NOT NULL,
        artifact_object_key text,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        inaccessible_at timestamptz,
        purged_at timestamptz,
        CONSTRAINT research_reports_retention_ck CHECK (
            expires_at <= created_at + interval '7 days'
            AND expires_at > created_at
            AND (purged_at IS NULL OR report IS NULL)
        )
    )
    """,
    """
    CREATE INDEX research_reports_cleanup_idx
        ON research_reports (expires_at, inaccessible_at)
        WHERE purged_at IS NULL
    """,
    """
    CREATE TABLE research_report_claims (
        report_id uuid NOT NULL REFERENCES research_reports(report_id),
        claim_id uuid NOT NULL,
        claim_type text NOT NULL CHECK (
            claim_type IN ('fact', 'system_inference', 'model_hypothesis')
        ),
        section text NOT NULL,
        claim jsonb NOT NULL,
        evidence_ids uuid[] NOT NULL,
        publication_eligible boolean NOT NULL CHECK (publication_eligible),
        created_at timestamptz NOT NULL,
        PRIMARY KEY (report_id, claim_id),
        CONSTRAINT research_fact_evidence_ck CHECK (
            claim_type <> 'fact' OR cardinality(evidence_ids) > 0
        )
    )
    """,
    """
    CREATE TABLE research_session_contexts (
        context_id uuid PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES research_sessions(session_id),
        kind text NOT NULL CHECK (
            kind IN ('session_text', 'model_intermediate', 'equity_embedding', 'object_artifact')
        ),
        content jsonb,
        object_key text,
        content_sha256 char(64) NOT NULL,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        deleted_at timestamptz,
        CONSTRAINT research_session_context_retention_ck CHECK (
            expires_at > created_at AND expires_at <= created_at + interval '7 days'
        ),
        CONSTRAINT research_session_context_payload_ck CHECK (
            content IS NOT NULL OR object_key IS NOT NULL OR deleted_at IS NOT NULL
        )
    )
    """,
    """
    CREATE INDEX research_session_contexts_cleanup_idx
        ON research_session_contexts (expires_at)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE TABLE macro_reference_facts (
        fact_id uuid PRIMARY KEY,
        namespace text NOT NULL,
        subject_scope text NOT NULL CHECK (
            subject_scope IN ('macro', 'institutional', 'taxonomy', 'historical_reference')
        ),
        temporal_stability text NOT NULL CHECK (
            temporal_stability IN ('stable', 'historical_final')
        ),
        origin_type text NOT NULL CHECK (origin_type = 'deterministic_reference'),
        subject text NOT NULL,
        predicate text NOT NULL,
        object_value jsonb NOT NULL,
        fact_class text NOT NULL CHECK (
            fact_class IN (
                'definition', 'institutional_relationship', 'stable_classification',
                'versioned_interpretation_rule', 'confirmed_historical_fact'
            )
        ),
        interpretation_rule_version text NOT NULL,
        valid_from timestamptz NOT NULL,
        valid_until timestamptz,
        first_seen_at timestamptz NOT NULL,
        revision_known_at timestamptz NOT NULL,
        revision integer NOT NULL CHECK (revision >= 1),
        evidence_ids uuid[] NOT NULL,
        content_sha256 char(64) NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT macro_reference_fact_time_ck CHECK (
            revision_known_at >= first_seen_at
            AND (valid_until IS NULL OR valid_until > valid_from)
        ),
        CONSTRAINT macro_reference_fact_evidence_ck CHECK (cardinality(evidence_ids) > 0)
    )
    """,
    """
    CREATE INDEX macro_reference_facts_pit_idx
        ON macro_reference_facts (namespace, subject, predicate, valid_from, revision_known_at)
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
