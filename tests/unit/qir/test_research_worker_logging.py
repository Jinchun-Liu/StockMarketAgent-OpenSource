from __future__ import annotations

import logging
from uuid import UUID

import httpx
import pytest
from apps.worker import tasks

from stock_assistant.platform.discovery.tavily import TavilyDiscoveryError
from stock_assistant.platform.sources.governed_web import GovernedWebSourceError


@pytest.mark.parametrize(
    ("error", "expected_code", "retryable"),
    [
        (
            GovernedWebSourceError("WEB_FETCH_TEMPORARILY_UNAVAILABLE", retryable=True),
            "WEB_FETCH_TEMPORARILY_UNAVAILABLE",
            True,
        ),
        (
            TavilyDiscoveryError("TAVILY_AUTHENTICATION_FAILED"),
            "TAVILY_AUTHENTICATION_FAILED",
            False,
        ),
        (
            TavilyDiscoveryError("TAVILY_QUOTA_EXHAUSTED", retryable=True),
            "TAVILY_QUOTA_EXHAUSTED",
            True,
        ),
        (
            httpx.ProxyError("proxy-detail-marker"),
            "RESEARCH_WEB_EGRESS_PROXY_REJECTED",
            False,
        ),
        (
            httpx.ReadTimeout("timeout-detail-marker"),
            "RESEARCH_WEB_TRANSPORT_ERROR",
            True,
        ),
        (
            ValueError("validation-detail-marker"),
            "RESEARCH_WEB_VALIDATION_FAILED",
            False,
        ),
    ],
)
def test_research_web_failure_log_contains_only_stable_safe_fields(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_code: str,
    retryable: bool,
) -> None:
    correlation_id = UUID("cfc0f485-92de-450a-a064-7b275d18b8ba")

    with caplog.at_level(logging.WARNING, logger="apps.worker.tasks"):
        tasks._log_research_web_fallback_failure(
            correlation_id=correlation_id,
            error=error,
        )

    rendered = caplog.records[-1].getMessage()
    assert "task_type=research_web_fallback" in rendered
    assert f"correlation_id={correlation_id}" in rendered
    assert f"failure_code={expected_code}" in rendered
    assert f"retryable={str(retryable).lower()}" in rendered
    assert "detail-marker" not in rendered
    assert "query=" not in rendered
    assert "url=" not in rendered


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("INSUFFICIENT_EVIDENCE", "证据不足"),
        ("NARRATIVE_SUPPORT_INSUFFICIENT", "尚未形成证据支持的完整多空论证"),
        ("MODEL_CONFIGURATION_INVALID", "授权配置"),
        ("MODEL_TRANSPORT_UNAVAILABLE", "执行暂时中断"),
        ("WEB_FETCH_TEMPORARILY_UNAVAILABLE", "执行暂时中断"),
        ("MODEL_OUTPUT_INVALID", "模型输出"),
        ("RESEARCH_INTERNAL_ERROR", "执行失败"),
    ],
)
def test_worker_failure_message_distinguishes_evidence_from_execution(
    code: str, expected: str
) -> None:
    assert expected in tasks._research_failure_message(code)
