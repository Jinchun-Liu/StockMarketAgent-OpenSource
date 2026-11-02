"""Persist fail-closed research cutoff semantics.

Revision ID: 20260904_0019
Revises: 20260830_0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260904_0019"
down_revision: str | None = "20260830_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Research cutoff mode determines whether live discovery is legal; "
    "change it only through a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE research_sessions
        ADD COLUMN cutoff_mode text NOT NULL DEFAULT 'historical_replay',
        ADD CONSTRAINT research_sessions_cutoff_mode_ck CHECK (
            cutoff_mode IN ('current_run', 'historical_replay')
        )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
