"""Fail-closed public-page connector for model-assisted R2 evidence completion."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from typing import Final, Literal, cast
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import httpx

from stock_assistant.contracts.discovery import (
    RESEARCH_WEB_SOURCE_CAPABILITY,
    RESEARCH_WEB_SOURCE_ID,
    RobotsOriginStop,
)
from stock_assistant.contracts.formal_disclosure import (
    CandidateVerificationOutcome,
    DirectAttachmentCandidate,
    FormalDocumentVerification,
    OfficialDocumentIndexBinding,
)
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    FetchBatch,
    FetchRequest,
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
    SourceAccessChannelV1_2,
    SourceCapability,
    SourceDescriptor,
    SourceEvidenceRole,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceRecordV1_2,
    SourceReleaseTier,
    SourceRightsStatusV1_2,
    SourceRuntimeModeV1_2,
    SourceTier,
)
from stock_assistant.platform.sources.formal_document import (
    formal_identity,
    formal_publication,
)
from stock_assistant.platform.sources.formal_document import (
    publication_date as _publication_date,
)
from stock_assistant.ports.ingestion import GovernedWebSourceError as GovernedWebSourceError

GOVERNED_WEB_SOURCE_ID: Final = RESEARCH_WEB_SOURCE_ID
GOVERNED_WEB_ADAPTER_VERSION: Final = "governed-web-source.v1.3"
GOVERNED_WEB_CAPABILITY: Final = RESEARCH_WEB_SOURCE_CAPABILITY
_USER_AGENT: Final = "StockMarketAgentResearch/1.0"
_MAX_BODY_BYTES: Final = 12 * 1024 * 1024
_MAX_TEXT_CHARS: Final = 100_000
_LOGGER = logging.getLogger(__name__)

AddressResolver = Callable[[str], Awaitable[Sequence[str]]]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _pdf_text(content: bytes) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("pdf_text.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={},
    )
    try:
        async with asyncio.timeout(12):
            output, _ = await process.communicate(content)
        if process.returncode != 0 or len(output) > 4 * (_MAX_TEXT_CHARS + 100):
            raise GovernedWebSourceError("WEB_PDF_TEXT_UNAVAILABLE")
        return output.decode("utf-8")
    except TimeoutError as error:
        raise GovernedWebSourceError("WEB_PDF_EXTRACTION_TIMEOUT") from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class DevelopmentResearchWebRightsPolicy:
    """R1-R6 allowlist profile; it is not formal production authorization."""

    def __init__(self, allowed_snapshots: dict[str, str]) -> None:
        self._allowed_snapshots = dict(allowed_snapshots)

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        allowed = (
            self._allowed_snapshots.get(check.source_id) == check.rights_snapshot_hash
            and check.operation in {RightsOperation.FETCH, RightsOperation.RETAIN}
            and check.permitted_use is RightsPermittedUse.AI_USE
        )
        return RightsDecision(
            allowed=allowed,
            reason_code="R1_R6_WEB_RESEARCH_ALLOWED" if allowed else "WEB_RIGHTS_BLOCKED",
            decided_at=check.context.requested_at,
            rights_snapshot_hash=check.rights_snapshot_hash,
            retention_until=(check.context.requested_at + timedelta(days=7) if allowed else None),
        )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []
        self.publication_values: list[tuple[str, str]] = []
        self._jsonld: str | None = None
        self.attachment_hrefs: list[tuple[int, str]] = []
        self.hrefs_seen = 0
        self.attachment_capacity_exceeded = False
        self.article_count = 0
        self.heading_titles: list[str] = []
        self._heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.casefold() == "article":
            self.article_count += 1
        if tag.casefold() == "h1":
            self._heading = ""
        href = values.get("href")
        if tag.casefold() == "a" and self._ignored_depth == 0 and href:
            ordinal = self.hrefs_seen
            self.hrefs_seen = min(13, self.hrefs_seen + 1)
            if len(href) > 2048 or ordinal >= 12:
                self.attachment_capacity_exceeded = True
            else:
                try:
                    parsed_href = urlsplit(href)
                    if parsed_href.path.casefold().endswith(".pdf"):
                        self.attachment_hrefs.append((ordinal, href))
                except ValueError:
                    # A malformed individual link cannot hide other actual hrefs.
                    pass
        if tag.casefold() == "script" and values.get("type") == "application/ld+json":
            self._jsonld = ""
        name = (values.get("property") or values.get("name") or "").casefold()
        if (
            tag.casefold() == "meta"
            and name
            in {
                "article:published_time",
                "datepublished",
                "pubdate",
                "publishdate",
                "publication_date",
            }
            and values.get("content")
        ):
            self.publication_values.append((str(values["content"]), f"html_meta:{name}"))
        if (
            tag.casefold() == "time"
            and values.get("itemprop") == "datePublished"
            and values.get("datetime")
        ):
            self.publication_values.append((str(values["datetime"]), "html_time:datePublished"))
        if tag.casefold() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "h1" and self._heading is not None:
            if len(self.heading_titles) < 2:
                self.heading_titles.append(self._heading.strip())
            self._heading = None
        if tag.casefold() == "script" and self._jsonld is not None:
            try:
                document = json.loads(self._jsonld)
            except (json.JSONDecodeError, RecursionError):
                document = None
            nodes = document if isinstance(document, list) else [document]
            for node in nodes[:20]:
                if isinstance(node, dict):
                    value = node.get("datePublished")
                    if isinstance(value, str):
                        self.publication_values.append((value, "jsonld:datePublished"))
            self._jsonld = None
        if tag.casefold() in {"script", "style", "noscript", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._jsonld is not None:
            self._jsonld = (self._jsonld + data)[:40000]
        if self._ignored_depth == 0 and data.strip():
            if self._heading is not None:
                self._heading = (self._heading + data)[:513]
            self.parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self.parts)[:_MAX_TEXT_CHARS]


async def _default_resolver(hostname: str) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, 443, type=0, proto=0)
    return tuple(sorted({record[4][0] for record in records}))


def _safe_addresses(values: Sequence[str]) -> tuple[str, ...]:
    if not values:
        raise GovernedWebSourceError("WEB_DNS_EMPTY")
    addresses: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise GovernedWebSourceError("WEB_DNS_INVALID") from exc
        if not address.is_global or any(
            (
                address.is_private,
                address.is_loopback,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
                address.is_unspecified,
            )
        ):
            raise GovernedWebSourceError("WEB_PRIVATE_ADDRESS_BLOCKED")
        addresses.append(address.compressed)
    return tuple(sorted(set(addresses)))


def _string_option(request: FetchRequest, key: str) -> str:
    value = request.options.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GovernedWebSourceError("WEB_FETCH_OPTIONS_INVALID")
    return value


def _resolve_publication(
    *,
    url: str,
    text: str,
    metadata: list[tuple[str, str]],
    hint: datetime | None,
    cutoff: datetime,
) -> tuple[datetime, str]:
    parsed_url = urlsplit(url)
    host = parsed_url.hostname or ""
    chinese = host.endswith(".cn") or host in {
        "www.stcn.com",
        "www.cnstock.com",
        "www.zmax-opt.com",
        "www.catl.com",
    }
    observations = list(metadata)
    if host == "static.cninfo.com.cn":
        match = re.fullmatch(r"/finalpage/(\d{4}-\d{2}-\d{2})/[^/]+\.PDF", parsed_url.path, re.I)
        if match:
            observations.append((match[1], "cninfo_announcement_path"))
    for match in re.finditer(
        r"(?:发布日期|发布时间|公告日期)\s*[:\uFF1A]?\s*"
        r"(\d{4}[-年]\d{1,2}[-月]\d{1,2}日?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)",
        text,
    ):
        observations.append(
            (match[1], f"publication_label:characters {match.start()}:{match.end()}")
        )
    if len(observations) > 20:
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_CONFLICT")
    dated = [
        (*_publication_date(value, chinese_source=chinese), basis) for value, basis in observations
    ]
    if not dated:
        if hint is None:
            raise GovernedWebSourceError("WEB_PUBLICATION_TIME_UNKNOWN")
        dated = [(hint, False, "search_metadata")]
    timezone = ZoneInfo("Asia/Shanghai") if chinese else UTC
    days = {stamp.astimezone(timezone).date() for stamp, _, _ in dated}
    exact = {stamp for stamp, day_only, _ in dated if not day_only}
    if (
        len(days) != 1
        or len(exact) > 1
        or (hint is not None and hint.astimezone(timezone).date() not in days)
    ):
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_CONFLICT")
    published = next(iter(exact)) if exact else max(stamp for stamp, _, _ in dated)
    if published > cutoff:
        raise GovernedWebSourceError("WEB_PUBLICATION_TIME_INVALID")
    return published, "; ".join(dict.fromkeys(basis for _, _, basis in dated))[:1000]


class GovernedWebSourceConnector:
    """Fetch one pre-authorized page without login, cookies, redirects, or raw HTML output."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        allowed_domains: set[str],
        resolver: AddressResolver = _default_resolver,
        clock: Clock = _utc_now,
    ) -> None:
        self._client = client
        self._allowed_domains = {value.casefold() for value in allowed_domains}
        self._resolver = resolver
        self._clock = clock

    async def _bounded_request(
        self,
        url: str,
        *,
        limit: int,
        timeout_seconds: float,
        accept: str,
        phase: Literal["robots", "document"],
        correlation_id: UUID,
        form: dict[str, str] | None = None,
    ) -> httpx.Response:
        started = monotonic()
        transport_phase = "request"
        tls_state = "not_observed"
        connect_request_observed = False
        proxy_status = 0
        status = 0
        body = bytearray()
        failure_kind = "none"

        async def trace(event: str, info: dict[str, object]) -> None:
            nonlocal transport_phase, tls_state, connect_request_observed, proxy_status
            if event == "http11.send_request_headers.started":
                connect_request_observed = (
                    getattr(info.get("request"), "method", None) == b"CONNECT"
                )
            if event == "http11.receive_response_headers.complete" and connect_request_observed:
                response_head = info.get("return_value")
                if (
                    isinstance(response_head, tuple)
                    and len(response_head) == 4
                    and type(response_head[1]) is int
                    and 100 <= response_head[1] <= 599
                ):
                    proxy_status = response_head[1]
            operation = event.split(".")[-2] if "." in event else ""
            if operation == "start_tls":
                if event.endswith(".started"):
                    tls_state = "started"
                elif event.endswith(".complete"):
                    tls_state = "completed"
            if event.endswith(".started") and operation in {
                "connect_tcp",
                "start_tls",
                "send_request_headers",
                "send_request_body",
                "receive_response_headers",
                "receive_response_body",
            }:
                transport_phase = operation

        try:
            attempt = 0
            while True:
                body.clear()
                transport_phase = "request"
                tls_state = "not_observed"
                connect_request_observed = False
                proxy_status = 0
                status = 0
                try:
                    async with self._client.stream(
                        "POST" if form is not None else "GET",
                        url,
                        data=form,
                        headers={"User-Agent": _USER_AGENT, "Accept": accept},
                        follow_redirects=False,
                        timeout=timeout_seconds,
                        extensions={"trace": trace},
                    ) as response:
                        status = response.status_code
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            if len(body) + len(chunk) > limit:
                                raise GovernedWebSourceError("WEB_CONTENT_TOO_LARGE")
                            body.extend(chunk)
                        # aiter_bytes already applies HTTP content decoding. The buffered
                        # response must describe those decoded bytes, not the wire body.
                        headers = response.headers.copy()
                        headers.pop("content-encoding", None)
                        headers.pop("content-length", None)
                        return httpx.Response(
                            response.status_code,
                            headers=headers,
                            content=bytes(body),
                            request=response.request,
                        )
                except (httpx.ConnectError, httpx.TimeoutException) as error:
                    if form is not None or attempt == 1 or (status != 0 and status != 200):
                        raise
                    attempt += 1
                    _LOGGER.info(
                        "research_web_fetch_retry phase=%s correlation_id=%s "
                        "hostname=%s attempt=%s failure_kind=%s",
                        phase,
                        correlation_id,
                        urlsplit(url).hostname,
                        attempt,
                        type(error).__name__,
                    )
                    # GET is idempotent. Retry only a transient transport failure;
                    # a returned robots/HTTP policy response is never retried.
                    # The caller's task deadline/cancellation also covers backoff.
                    await asyncio.sleep(0.25)
        except httpx.ProxyError as error:
            failure_kind = "ProxyError"
            retryable = proxy_status in {502, 504}
            raise GovernedWebSourceError(
                "WEB_FETCH_TEMPORARILY_UNAVAILABLE" if retryable else "WEB_FETCH_REJECTED",
                retryable=retryable,
            ) from error
        except httpx.DecodingError as error:
            failure_kind = "DecodingError"
            raise GovernedWebSourceError(
                "WEB_ROBOTS_UNAVAILABLE" if phase == "robots" else "WEB_CONTENT_DECODE_FAILED"
            ) from error
        except httpx.RequestError as error:
            failure_kind = type(error).__name__
            raise
        except GovernedWebSourceError:
            failure_kind = "resource_limit"
            raise
        except asyncio.CancelledError:
            failure_kind = "cancelled"
            raise
        finally:
            # Never log the URL path/query, headers, trace info or exception text.
            _LOGGER.info(
                "research_web_fetch phase=%s transport_phase=%s correlation_id=%s "
                "hostname=%s status=%s bytes=%s elapsed_ms=%s failure_kind=%s tls_state=%s "
                "proxy_status=%s",
                phase,
                transport_phase,
                correlation_id,
                urlsplit(url).hostname,
                status,
                len(body),
                round((monotonic() - started) * 1000),
                failure_kind,
                tls_state,
                proxy_status,
            )

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            schema_version="1.0",
            source_id=GOVERNED_WEB_SOURCE_ID,
            display_name="Governed model-assisted public web fetch",
            adapter_version=GOVERNED_WEB_ADAPTER_VERSION,
            source_tier=SourceTier.DEVELOPMENT_ONLY,
            enabled=True,
            capabilities=[
                SourceCapability(
                    capability=GOVERNED_WEB_CAPABILITY,
                    content_tier=ContentTier.DEVELOPMENT_ONLY,
                    fact_eligibility=FactEligibility.INELIGIBLE,
                )
            ],
            authority=0.5,
            rights_registry_id="research-web-allowlist.v1",
        )

    async def _public_response(
        self,
        url: str,
        *,
        correlation_id: UUID,
        accept: str,
        limit: int = _MAX_BODY_BYTES,
        form: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Shared governed transport for pages and public disclosure-index metadata."""
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or hostname not in self._allowed_domains
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise GovernedWebSourceError("WEB_URL_NOT_ALLOWLISTED")
        before = _safe_addresses(await self._resolver(hostname))
        robots_url = f"https://{hostname}/robots.txt"
        try:
            robots_response = await self._bounded_request(
                robots_url,
                limit=512 * 1024,
                timeout_seconds=10.0,
                accept="text/plain",
                phase="robots",
                correlation_id=correlation_id,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise GovernedWebSourceError(
                "WEB_FETCH_TEMPORARILY_UNAVAILABLE", retryable=True
            ) from exc
        if robots_response.status_code not in {200, 404, 410}:
            status = robots_response.status_code
            stop = (
                RobotsOriginStop(
                    origin=f"https://{hostname}",
                    http_status=cast(Literal[401, 403, 429], status),
                    observed_at=self._clock(),
                )
                if status in (401, 403, 429)
                else None
            )
            raise GovernedWebSourceError("WEB_ROBOTS_UNAVAILABLE", robots_origin_stop=stop)
        # RFC 9309 section 2.3.1.3: confirmed absence is distinct from an
        # unreachable policy. Keep explicit denial, rate limits and 5xx closed.
        if robots_response.status_code == 200:
            robot = RobotFileParser()
            robot.set_url(robots_url)
            robot.parse(robots_response.text.splitlines())
            if not robot.can_fetch(_USER_AGENT, url):
                raise GovernedWebSourceError("WEB_ROBOTS_BLOCKED")
        try:
            response = await self._bounded_request(
                url,
                limit=limit,
                timeout_seconds=15.0,
                accept=accept,
                form=form,
                phase="document",
                correlation_id=correlation_id,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise GovernedWebSourceError(
                "WEB_FETCH_TEMPORARILY_UNAVAILABLE", retryable=True
            ) from exc
        if 300 <= response.status_code < 400:
            raise GovernedWebSourceError("WEB_REDIRECT_BLOCKED")
        if response.status_code != 200:
            raise GovernedWebSourceError("WEB_FETCH_REJECTED")
        after = _safe_addresses(await self._resolver(hostname))
        if before != after:
            raise GovernedWebSourceError("WEB_DNS_REBINDING_BLOCKED")
        return response

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        if (
            request.source_id != GOVERNED_WEB_SOURCE_ID
            or request.capability != GOVERNED_WEB_CAPABILITY
            or request.options_namespace != "research.web_fetch"
            or request.options_owner != "research"
            or request.limit != 1
        ):
            raise GovernedWebSourceError("WEB_FETCH_REQUEST_INVALID")
        url = _string_option(request, "url")
        source_id = _string_option(request, "registered_source_id")
        source_level = _string_option(request, "source_level")
        source_group = _string_option(request, "source_group")
        research_use = _string_option(request, "research_use")
        if research_use != "excerpt_bound_fact":
            raise GovernedWebSourceError("WEB_RESEARCH_USE_NOT_ELIGIBLE")
        current_observation = request.options.get("current_run_observation") is True
        formal_rule = request.options.get("acquisition_rule") == "research-real-acquisition.v32"
        published_value = request.options.get("published_at")
        published_at: datetime | None = None
        if published_value is not None and not formal_rule:
            if not isinstance(published_value, str):
                raise GovernedWebSourceError("WEB_PUBLICATION_TIME_UNKNOWN")
            try:
                published_at = datetime.fromisoformat(published_value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise GovernedWebSourceError("WEB_PUBLICATION_TIME_UNKNOWN") from exc
            if published_at.tzinfo is None or published_at > request.context.as_of:
                raise GovernedWebSourceError("WEB_PUBLICATION_TIME_INVALID")
        elif not current_observation and not formal_rule:
            raise GovernedWebSourceError("WEB_PUBLICATION_TIME_UNKNOWN")
        response = await self._public_response(
            url,
            correlation_id=request.context.correlation_id,
            accept="text/html,text/plain,application/pdf",
        )
        if len(response.content) > _MAX_BODY_BYTES:
            raise GovernedWebSourceError("WEB_CONTENT_TOO_LARGE")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
        if media_type not in {"text/html", "text/plain", "application/pdf"}:
            raise GovernedWebSourceError("WEB_CONTENT_TYPE_BLOCKED")
        last_modified_value = response.headers.get("last-modified")
        observed_at = self._clock()
        if observed_at.tzinfo is None:
            raise GovernedWebSourceError("WEB_FETCH_TIME_INVALID")
        outcome: CandidateVerificationOutcome | None = None
        verification: FormalDocumentVerification | None = None
        if formal_rule:
            candidate_value = request.options.get("candidate_id")
            candidate_id = (
                UUID(candidate_value)
                if isinstance(candidate_value, str)
                else request.context.request_id
            )
            receipt_id = uuid5(request.context.request_id, url)
            category = cast(
                Literal["regulatory_official", "company_official", "other"],
                source_level
                if source_level in {"regulatory_official", "company_official"}
                else "other",
            )
            outcome = CandidateVerificationOutcome(
                candidate_id=candidate_id,
                source_id=source_id,
                canonical_url=url,
                source_category=category,
                stage="extraction",
                reason="WEB_FORMAL_DOCUMENT_UNVERIFIED",
                formal_metadata_checked=False,
                receipt_id=receipt_id,
                observed_at=observed_at,
            )
        publication_values: list[tuple[str, str]] = []
        if media_type == "application/pdf":
            try:
                normalized_text = await _pdf_text(response.content)
            except GovernedWebSourceError as exc:
                if outcome is not None:
                    exc.discovery_outcome = outcome.model_copy(update={"reason": exc.code})
                raise
            extraction_scope = (
                "prefix_truncated"
                if "[PDF extraction limit reached; remaining pages unavailable]" in normalized_text
                else "complete"
            )
        elif media_type == "text/html":
            extractor = _TextExtractor()
            extractor.feed(response.text)
            normalized_text = extractor.text()
            extraction_scope = (
                "prefix_truncated"
                if len("\n".join(extractor.parts)) > _MAX_TEXT_CHARS
                else "complete"
            )
            publication_values = extractor.publication_values
        else:
            normalized_text = response.text[:_MAX_TEXT_CHARS]
            extraction_scope = (
                "prefix_truncated" if len(response.text) > _MAX_TEXT_CHARS else "complete"
            )
        if formal_rule:
            attachments: list[DirectAttachmentCandidate] = []
            if media_type == "text/html" and request.options.get("direct_attachment") is not True:
                for ordinal, href in extractor.attachment_hrefs:
                    try:
                        resolved = urlsplit(urljoin(url, href))
                        port = resolved.port
                    except ValueError:
                        continue
                    if (
                        resolved.scheme != "https"
                        or resolved.hostname not in self._allowed_domains
                        or resolved.username is not None
                        or resolved.password is not None
                        or port not in {None, 443}
                    ):
                        continue
                    target = resolved._replace(fragment="").geturl()
                    if (
                        len(target) > 2048
                        or target == url
                        or any(item.canonical_url == target for item in attachments)
                    ):
                        continue
                    if len(attachments) == 4:
                        break
                    attachments.append(
                        DirectAttachmentCandidate(
                            parent_candidate_id=candidate_id,
                            canonical_url=target,
                            href=href,
                            href_ordinal=ordinal,
                            parent_receipt_id=receipt_id,
                        )
                    )
            assert outcome is not None
            outcome = outcome.model_copy(
                update={
                    "stage": "pit",
                    "attachment_candidates": attachments,
                    "attachment_capacity_exceeded": media_type == "text/html"
                    and (
                        extractor.attachment_capacity_exceeded
                        or len(extractor.attachment_hrefs) > 4
                    ),
                }
            )
        try:
            if last_modified_value is None and not current_observation:
                raise GovernedWebSourceError("WEB_HISTORICAL_VERSION_UNKNOWN")
            try:
                parsed_last_modified = (
                    observed_at
                    if last_modified_value is None
                    else parsedate_to_datetime(last_modified_value)
                )
            except (TypeError, ValueError) as exc:
                raise GovernedWebSourceError("WEB_HISTORICAL_VERSION_UNKNOWN") from exc
            if parsed_last_modified.tzinfo is None:
                raise GovernedWebSourceError("WEB_HISTORICAL_VERSION_UNKNOWN")
            last_modified_at = parsed_last_modified.astimezone(UTC)
            if last_modified_value is not None and last_modified_at > request.context.as_of:
                raise GovernedWebSourceError("WEB_FUTURE_REVISION_BLOCKED")
            if len(normalized_text.strip()) < 100:
                raise GovernedWebSourceError("WEB_CONTENT_EMPTY")
            if formal_rule:
                assert outcome is not None
                outcome = outcome.model_copy(
                    update={"stage": "identity", "formal_metadata_checked": True}
                )
                if outcome.source_category == "other":
                    raise GovernedWebSourceError("WEB_FORMAL_SOURCE_UNVERIFIED")
                index_value = request.options.get("official_index")
                index = (
                    OfficialDocumentIndexBinding.model_validate(index_value)
                    if index_value is not None
                    else None
                )
                identity = formal_identity(
                    text=normalized_text,
                    url=url,
                    instrument_id=_string_option(request, "instrument_id"),
                    instrument_name=_string_option(request, "instrument_name"),
                    index=index,
                )
                if media_type == "text/html" and (
                    extractor.article_count != 1
                    or len(extractor.heading_titles) != 1
                    or "".join(identity.title.split())
                    != "".join(extractor.heading_titles[0].split())
                    or extraction_scope != "complete"
                ):
                    raise GovernedWebSourceError("WEB_FORMAL_DOCUMENT_UNVERIFIED")
                outcome = outcome.model_copy(update={"stage": "publication"})
                published_at, proofs = formal_publication(
                    text=normalized_text,
                    url=url,
                    metadata=publication_values,
                    index=index,
                    receipt_id=outcome.receipt_id,
                    cutoff=request.context.as_of,
                )
                publication_basis = "; ".join(dict.fromkeys(proof.kind for proof in proofs))
                verification = FormalDocumentVerification(
                    identity=identity,
                    publication_evidence=proofs,
                    published_at=published_at,
                    source_id=source_id,
                    normalized_original_sha256=hashlib.sha256(normalized_text.encode()).hexdigest(),
                    rights_snapshot_sha256=request.rights_snapshot_hash,
                    first_seen_at=observed_at,
                    pit_cutoff=request.context.as_of,
                    media_type=cast(
                        Literal["application/pdf", "text/html", "text/plain"], media_type
                    ),
                )
                outcome = outcome.model_copy(
                    update={"stage": "verified", "reason": "WEB_FORMAL_VERIFIED"}
                )
            else:
                published_at, publication_basis = _resolve_publication(
                    url=url,
                    text=normalized_text,
                    metadata=publication_values,
                    hint=published_at,
                    cutoff=request.context.as_of,
                )
        except GovernedWebSourceError as error:
            if outcome is not None:
                error.discovery_outcome = outcome.model_copy(update={"reason": error.code})
            raise
        fetched_at = observed_at
        if fetched_at.tzinfo is None:
            raise GovernedWebSourceError("WEB_FETCH_TIME_INVALID")
        fetched_at = fetched_at.astimezone(UTC)
        content_sha256 = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        record = SourceRecordV1_2(
            source_id=GOVERNED_WEB_SOURCE_ID,
            formal_document=verification,
            external_id=hashlib.sha256(url.encode("utf-8")).hexdigest(),
            capability=GOVERNED_WEB_CAPABILITY,
            record_type="research_web_page",
            source_tier=SourceTier.DEVELOPMENT_ONLY,
            content_tier=ContentTier.DEVELOPMENT_ONLY,
            fact_eligibility=FactEligibility.INELIGIBLE,
            authority=0.5,
            canonical_url=url,
            event_time=published_at,
            published_at=published_at,
            first_seen_at=fetched_at,
            fetched_at=fetched_at,
            content_sha256=content_sha256,
            media_type="text/plain",
            payload={
                "text": normalized_text,
                "extraction_scope": extraction_scope,
                "original_media_type": media_type,
                "registered_source_id": source_id,
                "source_level": source_level,
                "source_group": source_group,
                "acquisition_mode": "model_assisted_web",
                "last_modified_at": last_modified_at.isoformat(),
                "revision_basis": (
                    "current_observation" if last_modified_value is None else "http_last_modified"
                ),
                "research_use": research_use,
                "publication_basis": publication_basis,
            },
            payload_namespace="research.web_evidence",
            payload_owner="research",
            rights_snapshot_hash=request.rights_snapshot_hash,
            provenance=SourceProvenanceV1_2(
                schema_version="1.2",
                provider=source_id,
                access_channel=SourceAccessChannelV1_2.OFFICIAL_HTTP,
                adapter_name="governed_web",
                adapter_version=GOVERNED_WEB_ADAPTER_VERSION,
                endpoint_name="public_page",
                source_identifier=url,
                runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
                release_tier=SourceReleaseTier.DEV_ONLY,
                rights_status=SourceRightsStatusV1_2.UNVERIFIED,
                quality_tier=SourceQualityTier.DEV_WEB,
                evidence_role=SourceEvidenceRole.CROSS_CHECK,
            ),
        )
        return FetchBatch(
            schema_version="1.1" if formal_rule else "1.0",
            discovery_outcome=outcome,
            source_id=GOVERNED_WEB_SOURCE_ID,
            records=[record],
            exhausted=True,
            started_at=fetched_at,
            completed_at=fetched_at,
            source_watermark=published_at,
            rights_snapshot_hash=request.rights_snapshot_hash,
        )


__all__ = [
    "GOVERNED_WEB_ADAPTER_VERSION",
    "GOVERNED_WEB_CAPABILITY",
    "GOVERNED_WEB_SOURCE_ID",
    "DevelopmentResearchWebRightsPolicy",
    "GovernedWebSourceConnector",
    "GovernedWebSourceError",
]
