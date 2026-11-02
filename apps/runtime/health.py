"""Quiet ECS health command; replacement tasks reload managed credentials."""

import asyncio

from stock_assistant.common.time import utc_now
from stock_assistant.config import database_url_from_environment, get_settings
from stock_assistant.contracts.common import HealthStatus, OperationContext
from stock_assistant.platform.db.health import PostgresHealthCheck
from stock_assistant.ports.base import HealthCheckPort


def main() -> int:
    try:
        url = database_url_from_environment(get_settings().database_url.get_secret_value())
        probe: HealthCheckPort = PostgresHealthCheck(url)
        result = asyncio.run(
            probe.check_health(OperationContext(schema_version="1.0", as_of=utc_now()))
        )
    except (ValueError, RuntimeError, OSError):
        return 1
    return 0 if result.status is HealthStatus.HEALTHY else 1


if __name__ == "__main__":
    raise SystemExit(main())
