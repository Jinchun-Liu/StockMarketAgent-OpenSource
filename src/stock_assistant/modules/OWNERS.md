# Module boundaries

| Module | Owns | Must not own |
|---|---|---|
| `portfolio` | one active-target revision and optional private position metadata | broker order execution or multi-target scheduling |
| `instruments` | security/company/industry/region identity | market documents |
| `ingestion` | connectors, normalization, rights and source quality | model opinions |
| `events` | domestic/international facts, clusters, revisions and material changes | notifications or unsupported supply-chain inference |
| `memory` | working/episodic/semantic retrieval | source scraping |
| `briefings` | single-target premarket and evidence summaries | raw persistence |
| `policies` | policy-to-region/industry/company relevance | market prices |
| `sectors` | sector identity, breadth and numerical reference context | deferred fair rotation or order generation |
| `chat` | intent, retrieval, grounded answer workflow | unrestricted browsing |
| `forecasting` | point-in-time features, models and predictions | prose generation |
| `notifications` | in-app delivery, idempotency and acknowledgement | event extraction |

Modules communicate through contracts and application services. Direct cross-module database access is forbidden.
This table describes domain responsibility, not human approval or active lane ownership. Executable dependency rules
live in `governance/architecture.json`; distributed write ownership lives in `governance/delivery.json`.
