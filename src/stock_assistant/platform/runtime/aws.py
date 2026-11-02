"""Compose managed-service URLs from separately injected ECS values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

from stock_assistant.config import database_url_from_environment


@dataclass(frozen=True, slots=True)
class ValkeyUrls:
    redis: str
    broker: str
    result_backend: str


def valkey_urls_from_environment(
    *,
    default_redis: str,
    default_broker: str,
    default_result_backend: str,
) -> ValkeyUrls:
    """Build TLS Valkey URLs only when the complete managed endpoint is injected."""

    host = os.getenv("VALKEY_HOST")
    if host is None:
        return ValkeyUrls(default_redis, default_broker, default_result_backend)
    port_text = os.getenv("VALKEY_PORT")
    auth_token = os.getenv("VALKEY_AUTH_TOKEN")
    if not port_text or not auth_token:
        raise RuntimeError("VALKEY_CONFIGURATION_INCOMPLETE")
    try:
        port = int(port_text)
    except ValueError as error:
        raise RuntimeError("VALKEY_PORT_INVALID") from error
    if port < 1 or port > 65535:
        raise RuntimeError("VALKEY_PORT_INVALID")
    credential = quote(auth_token, safe="")
    base = f"rediss://:{credential}@{host}:{port}"
    tls_query = "ssl_cert_reqs=required"
    return ValkeyUrls(
        redis=f"{base}/0?{tls_query}",
        broker=f"{base}/1?{tls_query}",
        result_backend=f"{base}/2?{tls_query}",
    )


__all__ = ["ValkeyUrls", "database_url_from_environment", "valkey_urls_from_environment"]
