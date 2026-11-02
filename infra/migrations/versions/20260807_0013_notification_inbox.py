"""Add the public R1 behavior notification inbox.

Revision ID: 20260807_0013
Revises: 20260807_0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260807_0013"
down_revision: str | None = "20260807_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Notification inbox items are user-visible delivery facts; reverse structural changes "
    "with a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE notification_inbox_items (
        recipient_key text NOT NULL,
        message_id uuid NOT NULL,
        event_name text NOT NULL CHECK (
            event_name = 'com.stockassistant.behavior.band_transition.v1'
        ),
        idempotency_key char(64) NOT NULL,
        envelope jsonb NOT NULL,
        occurred_at timestamptz NOT NULL,
        projected_at timestamptz NOT NULL,
        PRIMARY KEY (recipient_key, message_id),
        CONSTRAINT notification_inbox_recipient_ck CHECK (recipient_key = 'public:r1'),
        CONSTRAINT notification_inbox_business_uidx UNIQUE (
            recipient_key, idempotency_key
        )
    )
    """,
    """
    CREATE INDEX notification_inbox_resume_idx
        ON notification_inbox_items (
            recipient_key, event_name, projected_at, message_id
        )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
