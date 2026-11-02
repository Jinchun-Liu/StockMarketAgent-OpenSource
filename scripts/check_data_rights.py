"""Validate staged source records without treating R1-R6 as R7 approval."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED = {
    "owner",
    "official_url",
    "access_method",
    "license_id",
    "expiry",
    "quota",
    "cache_allowed",
    "fulltext_allowed",
    "embedding_allowed",
    "training_allowed",
    "redistribution_allowed",
    "retention_days",
}
UNRESOLVED_MARKERS = (
    "pending",
    "review-required",
    "review_required",
    "per-domain",
    "unknown",
)
CONTROL_FIELDS = {
    "cache_allowed",
    "fulltext_allowed",
    "embedding_allowed",
    "training_allowed",
    "redistribution_allowed",
}


def _is_unresolved(value: object) -> bool:
    return isinstance(value, str) and any(marker in value.lower() for marker in UNRESOLVED_MARKERS)


def _is_r1_r6_research_profile(source: dict[str, Any]) -> bool:
    """Recognize the explicit non-commercial owner research profile."""

    profile = source.get("dev_live")
    return bool(
        isinstance(profile, dict)
        and profile.get("runtime_mode") == "dev_live"
        and profile.get("release_tier") == "dev_only"
        and profile.get("rights_status") == "unverified"
    )


def _expiry_error(value: object, *, today: date, require_current: bool) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return "expiry datetime must include a timezone"
        expiry_date = value.astimezone(UTC).date()
    elif isinstance(value, date):
        expiry_date = value
    elif isinstance(value, str):
        try:
            expiry_date = date.fromisoformat(value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return "expiry must be null or ISO-8601 date/datetime"
            if parsed.tzinfo is None:
                return "expiry datetime must include a timezone"
            expiry_date = parsed.astimezone(UTC).date()
    else:
        return "expiry must be null or ISO-8601 date/datetime"
    return "rights record is expired" if require_current and expiry_date < today else None


def validate_document(document: dict[str, Any], *, today: date | None = None) -> list[str]:
    """Return fail-closed registry errors without making network calls."""

    check_date = today or datetime.now(UTC).date()
    errors: list[str] = []
    seen_ids: set[str] = set()
    for source in document.get("sources", []):
        source_id = source.get("id", "<missing-id>")
        if not isinstance(source_id, str) or not source_id:
            errors.append("<missing-id>: source id must be a non-empty string")
            continue
        if source_id in seen_ids:
            errors.append(f"{source_id}: duplicate source id")
        seen_ids.add(source_id)

        enabled = source.get("enabled")
        if not isinstance(enabled, bool):
            errors.append(f"{source_id}: enabled must be a boolean")

        capabilities = source.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities:
            errors.append(f"{source_id}: capabilities must be a non-empty list")
        authority = source.get("authority")
        if not isinstance(authority, int | float) or isinstance(authority, bool):
            errors.append(f"{source_id}: authority must be numeric")
        elif not 0 <= authority <= 1:
            errors.append(f"{source_id}: authority must be between 0 and 1")

        rights = source.get("data_rights", {})
        if not isinstance(rights, dict):
            errors.append(f"{source_id}: data_rights must be an object")
            continue
        missing = REQUIRED - rights.keys()
        if missing:
            errors.append(f"{source_id}: missing rights fields {sorted(missing)}")

        retention = rights.get("retention_days")
        if not isinstance(retention, int) or isinstance(retention, bool) or retention < 0:
            errors.append(f"{source_id}: retention_days must be a non-negative integer")
        expiry_error = _expiry_error(
            rights.get("expiry"), today=check_date, require_current=enabled is not False
        )
        if expiry_error:
            errors.append(f"{source_id}: {expiry_error}")

        if enabled:
            unresolved = {
                key
                for key, value in rights.items()
                if _is_unresolved(value) and (key in CONTROL_FIELDS or key == "license_id")
            }
            if unresolved and not _is_r1_r6_research_profile(source):
                errors.append(f"{source_id}: enabled with unresolved rights {sorted(unresolved)}")
            if rights.get("fulltext_allowed") is False and rights.get("embedding_allowed") not in (
                False,
                None,
            ):
                errors.append(
                    f"{source_id}: embedding cannot be enabled when full text is forbidden"
                )

        capability_tiers = source.get("capability_tiers")
        if capability_tiers is not None:
            if not isinstance(capability_tiers, dict):
                errors.append(f"{source_id}: capability_tiers must be an object")
            elif isinstance(capabilities, list) and set(capability_tiers) != set(capabilities):
                errors.append(f"{source_id}: capability_tiers must cover every capability")
    return errors


def validate(path: Path) -> list[str]:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_document(document)


def main() -> int:
    errors = validate(Path("configs/sources.yaml"))
    if errors:
        print("\n".join(errors))
        return 1
    print("data-rights registry is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
