"""Isolated child process for fixed BaoStock and official-page endpoints."""

from __future__ import annotations

import importlib
import json
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
from http.client import HTTPMessage
from typing import IO, Final, Protocol, cast
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from pydantic import JsonValue

from stock_assistant.platform.sources.akshare_worker import _run as _run_akshare
from stock_assistant.platform.sources.free_source_runner import validate_free_source_arguments

_OFFICIAL_URLS: Final = {
    "official_sse_disclosures": (
        "https://www.sse.com.cn/assortment/stock/list/info/announcement/"
        "index.shtml?productId=603986"
    ),
    "official_sse_reports": (
        "https://www.sse.com.cn/assortment/stock/list/info/announcement/"
        "index.shtml?productId=603986"
    ),
    "official_gov_policy": "https://sousuo.www.gov.cn/zcwjk/",
    "official_miit_policy": "https://www.miit.gov.cn/zwgk/index.html",
}
_APPROVED_CANONICAL_HOSTS: Final = {
    "official_gov_policy": frozenset({"sousuo.www.gov.cn", "www.gov.cn"}),
    "official_miit_policy": frozenset({"www.miit.gov.cn"}),
    "official_sse_disclosures": frozenset({"www.sse.com.cn"}),
    "official_sse_reports": frozenset({"www.sse.com.cn"}),
}
_APPROVED_REQUEST_HOSTS: Final = {
    endpoint: frozenset({urlsplit(url).hostname}) for endpoint, url in _OFFICIAL_URLS.items()
}
_OFFICIAL_STRUCTURE_TOKENS: Final = {
    "official_gov_policy": frozenset({"zcwjk-list", "policy-result-list", "policy-search-results"}),
    "official_miit_policy": frozenset(
        {"zwgk-list", "policy-file-list", "government-information-list"}
    ),
    "official_sse_disclosures": frozenset(
        {"announcement-list", "disclosure-list", "sse-announcement-list"}
    ),
    "official_sse_reports": frozenset(
        {"announcement-list", "disclosure-list", "sse-announcement-list"}
    ),
}
_OFFICIAL_METADATA_DEFAULTS: Final = {
    "official_gov_policy": ("国务院办公厅", "政策文件"),
    "official_miit_policy": ("工业和信息化部", "政务公开"),
    "official_sse_disclosures": ("上海证券交易所", "公告核验"),
    "official_sse_reports": ("上海证券交易所", "公告核验"),
}
_CONTAINER_TAGS: Final = frozenset(
    {"article", "dd", "div", "li", "main", "section", "table", "tbody", "tr", "ul"}
)
_USER_AGENT: Final = "StockMarketAgent-R1-metadata/1.0 (personal-research)"
_REQUEST_TIMEOUT_SECONDS: Final = 10.0
_MIN_DOMAIN_INTERVAL_SECONDS: Final = 5.0
_ROBOTS_MAX_BYTES: Final = 256 * 1024
_RISK_MARKERS: Final = (
    "captcha",
    "robot check",
    "安全验证",
    "访问过于频繁",
    "请输入验证码",
)
_DATE_PATTERN: Final = re.compile(
    r"(?P<year>20\d{2})\s*[-./年]\s*(?P<month>\d{1,2})\s*[-./月]\s*"
    r"(?P<day>\d{1,2})(?:\s*日)?"
    r"(?:[ T]+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)
_FREE_AKSHARE_ENDPOINTS: Final = frozenset(
    {
        "stock_zh_a_disclosure_report_cninfo",
        "stock_financial_analysis_indicator_em",
        "stock_zh_a_hist",
        "stock_news_em",
    }
)
_AKSHARE_TO_FREE_ERROR: Final = {
    "AKSHARE_CAPTCHA_REQUIRED": "FREE_SOURCE_FORBIDDEN",
    "AKSHARE_FORBIDDEN": "FREE_SOURCE_FORBIDDEN",
    "AKSHARE_HTML_ERROR_PAGE": "FREE_SOURCE_SCHEMA_DRIFT",
    "AKSHARE_RATE_LIMITED": "FREE_SOURCE_RATE_LIMITED",
    "AKSHARE_RESPONSE_TOO_LARGE": "FREE_SOURCE_RESPONSE_TOO_LARGE",
    "AKSHARE_ROW_LIMIT_EXCEEDED": "FREE_SOURCE_ROW_LIMIT_EXCEEDED",
    "AKSHARE_SCHEMA_DRIFT": "FREE_SOURCE_SCHEMA_DRIFT",
    "AKSHARE_TIMEOUT": "FREE_SOURCE_TIMEOUT",
}


class _BaoStockStatus(Protocol):
    @property
    def error_code(self) -> str: ...


class _BaoStockQueryResult(_BaoStockStatus, Protocol):
    @property
    def fields(self) -> Sequence[str]: ...

    def next(self) -> bool: ...

    def get_row_data(self) -> Sequence[str]: ...


class _BaoStockSdk(Protocol):
    def login(self) -> _BaoStockStatus: ...

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        *,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> _BaoStockQueryResult: ...

    def logout(self) -> _BaoStockStatus | None: ...


@dataclass(slots=True)
class _ContainerCapture:
    tag: str
    attributes: dict[str, str]
    approved_structure: bool
    text: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _AnchorCapture:
    href: str
    attributes: dict[str, str]
    container: _ContainerCapture | None
    text: list[str] = field(default_factory=list)


class _OfficialMetadataParser(HTMLParser):
    """Extract bounded publication metadata without retaining source HTML."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(convert_charrefs=True)
        self._structure_tokens = _OFFICIAL_STRUCTURE_TOKENS[endpoint]
        self.containers: list[_ContainerCapture] = []
        self.anchors: list[_AnchorCapture] = []
        self.structure_seen = False
        self._active_anchor: _AnchorCapture | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        lowered = tag.lower()
        if lowered in _CONTAINER_TAGS:
            attribute_tokens = {
                token.lower()
                for attribute_name in ("class", "id")
                for token in attributes.get(attribute_name, "").split()
            }
            structure_match = bool(attribute_tokens & self._structure_tokens)
            approved_structure = structure_match or (
                bool(self.containers) and self.containers[-1].approved_structure
            )
            self.structure_seen = self.structure_seen or structure_match
            self.containers.append(
                _ContainerCapture(lowered, attributes, approved_structure=approved_structure)
            )
        if lowered == "a" and self._active_anchor is None:
            href = attributes.get("href")
            if href and self.containers and self.containers[-1].approved_structure:
                self._active_anchor = _AnchorCapture(
                    href=href,
                    attributes=attributes,
                    container=self.containers[-1] if self.containers else None,
                )

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        for container in self.containers:
            container.text.append(text)
        if self._active_anchor is not None:
            self._active_anchor.text.append(text)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a" and self._active_anchor is not None:
            self.anchors.append(self._active_anchor)
            self._active_anchor = None
        if lowered in _CONTAINER_TAGS:
            for index in range(len(self.containers) - 1, -1, -1):
                if self.containers[index].tag == lowered:
                    del self.containers[index:]
                    break


class _DomainRequestLimiter:
    """Space each domain request, including redirects, by a fixed minimum interval."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last_request_started: dict[str, float] = {}

    def before_request(self, url: str) -> None:
        host = urlsplit(url).hostname
        if host is None:
            raise RuntimeError("FREE_SOURCE_FORBIDDEN")
        now = self._clock()
        previous = self._last_request_started.get(host)
        if previous is not None:
            delay = _MIN_DOMAIN_INTERVAL_SECONDS - (now - previous)
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
        self._last_request_started[host] = now


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    """Reject a redirect target before urllib can open a connection to it."""

    def __init__(self, endpoint: str, limiter: _DomainRequestLimiter) -> None:
        super().__init__()
        self._endpoint = endpoint
        self._limiter = limiter

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        target = urljoin(req.full_url, newurl)
        _validate_official_request_url(self._endpoint, target)
        self._limiter.before_request(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _error(reason_code: str) -> None:
    print(json.dumps({"ok": False, "error_code": reason_code}, separators=(",", ":")))


def _response_header(response: object, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    return value if isinstance(value, str) and len(value) <= 500 else None


@dataclass(frozen=True, slots=True)
class _OfficialResource:
    status: int
    body: bytes
    final_url: str
    content_type: str | None
    etag: str | None
    last_modified: str | None


def _validate_official_request_url(endpoint: str, url: str) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError("FREE_SOURCE_FORBIDDEN") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _APPROVED_REQUEST_HOSTS[endpoint]
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise RuntimeError("FREE_SOURCE_FORBIDDEN")


def _read_official_resource(
    endpoint: str,
    url: str,
    *,
    headers: Mapping[str, str],
    max_bytes: int,
    opener: OpenerDirector,
    limiter: _DomainRequestLimiter,
) -> _OfficialResource:
    _validate_official_request_url(endpoint, url)
    limiter.before_request(url)
    request = Request(url, headers=dict(headers))  # noqa: S310 - fixed HTTPS allowlist
    try:
        with opener.open(
            request,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            status = response.status
            final_url = response.geturl()
            _validate_official_request_url(endpoint, final_url)
            body = response.read(max_bytes + 1)
            content_type = _response_header(response, "Content-Type")
            etag = _response_header(response, "ETag")
            last_modified = _response_header(response, "Last-Modified")
    except HTTPError:
        raise
    except (TimeoutError, URLError) as error:
        raise RuntimeError("FREE_SOURCE_UNAVAILABLE") from error
    if status != 200:
        raise RuntimeError("FREE_SOURCE_UNAVAILABLE")
    if len(body) > max_bytes:
        raise RuntimeError("FREE_SOURCE_RESPONSE_TOO_LARGE")
    return _OfficialResource(
        status=status,
        body=body,
        final_url=final_url,
        content_type=content_type,
        etag=etag,
        last_modified=last_modified,
    )


def _check_official_robots(
    endpoint: str,
    *,
    opener: OpenerDirector,
    limiter: _DomainRequestLimiter,
    max_bytes: int,
) -> None:
    page_url = _OFFICIAL_URLS[endpoint]
    parsed_page = urlsplit(page_url)
    if parsed_page.hostname is None:
        raise RuntimeError("FREE_SOURCE_FORBIDDEN")
    robots_url = urlunsplit(("https", parsed_page.hostname, "/robots.txt", "", ""))
    try:
        resource = _read_official_resource(
            endpoint,
            robots_url,
            headers={
                "Accept": "text/plain",
                "Accept-Encoding": "identity",
                "User-Agent": _USER_AGENT,
            },
            max_bytes=min(max_bytes, _ROBOTS_MAX_BYTES),
            opener=opener,
            limiter=limiter,
        )
    except HTTPError as error:
        if error.code == 403:
            raise RuntimeError("FREE_SOURCE_FORBIDDEN") from error
        if error.code == 429:
            raise RuntimeError("FREE_SOURCE_RATE_LIMITED") from error
        raise RuntimeError("FREE_SOURCE_UNAVAILABLE") from error
    if resource.content_type is not None and "text/plain" not in resource.content_type.lower():
        raise RuntimeError("FREE_SOURCE_SCHEMA_DRIFT")
    robots_text = resource.body.decode("utf-8", errors="replace")
    if any(marker in robots_text.lower() for marker in _RISK_MARKERS):
        raise RuntimeError("FREE_SOURCE_FORBIDDEN")
    policy = robotparser.RobotFileParser()
    policy.set_url(robots_url)
    policy.parse(robots_text.splitlines())
    if not policy.can_fetch(_USER_AGENT, page_url):
        raise RuntimeError("FREE_SOURCE_FORBIDDEN")


def _publication_datetime(candidate: _AnchorCapture) -> str | None:
    values = [*candidate.attributes.values()]
    if candidate.container is not None:
        values.extend(candidate.container.attributes.values())
        values.extend(candidate.container.text)
    match = _DATE_PATTERN.search(" ".join(values))
    if match is None:
        return None
    second = match.group("second") or "00"
    if match.group("hour") is None:
        return (
            f"{match.group('year')}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
        )
    return (
        f"{match.group('year')}-{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d} {int(match.group('hour')):02d}:"
        f"{match.group('minute')}:{second}"
    )


def _official_metadata_rows(
    endpoint: str,
    *,
    body: bytes,
    base_url: str,
    max_rows: int,
) -> list[dict[str, JsonValue]]:
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError:
        content = body.decode("gb18030")
    lowered = content.lower()
    if any(marker in lowered for marker in _RISK_MARKERS):
        raise RuntimeError("FREE_SOURCE_FORBIDDEN")
    if "<html" not in lowered:
        raise RuntimeError("FREE_SOURCE_SCHEMA_DRIFT")
    parser = _OfficialMetadataParser(endpoint)
    parser.feed(content)
    parser.close()
    if not parser.structure_seen:
        raise RuntimeError("FREE_SOURCE_SCHEMA_DRIFT")
    publisher_default, category_default = _OFFICIAL_METADATA_DEFAULTS[endpoint]
    rows: dict[str, dict[str, JsonValue]] = {}
    for candidate in parser.anchors:
        title = " ".join(candidate.text).strip() or candidate.attributes.get("title", "").strip()
        published_at = _publication_datetime(candidate)
        if len(title) < 2 or published_at is None:
            continue
        canonical_url = urljoin(base_url, candidate.href)
        parsed_url = urlsplit(canonical_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in _APPROVED_CANONICAL_HOSTS[endpoint]
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            continue
        container_attributes = (
            candidate.container.attributes if candidate.container is not None else {}
        )
        publisher = candidate.attributes.get(
            "data-publisher",
            container_attributes.get("data-publisher", publisher_default),
        ).strip()
        category = candidate.attributes.get(
            "data-category",
            container_attributes.get("data-category", category_default),
        ).strip()
        row: dict[str, JsonValue] = {
            "title": title[:500],
            "published_at": published_at,
            "publisher": publisher[:200] or publisher_default,
            "category": category[:200] or category_default,
            "url": canonical_url[:2048],
        }
        identity = sha256(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        rows[identity] = row
        if len(rows) >= max_rows:
            break
    return [rows[key] for key in sorted(rows)]


def _official_page(
    endpoint: str,
    arguments: Mapping[str, JsonValue],
    *,
    max_rows: int,
    max_bytes: int,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, JsonValue]], dict[str, JsonValue]]:
    limiter = _DomainRequestLimiter(clock=clock, sleep=sleep)
    opener = build_opener(_AllowlistedRedirectHandler(endpoint, limiter))
    _check_official_robots(
        endpoint,
        opener=opener,
        limiter=limiter,
        max_bytes=max_bytes,
    )
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
        "User-Agent": _USER_AGENT,
    }
    if_none_match = arguments.get("if_none_match")
    if_modified_since = arguments.get("if_modified_since")
    if isinstance(if_none_match, str):
        headers["If-None-Match"] = if_none_match
    if isinstance(if_modified_since, str):
        headers["If-Modified-Since"] = if_modified_since
    try:
        resource = _read_official_resource(
            endpoint,
            _OFFICIAL_URLS[endpoint],
            headers=headers,
            max_bytes=max_bytes,
            opener=opener,
            limiter=limiter,
        )
    except HTTPError as error:
        if error.code == 304:
            return [], {
                "etag": _response_header(error, "ETag") or if_none_match,
                "last_modified": _response_header(error, "Last-Modified") or if_modified_since,
                "not_modified": True,
            }
        if error.code == 403:
            raise RuntimeError("FREE_SOURCE_FORBIDDEN") from error
        if error.code == 429:
            raise RuntimeError("FREE_SOURCE_RATE_LIMITED") from error
        raise RuntimeError("FREE_SOURCE_UNAVAILABLE") from error
    if resource.content_type is not None and "text/html" not in resource.content_type.lower():
        raise RuntimeError("FREE_SOURCE_SCHEMA_DRIFT")
    return (
        _official_metadata_rows(
            endpoint,
            body=resource.body,
            base_url=resource.final_url,
            max_rows=max_rows,
        ),
        {
            "etag": resource.etag,
            "last_modified": resource.last_modified,
            "not_modified": False,
        },
    )


def _baostock(arguments: Mapping[str, JsonValue], *, max_rows: int) -> list[dict[str, JsonValue]]:
    sdk = cast(_BaoStockSdk, importlib.import_module("baostock"))

    login = sdk.login()
    if str(login.error_code) != "0":
        raise RuntimeError("FREE_SOURCE_UNAVAILABLE")
    try:
        result = sdk.query_history_k_data_plus(
            str(arguments["code"]),
            str(arguments["fields"]),
            start_date=str(arguments["start_date"]),
            end_date=str(arguments["end_date"]),
            frequency="d",
            adjustflag="3",
        )
        if str(result.error_code) != "0":
            raise RuntimeError("FREE_SOURCE_UNAVAILABLE")
        rows: list[dict[str, JsonValue]] = []
        fields = list(result.fields)
        while result.next():
            values = result.get_row_data()
            row: dict[str, JsonValue] = dict(zip(fields, values, strict=True))
            if str(row.get("code", "")).strip().lower() != "sh.603986":
                raise RuntimeError("FREE_SOURCE_TARGET_LOCKED")
            rows.append(row)
            if len(rows) > max_rows:
                raise RuntimeError("FREE_SOURCE_ROW_LIMIT_EXCEEDED")
        return rows
    finally:
        sdk.logout()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        endpoint = payload["endpoint"]
        arguments = cast(dict[str, JsonValue], payload["arguments"])
        max_rows = int(payload["max_rows"])
        max_bytes = int(payload["max_bytes"])
        validate_free_source_arguments(endpoint, arguments)
        if endpoint == "baostock_history":
            rows = _baostock(arguments, max_rows=max_rows)
        elif endpoint in _FREE_AKSHARE_ENDPOINTS:
            akshare_response = _run_akshare(
                {
                    "function": endpoint,
                    "arguments": arguments,
                    "max_rows": max_rows,
                    "max_bytes": max_bytes,
                }
            )
            if akshare_response.get("ok") is not True:
                reason = str(akshare_response.get("error_code"))
                raise RuntimeError(_AKSHARE_TO_FREE_ERROR.get(reason, "FREE_SOURCE_UNAVAILABLE"))
            rows = cast(list[dict[str, JsonValue]], akshare_response.get("rows"))
        elif endpoint in _OFFICIAL_URLS:
            rows, response_metadata = _official_page(
                endpoint,
                arguments,
                max_rows=max_rows,
                max_bytes=max_bytes,
            )
        else:
            _error("FREE_SOURCE_ENDPOINT_NOT_ALLOWED")
            return 2
        response: dict[str, object] = {"ok": True, "rows": rows}
        if endpoint in _OFFICIAL_URLS:
            response["response"] = response_metadata
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > max_bytes:
            raise RuntimeError("FREE_SOURCE_RESPONSE_TOO_LARGE")
        print(encoded)
        return 0
    except RuntimeError as error:
        reason = str(error)
        _error(reason if reason.startswith("FREE_SOURCE_") else "FREE_SOURCE_UPSTREAM_ERROR")
        return 2
    except (KeyError, TypeError, ValueError):
        _error("FREE_SOURCE_SCHEMA_DRIFT")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
