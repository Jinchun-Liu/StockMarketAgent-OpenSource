"""Add event time and source provenance to raw evidence documents.

Revision ID: 20260801_0009
Revises: 20260730_0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260801_0009"
down_revision: str | None = "20260730_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORWARD_ONLY_REASON = (
    "Evidence event time and provenance are append-only lineage facts; "
    "change them only with a new forward-fix revision"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE raw_documents
        ADD COLUMN event_time timestamptz,
        ADD COLUMN provenance jsonb,
        ADD CONSTRAINT raw_documents_event_time_visibility_ck
            CHECK (event_time IS NULL OR event_time <= first_seen_at),
        ADD CONSTRAINT raw_documents_provenance_pair_ck
            CHECK ((event_time IS NULL) = (provenance IS NULL)),
        ADD CONSTRAINT raw_documents_provenance_shape_ck
            CHECK (
                provenance IS NULL
                OR (
                    jsonb_typeof(provenance) = 'object'
                    AND provenance ?& ARRAY[
                        'schema_version',
                        'provider',
                        'access_channel',
                        'adapter_name',
                        'adapter_version',
                        'endpoint_name',
                        'source_identifier',
                        'runtime_mode',
                        'release_tier',
                        'rights_status'
                    ]
                    AND (
                        provenance - ARRAY[
                            'schema_version',
                            'provider',
                            'access_channel',
                            'adapter_name',
                            'adapter_version',
                            'endpoint_name',
                            'source_identifier',
                            'runtime_mode',
                            'release_tier',
                            'rights_status'
                        ]
                    ) = '{}'::jsonb
                    AND provenance->>'schema_version' = '1.1'
                    AND (
                        (
                            provenance->>'runtime_mode' = 'demo_replay'
                            AND provenance->>'access_channel' = 'demo_replay'
                            AND provenance->>'release_tier' = 'dev_only'
                            AND provenance->>'rights_status' <> 'approved'
                        )
                        OR (
                            provenance->>'runtime_mode' = 'dev_live'
                            AND provenance->>'access_channel' = 'akshare'
                            AND provenance->>'release_tier' = 'dev_only'
                            AND provenance->>'rights_status' = 'unverified'
                        )
                        OR (
                            provenance->>'runtime_mode' = 'authorized'
                            AND provenance->>'access_channel' = 'authorized_api'
                            AND provenance->>'release_tier' = 'authorized'
                            AND provenance->>'rights_status' = 'approved'
                        )
                    )
                )
            )
    """,
    """
    CREATE INDEX raw_documents_event_time_pit_idx
        ON raw_documents (event_time, first_seen_at)
        WHERE event_time IS NOT NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(FORWARD_ONLY_REASON)
