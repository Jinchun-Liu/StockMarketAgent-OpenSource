"""Persist completion of R2 research-session purges.

Revision ID: 20260827_0015
Revises: 20260827_0014
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0015"
down_revision: str | None = "20260827_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Research purge completion is retained as tombstone audit evidence; "
    "reverse this schema with a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE research_sessions
        ADD COLUMN purged_at timestamptz
    """,
    """
    UPDATE research_sessions AS session
       SET purged_at = report.purged_at
      FROM research_reports AS report
     WHERE report.session_id = session.session_id
       AND report.purged_at IS NOT NULL
    """,
    """
    ALTER TABLE research_sessions
        ADD CONSTRAINT research_sessions_purge_completion_ck CHECK (
            purged_at IS NULL
            OR (
                status IN ('expired', 'deleted')
                AND purge_due_at IS NOT NULL
                AND purged_at >= purge_due_at
            )
        )
    """,
    """
    CREATE INDEX research_sessions_purge_pending_idx
        ON research_sessions (purge_due_at, session_id)
        WHERE status IN ('expired', 'deleted') AND purged_at IS NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
