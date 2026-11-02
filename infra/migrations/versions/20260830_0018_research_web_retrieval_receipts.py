"""Add minimal expiring receipts for model-assisted source discovery.

Revision ID: 20260830_0018
Revises: 20260828_0017
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260830_0018"
down_revision: str | None = "20260828_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Web retrieval receipts participate in the research source audit boundary; "
    "change them only through a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE research_web_retrieval_receipts (
        receipt_id uuid PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES research_sessions(session_id),
        query_sha256 char(64) NOT NULL CHECK (query_sha256 ~ '^[a-f0-9]{64}$'),
        candidate_url_sha256 char(64)[] NOT NULL DEFAULT ARRAY[]::char(64)[],
        selected_source_id text,
        selected_url_sha256 char(64),
        status text NOT NULL CHECK (
            status IN ('discovered', 'selected', 'rejected', 'fetched', 'failed')
        ),
        credits_used integer NOT NULL DEFAULT 0 CHECK (credits_used BETWEEN 0 AND 100),
        rule_version text NOT NULL,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        purged_at timestamptz,
        CONSTRAINT research_web_retrieval_selected_ck CHECK (
            (selected_source_id IS NULL) = (selected_url_sha256 IS NULL)
        ),
        CONSTRAINT research_web_retrieval_retention_ck CHECK (
            expires_at > created_at
            AND expires_at <= created_at + interval '7 days'
            AND (purged_at IS NULL OR purged_at >= created_at)
        )
    )
    """,
    """
    CREATE INDEX research_web_retrieval_session_idx
        ON research_web_retrieval_receipts (session_id, created_at, receipt_id)
    """,
    """
    CREATE INDEX research_web_retrieval_cleanup_idx
        ON research_web_retrieval_receipts (expires_at)
        WHERE purged_at IS NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
