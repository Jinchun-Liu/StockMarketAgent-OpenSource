"""Add database knowledge time to the append-only Job transition ledger.

Revision ID: 20260723_0006
Revises: 20260723_0005

The business occurrence time of a transition is not sufficient for point-in-time
recovery: a transition inserted later with a backdated ``occurred_at`` would
otherwise become visible to an earlier decision.  This forward fix records when
PostgreSQL first knew each ledger row and makes that value database-controlled.

``CURRENT_TIMESTAMP`` is stable, so supported PostgreSQL versions can store one
conservative migration cutoff in catalog metadata instead of rewriting historical
rows.  A trigger replaces it with statement-time knowledge for every new insert.
R0P runs before production writers exist; the ordinary index build is therefore
bounded to an empty or synthetic-small ledger.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0006"
down_revision: str | None = "20260723_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Job transition knowledge time is immutable audit evidence; remove or "
    "correct it only with a new forward-fix revision"
)


UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE job_run_transitions
        ADD COLUMN recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
    """,
    """
    COMMENT ON COLUMN job_run_transitions.recorded_at IS
        'Database knowledge time; legacy rows use the conservative 0006 migration cutoff'
    """,
    """
    CREATE FUNCTION r0p_set_job_transition_recorded_at()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $r0p$
    BEGIN
        NEW.recorded_at := clock_timestamp();
        RETURN NEW;
    END;
    $r0p$
    """,
    """
    CREATE TRIGGER job_run_transitions_recorded_at_trg
        BEFORE INSERT ON job_run_transitions
        FOR EACH ROW
        EXECUTE FUNCTION r0p_set_job_transition_recorded_at()
    """,
    """
    CREATE INDEX job_run_transitions_pit_recovery_idx
        ON job_run_transitions (
            job_run_id,
            recorded_at,
            occurred_at,
            sequence_number DESC
        )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
