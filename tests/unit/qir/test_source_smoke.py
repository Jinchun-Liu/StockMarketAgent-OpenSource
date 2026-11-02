from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

from apps.runtime.source_smoke import run_source_smoke
from pydantic import JsonValue, SecretStr

from stock_assistant.config.settings import Settings
from stock_assistant.platform.sources.research_preview_runner import (
    ResearchPreviewRunnerError,
)

NOW = datetime(2026, 8, 22, 7, 0, tzinfo=UTC)


class _NeverCalledRunner:
    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        raise AssertionError((endpoint_name, arguments))


class _MixedRunner:
    async def run(
        self,
        endpoint_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        if endpoint_name == "anns_d":
            return [
                {
                    "ann_date": "20260822",
                    "ts_code": "603986.SH",
                    "name": "兆易创新",
                    "title": "兆易创新董事会公告",
                    "url": "https://static.cninfo.com.cn/finalpage/test.pdf",
                    "rec_time": "2026-08-22 14:00:00",
                }
            ]
        if endpoint_name == "income":
            raise ResearchPreviewRunnerError(
                "RESEARCH_DATA_PERMISSION_BLOCKED",
                retryable=False,
            )
        if endpoint_name == "daily":
            return [{"unexpected": "provider-specific payload"}]
        return []


def test_source_smoke_rejects_json_secret_without_calling_provider() -> None:
    settings = Settings(r1_research_data_api_token=SecretStr('{"token":"not-a-raw-secret"}'))

    result = asyncio.run(run_source_smoke(settings=settings, now=NOW, runner=_NeverCalledRunner()))

    assert result.status == "degraded"
    assert result.credential_format_valid is False
    assert len(result.interfaces) == 5
    assert {item.reason_code for item in result.interfaces} == {
        "PROMAX_CREDENTIAL_MISSING",
        "QUANTDATA_CREDENTIAL_FORMAT_INVALID",
    }


def test_source_smoke_reports_each_interface_without_raw_provider_content() -> None:
    settings = Settings(
        r1_research_data_api_token=SecretStr("tk_live_" + "a" * 32),
        r1_promax_api_key=SecretStr("tsr_" + "b" * 32),
    )

    result = asyncio.run(run_source_smoke(settings=settings, now=NOW, runner=_MixedRunner()))

    by_endpoint = {item.endpoint_name: item for item in result.interfaces}
    assert result.status == "degraded"
    assert result.credential_format_valid is True
    assert by_endpoint["anns_d"].status == "available"
    assert by_endpoint["anns_d"].rows_received == 1
    assert by_endpoint["anns_d"].records_accepted == 1
    assert by_endpoint["income"].status == "blocked"
    assert by_endpoint["income"].reason_code == "RESEARCH_DATA_PERMISSION_BLOCKED"
    assert by_endpoint["daily"].status == "blocked"
    assert by_endpoint["daily"].reason_code == "RESEARCH_DATA_SCHEMA_INVALID"
    assert by_endpoint["news"].status == "empty"
    assert by_endpoint["npr"].status == "empty"
