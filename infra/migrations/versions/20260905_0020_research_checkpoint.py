"""Retain one recoverable research snapshot and explicit result completeness.

Revision ID: 20260905_0020
Revises: 20260904_0019
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260905_0020"
down_revision: str | None = "20260904_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = "Resume metadata is additive; revert application code or use a forward fix"
UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE research_sessions
        ADD COLUMN checkpoint_payload jsonb,
        ADD COLUMN result_completeness text,
        ADD CONSTRAINT research_sessions_result_completeness_ck CHECK (
            result_completeness IN ('complete', 'partial', 'insufficient_evidence', 'execution_failed')
        )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
