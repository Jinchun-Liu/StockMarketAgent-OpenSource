import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import (
    FetchRequest,
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
)
from stock_assistant.contracts.premarket_views import SourceCoverageStatus
from stock_assistant.modules.ingestion.r1_sources import source_batch_from_fetch
from stock_assistant.platform.sources.akshare_runner import (
    AkshareProcessResult,
    AkshareSubprocessRunner,
)
from stock_assistant.platform.sources.r1_dev_live import (
    DEV_LIVE_LICENSE_ID,
    DEV_LIVE_RIGHTS_HASH,
    DEV_LIVE_SOURCE_IDS,
    AkshareDevLiveAdapter,
    DevelopmentMetadataRightsPolicy,
    RightsGatedAkshareConnector,
    dev_live_macro_gap,
)

NOW = datetime(2026, 7, 31, 0, 55, tzinfo=UTC)
EXECUTION_STARTED = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)
EXECUTION_COMPLETED = EXECUTION_STARTED + timedelta(seconds=2)


def _context() -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        request_id=UUID(int=11),
        correlation_id=UUID(int=12),
        actor_id="r1-dev-live-test",
        as_of=NOW,
        requested_at=NOW - timedelta(seconds=1),
    )


def _request(capability: str, *, instrument_id: str = "XSHG:603986") -> FetchRequest:
    return FetchRequest(
        schema_version="1.0",
        context=_context(),
        source_id=DEV_LIVE_SOURCE_IDS[capability],
        capability=capability,
        rights_snapshot_hash=DEV_LIVE_RIGHTS_HASH,
        since=NOW - timedelta(days=30),
        until=NOW,
        instrument_ids=[instrument_id],
        limit=100,
    )


class DeniedPolicy:
    def __init__(self) -> None:
        self.checks: list[RightsCheck] = []

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        self.checks.append(check)
        return RightsDecision(
            allowed=False,
            reason_code="DEV_RIGHTS_BLOCKED",
            decided_at=NOW,
            rights_snapshot_hash=check.rights_snapshot_hash,
        )


@pytest.mark.asyncio
async def test_rights_denial_and_target_lock_happen_before_runner() -> None:
    runner_calls = 0

    def execute(*_args: object) -> AkshareProcessResult:
        nonlocal runner_calls
        runner_calls += 1
        return AkshareProcessResult(returncode=0, stdout='{"ok":true,"rows":[]}')

    runner = AkshareSubprocessRunner(executor=execute)
    adapter = AkshareDevLiveAdapter("market.snapshot")
    denied = DeniedPolicy()
    connector = RightsGatedAkshareConnector(
        adapter=adapter,
        rights_policy=denied,
        runner=runner,
    )

    assert not hasattr(adapter, "runner")
    assert not hasattr(adapter, "fetch")
    assert not hasattr(adapter, "fetch_after_rights")

    with pytest.raises(ApplicationError, match="DEV_RIGHTS_BLOCKED"):
        await connector.fetch(_request("market.snapshot"))
    assert len(denied.checks) == 1
    assert runner_calls == 0

    with pytest.raises(ApplicationError, match="R1_TARGET_LOCKED"):
        await connector.fetch(_request("market.snapshot", instrument_id="XSHG:600000"))
    assert len(denied.checks) == 1
    assert runner_calls == 0


