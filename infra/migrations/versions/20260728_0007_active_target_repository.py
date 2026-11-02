"""Add point-in-time storage for the fixed Active Target aggregate.

Revision ID: 20260728_0007
Revises: 20260723_0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260728_0007"
down_revision: str | None = "20260723_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Active Target revisions and idempotency receipts are audit evidence; "
    "remove or correct them only with a new forward-fix revision"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE active_target_revisions (
        watchlist_id uuid NOT NULL,
        revision integer NOT NULL CHECK (revision >= 1),
        watchlist_payload jsonb NOT NULL,
        active_target_payload jsonb NOT NULL,
        instrument_id text NOT NULL,
        activated_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (watchlist_id, revision),
        CONSTRAINT active_target_activation_order_ck
            CHECK (activated_at <= updated_at)
    )
    """,
    """
    CREATE INDEX active_target_revisions_pit_lookup_idx
        ON active_target_revisions (
            watchlist_id,
            updated_at DESC,
            recorded_at DESC,
            revision DESC
        )
    """,
    """
    CREATE TABLE active_target_write_receipts (
        idempotency_key text PRIMARY KEY,
        request_sha256 char(64) NOT NULL,
        watchlist_id uuid NOT NULL,
        revision integer NOT NULL,
        created boolean NOT NULL,
        changed boolean NOT NULL,
        stored_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT active_target_write_receipt_revision_fk
            FOREIGN KEY (watchlist_id, revision)
            REFERENCES active_target_revisions (watchlist_id, revision)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
