# ADR-0003: PostgreSQL/pgvector as episodic memory

- Status: superseded in part by ADR-0007
- Date: 2026-07-21

## Decision

Use Redis for 24–48 hour working memory, PostgreSQL/pgvector for event/document/prediction memory, MinIO for immutable evidence, and Git-managed YAML for semantic rules.

## Rationale

Events require transactions, versioning, filtering, audit and vector candidate retrieval. A second vector database would duplicate metadata, authorization and backups. Seven days is a retrieval window, not an audit retention policy.

## Consequences

HNSW is only candidate recall. Fact-slot comparison determines changes. Prediction and advice history remains after the vector hot window expires.
