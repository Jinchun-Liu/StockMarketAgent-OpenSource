"""Allow complete R1 source provenance 1.2 alongside immutable 1.1 rows.

Revision ID: 20260802_0010
Revises: 20260801_0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0010"
down_revision: str | None = "20260801_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V1_1_KEYS = (
    "schema_version",
    "provider",
    "access_channel",
    "adapter_name",
    "adapter_version",
    "endpoint_name",
    "source_identifier",
    "runtime_mode",
    "release_tier",
    "rights_status",
)
V1_2_KEYS = (*V1_1_KEYS, "quality_tier", "evidence_role")


def _shape(keys: tuple[str, ...], version: str, mode_expression: str) -> str:
    quoted = ",\n                        ".join(f"'{key}'" for key in keys)
    return f"""
                (
                    provenance ?& ARRAY[
                        {quoted}
                    ]
                    AND (
                        provenance - ARRAY[
                            {quoted}
                        ]
                    ) = '{{}}'::jsonb
                    AND provenance->>'schema_version' = '{version}'
                    AND ({mode_expression})
                )
    """


V1_1_MODES = """
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
"""

V1_2_MODES = """
    (
        provenance->>'runtime_mode' IN ('demo_replay', 'public_static')
        AND provenance->>'access_channel' = 'demo_replay'
        AND provenance->>'release_tier' = 'dev_only'
        AND provenance->>'rights_status' = 'not_applicable'
        AND provenance->>'quality_tier' = 'demo_replay'
    )
    OR (
        provenance->>'runtime_mode' = 'dev_live'
        AND provenance->>'access_channel' IN ('akshare', 'official_http', 'baostock_sdk')
        AND provenance->>'release_tier' = 'dev_only'
        AND provenance->>'rights_status' = 'unverified'
        AND provenance->>'quality_tier' IN (
            'free_official', 'free_structured', 'dev_web'
        )
    )
    OR (
        provenance->>'runtime_mode' = 'authorized'
        AND provenance->>'access_channel' = 'authorized_api'
        AND provenance->>'release_tier' = 'authorized'
        AND provenance->>'rights_status' = 'approved'
        AND provenance->>'quality_tier' = 'authorized'
    )
"""

V1_1_SHAPE = _shape(V1_1_KEYS, "1.1", V1_1_MODES)
V1_2_SHAPE = _shape(V1_2_KEYS, "1.2", V1_2_MODES)


def upgrade() -> None:
    op.drop_constraint(
        "raw_documents_provenance_shape_ck",
        "raw_documents",
        type_="check",
    )
    op.create_check_constraint(
        "raw_documents_provenance_shape_ck",
        "raw_documents",
        f"""
        provenance IS NULL
        OR (
            jsonb_typeof(provenance) = 'object'
            AND ({V1_1_SHAPE} OR {V1_2_SHAPE})
        )
        """,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM raw_documents
                 WHERE provenance->>'schema_version' = '1.2'
            ) THEN
                RAISE EXCEPTION
                    'R1 provenance 1.2 rows require removal or a forward-fix before downgrade';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        "raw_documents_provenance_shape_ck",
        "raw_documents",
        type_="check",
    )
    op.create_check_constraint(
        "raw_documents_provenance_shape_ck",
        "raw_documents",
        f"""
        provenance IS NULL
        OR (
            jsonb_typeof(provenance) = 'object'
            AND {V1_1_SHAPE}
        )
        """,
    )
