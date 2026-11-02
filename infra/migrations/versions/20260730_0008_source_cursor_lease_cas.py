"""Add monotonic versioning and bounded leases to source cursors.

Revision ID: 20260730_0008
Revises: 20260728_0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260730_0008"
down_revision: str | None = "20260728_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Source cursor versions and leases protect evidence lineage and monotonic watermarks; "
    "change them only with a new forward-fix revision"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE source_cursors
        ALTER COLUMN last_success_at DROP NOT NULL,
        ADD COLUMN cursor_version bigint NOT NULL DEFAULT 0,
        ADD COLUMN lease_owner text,
        ADD COLUMN lease_expires_at timestamptz,
        ADD CONSTRAINT source_cursors_cursor_version_ck
            CHECK (cursor_version >= 0),
        ADD CONSTRAINT source_cursors_lease_pair_ck
            CHECK (
                (lease_owner IS NULL AND lease_expires_at IS NULL)
                OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
            )
    """,
    """
    CREATE INDEX source_cursors_lease_expiry_idx
        ON source_cursors (lease_expires_at)
        WHERE lease_owner IS NOT NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
