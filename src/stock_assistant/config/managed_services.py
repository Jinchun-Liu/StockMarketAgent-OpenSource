"""Fail-closed composition of managed-service connection settings."""

from __future__ import annotations

import os

from sqlalchemy import URL


def database_url_from_environment(default_url: str) -> str:
    """Build an async PostgreSQL URL from split ECS settings when configured."""

    host = os.getenv("DB_HOST")
    if host is None:
        return default_url
    values = {
        "port": os.getenv("DB_PORT"),
        "database": os.getenv("DB_NAME"),
        "username": os.getenv("DB_USERNAME"),
        "password": os.getenv("DB_PASSWORD"),
    }
    missing = sorted(key for key, value in values.items() if not value)
    if missing:
        raise RuntimeError(f"DATABASE_CONFIGURATION_INCOMPLETE:{','.join(missing)}")
    try:
        port = int(values["port"] or "")
    except ValueError as error:
        raise RuntimeError("DATABASE_PORT_INVALID") from error
    return URL.create(
        drivername="postgresql+asyncpg",
        username=values["username"],
        password=values["password"],
        host=host,
        port=port,
        database=values["database"],
        query={"ssl": "require"},
    ).render_as_string(hide_password=False)


__all__ = ["database_url_from_environment"]
