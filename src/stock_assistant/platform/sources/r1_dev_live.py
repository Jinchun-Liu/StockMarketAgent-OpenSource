"""Development-only, rights-gated AKShare adapters for the fixed R1 target."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Final
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import JsonValue, ValidationError

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.contracts.ingestion import (
    ContentTier,
    FactEligibility,
    FetchBatch,
    FetchRequest,
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
    SourceAccessChannel,
    SourceCapability,
    SourceDescriptor,
    SourceProvenance,
    SourceRecord,
    SourceReleaseTier,
    SourceRightsStatus,
    SourceRuntimeMode,
    SourceTier,
)
from stock_assistant.contracts.portfolio import R1_FIXED_TARGET_INSTRUMENT_ID
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.modules.ingestion.source_rights import normalize_r1_instrument_code
from stock_assistant.platform.sources.akshare_runner import AkshareSubprocessRunner
from stock_assistant.ports.ingestion import RightsPolicyPort

_SHANGHAI: Final = ZoneInfo("Asia/Shanghai")
_DEV_LIVE_ID_NAMESPACE: Final = UUID("12bbf11e-b504-591d-bbf1-33870e6595dd")
DEV_LIVE_ADAPTER_VERSION: Final = "akshare-dev-live-v1"
DEV_LIVE_LICENSE_ID: Final = "akshare-upstream-rights-unverified"
DEV_LIVE_RIGHTS_HASH: Final = hashlib.sha256(b"akshare-dev-live-metadata-profile-v1").hexdigest()

DEV_LIVE_SOURCE_IDS: Final = {
    "statutory.announcements": "akshare_dev_cninfo_statutory",
    "financial.changes": "akshare_dev_eastmoney_financial",
    "market.snapshot": "akshare_dev_eastmoney_market",
    "company_industry.news": "akshare_dev_eastmoney_news",
    "macro.flashes": "akshare_dev_macro_unqualified",
}
_FUNCTION_BY_CAPABILITY: Final = {
    "statutory.announcements": "stock_zh_a_disclosure_report_cninfo",
    "financial.changes": "stock_financial_analysis_indicator_em",
    "market.snapshot": "stock_zh_a_hist",
    "company_industry.news": "stock_news_em",
}
_PROVIDER_BY_CAPABILITY: Final = {
    "statutory.announcements": "cninfo",
    "financial.changes": "eastmoney",
    "market.snapshot": "eastmoney",
    "company_industry.news": "eastmoney",
    "macro.flashes": "unqualified",
}
_RECORD_POLICY_BY_CAPABILITY: Final = {
    "statutory.announcements": (
        SourceTier.STATUTORY_DISCLOSURE,
        ContentTier.STATUTORY_DISCLOSURE,
        FactEligibility.COMPANY_FACT,
    ),
    "financial.changes": (
        SourceTier.DEVELOPMENT_ONLY,
        ContentTier.DEVELOPMENT_ONLY,
        FactEligibility.INELIGIBLE,
    ),
    "market.snapshot": (
        SourceTier.MARKET_DATA,
        ContentTier.MARKET_DATA,
        FactEligibility.MARKET_OBSERVATION,
    ),
    "company_industry.news": (
        SourceTier.DEVELOPMENT_ONLY,
        ContentTier.DEVELOPMENT_ONLY,
        FactEligibility.INELIGIBLE,
    ),
}
_FINANCIAL_FIELDS: Final = (
    "日期",
    "摊薄每股收益(元)",
    "每股净资产_调整后(元)",
    "净资产收益率(%)",
    "主营业务收入增长率(%)",
    "净利润增长率(%)",
    "资产负债率(%)",
    "销售毛利率(%)",
)
_ANNOUNCEMENT_FIELDS: Final = ("公告标题", "公告时间", "公告链接", "公告类型")
_MARKET_FIELDS: Final = ("日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额")
_NEWS_FIELDS: Final = ("新闻标题", "发布时间", "文章来源", "新闻链接")

_REQUIRED_RIGHTS: Final = (
    (RightsOperation.FETCH, RightsPermittedUse.NON_DISPLAY),
    (RightsOperation.CACHE_METADATA, RightsPermittedUse.NON_DISPLAY),
    (RightsOperation.RETAIN, RightsPermittedUse.NON_DISPLAY),
    (RightsOperation.DERIVE, RightsPermittedUse.DISPLAY),
    (RightsOperation.DERIVE, RightsPermittedUse.SUMMARY),
)
_ALLOWED_OPERATIONS: Final = {
    RightsOperation.FETCH,
    RightsOperation.CACHE_METADATA,
    RightsOperation.RETAIN,
    RightsOperation.DERIVE,
}
_ALLOWED_USES: Final = {
    RightsPermittedUse.DISPLAY,
    RightsPermittedUse.SUMMARY,
    RightsPermittedUse.NON_DISPLAY,
}


class DevelopmentMetadataRightsPolicy:
    """Local development profile; it never represents provider authorization."""

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        allowed = (
            check.source_id in DEV_LIVE_SOURCE_IDS.values()
            and check.rights_snapshot_hash == DEV_LIVE_RIGHTS_HASH
            and check.operation in _ALLOWED_OPERATIONS
            and check.permitted_use in _ALLOWED_USES
        )
        return RightsDecision(
            allowed=allowed,
            reason_code="DEV_METADATA_ALLOWED" if allowed else "DEV_RIGHTS_BLOCKED",
            decided_at=check.context.as_of,
            rights_snapshot_hash=check.rights_snapshot_hash,
            retention_until=(check.context.as_of + timedelta(days=1) if allowed else None),
        )


class AkshareDevLiveAdapter:
    """Pure request and normalization policy with no network-capable dependency."""

    def __init__(self, capability: str) -> None:
        if capability not in _FUNCTION_BY_CAPABILITY:
            raise ValueError(f"DEV_LIVE capability has no AKShare adapter: {capability}")
        self.capability = capability
        self.source_id = DEV_LIVE_SOURCE_IDS[capability]
        self.function_name = _FUNCTION_BY_CAPABILITY[capability]
        source_tier, content_tier, fact_eligibility = _RECORD_POLICY_BY_CAPABILITY[capability]
        self.provenance = SourceProvenance(
            schema_version="1.1",
            provider=_PROVIDER_BY_CAPABILITY[capability],
            access_channel=SourceAccessChannel.AKSHARE,
            adapter_name="akshare_dev_live",
            adapter_version=DEV_LIVE_ADAPTER_VERSION,
            endpoint_name=self.function_name,
            source_identifier=f"603986.SH:{capability}",
            runtime_mode=SourceRuntimeMode.DEV_LIVE,
            release_tier=SourceReleaseTier.DEV_ONLY,
            rights_status=SourceRightsStatus.UNVERIFIED,
        )
        self.descriptor = SourceDescriptor(
            schema_version="1.0",
            source_id=self.source_id,
            display_name=f"AKShare DEV {capability}",
            adapter_version=DEV_LIVE_ADAPTER_VERSION,
            source_tier=source_tier,
            enabled=False,
            capabilities=[
                SourceCapability(
                    capability=capability,
                    content_tier=content_tier,
                    fact_eligibility=fact_eligibility,
                )
            ],
            authority=0.0,
            rights_registry_id="akshare-dev-live-unverified",
        )

    def function_arguments(self, request: FetchRequest) -> dict[str, JsonValue]:
        """Build bounded provider arguments without performing I/O."""

        _validate_request(request, source_id=self.source_id, capability=self.capability)
        if request.until is None:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_WINDOW_REQUIRED")
        return _function_arguments(self.capability, request)

    def normalize(
        self,
        request: FetchRequest,
        rows: Sequence[Mapping[str, JsonValue]],
        *,
        execution_started_at: datetime | None = None,
        execution_completed_at: datetime | None = None,
    ) -> FetchBatch:
        """Normalize already-fetched rows; this method cannot initiate network I/O."""

        _validate_request(request, source_id=self.source_id, capability=self.capability)
        if request.until is None:
            raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_WINDOW_REQUIRED")
        started_at = execution_started_at or request.context.requested_at
        completed_at = execution_completed_at or started_at
        try:
            records = _normalize_records(
                capability=self.capability,
                source_id=self.source_id,
                provenance=self.provenance,
                request=request,
                rows=rows,
                first_seen_at=started_at,
                fetched_at=completed_at,
            )
            next_cursor = request.until.isoformat()
            batch_identity = _canonical_json(
                {
                    "source_id": self.source_id,
                    "capability": self.capability,
                    "since": None if request.since is None else request.since.isoformat(),
                    "until": request.until.isoformat(),
                    "rights_snapshot_hash": request.rights_snapshot_hash,
                    "record_ids": [str(item.record_id) for item in records],
                    "cursor": next_cursor,
                }
            )
            return FetchBatch(
                schema_version="1.0",
                batch_id=uuid5(_DEV_LIVE_ID_NAMESPACE, f"batch:{batch_identity}"),
                source_id=self.source_id,
                records=records,
                next_cursor=next_cursor,
                exhausted=True,
                started_at=started_at,
                completed_at=completed_at,
                source_watermark=max(
                    (item.event_time for item in records if item.event_time is not None),
                    default=request.until,
                ),
                rights_snapshot_hash=request.rights_snapshot_hash,
                warnings=[],
            )
        except ApplicationError as error:
            if error.safe_message != "AKSHARE_SCHEMA_DRIFT":
                raise
            raise _source_schema_invalid() from error
        except (AttributeError, KeyError, TypeError, ValueError, ValidationError) as error:
            raise _source_schema_invalid() from error


class RightsGatedAkshareConnector:
    """Existing SourceConnectorPort implementation with rights before runner access."""

    def __init__(
        self,
        *,
        adapter: AkshareDevLiveAdapter,
        rights_policy: RightsPolicyPort,
        runner: AkshareSubprocessRunner,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._adapter = adapter
        self._rights_policy = rights_policy
        self._runner = runner
        self._clock = clock

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._adapter.descriptor

    async def fetch(self, request: FetchRequest) -> FetchBatch:
        _validate_request(
            request,
            source_id=self._adapter.source_id,
            capability=self._adapter.capability,
        )
        for operation, permitted_use in _REQUIRED_RIGHTS:
            check = RightsCheck(
                context=request.context,
                source_id=request.source_id,
                operation=operation,
                rights_snapshot_hash=request.rights_snapshot_hash,
                content_category=request.capability,
                permitted_use=permitted_use,
            )
            decision = await self._rights_policy.authorize(check)
            if (
                not decision.allowed
                or decision.rights_snapshot_hash != request.rights_snapshot_hash
                or decision.decided_at > request.context.as_of
                or (
                    decision.retention_until is not None
                    and decision.retention_until < request.context.as_of
                )
            ):
                raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, decision.reason_code)
        execution_started_at = _read_execution_clock(self._clock)
        rows = await self._runner.run(
            self._adapter.function_name,
            self._adapter.function_arguments(request),
        )
        execution_completed_at = _read_execution_clock(self._clock)
        if execution_completed_at < execution_started_at:
            raise _source_clock_invalid()
        return self._adapter.normalize(
            request,
            rows,
            execution_started_at=execution_started_at,
            execution_completed_at=execution_completed_at,
        )


def _source_schema_invalid() -> ApplicationError:
    return ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_SCHEMA_INVALID")


def _source_clock_invalid() -> ApplicationError:
    return ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_CLOCK_INVALID")


def _read_execution_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        observed_at = clock()
        if not isinstance(observed_at, datetime):
            raise TypeError("clock must return datetime")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return observed_at.astimezone(UTC)
    except Exception as error:
        raise _source_clock_invalid() from error


def dev_live_macro_gap() -> tuple[str, SourceCoverageStatus, SourceProvenance]:
    """Expose the deliberate absence of a generic macro crawler in DEV_LIVE."""

    capability = "macro.flashes"
    provenance = SourceProvenance(
        schema_version="1.1",
        provider="unqualified",
        access_channel=SourceAccessChannel.AKSHARE,
        adapter_name="akshare_dev_live",
        adapter_version=DEV_LIVE_ADAPTER_VERSION,
        endpoint_name="not_qualified",
        source_identifier=f"603986.SH:{capability}",
        runtime_mode=SourceRuntimeMode.DEV_LIVE,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatus.UNVERIFIED,
    )
    return "DEV_LIVE_MACRO_NOT_QUALIFIED", SourceCoverageStatus.NOT_QUALIFIED, provenance


def _validate_request(request: FetchRequest, *, source_id: str, capability: str) -> None:
    if request.source_id != source_id or request.capability != capability:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_SOURCE_REQUEST_MISMATCH")
    if request.rights_snapshot_hash != DEV_LIVE_RIGHTS_HASH:
        raise ApplicationError(ErrorCode.RIGHTS_BLOCKED, "DEV_RIGHTS_PROFILE_MISMATCH")
    if request.instrument_ids != [R1_FIXED_TARGET_INSTRUMENT_ID]:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")
    normalize_r1_instrument_code(request.instrument_ids[0])


def _function_arguments(capability: str, request: FetchRequest) -> dict[str, JsonValue]:
    assert request.until is not None
    start = (request.since or request.until - timedelta(days=30)).astimezone(_SHANGHAI)
    end = request.until.astimezone(_SHANGHAI)
    if capability == "statutory.announcements":
        return {
            "symbol": "603986",
            "market": "沪深京",
            "category": "",
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        }
    if capability == "financial.changes":
        return {"symbol": "603986.SH", "start_year": str(start.year)}
    if capability == "market.snapshot":
        return {
            "symbol": "603986",
            "period": "daily",
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "adjust": "",
            "timeout": 10,
        }
    return {"symbol": "603986"}


def _normalize_records(
    *,
    capability: str,
    source_id: str,
    provenance: SourceProvenance,
    request: FetchRequest,
    rows: Sequence[Mapping[str, JsonValue]],
    first_seen_at: datetime,
    fetched_at: datetime,
) -> list[SourceRecord]:
    assert request.until is not None
    cutoff = min(request.context.as_of, request.until)
    selected_rows = list(rows)
    if capability == "market.snapshot":
        selected_rows = _last_complete_market_row(selected_rows, cutoff)
    candidates: dict[UUID, tuple[tuple[str, ...], SourceRecord]] = {}
    for row in selected_rows:
        payload = _select_payload(capability, row)
        event_time = _event_time(capability, row, cutoff)
        if event_time > cutoff:
            continue
        canonical = _canonical_json(payload)
        content_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
        title = _title(capability, row)
        url = _url(row)
        external_id = _external_id(capability, row, content_sha256)
        semantic_identity = _canonical_json(
            {
                "source_id": source_id,
                "capability": capability,
                "external_id": external_id,
                "event_time": event_time.isoformat(),
                "canonical_url": url,
                "content_sha256": content_sha256,
            }
        )
        record_id = uuid5(_DEV_LIVE_ID_NAMESPACE, f"record:{semantic_identity}")
        source_tier, content_tier, fact_eligibility = _RECORD_POLICY_BY_CAPABILITY[capability]
        record = SourceRecord(
            record_id=record_id,
            source_id=source_id,
            external_id=external_id,
            capability=capability,
            record_type=capability,
            source_tier=source_tier,
            content_tier=content_tier,
            fact_eligibility=fact_eligibility,
            authority=0.0,
            title=title,
            canonical_url=url,
            event_time=event_time,
            published_at=event_time,
            first_seen_at=first_seen_at,
            fetched_at=fetched_at,
            content_sha256=content_sha256,
            payload=payload,
            payload_namespace="stock_assistant.r1_dev_live",
            payload_owner="dmi",
            rights_snapshot_hash=request.rights_snapshot_hash,
            provenance=provenance,
        )
        stable_key = (
            event_time.isoformat(),
            external_id,
            content_sha256,
            str(record_id),
        )
        candidates[record_id] = (stable_key, record)
    return [
        record for _, record in sorted(candidates.values(), key=lambda candidate: candidate[0])
    ][: request.limit]


def _select_payload(
    capability: str,
    row: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    required = {
        "statutory.announcements": ("公告标题", "公告时间", "公告链接"),
        "financial.changes": ("日期",),
        "market.snapshot": ("日期", "收盘"),
        "company_industry.news": ("新闻标题", "发布时间", "新闻链接"),
    }[capability]
    if any(row.get(key) is None for key in required):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "AKSHARE_SCHEMA_DRIFT")
    if capability == "financial.changes" and not any(
        row.get(key) is not None for key in _FINANCIAL_FIELDS[1:]
    ):
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "AKSHARE_SCHEMA_DRIFT")
    fields = {
        "statutory.announcements": _ANNOUNCEMENT_FIELDS,
        "financial.changes": _FINANCIAL_FIELDS,
        "market.snapshot": _MARKET_FIELDS,
        "company_industry.news": _NEWS_FIELDS,
    }[capability]
    payload = {key: row[key] for key in fields if key in row and row[key] is not None}
    return payload


def _last_complete_market_row(
    rows: Sequence[Mapping[str, JsonValue]],
    as_of: datetime,
) -> list[Mapping[str, JsonValue]]:
    local = as_of.astimezone(_SHANGHAI)
    cutoff = (
        local.date()
        if local.timetz().replace(tzinfo=None) >= time(15)
        else local.date() - timedelta(days=1)
    )
    eligible = [
        row
        for row in rows
        if _parse_datetime(row.get("日期"), as_of).astimezone(_SHANGHAI).date() <= cutoff
    ]
    return (
        []
        if not eligible
        else [
            max(
                eligible,
                key=lambda row: (
                    _parse_datetime(row.get("日期"), as_of),
                    _canonical_json(_select_payload("market.snapshot", row)),
                ),
            )
        ]
    )


def _event_time(capability: str, row: Mapping[str, JsonValue], fallback: datetime) -> datetime:
    keys = {
        "statutory.announcements": ("公告时间", "公告日期"),
        "financial.changes": ("日期", "报告期"),
        "market.snapshot": ("日期",),
        "company_industry.news": ("发布时间",),
    }[capability]
    for key in keys:
        if key in row and row[key] is not None:
            return _parse_datetime(row[key], fallback)
    return fallback


def _parse_datetime(value: JsonValue | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return fallback
    raw = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, pattern).replace(tzinfo=_SHANGHAI)
        except ValueError:
            continue
        return parsed.astimezone(UTC)
    try:
        parsed_iso = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "AKSHARE_SCHEMA_DRIFT") from error
    if parsed_iso.tzinfo is None:
        parsed_iso = parsed_iso.replace(tzinfo=_SHANGHAI)
    return parsed_iso.astimezone(UTC)


def _title(capability: str, row: Mapping[str, JsonValue]) -> str:
    keys = {
        "statutory.announcements": ("公告标题",),
        "financial.changes": ("日期",),
        "market.snapshot": ("日期",),
        "company_industry.news": ("新闻标题",),
    }[capability]
    for key in keys:
        if key in row and row[key] is not None:
            return str(row[key])[:500]
    return f"603986.SH {capability}"


def _url(row: Mapping[str, JsonValue]) -> str | None:
    for key in ("公告链接", "新闻链接", "网址", "url"):
        value = row.get(key)
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
    return None


def _external_id(
    capability: str,
    row: Mapping[str, JsonValue],
    content_sha256: str,
) -> str:
    for key in ("公告编号", "公告链接", "新闻链接", "日期", "发布时间"):
        value = row.get(key)
        if value is not None:
            return f"{capability}:{value}"[:500]
    return f"{capability}:{content_sha256}"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "DEV_LIVE_ADAPTER_VERSION",
    "DEV_LIVE_LICENSE_ID",
    "DEV_LIVE_RIGHTS_HASH",
    "DEV_LIVE_SOURCE_IDS",
    "AkshareDevLiveAdapter",
    "DevelopmentMetadataRightsPolicy",
    "RightsGatedAkshareConnector",
    "dev_live_macro_gap",
]
