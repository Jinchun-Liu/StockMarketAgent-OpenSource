# ADR-0004: Constrained statistical fusion, not LLM score override

- Status: accepted
- Date: 2026-07-21

## Decision

Use Qwen3.5-9B only to extract evidence-grounded event features. Train separate LightGBM price and text models, fuse their out-of-fold probabilities with regularized logistic regression, and calibrate on a disjoint time window.

## Rationale

Allowing an LLM to subjectively modify a numeric forecast is not reproducible, calibrated or safely backtestable. A simple fusion model can be audited and rejected when it adds no value.

## Consequences

There is no guaranteed accuracy increase. Directional output stays hidden until rolling backtests and 60 trading days of frozen paper operation pass predefined gates.
