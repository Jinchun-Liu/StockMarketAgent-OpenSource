# Dataset builders

Builders create point-in-time Parquet datasets and manifests. A row is eligible only when all source facts have `first_seen_at <= feature_cutoff_at`, rights permit the intended use, and event-cluster split assignment is known.
