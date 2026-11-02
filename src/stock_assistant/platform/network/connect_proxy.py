"""Small explicit CONNECT proxy with an exact hostname allowlist."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Sequence
from time import monotonic
from typing import Final

_MAX_HEADER_BYTES: Final = 16 * 1024
_CONNECT_TIMEOUT_SECONDS: Final = 15.0
_LOGGER = logging.getLogger(__name__)
# Keep the tunnel open longer than the longest approved provider request budget.
# This remains bounded while preventing the proxy from truncating a valid model
# response before the client-side timeout can enforce the request deadline.
CONNECT_TUNNEL_IDLE_TIMEOUT_SECONDS: Final = 135.0


class ConnectProxyError(RuntimeError):
    """Safe proxy rejection without echoing request content."""


def normalize_allowed_hosts(raw: str) -> tuple[str, ...]:
    hosts = tuple(sorted({item.strip().casefold() for item in raw.split(",") if item.strip()}))
    if not hosts or any(
        "/" in item or ":" in item or "*" in item or item.startswith(".") for item in hosts
    ):
        raise ConnectProxyError("EGRESS_PROXY_ALLOWLIST_INVALID")
    return hosts


def hostname_allowed(hostname: str, allowed_hosts: Sequence[str]) -> bool:
    normalized = hostname.casefold().rstrip(".")
    return normalized in allowed_hosts


def safe_address(address: str) -> str:
    parsed = ipaddress.ip_address(address)
    if not parsed.is_global or any(
        (
            parsed.is_private,
            parsed.is_loopback,
            parsed.is_link_local,
            parsed.is_multicast,
            parsed.is_reserved,
            parsed.is_unspecified,
        )
    ):
        raise ConnectProxyError("EGRESS_PROXY_ADDRESS_BLOCKED")
    return parsed.compressed


async def _resolve(hostname: str) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, 443, type=0, proto=0)
    return tuple(sorted({safe_address(record[4][0]) for record in records}))


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await asyncio.wait_for(
            reader.read(64 * 1024), CONNECT_TUNNEL_IDLE_TIMEOUT_SECONDS
        ):
            writer.write(chunk)
            await writer.drain()
    except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        return
    finally:
        try:
            writer.write_eof()
        except (OSError, RuntimeError):
            pass


async def handle_connect(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    allowed_hosts: Sequence[str],
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    phase = "read_headers"
    allowed_hostname = "unapproved"
    started = monotonic()
    tunnel_established = False
    try:
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
        if len(header) > _MAX_HEADER_BYTES:
            raise ConnectProxyError("EGRESS_PROXY_HEADERS_TOO_LARGE")
        first_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="strict")
        parts = first_line.split(" ")
        if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] != "HTTP/1.1":
            raise ConnectProxyError("EGRESS_PROXY_CONNECT_REQUIRED")
        authority = parts[1]
        if authority.count(":") != 1:
            raise ConnectProxyError("EGRESS_PROXY_AUTHORITY_INVALID")
        hostname, port = authority.rsplit(":", 1)
        phase = "check_destination"
        if port != "443" or not hostname_allowed(hostname, allowed_hosts):
            raise ConnectProxyError("EGRESS_PROXY_DESTINATION_BLOCKED")
        allowed_hostname = hostname.casefold().rstrip(".")
        phase = "resolve"
        addresses = await asyncio.wait_for(_resolve(hostname), _CONNECT_TIMEOUT_SECONDS)
        if not addresses:
            raise ConnectProxyError("EGRESS_PROXY_DNS_EMPTY")
        phase = "connect"
        upstream_reader, connected_writer = await asyncio.wait_for(
            asyncio.open_connection(addresses[0], 443),
            _CONNECT_TIMEOUT_SECONDS,
        )
        upstream_writer = connected_writer
        tunnel_established = True
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        phase = "relay"
        await asyncio.gather(_relay(reader, connected_writer), _relay(upstream_reader, writer))
    except (
        ConnectProxyError,
        UnicodeError,
        OSError,
        TimeoutError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
    ) as error:
        response_status = b"403 Forbidden"
        if phase in {"resolve", "connect"}:
            if isinstance(error, TimeoutError):
                response_status = b"504 Gateway Timeout"
            elif isinstance(error, OSError):
                response_status = b"502 Bad Gateway"
        # Log only validated host and stable phase/type; never request headers,
        # destination addresses, tunneled data or exception text.
        _LOGGER.warning(
            "research_egress_failure phase=%s hostname=%s failure_kind=%s elapsed_ms=%s",
            phase,
            allowed_hostname,
            type(error).__name__,
            round((monotonic() - started) * 1000),
        )
        if not tunnel_established:
            writer.write(b"HTTP/1.1 " + response_status + b"\r\nContent-Length: 0\r\n\r\n")
            try:
                await writer.drain()
            except ConnectionError:
                pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def serve(allowed_hosts: Sequence[str]) -> None:
    if not allowed_hosts:
        raise ConnectProxyError("EGRESS_PROXY_ALLOWLIST_INVALID")
    server = await asyncio.start_server(
        lambda reader, writer: handle_connect(reader, writer, allowed_hosts=allowed_hosts),
        host="0.0.0.0",  # noqa: S104 - isolated container listener.
        port=3128,
        limit=_MAX_HEADER_BYTES,
    )
    async with server:
        await server.serve_forever()


__all__ = [
    "CONNECT_TUNNEL_IDLE_TIMEOUT_SECONDS",
    "ConnectProxyError",
    "handle_connect",
    "hostname_allowed",
    "normalize_allowed_hosts",
    "safe_address",
    "serve",
]
