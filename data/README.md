# Local data workspace

This directory is mounted by local jobs and is not a source of truth. Contents under `raw`, `interim`, `processed` and `private` are excluded from Git. Each generated dataset must have a manifest, rights snapshot and content hashes before it can be used for training or backtesting.

- `raw/`: immutable vendor/source snapshots permitted by contract.
- `interim/`: normalized documents and point-in-time feature staging.
- `processed/`: versioned Parquet datasets ready for research.
- `private/`: holdings exports and manually labelled confidential material.
- `samples/`: tiny synthetic, non-sensitive fixtures allowed in Git.
