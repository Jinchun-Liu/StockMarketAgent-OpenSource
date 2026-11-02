# ADR-0007: Valkey and SeaweedFS permissive storage baseline

- Status: accepted
- Date: 2026-07-22
- Supersedes: the Redis and MinIO implementation choices in ADR-0003; PostgreSQL/pgvector and Git-managed
  semantic rules remain unchanged

## Decision

Use Valkey for rebuildable 24–48 hour working memory, cache, rate limits, locks and the initial Celery
broker. Use SeaweedFS through its S3-compatible API for immutable evidence and ML/data artifacts. Keep
both behind `CachePort`/task boundaries and `ObjectStorePort`; PostgreSQL remains the only fact store.

R0P replaced the Compose services with pinned Valkey 9.1.0 and SeaweedFS 4.40 images. This records the
runtime selection only: production adapters, checksum/version-retention behavior, backup/restore and
failure injection must still pass before R1 stores real evidence. The old `REDIS_*` and `MINIO_*`
configuration names remain temporary compatibility names and do not mean those servers are deployed.

## Rationale

Redis 7.4 is RSALv2/SSPLv1 source-available rather than the prior BSD baseline. The MinIO community
repository was archived on 2026-04-25, is AGPL-3.0 and moved to source-only distribution. Valkey is
BSD-3-Clause and protocol-compatible with the required cache/client surface. SeaweedFS is actively
maintained under Apache-2.0 and exposes the S3 operations this project needs.

## Consequences

Migration must verify Celery, TTL, locking, rate limiting, multipart upload, checksum, lifecycle,
backup and clean-room restore. Existing environment names may be retained temporarily for compatibility,
but vendor types and names must not become public contracts. RabbitMQ or another workflow engine is not
added without measured reliability/SLO evidence; object-store features outside the tested S3 subset are
not assumed.
