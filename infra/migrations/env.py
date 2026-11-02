"""Async Alembic environment."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from stock_assistant.config import database_url_from_environment, get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _set_database_url(value: str) -> None:
    """Escape ConfigParser interpolation without changing the SQLAlchemy URL."""

    config.set_main_option("sqlalchemy.url", value.replace("%", "%%"))


database_url_override = config.attributes.get("database_url")
if database_url_override is not None:
    if not isinstance(database_url_override, str):
        raise TypeError("Alembic database_url override must be a string")
    _set_database_url(database_url_override)
else:
    _set_database_url(database_url_from_environment(get_settings().database_url.get_secret_value()))
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        asyncio.run(run_async_migrations())
        return
    do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
