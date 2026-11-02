# Integration tests

The R0P suite starts disposable, project-scoped PostgreSQL/pgvector, Valkey and SeaweedFS containers.
It verifies real health, a PostgreSQL query, a Valkey write/read, SeaweedFS control-plane access,
loopback-only console publication and the absence of host ports for every data plane. The same command
then starts a separate disposable PostgreSQL project for the transactional Outbox, technical Inbox and
Job-ledger recovery suite.

Run the real service suite with:

```text
SMA_RUN_DOCKER_INTEGRATION=1 make integration-test
```

The four-slot acceptance test creates four real detached Git worktrees at explicit temporary paths,
starts all four stacks concurrently, proves project/network/volume/cache separation, destroys one exact
slot, verifies the other three remain healthy, and finally removes each worktree through the Git CLI:

```text
SMA_RUN_DOCKER_PARALLEL_ISOLATION=1 make parallel-isolation-test
```

Both commands use only generated synthetic state. They never call a vendor API, read `.env`, use a real
account or expose PostgreSQL, Valkey or the SeaweedFS S3 endpoint to the host. Redis and MinIO are not
target runtime services.

The R1 database gate starts one disposable, loopback-only PostgreSQL project. It creates a dedicated
`stock_assistant_active_target_test` database for the Active Target repository tests and uses the
existing `stock_assistant_r0p_test` database for source-cursor CAS tests. Both suites must execute
against PostgreSQL without skips before the target succeeds. The target always removes its exact
Compose project and named volumes, including on failure:

```text
make r1-database-test
```

R1 is deployed as a single-host, single-version application. Operators must stop the old worker before
starting a new worker version; mixed old/new worker rolling deployment is unsupported.
