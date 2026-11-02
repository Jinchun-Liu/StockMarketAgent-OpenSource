"""Add append-only market revisions and behavior-pressure persistence.

Revision ID: 20260807_0012
Revises: 20260804_0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260807_0012"
down_revision: str | None = "20260804_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Behavior assessments, market revisions and notification identities are immutable audit "
    "facts; reverse structural changes with a new forward-fix migration"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE market_data_manifests (
        manifest_id uuid PRIMARY KEY,
        status text NOT NULL CHECK (status IN ('ready', 'incomplete', 'blocked')),
        requested_from timestamptz NOT NULL,
        requested_until timestamptz NOT NULL,
        as_of timestamptz NOT NULL,
        source_id text NOT NULL,
        rights_snapshot_hash char(64) NOT NULL,
        content_hash char(64) NOT NULL,
        row_count integer NOT NULL CHECK (row_count >= 0),
        payload jsonb NOT NULL,
        recorded_at timestamptz NOT NULL,
        CONSTRAINT market_data_manifests_window_ck
            CHECK (requested_from < requested_until AND requested_until <= as_of),
        CONSTRAINT market_data_manifests_content_uidx UNIQUE (content_hash)
    )
    """,
    """
    CREATE TABLE market_data_revisions (
        revision_id uuid PRIMARY KEY,
        instrument_id text NOT NULL,
        kind text NOT NULL CHECK (
            kind IN ('bar', 'share_capital', 'corporate_action', 'trading_rule')
        ),
        effective_at timestamptz NOT NULL,
        available_at timestamptz NOT NULL,
        first_seen_at timestamptz NOT NULL,
        revision_known_at timestamptz NOT NULL,
        supersedes_revision_id uuid REFERENCES market_data_revisions(revision_id),
        source_id text NOT NULL,
        rights_snapshot_hash char(64) NOT NULL,
        content_hash char(64) NOT NULL,
        payload jsonb NOT NULL,
        recorded_at timestamptz NOT NULL,
        CONSTRAINT market_data_revisions_pit_ck CHECK (
            available_at <= first_seen_at AND first_seen_at <= revision_known_at
        ),
        CONSTRAINT market_data_revisions_content_uidx UNIQUE (
            instrument_id, kind, effective_at, content_hash
        )
    )
    """,
    """
    CREATE INDEX market_data_revisions_pit_idx
        ON market_data_revisions (
            instrument_id, kind, effective_at, available_at, revision_known_at
        )
    """,
    """
    CREATE TABLE market_data_write_receipts (
        idempotency_key text PRIMARY KEY,
        request_sha256 char(64) NOT NULL,
        manifest_id uuid NOT NULL REFERENCES market_data_manifests(manifest_id),
        accepted_count integer NOT NULL CHECK (accepted_count >= 0),
        idempotent_replay_count integer NOT NULL CHECK (idempotent_replay_count >= 0),
        stored_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE behavior_pressure_assessments (
        assessment_id uuid PRIMARY KEY,
        instrument_id text NOT NULL CHECK (instrument_id = 'XSHG:603986'),
        mode text NOT NULL CHECK (mode IN ('post_close', 'intraday_10m')),
        formula_version text NOT NULL CHECK (
            formula_version = 'behavior-pressure-research.v1'
        ),
        window_start timestamptz NOT NULL,
        window_end timestamptz NOT NULL,
        as_of timestamptz NOT NULL,
        generated_at timestamptz NOT NULL,
        revision_known_at timestamptz NOT NULL,
        data_manifest_hash char(64) NOT NULL,
        comparison_semantics_hash char(64) NOT NULL,
        score smallint CHECK (score BETWEEN 0 AND 100),
        band text,
        availability text NOT NULL CHECK (
            availability IN ('ready', 'degraded', 'abstained')
        ),
        payload jsonb NOT NULL,
        recorded_at timestamptz NOT NULL,
        CONSTRAINT behavior_pressure_assessments_pit_ck CHECK (
            window_start < window_end
            AND window_end <= as_of
            AND as_of <= generated_at
            AND generated_at <= revision_known_at
        ),
        CONSTRAINT behavior_pressure_assessments_score_ck CHECK (
            (score IS NULL AND band IS NULL) OR (score IS NOT NULL AND band IS NOT NULL)
        ),
        CONSTRAINT behavior_pressure_assessments_logical_uidx UNIQUE (
            instrument_id, mode, window_end, formula_version,
            comparison_semantics_hash, data_manifest_hash
        )
    )
    """,
    """
    CREATE INDEX behavior_pressure_assessments_pit_idx
        ON behavior_pressure_assessments (
            instrument_id, mode, as_of DESC, generated_at DESC, revision_known_at DESC
        )
    """,
    """
    CREATE TABLE behavior_pressure_write_receipts (
        idempotency_key text PRIMARY KEY,
        request_sha256 char(64) NOT NULL,
        assessment_id uuid NOT NULL REFERENCES behavior_pressure_assessments(assessment_id),
        created boolean NOT NULL,
        stored_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE behavior_analog_observations (
        assessment_id uuid PRIMARY KEY REFERENCES behavior_pressure_assessments(assessment_id),
        instrument_id text NOT NULL,
        trading_day_index integer NOT NULL,
        as_of timestamptz NOT NULL,
        assessment_revision_known_at timestamptz NOT NULL,
        label_available_at timestamptz NOT NULL,
        feature_set_version text NOT NULL CHECK (
            feature_set_version = 'behavior-analog-14d.v1'
        ),
        feature_vector vector(14) NOT NULL,
        outcome_payload jsonb NOT NULL,
        created_at timestamptz NOT NULL,
        CONSTRAINT behavior_analog_observations_label_ck CHECK (
            as_of <= assessment_revision_known_at
            AND assessment_revision_known_at <= label_available_at
        )
    )
    """,
    """
    CREATE INDEX behavior_analog_observations_vector_idx
        ON behavior_analog_observations USING hnsw (feature_vector vector_l2_ops)
    """,
    """
    CREATE TABLE behavior_transition_states (
        instrument_id text NOT NULL CHECK (instrument_id = 'XSHG:603986'),
        mode text NOT NULL CHECK (mode IN ('post_close', 'intraday_10m')),
        trading_date date NOT NULL,
        revision integer NOT NULL CHECK (revision >= 1),
        state_payload jsonb NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (instrument_id, mode, trading_date)
    )
    """,
    """
    CREATE TABLE behavior_transition_write_receipts (
        idempotency_key text PRIMARY KEY,
        request_sha256 char(64) NOT NULL,
        instrument_id text NOT NULL,
        mode text NOT NULL,
        trading_date date NOT NULL,
        revision integer NOT NULL,
        stored_at timestamptz NOT NULL,
        FOREIGN KEY (instrument_id, mode, trading_date)
            REFERENCES behavior_transition_states(instrument_id, mode, trading_date)
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
