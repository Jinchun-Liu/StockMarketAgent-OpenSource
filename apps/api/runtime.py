"""Compatibility wrapper for the managed runtime database helper."""

from stock_assistant.platform.runtime import (
    database_url_from_environment as _managed_database_url,
)


def database_url_from_environment(default_url: str) -> str:
    """Preserve the published API-specific failure codes."""

    try:
        return _managed_database_url(default_url)
    except RuntimeError as error:
        detail = str(error)
        if detail.startswith("DATABASE_CONFIGURATION_INCOMPLETE"):
            raise RuntimeError(detail.replace("DATABASE_", "API_DATABASE_", 1)) from error
        if detail == "DATABASE_PORT_INVALID":
            raise RuntimeError("API_DATABASE_PORT_INVALID") from error
        raise


__all__ = ["database_url_from_environment"]