@pytest.mark.asyncio
async def test_development_rights_profile_allows_metadata_but_rejects_ai_and_fulltext() -> None:
    policy = DevelopmentMetadataRightsPolicy()
    request = _request("market.snapshot")

    allowed = await policy.authorize(
        RightsCheck(
            context=request.context,
            source_id=request.source_id,
            operation=RightsOperation.CACHE_METADATA,
            rights_snapshot_hash=request.rights_snapshot_hash,
            content_category=request.capability,
            permitted_use=RightsPermittedUse.SUMMARY,
        )
    )
    denied = [
        await policy.authorize(
            RightsCheck(
                context=request.context,
                source_id=request.source_id,
                operation=operation,
                rights_snapshot_hash=request.rights_snapshot_hash,
                content_category=request.capability,
                permitted_use=permitted_use,
            )
        )
        for operation, permitted_use in (
            (RightsOperation.CACHE_FULLTEXT, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.EMBED, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.TRAIN, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.EXPORT, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.REDISTRIBUTE, RightsPermittedUse.NON_DISPLAY),
            (RightsOperation.DERIVE, RightsPermittedUse.AI_USE),
        )
    ]

    assert allowed.allowed is True
    assert all(item.allowed is False for item in denied)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "rows"),
    (
        (
            "statutory.announcements",
            [
                {
                    "公告标题": "董事会公告",
                    "公告时间": "2026-07-30 16:00:00",
                    "公告链接": "https://example.invalid/announcement",
                    "公告类型": "董事会",
                    "正文": "must-not-persist",
                }
            ],
        ),
        (
            "financial.changes",
            [
                {
                    "日期": "2026-06-30",
                    "摊薄每股收益(元)": "1.23",
                    "每股净资产_调整后(元)": "10.00",
                    "净资产收益率(%)": "12.3",
                    "主营业务收入增长率(%)": "8.0",
                    "净利润增长率(%)": "7.0",
                    "资产负债率(%)": "20.0",
                    "销售毛利率(%)": "40.0",
                    "未授权字段": "must-not-persist",
                }
            ],
        ),
        (
            "market.snapshot",
            [
                {"日期": "2026-07-29", "开盘": 90, "收盘": 91, "最高": 92, "最低": 89},
                {"日期": "2026-07-30", "开盘": 91, "收盘": 93, "最高": 94, "最低": 90},
                {"日期": "2026-07-31", "开盘": 93, "收盘": 94, "最高": 95, "最低": 92},
            ],
        ),
        (
            "company_industry.news",
            [
                {
                    "新闻标题": "新品消息",
                    "发布时间": "2026-07-30 17:00:00",
                    "文章来源": "开发来源",
                    "新闻链接": "https://example.invalid/news",
                    "新闻内容": "A" * 500,
                    "用户Cookie": "must-not-persist",
                }
            ],
        ),
    ),
)
async def test_dev_live_connectors_normalize_only_bounded_metadata(
    capability: str,
    rows: list[dict[str, object]],
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def execute(
        function: str,
        arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ):
        assert isinstance(arguments, dict)
        calls.append((function, arguments))
        return AkshareProcessResult(
            returncode=0,
            stdout=json.dumps({"ok": True, "rows": rows}, ensure_ascii=False),
        )

    runner = AkshareSubprocessRunner(executor=execute)
    adapter = AkshareDevLiveAdapter(capability)
    connector = RightsGatedAkshareConnector(
        adapter=adapter,
        rights_policy=DevelopmentMetadataRightsPolicy(),
        runner=runner,
    )

    batch = await connector.fetch(_request(capability))

    assert len(calls) == 1
    assert batch.source_id == DEV_LIVE_SOURCE_IDS[capability]
    assert all(item.provenance == adapter.provenance for item in batch.records)
    assert all(item.object_key is None for item in batch.records)
    serialized = json.dumps(
        [item.payload for item in batch.records],
        ensure_ascii=False,
    )
    assert "must-not-persist" not in serialized
    if capability == "financial.changes":
        assert len(batch.records[0].payload) == 8
    if capability == "market.snapshot":
        assert len(batch.records) == 1
        assert batch.records[0].payload["日期"] == "2026-07-30"
        assert calls[0][1]["adjust"] == ""
    if capability == "company_industry.news":
        assert set(batch.records[0].payload) == {
            "新闻标题",
            "发布时间",
            "文章来源",
            "新闻链接",
        }
        assert "新闻内容" not in serialized


def test_macro_dev_live_is_explicitly_not_qualified_without_a_runner() -> None:
    reason, status, provenance = dev_live_macro_gap()

    assert reason == "DEV_LIVE_MACRO_NOT_QUALIFIED"
    assert status is SourceCoverageStatus.NOT_QUALIFIED
    assert provenance.endpoint_name == "not_qualified"


def test_normalization_is_byte_stable_across_order_duplicates_and_repeated_calls() -> None:
    rows = [
        {
            "新闻标题": "进展乙",
            "发布时间": "2026-07-30 18:00:00",
            "文章来源": "开发来源",
            "新闻链接": "https://example.invalid/news-b",
        },
        {
            "新闻标题": "进展甲",
            "发布时间": "2026-07-30 17:00:00",
            "文章来源": "开发来源",
            "新闻链接": "https://example.invalid/news-a",
        },
    ]
    adapter = AkshareDevLiveAdapter("company_industry.news")
    request = _request("company_industry.news")

    first = adapter.normalize(request, rows)
    reordered = adapter.normalize(request, [rows[1], rows[0]])
    repeated_and_duplicated = adapter.normalize(request, [*rows, rows[0]])

    assert first.model_dump_json() == reordered.model_dump_json()
    assert first.model_dump_json() == repeated_and_duplicated.model_dump_json()
    assert first.batch_id == reordered.batch_id == repeated_and_duplicated.batch_id
    assert [item.record_id for item in first.records] == [
        item.record_id for item in reordered.records
    ]


def test_market_same_date_tie_break_is_deterministic() -> None:
    rows = [
        {"日期": "2026-07-30", "开盘": 91, "收盘": 92, "最高": 94, "最低": 90},
        {"日期": "2026-07-30", "开盘": 91, "收盘": 93, "最高": 94, "最低": 90},
    ]
    adapter = AkshareDevLiveAdapter("market.snapshot")
    request = _request("market.snapshot")

    first = adapter.normalize(request, rows)
    reversed_rows = adapter.normalize(request, list(reversed(rows)))

    assert first.model_dump_json() == reversed_rows.model_dump_json()
    assert first.records[0].payload["收盘"] == 93


@pytest.mark.asyncio
async def test_upstream_schema_drift_fails_closed_without_persistable_records() -> None:
    def execute(
        _function: str,
        _arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ):
        return AkshareProcessResult(
            returncode=0,
            stdout=json.dumps({"ok": True, "rows": [{"日期": "2026-07-30"}]}),
        )

    runner = AkshareSubprocessRunner(executor=execute)
    adapter = AkshareDevLiveAdapter("market.snapshot")
    connector = RightsGatedAkshareConnector(
        adapter=adapter,
        rights_policy=DevelopmentMetadataRightsPolicy(),
        runner=runner,
    )

    with pytest.raises(ApplicationError, match="R1_SOURCE_SCHEMA_INVALID") as exc_info:
        await connector.fetch(_request("market.snapshot"))

    assert exc_info.value.safe_message == "R1_SOURCE_SCHEMA_INVALID"


@pytest.mark.asyncio
async def test_data_cutoff_before_request_time_keeps_execution_times_ordered() -> None:
    context = _context().model_copy(
        update={
            "requested_at": EXECUTION_STARTED,
            "as_of": NOW,
        }
    )
    request = _request("market.snapshot").model_copy(update={"context": context})

    def execute(
        _function: str,
        _arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ) -> AkshareProcessResult:
        return AkshareProcessResult(
            returncode=0,
            stdout=json.dumps({"ok": True, "rows": [{"日期": "2026-07-30", "收盘": 93}]}),
        )

    connector = RightsGatedAkshareConnector(
        adapter=AkshareDevLiveAdapter("market.snapshot"),
        rights_policy=DevelopmentMetadataRightsPolicy(),
        runner=AkshareSubprocessRunner(executor=execute),
        clock=iter((EXECUTION_STARTED, EXECUTION_COMPLETED)).__next__,
    )

    batch = await connector.fetch(request)

    assert batch.started_at == EXECUTION_STARTED
    assert batch.completed_at == EXECUTION_COMPLETED
    assert batch.completed_at >= batch.started_at
    assert batch.next_cursor == NOW.isoformat()
    assert batch.records[0].event_time < NOW
    assert batch.records[0].first_seen_at == EXECUTION_STARTED
    assert batch.records[0].fetched_at == EXECUTION_COMPLETED
    normalized = source_batch_from_fetch(
        fetch_batch=batch,
        capability="market.snapshot",
        provenance=AkshareDevLiveAdapter("market.snapshot").provenance,
        license_id=DEV_LIVE_LICENSE_ID,
    )
    assert normalized.events[0].revision_known_at == EXECUTION_COMPLETED


def test_normalize_converts_pydantic_failure_without_vendor_text() -> None:
    adapter = AkshareDevLiveAdapter("company_industry.news")
    request = _request("company_industry.news")
    vendor_detail = "vendor-private-detail"
    rows = [
        {
            "新闻标题": "news",
            "发布时间": "2026-07-30 17:00:00",
            "文章来源": vendor_detail,
            "新闻链接": "https://example.invalid/" + "x" * 2048,
        }
    ]

    with pytest.raises(ApplicationError) as exc_info:
        adapter.normalize(
            request,
            rows,
            execution_started_at=EXECUTION_STARTED,
            execution_completed_at=EXECUTION_COMPLETED,
        )

    assert exc_info.value.safe_message == "R1_SOURCE_SCHEMA_INVALID"
    assert vendor_detail not in str(exc_info.value)


@pytest.mark.parametrize(
    "rows",
    (
        [],
        [
            {
                "新闻标题": "future",
                "发布时间": "2026-07-31 09:00:00",
                "新闻链接": "https://example.invalid/future",
            }
        ],
    ),
)
def test_empty_or_future_only_success_advances_watermark_to_window_end(
    rows: list[dict[str, object]],
) -> None:
    adapter = AkshareDevLiveAdapter("company_industry.news")
    request = _request("company_industry.news")

    batch = adapter.normalize(
        request,
        rows,
        execution_started_at=EXECUTION_STARTED,
        execution_completed_at=EXECUTION_COMPLETED,
    )

    assert batch.records == []
    assert batch.source_watermark == request.until == NOW
    assert batch.source_watermark != batch.completed_at


def test_until_is_the_defensive_cutoff_for_records_and_missing_date_fallback() -> None:
    until = NOW - timedelta(hours=1)
    context = _context().model_copy(update={"as_of": NOW})
    request = _request("company_industry.news").model_copy(
        update={"context": context, "until": until}
    )
    adapter = AkshareDevLiveAdapter("company_industry.news")
    between_until_and_as_of = [
        {
            "新闻标题": "future for this window",
            "发布时间": "2026-07-31 08:30:00",
            "新闻链接": "https://example.invalid/future-window",
        }
    ]

    filtered = adapter.normalize(
        request,
        between_until_and_as_of,
        execution_started_at=EXECUTION_STARTED,
        execution_completed_at=EXECUTION_COMPLETED,
    )
    fallback = adapter.normalize(
        request,
        [
            {
                "新闻标题": "numeric timestamp",
                "发布时间": 1,
                "新闻链接": "https://example.invalid/fallback",
            }
        ],
        execution_started_at=EXECUTION_STARTED,
        execution_completed_at=EXECUTION_COMPLETED,
    )

    assert filtered.records == []
    assert filtered.source_watermark == until
    assert len(fallback.records) == 1
    assert fallback.records[0].event_time == until
    assert fallback.source_watermark == until


def test_market_complete_row_selection_uses_until_not_later_as_of() -> None:
    as_of = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    until = datetime(2026, 7, 30, 23, 0, tzinfo=UTC)
    context = _context().model_copy(update={"as_of": as_of})
    request = _request("market.snapshot").model_copy(update={"context": context, "until": until})

    batch = AkshareDevLiveAdapter("market.snapshot").normalize(
        request,
        [{"日期": "2026-07-31", "收盘": 93}],
        execution_started_at=EXECUTION_STARTED,
        execution_completed_at=EXECUTION_COMPLETED,
    )

    assert batch.records == []
    assert batch.source_watermark == until


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clock_values", "expected_runner_calls"),
    (
        ((RuntimeError("private clock detail"),), 0),
        ((datetime(2026, 7, 31, 1, 5),), 0),
        ((EXECUTION_COMPLETED, EXECUTION_STARTED), 1),
    ),
)
async def test_invalid_execution_clock_fails_closed_without_raw_detail(
    clock_values: tuple[object, ...],
    expected_runner_calls: int,
) -> None:
    values = iter(clock_values)
    runner_calls = 0

    def clock() -> datetime:
        value = next(values)
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, datetime)
        return value

    def execute(
        _function: str,
        _arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ) -> AkshareProcessResult:
        nonlocal runner_calls
        runner_calls += 1
        return AkshareProcessResult(returncode=0, stdout='{"ok":true,"rows":[]}')

    connector = RightsGatedAkshareConnector(
        adapter=AkshareDevLiveAdapter("market.snapshot"),
        rights_policy=DevelopmentMetadataRightsPolicy(),
        runner=AkshareSubprocessRunner(executor=execute),
        clock=clock,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await connector.fetch(_request("market.snapshot"))

    assert exc_info.value.safe_message == "R1_SOURCE_CLOCK_INVALID"
    assert "private clock detail" not in str(exc_info.value)
    assert runner_calls == expected_runner_calls


@pytest.mark.asyncio
async def test_non_utc_execution_clock_is_normalized_to_utc() -> None:
    local_tz = timezone(timedelta(hours=8))
    local_start = datetime(2026, 7, 31, 9, 5, tzinfo=local_tz)
    local_end = local_start + timedelta(seconds=2)

    def execute(
        _function: str,
        _arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ) -> AkshareProcessResult:
        return AkshareProcessResult(returncode=0, stdout='{"ok":true,"rows":[]}')

    connector = RightsGatedAkshareConnector(
        adapter=AkshareDevLiveAdapter("market.snapshot"),
        rights_policy=DevelopmentMetadataRightsPolicy(),
        runner=AkshareSubprocessRunner(executor=execute),
        clock=iter((local_start, local_end)).__next__,
    )

    batch = await connector.fetch(_request("market.snapshot"))

    assert batch.started_at == local_start.astimezone(UTC)
    assert batch.completed_at == local_end.astimezone(UTC)
