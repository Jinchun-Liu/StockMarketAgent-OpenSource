"""Persist versioned research artifact identities for exact purge.

Revision ID: 20260828_0017
Revises: 20260828_0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260828_0017"
down_revision: str | None = "20260828_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Versioned artifact identity is part of the research deletion audit boundary; "
    "change it only through a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE research_reports
        ADD COLUMN artifact_object_version_id text,
        ADD CONSTRAINT research_reports_artifact_version_ck CHECK (
            artifact_object_version_id IS NULL
            OR (
                artifact_object_key IS NOT NULL
                AND length(artifact_object_version_id) BETWEEN 1 AND 500
            )
        )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
