# ADR-0001: Modular monolith with separate runtime processes

- Status: accepted
- Date: 2026-07-21

## Decision

Keep domain code in one typed Python package, deploy API, worker and scheduler separately, and use stable contracts/outbox between boundaries. Use one Next.js frontend.

## Rationale

A personal deployment does not justify networked microservices, Kafka or Kubernetes. A single process would make long-running ingestion/model tasks and API reliability interfere. This shape preserves simple transactions and debugging while allowing later module extraction under measured load.

## Consequences

Module dependency rules and database ownership require review. Only one scheduler replica is allowed. Service extraction waits for explicit queue/latency/capacity triggers.
