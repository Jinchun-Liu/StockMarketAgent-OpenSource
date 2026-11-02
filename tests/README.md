# Test layers

- `unit/`: pure domain and configuration behavior.
- `integration/`: PostgreSQL/pgvector、Valkey、SeaweedFS 和 provider adapter 的本地临时容器测试；
  R0 的 Redis/MinIO 迁移兼容测试单独标识，不作为目标架构。
- `contract/`: JSON schemas, migrations, source rights and provider interfaces.
- `e2e/`: user workflows across Web/API/worker; no live vendor API in CI.
- `fixtures/`: synthetic, non-sensitive examples only.

Live-provider smoke tests run manually against sandbox/licensed accounts, have hard quotas and never print payloads.
