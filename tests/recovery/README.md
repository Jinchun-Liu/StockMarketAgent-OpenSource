# Recovery foundation tests

This directory owns the R0P transactional Outbox, technical Inbox and Job-transition
recovery acceptance tests. The suite uses only disposable PostgreSQL state created by
the isolated integration harness; it does not model the future user-facing Inbox.

The real suite requires a local or CI-internal `postgresql+asyncpg` URL whose database
name starts with `stock_assistant_r0p_test`. The integration harness supplies it through
the process-only `R0P_TEST_DATABASE_URL` variable; no dotenv file or real account is used.

The acceptance scenarios prove:

- a business result and its logical Outbox row commit or roll back together;
- 100 sequential/concurrent replays preserve one business result and one Outbox row;
- a claimed row reconstructs its complete persisted envelope, including schema identity
  and headers;
- an expired relay lease is reclaimed, while a stale attempt cannot acknowledge or
  release a newer lease even when the caller reuses its token;
- attempt 99 schedules a claimable final attempt, while an explicit failure or expired
  lease at attempt 100 records `OUTBOX_ATTEMPTS_EXHAUSTED`, clears the lease and cannot
  be claimed again;
- a technical Inbox receipt, its database side effect and acknowledgement share one
  transaction, so crashes roll back and post-commit redelivery does not repeat the effect;
- the technical Inbox stores only a canonical logical-content digest and rejects producer,
  partition, trace, time, schema, header or payload changes under an existing identity;
- a broker delivery followed by a relay crash is redelivered without repeating the Inbox
  database side effect;
- every one of the eight frozen Job states, including historical intermediate states, is
  recovered from the append-only transition ledger only when both occurrence and database
  knowledge time are visible at the requested point in time.

The technical Inbox helper deliberately accepts only a handler sharing the same database
connection. External side effects are not made exactly-once by this primitive and must use
their own idempotency mechanism in a later composed adapter.

The suite also verifies that the PostgreSQL Unit of Work and Outbox publisher structurally
satisfy their frozen runtime-checkable Ports. A Unit of Work becomes permanently terminal
after commit, rollback or transaction-start failure and never exposes a completed connection.
