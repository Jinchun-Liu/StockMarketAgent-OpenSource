# Runtime configuration

These YAML files contain environment-independent policy. Secrets and machine-specific endpoints belong in environment variables. Every configuration file has a `version`, and its content hash is recorded with generated briefings, predictions and model runs.

- `sources.yaml`: connector enablement and data-rights registry.
- `scoring.yaml`: quality formula, source gates and push thresholds.
- `schedules.yaml`: China-market task timing and trading-session gates.
- `models.yaml`: frozen model IDs, privacy rules and feature gates.
