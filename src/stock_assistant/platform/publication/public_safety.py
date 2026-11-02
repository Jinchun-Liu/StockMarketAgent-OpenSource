"""Fail-closed public JSON scanner for development-only snapshot assets."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final
from urllib.parse import urlsplit

from stock_assistant.common.public_secrets import contains_secret_pattern

_OFFICIAL_HOSTS: Final = frozenset(
    {
        "sousuo.www.gov.cn",
        "static.cninfo.com.cn",
        "www.gov.cn",
        "www.miit.gov.cn",
    }
)
_CNINFO_ATTACHMENT_HOST: Final = "static.cninfo.com.cn"
_REGISTERED_RESEARCH_URLS: Final = frozenset(
    {
        "https://my.feishu.cn/wiki/T7XWwCxtIiOcLIkkXQbc5I1Tntc",
        "https://quantdata888.duckdns.org",
        "https://quantdata888.duckdns.org/",
        "https://ts.gyzcloud.top/docs",
        "https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71",
    }
)
_FORBIDDEN_KEYS: Final = frozenset(
    {
        "authorization",
        "body",
        "cursor",
        "full_text",
        "fulltext",
        "html",
        "password",
        "provider_cursor",
        "raw_html",
        "secret",
        "stack_trace",
        "token",
        "traceback",
    }
)
_FORBIDDEN_TEXT: Final = (
    "/Users/",
    "/home/",
    "/private/",
    "127.0.0.1",
    "localhost",
    ".env",
    "Traceback (most recent call last)",
    "<html",
    "<!doctype html",
)


class PublicSafetyError(ValueError):
    """A candidate public document contains internal or unapproved data."""


def scan_public_json(document: bytes, *, label: str) -> None:
    """Validate one bounded UTF-8 JSON document without retaining its payload."""

    if len(document) > 10 * 1024 * 1024:
        raise PublicSafetyError(f"{label} exceeds the public byte limit")
    try:
        value = json.loads(document)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicSafetyError(f"{label} is not one UTF-8 JSON document") from error
    _scan(value, path=label)


def _scan(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise PublicSafetyError(f"{path} contains a non-string key")
            normalized = raw_key.casefold().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise PublicSafetyError(f"{path} contains forbidden field {raw_key}")
            _scan(child, path=f"{path}.{raw_key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _scan(child, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        return
    lowered = value.casefold()
    if any(fragment.casefold() in lowered for fragment in _FORBIDDEN_TEXT):
        raise PublicSafetyError(f"{path} contains non-public text")
    if contains_secret_pattern(value):
        raise PublicSafetyError(f"{path} contains secret-like text")
    if "://" not in value:
        return
    parsed = urlsplit(value)
    if value in _REGISTERED_RESEARCH_URLS:
        return
    if parsed.scheme != "https" or parsed.hostname not in _OFFICIAL_HOSTS:
        raise PublicSafetyError(f"{path} contains a non-allowlisted URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise PublicSafetyError(f"{path} contains an unsafe URL")
    if parsed.hostname == _CNINFO_ATTACHMENT_HOST and (
        parsed.netloc != _CNINFO_ATTACHMENT_HOST
        or parsed.query
        or not _is_canonical_cninfo_attachment_path(parsed.path)
    ):
        raise PublicSafetyError(f"{path} contains an unsafe CNINFO attachment URL")


def _is_canonical_cninfo_attachment_path(path: str) -> bool:
    if "%" in path or "\\" in path:
        return False
    segments = path.split("/")
    return (
        len(segments) >= 3
        and segments[:2] == ["", "finalpage"]
        and all(segment not in {"", ".", ".."} for segment in segments[2:])
        and segments[-1].casefold().endswith(".pdf")
    )


__all__ = ["PublicSafetyError", "scan_public_json"]
