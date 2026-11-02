"""Separate business watermark from successful refresh time.

Revision ID: 20260804_0011
Revises: 20260802_0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260804_0011"
down_revision: str | None = "20260802_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Business watermarks and successful refresh times are distinct monotonic audit facts; "
    "change them only with a new forward-fix revision"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE source_cursors
        ADD COLUMN business_watermark timestamptz
    """,
    """
    UPDATE source_cursors
       SET business_watermark = last_success_at
     WHERE last_success_at IS NOT NULL
    """,
    """
    ALTER TABLE source_cursors
        ADD CONSTRAINT source_cursors_refresh_state_ck
        CHECK (
            business_watermark IS NULL
            OR (
                last_success_at IS NOT NULL
                AND business_watermark <= last_success_at
            )
        ) NOT VALID
    """,
    """
    ALTER TABLE source_cursors
        VALIDATE CONSTRAINT source_cursors_refresh_state_ck
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
