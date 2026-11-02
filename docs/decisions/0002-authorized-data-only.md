# ADR-0002: Authorized and official data sources only

- Status: accepted
- Date: 2026-07-21

## Decision

Use CNINFO/transaction-exchange verification, official government policy sites and licensed Choice APIs. Keep undocumented Eastmoney endpoints, raw forum scraping and AKShare-based production ingestion disabled.

## Rationale

Reachable web endpoints and open-source wrappers do not grant stable access, caching, embedding, training or redistribution rights. Rights uncertainty threatens reliability and the entire derived dataset/model lineage.

## Consequences

Real-time news waits for a valid Choice contract. Every source needs a machine-enforced rights record; lack of permission causes visible data gaps instead of technical workarounds.
