from __future__ import annotations

import asyncio
import logging
from typing import cast

import pytest

from stock_assistant.modules.research.reporting import RESEARCH_MODEL_TIMEOUT_SECONDS
from stock_assistant.platform.network import connect_proxy
from stock_assistant.platform.network.connect_proxy import (
    CONNECT_TUNNEL_IDLE_TIMEOUT_SECONDS,
    ConnectProxyError,
    hostname_allowed,
    normalize_allowed_hosts,
    safe_address,
)


class _Writer:
    def __init__(self) -> None:
        self.output = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.output.extend(data)

    async def drain(self) -> None:
        pass

    def write_eof(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


@pytest.mark.parametrize("phase", ["resolve", "connect"])
@pytest.mark.parametrize("timed_out", [False, True])
async def test_proxy_transport_failure_is_not_permission_denial(
    phase: str, timed_out: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    failure = TimeoutError("private-marker") if timed_out else OSError("private-marker")

    async def resolver(_host: str) -> tuple[str, ...]:
        if phase == "resolve":
            raise failure
        return ("93.184.216.34",)

    async def connect(_host: str, _port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        raise failure

    monkeypatch.setattr(connect_proxy, "_resolve", resolver)
    monkeypatch.setattr(connect_proxy.asyncio, "open_connection", connect)
    reader = asyncio.StreamReader()
    reader.feed_data(b"CONNECT www.sse.com.cn:443 HTTP/1.1\r\nX-Private: private-marker\r\n\r\n")
    writer = _Writer()
    with caplog.at_level(logging.WARNING, logger=connect_proxy.__name__):
        await connect_proxy.handle_connect(
            reader, cast(asyncio.StreamWriter, writer), allowed_hosts=("www.sse.com.cn",)
        )
    expected = b"504 Gateway Timeout" if timed_out else b"502 Bad Gateway"
    assert bytes(writer.output).startswith(b"HTTP/1.1 " + expected + b"\r\n")
    assert writer.closed
    assert f"phase={phase}" in caplog.text
    assert "hostname=www.sse.com.cn" in caplog.text
    assert "private-marker" not in caplog.text


@pytest.mark.parametrize("forbidden_host", [False, True])
async def test_proxy_policy_denials_do_not_connect_or_log_unapproved_host(
    forbidden_host: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def resolver(_host: str) -> tuple[str, ...]:
        if forbidden_host:
            pytest.fail("unapproved host must not resolve")
        connect_proxy.safe_address("127.0.0.1")
        return ()

    monkeypatch.setattr(connect_proxy, "_resolve", resolver)
    reader = asyncio.StreamReader()
    hostname = "private-marker.invalid" if forbidden_host else "www.sse.com.cn"
    reader.feed_data(f"CONNECT {hostname}:443 HTTP/1.1\r\n\r\n".encode())
    writer = _Writer()
    with caplog.at_level(logging.WARNING, logger=connect_proxy.__name__):
        await connect_proxy.handle_connect(
            reader, cast(asyncio.StreamWriter, writer), allowed_hosts=("www.sse.com.cn",)
        )
    assert bytes(writer.output).startswith(b"HTTP/1.1 403 Forbidden\r\n")
    assert writer.closed
    assert "private-marker" not in caplog.text


async def test_proxy_cancellation_closes_without_fabricating_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def resolver(_host: str) -> tuple[str, ...]:
        entered.set()
        await asyncio.Event().wait()
        return ()

    monkeypatch.setattr(connect_proxy, "_resolve", resolver)
    reader = asyncio.StreamReader()
    reader.feed_data(b"CONNECT www.sse.com.cn:443 HTTP/1.1\r\n\r\n")
    writer = _Writer()
    task = asyncio.create_task(
        connect_proxy.handle_connect(
            reader, cast(asyncio.StreamWriter, writer), allowed_hosts=("www.sse.com.cn",)
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert writer.closed and not writer.output


async def test_established_tunnel_never_gets_a_second_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = _Writer()

    async def resolver(_host: str) -> tuple[str, ...]:
        return ("93.184.216.34",)

    async def connect(_host: str, _port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return asyncio.StreamReader(), cast(asyncio.StreamWriter, upstream)

    async def relay(_reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
        raise OSError("tunnel interrupted")

    monkeypatch.setattr(connect_proxy, "_resolve", resolver)
    monkeypatch.setattr(connect_proxy.asyncio, "open_connection", connect)
    monkeypatch.setattr(connect_proxy, "_relay", relay)
    reader = asyncio.StreamReader()
    reader.feed_data(b"CONNECT www.sse.com.cn:443 HTTP/1.1\r\n\r\n")
    writer = _Writer()
    await connect_proxy.handle_connect(
        reader, cast(asyncio.StreamWriter, writer), allowed_hosts=("www.sse.com.cn",)
    )
    assert bytes(writer.output) == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert writer.closed and upstream.closed


def test_tunnel_idle_budget_exceeds_research_model_deadline() -> None:
    assert CONNECT_TUNNEL_IDLE_TIMEOUT_SECONDS > RESEARCH_MODEL_TIMEOUT_SECONDS


def test_allowlist_matches_only_exact_hosts() -> None:
    rules = ("api.deepseek.com", "research-bucket.s3.ap-northeast-1.amazonaws.com")

    assert hostname_allowed("api.deepseek.com", rules)
    assert not hostname_allowed("evil-api.deepseek.com", rules)
    assert hostname_allowed("research-bucket.s3.ap-northeast-1.amazonaws.com", rules)
    assert not hostname_allowed("other-bucket.s3.ap-northeast-1.amazonaws.com", rules)
    assert not hostname_allowed("s3.ap-northeast-1.amazonaws.com.evil.example", rules)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_private_and_metadata_addresses_are_rejected(address: str) -> None:
    with pytest.raises(ConnectProxyError, match="EGRESS_PROXY_ADDRESS_BLOCKED"):
        safe_address(address)


def test_allowlist_requires_nonempty_normalized_entries() -> None:
    assert normalize_allowed_hosts("api.deepseek.com,api.tavily.com") == (
        "api.deepseek.com",
        "api.tavily.com",
    )
    with pytest.raises(ConnectProxyError, match="EGRESS_PROXY_ALLOWLIST_INVALID"):
        normalize_allowed_hosts("")
    with pytest.raises(ConnectProxyError, match="EGRESS_PROXY_ALLOWLIST_INVALID"):
        normalize_allowed_hosts("*.s3.ap-northeast-1.amazonaws.com")
