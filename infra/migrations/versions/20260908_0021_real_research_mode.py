"""Add private real-source research and acquisition progress states.

Revision ID: 20260908_0021
Revises: 20260905_0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0021"
down_revision: str | None = "20260905_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = "Disable new research with a forward configuration change; retain history"
UPGRADE_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE research_sessions DROP CONSTRAINT research_sessions_source_mode_check",
    """ALTER TABLE research_sessions ADD CONSTRAINT research_sessions_source_mode_check
       CHECK (source_mode IN ('synthetic_development_only', 'real_development_only', 'production_authorized'))""",
    "ALTER TABLE research_sessions DROP CONSTRAINT research_sessions_stage_check",
    """ALTER TABLE research_sessions ADD CONSTRAINT research_sessions_stage_check CHECK (stage IN (
       'queued', 'resolving_instrument', 'collecting_history', 'analyzing_business',
       'analyzing_financials', 'analyzing_valuation', 'analyzing_events', 'debating_cases',
       'validating_report', 'completed', 'auditing_data', 'refetching_data', 'scoring', 'generating_report'))""",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
