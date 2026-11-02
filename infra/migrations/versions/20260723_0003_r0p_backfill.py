"""Deterministically backfill R0P storage and quarantine ambiguous rows.

Revision ID: 20260723_0003
Revises: 20260723_0002

The BACKFILL step never invents request IDs, correlation IDs, schema versions,
producers or business idempotency keys.  Legacy rows whose wire identity cannot
be reconstructed are placed in ``migration_repair_queue`` and make the later
VALIDATE step fail closed.  Each safe update is idempotent and checkpointed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0003"
down_revision: str | None = "20260723_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "R0P backfill is forward-only because reverting deterministic identities or "
    "transition history would break auditability; repair with a new revision"
)

BACKFILL_REVISION = revision
BATCH_SIZE = 500


UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    DO $r0p$
    DECLARE
        batch_count integer;
        batch_last_key text;
    BEGIN
        LOOP
            WITH batch AS (
                SELECT id
                  FROM market_events
                 WHERE logical_event_id IS NULL
                    OR revision_known_at IS NULL
                    OR (
                        revision > 1
                        AND supersedes_revision_id IS NULL
                        AND EXISTS (
                            SELECT 1
                              FROM market_events AS direct_predecessor
                             WHERE direct_predecessor.cluster_id =
                                   market_events.cluster_id
                               AND direct_predecessor.revision =
                                   market_events.revision - 1
                        )
                    )
                 ORDER BY id
                 LIMIT 500
            ),
            changed AS (
                UPDATE market_events AS current
                   SET logical_event_id = COALESCE(
                           current.logical_event_id,
                           (
                               SELECT first_revision.id
                                 FROM market_events AS first_revision
                                WHERE first_revision.cluster_id = current.cluster_id
                                ORDER BY
                                    first_revision.revision,
                                    first_revision.created_at,
                                    first_revision.id
                                LIMIT 1
                           )
                       ),
                       revision_known_at = COALESCE(
                           current.revision_known_at,
                           GREATEST(current.first_seen_at, current.created_at)
                       ),
                       supersedes_revision_id = CASE
                           WHEN current.revision = 1 THEN NULL
                           ELSE COALESCE(
                               current.supersedes_revision_id,
                               (
                                   SELECT predecessor.id
                                     FROM market_events AS predecessor
                                    WHERE predecessor.cluster_id = current.cluster_id
                                      AND predecessor.revision = current.revision - 1
                               )
                           )
                       END
                 WHERE current.id IN (SELECT id FROM batch)
                 RETURNING current.id
            )
            SELECT
                count(*)::integer,
                max(id::text)
              INTO batch_count, batch_last_key
              FROM changed;

            EXIT WHEN batch_count = 0;

            INSERT INTO migration_backfill_checkpoints (
                migration_revision,
                operation_name,
                last_key,
                processed_rows,
                completed,
                updated_at
            )
            VALUES (
                '20260723_0003',
                'market_event_revision_identity',
                batch_last_key,
                batch_count,
                false,
                now()
            )
            ON CONFLICT (migration_revision, operation_name)
            DO UPDATE SET
                last_key = EXCLUDED.last_key,
                processed_rows =
                    migration_backfill_checkpoints.processed_rows
                    + EXCLUDED.processed_rows,
                completed = false,
                updated_at = EXCLUDED.updated_at;
        END LOOP;

        INSERT INTO migration_backfill_checkpoints (
            migration_revision,
            operation_name,
            last_key,
            processed_rows,
            completed,
            updated_at
        )
        VALUES (
            '20260723_0003',
            'market_event_revision_identity',
            NULL,
            0,
            true,
            now()
        )
        ON CONFLICT (migration_revision, operation_name)
        DO UPDATE SET completed = true, updated_at = EXCLUDED.updated_at;
    END;
    $r0p$
    """,
    """
    INSERT INTO migration_repair_queue (
        migration_revision,
        entity_table,
        entity_key,
        reason_code,
        safe_details
    )
    SELECT
        '20260723_0003',
        'market_events',
        cluster_id::text,
        'MARKET_EVENT_REVISION_CHAIN_INVALID',
        jsonb_build_object(
            'revision_count', count(*),
            'minimum_revision', min(revision),
            'maximum_revision', max(revision)
        )
      FROM market_events
     GROUP BY cluster_id
    HAVING min(revision) <> 1
        OR max(revision) <> count(*)
    ON CONFLICT (
        migration_revision,
        entity_table,
        entity_key,
        reason_code
    ) WHERE resolved_at IS NULL
    DO NOTHING
    """,
    """
    INSERT INTO migration_repair_queue (
        migration_revision,
        entity_table,
        entity_key,
        reason_code,
        safe_details
    )
    SELECT
        '20260723_0003',
        'market_events',
        id::text,
        'MARKET_EVENT_KNOWLEDGE_TIME_INVALID',
        jsonb_build_object(
            'published_after_first_seen', published_at > first_seen_at,
            'revision_known_before_first_seen',
                revision_known_at < first_seen_at
        )
      FROM market_events
     WHERE published_at > first_seen_at
        OR revision_known_at < first_seen_at
    ON CONFLICT (
        migration_revision,
        entity_table,
        entity_key,
        reason_code
    ) WHERE resolved_at IS NULL
    DO NOTHING
    """,
    """
    UPDATE job_runs
       SET attempt = 1
     WHERE attempt < 1
    """,
    """
    INSERT INTO job_run_transitions (
        job_run_id,
        sequence_number,
        from_status,
        to_status,
        attempt,
        occurred_at,
        request_id,
        correlation_id,
        reason_code,
        safe_metadata
    )
    SELECT
        job.id,
        1,
        NULL,
        job.status,
        job.attempt,
        COALESCE(job.finished_at, job.started_at, job.scheduled_for),
        job.request_id,
        job.correlation_id,
        'R0P_LEGACY_STATE_CAPTURED',
        jsonb_build_object('source', 'deterministic_backfill')
      FROM job_runs AS job
     WHERE NOT EXISTS (
        SELECT 1
          FROM job_run_transitions AS transition
         WHERE transition.job_run_id = job.id
     )
    """,
    """
    INSERT INTO migration_repair_queue (
        migration_revision,
        entity_table,
        entity_key,
        reason_code,
        safe_details
    )
    SELECT
        '20260723_0003',
        'job_runs',
        id::text,
        'JOB_WIRE_CONTEXT_UNRECOVERABLE',
        jsonb_build_object(
            'task_name', task_name,
            'status', status,
            'missing_wire_fields', true
        )
      FROM job_runs
     WHERE request_schema_name IS NULL
        OR request_schema_version IS NULL
        OR request_schema_sha256 IS NULL
        OR result_schema_name IS NULL
        OR result_schema_version IS NULL
        OR result_schema_sha256 IS NULL
        OR request_id IS NULL
        OR correlation_id IS NULL
        OR actor_id IS NULL
        OR workspace_id IS NULL
        OR as_of IS NULL
        OR requested_at IS NULL
        OR queued_at IS NULL
        OR queue_name IS NULL
        OR request_payload IS NULL
    ON CONFLICT (
        migration_revision,
        entity_table,
        entity_key,
        reason_code
    ) WHERE resolved_at IS NULL
    DO NOTHING
    """,
    """
    INSERT INTO migration_backfill_checkpoints (
        migration_revision,
        operation_name,
        last_key,
        processed_rows,
        completed,
        updated_at
    )
    SELECT
        '20260723_0003',
        'job_transition_seed',
        max(id::text),
        count(*),
        true,
        now()
      FROM job_runs
    ON CONFLICT (migration_revision, operation_name)
    DO UPDATE SET
        last_key = EXCLUDED.last_key,
        processed_rows = EXCLUDED.processed_rows,
        completed = true,
        updated_at = EXCLUDED.updated_at
    """,
    """
    CREATE FUNCTION r0p_capture_job_run_transition()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $r0p$
    DECLARE
        next_sequence integer;
    BEGIN
        IF TG_OP = 'UPDATE'
           AND NEW.status IS NOT DISTINCT FROM OLD.status
           AND NEW.attempt IS NOT DISTINCT FROM OLD.attempt THEN
            RETURN NEW;
        END IF;

        SELECT COALESCE(max(sequence_number), 0) + 1
          INTO next_sequence
          FROM job_run_transitions
         WHERE job_run_id = NEW.id;

        INSERT INTO job_run_transitions (
            job_run_id,
            sequence_number,
            from_status,
            to_status,
            attempt,
            occurred_at,
            request_id,
            correlation_id,
            reason_code,
            safe_metadata
        )
        VALUES (
            NEW.id,
            next_sequence,
            CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.status END,
            NEW.status,
            NEW.attempt,
            COALESCE(NEW.finished_at, NEW.started_at, NEW.queued_at, now()),
            NEW.request_id,
            NEW.correlation_id,
            CASE
                WHEN TG_OP = 'INSERT' THEN 'JOB_CREATED'
                ELSE 'JOB_STATE_CHANGED'
            END,
            jsonb_build_object('source', 'database_trigger')
        );
        RETURN NEW;
    END;
    $r0p$
    """,
    """
    CREATE TRIGGER job_runs_capture_transition_trg
        AFTER INSERT OR UPDATE OF status, attempt ON job_runs
        FOR EACH ROW
        EXECUTE FUNCTION r0p_capture_job_run_transition()
    """,
    """
    UPDATE outbox_events
       SET message_id = COALESCE(message_id, id),
           available_at = COALESCE(available_at, created_at),
           attempt = GREATEST(attempt, 1)
     WHERE message_id IS NULL
        OR available_at IS NULL
        OR attempt < 1
    """,
    """
    INSERT INTO migration_repair_queue (
        migration_revision,
        entity_table,
        entity_key,
        reason_code,
        safe_details
    )
    SELECT
        '20260723_0003',
        'outbox_events',
        id::text,
        'OUTBOX_ENVELOPE_IDENTITY_UNRECOVERABLE',
        jsonb_build_object(
            'topic', topic,
            'aggregate_type', aggregate_type,
            'missing_wire_fields', true
        )
      FROM outbox_events
     WHERE event_name IS NULL
        OR envelope_schema_version IS NULL
        OR payload_schema_name IS NULL
        OR payload_schema_version IS NULL
        OR payload_schema_sha256 IS NULL
        OR producer IS NULL
        OR occurred_at IS NULL
        OR produced_at IS NULL
        OR correlation_id IS NULL
        OR partition_key IS NULL
        OR idempotency_key IS NULL
    ON CONFLICT (
        migration_revision,
        entity_table,
        entity_key,
        reason_code
    ) WHERE resolved_at IS NULL
    DO NOTHING
    """,
    """
    INSERT INTO migration_backfill_checkpoints (
        migration_revision,
        operation_name,
        last_key,
        processed_rows,
        completed,
        updated_at
    )
    SELECT
        '20260723_0003',
        'outbox_safe_fields',
        max(id::text),
        count(*),
        true,
        now()
      FROM outbox_events
    ON CONFLICT (migration_revision, operation_name)
    DO UPDATE SET
        last_key = EXCLUDED.last_key,
        processed_rows = EXCLUDED.processed_rows,
        completed = true,
        updated_at = EXCLUDED.updated_at
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
