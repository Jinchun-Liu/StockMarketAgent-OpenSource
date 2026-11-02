"""Fail-closed runtime configuration helpers for managed deployments."""

from stock_assistant.platform.runtime.aws import (
    database_url_from_environment,
    valkey_urls_from_environment,
)

__all__ = ["database_url_from_environment", "valkey_urls_from_environment"]
