"""Add anonymous owner isolation to R2 research sessions.

Revision ID: 20260828_0016
Revises: 20260827_0015
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260828_0016"
down_revision: str | None = "20260827_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Research ownership is an access-control and audit boundary; change it only through a "
    "new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE research_sessions
        ADD COLUMN owner_principal_sha256 char(64)
    """,
    """
    UPDATE research_sessions
       SET owner_principal_sha256 =
           '0dc852dd1d1b1710a3f66271adc4d80e2fb7791c1418292fb4efc35dee6541e7'
     WHERE owner_principal_sha256 IS NULL
    """,
    """
    ALTER TABLE research_sessions
        ALTER COLUMN owner_principal_sha256 SET NOT NULL,
        ADD CONSTRAINT research_sessions_owner_sha256_ck
            CHECK (owner_principal_sha256 ~ '^[a-f0-9]{64}$')
    """,
    """
    CREATE INDEX research_sessions_owner_history_idx
        ON research_sessions (owner_principal_sha256, created_at DESC, session_id DESC)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE INDEX research_sessions_owner_active_idx
        ON research_sessions (owner_principal_sha256, status, created_at)
        WHERE deleted_at IS NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
