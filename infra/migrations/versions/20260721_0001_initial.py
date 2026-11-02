"""Initial point-in-time evidence, memory and forecast schema.

Revision ID: 20260721_0001
Revises: None
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260721_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = r"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE app_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE portfolios (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id),
    name text NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, name)
);

CREATE TABLE companies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name text NOT NULL,
    unified_credit_code text,
    registered_region_code text,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    UNIQUE (legal_name, valid_from)
);

CREATE TABLE instruments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id uuid REFERENCES companies(id),
    exchange text NOT NULL CHECK (exchange IN ('SSE', 'SZSE', 'BSE')),
    ticker text NOT NULL,
    name text NOT NULL,
    listed_at date,
    delisted_at date,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    UNIQUE (exchange, ticker, valid_from)
);
CREATE UNIQUE INDEX instruments_ticker_current_idx
    ON instruments (exchange, ticker) WHERE valid_to IS NULL;
ALTER TABLE instruments ADD CONSTRAINT instruments_no_overlapping_validity
    EXCLUDE USING gist (
        exchange WITH =,
        ticker WITH =,
        tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
    );

CREATE TABLE industry_memberships (
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    taxonomy text NOT NULL,
    industry_code text NOT NULL,
    industry_name text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    PRIMARY KEY (instrument_id, taxonomy, industry_code, valid_from)
);
ALTER TABLE industry_memberships ADD CONSTRAINT industry_memberships_no_overlap
    EXCLUDE USING gist (
        instrument_id WITH =,
        taxonomy WITH =,
        industry_code WITH =,
        tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
    );

CREATE TABLE company_regions (
    company_id uuid NOT NULL REFERENCES companies(id),
    region_code text NOT NULL,
    relation text NOT NULL CHECK (relation IN ('registered', 'headquarters', 'operation', 'project')),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    PRIMARY KEY (company_id, region_code, relation, valid_from)
);
ALTER TABLE company_regions ADD CONSTRAINT company_regions_no_overlap
    EXCLUDE USING gist (
        company_id WITH =,
        region_code WITH =,
        relation WITH =,
        tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
    );

CREATE TABLE holdings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    portfolio_id uuid NOT NULL REFERENCES portfolios(id),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    quantity_ciphertext bytea,
    cost_basis_ciphertext bytea,
    encryption_key_version text,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (quantity_ciphertext IS NULL AND cost_basis_ciphertext IS NULL
            AND encryption_key_version IS NULL)
        OR encryption_key_version IS NOT NULL
    )
);
CREATE UNIQUE INDEX holdings_portfolio_current_idx
    ON holdings (portfolio_id, instrument_id) WHERE valid_to IS NULL;
ALTER TABLE holdings ADD CONSTRAINT holdings_no_overlapping_validity
    EXCLUDE USING gist (
        portfolio_id WITH =,
        instrument_id WITH =,
        tstzrange(valid_from, COALESCE(valid_to, 'infinity'::timestamptz), '[)') WITH &&
    );

CREATE TABLE data_sources (
    id text PRIMARY KEY,
    name text NOT NULL,
    tier text NOT NULL,
    authority numeric(4,3) NOT NULL CHECK (authority BETWEEN 0 AND 1),
    rights_record jsonb NOT NULL,
    rights_hash text NOT NULL,
    rights_expiry timestamptz,
    enabled boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_cursors (
    source_id text NOT NULL REFERENCES data_sources(id),
    stream text NOT NULL,
    cursor_payload jsonb NOT NULL,
    cursor_hash char(64) NOT NULL,
    last_success_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, stream)
);

CREATE TABLE source_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id text NOT NULL REFERENCES data_sources(id),
    stream text NOT NULL,
    scheduled_for timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'blocked_rights', 'rate_limited')),
    input_cursor_hash char(64),
    output_cursor_hash char(64),
    documents_seen integer NOT NULL DEFAULT 0 CHECK (documents_seen >= 0),
    safe_error_code text,
    rights_hash char(64) NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX source_runs_source_time_idx ON source_runs (source_id, scheduled_for DESC);

CREATE TABLE raw_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id text NOT NULL REFERENCES data_sources(id),
    source_document_id text,
    canonical_url text NOT NULL,
    title text NOT NULL,
    content_sha256 char(64) NOT NULL,
    normalized_sha256 char(64) NOT NULL,
    object_key text,
    storage_class text NOT NULL CHECK (storage_class IN ('metadata_only', 'licensed_fulltext', 'official_document')),
    published_at timestamptz NOT NULL,
    first_seen_at timestamptz NOT NULL,
    fetched_at timestamptz NOT NULL,
    license_id text NOT NULL,
    rights_hash char(64) NOT NULL,
    extractor_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, content_sha256)
);
CREATE UNIQUE INDEX raw_documents_source_doc_idx
    ON raw_documents (source_id, source_document_id) WHERE source_document_id IS NOT NULL;
CREATE INDEX raw_documents_point_in_time_idx ON raw_documents (first_seen_at, published_at);

CREATE TABLE document_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES raw_documents(id),
    revision integer NOT NULL CHECK (revision >= 1),
    content_sha256 char(64) NOT NULL,
    object_key text,
    first_seen_at timestamptz NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    UNIQUE (document_id, revision)
);

CREATE TABLE document_embeddings (
    document_id uuid NOT NULL REFERENCES raw_documents(id),
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    embedding_model text NOT NULL,
    embedding_version text NOT NULL,
    content_sha256 char(64) NOT NULL,
    embedding vector(1024) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, chunk_index, embedding_model, embedding_version)
);
CREATE INDEX document_embeddings_hnsw_idx
    ON document_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE event_clusters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_event_type text NOT NULL,
    opened_at timestamptz NOT NULL,
    last_changed_at timestamptz NOT NULL,
    closed_at timestamptz
);

CREATE TABLE market_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id uuid NOT NULL REFERENCES event_clusters(id),
    revision integer NOT NULL CHECK (revision >= 1),
    event_type text NOT NULL,
    event_status text NOT NULL,
    direction text NOT NULL,
    materiality numeric(5,4) NOT NULL CHECK (materiality BETWEEN 0 AND 1),
    impact_horizon text NOT NULL,
    confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    event_time timestamptz,
    published_at timestamptz NOT NULL,
    first_seen_at timestamptz NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    extractor_version text NOT NULL,
    rule_version text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cluster_id, revision)
);
CREATE INDEX market_events_point_in_time_idx ON market_events (first_seen_at, event_type);
CREATE INDEX market_events_payload_gin_idx ON market_events USING gin (payload);

CREATE TABLE event_documents (
    event_id uuid NOT NULL REFERENCES market_events(id),
    document_id uuid NOT NULL REFERENCES raw_documents(id),
    relation text NOT NULL CHECK (relation IN ('primary', 'corroboration', 'update', 'correction', 'conflict')),
    PRIMARY KEY (event_id, document_id)
);

CREATE TABLE event_entities (
    event_id uuid NOT NULL REFERENCES market_events(id),
    entity_type text NOT NULL CHECK (entity_type IN ('instrument', 'company', 'region', 'industry')),
    entity_id text NOT NULL,
    PRIMARY KEY (event_id, entity_type, entity_id)
);
CREATE INDEX event_entities_lookup_idx ON event_entities (entity_type, entity_id, event_id);

CREATE TABLE event_claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id uuid NOT NULL REFERENCES market_events(id),
    name text NOT NULL,
    value jsonb NOT NULL,
    unit text,
    period text,
    evidence_document_id uuid NOT NULL REFERENCES raw_documents(id),
    evidence_span text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (event_id, evidence_document_id)
        REFERENCES event_documents(event_id, document_id)
);
CREATE INDEX event_claims_event_idx ON event_claims (event_id, name);

CREATE TABLE semantic_rule_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name text NOT NULL,
    version text NOT NULL,
    content_sha256 char(64) NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    change_reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rule_name, version)
);

CREATE TABLE conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id),
    title text,
    created_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz
);

CREATE TABLE conversation_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    role text NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    content jsonb NOT NULL,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    model_version text,
    prompt_hash text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversation_messages_order_idx ON conversation_messages (conversation_id, created_at);

CREATE TABLE llm_generations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid REFERENCES app_users(id),
    task_type text NOT NULL,
    provider text NOT NULL,
    model_version text NOT NULL,
    adapter_version text,
    prompt_hash char(64) NOT NULL,
    rule_version text NOT NULL,
    response_schema_hash char(64) NOT NULL,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    privacy_level text NOT NULL,
    request_hash char(64) NOT NULL,
    raw_response_hash char(64),
    status text NOT NULL CHECK (status IN ('succeeded', 'failed', 'rejected', 'degraded')),
    input_tokens integer CHECK (input_tokens >= 0),
    output_tokens integer CHECK (output_tokens >= 0),
    latency_ms integer CHECK (latency_ms >= 0),
    safe_error_code text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_generations_created_idx ON llm_generations (created_at DESC);

CREATE TABLE briefings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id),
    kind text NOT NULL CHECK (kind IN ('premarket', 'sector_rotation', 'policy', 'on_demand')),
    generated_at timestamptz NOT NULL,
    as_of timestamptz NOT NULL,
    data_delay_seconds integer NOT NULL CHECK (data_delay_seconds >= 0),
    omitted_duplicate_count integer NOT NULL DEFAULT 0 CHECK (omitted_duplicate_count >= 0),
    source_health jsonb NOT NULL DEFAULT '[]',
    model_version text NOT NULL,
    prompt_hash char(64) NOT NULL,
    rule_version text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX briefings_owner_kind_time_idx ON briefings (owner_id, kind, generated_at DESC);

CREATE TABLE predictions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id uuid NOT NULL REFERENCES instruments(id),
    decision_time timestamptz NOT NULL,
    feature_cutoff_at timestamptz NOT NULL,
    horizon text NOT NULL,
    quant_model_version text NOT NULL,
    text_model_version text NOT NULL,
    fusion_model_version text NOT NULL,
    feature_snapshot_hash text NOT NULL,
    direction_probability numeric(6,5) NOT NULL CHECK (direction_probability BETWEEN 0 AND 1),
    return_q10 numeric,
    return_q50 numeric,
    return_q90 numeric,
    calibration_version text NOT NULL,
    rule_version text NOT NULL,
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (feature_cutoff_at <= decision_time),
    CHECK (
        (return_q10 IS NULL AND return_q50 IS NULL AND return_q90 IS NULL)
        OR (
            return_q10 IS NOT NULL AND return_q50 IS NOT NULL AND return_q90 IS NOT NULL
            AND return_q10 <= return_q50 AND return_q50 <= return_q90
        )
    )
);
CREATE INDEX predictions_instrument_time_idx ON predictions (instrument_id, decision_time);

CREATE TABLE prediction_outcomes (
    prediction_id uuid PRIMARY KEY REFERENCES predictions(id),
    observed_at timestamptz NOT NULL,
    realized_return numeric,
    realized_excess_return numeric,
    assumed_entry_price numeric,
    assumed_exit_price numeric,
    estimated_cost numeric NOT NULL DEFAULT 0,
    executable boolean NOT NULL,
    non_executable_reason text,
    outcome_data_version text NOT NULL
);

CREATE TABLE advice_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id),
    instrument_id uuid REFERENCES instruments(id),
    prediction_id uuid REFERENCES predictions(id),
    evidence_ids uuid[] NOT NULL DEFAULT '{}',
    content jsonb NOT NULL,
    model_version text NOT NULL,
    prompt_hash text NOT NULL,
    rule_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id),
    event_cluster_id uuid NOT NULL REFERENCES event_clusters(id),
    event_revision integer NOT NULL,
    channel text NOT NULL CHECK (channel IN ('in_app', 'browser_desktop')),
    severity text NOT NULL CHECK (severity IN ('critical', 'high', 'normal', 'low')),
    change_type text NOT NULL CHECK (change_type IN ('new', 'update', 'correction', 'contradiction')),
    payload jsonb NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    acknowledged_at timestamptz,
    FOREIGN KEY (event_cluster_id, event_revision)
        REFERENCES market_events(cluster_id, revision)
);
CREATE INDEX notifications_owner_created_idx ON notifications (owner_id, created_at DESC);

CREATE TABLE job_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name text NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    scheduled_for timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    attempt integer NOT NULL DEFAULT 0,
    error_code text,
    safe_error_message text,
    metrics jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX job_runs_task_time_idx ON job_runs (task_name, scheduled_for DESC);

CREATE TABLE outbox_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic text NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempt integer NOT NULL DEFAULT 0
);
CREATE INDEX outbox_unpublished_idx ON outbox_events (created_at) WHERE published_at IS NULL;

CREATE TABLE audit_log (
    id bigserial PRIMARY KEY,
    owner_id uuid REFERENCES app_users(id),
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text,
    request_id text,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_created_idx ON audit_log (created_at DESC);
"""


DOWNGRADE_SQL = r"""
DROP TABLE IF EXISTS audit_log, outbox_events, job_runs, notifications, advice_records,
    prediction_outcomes, predictions, briefings, llm_generations,
    conversation_messages, conversations,
    semantic_rule_versions, event_claims, event_entities, event_documents, market_events,
    event_clusters, document_embeddings, document_revisions, raw_documents,
    source_runs, source_cursors, data_sources,
    holdings, company_regions, industry_memberships, instruments, companies, portfolios,
    app_users CASCADE;
DROP EXTENSION IF EXISTS vector;
DROP EXTENSION IF EXISTS btree_gist;
"""


def _execute_statements(script: str) -> None:
    """Execute plain DDL one statement at a time for asyncpg compatibility."""

    for statement in script.split(";"):
        if stripped := statement.strip():
            op.execute(stripped)


def upgrade() -> None:
    _execute_statements(UPGRADE_SQL)


def downgrade() -> None:
    _execute_statements(DOWNGRADE_SQL)
