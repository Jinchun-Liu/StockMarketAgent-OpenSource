"""Public, non-sensitive runtime capabilities."""

from fastapi import APIRouter, Request

from apps.api.http import request_ids
from stock_assistant.common.time import utc_now
from stock_assistant.config import get_settings
from stock_assistant.contracts.api import ApiResponse, ResponseMeta
from stock_assistant.contracts.system import RuntimeCapabilities

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/capabilities", response_model=ApiResponse[RuntimeCapabilities])
async def capabilities(request: Request) -> ApiResponse[RuntimeCapabilities]:
    settings = get_settings()
    as_of = utc_now()
    request_id, correlation_id = request_ids(request)
    return ApiResponse(
        data=RuntimeCapabilities(
            local_model=settings.local_llm_model,
            external_llm_enabled=settings.external_llm_enabled,
            social_source_enabled=settings.enable_social_source,
            directional_forecast_enabled=settings.enable_directional_forecast,
        ),
        meta=ResponseMeta(
            request_id=request_id,
            correlation_id=correlation_id,
            as_of=as_of,
        ),
    )
