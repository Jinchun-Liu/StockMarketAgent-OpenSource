"""Expand storage for stable event revisions, jobs and message delivery.

Revision ID: 20260723_0002
Revises: 20260721_0001

This is the EXPAND step of the R0P storage train.  Every new column remains
nullable until deterministic backfill and validation have completed.  The
migration is intentionally forward-only: dropping the technical inbox,
transition ledger or repair evidence would destroy audit history.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0002"
down_revision: str | None = "20260721_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "R0P expand is forward-only because downgrade would delete transition, "
    "deduplication and repair evidence; apply a new forward-fix revision instead"
)

JOB_STATUSES_SQL = (
    "'queued', 'running', 'succeeded', 'degraded', 'skipped', 'failed', 'cancelled', 'expired'"
)


UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE migration_repair_queue (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        migration_revision text NOT NULL,
        entity_table text NOT NULL,
        entity_key text NOT NULL,
        reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
        safe_details jsonb NOT NULL DEFAULT '{}'::jsonb,
        detected_at timestamptz NOT NULL DEFAULT now(),
        resolved_at timestamptz,
        resolution_note text,
        CHECK (
            (resolved_at IS NULL AND resolution_note IS NULL)
            OR (resolved_at IS NOT NULL AND resolution_note IS NOT NULL)
        )
    )
    """,
    """
    CREATE UNIQUE INDEX migration_repair_queue_unresolved_uidx
        ON migration_repair_queue (
            migration_revision,
            entity_table,
            entity_key,
            reason_code
        )
        WHERE resolved_at IS NULL
    """,
    """
    CREATE INDEX migration_repair_queue_status_idx
        ON migration_repair_queue (migration_revision, detected_at, id)
        WHERE resolved_at IS NULL
    """,
    """
    CREATE TABLE migration_backfill_checkpoints (
        migration_revision text NOT NULL,
        operation_name text NOT NULL,
        last_key text,
        processed_rows bigint NOT NULL DEFAULT 0 CHECK (processed_rows >= 0),
        completed boolean NOT NULL DEFAULT false,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (migration_revision, operation_name)
    )
    """,
    """
    ALTER TABLE market_events
        ADD COLUMN logical_event_id uuid,
        ADD COLUMN revision_known_at timestamptz,
        ADD COLUMN supersedes_revision_id uuid
    """,
    """
    ALTER TABLE market_events
        ADD CONSTRAINT market_events_revision_known_at_ck
            CHECK (revision_known_at >= first_seen_at) NOT VALID,
        ADD CONSTRAINT market_events_publication_visibility_ck
            CHECK (first_seen_at >= published_at) NOT VALID,
        ADD CONSTRAINT market_events_supersedes_not_self_ck
            CHECK (supersedes_revision_id IS NULL OR supersedes_revision_id <> id)
            NOT VALID,
        ADD CONSTRAINT market_events_supersedes_revision_fk
            FOREIGN KEY (supersedes_revision_id) REFERENCES market_events(id)
            NOT VALID
    """,
    """
    CREATE INDEX market_events_logical_pit_idx
        ON market_events (
            logical_event_id,
            revision_known_at DESC,
            revision DESC,
            id
        )
    """,
    """
    CREATE INDEX market_events_knowledge_cutoff_idx
        ON market_events (revision_known_at, first_seen_at, id)
    """,
    """
    CREATE FUNCTION r0p_fill_market_event_revision_identity()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $r0p$
    DECLARE
        predecessor market_events%ROWTYPE;
    BEGIN
        IF NEW.revision = 1 THEN
            IF NEW.logical_event_id IS NULL THEN
                NEW.logical_event_id := NEW.id;
            END IF;
            IF NEW.supersedes_revision_id IS NOT NULL THEN
                RAISE EXCEPTION 'revision 1 cannot supersede another revision'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            SELECT *
              INTO predecessor
              FROM market_events
             WHERE cluster_id = NEW.cluster_id
               AND revision = NEW.revision - 1;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'market event predecessor is missing'
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.logical_event_id IS NULL THEN
                NEW.logical_event_id := predecessor.logical_event_id;
            ELSIF NEW.logical_event_id <> predecessor.logical_event_id THEN
                RAISE EXCEPTION 'logical event identity differs from predecessor'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.supersedes_revision_id IS NULL THEN
                NEW.supersedes_revision_id := predecessor.id;
            ELSIF NEW.supersedes_revision_id <> predecessor.id THEN
                RAISE EXCEPTION 'supersedes revision is not the direct predecessor'
                    USING ERRCODE = '23514';
            END IF;
        END IF;

        IF NEW.revision_known_at IS NULL THEN
            NEW.revision_known_at := GREATEST(NEW.first_seen_at, NEW.created_at);
        END IF;
        RETURN NEW;
    END;
    $r0p$
    """,
    """
    CREATE TRIGGER market_events_fill_revision_identity_trg
        BEFORE INSERT ON market_events
        FOR EACH ROW
        EXECUTE FUNCTION r0p_fill_market_event_revision_identity()
    """,
    """
    ALTER TABLE job_runs
        ADD COLUMN request_schema_name text,
        ADD COLUMN request_schema_version text,
        ADD COLUMN request_schema_sha256 char(64),
        ADD COLUMN result_schema_name text,
        ADD COLUMN result_schema_version text,
        ADD COLUMN result_schema_sha256 char(64),
        ADD COLUMN request_id uuid,
        ADD COLUMN correlation_id uuid,
        ADD COLUMN causation_id uuid,
        ADD COLUMN actor_id text,
        ADD COLUMN workspace_id text,
        ADD COLUMN as_of timestamptz,
        ADD COLUMN requested_at timestamptz,
        ADD COLUMN queued_at timestamptz,
        ADD COLUMN deadline_at timestamptz,
        ADD COLUMN expires_at timestamptz,
        ADD COLUMN queue_name text,
        ADD COLUMN max_attempts integer DEFAULT 3,
        ADD COLUMN request_payload jsonb,
        ADD COLUMN result_payload jsonb
    """,
    """
    ALTER TABLE job_runs
        ALTER COLUMN attempt SET DEFAULT 1,
        DROP CONSTRAINT IF EXISTS job_runs_status_check
    """,
    f"""
    ALTER TABLE job_runs
        ADD CONSTRAINT job_runs_status_check
            CHECK (status IN ({JOB_STATUSES_SQL})) NOT VALID,
        ADD CONSTRAINT job_runs_attempt_ck
            CHECK (attempt >= 1 AND max_attempts >= attempt AND max_attempts <= 100)
            NOT VALID,
        ADD CONSTRAINT job_runs_request_schema_hash_ck
            CHECK (
                request_schema_sha256 IS NULL
                OR request_schema_sha256 ~ '^[a-f0-9]{{64}}$'
            ) NOT VALID,
        ADD CONSTRAINT job_runs_result_schema_hash_ck
            CHECK (
                result_schema_sha256 IS NULL
                OR result_schema_sha256 ~ '^[a-f0-9]{{64}}$'
            ) NOT VALID,
        ADD CONSTRAINT job_runs_wire_identity_ck
            CHECK (
                task_name ~
                    '^stock_assistant\\.[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*\\.v[1-9][0-9]*$'
                AND length(idempotency_key) BETWEEN 16 AND 200
                AND (
                    request_schema_name IS NULL
                    OR request_schema_name ~ '^[a-z][a-z0-9_.-]{{1,199}}$'
                )
                AND (
                    result_schema_name IS NULL
                    OR result_schema_name ~ '^[a-z][a-z0-9_.-]{{1,199}}$'
                )
                AND (
                    request_schema_version IS NULL
                    OR request_schema_version ~ '^[1-9][0-9]*\\.[0-9]+$'
                )
                AND (
                    result_schema_version IS NULL
                    OR result_schema_version ~ '^[1-9][0-9]*\\.[0-9]+$'
                )
                AND (
                    request_payload IS NULL
                    OR jsonb_typeof(request_payload) = 'object'
                )
                AND (
                    result_payload IS NULL
                    OR jsonb_typeof(result_payload) = 'object'
                )
            ) NOT VALID,
        ADD CONSTRAINT job_runs_timing_ck
            CHECK (
                (started_at IS NULL OR queued_at IS NULL OR started_at >= queued_at)
                AND (
                    finished_at IS NULL
                    OR started_at IS NULL
                    OR finished_at >= started_at
                )
                AND (
                    expires_at IS NULL
                    OR expires_at > COALESCE(scheduled_for, queued_at, requested_at)
                )
            ) NOT VALID,
        ADD CONSTRAINT job_runs_terminal_state_ck
            CHECK (
                (
                    status IN ('queued', 'running')
                    AND finished_at IS NULL
                )
                OR (
                    status IN (
                        'succeeded',
                        'degraded',
                        'skipped',
                        'failed',
                        'cancelled',
                        'expired'
                    )
                    AND finished_at IS NOT NULL
                )
            ) NOT VALID,
        ADD CONSTRAINT job_runs_result_state_ck
            CHECK (
                (
                    status IN ('succeeded', 'degraded', 'skipped')
                    AND result_payload IS NOT NULL
                    AND error_code IS NULL
                    AND safe_error_message IS NULL
                )
                OR (
                    status = 'failed'
                    AND result_payload IS NULL
                    AND error_code IS NOT NULL
                    AND safe_error_message IS NOT NULL
                )
                OR (
                    status IN ('queued', 'running', 'cancelled', 'expired')
                    AND result_payload IS NULL
                )
            ) NOT VALID
    """,
    f"""
    CREATE TABLE job_run_transitions (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        job_run_id uuid NOT NULL REFERENCES job_runs(id),
        sequence_number integer NOT NULL CHECK (sequence_number >= 1),
        from_status text,
        to_status text NOT NULL CHECK (to_status IN ({JOB_STATUSES_SQL})),
        attempt integer NOT NULL CHECK (attempt >= 1 AND attempt <= 100),
        occurred_at timestamptz NOT NULL,
        request_id uuid,
        correlation_id uuid,
        reason_code text CHECK (
            reason_code IS NULL OR reason_code ~ '^[A-Z][A-Z0-9_]{{1,63}}$'
        ),
        safe_metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        CHECK (from_status IS NULL OR from_status IN ({JOB_STATUSES_SQL})),
        CHECK (
            (sequence_number = 1 AND from_status IS NULL)
            OR (sequence_number > 1 AND from_status IS NOT NULL)
        ),
        UNIQUE (job_run_id, sequence_number)
    )
    """,
    """
    CREATE INDEX job_run_transitions_recovery_idx
        ON job_run_transitions (job_run_id, sequence_number DESC)
    """,
    """
    CREATE FUNCTION r0p_reject_job_transition_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $r0p$
    BEGIN
        RAISE EXCEPTION 'job_run_transitions is append-only'
            USING ERRCODE = '55000';
    END;
    $r0p$
    """,
    """
    CREATE TRIGGER job_run_transitions_append_only_trg
        BEFORE UPDATE OR DELETE ON job_run_transitions
        FOR EACH ROW
        EXECUTE FUNCTION r0p_reject_job_transition_mutation()
    """,
    """
    CREATE FUNCTION r0p_validate_job_transition_insert()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $r0p$
    DECLARE
        current_job_status text;
        current_job_attempt integer;
        previous_sequence integer;
        previous_status text;
    BEGIN
        SELECT status, attempt
          INTO current_job_status, current_job_attempt
          FROM job_runs
         WHERE id = NEW.job_run_id;

        IF NOT FOUND
           OR NEW.to_status <> current_job_status
           OR NEW.attempt <> current_job_attempt THEN
            RAISE EXCEPTION 'job transition does not match current job state'
                USING ERRCODE = '23514';
        END IF;

        SELECT sequence_number, to_status
          INTO previous_sequence, previous_status
          FROM job_run_transitions
         WHERE job_run_id = NEW.job_run_id
         ORDER BY sequence_number DESC
         LIMIT 1;

        IF NOT FOUND THEN
            IF NEW.sequence_number <> 1 OR NEW.from_status IS NOT NULL THEN
                RAISE EXCEPTION 'first job transition must start at sequence 1'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.sequence_number <> previous_sequence + 1
           OR NEW.from_status <> previous_status THEN
            RAISE EXCEPTION 'job transition sequence or previous state is invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $r0p$
    """,
    """
    CREATE TRIGGER job_run_transitions_validate_insert_trg
        BEFORE INSERT ON job_run_transitions
        FOR EACH ROW
        EXECUTE FUNCTION r0p_validate_job_transition_insert()
    """,
    """
    ALTER TABLE outbox_events
        ADD COLUMN message_id uuid,
        ADD COLUMN event_name text,
        ADD COLUMN envelope_schema_version text,
        ADD COLUMN payload_schema_name text,
        ADD COLUMN payload_schema_version text,
        ADD COLUMN payload_schema_sha256 char(64),
        ADD COLUMN producer text,
        ADD COLUMN occurred_at timestamptz,
        ADD COLUMN produced_at timestamptz,
        ADD COLUMN correlation_id uuid,
        ADD COLUMN causation_id uuid,
        ADD COLUMN partition_key text,
        ADD COLUMN idempotency_key text,
        ADD COLUMN headers jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN available_at timestamptz,
        ADD COLUMN lease_owner text,
        ADD COLUMN leased_at timestamptz,
        ADD COLUMN lease_expires_at timestamptz,
        ADD COLUMN last_error_code text
    """,
    """
    ALTER TABLE outbox_events
        ALTER COLUMN attempt SET DEFAULT 1,
        ADD CONSTRAINT outbox_events_attempt_ck
            CHECK (attempt >= 1 AND attempt <= 100) NOT VALID,
        ADD CONSTRAINT outbox_events_schema_hash_ck
            CHECK (
                payload_schema_sha256 IS NULL
                OR payload_schema_sha256 ~ '^[a-f0-9]{64}$'
            ) NOT VALID,
        ADD CONSTRAINT outbox_events_headers_ck
            CHECK (jsonb_typeof(headers) = 'array') NOT VALID,
        ADD CONSTRAINT outbox_events_wire_identity_ck
            CHECK (
                (
                    event_name IS NULL
                    OR event_name ~
                        '^com\\.stockassistant\\.[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*\\.v[1-9][0-9]*$'
                )
                AND (
                    envelope_schema_version IS NULL
                    OR envelope_schema_version ~ '^[1-9][0-9]*\\.[0-9]+$'
                )
                AND (
                    payload_schema_name IS NULL
                    OR payload_schema_name ~ '^[a-z][a-z0-9_.-]{1,199}$'
                )
                AND (
                    payload_schema_version IS NULL
                    OR payload_schema_version ~ '^[1-9][0-9]*\\.[0-9]+$'
                )
                AND (
                    producer IS NULL
                    OR producer ~ '^[a-z][a-z0-9_-]{1,99}$'
                )
                AND (
                    idempotency_key IS NULL
                    OR length(idempotency_key) BETWEEN 16 AND 200
                )
                AND (
                    partition_key IS NULL
                    OR length(partition_key) BETWEEN 1 AND 200
                )
                AND jsonb_typeof(payload) = 'object'
            ) NOT VALID,
        ADD CONSTRAINT outbox_events_timing_ck
            CHECK (
                (
                    produced_at IS NULL
                    OR occurred_at IS NULL
                    OR produced_at >= occurred_at
                )
                AND (
                    produced_at IS NULL
                    OR created_at >= produced_at
                )
            ) NOT VALID,
        ADD CONSTRAINT outbox_events_event_name_match_ck
            CHECK (event_name IS NULL OR event_name = topic) NOT VALID,
        ADD CONSTRAINT outbox_events_lease_ck
            CHECK (
                (
                    lease_owner IS NULL
                    AND leased_at IS NULL
                    AND lease_expires_at IS NULL
                )
                OR (
                    lease_owner IS NOT NULL
                    AND leased_at IS NOT NULL
                    AND lease_expires_at > leased_at
                    AND published_at IS NULL
                )
            ) NOT VALID
    """,
    """
    CREATE INDEX outbox_events_delivery_idx
        ON outbox_events (available_at, created_at, id)
        WHERE published_at IS NULL
    """,
    """
    CREATE INDEX outbox_events_lease_expiry_idx
        ON outbox_events (lease_expires_at, id)
        WHERE published_at IS NULL AND lease_expires_at IS NOT NULL
    """,
    """
    CREATE TABLE message_inbox_receipts (
        consumer_id text NOT NULL,
        message_id uuid NOT NULL,
        event_name text NOT NULL,
        idempotency_key text NOT NULL,
        received_at timestamptz NOT NULL,
        processing_started_at timestamptz,
        processed_at timestamptz,
        delivery_attempt integer NOT NULL DEFAULT 1
            CHECK (delivery_attempt >= 1 AND delivery_attempt <= 1000),
        disposition text NOT NULL DEFAULT 'received'
            CHECK (
                disposition IN (
                    'received',
                    'processing',
                    'acknowledged',
                    'retry_scheduled',
                    'dead_lettered',
                    'discarded'
                )
            ),
        reason_code text CHECK (
            reason_code IS NULL OR reason_code ~ '^[A-Z][A-Z0-9_]{1,63}$'
        ),
        safe_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        PRIMARY KEY (consumer_id, message_id),
        UNIQUE (consumer_id, event_name, idempotency_key),
        CHECK (
            processing_started_at IS NULL
            OR processing_started_at >= received_at
        ),
        CHECK (
            processed_at IS NULL
            OR processed_at >= COALESCE(processing_started_at, received_at)
        )
    )
    """,
    """
    COMMENT ON TABLE message_inbox_receipts IS
        'Technical consumer deduplication ledger; never a user-facing notification inbox'
    """,
    """
    CREATE INDEX message_inbox_receipts_processing_idx
        ON message_inbox_receipts (consumer_id, disposition, received_at)
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
