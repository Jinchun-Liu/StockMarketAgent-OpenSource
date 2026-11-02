"""Contract R0P storage after validation and old-writer retirement.

Revision ID: 20260723_0005
Revises: 20260723_0004

This CONTRACT step is safe only after the deployment gate proves old writers
are zero.  It makes validated wire fields mandatory, installs logical
uniqueness constraints and replaces the compatibility event trigger with a
strict chain validator.  No historical rows or ledgers are removed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0005"
down_revision: str | None = "20260723_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "R0P contract is forward-only because weakening required wire identity and "
    "uniqueness would re-enable unsafe writers; use a new forward-fix revision"
)


UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE market_events
        ALTER COLUMN logical_event_id SET NOT NULL,
        ALTER COLUMN revision_known_at SET NOT NULL
    """,
    """
    CREATE UNIQUE INDEX market_events_logical_revision_uidx
        ON market_events (logical_event_id, revision)
    """,
    """
    CREATE UNIQUE INDEX market_events_supersedes_uidx
        ON market_events (supersedes_revision_id)
        WHERE supersedes_revision_id IS NOT NULL
    """,
    """
    DROP TRIGGER market_events_fill_revision_identity_trg ON market_events
    """,
    """
    DROP FUNCTION r0p_fill_market_event_revision_identity()
    """,
    """
    CREATE FUNCTION r0p_validate_market_event_revision_chain()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $r0p$
    DECLARE
        predecessor market_events%ROWTYPE;
    BEGIN
        IF NEW.revision = 1 THEN
            IF NEW.supersedes_revision_id IS NOT NULL THEN
                RAISE EXCEPTION 'revision 1 cannot supersede another revision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;

        SELECT *
          INTO predecessor
          FROM market_events
         WHERE id = NEW.supersedes_revision_id;

        IF NOT FOUND
           OR predecessor.logical_event_id <> NEW.logical_event_id
           OR predecessor.revision <> NEW.revision - 1 THEN
            RAISE EXCEPTION 'market event revision chain is invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $r0p$
    """,
    """
    CREATE TRIGGER market_events_validate_revision_chain_trg
        BEFORE INSERT OR UPDATE OF
            logical_event_id,
            revision,
            supersedes_revision_id
        ON market_events
        FOR EACH ROW
        EXECUTE FUNCTION r0p_validate_market_event_revision_chain()
    """,
    """
    ALTER TABLE job_runs
        ALTER COLUMN request_schema_name SET NOT NULL,
        ALTER COLUMN request_schema_version SET NOT NULL,
        ALTER COLUMN request_schema_sha256 SET NOT NULL,
        ALTER COLUMN result_schema_name SET NOT NULL,
        ALTER COLUMN result_schema_version SET NOT NULL,
        ALTER COLUMN result_schema_sha256 SET NOT NULL,
        ALTER COLUMN request_id SET NOT NULL,
        ALTER COLUMN correlation_id SET NOT NULL,
        ALTER COLUMN actor_id SET NOT NULL,
        ALTER COLUMN workspace_id SET NOT NULL,
        ALTER COLUMN as_of SET NOT NULL,
        ALTER COLUMN requested_at SET NOT NULL,
        ALTER COLUMN queued_at SET NOT NULL,
        ALTER COLUMN queue_name SET NOT NULL,
        ALTER COLUMN max_attempts SET NOT NULL,
        ALTER COLUMN request_payload SET NOT NULL
    """,
    """
    CREATE INDEX job_runs_correlation_idx
        ON job_runs (correlation_id, scheduled_for DESC, id)
    """,
    """
    ALTER TABLE outbox_events
        ALTER COLUMN message_id SET NOT NULL,
        ALTER COLUMN event_name SET NOT NULL,
        ALTER COLUMN envelope_schema_version SET NOT NULL,
        ALTER COLUMN payload_schema_name SET NOT NULL,
        ALTER COLUMN payload_schema_version SET NOT NULL,
        ALTER COLUMN payload_schema_sha256 SET NOT NULL,
        ALTER COLUMN producer SET NOT NULL,
        ALTER COLUMN occurred_at SET NOT NULL,
        ALTER COLUMN produced_at SET NOT NULL,
        ALTER COLUMN correlation_id SET NOT NULL,
        ALTER COLUMN partition_key SET NOT NULL,
        ALTER COLUMN idempotency_key SET NOT NULL,
        ALTER COLUMN available_at SET NOT NULL
    """,
    """
    CREATE UNIQUE INDEX outbox_events_message_id_uidx
        ON outbox_events (message_id)
    """,
    """
    CREATE UNIQUE INDEX outbox_events_business_message_uidx
        ON outbox_events (producer, event_name, idempotency_key)
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
        '20260723_0005',
        'r0p_contract',
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
