"""Liveness and readiness endpoints."""

from fastapi import APIRouter

from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.contracts.system import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp=utc_now(),
    )


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    # R1 adds bounded dependency probes for PostgreSQL, Valkey and SeaweedFS.
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        timestamp=utc_now(),
    )
