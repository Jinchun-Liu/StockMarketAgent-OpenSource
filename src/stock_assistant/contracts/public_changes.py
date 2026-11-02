"""Public change vocabulary and the explicit internal duplicate mapping."""

from stock_assistant.contracts.briefings import BriefingChangeType


def map_public_change_type(value: str) -> BriefingChangeType | None:
    """Map internal change values; duplicates are intentionally omitted."""

    if value == "duplicate":
        return None
    try:
        return BriefingChangeType(value)
    except ValueError as exc:
        raise ValueError(f"unsupported public change type: {value}") from exc
