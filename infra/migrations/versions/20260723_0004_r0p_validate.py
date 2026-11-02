"""Fail closed unless the R0P backfill is complete and internally consistent.

Revision ID: 20260723_0004
Revises: 20260723_0003

This VALIDATE step deliberately stops an upgrade when repair work remains.  An
operator must repair the affected row using evidence, mark the repair item
resolved, and rerun this revision.  It never guesses missing wire identities.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0004"
down_revision: str | None = "20260723_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "R0P validation is forward-only; correct invalid data and apply a new "
    "forward-fix revision instead of removing validated safeguards"
)


VALIDATION_BLOCK = """
DO $r0p$
DECLARE
    failure_count bigint;
BEGIN
    SELECT count(*)
      INTO failure_count
      FROM migration_repair_queue
     WHERE migration_revision = '20260723_0003'
       AND resolved_at IS NULL;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % unresolved repair item(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM migration_backfill_checkpoints
     WHERE migration_revision = '20260723_0003'
       AND completed = false;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % incomplete checkpoint(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM (
          VALUES
              ('market_event_revision_identity'),
              ('job_transition_seed'),
              ('outbox_safe_fields')
      ) AS expected(operation_name)
     WHERE NOT EXISTS (
         SELECT 1
           FROM migration_backfill_checkpoints AS checkpoint
          WHERE checkpoint.migration_revision = '20260723_0003'
            AND checkpoint.operation_name = expected.operation_name
            AND checkpoint.completed = true
     );
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % required checkpoint(s) missing',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM market_events
     WHERE logical_event_id IS NULL
        OR revision_known_at IS NULL
        OR revision_known_at < first_seen_at
        OR published_at > first_seen_at
        OR (revision = 1 AND supersedes_revision_id IS NOT NULL)
        OR (revision > 1 AND supersedes_revision_id IS NULL);
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % invalid market event revision(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM (
          SELECT logical_event_id, revision
            FROM market_events
           GROUP BY logical_event_id, revision
          HAVING count(*) > 1
      ) AS duplicate_event_revisions;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % duplicate logical event revision(s)',
            failure_count
            USING ERRCODE = '23505';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM market_events AS current
      LEFT JOIN market_events AS predecessor
        ON predecessor.id = current.supersedes_revision_id
     WHERE current.revision > 1
       AND (
           predecessor.id IS NULL
           OR predecessor.logical_event_id <> current.logical_event_id
           OR predecessor.revision <> current.revision - 1
       );
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % broken supersedes link(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
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
        OR attempt < 1
        OR max_attempts < attempt
        OR max_attempts > 100
        OR (
            status IN ('queued', 'running')
            AND finished_at IS NOT NULL
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
            AND finished_at IS NULL
        );
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % incomplete or invalid job run(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM job_runs AS job
     WHERE NOT EXISTS (
         SELECT 1
           FROM job_run_transitions AS transition
          WHERE transition.job_run_id = job.id
     );
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % job(s) without transition history',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM job_runs AS job
      JOIN LATERAL (
          SELECT transition.to_status, transition.attempt
            FROM job_run_transitions AS transition
           WHERE transition.job_run_id = job.id
           ORDER BY transition.sequence_number DESC
           LIMIT 1
      ) AS latest_transition ON true
     WHERE latest_transition.to_status <> job.status
        OR latest_transition.attempt <> job.attempt;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % job/ledger state mismatch(es)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM (
          SELECT
              job_run_id,
              min(sequence_number) AS minimum_sequence,
              max(sequence_number) AS maximum_sequence,
              count(*) AS transition_count
            FROM job_run_transitions
           GROUP BY job_run_id
          HAVING min(sequence_number) <> 1
              OR max(sequence_number) <> count(*)
      ) AS invalid_transition_sequences;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % non-contiguous transition ledger(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM outbox_events
     WHERE message_id IS NULL
        OR event_name IS NULL
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
        OR available_at IS NULL
        OR attempt < 1;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % incomplete outbox envelope(s)',
            failure_count
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM (
          SELECT message_id
            FROM outbox_events
           GROUP BY message_id
          HAVING count(*) > 1
      ) AS duplicate_message_ids;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % duplicate message ID(s)',
            failure_count
            USING ERRCODE = '23505';
    END IF;

    SELECT count(*)
      INTO failure_count
      FROM (
          SELECT producer, event_name, idempotency_key
            FROM outbox_events
           GROUP BY producer, event_name, idempotency_key
          HAVING count(*) > 1
      ) AS duplicate_business_messages;
    IF failure_count > 0 THEN
        RAISE EXCEPTION
            'R0P validation blocked: % duplicate business message(s)',
            failure_count
            USING ERRCODE = '23505';
    END IF;
END;
$r0p$
"""


UPGRADE_STATEMENTS: tuple[str, ...] = (
    VALIDATION_BLOCK,
    """
    ALTER TABLE market_events
        VALIDATE CONSTRAINT market_events_revision_known_at_ck,
        VALIDATE CONSTRAINT market_events_publication_visibility_ck,
        VALIDATE CONSTRAINT market_events_supersedes_not_self_ck,
        VALIDATE CONSTRAINT market_events_supersedes_revision_fk
    """,
    """
    ALTER TABLE job_runs
        VALIDATE CONSTRAINT job_runs_status_check,
        VALIDATE CONSTRAINT job_runs_attempt_ck,
        VALIDATE CONSTRAINT job_runs_request_schema_hash_ck,
        VALIDATE CONSTRAINT job_runs_result_schema_hash_ck,
        VALIDATE CONSTRAINT job_runs_wire_identity_ck,
        VALIDATE CONSTRAINT job_runs_timing_ck,
        VALIDATE CONSTRAINT job_runs_terminal_state_ck,
        VALIDATE CONSTRAINT job_runs_result_state_ck
    """,
    """
    ALTER TABLE outbox_events
        VALIDATE CONSTRAINT outbox_events_attempt_ck,
        VALIDATE CONSTRAINT outbox_events_schema_hash_ck,
        VALIDATE CONSTRAINT outbox_events_headers_ck,
        VALIDATE CONSTRAINT outbox_events_wire_identity_ck,
        VALIDATE CONSTRAINT outbox_events_timing_ck,
        VALIDATE CONSTRAINT outbox_events_event_name_match_ck,
        VALIDATE CONSTRAINT outbox_events_lease_ck
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
    VALUES (
        '20260723_0004',
        'r0p_validation',
        NULL,
        0,
        true,
        now()
    )
    ON CONFLICT (migration_revision, operation_name)
    DO UPDATE SET completed = true, updated_at = EXCLUDED.updated_at
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
