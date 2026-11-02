#!/usr/bin/env python3
"""Validate the canonical metric registry using the Python standard library only."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

METRIC_ID = re.compile(r"^metric:[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*:v[1-9][0-9]*$")
METRIC_NAME = re.compile(r"^stock_assistant_[a-z][a-z0-9_]*$")
SEMANTIC_KEY = re.compile(r"^[a-z][a-z0-9_.|:-]*$")
OWNER = re.compile(r"^[a-z][a-z0-9_-]*$")
ALLOWED_CATEGORIES = {"runtime", "data_quality", "product_quality", "ml_quality"}
ALLOWED_TYPES = {"counter", "gauge", "histogram", "info"}
ALLOWED_UNITS = {"1", "seconds", "bytes", "items", "ratio"}
ALLOWED_STATUSES = {"declared", "implemented", "deprecated"}
FORBIDDEN_LABELS = {
    "actor_id",
    "correlation_id",
    "evidence_id",
    "exception",
    "holding_id",
    "instrument_id",
    "message",
    "prompt",
    "request_id",
    "symbol",
    "url",
    "user_id",
    "workspace_id",
}
REQUIRED_CHANGE_POLICY = {
    "search_existing_before_change",
    "reuse_semantic_equivalent",
    "duplicate_semantics_forbidden",
    "breaking_change_requires_new_major",
    "synchronize_all_affected_standards",
}


class MetricRegistryError(ValueError):
    """Raised when the registry is malformed."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MetricRegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, json.JSONDecodeError, MetricRegistryError) as exc:
        raise MetricRegistryError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MetricRegistryError(f"{path}: root must be an object")
    return value


def _required_string(item: dict[str, object], key: str, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise MetricRegistryError(f"{label}.{key} must be a non-empty string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MetricRegistryError(f"{label} must be an array of strings")
    return value


def _validate_change_policy(registry: dict[str, object]) -> list[str]:
    policy = registry.get("change_policy")
    if not isinstance(policy, dict):
        return ["governance/metrics.json.change_policy must be an object"]
    errors: list[str] = []
    for key in sorted(REQUIRED_CHANGE_POLICY):
        if policy.get(key) is not True:
            errors.append(f"governance/metrics.json.change_policy.{key} must be true")
    return errors


def _validate_buckets(item: dict[str, object], label: str, metric_type: str) -> list[str]:
    buckets = item.get("buckets")
    if metric_type != "histogram":
        return [] if buckets is None else [f"{label}.buckets is only valid for histograms"]
    if not isinstance(buckets, list) or not buckets:
        return [f"{label}.buckets must be a non-empty array for histograms"]
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
        for value in buckets
    ):
        return [f"{label}.buckets must contain positive finite numbers"]
    numeric = [float(value) for value in buckets]
    if numeric != sorted(set(numeric)):
        return [f"{label}.buckets must be strictly increasing and unique"]
    return []


def validate(root: Path) -> list[str]:
    """Return all metric registry violations."""

    try:
        registry = _load_json(root / "governance/metrics.json")
    except MetricRegistryError as exc:
        return [str(exc)]

    errors = _validate_change_policy(registry)
    if registry.get("schema_version") != "1.0":
        errors.append("governance/metrics.json.schema_version must be 1.0")
    status_definitions = registry.get("status_definitions")
    if not isinstance(status_definitions, dict) or set(status_definitions) != ALLOWED_STATUSES:
        errors.append(
            "governance/metrics.json.status_definitions must define exact supported statuses"
        )
    raw_metrics = registry.get("metrics")
    if not isinstance(raw_metrics, list) or not all(isinstance(item, dict) for item in raw_metrics):
        return [*errors, "governance/metrics.json.metrics must be an array of objects"]

    seen: dict[str, set[str]] = {"id": set(), "name": set(), "semantic_key": set()}
    for index, item in enumerate(raw_metrics):
        label = f"governance/metrics.json.metrics[{index}]"
        try:
            metric_id = _required_string(item, "id", label)
            name = _required_string(item, "name", label)
            semantic_key = _required_string(item, "semantic_key", label)
            category = _required_string(item, "category", label)
            metric_type = _required_string(item, "type", label)
            unit = _required_string(item, "unit", label)
            owner = _required_string(item, "owner", label)
            description = _required_string(item, "description", label)
            status = _required_string(item, "status", label)
            labels = _string_list(item.get("labels"), f"{label}.labels")
        except MetricRegistryError as exc:
            errors.append(str(exc))
            continue

        for key, value in (("id", metric_id), ("name", name), ("semantic_key", semantic_key)):
            if value in seen[key]:
                errors.append(f"{label}.{key} duplicates an existing metric: {value}")
            seen[key].add(value)
        if not METRIC_ID.fullmatch(metric_id):
            errors.append(f"{label}.id has invalid format: {metric_id}")
        if not METRIC_NAME.fullmatch(name):
            errors.append(f"{label}.name has invalid format: {name}")
        if not SEMANTIC_KEY.fullmatch(semantic_key):
            errors.append(f"{label}.semantic_key has invalid format: {semantic_key}")
        if not OWNER.fullmatch(owner):
            errors.append(f"{label}.owner has invalid format: {owner}")
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"{label}.category must be one of {sorted(ALLOWED_CATEGORIES)}")
        if metric_type not in ALLOWED_TYPES:
            errors.append(f"{label}.type must be one of {sorted(ALLOWED_TYPES)}")
        if unit not in ALLOWED_UNITS:
            errors.append(f"{label}.unit must be one of {sorted(ALLOWED_UNITS)}")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{label}.status must be one of {sorted(ALLOWED_STATUSES)}")
        if len(description.strip()) < 20:
            errors.append(f"{label}.description must define the metric semantics")
        if labels != sorted(set(labels)):
            errors.append(f"{label}.labels must be sorted and unique")
        forbidden = sorted(set(labels) & FORBIDDEN_LABELS)
        if forbidden:
            errors.append(f"{label}.labels contains forbidden high-cardinality labels: {forbidden}")
        if metric_type == "counter" and not name.endswith("_total"):
            errors.append(f"{label}.name for a counter must end with _total")
        errors.extend(_validate_buckets(item, label, metric_type))

        source = item.get("source")
        if status == "implemented":
            if not isinstance(source, str) or not source:
                errors.append(f"{label}.source is required for implemented metrics")
            elif not (root / source).is_file():
                errors.append(f"{label}.source does not exist: {source}")
        if status == "deprecated" and not isinstance(item.get("replaced_by"), str):
            errors.append(f"{label}.replaced_by is required for deprecated metrics")

    return errors


def main() -> int:
    errors = validate(_repo_root())
    if errors:
        print("Metric registry check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Metric registry check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
