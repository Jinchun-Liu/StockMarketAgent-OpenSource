"""Managed credentials must be tested on a new connection, with no secret output."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.runtime import health as runtime_health
from asyncpg import InvalidPasswordError
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from stock_assistant.contracts.common import ComponentHealth, HealthStatus, OperationContext
from stock_assistant.platform.db import health

NOW = datetime(2026, 9, 1, tzinfo=UTC)
URL = "postgresql+asyncpg://synthetic:synthetic@localhost/synthetic"


def _engine(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, AsyncMock]:
    connection = AsyncMock()
    engine = MagicMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    engine.dispose = AsyncMock()
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(health, "create_async_engine", factory)
    return factory, engine, connection


@pytest.mark.asyncio
async def test_health_uses_fresh_unpooled_connection_each_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, engine, connection = _engine(monkeypatch)
    probe = health.PostgresHealthCheck(URL)
    for _ in range(2):
        result = await probe.check_health(OperationContext(schema_version="1.0", as_of=NOW))
        assert result.status is HealthStatus.HEALTHY
    assert factory.call_count == engine.dispose.await_count == 2
    assert all(call.kwargs["poolclass"] is NullPool for call in factory.call_args_list)
    assert all(call.kwargs["connect_args"]["timeout"] <= 5 for call in factory.call_args_list)
    assert str(connection.execute.call_args.args[0]) == "SELECT 1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        InvalidPasswordError("synthetic-private-detail"),
        OperationalError("statement", {}, Exception("synthetic-private-detail")),
        OSError("synthetic-private-detail"),
    ],
)
async def test_database_failure_is_unhealthy_and_redacted(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    _, engine, _ = _engine(monkeypatch)
    engine.connect.return_value.__aenter__.side_effect = failure
    result = await health.PostgresHealthCheck(URL).check_health(
        OperationContext(schema_version="1.0", as_of=NOW)
    )
    assert result.status is HealthStatus.UNAVAILABLE
    assert result.reason_code == "DATABASE_UNAVAILABLE"
    assert "synthetic-private-detail" not in result.model_dump_json()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_timeout_is_bounded_and_disposes(monkeypatch: pytest.MonkeyPatch) -> None:
    _, engine, _ = _engine(monkeypatch)
    monkeypatch.setattr(health, "_TIMEOUT_SECONDS", 0.01)

    async def hang() -> None:
        await asyncio.Event().wait()

    engine.connect.return_value.__aenter__.side_effect = hang
    result = await health.PostgresHealthCheck(URL).check_health(
        OperationContext(schema_version="1.0", as_of=NOW)
    )
    assert result.status is HealthStatus.UNAVAILABLE
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_does_not_swallow_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    _, engine, _ = _engine(monkeypatch)
    engine.connect.return_value.__aenter__.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await health.PostgresHealthCheck(URL).check_health(
            OperationContext(schema_version="1.0", as_of=NOW)
        )
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "status,expected", [(HealthStatus.HEALTHY, 0), (HealthStatus.UNAVAILABLE, 1)]
)
def test_container_exit_status_tracks_database_health(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: HealthStatus,
    expected: int,
) -> None:
    monkeypatch.setattr(runtime_health, "database_url_from_environment", lambda _: URL)
    adapter = MagicMock()
    adapter.check_health = AsyncMock(
        return_value=ComponentHealth(component="postgresql", status=status)
    )
    monkeypatch.setattr(runtime_health, "PostgresHealthCheck", lambda _: adapter)
    assert runtime_health.main() == expected
    assert capsys.readouterr() == ("", "")


def test_container_configuration_failure_is_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def invalid(_: str) -> str:
        raise RuntimeError("synthetic-private-detail")

    monkeypatch.setattr(runtime_health, "database_url_from_environment", invalid)
    assert runtime_health.main() == 1
    assert capsys.readouterr() == ("", "")
